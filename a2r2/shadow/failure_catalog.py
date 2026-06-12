"""Walk every failed shadow episode and record it in a self-contained,
model-readable failure catalog.

The goal is that another model (or a person) can read `failures.md` / `failures.json`
cold -- with no access to this codebase or conversation -- and understand, for
each real failure:

* what the agent was trying to do (goal + the failing step),
* what was on screen when it failed,
* why it failed (the agent's own reported reason + a generic category),
* what A2R2 would have decided and verified, and
* in plain language, whether A2R2 helped (predicted early / flagged coincidentally
  / was blind) and why.

Near-identical failures are grouped by signature so the catalog shows patterns,
not noise. It is read-only and offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from a2r2.shadow.aggregate import load_reports

SCHEMA_VERSION = "failure_catalog.v1"


def _load_trace_steps(episode_dir: Optional[str]) -> List[Dict[str, Any]]:
    if not episode_dir:
        return []
    path = Path(episode_dir) / "steps.jsonl"
    if not path.exists():
        return []
    steps: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return steps


def _episode_outcome(report: Dict[str, Any]) -> str:
    matched = report.get("a2r2_prediction_matched_failure")
    lead = report.get("timeline", {}).get("a2r2_lead_over_failure")
    if matched is True and isinstance(lead, (int, float)) and lead > 0:
        return "predicted_early"
    if matched is True and lead == 0:
        return "coincident"
    if matched is True:
        return "flagged_unknown_lead"
    return "blind"


_OUTCOME_PLAIN = {
    "predicted_early": "A2R2 flagged a relevant concern BEFORE the failing step (genuine early prediction).",
    "coincident": "A2R2 flagged at the SAME step the agent failed (lead 0) -- a coincident no_progress flag, not a prediction.",
    "flagged_unknown_lead": "A2R2 flagged the episode but the lead could not be computed.",
    "blind": "A2R2 did NOT flag this failure at all -- a blind spot.",
}


def _screen_context(row: Dict[str, Any], gated_rows: List[Dict[str, Any]], trace_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Gated rows are recorded as trace steps in the same order; pair by index.
    try:
        idx = gated_rows.index(row)
    except ValueError:
        idx = None
    step = trace_steps[idx] if (idx is not None and idx < len(trace_steps)) else None
    if not step:
        return {"screen_page": None, "screen_visible_text": [], "screen_package": None}
    before = step.get("before_state", {}) if isinstance(step.get("before_state"), dict) else {}
    meta = before.get("metadata", {}) if isinstance(before.get("metadata"), dict) else {}
    visible = meta.get("visible_text") or []
    return {
        "screen_page": meta.get("page"),
        "screen_visible_text": [str(t) for t in visible[:8]],
        "screen_package": before.get("package"),
    }


def _make_entry(report: Dict[str, Any], row: Dict[str, Any], ctx: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    return {
        "episode_id": report.get("episode_id"),
        "goal": report.get("goal"),
        "failing_step": row.get("seq"),
        "skill": row.get("skill"),
        "intended_target": row.get("target"),
        "screen_page": ctx.get("screen_page"),
        "screen_package": ctx.get("screen_package"),
        "screen_visible_text": ctx.get("screen_visible_text"),
        "why_it_failed": row.get("detail"),
        "failure_category": row.get("agent_reported_failure_label"),
        "a2r2_would_decide": row.get("would_decision"),
        "a2r2_gate_label": row.get("would_label"),
        "a2r2_verify_label": row.get("verify_label"),
        "a2r2_flagged": row.get("a2r2_flagged"),
        "a2r2_outcome": outcome,
        "a2r2_assessment": _OUTCOME_PLAIN.get(outcome, outcome),
        "source": report.get("_source_path"),
    }


def _signature(entry: Dict[str, Any]) -> str:
    detail = str(entry.get("why_it_failed") or "")
    # Strip a trailing concrete target so "find tap target: save" and
    # "... : send" group by shape, not by the specific word.
    short = detail.split(":")[0].strip().lower() if ":" in detail else detail.strip().lower()
    return "|".join(
        [
            str(entry.get("goal") or ""),
            str(entry.get("skill") or ""),
            str(entry.get("failure_category") or ""),
            short[:80],
            str(entry.get("a2r2_outcome") or ""),
        ]
    )


def build_catalog(reports_dir: str) -> Dict[str, Any]:
    reports = load_reports(reports_dir)
    failed = [r for r in reports if r.get("final_success") is False]

    entries: List[Dict[str, Any]] = []
    for report in failed:
        outcome = _episode_outcome(report)
        rows = report.get("rows", []) if isinstance(report.get("rows"), list) else []
        gated_rows = [row for row in rows if row.get("gated")]
        trace_steps = _load_trace_steps(report.get("episode_dir"))
        failing_rows = [row for row in rows if row.get("agent_step_success") is False]
        if failing_rows:
            for row in failing_rows:
                ctx = _screen_context(row, gated_rows, trace_steps)
                entries.append(_make_entry(report, row, ctx, outcome))
        else:
            # Episode marked failed but no single step reported failure.
            entries.append(
                {
                    "episode_id": report.get("episode_id"),
                    "goal": report.get("goal"),
                    "failing_step": None,
                    "skill": None,
                    "intended_target": None,
                    "why_it_failed": "Episode ended unsuccessful with no single failing step.",
                    "failure_category": "episode_level_failure",
                    "a2r2_outcome": outcome,
                    "a2r2_assessment": _OUTCOME_PLAIN.get(outcome, outcome),
                    "source": report.get("_source_path"),
                    "screen_visible_text": [],
                }
            )

    # Group by signature.
    groups: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        sig = _signature(entry)
        group = groups.setdefault(
            sig,
            {"signature": sig, "count": 0, "episode_ids": [], "representative": entry},
        )
        group["count"] += 1
        if entry.get("episode_id"):
            group["episode_ids"].append(entry["episode_id"])

    by_category: Dict[str, int] = {}
    by_outcome: Dict[str, int] = {}
    for entry in entries:
        cat = str(entry.get("failure_category") or "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1
        out = str(entry.get("a2r2_outcome") or "unknown")
        by_outcome[out] = by_outcome.get(out, 0) + 1

    grouped = sorted(groups.values(), key=lambda g: g["count"], reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "reports_dir": str(reports_dir),
        "failed_episodes": len(failed),
        "failing_step_records": len(entries),
        "failure_groups": len(grouped),
        "by_category": by_category,
        "by_a2r2_outcome": by_outcome,
        "groups": grouped,
        "all_records": entries,
    }


def render_markdown(catalog: Dict[str, Any]) -> str:
    lines = [
        "# A2R2 Shadow Failure Catalog",
        "",
        "Every failed real-agent episode, recorded so it can be read cold (by a",
        "person or another model) with no extra context.",
        "",
        "- Failed episodes: {0}".format(catalog.get("failed_episodes")),
        "- Distinct failure patterns: {0}".format(catalog.get("failure_groups")),
        "- By category: {0}".format(catalog.get("by_category")),
        "- By A2R2 outcome: {0}".format(catalog.get("by_a2r2_outcome")),
        "",
        "## Failure patterns (grouped, most common first)",
        "",
    ]
    for i, group in enumerate(catalog.get("groups", []), start=1):
        rep = group.get("representative", {})
        vt = rep.get("screen_visible_text") or []
        lines.extend(
            [
                "### {0}. {1} x{2}".format(
                    i,
                    rep.get("failure_category") or "unknown",
                    group.get("count"),
                ),
                "",
                "- Goal: {0}".format(rep.get("goal")),
                "- Failing step: skill `{0}`, intended target `{1}`".format(
                    rep.get("skill"), rep.get("intended_target")
                ),
                "- Screen when it failed: page `{0}`, package `{1}`".format(
                    rep.get("screen_page"), rep.get("screen_package")
                ),
                "- Visible text sample: {0}".format(", ".join(vt) if vt else "(none captured)"),
                "- Why it failed (agent-reported): {0}".format(rep.get("why_it_failed")),
                "- A2R2 would decide: `{0}` (gate label `{1}`, verify label `{2}`, flagged={3})".format(
                    rep.get("a2r2_would_decide"),
                    rep.get("a2r2_gate_label"),
                    rep.get("a2r2_verify_label"),
                    rep.get("a2r2_flagged"),
                ),
                "- A2R2 outcome: **{0}** — {1}".format(
                    rep.get("a2r2_outcome"), rep.get("a2r2_assessment")
                ),
                "- Occurrences: {0} episode(s)".format(group.get("count")),
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Build a model-readable catalog of real shadow failures.")
    parser.add_argument("--reports-dir", default="data/reports", help="Dir scanned recursively for shadow_report.json.")
    parser.add_argument("--out", default="data/reports/a2r2_failure_catalog.md", help="Markdown output path (also writes .json).")
    args = parser.parse_args(argv)

    catalog = build_catalog(args.reports_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(catalog), encoding="utf-8")
    out_path.with_suffix(".json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("failure_catalog_markdown={0}".format(out_path))
    print("failed_episodes={0} patterns={1} by_outcome={2}".format(
        catalog.get("failed_episodes"), catalog.get("failure_groups"), catalog.get("by_a2r2_outcome")
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
