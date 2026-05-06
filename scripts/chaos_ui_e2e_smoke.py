from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.demo_config import build_demo_message_config
from app.diagnostics import summarize_for_console, write_failure_diagnostic
from app.skills.base import SkillContext
from app.skills.type_text import TypeTextSkill
from app.state import AgentState
from app.utils.adb import ADBClient, ADBError
from app.utils.screenshot import ScreenshotManager
from chaos_ui_harness import (
    DEFAULT_FIXTURE_APK,
    TASK_TYPE,
    _capture_state,
    dry_run_decision,
    install_fixture_if_needed,
    prepare_fixture_input_surface,
)


GOAL = 'enter "hello chaos" into the input surface'
EXPECTED_TEXT = "hello chaos"


class NullLogger(object):
    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _visible_text_contains(summary: Dict[str, Any], expected_text: str) -> bool:
    expected = " ".join(str(expected_text or "").strip().lower().split())
    if not expected:
        return False
    visible = " ".join(str(item or "").strip().lower() for item in summary.get("visible_text", []))
    if expected in visible:
        return True
    for candidate in summary.get("possible_targets", []):
        if not isinstance(candidate, dict):
            continue
        candidate_text = " ".join(
            str(candidate.get(key) or "").strip().lower()
            for key in ("label", "content_desc", "hint")
        )
        if expected in candidate_text:
            return True
    return False


def run_smoke(
    adb: ADBClient,
    output_root: Path,
    fixture_apk: Optional[str] = None,
    skip_install: bool = False,
) -> Dict[str, Any]:
    install_fixture_if_needed(adb, fixture_apk, skip_install=skip_install)
    case_dir = output_root / "fixture_input_surface_e2e_{0}".format(_timestamp())
    case_dir.mkdir(parents=True, exist_ok=True)

    prepared = prepare_fixture_input_surface(adb, case_dir, GOAL)
    prepared_state = prepared.get("after") or prepared.get("before")
    if not prepared.get("ok") or not prepared_state:
        report = {
            "case": "fixture_input_surface_e2e",
            "status": "fail",
            "reason": "Precondition failed: {0}".format(prepared.get("reason", "unknown")),
            "artifacts_dir": str(case_dir),
        }
        diagnostic = write_failure_diagnostic(
            label="chaos_e2e_precondition",
            kind="chaos_e2e_failure",
            summary=str(report["reason"]),
            goal=GOAL,
            task_type=TASK_TYPE,
            case="fixture_input_surface_e2e",
            adb=adb,
            context={"prepared": prepared, "report": report},
            artifacts={"artifacts_dir": str(case_dir), "e2e_report_path": str(case_dir / "e2e_report.json")},
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
        (case_dir / "e2e_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    runtime_config = build_demo_message_config()
    decision = dry_run_decision(case_dir, GOAL, prepared_state)
    if decision.get("skill") != "type_text":
        report = {
            "case": "fixture_input_surface_e2e",
            "status": "fail",
            "reason": "Expected type_text decision, got {0}.".format(decision.get("skill")),
            "decision": decision,
            "artifacts_dir": str(case_dir),
        }
        diagnostic = write_failure_diagnostic(
            label="chaos_e2e_bad_decision",
            kind="chaos_e2e_failure",
            summary=str(report["reason"]),
            goal=GOAL,
            task_type=TASK_TYPE,
            case="fixture_input_surface_e2e",
            adb=adb,
            context={"decision": decision, "report": report},
            artifacts={"artifacts_dir": str(case_dir), "e2e_report_path": str(case_dir / "e2e_report.json")},
            requested_device=getattr(adb, "device_id", None),
            runtime_config=runtime_config,
            output_dir=case_dir / "diagnostics",
        )
        report["diagnostic"] = {
            "schema_version": diagnostic.get("schema_version"),
            "human_summary": diagnostic.get("human_summary"),
            "report_path": diagnostic.get("artifacts", {}).get("diagnostic_report_path"),
            "screenshot_path": diagnostic.get("artifacts", {}).get("screenshot_path"),
            "ui_dump_path": diagnostic.get("artifacts", {}).get("ui_dump_path"),
        }
        (case_dir / "e2e_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    state = AgentState()
    state.start_task(GOAL, TASK_TYPE)
    state.update_screen_summary(prepared_state["summary"])
    context = SkillContext(
        adb=adb,
        state=state,
        logger=NullLogger(),
        screenshot_manager=ScreenshotManager(base_dir=str(case_dir / "screenshots")),
        registry={},
        runtime_config=runtime_config,
    )
    skill_result = TypeTextSkill().execute(decision.get("args") or {}, context)
    after_execute = _capture_state(adb, case_dir, "after_execute", GOAL)
    verified = bool(skill_result.get("success")) and _visible_text_contains(after_execute["summary"], EXPECTED_TEXT)
    report = {
        "case": "fixture_input_surface_e2e",
        "status": "pass" if verified else "fail",
        "reason": "Text was entered and verified." if verified else "Text entry was not verified.",
        "goal": GOAL,
        "decision": decision,
        "skill_result": skill_result,
        "after_execute_screenshot_path": after_execute["screenshot_path"],
        "after_execute_xml_path": after_execute["xml_path"],
        "artifacts_dir": str(case_dir),
    }
    if not verified:
        diagnostic = write_failure_diagnostic(
            label="chaos_e2e_execute_failed",
            kind="chaos_e2e_failure",
            summary=str(report["reason"]),
            goal=GOAL,
            task_type=TASK_TYPE,
            case="fixture_input_surface_e2e",
            adb=adb,
            state=state,
            context={
                "decision": decision,
                "skill_result": skill_result,
                "report": report,
            },
            artifacts={
                "artifacts_dir": str(case_dir),
                "e2e_report_path": str(case_dir / "e2e_report.json"),
                "after_execute_screenshot_path": after_execute["screenshot_path"],
                "after_execute_xml_path": after_execute["xml_path"],
            },
            requested_device=getattr(adb, "device_id", None),
            runtime_config=runtime_config,
            output_dir=case_dir / "diagnostics",
        )
        report["diagnostic"] = {
            "schema_version": diagnostic.get("schema_version"),
            "human_summary": diagnostic.get("human_summary"),
            "report_path": diagnostic.get("artifacts", {}).get("diagnostic_report_path"),
            "screenshot_path": diagnostic.get("artifacts", {}).get("screenshot_path"),
            "ui_dump_path": diagnostic.get("artifacts", {}).get("ui_dump_path"),
        }
    (case_dir / "e2e_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a minimal execute-and-verify chaos UI smoke.")
    parser.add_argument("--device-id", default=None, help="ADB serial. Defaults to the first ready device.")
    parser.add_argument("--adb-path", default=None, help="Optional adb.exe path.")
    parser.add_argument("--fixture-apk", default=DEFAULT_FIXTURE_APK, help="Path to app-debug.apk.")
    parser.add_argument("--skip-install", action="store_true", help="Assume the chaos fixture app is already installed.")
    parser.add_argument("--output-root", default="data/tmp/chaos_e2e", help="Directory for screenshots and JSON reports.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    adb = ADBClient(adb_path=args.adb_path, device_id=args.device_id)
    try:
        adb.ensure_device(timeout=5)
    except ADBError as exc:
        diagnostic = write_failure_diagnostic(
            label="chaos_e2e_no_device",
            kind="adb_error",
            summary="No ready Android device for chaos E2E smoke.",
            goal=GOAL,
            task_type=TASK_TYPE,
            case="fixture_input_surface_e2e",
            error=exc,
            adb=adb,
            requested_device=args.device_id,
            exit_code=2,
            capture_artifacts=False,
        )
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        print(summarize_for_console(diagnostic), file=sys.stderr)
        return 2
    try:
        report = run_smoke(
            adb,
            Path(args.output_root),
            fixture_apk=args.fixture_apk,
            skip_install=args.skip_install,
        )
    except Exception as exc:
        diagnostic = write_failure_diagnostic(
            label="chaos_e2e_exception",
            kind="unhandled_exception",
            summary="Unhandled exception while running chaos E2E smoke.",
            goal=GOAL,
            task_type=TASK_TYPE,
            case="fixture_input_surface_e2e",
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
