"""Shared test setup.

Puts the repo root on ``sys.path`` so tests can import ``oak`` and
``branches`` without the package being installed. ``OAK_ROOT`` overrides it,
which is how these run inside the bot container where the code lives at
``/home/container``.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("OAK_ROOT", str(Path(__file__).resolve().parents[1])))
