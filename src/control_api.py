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
MAX_BODY = 64 * 1024        # a control command is never this big


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

    __slots__ = ("name", "args", "event", "result", "error", "cancelled")

    def __init__(self, name, args):
        self.name = name
        self.args = args
        self.event = threading.Event()
        self.result = None
        self.error = None
        # Set when the HTTP thread gave up waiting. The main loop must then
        # drop the command rather than run it: the client already saw a 504,
        # so executing it anyway means the caller's retry double-applies. That
        # was live -- a `play` that timed out still loaded the track 2.5s
        # later, and a retried `next` skipped two.
        self.cancelled = False


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
        # Declared here rather than only on failure. The launcher reads this
        # directly when start() fails, and describe() reaches for it, so
        # having it exist only on the error path made both a latent
        # AttributeError waiting on any code that read it eagerly.
        self.last_error = None

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
            if req.cancelled:
                # The HTTP thread already answered 504 and moved on. Do not run
                # a command nobody is waiting for.
                req.event.set()
                continue
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

            def _authed(self, allow_query_token=False):
                """Check the bearer token.

                `allow_query_token` exists only for /webhook, because most
                webhook senders (Discord, Slack test boxes, IFTTT, a curl on
                a phone) can add a URL but cannot add an Authorization header.
                It is a real trade-off: a token in a URL is far more likely to
                end up in a proxy log, a shell history, or a screenshot than a
                header is. Since this binds loopback-only and the token lives
                in a 0600 file, that risk is bounded -- but the header form is
                still the better one, and is what the other routes require.
                """
                got = self.headers.get("Authorization", "")
                if not got:
                    got = self.headers.get("X-HashPlay-Token", "")
                    if got:
                        got = f"Bearer {got}"
                if not got and allow_query_token:
                    from urllib.parse import parse_qs, urlparse
                    q = (parse_qs(urlparse(self.path).query,
                                  keep_blank_values=True)
                         .get("token", [""])[0])
                    if q:
                        got = f"Bearer {q}"
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
                # Drain the body before replying, or close the connection.
                # We answer on a keep-alive connection (HTTP/1.1), so unread
                # body bytes stay in the socket and get parsed as the start of
                # the NEXT request. A 401 on /seek therefore made the next
                # legitimate /next on the same socket return a 400 HTML error
                # page. Same defect on the oversized-body path, which raised
                # before reading anything.
                self._drain()
                self._send(code, {"ok": False, "error": why})

            def _drain(self):
                """Consume and discard a request body we have not read yet.

                Must be conditional. An earlier version drained unconditionally
                and deadlocked: for a 404 the body had ALREADY been read by
                _body(), so this blocked forever waiting for bytes the client
                had already sent and was now waiting on us for. The server
                stopped answering and the test hung on its read.
                """
                if getattr(self, "_body_read", False):
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                remaining = min(max(n, 0), MAX_BODY + 1)
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        return
                    remaining -= len(chunk)

            def _body(self):
                """Parse the request body as JSON, or as a form.

                Webhook senders default to form encoding, and rejecting that
                would mean the most likely caller has to build a JSON body it
                has no way to configure.
                """
                self._body_read = True
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return {}
                if n <= 0:
                    return {}
                if n > MAX_BODY:
                    raise ValueError("body too large")
                raw = self.rfile.read(n)
                ctype = (self.headers.get("Content-Type") or "").lower()
                text = raw.decode("utf-8", "replace")
                if "application/x-www-form-urlencoded" in ctype:
                    from urllib.parse import parse_qs
                    # keep_blank_values: `next=` with an empty value is how a
                    # sender says "next, no arguments". parse_qs drops blank
                    # values by default, so the key vanished and the bare-
                    # keyword shorthands silently did nothing.
                    # Flattened to one string per key: a form body is
                    # inherently scalar, and a list would confuse the
                    # command handlers.
                    return {k: v[0] for k, v
                            in parse_qs(text, keep_blank_values=True).items()}
                try:
                    return json.loads(text) or {}
                except ValueError as exc:
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
                    req.cancelled = True
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

            def _text(self, code, line):
                """A one-line plain-text reply.

                Webhook senders show the raw response body in their test
                panel, and a human is usually the one reading it there. JSON
                at that moment is noise; "playing: X" is not.
                """
                body = (line + "\n").encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                self._body_read = False
                path = self.path.split("?", 1)[0].rstrip("/")
                # /webhook is allowed to authenticate from ?token=, so its auth
                # check is deferred to _do_webhook. Checking it here first
                # made the query-token path unreachable: the request was
                # rejected 401 before the lenient check could run.
                if path != "/webhook" and not self._authed():
                    return self._deny(401, "bad or missing bearer token")
                try:
                    args = self._body()
                except ValueError as exc:
                    return self._deny(400, str(exc))
                if not isinstance(args, dict):
                    return self._deny(400, "body must be a JSON object")
                if path == "/webhook":
                    return self._do_webhook(args)
                if path in ("/play", "/pause", "/resume", "/next", "/prev",
                            "/seek", "/volume", "/muted", "/visualizer",
                            "/video"):
                    return self._reply(path.lstrip("/"), args)
                self._deny(404, f"no such endpoint: {path}")

            def _do_webhook(self, args):
                """One flat endpoint for things that cannot speak JSON.

                Deliberately a THIN translation layer: it normalises a few
                shapes and then calls the same command handlers the JSON API
                uses. It grants no capability of its own, so it cannot drift
                into a second, less-audited way to reach the player.
                """
                if not self._authed(allow_query_token=True):
                    return self._text(401, "bad or missing token")

                # Normalise the shapes a sender is likely to produce:
                #   {"action":"play","query":"x"}   explicit
                #   {"track":"x"} / {"q":"x"}       "play this"
                #   {"volume":0.3}                  bare key, implied action
                #   {"cmd":"next"}                  cmd as an alias
                # One list, used twice. It was two lists once and they drifted:
                # `song=` and `name=` were accepted as track aliases below but
                # were not in the implied-action list, so they silently did
                # nothing.
                TRACK_KEYS = ("track", "q", "query", "title", "song", "name")

                action = (args.get("action") or args.get("cmd") or "").strip()
                lowered = {str(k).lower(): v for k, v in args.items()}

                if not action:
                    for key in TRACK_KEYS:
                        if lowered.get(key):
                            action = "play"
                            break
                if not action:
                    for key, implied in (("volume", "volume"),
                                         ("seek", "seek"),
                                         ("next", "next"),
                                         ("prev", "prev"),
                                         ("pause", "pause"),
                                         ("resume", "resume")):
                        if key in lowered:
                            action = implied
                            break
                if not action:
                    if self.path.split("?", 1)[0].rstrip("/") == "/webhook":
                        return self._text(
                            200, "HashPlay is up. POST an action, e.g. "
                                 '{"action":"play","query":"daft punk"}')
                    return self._text(400, "no action given")

                action = action.lower().strip()
                # "mute" is what people type; the command is "muted".
                if action == "mute":
                    action = "muted"
                # Drop the control keys so a handler never sees them.
                payload = {k: v for k, v in args.items()
                           if k.lower() not in ("action", "cmd", "token")}
                # Normalise the track-ish keys onto "query". Without this,
                # `track=daft punk` set action=play but handed api_cmd_play a
                # dict with no "query" key at all, so it fell through to its
                # bare-play branch and merely RESUMED whatever was already
                # loaded -- a silent no-op that looks like it worked.
                if action == "play" and "index" not in payload:
                    for key in TRACK_KEYS:
                        if payload.get(key):
                            payload["query"] = payload[key]
                            break
                # Same class of bug for the scalar shorthands: `volume=0.3`
                # names the action AND the value, but api_cmd_volume reads
                # "value" and api_cmd_seek reads "seconds". Left untranslated
                # both replied with a ValueError.
                for action_name, param in (("volume", "value"),
                                           ("seek", "seconds")):
                    if (action == action_name and param not in payload
                            and action_name in payload):
                        payload[param] = payload[action_name]
                # Everything arrives as a string over a form; the numeric
                # handlers want real numbers. `muted` is the exception: its
                # "value" is a boolean, and coercing "true" with float()
                # raised and rejected the request outright.
                for key in ("index", "seconds", "delta", "value"):
                    if key not in payload or not isinstance(payload[key], str):
                        continue
                    if key == "value" and action in ("muted", "play"):
                        payload[key] = payload[key].strip().lower() in (
                            "1", "true", "yes", "on")
                        continue
                    try:
                        payload[key] = float(payload[key])
                        if key == "index":
                            payload[key] = int(payload[key])
                    except ValueError:
                        return self._text(400, f"{key} must be a number")

                # Read-only asks. These are answered from the snapshot the
                # main loop publishes, or by the same helpers /tracks uses,
                # so they cannot block behind the command queue.
                if action == "status":
                    snap = api.snapshot or {}
                    cur = snap.get("track") or "nothing"
                    return self._text(
                        200, f"{'playing' if snap.get('playing') else 'paused'}"
                             f": {cur}  [{snap.get('artist') or 'unknown'}]"
                             f"  vol={snap.get('volume')}")
                if action == "tracks":
                    status, out = self._call("__tracks", {})
                    if status != 200:
                        return self._text(status, str((out or {}).get(
                            "error", "failed")))
                    return self._text(200, f"tracks: {len(out.get('result') or [])}"
                                          f" in the library")
                if action == "search":
                    q = payload.get("q") or payload.get("query") or ""
                    status, out = self._call("__search", {"q": q})
                    if status != 200:
                        return self._text(status, str((out or {}).get(
                            "error", "failed")))
                    res = (out or {}).get("result") or {}
                    return self._text(200, f"search {q!r}: "
                                          f"{res.get('count', 0)} match(es)")

                if action not in ("play", "pause", "resume", "next", "prev",
                                 "seek", "volume", "muted", "visualizer",
                                 "video"):
                    return self._text(400, f"unknown action {action!r}")

                status, payload_out = self._call(action, payload)
                if status != 200:
                    return self._text(status, str((payload_out or {}).get(
                        "error", "failed")))
                result = (payload_out or {}).get("result")
                if isinstance(result, dict):
                    for key in ("track", "status", "volume", "mode", "muted",
                                "video", "index", "count"):
                        if key in result:
                            return self._text(200, f"{action}: {key}="
                                                    f" {result[key]}")
                if isinstance(result, list):
                    return self._text(200, f"{action}: {len(result)} item(s)")
                return self._text(200, f"{action}: ok")

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
                    "POST /webhook": 'one-shot form or JSON: '
                                     '{"action":"play","query":"daft punk"} '
                                     "— also accepts ?token= for senders "
                                     "that cannot set headers; replies in "
                                     "plain text",
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
            return self.last_error or "API not running"
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
