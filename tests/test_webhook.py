"""The /webhook endpoint, exercised over a real socket with a real main loop.

The point of a webhook is that something with no idea what this program is --
a Discord webhook, an IFTTT recipe, a curl on a phone, a bot framework -- can
make the music play. So these tests send the shapes those things actually
send: form-encoded bodies, tokens in the URL, and no Content-Type at all.
They fail rather than hang if the main loop stops draining.
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SRC = "/Users/giathinh/ncs-music-launcher/src"
sys.path.insert(0, SRC)

import control_api as capi

PORT = 8903
TOKEN = "webhook-token-0123456789abcd"
fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}  {detail}")
        fails.append(label)


def post(path, data=None, ctype="application/json", use_url_token=False,
         raw_headers=None):
    """One real request. Returns (status, text)."""
    url = f"http://127.0.0.1:{PORT}{path}"
    if use_url_token:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={urllib.parse.quote(TOKEN)}"
    body = None
    if data is not None:
        body = (data if isinstance(data, bytes)
                else json.dumps(data).encode())
    req = urllib.request.Request(url, data=body, method="POST")
    if ctype:
        req.add_header("Content-Type", ctype)
    if raw_headers:
        for k, v in raw_headers.items():
            req.add_header(k, v)
    else:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode().strip(), \
                r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode().strip(), \
            e.headers.get("Content-Type", "")


print("WEBHOOK ENDPOINT")

state = {"playing": True, "track": "Around The World",
         "artist": "Daft Punk", "volume": 0.8, "paused": False}

calls = []


def make_handlers():
    def rec(name):
        def f(a):
            calls.append((name, a))
            if name == "play":
                return {"status": "playing", "index": a.get("index", 7),
                        "track": "Around The World"}
            if name == "volume":
                return {"volume": a.get("value", 0.5)}
            if name == "muted":
                return {"muted": a.get("value", True)}
            if name == "visualizer":
                return {"mode": a.get("mode", "bars")}
            if name == "next":
                return {"index": 8, "track": "Get Lucky"}
            if name == "pause":
                return {"status": "paused", "track": "Around The World"}
            if name == "video":
                return {"video": a.get("source") or "off"}
            if name == "seek":
                return {"seeked_to": a.get("seconds", 0.0)}
            return {"ok": True}
        return f

    return {n: rec(n) for n in ("play", "pause", "resume", "next", "prev",
                                "seek", "volume", "muted", "visualizer",
                                "video", "__tracks", "__search")}


api = capi.ControlAPI(port=PORT, token=TOKEN, timeout=4.0)
assert api.start(), getattr(api, "last_error", "start failed")

stop = threading.Event()


def fake_loop():
    while not stop.is_set():
        api.publish(dict(state))
        api.drain(HANDLERS)
        time.sleep(0.004)


HANDLERS = make_handlers()
t = threading.Thread(target=fake_loop, daemon=True)
t.start()
time.sleep(0.4)

print("1) a JSON body, the shape an agent would send")
s, txt, ctype = post("/webhook", {"action": "play", "query": "daft punk"})
check("-> 200", s == 200, (s, txt))
check("replies in plain text", ctype.startswith("text/plain"), ctype)
check("the reply names the track", "Around The World" in txt, txt)
check("the handler got the query", ("play", {"query": "daft punk"})
      in calls, calls[-3:])

print("2) form-encoded, which is what webhook senders default to")
calls.clear()
form = urllib.parse.urlencode({"action": "play", "query": "justice"}).encode()
s, txt, ctype = post("/webhook", form,
                     ctype="application/x-www-form-urlencoded")
check("form body -> 200", s == 200, (s, txt))
check("form fields reached the handler",
      ("play", {"query": "justice"}) in calls, calls[-3:])

print("3) shorthands: bare track= implies play")
calls.clear()
s, txt, _ = post("/webhook", urllib.parse.urlencode({"track": "kavinsky"}).encode(),
                 ctype="application/x-www-form-urlencoded")
check("track= alone -> 200", s == 200, (s, txt))
check("track= became a play with a query",
      ("play", {"track": "kavinsky", "query": "kavinsky"}) in calls,
      calls[-3:])

print("3b) REGRESSION: track= must reach the handler as a QUERY")
# Found by running the real app, not by the mock: `track=daft punk` set
# action=play but passed no "query" key, so the real api_cmd_play fell into its
# bare-play branch and merely RESUMED the current track -- replying "ok" while
# doing nothing. A mock handler that only records the payload cannot see this.
for field in ("track", "title", "q", "song", "name"):
    calls.clear()
    s, txt, _ = post("/webhook", urllib.parse.urlencode({field: "x"}).encode(),
                     ctype="application/x-www-form-urlencoded")
    got = calls[0] if calls else None
    check(f"{field}= is normalised to query= for the play handler",
          got and got[0] == "play" and got[1].get("query") == "x", got)
    check(f"{field}= kept its value under query", s == 200, (s, txt))

# an explicit index must win over a stray track name
calls.clear()
s, txt, _ = post("/webhook", urllib.parse.urlencode(
    {"action": "play", "index": "4", "track": "ignored"}).encode(),
    ctype="application/x-www-form-urlencoded")
check("an explicit index is not overridden by a track name",
      calls and calls[0][1].get("index") == 4, calls[-2:])

print("4) shorthands: bare next=, volume=, seek=")
for field, value, want, expect_param, expect_val in (
        ("next", "", "next", None, None),
        ("prev", "", "prev", None, None),
        ("volume", "0.3", "volume", "value", 0.3),
        ("seek", "42", "seek", "seconds", 42.0)):
    calls.clear()
    body = f"{field}={value}"
    s, txt, _ = post("/webhook", body.encode(),
                     ctype="application/x-www-form-urlencoded")
    got = calls[0] if calls else None
    check(f"bare {field}= implies {want}",
          s == 200 and got and got[0] == want, (s, txt, got))
    # The bare-key form names the action and the value with the SAME word, so
    # it must be translated to the parameter the handler actually reads.
    if expect_param:
        check(f"{field}= is translated to {expect_param}=",
              got and got[1].get(expect_param) == expect_val, got)

print("5) numbers survive the form round-trip")
calls.clear()
s, txt, _ = post("/webhook", urllib.parse.urlencode(
    {"action": "volume", "value": "0.25"}).encode(),
    ctype="application/x-www-form-urlencoded")
check("volume arrives as a number, not a string",
      ("volume", {"value": 0.25}) in calls, calls[-2:])
check("the reply echoes it", "0.25" in txt, txt)

calls.clear()
s, txt, _ = post("/webhook", urllib.parse.urlencode(
    {"action": "play", "index": "12"}).encode(),
    ctype="application/x-www-form-urlencoded")
check("index arrives as an int",
      ("play", {"index": 12}) in calls, calls[-2:])

print("6) booleans")
calls.clear()
s, txt, _ = post("/webhook", urllib.parse.urlencode(
    {"action": "mute", "value": "true"}).encode(),
    ctype="application/x-www-form-urlencoded")
check("'mute' maps to the 'muted' command", calls and calls[0][0] == "muted",
      calls[-2:])
check("value became a real bool",
      calls and calls[0][1].get("value") is True, calls[-2:])
calls.clear()
s, txt, _ = post("/webhook", urllib.parse.urlencode(
    {"action": "muted", "value": "0"}).encode(),
    ctype="application/x-www-form-urlencoded")
check("'0' is False, not True", calls and calls[0][1].get("value") is False,
      calls[-2:])

print("7) read-only actions answer without the command queue")
s, txt, _ = post("/webhook", {"action": "status"})
check("status -> 200", s == 200, (s, txt))
check("status names the track", "Around The World" in txt, txt)
check("status shows the volume", "0.8" in txt, txt)
s, txt, _ = post("/webhook", {"action": "tracks"})
check("tracks -> 200 and counts", s == 200 and "tracks:" in txt, (s, txt))
s, txt, _ = post("/webhook", {"action": "search", "q": "daft"})
check("search -> 200 and counts", s == 200 and "match" in txt, (s, txt))

print("8) auth still applies, and ?token= is the concession")
s, txt, _ = post("/webhook", {"action": "next"},
                 raw_headers={"Authorization": "Bearer wrong"})
check("a wrong header token -> 401", s == 401, (s, txt))
calls.clear()
s, txt, _ = post("/webhook", {"action": "next"}, use_url_token=True,
                 raw_headers={"X-Dummy": "1"})
check("?token= in the URL works (for senders with no header support)",
      s == 200 and calls and calls[0][0] == "next", (s, txt, calls[-2:]))
s, txt, _ = post("/webhook", {"action": "next"},
                 raw_headers={"X-HashPlay-Token": TOKEN})
check("an X-HashPlay-Token header works too", s == 200, (s, txt))

print("9) bad input is refused, in plain English")
s, txt, _ = post("/webhook", {"action": "selfdestruct"})
check("an unknown action -> 400", s == 400, (s, txt))
check("the error names the action", "selfdestruct" in txt, txt)
s, txt, _ = post("/webhook", {})
check("no action -> 200 with a usage hint",
      s == 200 and "HashPlay" in txt, (s, txt))
s, txt, _ = post("/webhook", b"{not json")
check("malformed JSON -> 400", s == 400, (s, txt))
s, txt, _ = post("/webhook", {"action": "volume", "value": "loud"})
check("a non-numeric volume -> 400 naming the field",
      s == 400 and "must be a number" in txt, (s, txt))

print("10) a handler that fails is reported, not swallowed")
_good_play = HANDLERS["play"]
HANDLERS["play"] = lambda a: (_ for _ in ()).throw(RuntimeError("busy"))
s, txt, _ = post("/webhook", {"action": "play", "query": "x"})
check("a raising handler -> 400 with its message",
      s == 400 and "busy" in txt, (s, txt))
HANDLERS["play"] = _good_play          # restore, or step 11 inherits it

print("11) the other JSON routes are unaffected")
req = urllib.request.Request(f"http://127.0.0.1:{PORT}/play",
                             data=json.dumps({"query": "x"}).encode(),
                             method="POST")
req.add_header("Authorization", f"Bearer {TOKEN}")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=6) as r:
    ctype = r.headers.get("Content-Type", "")
    body = json.loads(r.read().decode())
check("POST /play still returns JSON, not text",
      ctype.startswith("application/json"), ctype)
check("and still works", body.get("ok") is True, body)

stop.set()
api.stop()
print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("WEBHOOK TESTS PASSED")
