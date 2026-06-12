"""Step-level retrospection of a recorded episode via an evidence provider.

Flow:

    trace.v1 episode -> build_review_plan (select flagged/suspicious steps)
                     -> run_review (provider judges each selected step)
                     -> review.v1 (per-step verdicts + first wrong step)

Cost discipline: by default only steps that A2R2 flagged are reviewed, so a
typical failed episode costs 1-3 provider calls. `score_exam` measures the
reviewer's agreement against human-verified cases — the reviewer is not trusted
until it passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SCHEMA_VERSION = "review.v1"


def _load_steps(episode_dir: str) -> List[Dict[str, Any]]:
    path = Path(episode_dir) / "steps.jsonl"
    if not path.exists():
        return []
    steps: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    steps.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return steps


def _action_desc(proposed: Mapping[str, Any]) -> str:
    action_type = str(proposed.get("action_type") or "unknown")
    target = proposed.get("target_text") or proposed.get("target_resource_id")
    if target:
        return "{0}({1})".format(action_type, target)
    if proposed.get("x") is not None and proposed.get("y") is not None:
        return "{0} at ({1},{2})".format(action_type, proposed.get("x"), proposed.get("y"))
    return action_type


def _step_labels(step: Mapping[str, Any]) -> List[str]:
    diagnosis = step.get("diagnosis") if isinstance(step.get("diagnosis"), Mapping) else {}
    labels = [
        diagnosis.get("label"),
        diagnosis.get("decision_label"),
        diagnosis.get("progress_label"),
    ]
    return sorted({str(label) for label in labels if label})


def build_review_plan(episode_dir: str, only_flagged: bool = True) -> List[Dict[str, Any]]:
    plan: List[Dict[str, Any]] = []
    for step in _load_steps(episode_dir):
        labels = _step_labels(step)
        if only_flagged and not labels:
            continue
        before = step.get("before_state") if isinstance(step.get("before_state"), Mapping) else {}
        proposed = step.get("proposed_action") if isinstance(step.get("proposed_action"), Mapping) else {}
        raw = proposed.get("raw") if isinstance(proposed.get("raw"), Mapping) else {}
        plan.append(
            {
                "episode_dir": str(episode_dir),
                "step_index": step.get("step_index"),
                "goal": step.get("goal"),
                "action_desc": _action_desc(proposed),
                "claims_done": bool(raw.get("agent_claimed_success")),
                "screenshot_path": before.get("screenshot_path"),
                "labels": labels,
            }
        )
    return plan


def run_review(plan: Iterable[Mapping[str, Any]], provider: Any) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    first_wrong: Optional[int] = None
    for item in plan:
        verdict = provider.assess(
            goal=str(item.get("goal") or ""),
            action_desc=str(item.get("action_desc") or ""),
            screenshot_path=item.get("screenshot_path"),
            claims_done=bool(item.get("claims_done")),
        )
        record = {
            "step_index": item.get("step_index"),
            "action_desc": item.get("action_desc"),
            "labels": item.get("labels"),
            "verdict": verdict.to_dict(),
        }
        steps.append(record)
        if verdict.reasonable is False and first_wrong is None:
            first_wrong = item.get("step_index")
    return {
        "schema_version": SCHEMA_VERSION,
        "episode_dir": None,  # filled by callers that know it
        "steps_reviewed": len(steps),
        "first_wrong_step": first_wrong,
        "steps": steps,
    }


def score_exam(cases: Iterable[Mapping[str, Any]], provider: Any) -> Dict[str, Any]:
    """Score the reviewer against human-verified cases.

    Each case: {goal, action_desc, screenshot_path?, claims_done?,
    expected_reasonable: bool}. Returns agreement stats; the reviewer should not
    label training data until agreement is acceptable to a human.
    """
    total = agree = answered = 0
    rows: List[Dict[str, Any]] = []
    for case in cases:
        total += 1
        verdict = provider.assess(
            goal=str(case.get("goal") or ""),
            action_desc=str(case.get("action_desc") or ""),
            screenshot_path=case.get("screenshot_path"),
            claims_done=bool(case.get("claims_done")),
        )
        expected = bool(case.get("expected_reasonable"))
        if verdict.reasonable is not None:
            answered += 1
            if verdict.reasonable == expected:
                agree += 1
        rows.append(
            {
                "action_desc": case.get("action_desc"),
                "expected_reasonable": expected,
                "got": verdict.reasonable,
                "reason": verdict.reason,
            }
        )
    return {
        "cases": total,
        "answered": answered,
        "agreed": agree,
        "agreement_rate": (agree / float(answered)) if answered else None,
        "rows": rows,
    }


def render_markdown(review: Dict[str, Any]) -> str:
    lines = [
        "# A2R2 Episode Review",
        "",
        "- Steps reviewed: {0}".format(review.get("steps_reviewed")),
        "- First wrong step (reviewer): {0}".format(review.get("first_wrong_step")),
        "",
        "| Step | Action | A2R2 labels | Reasonable | Goal evidence | Reason |",
        "|---:|---|---|---|---|---|",
    ]
    for step in review.get("steps", []):
        verdict = step.get("verdict") or {}
        lines.append(
            "| {0} | {1} | `{2}` | {3} | {4} | {5} |".format(
                step.get("step_index"),
                step.get("action_desc"),
                step.get("labels"),
                verdict.get("reasonable"),
                verdict.get("goal_evidence"),
                str(verdict.get("reason") or "")[:80],
            )
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Review one episode's flagged steps via an evidence provider.")
    parser.add_argument("--episode-dir", required=True)
    parser.add_argument("--provider", choices=("static", "dashscope"), default="static")
    parser.add_argument("--model", default=None, help="Provider model name (dashscope only).")
    parser.add_argument("--all-steps", action="store_true", help="Review every step, not only flagged ones.")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    if args.provider == "dashscope":
        from a2r2.evidence import DashScopeEvidenceProvider

        provider = DashScopeEvidenceProvider(model=args.model) if args.model else DashScopeEvidenceProvider()
    else:
        from a2r2.evidence import StaticEvidenceProvider

        provider = StaticEvidenceProvider()

    plan = build_review_plan(args.episode_dir, only_flagged=not args.all_steps)
    review = run_review(plan, provider)
    review["episode_dir"] = str(args.episode_dir)
    rendered = render_markdown(review)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        out_path.with_suffix(".json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print("review_markdown={0}".format(out_path))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
