"""Generate the A2R2 vs MobileGym reliability benchmark from imported summaries.

Thin wrapper around `a2r2.reports.mobilegym_benchmark`.

    python scripts\\mobilegym_benchmark.py ^
      --reports-dir data\\reports\\mobilegym_import ^
      --out docs\\mobilegym_benchmark_v0.1.md
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.reports.mobilegym_benchmark import main


def run(argv: Optional[List[str]] = None) -> int:
    return main(argv)


if __name__ == "__main__":
    raise SystemExit(run())
