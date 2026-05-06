from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.demo_config import build_demo_message_config
from app.diagnostics import summarize_for_console, write_failure_diagnostic
from app.reasoning_orchestrator import ReasoningOrchestrator
from app.reasoning_validator import ReasoningValidator
from app.skills.read_screen import read_screen_summary
from app.trace_bus import TraceBus
from app.ui_state import normalize_ui_state
from app.utils.adb import ADBClient, ADBError


TASK_TYPE = "guided_ui_task"
CHROME_PACKAGE = "com.android.chrome"
FIXTURE_PACKAGE = "com.example.chaosfixture"
FIXTURE_ACTIVITY = "com.example.chaosfixture/.MainActivity"
DEFAULT_FIXTURE_APK = r"F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"


class DryRunRuntime(object):
    def ensure_local_text_service(self) -> Dict[str, Any]:
        return {"available": False, "started": False, "base_url": ""}

    def ensure_local_vl_service(self) -> Dict[str, Any]:
        return {"available": False, "started": False, "base_url": ""}

    def local_vl_enabled(self) -> bool:
        return False

    def cloud_reviewer_configured(self) -> bool:
        return False

    def cloud_reviewer_base_url(self) -> str:
        return ""

    def cloud_reviewer_api_key(self) -> str:
        return ""

    def cloud_reviewer_model(self) -> str:
        return ""

    def shutdown_owned_processes(self) -> None:
        return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key) or "").strip().lower()
        for key in ("label", "resource_id", "content_desc", "class_name", "hint")
    )


def _find_search_input(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    best_target = None
    best_score = -1
    for target in summary.get("possible_targets", []):
        if not isinstance(target, dict):
            continue
        combined = _candidate_text(target)
        if "edittext" not in combined and "url_bar" not in combined and "location_bar" not in combined:
            continue
        score = 0
        if "edittext" in combined:
            score += 3
        if "url_bar" in combined or "location_bar" in combined:
            score += 3
        if any(marker in combined for marker in ("search", "url", "address")):
            score += 2
        if target.get("focused"):
            score += 2
        if score > best_score:
            best_score = score
            best_target = target
    return best_target


def _find_target_by_text(summary: Dict[str, Any], text: str) -> Optional[Dict[str, Any]]:
    wanted = str(text or "").strip().lower()
    if not wanted:
        return None
    best_target = None
    best_score = -1
    for target in summary.get("possible_targets", []):
        if not isinstance(target, dict):
            continue
        combined = _candidate_text(target)
        label = str(target.get("label") or "").strip().lower()
        content_desc = str(target.get("content_desc") or "").strip().lower()
        score = -1
        if label == wanted or content_desc == wanted:
            score = 100
        elif wanted in label or wanted in content_desc:
            score = 90
        elif wanted in combined:
            score = 70
        if bool(target.get("clickable")):
            score += 5
        if score > best_score:
            best_score = score
            best_target = target
    return best_target


def _tap_text(adb: ADBClient, summary: Dict[str, Any], text: str) -> Optional[Dict[str, Any]]:
    target = _find_target_by_text(summary, text)
    if not target or not target.get("bounds"):
        return None
    bounds = target["bounds"]
    adb.tap(bounds["center_x"], bounds["center_y"])
    return target


def _build_orchestrator(trace_path: Path) -> ReasoningOrchestrator:
    return ReasoningOrchestrator(
        validator=ReasoningValidator(allowed_task_types=[TASK_TYPE, "read_current_screen"]),
        model_runtime=DryRunRuntime(),
        trace_bus=TraceBus(trace_path=str(trace_path), console_enabled=False),
        rule_fallback=lambda **kwargs: {
            "page_type": kwargs["screen_summary"].get("page", "unknown_page"),
            "summary": "Rule fallback selected.",
            "facts": [],
            "targets": [],
            "next_action": None,
            "confidence": 0.61,
            "requires_confirmation": False,
        },
    )


def _capture_state(
    adb: ADBClient,
    case_dir: Path,
    name: str,
    goal: str,
) -> Dict[str, Any]:
    screenshot_path = case_dir / "{0}.png".format(name)
    xml_path = case_dir / "{0}.xml".format(name)
    last_error: Optional[Exception] = None
    summary: Optional[Dict[str, Any]] = None
    for attempt in range(1, 4):
        adb.screenshot(str(screenshot_path))
        try:
            summary = read_screen_summary(adb, str(xml_path), runtime_config=build_demo_message_config())
            break
        except ADBError as exc:
            last_error = exc
            (case_dir / "{0}.read_error_attempt_{1}.txt".format(name, attempt)).write_text(
                str(exc),
                encoding="utf-8",
            )
            time.sleep(1.5 * attempt)
    if summary is None:
        raise last_error or ADBError("Unable to capture UI state.")
    ui_state = normalize_ui_state(goal, TASK_TYPE, summary)
    (case_dir / "{0}.summary.json".format(name)).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (case_dir / "{0}.ui_state.json".format(name)).write_text(
        json.dumps(ui_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "screenshot_path": str(screenshot_path),
        "xml_path": str(xml_path),
        "summary": summary,
        "ui_state": ui_state,
    }


def prepare_chrome_search_stylus_overlay(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    adb.force_stop_app(CHROME_PACKAGE)
    try:
        adb.open_url("https://www.google.com/", package_name=CHROME_PACKAGE, wait_time=2.5)
    except ADBError:
        adb.open_app(CHROME_PACKAGE, wait_time=2.0)
    before = _capture_state(adb, case_dir, "before_focus", goal)
    if before["ui_state"].get("goal_progress", {}).get("done"):
        return {
            "ok": False,
            "reason": "Chrome is already in a completed search-result state; prepare could not reset to a search surface.",
            "before": before,
        }
    target = _find_search_input(before["summary"])
    if not target or not target.get("bounds"):
        return {"ok": False, "reason": "Could not find Chrome search input.", "before": before}
    bounds = target["bounds"]
    adb.tap(bounds["center_x"], bounds["center_y"])
    time.sleep(2.0)
    after = _capture_state(adb, case_dir, "after_focus", goal)
    overlay = after["summary"].get("system_overlay") or {}
    overlay_type = str(overlay.get("type") or "")
    return {
        "ok": bool(overlay.get("present")) and "input_method" in overlay_type,
        "reason": "Input-method overlay detected." if overlay.get("present") else "Input-method overlay was not detected.",
        "tapped_target": target,
        "before": before,
        "after": after,
    }


def install_fixture_if_needed(adb: ADBClient, apk_path: Optional[str], skip_install: bool = False) -> None:
    if skip_install:
        return
    apk = Path(apk_path or os.environ.get("CHAOS_FIXTURE_APK") or DEFAULT_FIXTURE_APK)
    if not apk.exists():
        raise ADBError("Chaos fixture APK not found: {0}".format(apk))
    adb.run("install", "-r", str(apk), check=True, timeout=90)


def reset_fixture(adb: ADBClient) -> None:
    adb.force_stop_app(FIXTURE_PACKAGE)
    adb.shell("pm clear {0}".format(FIXTURE_PACKAGE), check=False, timeout=10)
    for permission in ("android.permission.CAMERA", "android.permission.POST_NOTIFICATIONS"):
        adb.shell("pm revoke {0} {1}".format(FIXTURE_PACKAGE, permission), check=False, timeout=10)
        adb.shell(
            "pm clear-permission-flags {0} {1} user-set user-fixed".format(FIXTURE_PACKAGE, permission),
            check=False,
            timeout=10,
        )


def launch_fixture(adb: ADBClient) -> None:
    adb.shell("am start -n {0}".format(FIXTURE_ACTIVITY), check=True, timeout=10)
    _wait_for_fixture_home(adb, timeout_seconds=12.0)


def _wait_for_fixture_home(adb: ADBClient, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    probe_path = Path("data/tmp/chaos_fixture_ready.xml")
    markers = (
        "Request notification permission",
        "Open input surface",
        "Show blocking dialog",
        "Show loading state",
    )
    last_error = ""
    while time.time() < deadline:
        try:
            dump_path = adb.dump_ui_xml(str(probe_path))
            xml_text = dump_path.read_text(encoding="utf-8", errors="replace")
            if all(marker in xml_text for marker in ("Open input surface", "Show error state")):
                return
            if any(marker in xml_text for marker in markers):
                return
            last_error = "fixture markers not visible yet"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.8)
    raise ADBError("Chaos fixture home did not stabilize within {0:.1f}s: {1}".format(timeout_seconds, last_error))


def prepare_fixture_button_case(
    adb: ADBClient,
    case_dir: Path,
    goal: str,
    button_text: str,
    expected_text: str,
    wait_after_tap: float = 1.2,
) -> Dict[str, Any]:
    reset_fixture(adb)
    launch_fixture(adb)
    home = _capture_state(adb, case_dir, "fixture_home", goal)
    target = _tap_text(adb, home["summary"], button_text)
    if not target:
        return {
            "ok": False,
            "reason": "Could not find fixture button: {0}".format(button_text),
            "before": home,
        }
    time.sleep(wait_after_tap)
    after = _capture_state(adb, case_dir, "after_trigger", goal)
    visible = " ".join(str(item or "").lower() for item in after["summary"].get("visible_text", []))
    expected_visible = str(expected_text or "").strip().lower() in visible
    return {
        "ok": expected_visible,
        "reason": "Expected text was visible." if expected_visible else "Expected text was not visible: {0}".format(expected_text),
        "tapped_target": target,
        "before": home,
        "after": after,
    }


def prepare_fixture_notification_permission(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Request notification permission",
        expected_text="allow",
    )


def prepare_fixture_camera_permission(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Request camera permission",
        expected_text="allow",
    )


def prepare_fixture_blocking_dialog(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Show blocking dialog",
        expected_text="blocking dialog",
    )


def prepare_fixture_onboarding_overlay(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Show onboarding overlay",
        expected_text="welcome to chaos fixture",
    )


def prepare_fixture_bottom_sheet(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Show bottom sheet",
        expected_text="try this new feature",
    )


def prepare_fixture_input_surface(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Open input surface",
        expected_text="search or type here",
    )


def prepare_fixture_loading_state(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Show loading state",
        expected_text="loading",
        wait_after_tap=0.5,
    )


def prepare_fixture_error_state(adb: ADBClient, case_dir: Path, goal: str) -> Dict[str, Any]:
    return prepare_fixture_button_case(
        adb,
        case_dir,
        goal,
        button_text="Show error state",
        expected_text="something went wrong",
    )


def dry_run_decision(case_dir: Path, goal: str, state: Dict[str, Any]) -> Dict[str, Any]:
    orchestrator = _build_orchestrator(case_dir / "reasoning_trace.jsonl")
    result = orchestrator.resolve(
        goal=goal,
        task_type=TASK_TYPE,
        screen_summary=state["summary"],
        screenshot_path=state["screenshot_path"],
        recent_actions=[{"action": "tap", "success": True, "detail": "Chaos harness prepared the UI state."}],
        relevant_memories=[],
    )
    decision = result["decision"].to_dict()
    (case_dir / "decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return decision


CASES = {
    "chrome_search_stylus_overlay": {
        "goal": "open chrome and search for llm",
        "prepare": prepare_chrome_search_stylus_overlay,
        "expected_skills": {"search_in_app", "type_text"},
        "forbidden_skills": {"back"},
        "requires_fixture": False,
    },
    "fixture_notification_permission": {
        "goal": "handle the notification permission dialog",
        "prepare": prepare_fixture_notification_permission,
        "expected_skills": {"tap"},
        "forbidden_skills": {"back", "search_in_app", "type_text"},
        "expected_target_any": {"allow", "while using", "only this time"},
        "requires_fixture": True,
    },
    "fixture_camera_permission": {
        "goal": "handle the camera permission dialog",
        "prepare": prepare_fixture_camera_permission,
        "expected_skills": {"tap"},
        "forbidden_skills": {"back", "search_in_app", "type_text"},
        "expected_target_any": {"allow", "while using", "only this time"},
        "requires_fixture": True,
    },
    "fixture_blocking_dialog": {
        "goal": "handle the blocking dialog",
        "prepare": prepare_fixture_blocking_dialog,
        "expected_skills": {"tap"},
        "forbidden_skills": {"back", "search_in_app", "type_text"},
        "expected_target_contains": "allow",
        "requires_fixture": True,
    },
    "fixture_onboarding_overlay": {
        "goal": "dismiss the onboarding overlay",
        "prepare": prepare_fixture_onboarding_overlay,
        "expected_skills": {"tap"},
        "forbidden_skills": {"back", "search_in_app", "type_text"},
        "expected_target_any": {"get started", "skip", "continue", "next"},
        "requires_fixture": True,
    },
    "fixture_bottom_sheet": {
        "goal": "handle the bottom sheet prompt",
        "prepare": prepare_fixture_bottom_sheet,
        "expected_skills": {"tap"},
        "forbidden_skills": {"back", "search_in_app", "type_text"},
        "expected_target_any": {"continue", "not now", "skip"},
        "requires_fixture": True,
    },
    "fixture_input_surface": {
        "goal": "enter \"hello chaos\" into the input surface",
        "prepare": prepare_fixture_input_surface,
        "expected_skills": {"type_text"},
        "forbidden_skills": {"back", "search_in_app"},
        "expected_target_any": {"search", "type", "input"},
        "requires_fixture": True,
    },
    "fixture_loading_state": {
        "goal": "wait for the loading state to finish",
        "prepare": prepare_fixture_loading_state,
        "expected_skills": {"wait"},
        "forbidden_skills": {"back", "tap", "search_in_app", "type_text"},
        "requires_fixture": True,
    },
    "fixture_error_state": {
        "goal": "recover from the error state",
        "prepare": prepare_fixture_error_state,
        "expected_skills": {"tap"},
        "forbidden_skills": {"back", "search_in_app", "type_text"},
        "expected_target_any": {"retry", "try again", "reload", "refresh"},
        "requires_fixture": True,
    },
}


def _decision_target_text(decision: Dict[str, Any]) -> str:
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    return " ".join(str(args.get(key) or "").strip().lower() for key in ("target", "label", "text", "action_id"))


def run_case(
    adb: ADBClient,
    case_name: str,
    output_root: Path,
    fixture_apk: Optional[str] = None,
    skip_install: bool = False,
) -> Dict[str, Any]:
    if case_name not in CASES:
        raise ValueError("Unknown chaos case: {0}".format(case_name))
    case = CASES[case_name]
    if case.get("requires_fixture"):
        install_fixture_if_needed(adb, fixture_apk, skip_install=skip_install)
    goal = case["goal"]
    case_dir = output_root / "{0}_{1}".format(case_name, _timestamp())
    case_dir.mkdir(parents=True, exist_ok=True)

    prepared = case["prepare"](adb, case_dir, goal)
    state = (prepared.get("after") or prepared.get("before")) if isinstance(prepared, dict) else None
    if not state:
        report = {"case": case_name, "status": "fail", "reason": "No state was captured."}
    elif not prepared.get("ok"):
        report = {
            "case": case_name,
            "status": "fail",
            "reason": "Precondition failed: {0}".format(prepared.get("reason", "unknown")),
            "artifacts_dir": str(case_dir),
        }
    else:
        decision = dry_run_decision(case_dir, goal, state)
        skill = decision.get("skill")
        expected_skills = case["expected_skills"]
        forbidden_skills = case["forbidden_skills"]
        target_expectations = {
            str(item or "").strip().lower()
            for item in case.get("expected_target_any", set())
            if str(item or "").strip()
        }
        legacy_target_expectation = str(case.get("expected_target_contains") or "").strip().lower()
        if legacy_target_expectation:
            target_expectations.add(legacy_target_expectation)
        target_ok = True
        target_text = _decision_target_text(decision)
        if target_expectations:
            target_ok = any(expectation in target_text for expectation in target_expectations)
        passed = skill in expected_skills and skill not in forbidden_skills and target_ok
        report = {
            "case": case_name,
            "status": "pass" if passed else "fail",
            "reason": "Decision skill={0}.".format(skill),
            "goal": goal,
            "expected_skills": sorted(expected_skills),
            "forbidden_skills": sorted(forbidden_skills),
            "expected_target_any": sorted(target_expectations),
            "decision": decision,
            "system_overlay": state["summary"].get("system_overlay"),
            "ui_state": state["ui_state"],
            "artifacts_dir": str(case_dir),
        }
    if report.get("status") != "pass":
        diagnostic = write_failure_diagnostic(
            label="chaos_{0}".format(case_name),
            kind="chaos_harness_failure",
            summary=str(report.get("reason") or "Chaos harness case failed."),
            goal=goal,
            task_type=TASK_TYPE,
            case=case_name,
            adb=adb,
            context={
                "case": case_name,
                "prepared_ok": bool(prepared.get("ok")) if isinstance(prepared, dict) else False,
                "report": report,
            },
            artifacts={"artifacts_dir": str(case_dir), "case_report_path": str(case_dir / "report.json")},
            requested_device=getattr(adb, "device_id", None),
            runtime_config=build_demo_message_config(),
            output_dir=case_dir / "diagnostics",
        )
        report["diagnostic"] = {
            "schema_version": diagnostic.get("schema_version"),
            "human_summary": diagnostic.get("human_summary"),
            "report_path": diagnostic.get("artifacts", {}).get("diagnostic_report_path"),
            "screenshot_path": diagnostic.get("artifacts", {}).get("screenshot_path"),
            "ui_dump_path": diagnostic.get("artifacts", {}).get("ui_dump_path"),
        }
    (case_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Android GUI chaos UI regression cases.")
    parser.add_argument("--case", default="chrome_search_stylus_overlay", choices=sorted(CASES.keys()))
    parser.add_argument("--device-id", default=None, help="ADB serial. Defaults to the first ready device.")
    parser.add_argument("--adb-path", default=None, help="Optional adb.exe path.")
    parser.add_argument("--fixture-apk", default=None, help="Path to app-debug.apk for com.example.chaosfixture.")
    parser.add_argument("--skip-install", action="store_true", help="Assume the chaos fixture app is already installed.")
    parser.add_argument("--output-root", default="data/tmp/chaos", help="Directory for screenshots and JSON reports.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    adb = ADBClient(adb_path=args.adb_path, device_id=args.device_id)
    try:
        adb.ensure_device(timeout=5)
    except ADBError as exc:
        diagnostic = write_failure_diagnostic(
            label="chaos_no_device",
            kind="adb_error",
            summary="No ready Android device for chaos harness.",
            case=args.case,
            error=exc,
            adb=adb,
            requested_device=args.device_id,
            exit_code=2,
            capture_artifacts=False,
        )
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        print(summarize_for_console(diagnostic), file=sys.stderr)
        return 2

    output_root = Path(args.output_root)
    try:
        report = run_case(
            adb,
            args.case,
            output_root,
            fixture_apk=args.fixture_apk,
            skip_install=args.skip_install,
        )
    except Exception as exc:
        diagnostic = write_failure_diagnostic(
            label="chaos_exception_{0}".format(args.case),
            kind="unhandled_exception",
            summary="Unhandled exception while running chaos harness.",
            case=args.case,
            error=exc,
            adb=adb,
            requested_device=args.device_id,
            exit_code=3,
            runtime_config=build_demo_message_config(),
        )
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        print(summarize_for_console(diagnostic), file=sys.stderr)
        return 3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
