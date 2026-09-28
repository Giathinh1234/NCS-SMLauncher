"""A local HTTP control API, so any AI agent can drive the music.

The 1.0.x app embedded a chat panel that shelled out to the `hermes` CLI.
That coupled a music player to one specific agent install, and the user had to
be in the app's own text box to use it. This replaces it with a plain
loopback HTTP API: anything that can make a request -- Claude Code, OpenCode,
a script, curl, another program on this machine -- can control playback.

    GET  /                  service info + every endpoint (discoverable)
    GET  /status            current player state
    GET  /tracks            the library
    GET  /tracks/search?q=  filter by title/artist
    POST /play              {"index": 3} | {"query": "jazz"} | {}
    POST /pause  /resume  /next  /prev
    POST /seek              {"seconds": 30} | {"delta": -5}
    POST /volume            {"value": 0.5} | {"delta": -0.1}
    POST /muted             {"value": true}
    POST /visualizer        {"mode": "bars"} | {"next": true}
    POST /video             {"source": "/path/clip.mp4"} | {"off": true}

TWO DESIGN RULES WORTH KNOWING:

1. **Loopback only, and token-authenticated.** The server binds 127.0.0.1, so
   nothing off this machine can reach it, but any local process could. Every
   request needs `Authorization: Bearer <token>`; the token is generated once
   and stored with the rest of the settings. Without it, a random web page
   could have driven the player's audio. Constant-time comparison, because a
   token check that leaks its own length is not a token check.

2. **The HTTP thread never touches pygame.** Calling set_mode or blitting
   from another thread is undefined behaviour in SDL, and a crash there would
   look like a random freeze. Instead the server enqueues a command and waits
   on an Event; the main loop drains the queue at the top of each frame and
   publishes the result. State the other way (main -> HTTP) is a plain dict
   the main loop replaces wholesale each frame, which is atomic in CPython
   and needs no lock.
"""
import json
import hmac
import os
import queue
import secrets
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Bumped when the response shape changes, so an agent can tell.
API_VERSION = 1

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8777

# Bounded so one bad agent cannot grow the process without limit. A queue that
# only ever grows is a slow memory leak with extra steps.
MAX_PENDING = 64


def probe_dependencies():
    """What the app can actually do right now. Presence, not a version claim."""
    out = {}
    for name, note in (
        ("ffmpeg", "video backgrounds (press V)"),
        ("ffprobe", "probing video sources"),
    ):
        path = shutil.which(name)
        out[name] = {"present": bool(path), "path": path or "", "note": note}
    for mod, note in (
        ("libtorrent", "torrent downloads (press T)"),
        ("sounddevice", "system audio output"),
        ("miniaudio", "decoding"),
    ):
        try:
            __import__(mod)
            out[mod] = {"present": True, "path": mod, "note": note}
        except Exception:
            out[mod] = {"present": False, "path": "", "note": note}
    return out


class _Request:
    """One pending call from the HTTP thread to the main loop."""

    __slots__ = ("name", "args", "event", "result", "error")

    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.event = threading.Event()
        self.result = None
        self.error = None


class ControlAPI:
    """Loopback HTTP front end for the player.

    The main loop must call `drain(handler_map)` every frame; that is what
    actually executes the commands.
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, token=None,
                 timeout=5.0, enabled=True):
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.timeout = timeout
        self.enabled = enabled

        self._queue = queue.Queue(maxsize=MAX_PENDING)
        # Replaced wholesale by the main loop each frame. A single attribute
        # rebind is atomic, so the HTTP thread always sees a whole snapshot.
        self.snapshot = {}
        self._server = None
        self._thread = None

    # ---- main-loop side ------------------------------------------------
    def publish(self, state):
        """Hand the HTTP thread a fresh state snapshot. Called every frame."""
        self.snapshot = state

    def drain(self, handler_map, limit=16):
        """Run queued commands on the main thread.

        `handler_map` maps a command name to a callable. Returns the number
        executed. Unknown commands become an error reply rather than a
        silent no-op, so a typo in an agent's call is visible.
        """
        ran = 0
        for _ in range(limit):
            try:
                req = self._queue.get_nowait()
            except queue.Empty:
                break
            fn = handler_map.get(req.name)
            if fn is None:
                req.error = f"unknown command: {req.name}"
            else:
                try:
                    req.result = fn(req.args)
                except Exception as exc:      # a bad call must not kill the app
                    req.error = f"{type(exc).__name__}: {exc}"
            req.event.set()
            ran += 1
        return ran

    # ---- HTTP side -----------------------------------------------------
    def start(self):
        if self._server is not None:
            return True
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):       # keep the app's stdout clean
                pass

            def _authed(self):
                got = self.headers.get("Authorization", "")
                want = f"Bearer {api.token}"
                # compare_digest, not ==: a plain comparison leaks the token
                # length through timing.
                return hmac.compare_digest(got, want)

            def _send(self, code, payload):
                body = json.dumps(payload, indent=None,
                                  default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the agent hung up; nothing to do

            def _deny(self, code, why):
                self._send(code, {"ok": False, "error": why})

            def _body(self):
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return {}
                if n <= 0:
                    return {}
                if n > 64 * 1024:
                    raise ValueError("body too large")
                try:
                    raw = self.rfile.read(n)
                    return json.loads(raw.decode("utf-8")) or {}
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError(f"invalid JSON body: {exc}")

            def _call(self, name, args):
                """Queue a command and wait for the main loop to run it.

                Returns (status, payload) for the caller to send, rather than
                sending it itself. The GET routes below wrap this in
                self._send(), and a version that replied directly produced
                TWO responses on one request: a 504 followed by a 200 with a
                null body, which is a protocol violation and left clients
                reading whichever arrived first.
                """
                req = _Request(name, args)
                try:
                    api._queue.put_nowait(req)
                except queue.Full:
                    return 429, {"ok": False,
                                  "error": "too many pending commands"}
                if not req.event.wait(api.timeout):
                    return 504, {"ok": False,
                                 "error": f"the app did not answer within "
                                          f"{api.timeout}s (is it running and "
                                          f"responsive?)"}
                if req.error:
                    return 400, {"ok": False, "error": req.error}
                return 200, {"ok": True, "result": req.result}

            def _reply(self, name, args):
                """_call, then send exactly one response."""
                status, payload = self._call(name, args)
                self._send(status, payload)

            # ---- routes ----
            def do_GET(self):
                if not self._authed():
                    return self._deny(401, "bad or missing bearer token")
                path = self.path.split("?", 1)[0].rstrip("/") or "/"
                if path == "/":
                    return self._send(200, api._index())
                if path == "/status":
                    return self._send(200, {"ok": True, "result": api.snapshot})
                if path == "/tracks":
                    return self._reply("__tracks", {})
                if path == "/tracks/search":
                    q = ""
                    if "?" in self.path:
                        from urllib.parse import parse_qs, urlparse
                        q = (parse_qs(urlparse(self.path).query)
                             .get("q", [""])[0])
                    return self._reply("__search", {"q": q})
                self._deny(404, f"no such endpoint: {path}")

            def do_POST(self):
                if not self._authed():
                    return self._deny(401, "bad or missing bearer token")
                path = self.path.split("?", 1)[0].rstrip("/")
                try:
                    args = self._body()
                except ValueError as exc:
                    return self._deny(400, str(exc))
                if not isinstance(args, dict):
                    return self._deny(400, "body must be a JSON object")
                if path in ("/play", "/pause", "/resume", "/next", "/prev",
                            "/seek", "/volume", "/muted", "/visualizer",
                            "/video"):
                    return self._reply(path.lstrip("/"), args)
                self._deny(404, f"no such endpoint: {path}")

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self._server = None
            self.last_error = f"could not bind {self.host}:{self.port} ({exc})"
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return True

    def _index(self):
        return {
            "ok": True,
            "result": {
                "service": "HashPlay control API",
                "api_version": API_VERSION,
                "play": "the app is running" if self.snapshot else
                        "the app is running but has not published state yet",
                "auth": "send 'Authorization: Bearer <token>' on every request",
                "endpoints": {
                    "GET /": "this list",
                    "GET /status": "current player state",
                    "GET /tracks": "the library",
                    "GET /tracks/search?q=": "search title/artist",
                    "POST /play": '{"index": n} | {"query": "text"} | {}',
                    "POST /pause": "{}",
                    "POST /resume": "{}",
                    "POST /next": "{}",
                    "POST /prev": "{}",
                    "POST /seek": '{"seconds": n} | {"delta": n}',
                    "POST /volume": '{"value": 0..1} | {"delta": n}',
                    "POST /muted": '{"value": bool}',
                    "POST /visualizer": '{"mode": "bars"} | {"next": true}',
                    "POST /video": '{"source": "path"} | {"off": true}',
                },
            },
        }

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self._thread = None

    def describe(self):
        """One line for the in-app panel."""
        if not self.enabled:
            return "control API disabled (api_enabled = false)"
        if self._server is None:
            return getattr(self, "last_error", "API not running")
        return f"http://{self.host}:{self.port}  (token: {self.token})"


def load_or_make_token(path):
    """Reuse the stored token so it survives restarts.

    A token that changed every launch would make the app hostile to use: the
    agent would have to re-read it every time, and a stale token in a script
    would fail for no visible reason.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read().strip()
        if len(existing) >= 16:
            return existing
    except (OSError, UnicodeDecodeError):
        pass
    token = secrets.token_urlsafe(24)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 0600: this file grants control of the player's audio to anyone who
        # can read it, and it lives in the user's home directory.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except OSError:
        pass          # an unwritable dir just means a per-session token
    return token
