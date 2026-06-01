"""Aggregate many single-episode shadow reports into a corpus-level scorecard.

Each `ShadowSession` run writes one `shadow_report.json`. This module rolls a
collection of them up into real-denominator metrics, so a batch of real-agent
episodes becomes evidence instead of scattered single runs:

* early-detection rate  = failed episodes A2R2 flagged at/before failure / failed
* blind-spot rate       = failed episodes A2R2 did NOT predict but shadow
                          captured the agent's failure category / failed
* over-flag rate        = gated steps A2R2 would wrongly block / gated steps
* mean/median lead time = over failed episodes A2R2 predicted early
* avg overhead per action and agent-reported-failure-label distribution

It is read-only and offline; it never touches a device.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "shadow_aggregate.v1"


def load_reports(root: str) -> List[Dict[str, Any]]:
    base = Path(root)
    paths: List[Path]
    if base.is_file():
        paths = [base]
    else:
        paths = sorted(base.rglob("shadow_report.json"))
    reports: List[Dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_source_path"] = str(path)
        reports.append(payload)
    return reports


def _rate(numerator: int, denominator: int) -> Dict[str, Any]:
    if denominator <= 0:
        return {"value": None, "numerator": numerator, "denominator": denominator}
    return {"value": numerator / float(denominator), "numerator": numerator, "denominator": denominator}


def aggregate(reports: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    reports = list(reports)
    episodes_total = len(reports)
    failed = [r for r in reports if r.get("final_success") is False]
    succeeded = [r for r in reports if r.get("final_success") is True]

    matched = [r for r in failed if r.get("a2r2_prediction_matched_failure") is True]
    blind = [r for r in failed if r.get("failure_captured_not_predicted") is True]

    # Honesty split: a "match" at lead 0 means A2R2 flagged at the same step the
    # agent already failed (coincident), not a genuine early prediction. Only
    # lead > 0 counts as predicted-early.
    def _lead(r: Dict[str, Any]) -> Optional[float]:
        value = r.get("timeline", {}).get("a2r2_lead_over_failure")
        return value if isinstance(value, (int, float)) else None

    predicted_early = [r for r in matched if (_lead(r) or 0) > 0]
    coincident = [r for r in matched if _lead(r) == 0]
    predicted = predicted_early  # lead time stats only over genuinely-early hits

    gated_steps = sum(int(r.get("gated_steps") or 0) for r in reports)
    over_flags = sum(int(r.get("over_flag_count") or 0) for r in reports)

    # Lead time only meaningful where A2R2 predicted a failure.
    lead_values = [
        r.get("timeline", {}).get("a2r2_lead_over_failure")
        for r in predicted
        if isinstance(r.get("timeline", {}).get("a2r2_lead_over_failure"), (int, float))
    ]
    lead_over_agent_values = [
        r.get("timeline", {}).get("a2r2_lead_over_agent_guard")
        for r in reports
        if isinstance(r.get("timeline", {}).get("a2r2_lead_over_agent_guard"), (int, float))
    ]

    # Overhead is weighted by gated steps so episodes with more actions count more.
    overhead_weighted_sum = 0.0
    overhead_weight = 0
    for r in reports:
        avg = r.get("avg_a2r2_overhead_ms_per_action")
        n = int(r.get("gated_steps") or 0)
        if isinstance(avg, (int, float)) and n > 0:
            overhead_weighted_sum += float(avg) * n
            overhead_weight += n
    avg_overhead = overhead_weighted_sum / overhead_weight if overhead_weight else None

    label_counts: Dict[str, int] = {}
    for r in reports:
        for label, count in (r.get("agent_reported_failure_labels") or {}).items():
            label_counts[str(label)] = label_counts.get(str(label), 0) + int(count)

    return {
        "schema_version": SCHEMA_VERSION,
        "episodes_total": episodes_total,
        "episodes_failed": len(failed),
        "episodes_succeeded": len(succeeded),
        "gated_steps_total": gated_steps,
        "metrics": {
            "predicted_early_rate": _rate(len(predicted_early), len(failed)),
            "coincident_flag_rate": _rate(len(coincident), len(failed)),
            "flagged_at_or_before_rate": _rate(len(matched), len(failed)),
            "blind_spot_rate": _rate(len(blind), len(failed)),
            "over_flag_rate": _rate(over_flags, gated_steps),
            "mean_lead_over_failure": (mean(lead_values) if lead_values else None),
            "median_lead_over_failure": (median(lead_values) if lead_values else None),
            "mean_lead_over_agent_guard": (mean(lead_over_agent_values) if lead_over_agent_values else None),
            "avg_overhead_ms_per_action": avg_overhead,
        },
        "agent_reported_failure_labels": label_counts,
        "episodes": [
            {
                "episode_id": r.get("episode_id"),
                "goal": r.get("goal"),
                "final_success": r.get("final_success"),
                "matched_failure": r.get("a2r2_prediction_matched_failure"),
                "captured_not_predicted": r.get("failure_captured_not_predicted"),
                "over_flag_count": r.get("over_flag_count"),
                "source": r.get("_source_path"),
            }
            for r in reports
        ],
    }


def _fmt_rate(metric: Any) -> str:
    if not isinstance(metric, dict):
        return "-"
    value = metric.get("value")
    if value is None:
        return "n/a ({0}/{1})".format(metric.get("numerator"), metric.get("denominator"))
    return "{0:.0%} ({1}/{2})".format(float(value), metric.get("numerator"), metric.get("denominator"))


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return "{0:.2f}".format(value)
    return str(value)


def render_markdown(agg: Dict[str, Any]) -> str:
    m = agg.get("metrics", {})
    lines = [
        "# A2R2 Shadow Aggregate Scorecard",
        "",
        "Corpus-level rollup of real-agent shadow episodes. Denominators are real",
        "(failed episodes / gated steps), not assumed.",
        "",
        "- Episodes: {0} ({1} failed, {2} succeeded)".format(
            agg.get("episodes_total"), agg.get("episodes_failed"), agg.get("episodes_succeeded")
        ),
        "- Gated steps total: {0}".format(agg.get("gated_steps_total")),
        "",
        "| Metric | Value |",
        "|---|---:|",
        "| Predicted-early rate (lead > 0, of failures) | {0} |".format(_fmt_rate(m.get("predicted_early_rate"))),
        "| Coincident-flag rate (lead = 0) | {0} |".format(_fmt_rate(m.get("coincident_flag_rate"))),
        "| Flagged at-or-before rate (lead >= 0) | {0} |".format(_fmt_rate(m.get("flagged_at_or_before_rate"))),
        "| Blind-spot rate (captured, not predicted) | {0} |".format(_fmt_rate(m.get("blind_spot_rate"))),
        "| Over-flag rate (of gated steps) | {0} |".format(_fmt_rate(m.get("over_flag_rate"))),
        "| Mean lead over failure (steps) | {0} |".format(_fmt_num(m.get("mean_lead_over_failure"))),
        "| Median lead over failure (steps) | {0} |".format(_fmt_num(m.get("median_lead_over_failure"))),
        "| Mean lead over agent guard (steps) | {0} |".format(_fmt_num(m.get("mean_lead_over_agent_guard"))),
        "| Avg overhead / action (ms) | {0} |".format(_fmt_num(m.get("avg_overhead_ms_per_action"))),
        "",
        "Agent-reported failure categories: {0}".format(agg.get("agent_reported_failure_labels") or "-"),
        "",
        "## Episodes",
        "",
        "| Episode | Success | Matched failure | Captured not predicted | Over-flags |",
        "|---|---|---|---|---:|",
    ]
    for ep in agg.get("episodes", []):
        lines.append(
            "| {id} | {ok} | {matched} | {cap} | {of} |".format(
                id=ep.get("episode_id"),
                ok=_fmt_num(ep.get("final_success")),
                matched=_fmt_num(ep.get("matched_failure")),
                cap=_fmt_num(ep.get("captured_not_predicted")),
                of=_fmt_num(ep.get("over_flag_count")),
            )
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Aggregate A2R2 shadow reports into a corpus scorecard.")
    parser.add_argument("--reports-dir", default="data/reports", help="Dir scanned recursively for shadow_report.json.")
    parser.add_argument("--out", default=None, help="Optional Markdown output path (also writes .json beside it).")
    args = parser.parse_args(argv)

    reports = load_reports(args.reports_dir)
    agg = aggregate(reports)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(agg), encoding="utf-8")
        out_path.with_suffix(".json").write_text(
            json.dumps(agg, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print("aggregate_markdown={0}".format(out_path))
    else:
        print(json.dumps(agg, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
