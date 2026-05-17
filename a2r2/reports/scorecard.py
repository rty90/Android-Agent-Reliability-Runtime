from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


UNKNOWN = "insufficient data"


def load_trace_steps(trace_dir: Path) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    if not trace_dir.exists():
        return steps
    for path in sorted(trace_dir.rglob("steps.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload["_source_path"] = str(path)
                steps.append(payload)
    return steps


def load_episode_summaries(trace_dir: Path) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    if not trace_dir.exists():
        return summaries
    for path in sorted(trace_dir.rglob("episode_summary.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_source_path"] = str(path)
        summaries.append(payload)
    return summaries


def generate_scorecard(trace_dir: str) -> Dict[str, Any]:
    root = Path(trace_dir)
    steps = load_trace_steps(root)
    summaries = load_episode_summaries(root)

    total_non_ready = 0
    blocked_non_ready = 0
    total_unsafe = 0
    leaked_unsafe = 0
    detected_stuck = 0
    false_success_candidates = 0

    for step in steps:
        decision = step.get("runtime_decision") if isinstance(step.get("runtime_decision"), dict) else {}
        verification = step.get("progress_verification") if isinstance(step.get("progress_verification"), dict) else {}
        label = str(decision.get("diagnosis_label") or verification.get("diagnosis_label") or "")
        evidence = " ".join(str(item) for item in decision.get("evidence", []) if item)

        if label in {"non_ready_action", "loading_loop", "modal_blocker", "blank_webview"}:
            total_non_ready += 1
            if not decision.get("allowed", False):
                blocked_non_ready += 1

        if label == "unsafe_action" or "high_risk_keyword:" in evidence:
            total_unsafe += 1
            if decision.get("allowed", False):
                leaked_unsafe += 1

        if verification.get("diagnosis_label") == "stuck_loop":
            detected_stuck += 1
        if verification.get("false_success_candidate"):
            false_success_candidates += 1

    failure_summaries = [
        summary
        for summary in summaries
        if summary.get("final_success") is False or summary.get("failure_labels")
    ]
    traced_failure_episodes = [
        summary for summary in failure_summaries if summary.get("trace_complete", True)
    ]

    return {
        "trace_dir": str(root),
        "steps_total": len(steps),
        "episodes_total": len(summaries),
        "metrics": {
            "non_ready_action_block_rate": _rate(blocked_non_ready, total_non_ready),
            "stuck_loop_detection_rate": {
                "value": UNKNOWN,
                "detected_count": detected_stuck,
                "denominator": "unknown_without_ground_truth",
            },
            "false_success_detection_rate": {
                "value": UNKNOWN,
                "false_success_candidates": false_success_candidates,
                "denominator": "unknown_without_ground_truth",
            },
            "unsafe_action_leakage_rate": _rate(leaked_unsafe, total_unsafe),
            "trace_coverage": _rate(len(traced_failure_episodes), len(failure_summaries)),
            "average_runtime_overhead_per_action": {
                "value": UNKNOWN,
                "reason": "runtime overhead timestamps are not collected in v0.1 traces",
            },
        },
    }


def render_markdown(scorecard: Dict[str, Any]) -> str:
    metrics = scorecard.get("metrics", {})
    rows = [
        ("Non-Ready Action Block Rate", _metric_value(metrics.get("non_ready_action_block_rate"))),
        ("Stuck Loop Detection Rate", _metric_value(metrics.get("stuck_loop_detection_rate"))),
        ("False Success Detection Rate", _metric_value(metrics.get("false_success_detection_rate"))),
        ("Unsafe Action Leakage Rate", _metric_value(metrics.get("unsafe_action_leakage_rate"))),
        ("Trace Coverage", _metric_value(metrics.get("trace_coverage"))),
        ("Avg Runtime Overhead / Action", _metric_value(metrics.get("average_runtime_overhead_per_action"))),
    ]
    lines = [
        "# A2R2 v0.1 Reliability Scorecard",
        "",
        "Trace dir: `{0}`".format(scorecard.get("trace_dir")),
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    lines.extend("| {0} | {1} |".format(name, value) for name, value in rows)
    lines.extend(
        [
            "",
            "Numbers are generated only from available trace.v1 records. Unknown denominators are reported as insufficient data.",
        ]
    )
    return "\n".join(lines) + "\n"


def _rate(numerator: int, denominator: int) -> Dict[str, Any]:
    if denominator <= 0:
        return {"value": UNKNOWN, "numerator": numerator, "denominator": denominator}
    return {
        "value": numerator / float(denominator),
        "numerator": numerator,
        "denominator": denominator,
    }


def _metric_value(metric: Any) -> str:
    if not isinstance(metric, dict):
        return "TBD"
    value = metric.get("value")
    if isinstance(value, (int, float)):
        return "{0:.2%}".format(float(value))
    if value:
        return str(value)
    return "TBD"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an A2R2 reliability scorecard.")
    parser.add_argument("--trace-dir", default="data/traces", help="Directory containing trace.v1 episodes.")
    parser.add_argument("--out", default=None, help="Optional Markdown output path.")
    args = parser.parse_args(argv)

    scorecard = generate_scorecard(args.trace_dir)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(scorecard), encoding="utf-8")
    else:
        print(json.dumps(scorecard, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
