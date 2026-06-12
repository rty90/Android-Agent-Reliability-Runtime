"""Run the A2R2 offline trap-app MiniBench from the scripts/ entry point.

This is a thin wrapper around ``a2r2.bench.minibench`` so it can be launched the
same way as the other scripts in this folder:

    python scripts\\a2r2_minibench.py
    python scripts\\a2r2_minibench.py --repeat 3 --out-dir data\\reports\\minibench
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.bench.minibench import main


def run(argv: Optional[List[str]] = None) -> int:
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run())
