from __future__ import annotations

import json
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


SCHEMA_VERSION = "agent.diagnostic.v1"
DEFAULT_DIAGNOSTICS_ROOT = Path("data/tmp/diagnostics")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _truncate(value: str, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...<truncated>..."


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "<max_depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize(item, depth + 1)
            for key, item in value.items()
            if not _looks_secret_key(str(key))
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, depth + 1) for item in list(value)[:80]]
    return _truncate(repr(value))


def _looks_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("api_key", "token", "secret", "password"))


def _extract_package_from_focus(focus: str) -> str:
    match = re.search(r"\s(u\d+\s+)?([A-Za-z0-9_.]+)/", focus or "")
    if match:
        return match.group(2)
    return ""


def _tail_lines(text: str, limit: int = 80) -> str:
    lines = str(text or "").splitlines()
    return "\n".join(lines[-limit:])


def normalize_exception(exc: BaseException, include_traceback: bool = True) -> Dict[str, Any]:
    payload = {
        "type": exc.__class__.__name__,
        "message": _truncate(str(exc)),
    }
    if include_traceback:
        payload["traceback"] = _truncate("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), 12000)
    return payload


def collect_device_snapshot(adb: Any = None, requested_device: Optional[str] = None) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "requested_device": requested_device or getattr(adb, "device_id", None),
        "adb_path": getattr(adb, "adb_path", None),
        "connected": False,
        "devices": [],
        "current_focus": "",
        "foreground_package": "",
        "top_activity": "",
        "crash_log_tail": "",
        "crash_log_note": "Tail of adb logcat -b crash; may include earlier system/app crashes if the buffer was not cleared.",
    }
    if adb is None:
        return snapshot

    try:
        devices = adb.list_devices(only_ready=False)
        snapshot["devices"] = _sanitize(devices)
        ready_ids = {item.get("device_id") for item in devices if item.get("status") == "device"}
        requested = snapshot.get("requested_device")
        snapshot["connected"] = bool(ready_ids) if not requested else requested in ready_ids
    except Exception as exc:
        snapshot["device_probe_error"] = "{0}: {1}".format(exc.__class__.__name__, exc)

    if not snapshot.get("connected"):
        return snapshot

    try:
        focus = adb.get_current_focus()
        snapshot["current_focus"] = _truncate(focus)
        snapshot["foreground_package"] = _extract_package_from_focus(focus)
    except Exception as exc:
        snapshot["focus_probe_error"] = "{0}: {1}".format(exc.__class__.__name__, exc)

    if hasattr(adb, "shell"):
        try:
            top_activity = adb.shell(
                "dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | head -n 5",
                check=False,
                timeout=4,
            )
            snapshot["top_activity"] = _truncate(top_activity)
        except Exception as exc:
            snapshot["activity_probe_error"] = "{0}: {1}".format(exc.__class__.__name__, exc)

    if hasattr(adb, "run"):
        try:
            logcat = adb.run("logcat", "-b", "crash", "-d", "-t", "40", check=False, timeout=5)
            snapshot["crash_log_tail"] = _truncate(_tail_lines(logcat.stdout or "", 40), 3000)
        except Exception as exc:
            snapshot["logcat_probe_error"] = "{0}: {1}".format(exc.__class__.__name__, exc)

    return snapshot


def adb_target_is_connected(adb: Any = None, requested_device: Optional[str] = None) -> bool:
    if adb is None:
        return False
    requested = requested_device or getattr(adb, "device_id", None)
    try:
        devices = adb.list_devices(only_ready=True)
    except Exception:
        return False
    if requested:
        return requested in {item.get("device_id") for item in devices}
    return bool(devices)


def capture_failure_artifacts(
    adb: Any = None,
    output_dir: Optional[Path] = None,
    prefix: str = "failure",
    runtime_config: Any = None,
) -> Dict[str, Any]:
    artifacts: Dict[str, Any] = {}
    if adb is None or output_dir is None:
        return artifacts
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        screenshot_path = output_dir / "{0}.png".format(prefix)
        adb.screenshot(str(screenshot_path))
        artifacts["screenshot_path"] = str(screenshot_path)
    except Exception as exc:
        artifacts["screenshot_error"] = "{0}: {1}".format(exc.__class__.__name__, exc)

    try:
        from app.skills.read_screen import read_screen_summary

        xml_path = output_dir / "{0}.xml".format(prefix)
        summary = read_screen_summary(adb, str(xml_path), runtime_config=runtime_config)
        artifacts["ui_dump_path"] = str(xml_path)
        artifacts["screen_summary_path"] = str(output_dir / "{0}.summary.json".format(prefix))
        Path(artifacts["screen_summary_path"]).write_text(
            json.dumps(_sanitize(summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        artifacts["screen_summary"] = {
            "app": summary.get("app"),
            "page": summary.get("page"),
            "visible_text": list(summary.get("visible_text") or [])[:20],
            "system_overlay": summary.get("system_overlay"),
        }
    except Exception as exc:
        artifacts["ui_dump_error"] = "{0}: {1}".format(exc.__class__.__name__, exc)

    return artifacts


def recent_actions_from_state(state: Any = None, limit: int = 8) -> Any:
    if state is None:
        return []
    actions = getattr(state, "recent_actions", []) or []
    return _sanitize(actions[-limit:])


def compact_state(state: Any = None) -> Dict[str, Any]:
    if state is None:
        return {}
    return _sanitize(
        {
            "current_task": getattr(state, "current_task", None),
            "task_type": getattr(state, "task_type", None),
            "current_app": getattr(state, "current_app", None),
            "current_page": getattr(state, "current_page", None),
            "last_action": getattr(state, "last_action", None),
            "last_action_success": getattr(state, "last_action_success", None),
            "needs_replan": getattr(state, "needs_replan", None),
            "risk_flag": getattr(state, "risk_flag", None),
            "last_failure_reason": getattr(state, "last_failure_reason", None),
            "artifacts": getattr(state, "artifacts", None),
        }
    )


def build_diagnostic_report(
    *,
    kind: str,
    status: str,
    summary: str,
    goal: str = "",
    task_type: str = "",
    case: str = "",
    error: Optional[Any] = None,
    adb: Any = None,
    state: Any = None,
    context: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Mapping[str, Any]] = None,
    requested_device: Optional[str] = None,
    exit_code: Optional[int] = None,
) -> Dict[str, Any]:
    error_payload = None
    if isinstance(error, BaseException):
        error_payload = normalize_exception(error)
    elif error is not None:
        error_payload = _sanitize(error)

    report = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "kind": kind,
        "human_summary": _truncate(summary, 1000),
        "goal": goal,
        "task_type": task_type,
        "case": case,
        "exit_code": exit_code,
        "error": error_payload,
        "device": collect_device_snapshot(adb, requested_device=requested_device),
        "context": _sanitize(dict(context or {})),
        "state": compact_state(state),
        "recent_actions": recent_actions_from_state(state),
        "artifacts": _sanitize(dict(artifacts or {})),
    }
    return report


def write_diagnostic_report(report: Mapping[str, Any], output_dir: Path, filename: str = "diagnostic.json") -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / filename
    payload = _sanitize(dict(report))
    payload.setdefault("artifacts", {})
    if isinstance(payload["artifacts"], dict):
        payload["artifacts"]["diagnostic_report_path"] = str(report_path)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(report_path)


def failure_output_dir(label: str, root: Path = DEFAULT_DIAGNOSTICS_ROOT) -> Path:
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "failure")).strip("_") or "failure"
    return root / "{0}_{1}".format(safe_label[:80], timestamp())


def write_failure_diagnostic(
    *,
    label: str,
    kind: str,
    summary: str,
    goal: str = "",
    task_type: str = "",
    case: str = "",
    error: Optional[Any] = None,
    adb: Any = None,
    state: Any = None,
    context: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Mapping[str, Any]] = None,
    requested_device: Optional[str] = None,
    exit_code: Optional[int] = None,
    runtime_config: Any = None,
    capture_artifacts: bool = True,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    target_dir = output_dir or failure_output_dir(label)
    combined_artifacts = dict(artifacts or {})
    if capture_artifacts and adb_target_is_connected(adb, requested_device=requested_device):
        combined_artifacts.update(
            capture_failure_artifacts(
                adb=adb,
                output_dir=target_dir,
                prefix="failure",
                runtime_config=runtime_config,
            )
        )
    report = build_diagnostic_report(
        kind=kind,
        status="fail",
        summary=summary,
        goal=goal,
        task_type=task_type,
        case=case,
        error=error,
        adb=adb,
        state=state,
        context=context,
        artifacts=combined_artifacts,
        requested_device=requested_device,
        exit_code=exit_code,
    )
    report_path = write_diagnostic_report(report, target_dir)
    report["artifacts"]["diagnostic_report_path"] = report_path
    return report


def summarize_for_console(report: Mapping[str, Any]) -> str:
    artifacts = report.get("artifacts") if isinstance(report.get("artifacts"), Mapping) else {}
    parts = [
        "[diagnostic] {0}".format(report.get("human_summary") or report.get("kind") or "failure"),
        "report={0}".format(artifacts.get("diagnostic_report_path") or "<not written>"),
    ]
    screenshot = artifacts.get("screenshot_path")
    if screenshot:
        parts.append("screenshot={0}".format(screenshot))
    ui_dump = artifacts.get("ui_dump_path")
    if ui_dump:
        parts.append("xml={0}".format(ui_dump))
    return "\n".join(parts)
