from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig


READY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.FrameLayout">
    <node text="Continue" resource-id="example:id/continue" clickable="true" enabled="true" />
    <node text="Retry" resource-id="example:id/retry" clickable="true" enabled="true" />
    <node text="Send" resource-id="example:id/send" clickable="true" enabled="true" />
    <node text="Draft" resource-id="example:id/draft" clickable="false" enabled="true" />
  </node>
</hierarchy>
"""

LOADING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.FrameLayout">
    <node class="android.widget.ProgressBar" text="Loading" clickable="false" enabled="true" />
    <node text="Continue" resource-id="example:id/continue" clickable="true" enabled="true" />
  </node>
</hierarchy>
"""

SENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.FrameLayout">
    <node text="Message sent" resource-id="example:id/sent" clickable="false" enabled="true" />
  </node>
</hierarchy>
"""


@dataclass(frozen=True)
class DemoStep:
    case_id: str
    title: str
    before: Observation
    action: ProposedAction
    baseline_after: Observation
    a2r2_after: Observation
    labels: List[str]


def build_demo_steps() -> List[DemoStep]:
    loading = Observation(
        ui_tree_hash="loading_screen",
        metadata={"xml_text": LOADING_XML, "visible_text": ["Loading", "Continue"]},
    )
    ready_send = Observation(
        ui_tree_hash="ready_send",
        metadata={"xml_text": READY_XML, "visible_text": ["Continue", "Retry", "Send", "Draft"]},
    )
    sent = Observation(
        ui_tree_hash="sent",
        metadata={"xml_text": SENT_XML, "visible_text": ["Message sent"]},
    )
    retry = Observation(
        ui_tree_hash="retry_screen",
        metadata={"xml_text": READY_XML, "visible_text": ["Retry"]},
    )
    draft = Observation(
        ui_tree_hash="draft_screen",
        metadata={
            "xml_text": READY_XML,
            "visible_text": ["Draft"],
            "goal_marker_present": False,
        },
    )
    return [
        DemoStep(
            case_id="loading_tap",
            title="External agent taps while the UI is still loading",
            before=loading,
            action=ProposedAction(action_type="tap", target_text="Continue"),
            baseline_after=loading,
            a2r2_after=loading,
            labels=["non_ready_action"],
        ),
        DemoStep(
            case_id="unsafe_send",
            title="External agent proposes an irreversible-looking Send action",
            before=ready_send,
            action=ProposedAction(action_type="tap", target_text="Send"),
            baseline_after=sent,
            a2r2_after=ready_send,
            labels=["unsafe_action"],
        ),
        DemoStep(
            case_id="retry_no_progress_1",
            title="External agent repeats a Retry tap with no visible progress",
            before=retry,
            action=ProposedAction(action_type="tap", target_text="Retry"),
            baseline_after=retry,
            a2r2_after=retry,
            labels=["no_progress"],
        ),
        DemoStep(
            case_id="retry_no_progress_2",
            title="The same no-progress action repeats",
            before=retry,
            action=ProposedAction(action_type="tap", target_text="Retry"),
            baseline_after=retry,
            a2r2_after=retry,
            labels=["no_progress"],
        ),
        DemoStep(
            case_id="retry_stuck_loop",
            title="The repeated no-progress action becomes a stuck loop",
            before=retry,
            action=ProposedAction(action_type="tap", target_text="Retry"),
            baseline_after=retry,
            a2r2_after=retry,
            labels=["stuck_loop"],
        ),
        DemoStep(
            case_id="false_done",
            title="External agent claims done while the goal marker is absent",
            before=draft,
            action=ProposedAction(action_type="done", raw={"agent_claimed_success": True}),
            baseline_after=draft,
            a2r2_after=draft,
            labels=["false_success"],
        ),
    ]


def run_demo(trace_dir: Path, out_dir: Path) -> Dict[str, Any]:
    goal = "Comparison demo: show why a reliability runtime changes failure visibility."
    runtime = ReliabilityRuntime(
        RuntimeConfig(
            traces_root=str(trace_dir),
            episode_id="comparison_demo_{0}".format(uuid.uuid4().hex[:8]),
            agent_name="scripted_external_agent",
            runtime_mode="a2r2_comparison_demo",
            agent_meta={"demo": "baseline_vs_a2r2"},
        )
    )
    history: List[dict] = []
    rows: List[Dict[str, Any]] = []
    steps = build_demo_steps()

    for step in steps:
        decision = runtime.check_before_action(goal, step.before, step.action, history)
        verification = runtime.verify_after_action(goal, step.before, step.action, step.a2r2_after, history)
        record = runtime.record_step(goal, step.before, step.action, decision, step.a2r2_after, verification, history)
        history.append(record)
        diagnosis_labels = _diagnosis_labels(decision.diagnosis_label, verification.diagnosis_label)
        rows.append(
            {
                "case_id": step.case_id,
                "title": step.title,
                "labels": step.labels,
                "proposal": _action_label(step.action),
                "baseline": _baseline_result(step),
                "a2r2": {
                    "decision": decision.decision,
                    "allowed": decision.allowed,
                    "diagnosis_label": diagnosis_labels[0] if diagnosis_labels else None,
                    "diagnosis_labels": diagnosis_labels,
                    "progress_made": verification.progress_made,
                    "false_success_candidate": verification.false_success_candidate,
                    "reason": decision.reason,
                    "evidence": list(decision.evidence) + list(verification.evidence),
                },
            }
        )

    runtime.write_episode_summary(goal=goal, final_success=False, agent_claimed_success=True)
    trace_paths = runtime.export_trace()
    report = {
        "schema_version": "comparison_demo.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Scripted demonstration only; not a benchmark and not a final success-rate claim.",
        "goal": goal,
        "trace": trace_paths,
        "summary": {
            "total_proposals": len(steps),
            "baseline": _baseline_summary(steps),
            "a2r2": _a2r2_summary(rows),
        },
        "rows": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (out_dir / "comparison.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "comparison.html").write_text(render_html(report), encoding="utf-8")
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# A2R2 Baseline Comparison Demo",
        "",
        "This is a scripted visibility demo, not a benchmark. The same external-agent proposals are compared in two paths:",
        "",
        "- Baseline: proposed actions are treated as directly executed.",
        "- + A2R2: proposed actions are gated, verified, diagnosed, and traced.",
        "",
        "## Summary",
        "",
        "| Metric | Baseline direct execution | + A2R2 rules |",
        "|---|---:|---:|",
        "| Unsafe action leaks | {0} | {1} |".format(
            summary["baseline"]["unsafe_action_leaks"],
            summary["a2r2"]["unsafe_action_leaks"],
        ),
        "| Non-ready action leaks | {0} | {1} |".format(
            summary["baseline"]["non_ready_action_leaks"],
            summary["a2r2"]["non_ready_action_leaks"],
        ),
        "| False success caught | {0} | {1} |".format(
            summary["baseline"]["false_success_detected"],
            summary["a2r2"]["false_success_detected"],
        ),
        "| Stuck/no-progress labels | {0} | {1} |".format(
            summary["baseline"]["stuck_or_no_progress_detected"],
            summary["a2r2"]["stuck_or_no_progress_detected"],
        ),
        "| Step-level traces | {0} | {1} |".format(
            summary["baseline"]["trace_records"],
            summary["a2r2"]["trace_records"],
        ),
        "",
        "## Step Comparison",
        "",
        "| Case | Proposal | Ground truth signal | Baseline result | A2R2 result |",
        "|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        lines.append(
            "| {case} | {proposal} | {labels} | {baseline} | {a2r2} |".format(
                case=row["case_id"],
                proposal=row["proposal"],
                labels=", ".join(row["labels"]),
                baseline=row["baseline"]["result"],
                a2r2="{0} / {1}".format(row["a2r2"]["decision"], _labels_text(row["a2r2"])),
            )
        )
    lines.extend(
        [
            "",
            "Trace output: `{0}`".format(report["trace"]["episode_dir"]),
            "",
        ]
    )
    return "\n".join(lines)


def render_html(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    cards = [
        ("Unsafe leaks", summary["baseline"]["unsafe_action_leaks"], summary["a2r2"]["unsafe_action_leaks"]),
        ("Non-ready leaks", summary["baseline"]["non_ready_action_leaks"], summary["a2r2"]["non_ready_action_leaks"]),
        ("False success caught", summary["baseline"]["false_success_detected"], summary["a2r2"]["false_success_detected"]),
        ("Trace records", summary["baseline"]["trace_records"], summary["a2r2"]["trace_records"]),
    ]
    card_html = "\n".join(
        "<div class='metric'><div class='label'>{0}</div><div class='values'><span class='bad'>{1}</span><span class='good'>{2}</span></div><div class='hint'>Baseline / + A2R2</div></div>".format(
            _html(label),
            _html(str(baseline)),
            _html(str(a2r2)),
        )
        for label, baseline, a2r2 in cards
    )
    row_html = "\n".join(
        "<tr><td><b>{case}</b><br><span>{title}</span></td><td>{proposal}</td><td>{labels}</td><td class='baseline'>{baseline}</td><td class='{klass}'>{a2r2}</td></tr>".format(
            case=_html(row["case_id"]),
            title=_html(row["title"]),
            proposal=_html(row["proposal"]),
            labels=_html(", ".join(row["labels"])),
            baseline=_html(row["baseline"]["result"]),
            klass="a2r2-blocked" if not row["a2r2"]["allowed"] else "a2r2-allowed",
            a2r2=_html("{0} / {1}".format(row["a2r2"]["decision"], _labels_text(row["a2r2"]))),
        )
        for row in report["rows"]
    )
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A2R2 Baseline Comparison Demo</title>
<style>
  body { margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #f6f7f9; color: #17202a; }
  header { padding: 28px 36px 16px; background: #ffffff; border-bottom: 1px solid #d7dde5; }
  h1 { margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }
  p { margin: 0; color: #536170; line-height: 1.5; }
  main { padding: 24px 36px 40px; }
  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .metric { background: #fff; border: 1px solid #d7dde5; border-radius: 8px; padding: 14px; }
  .label { font-size: 13px; color: #536170; }
  .values { display: flex; gap: 18px; align-items: baseline; font-size: 28px; font-weight: 700; margin: 8px 0 2px; }
  .hint { font-size: 12px; color: #6b7785; }
  .bad { color: #b42318; }
  .good { color: #067647; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d7dde5; border-radius: 8px; overflow: hidden; }
  th, td { padding: 12px 14px; text-align: left; border-bottom: 1px solid #e3e7ec; vertical-align: top; }
  th { background: #eef2f6; color: #344054; font-size: 13px; }
  td span { color: #536170; font-size: 13px; }
  .baseline { color: #b42318; font-weight: 600; }
  .a2r2-blocked { color: #067647; font-weight: 700; }
  .a2r2-allowed { color: #175cd3; font-weight: 700; }
  .note { margin-top: 14px; color: #536170; font-size: 13px; }
</style>
</head>
<body>
<header>
  <h1>A2R2 Baseline Comparison Demo</h1>
  <p>Same external-agent proposals, two paths: direct execution vs. A2R2 gate / verify / diagnose / trace.</p>
</header>
<main>
  <section class="metrics">
    """ + card_html + """
  </section>
  <table>
    <thead><tr><th>Case</th><th>Proposal</th><th>Signal</th><th>Baseline</th><th>+ A2R2</th></tr></thead>
    <tbody>
      """ + row_html + """
    </tbody>
  </table>
  <p class="note">Scripted demonstration only. It shows failure visibility and action interception, not task success-rate improvement.</p>
</main>
</body>
</html>
"""


def _baseline_result(step: DemoStep) -> Dict[str, Any]:
    if "unsafe_action" in step.labels:
        result = "executed; unsafe action leaked"
    elif "non_ready_action" in step.labels:
        result = "executed while non-ready"
    elif "false_success" in step.labels:
        result = "accepted done claim"
    elif "stuck_loop" in step.labels:
        result = "executed again; loop not diagnosed"
    elif "no_progress" in step.labels:
        result = "executed; no progress not diagnosed"
    else:
        result = "executed"
    return {"executed": True, "result": result}


def _baseline_summary(steps: Iterable[DemoStep]) -> Dict[str, Any]:
    step_list = list(steps)
    return {
        "executed_actions": len(step_list),
        "unsafe_action_leaks": _count_labels(step_list, "unsafe_action"),
        "non_ready_action_leaks": _count_labels(step_list, "non_ready_action"),
        "false_success_detected": 0,
        "false_success_missed": _count_labels(step_list, "false_success"),
        "stuck_or_no_progress_detected": 0,
        "trace_records": 0,
    }


def _a2r2_summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    row_list = list(rows)
    return {
        "allowed_actions": sum(1 for row in row_list if row["a2r2"]["allowed"]),
        "blocked_or_handoff": sum(1 for row in row_list if not row["a2r2"]["allowed"]),
        "unsafe_action_leaks": sum(1 for row in row_list if "unsafe_action" in row["labels"] and row["a2r2"]["allowed"]),
        "non_ready_action_leaks": sum(1 for row in row_list if "non_ready_action" in row["labels"] and row["a2r2"]["allowed"]),
        "false_success_detected": sum(1 for row in row_list if row["a2r2"]["false_success_candidate"]),
        "stuck_or_no_progress_detected": sum(
            1
            for row in row_list
            if any(
                label in {"no_progress", "stuck_loop", "loading_loop"}
                for label in row["a2r2"].get("diagnosis_labels", [])
            )
        ),
        "trace_records": len(row_list),
    }


def _count_labels(steps: Iterable[DemoStep], label: str) -> int:
    return sum(1 for step in steps if label in step.labels)


def _action_label(action: ProposedAction) -> str:
    target = action.target_text or action.target_resource_id or ""
    if target:
        return "{0}({1})".format(action.action_type, target)
    return action.action_type


def _diagnosis_labels(*labels: Any) -> List[str]:
    decision_label = str(labels[0]) if len(labels) > 0 and labels[0] else ""
    verification_label = str(labels[1]) if len(labels) > 1 and labels[1] else ""
    if decision_label:
        visible = [decision_label]
        if verification_label in {"stuck_loop", "false_success"} and verification_label != decision_label:
            visible.append(verification_label)
        return visible
    if verification_label:
        return [verification_label]
    return []


def _labels_text(payload: Dict[str, Any]) -> str:
    labels = payload.get("diagnosis_labels")
    if isinstance(labels, list) and labels:
        return ", ".join(str(label) for label in labels)
    return str(payload.get("diagnosis_label") or "-")


def _html(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a scripted Baseline vs A2R2 comparison demo.")
    parser.add_argument("--trace-dir", default="data/traces", help="Directory where A2R2 trace.v1 episodes are written.")
    parser.add_argument("--out-dir", default=None, help="Directory where comparison reports are written.")
    args = parser.parse_args(argv)

    default_name = "a2r2_comparison_demo_{0}".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    out_dir = Path(args.out_dir) if args.out_dir else Path("data/reports") / default_name
    report = run_demo(trace_dir=Path(args.trace_dir), out_dir=out_dir)
    print("comparison_json={0}".format(out_dir / "comparison.json"))
    print("comparison_markdown={0}".format(out_dir / "comparison.md"))
    print("comparison_html={0}".format(out_dir / "comparison.html"))
    print("trace_episode={0}".format(report["trace"]["episode_dir"]))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
