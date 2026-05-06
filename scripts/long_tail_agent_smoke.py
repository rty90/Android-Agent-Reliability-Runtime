from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.demo_config import build_demo_message_config
from app.diagnostics import summarize_for_console, write_failure_diagnostic
from app.readiness import classify_readiness
from app.utils.adb import ADBClient, ADBError
from chaos_ui_e2e_smoke import run_smoke as run_input_surface_e2e
from chaos_ui_harness import (
    CASES as CHAOS_CASES,
    DEFAULT_FIXTURE_APK,
    TASK_TYPE,
    _capture_state,
    dry_run_decision,
    install_fixture_if_needed,
    run_case,
)


CHROME_PACKAGE = "com.android.chrome"
SETTINGS_PACKAGE = "com.android.settings"

REAL_SEARCH_GOALS = [
    "open chrome and search for llm benchmark news",
    "open chrome and search for android emulator crash fix",
    "open chrome and search for bilibili llm videos",
    "open chrome and look up qwen vl model docs",
    "open chrome and find videos about mobile gui agents",
]

CHROME_TORTURE_SCENARIOS = [
    {
        "name": "google_serp_query_operators",
        "url": "https://www.google.com/search?q={query}",
        "query": 'android "uiautomator dump" overlay blocker fix',
        "goal": "inspect this Chrome Google results page and find the next useful action for learning about Android uiautomator overlay blockers",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "github_repo_search",
        "url": "https://github.com/search?q={query}&type=repositories",
        "query": "mobile gui agent android automation",
        "goal": "on GitHub in Chrome, search or refine the page to find repositories about mobile GUI agents",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "youtube_video_search",
        "url": "https://m.youtube.com/results?search_query={query}",
        "query": "LLM mobile GUI agent demo",
        "goal": "on YouTube mobile web in Chrome, find videos about LLM mobile GUI agents",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "wikipedia_special_search",
        "url": "https://en.wikipedia.org/w/index.php?search={query}",
        "query": "large language model agent",
        "goal": "on Wikipedia in Chrome, use the current page to look up large language model agents",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "stackoverflow_search",
        "url": "https://stackoverflow.com/search?q={query}",
        "query": "android emulator terminated after reboot",
        "goal": "on Stack Overflow in Chrome, find answers about Android emulator terminated after reboot",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "mdn_search",
        "url": "https://developer.mozilla.org/en-US/search?q={query}",
        "query": "web input focus keyboard overlay",
        "goal": "on MDN in Chrome, search for web input focus keyboard overlay behavior",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "bilibili_search",
        "url": "https://search.bilibili.com/all?keyword={query}",
        "query": "LLM agent",
        "goal": "on Bilibili in Chrome, find videos about LLM agents",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
    {
        "name": "reddit_search",
        "url": "https://www.reddit.com/search/?q={query}",
        "query": "android emulator crash play store",
        "goal": "on Reddit in Chrome, find discussions about Android emulator crashing when opening Play Store",
        "expected_skills": {None, "tap", "search_in_app", "type_text", "wait"},
    },
]


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _case_status(report: Dict[str, Any]) -> str:
    return str(report.get("status") or "unknown").strip().lower()


def _compact_decision(decision: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(decision, dict):
        return {}
    return {
        "skill": decision.get("skill"),
        "args": decision.get("args"),
        "confidence": decision.get("confidence"),
        "selected_backend": decision.get("selected_backend"),
        "reason_summary": decision.get("reason_summary"),
        "validation_errors": decision.get("validation_errors"),
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_chaos_dry_case(
    adb: ADBClient,
    output_root: Path,
    case_name: str,
    fixture_apk: Optional[str],
    skip_install: bool,
) -> Dict[str, Any]:
    report = run_case(
        adb=adb,
        case_name=case_name,
        output_root=output_root / "chaos",
        fixture_apk=fixture_apk,
        skip_install=skip_install,
    )
    return {
        "kind": "chaos_dry_run",
        "case": case_name,
        "status": _case_status(report),
        "goal": report.get("goal") or CHAOS_CASES.get(case_name, {}).get("goal"),
        "decision": _compact_decision(report.get("decision")),
        "reason": report.get("reason"),
        "artifacts_dir": report.get("artifacts_dir"),
        "diagnostic": report.get("diagnostic"),
    }


def run_fixture_input_e2e(
    adb: ADBClient,
    output_root: Path,
    fixture_apk: Optional[str],
    skip_install: bool,
) -> Dict[str, Any]:
    report = run_input_surface_e2e(
        adb=adb,
        output_root=output_root / "chaos_e2e",
        fixture_apk=fixture_apk,
        skip_install=skip_install,
    )
    return {
        "kind": "chaos_e2e",
        "case": "fixture_input_surface_e2e",
        "status": _case_status(report),
        "goal": report.get("goal"),
        "decision": _compact_decision(report.get("decision")),
        "skill_result": report.get("skill_result"),
        "reason": report.get("reason"),
        "artifacts_dir": report.get("artifacts_dir"),
        "diagnostic": report.get("diagnostic"),
    }


def run_settings_readonly_question(adb: ADBClient, output_root: Path) -> Dict[str, Any]:
    goal = "open settings and inspect the current page"
    case_dir = output_root / "real_settings_readonly_{0}".format(_timestamp())
    adb.force_stop_app(SETTINGS_PACKAGE)
    adb.open_app(SETTINGS_PACKAGE, wait_time=1.8)
    state = _capture_state(adb, case_dir, "settings_home", goal)
    decision = dry_run_decision(case_dir, goal, state)
    passed = decision.get("skill") is None
    report = {
        "kind": "real_app_dry_run",
        "case": "settings_readonly",
        "status": "pass" if passed else "fail",
        "goal": goal,
        "decision": _compact_decision(decision),
        "reason": "Read-only settings inspection should not choose an action.",
        "artifacts_dir": str(case_dir),
    }
    _write_json(case_dir / "long_tail_case.json", report)
    return report


def run_chrome_random_search_question(adb: ADBClient, output_root: Path, rng: random.Random) -> Dict[str, Any]:
    goal = rng.choice(REAL_SEARCH_GOALS)
    case_dir = output_root / "real_chrome_search_{0}".format(_timestamp())
    adb.force_stop_app(CHROME_PACKAGE)
    try:
        adb.open_url("https://www.google.com/", package_name=CHROME_PACKAGE, wait_time=2.5)
    except ADBError:
        adb.open_app(CHROME_PACKAGE, wait_time=2.0)
    state_before = _capture_state(adb, case_dir, "before_focus", goal)
    search_target = _find_search_target(state_before.get("summary") or {})
    if search_target and search_target.get("bounds"):
        bounds = search_target["bounds"]
        adb.tap(bounds["center_x"], bounds["center_y"])
        time.sleep(1.5)
    state = _capture_state(adb, case_dir, "after_focus", goal)
    decision = dry_run_decision(case_dir, goal, state)
    skill = decision.get("skill")
    passed = skill in {"search_in_app", "type_text"}
    report = {
        "kind": "real_app_random_question",
        "case": "chrome_random_search",
        "status": "pass" if passed else "fail",
        "goal": goal,
        "decision": _compact_decision(decision),
        "reason": "Random Chrome search question should choose search_in_app or type_text.",
        "prepared_target": search_target,
        "artifacts_dir": str(case_dir),
    }
    _write_json(case_dir / "long_tail_case.json", report)
    return report


def run_chrome_web_torture_question(
    adb: ADBClient,
    output_root: Path,
    rng: random.Random,
    scenario_name: Optional[str] = None,
) -> Dict[str, Any]:
    scenarios = [item for item in CHROME_TORTURE_SCENARIOS if item["name"] == scenario_name]
    scenario = dict(scenarios[0] if scenarios else rng.choice(CHROME_TORTURE_SCENARIOS))
    query = scenario["query"]
    url = scenario["url"].format(query=quote_plus(query))
    goal = scenario["goal"]
    case_dir = output_root / "real_chrome_web_torture_{0}_{1}".format(
        scenario["name"],
        _timestamp(),
    )
    adb.force_stop_app(CHROME_PACKAGE)
    try:
        adb.open_url(url, package_name=CHROME_PACKAGE, wait_time=5.0)
    except ADBError:
        adb.open_app(CHROME_PACKAGE, wait_time=3.0)
    state = _capture_state(adb, case_dir, "loaded_page", goal)
    decision = dry_run_decision(case_dir, goal, state)
    skill = decision.get("skill")
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    current_url = str((state.get("summary") or {}).get("current_url") or "")
    target_text = " ".join(
        str(args.get(key) or "").strip().lower()
        for key in ("target", "label", "text", "query", "action_id")
    )
    readiness = ((state.get("ui_state") or {}).get("readiness") or {}) if isinstance(state.get("ui_state"), dict) else {}
    if not isinstance(readiness, dict) or not readiness:
        readiness = classify_readiness(state.get("summary") or {}, blockers=[])
    readiness_status = str(readiness.get("status") or "")
    observed_blocker = readiness_status == "blocked"
    observed_loading = readiness_status in {"loading", "uncertain"}
    forbidden = {"back", "open_app"}
    if observed_blocker:
        passed = skill == "wait"
    elif observed_loading:
        passed = skill == "wait"
    else:
        passed = skill is None and skill not in forbidden
    if skill in {"type_text", "search_in_app"} and not (args.get("text") or args.get("query")):
        passed = False
    if "settings" in target_text and "setting" not in goal.lower():
        passed = False
    report = {
        "kind": "real_app_web_torture",
        "case": "chrome_web_torture",
        "scenario": scenario["name"],
        "status": "pass" if passed else "fail",
        "goal": goal,
        "url": url,
        "query": query,
        "page": (state.get("summary") or {}).get("page"),
        "current_url": current_url,
        "current_domain": (state.get("summary") or {}).get("current_domain"),
        "readiness": readiness,
        "observed_blocker": observed_blocker,
        "observed_loading": observed_loading,
        "decision": _compact_decision(decision),
        "reason": (
            "Complex Chrome page exposed a web blocker/captcha/consent state."
            if observed_blocker
            else "Complex Chrome page still looked visually unloaded; expected wait instead of acting/searching."
            if observed_loading
            else "Complex Chrome search-result page should stop when the requested result page is already loaded."
        ),
        "artifacts_dir": str(case_dir),
    }
    _write_json(case_dir / "long_tail_case.json", report)
    return report


def _find_search_target(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    best = None
    best_score = -1
    for target in summary.get("possible_targets", []):
        if not isinstance(target, dict):
            continue
        combined = " ".join(
            str(target.get(key) or "").strip().lower()
            for key in ("label", "resource_id", "content_desc", "class_name", "hint")
        )
        if "edittext" not in combined and "url_bar" not in combined and "location_bar" not in combined:
            continue
        score = 0
        if "edittext" in combined:
            score += 4
        if "url_bar" in combined or "location_bar" in combined:
            score += 4
        if any(marker in combined for marker in ("search", "url", "address")):
            score += 2
        if bool(target.get("focused")):
            score += 1
        if target.get("bounds") and score > best_score:
            best = target
            best_score = score
    return best


def _build_iteration_plan(rng: random.Random, iterations: int, profile: str = "mixed") -> List[Dict[str, Any]]:
    if profile == "chrome_torture":
        scenarios = [item["name"] for item in CHROME_TORTURE_SCENARIOS]
        rng.shuffle(scenarios)
        plan = []
        for index in range(max(1, iterations)):
            if index < len(scenarios):
                scenario_name = scenarios[index]
            else:
                scenario_name = rng.choice(scenarios)
            plan.append(
                {
                    "kind": "real_app_web_torture",
                    "case": "chrome_web_torture",
                    "scenario": scenario_name,
                }
            )
        return plan

    must_run = [
        {"kind": "chaos_dry_run", "case": "fixture_notification_permission"},
        {"kind": "chaos_dry_run", "case": "fixture_input_surface"},
        {"kind": "chaos_dry_run", "case": "fixture_loading_state"},
        {"kind": "chaos_dry_run", "case": "fixture_error_state"},
        {"kind": "chaos_e2e", "case": "fixture_input_surface_e2e"},
        {"kind": "real_app_dry_run", "case": "settings_readonly"},
        {"kind": "real_app_random_question", "case": "chrome_random_search"},
        {"kind": "real_app_web_torture", "case": "chrome_web_torture"},
    ]
    optional_cases = [
        {"kind": "chaos_dry_run", "case": case_name}
        for case_name in sorted(CHAOS_CASES.keys())
        if case_name != "chrome_search_stylus_overlay"
    ] + [
        {"kind": "chaos_dry_run", "case": "chrome_search_stylus_overlay"},
        {"kind": "real_app_random_question", "case": "chrome_random_search"},
        {"kind": "real_app_web_torture", "case": "chrome_web_torture"},
        {"kind": "real_app_dry_run", "case": "settings_readonly"},
    ]
    plan = list(must_run[: max(0, min(iterations, len(must_run)))])
    while len(plan) < iterations:
        plan.append(dict(rng.choice(optional_cases)))
    rng.shuffle(plan)
    return plan


def run_long_tail(
    adb: ADBClient,
    output_root: Path,
    iterations: int,
    seed: int,
    fixture_apk: Optional[str],
    skip_install: bool,
    profile: str = "mixed",
) -> Dict[str, Any]:
    rng = random.Random(seed)
    run_dir = output_root / "long_tail_{0}_seed_{1}".format(_timestamp(), seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    install_fixture_if_needed(adb, fixture_apk, skip_install=skip_install)

    plan = _build_iteration_plan(rng, iterations, profile=profile)
    results: List[Dict[str, Any]] = []
    for index, item in enumerate(plan, start=1):
        item_dir = run_dir / "round_{0:02d}_{1}".format(index, item["case"])
        item_dir.mkdir(parents=True, exist_ok=True)
        try:
            if item["kind"] == "chaos_dry_run":
                result = run_chaos_dry_case(
                    adb=adb,
                    output_root=item_dir,
                    case_name=item["case"],
                    fixture_apk=fixture_apk,
                    skip_install=True,
                )
            elif item["kind"] == "chaos_e2e":
                result = run_fixture_input_e2e(
                    adb=adb,
                    output_root=item_dir,
                    fixture_apk=fixture_apk,
                    skip_install=True,
                )
            elif item["kind"] == "real_app_dry_run":
                result = run_settings_readonly_question(adb, item_dir)
            elif item["kind"] == "real_app_random_question":
                result = run_chrome_random_search_question(adb, item_dir, rng)
            elif item["kind"] == "real_app_web_torture":
                result = run_chrome_web_torture_question(
                    adb,
                    item_dir,
                    rng,
                    scenario_name=item.get("scenario"),
                )
            else:
                raise ValueError("Unknown long-tail item kind: {0}".format(item["kind"]))
        except Exception as exc:
            diagnostic = write_failure_diagnostic(
                label="long_tail_{0}_{1}".format(index, item["case"]),
                kind="long_tail_exception",
                summary="Unhandled exception while running long-tail smoke item.",
                goal=item.get("case"),
                task_type=TASK_TYPE,
                case=item.get("case"),
                error=exc,
                adb=adb,
                requested_device=getattr(adb, "device_id", None),
                runtime_config=build_demo_message_config(),
                output_dir=item_dir / "diagnostics",
            )
            result = {
                "kind": item.get("kind"),
                "case": item.get("case"),
                "status": "fail",
                "reason": str(exc),
                "diagnostic": {
                    "schema_version": diagnostic.get("schema_version"),
                    "human_summary": diagnostic.get("human_summary"),
                    "report_path": diagnostic.get("artifacts", {}).get("diagnostic_report_path"),
                },
                "artifacts_dir": str(item_dir),
            }
        result["round"] = index
        result["planned_kind"] = item["kind"]
        results.append(result)
        _write_json(item_dir / "round_result.json", result)

    passed = [item for item in results if item.get("status") == "pass"]
    failed = [item for item in results if item.get("status") != "pass"]
    report = {
        "case": "long_tail_agent_smoke",
        "status": "pass" if not failed else "fail",
        "seed": seed,
        "profile": profile,
        "iterations": iterations,
        "passed": len(passed),
        "failed": len(failed),
        "results": results,
        "artifacts_dir": str(run_dir),
    }
    _write_json(run_dir / "long_tail_report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a long-tail Android agent smoke test.")
    parser.add_argument("--device-id", default=None, help="ADB serial. Defaults to the first ready device.")
    parser.add_argument("--adb-path", default=None, help="Optional adb.exe path.")
    parser.add_argument("--fixture-apk", default=DEFAULT_FIXTURE_APK, help="Path to com.example.chaosfixture APK.")
    parser.add_argument("--skip-install", action="store_true", help="Assume the fixture app is already installed.")
    parser.add_argument("--output-root", default="data/tmp/long_tail", help="Directory for long-tail reports.")
    parser.add_argument("--iterations", type=int, default=12, help="Number of mixed long-tail rounds.")
    parser.add_argument("--seed", type=int, default=20260501, help="Random seed for reproducible question mix.")
    parser.add_argument(
        "--profile",
        choices=("mixed", "chrome_torture"),
        default="mixed",
        help="Use mixed app/fixture coverage or Chrome-only complex web pages.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    adb = ADBClient(adb_path=args.adb_path, device_id=args.device_id)
    try:
        adb.ensure_device(timeout=5)
    except ADBError as exc:
        diagnostic = write_failure_diagnostic(
            label="long_tail_no_device",
            kind="adb_error",
            summary="No ready Android device for long-tail smoke.",
            case="long_tail_agent_smoke",
            error=exc,
            adb=adb,
            requested_device=args.device_id,
            exit_code=2,
            capture_artifacts=False,
        )
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        print(summarize_for_console(diagnostic), file=sys.stderr)
        return 2

    report = run_long_tail(
        adb=adb,
        output_root=Path(args.output_root),
        iterations=max(1, int(args.iterations)),
        seed=int(args.seed),
        fixture_apk=args.fixture_apk,
        skip_install=bool(args.skip_install),
        profile=args.profile,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
