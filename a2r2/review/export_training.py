"""Export recorded episodes as training-data files.

Two honest v0 formats (no fabrication, screenshots referenced by path):

* SFT: successful episodes as (goal, ordered steps) samples.
* Failure steps: flagged steps from failed episodes with their labels — the raw
  material for DPO pairs once a corrected action exists (from interventions or
  reviewer suggestions). We deliberately do not invent the "good" action.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from a2r2.review.retrospect import _action_desc, _load_steps, _step_labels


def _episode_dirs(traces_root: str) -> List[Path]:
    root = Path(traces_root)
    if not root.exists():
        return []
    return sorted(p.parent for p in root.rglob("episode_summary.json"))


def _summary(episode_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads((episode_dir / "episode_summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def export_sft(traces_root: str, out_path: str) -> int:
    """One JSONL row per successful episode: {goal, steps:[{screenshot, action}]}."""
    rows = 0
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for episode_dir in _episode_dirs(traces_root):
            summary = _summary(episode_dir)
            if summary.get("final_success") is not True:
                continue
            steps = _load_steps(str(episode_dir))
            if not steps:
                continue
            sample = {
                "episode_id": summary.get("episode_id"),
                "goal": summary.get("goal"),
                "steps": [
                    {
                        "screenshot": (step.get("before_state") or {}).get("screenshot_path"),
                        "action": step.get("proposed_action"),
                    }
                    for step in steps
                ],
            }
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
            rows += 1
    return rows


def export_failure_steps(traces_root: str, out_path: str) -> int:
    """One JSONL row per flagged step in a failed episode (DPO raw material)."""
    rows = 0
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for episode_dir in _episode_dirs(traces_root):
            summary = _summary(episode_dir)
            if summary.get("final_success") is not False:
                continue
            for step in _load_steps(str(episode_dir)):
                labels = _step_labels(step)
                if not labels:
                    continue
                proposed: Mapping[str, Any] = step.get("proposed_action") or {}
                handle.write(
                    json.dumps(
                        {
                            "episode_id": summary.get("episode_id"),
                            "goal": step.get("goal"),
                            "step_index": step.get("step_index"),
                            "screenshot": (step.get("before_state") or {}).get("screenshot_path"),
                            "action": dict(proposed),
                            "action_desc": _action_desc(proposed),
                            "labels": labels,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                rows += 1
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Export traces as training data files.")
    parser.add_argument("--traces-root", required=True)
    parser.add_argument("--out-dir", default="data/reports/training_export")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    sft = export_sft(args.traces_root, str(out_dir / "sft_success.jsonl"))
    failures = export_failure_steps(args.traces_root, str(out_dir / "failure_steps.jsonl"))
    print("sft_episodes={0} failure_steps={1} out_dir={2}".format(sft, failures, out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
