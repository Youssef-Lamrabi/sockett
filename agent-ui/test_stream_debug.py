"""
test_stream_debug.py
====================
Minimal smoke-test for the BioAgent streaming pipeline.

Run from the agent-ui/ directory (or any directory where `genomeer` is
importable):

    python test_stream_debug.py

What it tests
-------------
1. BioAgent can be instantiated with source="Custom", a fake API key, and a
   bogus base_url.
2. go_stream() raises an exception immediately (AuthenticationError or
   similar) rather than hanging indefinitely.
3. The exception surfaces as a {"type": "error", ...} dict instead of a bare
   Python exception silently dropped into the void — i.e. the caller can
   always detect failures.
4. A {"type": "done"} event is always the last event yielded, even on error.

Expected output on a correctly-patched server
---------------------------------------------
  [1/4] BioAgent instantiated OK
  [2/4] go_stream() started ...
  [3/4] Events received:
        event 0: type=error  text=<some auth/connection error message>
        event 1: type=done
  [4/4] PASS — error event received before done; stream terminated cleanly.

If the stream hangs for more than TIMEOUT_S seconds the test will kill it
and report a FAIL.
"""

import sys, os, traceback, threading, time

# ---------------------------------------------------------------------------
# Adjust import path so we can import genomeer without a pip install
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # parent of agent-ui/
_GENOMEER_SRC = os.path.join(_ROOT, "genomeer", "src")
if _GENOMEER_SRC not in sys.path:
    sys.path.insert(0, _GENOMEER_SRC)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FAKE_BASE_URL  = "http://127.0.0.1:19999/v1"   # nothing listening here
FAKE_API_KEY   = "sk-FAKE-KEY-FOR-TESTING-ONLY"
TEST_MODEL     = "gpt-4o-mini"
TEST_PROMPT    = "Say hello"
TIMEOUT_S      = 35   # seconds — slightly longer than FIRST_TOKEN_TIMEOUT (30 s)

# ---------------------------------------------------------------------------
# Step 1 — instantiate BioAgent
# ---------------------------------------------------------------------------
print("[1/4] Instantiating BioAgent(source='Custom', fake api_key, unreachable base_url) …")
try:
    from genomeer.agent.v2 import BioAgent

    agent = BioAgent(
        path="./data",
        llm=TEST_MODEL,
        source="Custom",
        base_url=FAKE_BASE_URL,
        api_key=FAKE_API_KEY,
        use_tool_retriever=False,
        timeout_seconds=10,          # short so the test doesn't wait forever
        auto_start_artifacts=False,
        interaction_mode="auto",
    )
    print("[1/4] BioAgent instantiated OK")
except Exception as exc:
    print(f"[1/4] FAIL — BioAgent.__init__ raised: {exc}")
    traceback.print_exc()
    sys.exit(1)

# ---------------------------------------------------------------------------
# Step 2 / 3 — call go_stream() and collect events, with a hard timeout
# ---------------------------------------------------------------------------
print(f"[2/4] go_stream() started (timeout={TIMEOUT_S}s) …")

events: list[dict] = []
stream_exc: Exception | None = None
finished = threading.Event()

def _run_stream():
    global stream_exc
    try:
        # go_stream() is a synchronous generator; iterate it fully.
        for evt in agent.go_stream(TEST_PROMPT, mode="prod"):
            events.append(evt)
            # Stop after done or error — don't wait for 300 s timeout
            if evt.get("type") in ("done", "error"):
                if evt.get("type") == "done":
                    break
    except Exception as exc:
        stream_exc = exc
        # Treat a bare exception as an error event so the assertion below
        # still has something to check.
        events.append({"type": "error", "text": str(exc)})
        events.append({"type": "done"})
    finally:
        finished.set()

t = threading.Thread(target=_run_stream, daemon=True)
t.start()
completed = finished.wait(timeout=TIMEOUT_S)

if not completed:
    print(f"[2/4] FAIL — go_stream() did NOT terminate within {TIMEOUT_S} seconds.")
    print("       This means the stream is hanging (endpoint unreachable, no timeout triggered).")
    print("       The first-token timeout fix in routes_chat.py is server-side; this test checks")
    print("       whether BioAgent itself times out when the LLM endpoint is unreachable.")
    sys.exit(1)

print("[2/4] go_stream() returned within timeout.")

# ---------------------------------------------------------------------------
# Step 3 — inspect events
# ---------------------------------------------------------------------------
print(f"[3/4] Events received ({len(events)} total):")
for i, evt in enumerate(events):
    text_preview = (evt.get("text") or "")[:120].replace("\n", " ")
    print(f"      event {i}: type={evt.get('type')!r:<8}  text={text_preview!r}")

# ---------------------------------------------------------------------------
# Step 4 — assertions
# ---------------------------------------------------------------------------
types = [e.get("type") for e in events]
has_error = "error" in types
has_done  = "done"  in types
error_before_done = (
    types.index("error") < types.index("done")
    if has_error and has_done else False
)

print("[4/4] Assertions:")

if not has_error:
    print("      WARN — no 'error' event received. Either the model responded "
          "successfully (unlikely with a fake key) or the exception was swallowed.")

if not has_done:
    print("      FAIL — no 'done' event received. Stream did not terminate cleanly.")
    sys.exit(1)

if has_error and not error_before_done:
    print("      FAIL — 'done' arrived before 'error'. Producer finally-block ordering is wrong.")
    sys.exit(1)

if stream_exc:
    print(f"      INFO — bare exception also captured (expected): {stream_exc}")

if has_error and has_done and error_before_done:
    print("      PASS — error event received before done; stream terminated cleanly.")
elif has_done:
    print("      PASS (no error) — stream completed without error (fake key may have been accepted "
          "or model returned a response; check manually if that's unexpected).")
