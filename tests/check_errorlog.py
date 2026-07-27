import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("OAK_ROOT", str(Path(__file__).resolve().parents[1])))
from oak.errorlog import ErrorReporter, _BufferingErrorHandler

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("  OK  " if cond else "  FAIL"), name)


h = _BufferingErrorHandler(level=logging.ERROR)
log = logging.getLogger("oak.branch.test")
log.addHandler(h)
log.propagate = False

log.error("boom %s", 42)
log.warning("should be filtered out by level")
check("error captured, warning filtered", len(h.drain()) == 1)

log.error("with traceback")
try:
    raise ValueError("inner")
except ValueError:
    log.error("caught", exc_info=True)
items = h.drain()
check("traceback included", any("ValueError: inner" in i[3] for i in items))

# A record from a discord.* logger must never be forwarded, or a failed send
# logs an error which is re-queued and fails again forever.
dlog = logging.getLogger("discord.client")
dlog.addHandler(h)
dlog.propagate = False
dlog.error("discord failure")
check("discord.* records dropped", len(h.drain()) == 0)

# emit() must swallow anything. A broken __str__ in a log argument must not
# take down the caller that was merely trying to log.
class Exploding:
    def __str__(self):
        raise RuntimeError("nope")


try:
    log.error("bad arg: %s", Exploding())
    raised = False
except Exception:
    raised = True
check("emit() never raises on bad args", not raised)

# Buffer is bounded; overflow is counted, not unbounded growth.
h2 = _BufferingErrorHandler(level=logging.ERROR, capacity=5)
log2 = logging.getLogger("oak.branch.test2")
log2.addHandler(h2)
log2.propagate = False
for i in range(20):
    log2.error("msg %d", i)
check("buffer bounded to capacity", len(h2.drain()) == 5)
check("overflow counted", h2.dropped == 15)

# Dedupe signature keys on the first line only, so a changing tail still
# collapses.
sig = ErrorReporter._signature
check(
    "signature collapses varying tail",
    sig("a", "Failed to DM user 123\nstack") == sig("a", "Failed to DM user 123\nother"),
)
check("signature separates distinct messages", sig("a", "one") != sig("a", "two"))
check("signature separates loggers", sig("a", "same") != sig("b", "same"))

print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
