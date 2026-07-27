import copy
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("OAK_ROOT", str(Path(__file__).resolve().parents[1])))
from branches.tickets.helpers import hash_config

base = {
    "settings": {
        "panel": {"title": "T", "description": "D", "color": 1, "categories_field_name": "C"},
        "categories": {
            "a": {"label": "A", "emoji": "x", "description": "d", "enabled": True},
            "b": {"label": "B", "emoji": "y", "description": "e", "enabled": False},
        },
        "transcript": {"web": {"base_url": "http://localhost:5454", "bind_host": "0.0.0.0"}},
        "staff_role_ids": [1, 2],
    }
}
h0 = hash_config(base)


def mut(fn):
    c = copy.deepcopy(base)
    fn(c)
    return c


cases = [
    ("base_url change", mut(lambda c: c["settings"]["transcript"]["web"].update(base_url="https://x")), "same"),
    ("staff roles change", mut(lambda c: c["settings"].update(staff_role_ids=[9])), "same"),
    ("disabled cat relabel", mut(lambda c: c["settings"]["categories"]["b"].update(label="zzz")), "same"),
    ("panel title change", mut(lambda c: c["settings"]["panel"].update(title="NEW")), "DIFFERENT"),
    ("category label change", mut(lambda c: c["settings"]["categories"]["a"].update(label="RENAMED")), "DIFFERENT"),
    ("disabled cat enabled", mut(lambda c: c["settings"]["categories"]["b"].update(enabled=True)), "DIFFERENT"),
]

ok = True
for name, cfg, want in cases:
    got = "same" if hash_config(cfg) == h0 else "DIFFERENT"
    good = got == want
    ok &= good
    print(("  OK  " if good else "  FAIL"), "%-24s -> %-9s (want %s)" % (name, got, want))
print("ALL PASS" if ok else "FAILURES PRESENT")
