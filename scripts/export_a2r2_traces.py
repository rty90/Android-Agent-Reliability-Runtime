from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.types import ProgressVerification, RuntimeDecision


DEFAULT_REPORT_ROOTS = (
    Path("data/tmp/chaos"),
    Path("data/tmp/chaos_e2e"),
    Path("data/tmp/long_tail"),
    Path("data/tmp/capability_ladder"),
    Path("data/tmp/diagnostics"),
)
REPORT_NAMES = {
    "report.json",
    "e2e_report.json",
    "long_tail_report.json",
    "capability_ladder_report.json",
    "diagnostic.json",
}
FAIL_STATUSES = {"fail", "failed", "error", "timeout"}
NON_READY_STATUSES = {"blocked", "loading", "uncertain"}
BLOCKING_SKILLS = {"", "<complete>", "wait", "manual_handoff", "diagnose"}


def convert_reports(
    roots: Sequence[Path],
    out_dir: Path,
    latest: int = 50,
) -> Dict[str, Any]:
    paths = sorted(iter_report_paths(roots), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest > 0:
        paths = paths[:latest]
    converted: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for path in paths:
        payload = _load_json(path)
        if payload is None:
            skipped.append(str(path))
            continue
        converted.append(convert_report(path, payload, out_dir))
    return {
        "converted": len(converted),
        "skipped": len(skipped),
        "episodes": converted,
        "skipped_paths": skipped,
        "out_dir": str(out_dir),
    }


def convert_report(path: Path, payload: Mapping[str, Any], out_dir: Path) -> Dict[str, Any]:
    report_type = _report_type(path, payload)
    episode_id = _episode_id(path, payload)
    runtime = ReliabilityRuntime(
        RuntimeConfig(
            traces_root=str(out_dir),
            episode_id=episode_id,
            agent_name="existing_harness_report",
            runtime_mode="report_conversion",
            agent_meta={
                "source_report": str(path),
                "report_type": report_type,
                "converted_from_existing_report": True,
            },
        )
    )
    goal = _text(payload.get("goal")) or _text(payload.get("case")) or report_type
    history: List[Dict[str, Any]] = []
    failure_labels: List[str] = []
    false_success = False

    for item in _step_items(report_type, payload):
        step_goal = _text(item.get("goal")) or goal
        action = _action_from(item)
        decision = _decision_from(item)
        verification = _verification_from(item)
        before = _observation_from(item, path, state="before")
        after = _observation_from(item, path, state="after")
        record = runtime.record_step(
            goal=step_goal,
            before_observation=before,
            proposed_action=action,
            decision=decision,
            after_observation=after,
            verification=verification,
            history=history,
        )
        history.append(record)
        if decision.diagnosis_label:
            failure_labels.append(decision.diagnosis_label)
        if verification.diagnosis_label:
            failure_labels.append(verification.diagnosis_label)
        false_success = false_success or verification.false_success_candidate

    final_status = _status(payload.get("status"))
    runtime.write_episode_summary(
        goal=goal,
        final_success=final_status == "pass" if final_status else None,
        agent_claimed_success=bool(payload.get("agent_claimed_success")),
        trace_complete=True,
    )
    exported = runtime.export_trace()
    exported.update(
        {
            "source_report": str(path),
            "report_type": report_type,
            "step_count": len(history),
            "failure_labels": sorted(set(failure_labels)),
            "false_success": false_success,
        }
    )
    return exported


def iter_report_paths(roots: Sequence[Path]) -> Iterable[Path]:
    for root in roots:
        target = root if root.is_absolute() else REPO_ROOT / root
        if target.is_file() and target.name in REPORT_NAMES:
            yield target
            continue
        if not target.exists():
            continue
        for path in target.rglob("*.json"):
            if path.name in REPORT_NAMES:
                yield path


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _report_type(path: Path, payload: Mapping[str, Any]) -> str:
    if path.name == "capability_ladder_report.json":
        return "capability_ladder"
    if path.name == "long_tail_report.json":
        return "long_tail"
    if path.name == "e2e_report.json":
        return "chaos_e2e"
    if path.name == "diagnostic.json" or payload.get("schema_version") == "agent.diagnostic.v1":
        return "diagnostic"
    return "chaos"


def _step_items(report_type: str, payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if report_type == "long_tail":
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        return [dict(item) for item in results if isinstance(item, Mapping)] or [dict(payload)]
    if report_type == "capability_ladder":
        items: List[Dict[str, Any]] = []
        levels = payload.get("levels") if isinstance(payload.get("levels"), list) else []
        for level in levels:
            if not isinstance(level, Mapping):
                continue
            for case in level.get("cases") or []:
                if not isinstance(case, Mapping):
                    continue
                item = dict(case)
                item.setdefault("ladder_level", level.get("level_id"))
                items.append(item)
        return items or [dict(payload)]
    return [dict(payload)]


def _action_from(item: Mapping[str, Any]) -> ProposedAction:
    decision = item.get("decision") if isinstance(item.get("decision"), Mapping) else {}
    args = decision.get("args") if isinstance(decision.get("args"), Mapping) else {}
    skill = _text(decision.get("skill")) or _text(item.get("skill")) or "done"
    return ProposedAction(
        action_type=skill,
        x=_optional_int(args.get("x")),
        y=_optional_int(args.get("y")),
        target_text=_first_text(args, ("target", "target_text", "text", "label")),
        target_resource_id=_first_text(args, ("target_resource_id", "resource_id", "target_id")),
        raw=dict(decision) if decision else {"source_status": _status(item.get("status"))},
    )


def _decision_from(item: Mapping[str, Any]) -> RuntimeDecision:
    readiness = _readiness_from(item)
    readiness_status = _status(readiness.get("status"))
    readiness_label = _text(readiness.get("label"))
    decision_payload = item.get("decision") if isinstance(item.get("decision"), Mapping) else {}
    skill = _text(decision_payload.get("skill")) or "<complete>"
    status = _status(item.get("status"))
    diagnosis = _diagnosis_from_readiness(readiness_status, readiness_label)
    evidence = _evidence(item, readiness, decision_payload)

    if _unsafe_label(item):
        return RuntimeDecision(
            decision="manual_handoff",
            allowed=False,
            reason="Converted report indicates unsafe action handling.",
            diagnosis_label="unsafe_action",
            evidence=evidence + ["converted_report"],
            confidence=0.70,
            policy_name="ReportTraceConverter",
        )

    if diagnosis:
        blocked = skill in BLOCKING_SKILLS
        return RuntimeDecision(
            decision="wait" if skill == "wait" else "block" if blocked else "allow",
            allowed=not blocked,
            reason="Converted report readiness status={0}, skill={1}.".format(readiness_status, skill),
            diagnosis_label=diagnosis,
            evidence=evidence,
            confidence=0.65,
            policy_name="ReportTraceConverter",
        )

    return RuntimeDecision(
        decision="allow" if status not in FAIL_STATUSES else "block",
        allowed=status not in FAIL_STATUSES,
        reason=_text(item.get("reason")) or "Converted existing harness report.",
        diagnosis_label=None if status not in FAIL_STATUSES else _text(item.get("kind")) or "no_progress",
        evidence=evidence,
        confidence=0.60,
        policy_name="ReportTraceConverter",
    )


def _verification_from(item: Mapping[str, Any]) -> ProgressVerification:
    status = _status(item.get("status"))
    readiness = _readiness_from(item)
    readiness_status = _status(readiness.get("status"))
    skill = _text((item.get("decision") or {}).get("skill")) if isinstance(item.get("decision"), Mapping) else ""
    false_success = bool(
        status == "pass"
        and readiness_status in NON_READY_STATUSES
        and not skill
    )
    diagnosis = None
    if false_success:
        diagnosis = "false_success"
    elif status in FAIL_STATUSES:
        diagnosis = _text(item.get("failure_label")) or _diagnosis_from_readiness(
            readiness_status,
            _text(readiness.get("label")),
        )
    return ProgressVerification(
        progress_made=status == "pass" and not false_success,
        ui_changed=None,
        xml_changed=None,
        screenshot_changed=None,
        agent_claimed_success=status == "pass" and not skill,
        false_success_candidate=false_success,
        diagnosis_label=diagnosis,
        evidence=["converted_status:{0}".format(status)],
    )


def _observation_from(item: Mapping[str, Any], path: Path, state: str) -> Observation:
    readiness = _readiness_from(item)
    artifacts_dir = _text(item.get("artifacts_dir")) or _text(item.get("artifacts")) or str(path.parent)
    metadata = {
        "source_report": str(path),
        "source_state": state,
        "case": _text(item.get("case")) or _text(item.get("kind")) or path.parent.name,
        "status": _status(item.get("status")),
        "reason": _text(item.get("reason")),
        "readiness": readiness,
        "artifacts_dir": artifacts_dir,
    }
    return Observation(
        screenshot_path=_artifact_path(item, "screenshot_path"),
        xml_path=_artifact_path(item, "ui_dump_path") or _artifact_path(item, "xml_path"),
        ui_tree_hash="{0}:{1}:{2}:{3}".format(
            path.name,
            metadata["case"],
            state,
            _text(readiness.get("label")) or _status(readiness.get("status")) or metadata["status"],
        ),
        metadata=metadata,
    )


def _readiness_from(item: Mapping[str, Any]) -> Dict[str, Any]:
    direct = item.get("readiness")
    if isinstance(direct, Mapping):
        return dict(direct)
    ui_state = item.get("ui_state")
    if isinstance(ui_state, Mapping) and isinstance(ui_state.get("readiness"), Mapping):
        return dict(ui_state["readiness"])
    return {}


def _diagnosis_from_readiness(status: str, label: str) -> Optional[str]:
    if status not in NON_READY_STATUSES:
        return None
    lowered = label.lower()
    if "webview" in lowered or "browser_content" in lowered or "blank" in lowered:
        return "blank_webview"
    if "modal" in lowered or "dialog" in lowered or "permission" in lowered or "blocker" in lowered:
        return "modal_blocker"
    if "loading" in lowered or status == "loading":
        return "non_ready_action"
    return "non_ready_action"


def _unsafe_label(item: Mapping[str, Any]) -> bool:
    combined = " ".join(
        _text(value).lower()
        for value in (
            item.get("failure_label"),
            item.get("reason"),
            item.get("kind"),
        )
    )
    return "unsafe" in combined


def _evidence(
    item: Mapping[str, Any],
    readiness: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> List[str]:
    evidence: List[str] = ["converted_report"]
    if readiness.get("status"):
        evidence.append("readiness_status:{0}".format(readiness.get("status")))
    if readiness.get("label"):
        evidence.append("readiness_label:{0}".format(readiness.get("label")))
    if decision.get("skill"):
        evidence.append("skill:{0}".format(decision.get("skill")))
    if item.get("reason"):
        evidence.append("reason:{0}".format(_text(item.get("reason"))[:160]))
    return evidence


def _artifact_path(item: Mapping[str, Any], key: str) -> Optional[str]:
    value = item.get(key)
    if value:
        return str(value)
    diagnostic = item.get("diagnostic")
    if isinstance(diagnostic, Mapping) and diagnostic.get(key):
        return str(diagnostic.get(key))
    return None


def _episode_id(path: Path, payload: Mapping[str, Any]) -> str:
    stem = "{0}_{1}".format(_report_type(path, payload), path.parent.name)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_")[:80] or "converted_report"
    return "converted_{0}".format(safe)


def _status(value: Any) -> str:
    return _text(value).lower()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_text(mapping: Mapping[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Convert existing harness reports into A2R2 trace.v1 episodes.")
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Report file or directory to scan. Can be repeated. Defaults to data/tmp report roots.",
    )
    parser.add_argument("--out", default="data/traces", help="A2R2 trace output directory.")
    parser.add_argument("--latest", type=int, default=50, help="Maximum newest reports to convert. Use 0 for all.")
    args = parser.parse_args(argv)

    roots = [Path(item) for item in args.root] if args.root else list(DEFAULT_REPORT_ROOTS)
    summary = convert_reports(roots=roots, out_dir=Path(args.out), latest=args.latest)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
