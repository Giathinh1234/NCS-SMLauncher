"""Two API bugs that only show up on a reused connection, or under timeout.

1. A 401 left the request body unread on a keep-alive connection, so the next
   legitimate request on the same socket was parsed starting from the leftover
   bytes and answered with a 400 HTML error page.

2. A command that timed out was still executed afterwards. The client had
   already been told 504, so a retry double-applied: a `play` that timed out
   loaded the track anyway seconds later, and a retried `next` skipped two
   tracks.

Both are tested over a real socket against the real handler.
"""
import http.client
import os
import queue
import sys
import threading
import time

ROOT = "/Users/giathinh/ncs-music-launcher"
sys.path.insert(0, os.path.join(ROOT, "src"))

import control_api                                    # noqa: E402

fails = []


def check(label, cond, detail=""):
    if cond:
        print(f"   ok  {label}")
    else:
        print(f"   FAIL {label}   {detail}")
        fails.append(label)


TOKEN = "test-token-0123456789abcdef"
PORT = 8797
calls = []

api = control_api.ControlAPI(host="127.0.0.1", port=PORT, token=TOKEN)
check("the API bound", api.start(), api.last_error)


def handlers():
    return {
        "play": lambda a: calls.append(("play", dict(a))) or {"ok": True},
        "next": lambda a: calls.append(("next", dict(a))) or {"ok": True},
        "seek": lambda a: calls.append(("seek", dict(a))) or {"ok": True},
    }


# The main loop, as the launcher does it: drain once per tick.
stop = threading.Event()


def main_loop():
    while not stop.is_set():
        api.drain(handlers())


loop = threading.Thread(target=main_loop, daemon=True)
loop.start()

print("API CONNECTION REUSE")
try:
    # --- 1. a 401 must not poison the connection -------------------------
    # http.client reuses one socket for the whole connection, exactly like a
    # real agent's HTTP client.
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=6)
    conn.request("POST", "/seek",
                 body=b'{"seconds": 12}',
                 headers={"Authorization": "Bearer wrong-token",
                          "Content-Type": "application/json"})
    r1 = conn.getresponse()
    first = r1.read()
    check("an unauthenticated POST is 401", r1.status == 401, r1.status)

    # Now the SAME socket, correctly authenticated. Before the fix this came
    # back as 400 with an HTML page, because the unread 14-byte body was
    # still sitting in the socket.
    conn.request("POST", "/next", body=b"{}",
                 headers={"Authorization": f"Bearer {TOKEN}",
                          "Content-Type": "application/json"})
    r2 = conn.getresponse()
    second = r2.read()
    check("the NEXT request on the same socket still works", r2.status == 200,
          f"{r2.status} {second[:120]!r}")
    check("and it is a real JSON result, not an HTML error page",
          b"result" in second and b"<html" not in second.lower(),
          second[:120])
    conn.close()

    # And a third, to be sure the socket is still healthy.
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=6)
    conn.request("POST", "/seek", body=b'{"seconds": 5}',
                 headers={"Authorization": f"Bearer {TOKEN}",
                          "Content-Type": "application/json"})
    r3 = conn.getresponse()
    r3.read()
    check("a fresh connection is fine too", r3.status == 200, r3.status)
    conn.close()

    # --- 2. a timed-out command must not run ----------------------------
    print("API TIMEOUT")
    # Freeze the main loop so nothing drains, guaranteeing a timeout.
    api.timeout = 0.4
    slow = control_api.ControlAPI  # keep the class referenced
    # Point the handler at something that would be clearly observable.
    before = len(calls)
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=6)
    stop.set()                       # main loop stops draining
    time.sleep(0.2)
    conn.request("POST", "/play", body=b'{"query": "daft punk"}',
                 headers={"Authorization": f"Bearer {TOKEN}",
                          "Content-Type": "application/json"})
    timed_out = None
    try:
        r = conn.getresponse()
        timed_out = (r.status, r.read())
    except Exception as e:
        timed_out = ("exception", str(e))
    check("a command the app never runs gets 504",
          isinstance(timed_out, tuple) and timed_out[0] == 504, timed_out)

    # Now let the main loop run again. The queued command must be dropped,
    # not executed after the client was already told it failed.
    stop.clear()
    loop2 = threading.Thread(target=main_loop, daemon=True)
    loop2.start()
    time.sleep(1.2)
    check("the timed-out command was NOT executed afterwards",
          len(calls) == before, f"ran {calls[before:]}")
    check("so a retry cannot double-apply", not any(
        c[1].get("query") == "daft punk" for c in calls[before:]),
          calls[before:])

    conn.close()
finally:
    stop.set()
    time.sleep(0.2)
    api.stop()

print()
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("API CONNECTION TESTS PASSED")
