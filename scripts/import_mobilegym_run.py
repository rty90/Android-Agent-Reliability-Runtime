from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.adapters.mobilegym import import_mobilegym_run, render_mobilegym_import_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a MobileGym run into A2R2 trace.v1.")
    parser.add_argument("--run-dir", required=True, help="MobileGym runs/<timestamp> directory")
    parser.add_argument(
        "--traces-root",
        default="data/traces/mobilegym_import",
        help="A2R2 trace output root",
    )
    parser.add_argument(
        "--out-dir",
        default="data/reports/mobilegym_import",
        help="Directory for mobilegym_a2r2_summary.{json,md}",
    )
    parser.add_argument("--markdown", action="store_true", help="Print markdown summary instead of JSON")
    args = parser.parse_args()

    summary = import_mobilegym_run(args.run_dir, traces_root=args.traces_root, out_dir=args.out_dir)
    if args.markdown:
        print(render_mobilegym_import_markdown(summary))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
