"""The control API, exercised over a real socket.

Nothing here is mocked: a real ThreadingHTTPServer binds a real port, real
HTTP requests go over a real socket, and the commands are executed by a real
`drain()` call standing in for the main loop. That is deliberate -- a mocked
HTTP layer would not have caught the threading design, which is the whole
risk in this module.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

SRC = "/Users/giathinh/ncs-music-launcher/src"
sys.path.insert(0, SRC)

import control_api as capi

PORT = 8899            # fixed so a stray server from a previous run is obvious
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}  {detail}")
        fails.append(label)


def call(path, body=None, method=None, token=None, expect_error=False):
    """One real HTTP request. Returns (status_code, parsed_json)."""
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


print("CONTROL API")

# ---- start a server with a stand-in main loop ----------------------------
api = capi.ControlAPI(port=PORT, token="test-token-0123456789abcdef", timeout=3.0)
state = {"playing": False, "track": None}

# The main loop, standing in: publishes state, drains commands.
def fake_loop(stop):
    while not stop.is_set():
        api.publish(dict(state))
        api.drain(HANDLERS)
        time.sleep(0.005)


HANDLERS = {
    "play": lambda a: {"played": a},
    "pause": lambda a: "paused",
    "next": lambda a: "next",
    "seek": lambda a: {"seeked": a},
    "volume": lambda a: {"volume": a},
    "__tracks": lambda a: [{"i": 0, "title": "one"}, {"i": 1, "title": "two"}],
    "__search": lambda a: {"q": a.get("q"), "hits": 1},
}

assert api.start(), getattr(api, "last_error", "start failed")
stop = threading.Event()
t = threading.Thread(target=fake_loop, args=(stop,), daemon=True)
t.start()
time.sleep(0.4)

print("1) discovery")
st, body = call("/", token="test-token-0123456789abcdef")
check("GET / returns 200", st == 200, st)
endpoints = body.get("result", {}).get("endpoints", {})
check("lists the playback endpoints",
      all(k in endpoints for k in ("GET /status", "GET /tracks", "POST /play",
                                   "POST /pause", "POST /next", "POST /seek",
                                   "POST /volume", "POST /visualizer")),
      sorted(endpoints))
check("advertises the auth scheme", "Bearer" in body["result"].get("auth", ""))

print("2) authentication is required")
st, body = call("/status")
check("no token -> 401", st == 401, st)
st, _ = call("/status", token="wrong-token")
check("wrong token -> 401", st == 401, st)

print("3) state published by the main loop is readable")
state.update({"playing": True, "track": "Drift - NCS"})
time.sleep(0.15)
st, body = call("/status", token="test-token-0123456789abcdef")
check("GET /status -> 200", st == 200, st)
check("status carries the published state",
      body["result"].get("track") == "Drift - NCS", body)

print("4) commands run on the main loop and return its result")
st, body = call("/play", {"index": 3}, token="test-token-0123456789abcdef")
check("POST /play -> 200", st == 200, st)
check("main loop's return value comes back",
      body.get("result") == {"played": {"index": 3}}, body)
st, body = call("/pause", {}, token="test-token-0123456789abcdef")
check("POST /pause returns the handler result", body.get("result") == "paused", body)

print("5) library and search")
st, body = call("/tracks", token="test-token-0123456789abcdef")
check("GET /tracks returns the library", len(body.get("result", [])) == 2, body)
st, body = call("/tracks/search?q=jazz", token="test-token-0123456789abcdef")
check("search passes the query through", body["result"].get("q") == "jazz", body)

print("6) bad requests are refused clearly, not crashed on")
st, body = call("/nope", token="test-token-0123456789abcdef")
check("unknown GET -> 404", st == 404, st)
st, body = call("/nope", {}, token="test-token-0123456789abcdef")
check("unknown POST -> 404", st == 404, st)
st, body = call("/play", {}, method="POST")
check("POST with no token -> 401, and auth is checked before the body",
      st == 401, st)

req = urllib.request.Request(f"http://127.0.0.1:{PORT}/play", data=b"{not json",
                             method="POST")
req.add_header("Authorization", "Bearer test-token-0123456789abcdef")
try:
    urllib.request.urlopen(req, timeout=6)
    check("malformed JSON -> 400", False, "it was accepted")
except urllib.error.HTTPError as e:
    check("malformed JSON -> 400", e.code == 400, e.code)

print("7) an unknown command is an error, not a silent success")
api2_stop = stop.is_set
stop.set()          # stop the loop: nothing will drain the queue
time.sleep(0.2)
# Re-add a handler map without the command, restart a loop.
HANDLERS.pop("next")
stop.clear()
t2 = threading.Thread(target=fake_loop, args=(stop,), daemon=True)
t2.start()
time.sleep(0.3)
st, body = call("/next", {}, token="test-token-0123456789abcdef")
check("handler that raises -> 400 with the message",
      st == 400 and "unknown command" in body.get("error", ""), (st, body))

print("8) a handler that raises is reported, and the app survives")
def boom(_a):
    raise RuntimeError("player is busy")
HANDLERS["play"] = boom
st, body = call("/play", {}, token="test-token-0123456789abcdef")
check("exception -> 400 with the message",
      st == 400 and "player is busy" in body.get("error", ""), (st, body))
st, body = call("/pause", {}, token="test-token-0123456789abcdef")
check("the API still works after a handler raised", st == 200, st)

stop.set()
api.stop()
time.sleep(0.2)

print("9) a dead main loop times out instead of hanging the agent")
dead = capi.ControlAPI(port=PORT + 1, token="t-0123456789abcdef", timeout=0.7)
assert dead.start(), getattr(dead, "last_error", "start failed")
# never call drain(), so the request can never be serviced
t0 = time.time()
url = f"http://127.0.0.1:{PORT + 1}/play"
req = urllib.request.Request(url, data=b"{}", method="POST")
req.add_header("Authorization", "Bearer t-0123456789abcdef")
try:
    urllib.request.urlopen(req, timeout=6)
    check("unserviced request -> 504", False, "it returned 200")
except urllib.error.HTTPError as e:
    took = time.time() - t0
    payload = json.loads(e.read().decode() or "{}")
    check("unserviced request -> 504", e.code == 504, e.code)
    check("it gave up promptly", took < 3.0, f"{took:.1f}s")
    check("the 504 explains why", "did not answer" in payload.get("error", ""), payload)
dead.stop()

print("10) the token is reused across launches, and stored 0600")
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "api_token")
    t1 = capi.load_or_make_token(p)
    t2b = capi.load_or_make_token(p)
    check("stable across calls", t1 == t2b, (t1[:8], t2b[:8]))
    check("long enough to be a real token", len(t1) >= 16, len(t1))
    mode = os.stat(p).st_mode & 0o777
    check("stored 0600 (it grants audio control)", mode == 0o600, oct(mode))
    t3 = capi.load_or_make_token(os.path.join(d, "nested", "api_token"))
    check("creates the dir if needed", len(t3) >= 16, len(t3))

print("11) dependency probe reports honestly")
deps = capi.probe_dependencies()
check("probes ffmpeg/ffprobe/libtorrent/sounddevice/miniaudio",
      set(deps) == {"ffmpeg", "ffprobe", "libtorrent", "sounddevice", "miniaudio"},
      sorted(deps))
check("every entry is a dict with present + note",
      all(isinstance(v, dict) and "present" in v and "note" in v
          for v in deps.values()))
check("a missing optional dep is present=False, not an error",
      all(v["present"] in (True, False) for v in deps.values()))
for k, v in sorted(deps.items()):
    print(f"      {k:14} {'yes' if v['present'] else 'no ':4} {v['note']}")

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("CONTROL API PASSED")
