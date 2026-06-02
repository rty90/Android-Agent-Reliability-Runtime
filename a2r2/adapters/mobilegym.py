from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.policies.common import stable_hash


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def import_mobilegym_run(
    run_dir: str | Path,
    traces_root: str | Path = "data/traces/mobilegym_import",
    out_dir: str | Path | None = None,
) -> Dict[str, Any]:
    """Convert a MobileGym run directory into A2R2 trace.v1 episodes.

    This is intentionally an offline bridge. MobileGym remains the environment
    and judge; A2R2 adds process-level reliability labels over the recorded
    agent actions.
    """

    source = Path(run_dir)
    results = load_jsonl(source / "results.jsonl")
    meta = _read_json(source / "meta.json")
    imported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for result in results:
        episode_dir = find_trajectory_dir(source, result, repeat_n=int(meta.get("repeat_n") or 1))
        if not episode_dir:
            skipped.append({"id": result.get("id"), "reason": "trajectory_not_found"})
            continue
        trajectory = _read_json(episode_dir / "trajectory.json")
        if not isinstance(trajectory, list):
            skipped.append({"id": result.get("id"), "reason": "trajectory_json_not_list"})
            continue
        imported.append(
            import_mobilegym_episode(
                source_run_dir=source,
                episode_dir=episode_dir,
                trajectory=trajectory,
                result=result,
                run_meta=meta,
                traces_root=traces_root,
            )
        )

    summary = build_mobilegym_import_summary(source, meta, results, imported, skipped)
    if out_dir:
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "mobilegym_a2r2_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (target / "mobilegym_a2r2_summary.md").write_text(
            render_mobilegym_import_markdown(summary), encoding="utf-8"
        )
    return summary


def import_mobilegym_episode(
    *,
    source_run_dir: Path,
    episode_dir: Path,
    trajectory: List[Mapping[str, Any]],
    result: Mapping[str, Any],
    run_meta: Mapping[str, Any],
    traces_root: str | Path,
) -> Dict[str, Any]:
    task_id = str(result.get("id") or episode_dir.name)
    goal = str(result.get("task_name") or task_id)
    episode_id = "mobilegym_{0}".format(_safe_id("{0}_t{1}".format(task_id, result.get("trial_id", 0))))
    runtime = ReliabilityRuntime(
        RuntimeConfig(
            traces_root=str(traces_root),
            episode_id=episode_id,
            agent_name="mobilegym:{0}".format(run_meta.get("agent") or "unknown"),
            runtime_mode="mobilegym_offline_import",
            agent_meta={
                "source": "mobilegym",
                "source_run_dir": str(source_run_dir),
                "source_episode_dir": str(episode_dir),
                "model_name": run_meta.get("model_name"),
            },
        )
    )
    history: List[Dict[str, Any]] = []
    for index, step in enumerate(trajectory):
        next_step = trajectory[index + 1] if index + 1 < len(trajectory) else step
        before = observation_from_mobilegym_step(episode_dir, step, result=result)
        after = observation_from_mobilegym_step(
            episode_dir,
            next_step,
            result=result,
            terminal_action=str(step.get("action_type") or "").upper() in {"COMPLETE", "ABORT"},
        )
        action = proposed_action_from_mobilegym_step(step)
        decision = runtime.check_before_action(goal, before, action, history)
        verification = runtime.verify_after_action(goal, before, action, after, history)
        record = runtime.record_step(goal, before, action, decision, after, verification, history)
        history.append(record)

    runtime.write_episode_summary(
        goal=goal,
        final_success=bool(result.get("is_success")),
        agent_claimed_success=_agent_claimed_success(result, trajectory),
    )
    exported = runtime.export_trace()
    episode_summary = _read_json(Path(exported["summary_path"]))
    return {
        "task_id": task_id,
        "trial_id": result.get("trial_id", 0),
        "goal": goal,
        "mobilegym_success": bool(result.get("is_success")),
        "mobilegym_false_complete": bool(result.get("false_complete")),
        "mobilegym_unexpected_side_effects": not _judge_clean(result),
        "mobilegym_progress": result.get("progress"),
        "a2r2_episode_id": runtime.episode_id,
        "a2r2_summary_path": exported["summary_path"],
        "a2r2_steps_path": exported["steps_path"],
        "a2r2_failure_labels": episode_summary.get("failure_labels", []),
        "a2r2_false_success": bool(episode_summary.get("false_success")),
        "steps_imported": len(trajectory),
    }


def observation_from_mobilegym_step(
    episode_dir: Path,
    step: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
    terminal_action: bool = False,
) -> Observation:
    route = step.get("route") if isinstance(step.get("route"), Mapping) else {}
    screenshot = str(step.get("screenshot") or "")
    screenshot_path = str(episode_dir / screenshot) if screenshot else None
    app = str(route.get("app") or "")
    path = str(route.get("path") or "")
    text = "MobileGym route app={0} path={1}".format(app, path)
    xml_text = (
        "<hierarchy><node text=\"{0}\" package=\"mobilegym.{1}\" "
        "class=\"mobilegym.Route\" clickable=\"false\" enabled=\"true\" /></hierarchy>"
    ).format(_xml_escape(text), _xml_escape(app or "unknown"))
    goal_satisfied: Optional[bool] = None
    if terminal_action:
        goal_satisfied = bool(result.get("is_success"))
    metadata = {
        "source": "mobilegym",
        "route": dict(route),
        "visible_text": [text],
        "xml_text": xml_text,
        "goal_marker_present": goal_satisfied,
        "mobilegym_result": {
            "is_success": result.get("is_success"),
            "false_complete": result.get("false_complete"),
            "progress": result.get("progress"),
        },
    }
    return Observation(
        screenshot_path=screenshot_path,
        package="mobilegym.{0}".format(app) if app else "mobilegym",
        activity=path or app or None,
        ui_tree_hash=stable_hash({"route": route, "screenshot": screenshot}),
        metadata=metadata,
    )


def proposed_action_from_mobilegym_step(step: Mapping[str, Any]) -> ProposedAction:
    action_type = str(step.get("action_type") or "").upper()
    data = step.get("action_data") if isinstance(step.get("action_data"), Mapping) else {}
    mapped = {
        "CLICK": "tap",
        "DOUBLE_TAP": "double_tap",
        "LONG_PRESS": "long_press",
        "TYPE": "input_text",
        "SWIPE": "swipe",
        "DRAG": "drag",
        "BACK": "back",
        "HOME": "home",
        "RECENT": "recent",
        "ENTER": "enter",
        "WAIT": "wait",
        "AWAKE": "open_app",
        "ANSWER": "answer",
        "COMPLETE": "done",
        "ABORT": "terminate",
        "INFO": "manual_handoff",
        "NOOP": "noop",
    }.get(action_type, action_type.lower() or "unknown")
    point = data.get("point") if isinstance(data.get("point"), list) else None
    x = int(point[0]) if point and len(point) >= 2 and isinstance(point[0], (int, float)) else None
    y = int(point[1]) if point and len(point) >= 2 and isinstance(point[1], (int, float)) else None
    target_text = data.get("value") or data.get("return") or data.get("message")
    raw = {
        "mobilegym_action_type": action_type,
        "mobilegym_action_data": dict(data),
        "thought": step.get("thought"),
        "summary": step.get("summary"),
    }
    if action_type == "COMPLETE":
        raw["agent_claimed_success"] = True
    return ProposedAction(
        action_type=mapped,
        x=x,
        y=y,
        target_text=str(target_text) if target_text is not None else None,
        raw=raw,
    )


def find_trajectory_dir(run_dir: Path, result: Mapping[str, Any], repeat_n: int = 1) -> Optional[Path]:
    task_id = str(result.get("id") or "")
    trial_id = int(result.get("trial_id") or 0)
    base = _safe_task_dir(task_id)
    candidates = [run_dir / "trajectory" / base]
    if repeat_n > 1 or trial_id:
        candidates.insert(0, run_dir / "trajectory" / "{0}_t{1}".format(base, trial_id))
    for candidate in candidates:
        if (candidate / "trajectory.json").exists():
            return candidate
    for meta_path in (run_dir / "trajectory").glob("*/meta.json"):
        meta = _read_json(meta_path)
        if meta.get("task_id") == task_id and int(meta.get("trial_id") or 0) == trial_id:
            candidate = meta_path.parent
            if (candidate / "trajectory.json").exists():
                return candidate
    return None


def build_mobilegym_import_summary(
    run_dir: Path,
    run_meta: Mapping[str, Any],
    results: Iterable[Mapping[str, Any]],
    imported: List[Mapping[str, Any]],
    skipped: List[Mapping[str, Any]],
) -> Dict[str, Any]:
    result_list = list(results)
    false_complete = sum(1 for item in result_list if item.get("false_complete"))
    unexpected = sum(1 for item in result_list if not _judge_clean(item))
    a2r2_false_success = sum(1 for item in imported if item.get("a2r2_false_success"))
    label_counts: Dict[str, int] = {}
    for item in imported:
        for label in item.get("a2r2_failure_labels", []):
            label_counts[str(label)] = label_counts.get(str(label), 0) + 1
    return {
        "schema_version": "a2r2_mobilegym_import.v1",
        "source_run_dir": str(run_dir),
        "agent": run_meta.get("agent"),
        "model_name": run_meta.get("model_name"),
        "episodes_total": len(result_list),
        "episodes_imported": len(imported),
        "episodes_skipped": len(skipped),
        "mobilegym_successes": sum(1 for item in result_list if item.get("is_success")),
        "mobilegym_false_complete": false_complete,
        "mobilegym_unexpected_side_effects": unexpected,
        "a2r2_false_success": a2r2_false_success,
        "a2r2_failure_label_counts": label_counts,
        "imported": list(imported),
        "skipped": list(skipped),
    }


def render_mobilegym_import_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# A2R2 MobileGym Import",
        "",
        "| Metric | Value |",
        "|---|---:|",
        "| Episodes total | {0} |".format(summary.get("episodes_total")),
        "| Episodes imported | {0} |".format(summary.get("episodes_imported")),
        "| MobileGym successes | {0} |".format(summary.get("mobilegym_successes")),
        "| MobileGym false complete | {0} |".format(summary.get("mobilegym_false_complete")),
        "| MobileGym unexpected side effects | {0} |".format(summary.get("mobilegym_unexpected_side_effects")),
        "| A2R2 false success candidates | {0} |".format(summary.get("a2r2_false_success")),
        "",
        "A2R2 failure labels: `{0}`".format(summary.get("a2r2_failure_label_counts") or {}),
        "",
        "## Imported Episodes",
        "",
        "| Task | Success | FC | USE | A2R2 labels |",
        "|---|---:|---:|---:|---|",
    ]
    for item in summary.get("imported", []):
        lines.append(
            "| {0} | {1} | {2} | {3} | `{4}` |".format(
                item.get("task_id"),
                item.get("mobilegym_success"),
                item.get("mobilegym_false_complete"),
                item.get("mobilegym_unexpected_side_effects"),
                item.get("a2r2_failure_labels") or [],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _agent_claimed_success(result: Mapping[str, Any], trajectory: List[Mapping[str, Any]]) -> bool:
    stop_reason = str((result.get("execution") or {}).get("stop_reason") or "").upper()
    return stop_reason == "COMPLETE" or any(
        str(step.get("action_type") or "").upper() == "COMPLETE" for step in trajectory
    )


def _judge_clean(result: Mapping[str, Any]) -> bool:
    judge = result.get("judge")
    if isinstance(judge, Mapping) and "clean" in judge:
        return bool(judge.get("clean"))
    return True


def _safe_task_dir(task_id: str) -> str:
    return task_id.replace(".", "_").replace("/", "_").replace(" ", "_")


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120]


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
