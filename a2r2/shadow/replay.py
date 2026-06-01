"""Offline A/B replay for candidate `target_missing` rules on real data.

The replay reads stored shadow reports plus paired trace.v1 steps and evaluates
candidate rules without changing the live policy. It answers the practical
question before shipping a gate:

* how many real failures would the candidate catch at or before the failing
  step, and
* how many it would predict earlier than the failing step (lead > 0), and
* how many actually successful steps would it wrongly flag.

Two variants are measured:

* `actionable_targets`: target is absent from `before_state.metadata.possible_targets`.
  This is the refined signal because it sees icon/content-desc controls.
* `text_only_reference`: target is absent from raw visible text/XML. This is kept
  as a reference because it explains the known false positives on icon buttons
  such as Save.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from a2r2.policies.common import observation_text
from a2r2.shadow.aggregate import load_reports
from a2r2.types import Observation, ProposedAction

SCHEMA_VERSION = "shadow_replay.v1"

_TAP_TYPES = frozenset({"tap", "click"})
_TARGET_KEYS = ("label", "text", "content_desc", "resource_id", "target_id", "hint")
_CONCERN_VERIFY_LABELS = frozenset({"no_progress", "stuck_loop", "false_success"})


def candidate_target_missing(before: Observation, action: ProposedAction) -> bool:
    """Refined signal: named tap target is absent from actionable UI facts."""
    if not _is_named_tap(action):
        return False
    target_text = str(action.target_text or "").strip()
    target_id = str(action.target_resource_id or "").strip()
    target_facts = (before.metadata or {}).get("possible_targets")
    if not isinstance(target_facts, list):
        return False  # old traces lack actionable facts; do not guess
    if _target_facts_incomplete(before.metadata or {}, target_facts):
        return False
    if not target_facts:
        return True
    return not any(
        isinstance(target, Mapping) and _target_fact_matches(target, target_text, target_id)
        for target in target_facts
    )


def candidate_target_missing_text_only(before: Observation, action: ProposedAction) -> bool:
    """Reference signal: target is absent from raw observable text/XML."""
    if not _is_named_tap(action):
        return False
    target = str(action.target_text or action.target_resource_id or "").strip().lower()
    if not target:
        return False
    corpus = observation_text(before).lower()
    if not corpus.strip():
        return False
    return target not in corpus


def _is_named_tap(action: ProposedAction) -> bool:
    if str(action.action_type or "").lower() not in _TAP_TYPES:
        return False
    return bool(str(action.target_text or action.target_resource_id or "").strip())


def _has_actionable_target_facts(before: Observation, action: ProposedAction) -> bool:
    metadata = before.metadata or {}
    target_facts = metadata.get("possible_targets")
    return (
        _is_named_tap(action)
        and isinstance(target_facts, list)
        and not _target_facts_incomplete(metadata, target_facts)
    )


def _target_fact_matches(target: Mapping[str, Any], target_text: str, target_id: str) -> bool:
    haystack = " ".join(str(target.get(key) or "").strip().lower() for key in _TARGET_KEYS)
    wanted_id = target_id.lower()
    wanted_text = target_text.lower()
    if wanted_id and wanted_id in haystack:
        return True
    if wanted_text and wanted_text in haystack:
        return True
    return False


def _target_facts_incomplete(metadata: Mapping[str, Any], target_facts: List[Any]) -> bool:
    if bool(metadata.get("possible_targets_truncated")):
        return True
    total = metadata.get("possible_target_total_count", metadata.get("possible_target_count"))
    if isinstance(total, int) and total > len(target_facts):
        return True
    if isinstance(total, int) and total >= 50 and len(target_facts) >= 50:
        return True
    if len(target_facts) >= 50 and "possible_targets_truncated" not in metadata:
        return True
    return False


def _obs(state: Optional[Dict[str, Any]]) -> Observation:
    state = state or {}
    return Observation(
        screenshot_path=state.get("screenshot_path"),
        xml_path=state.get("xml_path"),
        activity=state.get("activity"),
        package=state.get("package"),
        ui_tree_hash=state.get("ui_tree_hash"),
        metadata=state.get("metadata") or {},
    )


def _action(pa: Optional[Dict[str, Any]]) -> ProposedAction:
    pa = pa or {}
    return ProposedAction(
        action_type=str(pa.get("action_type") or ""),
        x=pa.get("x"),
        y=pa.get("y"),
        target_text=pa.get("target_text"),
        target_resource_id=pa.get("target_resource_id"),
        raw=pa.get("raw"),
    )


def _load_trace(episode_dir: Optional[str]) -> List[Dict[str, Any]]:
    if not episode_dir:
        return []
    path = Path(episode_dir) / "steps.jsonl"
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _rate(num: int, den: int) -> Dict[str, Any]:
    if den <= 0:
        return {"value": None, "numerator": num, "denominator": den}
    return {"value": num / float(den), "numerator": num, "denominator": den}


def _candidate_bucket(description: str) -> Dict[str, Any]:
    return {
        "description": description,
        "caught_at_or_before": 0,
        "predicted_early": 0,
        "coincident_gate": 0,
        "caught_failures": [],
        "over_flag_steps": 0,
        "over_flag_examples": [],
        "success_step_flags": 0,
        "success_step_flag_examples": [],
    }


def _is_over_flag_cost(row: Mapping[str, Any], final_success: Any) -> bool:
    """Match ShadowSession's current over-flag definition."""
    return (
        row.get("agent_step_success") is True
        and final_success is True
        and row.get("progress_made") is not False
        and row.get("verify_label") not in _CONCERN_VERIFY_LABELS
    )


def replay_corpus(reports_dir: str) -> Dict[str, Any]:
    reports = load_reports(reports_dir)

    failures_total = 0
    failures_with_trace = 0
    baseline_early = 0
    gated_success_steps = 0
    actionable_fact_steps = 0
    missing_actionable_fact_steps = 0

    candidates: Dict[str, Dict[str, Any]] = {
        "actionable_targets": _candidate_bucket(
            "tap target absent from before-screen possible_targets"
        ),
        "text_only_reference": _candidate_bucket(
            "tap target absent from before-screen text/XML"
        ),
    }

    for report in reports:
        final_success = report.get("final_success")
        rows = report.get("rows", []) if isinstance(report.get("rows"), list) else []
        gated_rows = [r for r in rows if r.get("gated")]
        trace = _load_trace(report.get("episode_dir"))
        pairs = list(zip(gated_rows, trace))

        flags: Dict[str, List[bool]] = {name: [] for name in candidates}
        for row, step in pairs:
            before = _obs(step.get("before_state"))
            action = _action(step.get("proposed_action"))

            if _is_named_tap(action):
                if _has_actionable_target_facts(before, action):
                    actionable_fact_steps += 1
                else:
                    missing_actionable_fact_steps += 1

            step_flags = {
                "actionable_targets": candidate_target_missing(before, action),
                "text_only_reference": candidate_target_missing_text_only(before, action),
            }
            for name, flag in step_flags.items():
                flags[name].append(flag)

            agent_ok = row.get("agent_step_success")
            if agent_ok is True and final_success is True:
                gated_success_steps += 1
                for name, flag in step_flags.items():
                    if not flag:
                        continue
                    example = {
                        "episode_id": report.get("episode_id"),
                        "goal": report.get("goal"),
                        "target": action.target_text or action.target_resource_id,
                        "page": (before.metadata or {}).get("page"),
                        "verify_label": row.get("verify_label"),
                        "progress_made": row.get("progress_made"),
                    }
                    candidates[name]["success_step_flags"] += 1
                    if len(candidates[name]["success_step_flag_examples"]) < 12:
                        candidates[name]["success_step_flag_examples"].append(example)
                    if _is_over_flag_cost(row, final_success):
                        candidates[name]["over_flag_steps"] += 1
                        if len(candidates[name]["over_flag_examples"]) < 12:
                            candidates[name]["over_flag_examples"].append(example)

        if final_success is not False:
            continue

        failures_total += 1
        if pairs:
            failures_with_trace += 1
        lead = report.get("timeline", {}).get("a2r2_lead_over_failure")
        if isinstance(lead, (int, float)) and lead > 0:
            baseline_early += 1

        fail_idx = next(
            (i for i, (row, _) in enumerate(pairs) if row.get("agent_step_success") is False),
            None,
        )
        for name, candidate_flags in flags.items():
            cand_idx = next((i for i, flag in enumerate(candidate_flags) if flag), None)
            if fail_idx is None or cand_idx is None or cand_idx > fail_idx:
                continue
            lead = fail_idx - cand_idx
            candidates[name]["caught_at_or_before"] += 1
            if lead > 0:
                candidates[name]["predicted_early"] += 1
            else:
                candidates[name]["coincident_gate"] += 1
            candidates[name]["caught_failures"].append(
                {
                    "episode_id": report.get("episode_id"),
                    "goal": report.get("goal"),
                    "target": _action(pairs[fail_idx][1].get("proposed_action")).target_text,
                    "candidate_lead": lead,
                }
            )

    rendered_candidates: Dict[str, Any] = {}
    for name, data in candidates.items():
        rendered_candidates[name] = {
            "description": data["description"],
            "caught_at_or_before": data["caught_at_or_before"],
            "predicted_early": data["predicted_early"],
            "coincident_gate": data["coincident_gate"],
            "caught_at_or_before_rate": _rate(data["caught_at_or_before"], failures_total),
            "predicted_early_rate": _rate(data["predicted_early"], failures_total),
            "coincident_gate_rate": _rate(data["coincident_gate"], failures_total),
            "over_flag": {
                "rate": _rate(data["over_flag_steps"], gated_success_steps),
                "steps": data["over_flag_steps"],
                "gated_success_steps": gated_success_steps,
                "examples": data["over_flag_examples"],
            },
            "success_step_flags": {
                "rate": _rate(data["success_step_flags"], gated_success_steps),
                "steps": data["success_step_flags"],
                "gated_success_steps": gated_success_steps,
                "examples": data["success_step_flag_examples"],
            },
            "caught_failures": data["caught_failures"],
        }

    primary = rendered_candidates["actionable_targets"]
    return {
        "schema_version": SCHEMA_VERSION,
        "reports_dir": str(reports_dir),
        "candidate": "target_missing: tap target absent from before-screen actionable targets",
        "failures_total": failures_total,
        "failures_with_trace": failures_with_trace,
        "baseline_predicted_early": baseline_early,
        "candidate_caught_early": primary["predicted_early"],
        "candidate_caught_at_or_before": primary["caught_at_or_before"],
        "early_detection": {
            "baseline_rate": _rate(baseline_early, failures_total),
            "candidate_rate": primary["predicted_early_rate"],
            "candidate_at_or_before_rate": primary["caught_at_or_before_rate"],
        },
        "over_flag": primary["over_flag"],
        "candidate_caught_failures": primary["caught_failures"],
        "actionable_target_fact_coverage": {
            "available_named_tap_steps": actionable_fact_steps,
            "missing_named_tap_steps": missing_actionable_fact_steps,
        },
        "candidates": rendered_candidates,
    }


def render_markdown(rep: Dict[str, Any]) -> str:
    ed = rep.get("early_detection", {})

    def _r(metric: Any) -> str:
        if not isinstance(metric, dict):
            return "-"
        value = metric.get("value")
        if value is None:
            return "n/a ({0}/{1})".format(metric.get("numerator"), metric.get("denominator"))
        return "{0:.0%} ({1}/{2})".format(
            value, metric.get("numerator"), metric.get("denominator")
        )

    candidates = rep.get("candidates", {}) if isinstance(rep.get("candidates"), dict) else {}
    action = candidates.get("actionable_targets", {})
    text_ref = candidates.get("text_only_reference", {})
    action_over = action.get("over_flag") or {}
    text_over = text_ref.get("over_flag") or {}
    action_success_flags = action.get("success_step_flags") or {}
    text_success_flags = text_ref.get("success_step_flags") or {}
    coverage = rep.get("actionable_target_fact_coverage", {})

    lines = [
        "# A2R2 Candidate Rule Replay (target_missing)",
        "",
        "Offline A/B of candidate rules on the stored real corpus. The rules are NOT",
        "live enforcement decisions; this measures whether they are worth shipping.",
        "",
        "Primary candidate: {0}".format(rep.get("candidate")),
        "",
        "| Metric | Baseline | Actionable-target candidate | Text-only reference |",
        "|---|---:|---:|---:|",
        "| Failures predicted earlier step (lead > 0) | {0} | {1} | {2} |".format(
            _r(ed.get("baseline_rate")),
            _r(action.get("predicted_early_rate")),
            _r(text_ref.get("predicted_early_rate")),
        ),
        "| Failures caught at/before failing action | - | {0} | {1} |".format(
            _r(action.get("caught_at_or_before_rate")),
            _r(text_ref.get("caught_at_or_before_rate")),
        ),
        "| Coincident gate catches (lead = 0) | - | {0} | {1} |".format(
            _r(action.get("coincident_gate_rate")),
            _r(text_ref.get("coincident_gate_rate")),
        ),
        "| Over-flag cost | - | {0} | {1} |".format(
            _r(action_over.get("rate")),
            _r(text_over.get("rate")),
        ),
        "| Raw success-step flags | - | {0} | {1} |".format(
            _r(action_success_flags.get("rate")),
            _r(text_success_flags.get("rate")),
        ),
        "",
        "- Failures total: {0} ({1} have replayable traces)".format(
            rep.get("failures_total"), rep.get("failures_with_trace")
        ),
        "- Actionable target facts available on named tap steps: {0}".format(
            coverage.get("available_named_tap_steps")
        ),
        "- Named tap steps missing actionable target facts: {0}".format(
            coverage.get("missing_named_tap_steps")
        ),
        "",
        "## Failures the actionable-target candidate would catch at/before the failing action",
        "",
    ]
    for item in action.get("caught_failures", []):
        lines.append(
            "- `{0}` target `{1}` (lead {2}) - {3}".format(
                item.get("episode_id"),
                item.get("target"),
                item.get("candidate_lead"),
                item.get("goal"),
            )
        )
    lines.extend(["", "## Over-flag examples (actionable-target candidate)", ""])
    if not action_over.get("examples"):
        lines.append("- (none)")
    for item in action_over.get("examples", []):
        lines.append(
            "- `{0}` target `{1}` on page `{2}`".format(
                item.get("episode_id"), item.get("target"), item.get("page")
            )
        )

    lines.extend(["", "## Text-only reference over-flags", ""])
    if not text_over.get("examples"):
        lines.append("- (none)")
    for item in text_over.get("examples", []):
        lines.append(
            "- `{0}` target `{1}` on page `{2}`".format(
                item.get("episode_id"), item.get("target"), item.get("page")
            )
        )
    lines.extend(["", "## Raw success-step flags (not necessarily over-flag cost)", ""])
    raw_examples = action_success_flags.get("examples") or []
    if not raw_examples:
        lines.append("- (none)")
    for item in raw_examples:
        lines.append(
            "- `{0}` target `{1}` on page `{2}` (verify `{3}`, progress `{4}`)".format(
                item.get("episode_id"),
                item.get("target"),
                item.get("page"),
                item.get("verify_label"),
                item.get("progress_made"),
            )
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Replay candidate target_missing rules.")
    parser.add_argument("--reports-dir", default="data/reports")
    parser.add_argument("--out", default="data/reports/a2r2_replay_target_missing.md")
    args = parser.parse_args(argv)

    rep = replay_corpus(args.reports_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(rep), encoding="utf-8")
    out_path.with_suffix(".json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    action = rep.get("candidates", {}).get("actionable_targets", {})
    over = action.get("over_flag", {})
    print("replay_markdown={0}".format(out_path))
    print(
        "failures={0} baseline_early={1} actionable_at_or_before={2} actionable_over_flags={3}/{4}".format(
            rep.get("failures_total"),
            rep.get("baseline_predicted_early"),
            action.get("caught_at_or_before"),
            over.get("steps"),
            over.get("gated_success_steps"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
