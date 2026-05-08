from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from app.demo_config import build_demo_message_config
from app.diagnostics import summarize_for_console, write_failure_diagnostic
from app.utils.adb import ADBClient, ADBError
from chaos_ui_harness import DEFAULT_FIXTURE_APK, TASK_TYPE
from long_tail_agent_smoke import (
    _case_status,
    run_chaos_dry_case,
    run_chrome_random_search_question,
    run_chrome_web_torture_question,
    run_fixture_input_e2e,
    run_long_tail,
    run_settings_readonly_question,
)


Runner = Callable[["LadderContext", Path, random.Random], Dict[str, Any]]


@dataclass(frozen=True)
class LadderCase(object):
    key: str
    description: str
    runner: Runner


@dataclass(frozen=True)
class LadderLevel(object):
    level_id: str
    name: str
    description: str
    cases: Sequence[LadderCase]


@dataclass(frozen=True)
class LadderContext(object):
    adb: ADBClient
    fixture_apk: Optional[str]
    skip_install: bool
    endurance_iterations: int
    seed: int


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _compact_result(result: Dict[str, Any]) -> Dict[str, Any]:
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    readiness = result.get("readiness") if isinstance(result.get("readiness"), dict) else {}
    ui_state = result.get("ui_state") if isinstance(result.get("ui_state"), dict) else {}
    if not readiness and isinstance(ui_state.get("readiness"), dict):
        readiness = dict(ui_state["readiness"])
    return {
        "kind": result.get("kind"),
        "case": result.get("case"),
        "scenario": result.get("scenario"),
        "status": _case_status(result),
        "reason": result.get("reason"),
        "decision": {
            "skill": decision.get("skill"),
            "selected_backend": decision.get("selected_backend"),
            "confidence": decision.get("confidence"),
            "reason_summary": decision.get("reason_summary"),
        }
        if decision
        else {},
        "readiness": {
            "status": readiness.get("status"),
            "label": readiness.get("label"),
        }
        if readiness
        else {},
        "artifacts_dir": result.get("artifacts_dir"),
        "diagnostic": result.get("diagnostic"),
    }


def _false_success_risk(result: Dict[str, Any]) -> int:
    if _case_status(result) != "pass":
        return 0
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    ui_state = result.get("ui_state") if isinstance(result.get("ui_state"), dict) else {}
    readiness = result.get("readiness") if isinstance(result.get("readiness"), dict) else {}
    if not readiness and isinstance(ui_state.get("readiness"), dict):
        readiness = dict(ui_state["readiness"])
    readiness_status = str(readiness.get("status") or "")
    if readiness_status in {"blocked", "loading", "uncertain"} and not decision.get("skill"):
        return 1
    return 0


def _failure_label(result: Dict[str, Any]) -> str:
    ui_state = result.get("ui_state") if isinstance(result.get("ui_state"), dict) else {}
    primary_blocker = ui_state.get("primary_blocker") if isinstance(ui_state.get("primary_blocker"), dict) else {}
    if primary_blocker.get("type"):
        return str(primary_blocker["type"])
    readiness = result.get("readiness") if isinstance(result.get("readiness"), dict) else {}
    if readiness.get("label"):
        return str(readiness["label"])
    if result.get("diagnostic"):
        diagnostic = result.get("diagnostic") if isinstance(result.get("diagnostic"), dict) else {}
        return str(diagnostic.get("human_summary") or "diagnostic")
    return ""


def _run_chaos(case_name: str) -> Runner:
    def runner(ctx: LadderContext, output_root: Path, rng: random.Random) -> Dict[str, Any]:
        return run_chaos_dry_case(
            adb=ctx.adb,
            output_root=output_root,
            case_name=case_name,
            fixture_apk=ctx.fixture_apk,
            skip_install=True,
        )

    return runner


def _run_fixture_e2e(ctx: LadderContext, output_root: Path, rng: random.Random) -> Dict[str, Any]:
    return run_fixture_input_e2e(
        adb=ctx.adb,
        output_root=output_root,
        fixture_apk=ctx.fixture_apk,
        skip_install=True,
    )


def _run_settings(ctx: LadderContext, output_root: Path, rng: random.Random) -> Dict[str, Any]:
    return run_settings_readonly_question(ctx.adb, output_root)


def _run_chrome_random(ctx: LadderContext, output_root: Path, rng: random.Random) -> Dict[str, Any]:
    return run_chrome_random_search_question(ctx.adb, output_root, rng)


def _run_chrome_torture(scenario: str) -> Runner:
    def runner(ctx: LadderContext, output_root: Path, rng: random.Random) -> Dict[str, Any]:
        return run_chrome_web_torture_question(
            adb=ctx.adb,
            output_root=output_root,
            rng=rng,
            scenario_name=scenario,
        )

    return runner


def _run_endurance(ctx: LadderContext, output_root: Path, rng: random.Random) -> Dict[str, Any]:
    # Keep nested long-tail paths short enough for adb pull on Windows.
    short_root = output_root
    if len(output_root.parents) >= 2:
        short_root = output_root.parents[1] / "lt"
    return run_long_tail(
        adb=ctx.adb,
        output_root=short_root,
        iterations=max(1, int(ctx.endurance_iterations)),
        seed=ctx.seed + rng.randint(1, 999999),
        fixture_apk=ctx.fixture_apk,
        skip_install=True,
        profile="mixed",
    )


def build_ladder_levels() -> List[LadderLevel]:
    return [
        LadderLevel(
            level_id="L1",
            name="Readiness Baseline",
            description="Read a stable real app page and avoid unnecessary action.",
            cases=[
                LadderCase("settings_readonly", "Open Settings and inspect without acting.", _run_settings),
            ],
        ),
        LadderLevel(
            level_id="L2",
            name="Blocker Policy",
            description="Recognize fixture blockers and choose safe policy actions.",
            cases=[
                LadderCase("fixture_notification_permission", "Handle a notification permission prompt.", _run_chaos("fixture_notification_permission")),
                LadderCase("fixture_loading_state", "Wait on a loading state.", _run_chaos("fixture_loading_state")),
                LadderCase("fixture_error_state", "Recover from an error state.", _run_chaos("fixture_error_state")),
            ],
        ),
        LadderLevel(
            level_id="L3",
            name="Verified Execution",
            description="Move beyond dry-run by executing and verifying text entry.",
            cases=[
                LadderCase("fixture_input_surface", "Choose text entry on fixture input.", _run_chaos("fixture_input_surface")),
                LadderCase("fixture_input_surface_e2e", "Execute type_text and verify resulting UI text.", _run_fixture_e2e),
            ],
        ),
        LadderLevel(
            level_id="L4",
            name="Browser Search Surfaces",
            description="Handle Chrome search surfaces, including IME/stylus overlays.",
            cases=[
                LadderCase("chrome_search_stylus_overlay", "Search despite a stylus/input-method surface.", _run_chaos("chrome_search_stylus_overlay")),
                LadderCase("chrome_random_search", "Choose a search action for a random Chrome search goal.", _run_chrome_random),
            ],
        ),
        LadderLevel(
            level_id="L5",
            name="Complex Web Pages",
            description="Inspect complicated mobile web search result pages without false success or unsafe actions.",
            cases=[
                LadderCase("chrome_torture_bilibili", "Bilibili web search result page.", _run_chrome_torture("bilibili_search")),
                LadderCase("chrome_torture_github", "GitHub mobile repository search.", _run_chrome_torture("github_repo_search")),
                LadderCase("chrome_torture_wikipedia", "Wikipedia special search page.", _run_chrome_torture("wikipedia_special_search")),
                LadderCase("chrome_torture_youtube", "YouTube mobile search result page.", _run_chrome_torture("youtube_video_search")),
            ],
        ),
        LadderLevel(
            level_id="L6",
            name="Mixed Endurance",
            description="Run a mixed long-tail suite to expose interaction between cases.",
            cases=[
                LadderCase("mixed_endurance", "Nested mixed long-tail run.", _run_endurance),
            ],
        ),
    ]


def select_levels(levels: Sequence[LadderLevel], requested: str) -> List[LadderLevel]:
    requested_normalized = str(requested or "all").strip().upper()
    if requested_normalized == "ALL":
        return list(levels)
    selected = [level for level in levels if level.level_id.upper() == requested_normalized]
    if not selected:
        raise ValueError("Unknown ladder level: {0}".format(requested))
    return selected


def summarize_level(level: LadderLevel, results: Sequence[Dict[str, Any]], pass_threshold: float) -> Dict[str, Any]:
    passed = sum(1 for item in results if _case_status(item) == "pass")
    failed = len(results) - passed
    false_success_risk = sum(_false_success_risk(item) for item in results)
    pass_rate = float(passed) / float(len(results)) if results else 0.0
    failure_labels: Dict[str, int] = {}
    for item in results:
        if _case_status(item) == "pass":
            continue
        label = _failure_label(item) or "unknown"
        failure_labels[label] = failure_labels.get(label, 0) + 1
    level_passed = pass_rate >= pass_threshold and false_success_risk == 0
    return {
        "level_id": level.level_id,
        "name": level.name,
        "description": level.description,
        "status": "pass" if level_passed else "fail",
        "passed": passed,
        "failed": failed,
        "case_count": len(results),
        "pass_rate": round(pass_rate, 4),
        "false_success_risk": false_success_risk,
        "failure_labels": failure_labels,
        "cases": [_compact_result(item) for item in results],
    }


def compute_max_stable_level(level_summaries: Sequence[Dict[str, Any]]) -> str:
    max_stable = "none"
    for summary in level_summaries:
        if summary.get("status") != "pass":
            break
        max_stable = str(summary.get("level_id") or max_stable)
    return max_stable


def run_capability_ladder(
    adb: ADBClient,
    output_root: Path,
    requested_level: str,
    repeats: int,
    seed: int,
    fixture_apk: Optional[str],
    skip_install: bool,
    endurance_iterations: int,
    pass_threshold: float,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    run_dir = output_root / "ladder_{0}_{1}".format(_timestamp(), seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    levels = select_levels(build_ladder_levels(), requested_level)
    ctx = LadderContext(
        adb=adb,
        fixture_apk=fixture_apk,
        skip_install=skip_install,
        endurance_iterations=endurance_iterations,
        seed=seed,
    )

    if fixture_apk and not skip_install:
        from chaos_ui_harness import install_fixture_if_needed

        install_fixture_if_needed(adb, fixture_apk, skip_install=False)

    level_summaries: List[Dict[str, Any]] = []
    for level in levels:
        level_dir = run_dir / level.level_id
        level_results: List[Dict[str, Any]] = []
        for repeat in range(1, max(1, repeats) + 1):
            for case in level.cases:
                case_dir = level_dir / "repeat_{0:02d}_{1}".format(repeat, case.key)
                case_dir.mkdir(parents=True, exist_ok=True)
                try:
                    result = case.runner(ctx, case_dir, rng)
                except Exception as exc:
                    diagnostic = write_failure_diagnostic(
                        label="ladder_{0}_{1}".format(level.level_id, case.key),
                        kind="capability_ladder_exception",
                        summary="Unhandled exception while running capability ladder case.",
                        goal=case.description,
                        task_type=TASK_TYPE,
                        case=case.key,
                        error=exc,
                        adb=adb,
                        requested_device=getattr(adb, "device_id", None),
                        runtime_config=build_demo_message_config(),
                        output_dir=case_dir / "diagnostics",
                    )
                    result = {
                        "kind": "capability_ladder_case",
                        "case": case.key,
                        "status": "fail",
                        "reason": str(exc),
                        "diagnostic": {
                            "schema_version": diagnostic.get("schema_version"),
                            "human_summary": diagnostic.get("human_summary"),
                            "report_path": diagnostic.get("artifacts", {}).get("diagnostic_report_path"),
                        },
                        "artifacts_dir": str(case_dir),
                    }
                result["ladder_level"] = level.level_id
                result["ladder_case"] = case.key
                result["repeat"] = repeat
                level_results.append(result)
                _write_json(case_dir / "ladder_case_result.json", result)
        level_summary = summarize_level(level, level_results, pass_threshold=pass_threshold)
        level_summaries.append(level_summary)
        _write_json(level_dir / "level_report.json", level_summary)

    max_stable_level = compute_max_stable_level(level_summaries)
    failed_levels = [item for item in level_summaries if item.get("status") != "pass"]
    report = {
        "case": "capability_ladder_smoke",
        "status": "pass" if not failed_levels else "fail",
        "seed": seed,
        "requested_level": requested_level,
        "repeats": max(1, repeats),
        "pass_threshold": pass_threshold,
        "max_stable_level": max_stable_level,
        "first_failed_level": failed_levels[0]["level_id"] if failed_levels else None,
        "levels": level_summaries,
        "artifacts_dir": str(run_dir),
    }
    _write_json(run_dir / "capability_ladder_report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a capability ladder smoke test for the Android reliability runtime.")
    parser.add_argument("--device-id", default=None, help="ADB serial. Defaults to the first ready device.")
    parser.add_argument("--adb-path", default=None, help="Optional adb.exe path.")
    parser.add_argument("--fixture-apk", default=DEFAULT_FIXTURE_APK, help="Path to com.example.chaosfixture APK.")
    parser.add_argument("--skip-install", action="store_true", help="Assume the fixture app is already installed.")
    parser.add_argument("--output-root", default="data/tmp/capability_ladder", help="Directory for ladder reports.")
    parser.add_argument("--level", default="all", help="Level to run: all, L1, L2, L3, L4, L5, or L6.")
    parser.add_argument("--repeats", type=int, default=1, help="Repeat every case this many times.")
    parser.add_argument("--seed", type=int, default=20260507, help="Random seed for reproducible case selection.")
    parser.add_argument("--endurance-iterations", type=int, default=8, help="Nested mixed long-tail iterations for L6.")
    parser.add_argument("--pass-threshold", type=float, default=0.8, help="Minimum per-level pass rate.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    adb = ADBClient(adb_path=args.adb_path, device_id=args.device_id)
    try:
        adb.ensure_device(timeout=5)
    except ADBError as exc:
        diagnostic = write_failure_diagnostic(
            label="capability_ladder_no_device",
            kind="adb_error",
            summary="No ready Android device for capability ladder smoke.",
            case="capability_ladder_smoke",
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
        report = run_capability_ladder(
            adb=adb,
            output_root=Path(args.output_root),
            requested_level=args.level,
            repeats=max(1, int(args.repeats)),
            seed=int(args.seed),
            fixture_apk=args.fixture_apk,
            skip_install=bool(args.skip_install),
            endurance_iterations=max(1, int(args.endurance_iterations)),
            pass_threshold=float(args.pass_threshold),
        )
    except Exception as exc:
        diagnostic = write_failure_diagnostic(
            label="capability_ladder_exception",
            kind="unhandled_exception",
            summary="Unhandled exception while running capability ladder smoke.",
            case="capability_ladder_smoke",
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
