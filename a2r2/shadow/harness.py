"""Shadow session: observe a real agent's steps and record A2R2's would-decisions.

`ShadowSession.on_step` is designed to be wired in as the `step_observer` of the
legacy `app.executor.Executor`. It receives one event per executed step and:

* builds before/after Observations and a ProposedAction from the legacy
  (skill, args, screen summary) via the adapter,
* asks the A2R2 runtime what it *would* decide and verify (it never blocks),
* records a standard trace.v1 step, and
* tracks the timeline needed to compute lead time, over-flagging, and overhead.

It is intentionally decoupled from the agent: it can also be driven by synthetic
events in tests, with no device involved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.adapters.legacy_app import (
    classify_agent_failure,
    is_action_skill,
    observation_from_summary,
    proposed_action_from_step,
)

# Verify labels that count as A2R2 raising a reliability concern.
_CONCERN_VERIFY_LABELS = frozenset({"no_progress", "stuck_loop", "false_success"})
# Legacy skills / signals that mean the agent's own guards flagged trouble.
_AGENT_SELF_FLAG_SKILLS = frozenset({"manual_intervention"})


@dataclass
class ShadowStepRow:
    seq: int
    step_index: int
    skill: str
    action_type: str
    target: Optional[str]
    gated: bool
    would_decision: Optional[str]
    would_allowed: Optional[bool]
    would_label: Optional[str]
    verify_label: Optional[str]
    progress_made: Optional[bool]
    false_success_candidate: Optional[bool]
    agent_step_success: bool
    agent_self_flagged: bool
    a2r2_flagged: bool
    overhead_ms: Optional[float]
    agent_reported_failure_label: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


class ShadowSession:
    def __init__(
        self,
        goal: str,
        trace_dir: str = "data/traces/shadow",
        agent_name: str = "legacy_app_shadow",
        episode_id: Optional[str] = None,
        agent_meta: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.goal = goal
        meta = {"harness": "a2r2_shadow"}
        if agent_meta:
            meta.update(dict(agent_meta))
        self.runtime = ReliabilityRuntime(
            RuntimeConfig(
                traces_root=trace_dir,
                episode_id=episode_id,
                agent_name=agent_name,
                runtime_mode="shadow",
                enforce=False,
                agent_meta=meta,
            )
        )
        self.history: List[dict] = []
        self.rows: List[ShadowStepRow] = []
        self.errors: List[str] = []
        self._seq = 0
        self._final_success: Optional[bool] = None
        self._agent_claimed_success: Optional[bool] = None

    # -- observer ---------------------------------------------------------- #
    def on_step(self, event: Mapping[str, Any]) -> None:
        """Wired in as Executor.step_observer. Must never raise."""
        try:
            self._handle_step(event)
        except Exception as exc:  # shadow safety: never break the agent
            self.errors.append("{0}: {1}".format(type(exc).__name__, exc))

    def _handle_step(self, event: Mapping[str, Any]) -> None:
        self._seq += 1
        skill = str(event.get("skill") or "")
        args = event.get("args") or {}
        success = bool(event.get("success"))
        detail = str(event.get("detail") or "")
        before = observation_from_summary(event.get("before_summary"))
        after = observation_from_summary(event.get("after_summary"))
        action = proposed_action_from_step(skill, args)

        agent_self_flagged = (
            skill in _AGENT_SELF_FLAG_SKILLS
            or not success
            or bool((event.get("data") or {}).get("manual_intervention"))
        )
        # Capture the failure category the agent effectively reported. Recorded
        # separately from A2R2's own diagnosis: capturing a category is NOT a
        # claim that A2R2 predicted the failure early.
        agent_reported_failure_label = None
        if not success:
            agent_reported_failure_label = classify_agent_failure(detail, skill)

        gated = is_action_skill(skill)
        would_decision = would_allowed = would_label = None
        verify_label = progress_made = false_success_candidate = None
        a2r2_flagged = False
        overhead_ms: Optional[float] = None

        if gated:
            if agent_reported_failure_label:
                after.metadata["agent_reported_failure_label"] = agent_reported_failure_label
                after.metadata["agent_reported_failure_detail"] = detail[:200]
            start = time.perf_counter()
            decision = self.runtime.check_before_action(self.goal, before, action, self.history)
            verification = self.runtime.verify_after_action(self.goal, before, action, after, self.history)
            overhead_ms = (time.perf_counter() - start) * 1000.0
            record = self.runtime.record_step(
                self.goal, before, action, decision, after, verification, self.history
            )
            self.history.append(record)

            would_decision = decision.decision
            would_allowed = decision.allowed
            would_label = decision.diagnosis_label
            verify_label = verification.diagnosis_label
            progress_made = verification.progress_made
            false_success_candidate = verification.false_success_candidate
            a2r2_flagged = (not would_allowed) or (verify_label in _CONCERN_VERIFY_LABELS)

        self.rows.append(
            ShadowStepRow(
                seq=self._seq,
                step_index=int(event.get("step_index") or self._seq),
                skill=skill,
                action_type=action.action_type,
                target=action.target_text,
                gated=gated,
                would_decision=would_decision,
                would_allowed=would_allowed,
                would_label=would_label,
                verify_label=verify_label,
                progress_made=progress_made,
                false_success_candidate=false_success_candidate,
                agent_step_success=success,
                agent_self_flagged=agent_self_flagged,
                a2r2_flagged=a2r2_flagged,
                overhead_ms=overhead_ms,
                agent_reported_failure_label=agent_reported_failure_label,
                detail=detail[:200],
            )
        )

    # -- finalize / report -------------------------------------------------- #
    def finalize(
        self, final_success: Optional[bool], agent_claimed_success: Optional[bool] = None
    ) -> Dict[str, Any]:
        self._final_success = final_success
        self._agent_claimed_success = agent_claimed_success
        self.runtime.write_episode_summary(
            goal=self.goal,
            final_success=final_success,
            agent_claimed_success=agent_claimed_success,
        )
        return self.build_report()

    def _first_seq(self, predicate) -> Optional[int]:
        for row in self.rows:
            if predicate(row):
                return row.seq
        return None

    def build_report(self) -> Dict[str, Any]:
        episode_failed = self._final_success is False
        failure_step = self._first_seq(lambda r: not r.agent_step_success)
        a2r2_first_flag = self._first_seq(lambda r: r.a2r2_flagged)
        agent_first_self_flag = self._first_seq(lambda r: r.agent_self_flagged)

        def lead(reference: Optional[int]) -> Optional[int]:
            if reference is None or a2r2_first_flag is None:
                return None
            return reference - a2r2_first_flag

        # A2R2 caught the failure early if the episode failed and A2R2 raised a
        # concern at or before the first failing step.
        matched_failure = None
        if episode_failed:
            matched_failure = a2r2_first_flag is not None and (
                failure_step is None or a2r2_first_flag <= failure_step
            )

        gated_rows = [r for r in self.rows if r.gated]

        def is_over_flag(row: ShadowStepRow) -> bool:
            return bool(
                row.would_allowed is False
                and row.agent_step_success
                and self._final_success is True
                and row.progress_made is not False
                and row.verify_label not in _CONCERN_VERIFY_LABELS
            )

        over_flag = sum(1 for r in gated_rows if is_over_flag(r))
        overheads = [r.overhead_ms for r in gated_rows if r.overhead_ms is not None]
        avg_overhead = sum(overheads) / len(overheads) if overheads else None

        agent_reported_failures = [
            {"seq": r.seq, "skill": r.skill, "label": r.agent_reported_failure_label, "detail": r.detail}
            for r in self.rows
            if r.agent_reported_failure_label
        ]
        failure_label_counts: Dict[str, int] = {}
        for item in agent_reported_failures:
            label = str(item["label"])
            failure_label_counts[label] = failure_label_counts.get(label, 0) + 1
        # The key shadow signal: the agent failed with a captured category, but
        # A2R2's rules did not predict it early.
        failure_captured_not_predicted = bool(
            episode_failed and agent_reported_failures and not matched_failure
        )

        episode = self.runtime.export_trace()
        report = {
            "schema_version": "shadow_report.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "goal": self.goal,
            "episode_id": self.runtime.episode_id,
            "episode_dir": episode["episode_dir"],
            "final_success": self._final_success,
            "agent_claimed_success": self._agent_claimed_success,
            "steps_total": len(self.rows),
            "gated_steps": len(gated_rows),
            "would_block_steps": sum(1 for r in gated_rows if r.would_allowed is False),
            "a2r2_flag_steps": sum(1 for r in self.rows if r.a2r2_flagged),
            "agent_self_flag_steps": sum(1 for r in self.rows if r.agent_self_flagged),
            "timeline": {
                "first_failure_step": failure_step,
                "a2r2_first_flag_step": a2r2_first_flag,
                "agent_first_self_flag_step": agent_first_self_flag,
                "a2r2_lead_over_failure": lead(failure_step),
                "a2r2_lead_over_agent_guard": lead(agent_first_self_flag),
            },
            "a2r2_prediction_matched_failure": matched_failure,
            "failure_captured_not_predicted": failure_captured_not_predicted,
            "agent_reported_failures": agent_reported_failures,
            "agent_reported_failure_labels": failure_label_counts,
            "over_flag_count": over_flag,
            "avg_a2r2_overhead_ms_per_action": avg_overhead,
            "errors": list(self.errors),
            "rows": [
                dict(r.to_dict(), over_flag=is_over_flag(r) if r.gated else False)
                for r in self.rows
            ],
        }
        return report


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return "{0:.2f}".format(value)
    return str(value)


def render_markdown(report: Dict[str, Any]) -> str:
    tl = report.get("timeline", {})
    lines = [
        "# A2R2 Shadow Run Report",
        "",
        "Shadow mode: A2R2 observed a real agent run without intervening.",
        "",
        "- Goal: {0}".format(report.get("goal")),
        "- Episode: `{0}`".format(report.get("episode_id")),
        "- Final success (ground truth): {0}".format(_fmt(report.get("final_success"))),
        "",
        "## Headline",
        "",
        "| Question | Value |",
        "|---|---:|",
        "| A2R2 predicted the failure early | {0} |".format(_fmt(report.get("a2r2_prediction_matched_failure"))),
        "| Failure captured by shadow but NOT predicted by A2R2 | {0} |".format(_fmt(report.get("failure_captured_not_predicted"))),
        "| A2R2 lead over failure (steps) | {0} |".format(_fmt(tl.get("a2r2_lead_over_failure"))),
        "| A2R2 lead over agent's own guard (steps) | {0} |".format(_fmt(tl.get("a2r2_lead_over_agent_guard"))),
        "| Over-flag count (would block a step that succeeded) | {0} |".format(_fmt(report.get("over_flag_count"))),
        "| Avg A2R2 overhead / action (ms) | {0} |".format(_fmt(report.get("avg_a2r2_overhead_ms_per_action"))),
        "",
        "Agent-reported failure categories (captured by shadow): {0}".format(
            _fmt(report.get("agent_reported_failure_labels") or "-")
        ),
        "",
        "## Counts",
        "",
        "| Metric | Value |",
        "|---|---:|",
        "| Steps total | {0} |".format(_fmt(report.get("steps_total"))),
        "| Gated steps | {0} |".format(_fmt(report.get("gated_steps"))),
        "| Steps A2R2 would block/wait/handoff | {0} |".format(_fmt(report.get("would_block_steps"))),
        "| Steps A2R2 flagged a concern | {0} |".format(_fmt(report.get("a2r2_flag_steps"))),
        "| Steps the agent's own guards flagged | {0} |".format(_fmt(report.get("agent_self_flag_steps"))),
        "",
        "## Steps",
        "",
        "| # | Skill | Would | Gate label | Verify label | Agent ok | A2R2 flag | Over-flag |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in report.get("rows", []):
        lines.append(
            "| {seq} | {skill} | {would} | {glabel} | {vlabel} | {ok} | {flag} | {over} |".format(
                seq=row.get("seq"),
                skill=row.get("skill"),
                would=_fmt(row.get("would_decision")) if row.get("gated") else "(not gated)",
                glabel=_fmt(row.get("would_label")),
                vlabel=_fmt(row.get("verify_label")),
                ok=_fmt(row.get("agent_step_success")),
                flag=_fmt(row.get("a2r2_flagged")),
                over=_fmt(row.get("over_flag")),
            )
        )
    lines.append("")
    lines.append("Trace: `{0}`".format(report.get("episode_dir")))
    lines.append("")
    return "\n".join(lines) + "\n"


def _html(value: Any) -> str:
    return (
        _fmt(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(report: Dict[str, Any]) -> str:
    tl = report.get("timeline", {})
    cards = [
        ("Predicted failure early", report.get("a2r2_prediction_matched_failure")),
        ("Captured, not predicted", report.get("failure_captured_not_predicted")),
        ("Lead over failure (steps)", tl.get("a2r2_lead_over_failure")),
        ("Lead over agent guard", tl.get("a2r2_lead_over_agent_guard")),
        ("Over-flags", report.get("over_flag_count")),
        ("Overhead/action (ms)", report.get("avg_a2r2_overhead_ms_per_action")),
        ("Agent failure labels", report.get("agent_reported_failure_labels") or "-"),
    ]
    card_html = "\n".join(
        "<div class='metric'><div class='label'>{0}</div><div class='value'>{1}</div></div>".format(
            _html(label), _html(value)
        )
        for label, value in cards
    )
    rows = []
    for row in report.get("rows", []):
        flagged = bool(row.get("a2r2_flagged"))
        ok = bool(row.get("agent_step_success"))
        klass = "row-flag" if flagged else ("row-ok" if ok else "row-fail")
        rows.append(
            "<tr class='{klass}'><td>{seq}</td><td>{skill}</td><td>{would}</td>"
            "<td>{glabel}</td><td>{vlabel}</td><td>{ok}</td><td>{flag}</td><td>{over}</td></tr>".format(
                klass=klass,
                seq=_html(row.get("seq")),
                skill=_html(row.get("skill")),
                would=_html(row.get("would_decision")) if row.get("gated") else "(not gated)",
                glabel=_html(row.get("would_label")),
                vlabel=_html(row.get("verify_label")),
                ok=_html(row.get("agent_step_success")),
                flag=_html(row.get("a2r2_flagged")),
                over=_html(row.get("over_flag")),
            )
        )
    row_html = "\n".join(rows)
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A2R2 Shadow Run Report</title>
<style>
  body { margin:0; font-family: Segoe UI, Arial, sans-serif; background:#f6f7f9; color:#17202a; }
  header { padding:24px 32px; background:#fff; border-bottom:1px solid #d7dde5; }
  h1 { margin:0 0 6px; font-size:24px; }
  p { margin:0; color:#536170; }
  main { padding:22px 32px 40px; }
  .metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }
  .metric { background:#fff; border:1px solid #d7dde5; border-radius:8px; padding:14px; }
  .metric .label { font-size:13px; color:#536170; }
  .metric .value { font-size:22px; font-weight:700; margin-top:8px; }
  table { width:100%; border-collapse:collapse; background:#fff; border:1px solid #d7dde5; border-radius:8px; overflow:hidden; }
  th,td { padding:9px 11px; text-align:left; border-bottom:1px solid #e3e7ec; font-size:13px; }
  th { background:#eef2f6; color:#344054; }
  .row-flag td { background:#fff4ed; }
  .row-fail td { background:#fde9e7; }
  .note { margin-top:14px; color:#536170; font-size:13px; }
</style></head>
<body>
<header><h1>A2R2 Shadow Run Report</h1>
<p>A2R2 observed a real agent run without intervening (shadow mode).</p></header>
<main>
  <section class="metrics">
    """ + card_html + """
  </section>
  <table>
    <thead><tr><th>#</th><th>Skill</th><th>Would</th><th>Gate label</th><th>Verify label</th><th>Agent ok</th><th>A2R2 flag</th><th>Over-flag</th></tr></thead>
    <tbody>
      """ + row_html + """
    </tbody>
  </table>
  <p class="note">Shadow mode never blocks the agent. "Would" is what A2R2 would have decided if enforcing.</p>
</main>
</body></html>
"""
