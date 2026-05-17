# A2R2: Android Agent Reliability Runtime

Project URL: https://github.com/rty90/Android-Agent-Reliability-Runtime

A2R2 is a black-box safety gate for Android GUI agents: it catches unsafe
actions before execution, verifies real progress after execution, and turns
silent failures into reproducible traces.

Mobile agents are getting better at doing tasks. They are still bad at knowing
when they are failing.

A2R2 does not try to be a smarter agent. It wraps existing agents with a
process-level reliability layer.

## What A2R2 Does

- Readiness Gate
- Action Risk Gate
- Progress Verification
- False Success Detection
- Failure Taxonomy
- Black-Box Trace Recorder
- Reliability Scorecard
- Agent-Agnostic Wrapper

## What A2R2 Does Not Do

- It does not plan tasks.
- It does not replan after failure.
- It does not decompose goals.
- It does not replace AndroidWorld.
- It does not optimize final success rate directly.
- It does not train VLMs in v0.1.
- It does not bypass login/captcha/payment walls.

## Runtime Boundary

```text
Any Agent -> A2R2 Runtime -> Executor / Android Device
                  |
                  v
          Trace Recorder / Scorecard
```

AndroidWorld tells whether an agent failed. A2R2 tells why and when it started
failing before it fully failed.

The existing `app/` package is treated as legacy agent/proposer/executor code
that A2R2 can wrap. In particular, `app/reasoning_orchestrator.py` is an action
proposer component, not the runtime decision authority.

## Core v0.1 API

```python
from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig

runtime = ReliabilityRuntime(config=RuntimeConfig())

decision = runtime.check_before_action(
    goal=goal,
    observation=before,
    proposed_action=action,
    history=history,
)

verification = runtime.verify_after_action(
    goal=goal,
    before_observation=before,
    action=action,
    after_observation=after,
    history=history,
)

runtime.record_step(goal, before, action, decision, after, verification, history)
```

## Benchmark Template

Do not invent benchmark numbers. Fill these only from trace-backed runs.

| Metric | Baseline Agent | + A2R2 Rules | + A2R2 Rules + VLM |
|---|---:|---:|---:|
| Non-Ready Action Block Rate | TBD | TBD | future |
| Stuck Loop Detection Rate | TBD | TBD | future |
| False Success Detection Rate | TBD | TBD | future |
| Unsafe Action Leakage Rate | TBD | TBD | future |
| Trace Coverage | TBD | TBD | future |
| Avg Runtime Overhead / Action | TBD | TBD | future |

## Repository Map

- `a2r2/` - v0.1 reliability middleware API, policies, recorder, and scorecard.
- `examples/wrap_external_agent.py` - dry-run wrapper around a dummy external agent.
- `docs/` - architecture, trace schema, failure taxonomy, and scorecard definitions.
- `app/` - legacy Android GUI agent/proposer/executor modules.
- `scripts/` - existing chaos, long-tail, ladder, and summary harnesses.

## Setup

Requirements:

- Python 3.8+
- Android Studio Emulator or an Android device with ADB enabled
- Android platform-tools available through `adb`
- Optional: chaos fixture APK for deterministic blocker tests

Install Python dependencies:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Check that a device is online:

```powershell
adb devices
```

## Basic CLI Usage

Show CLI help:

```powershell
python -m app.main --help
```

Read the current screen:

```powershell
python -m app.main --task "read the current screen and summarize it" --task-type read_current_screen
```

Run a guided UI task with the reasoning stack:

```powershell
python -m app.main --task "open settings and inspect the current page" --task-type guided_ui_task --reasoner-backend stack --agent-mode interactive --max-steps 3 --auto-confirm
```

Run coach mode, where the system suggests actions while a human operates:

```powershell
python -m app.main --task "open chrome and search for llm" --task-type guided_ui_task --agent-mode coach --reasoner-backend stack
```

## Supported Task Types

The older bounded flows are still available as execution-kernel capabilities:

- `send_message`
- `extract_and_copy`
- `create_reminder`
- `read_current_screen`
- `guided_ui_task`
- `unsupported`

The new project direction is centered on `guided_ui_task`, diagnostics, coach
mode, and reliability testing.

## Model Configuration

The runtime can use local or OpenAI-compatible model services for reasoning, but
models should be treated as action proposers rather than final decision-makers.

Common environment variables:

```powershell
$env:LOCAL_TEXT_REASONER_BASE_URL="http://127.0.0.1:9000/v1"
$env:LOCAL_TEXT_REASONER_MODEL="Qwen/Qwen3.5-0.8B"
$env:REASONING_REQUEST_TIMEOUT_SECONDS="30"
$env:REASONING_DISABLE_LOCAL_TEXT_AFTER_FAILURE="1"
$env:REASONING_ENABLE_LOCAL_VL="0"
```

Do not commit API keys. Use environment variables or your shell profile.

## Chaos UI Regression Harness

The chaos harness is a deterministic ADB regression tool for UI blockers:

- permission dialogs
- onboarding overlays
- bottom sheets
- loading states
- error states
- stylus / IME overlays
- Chrome search overlay cases

Set these values for your own machine before running the examples:

```powershell
$env:ADB_PATH="<path-to-adb-executable>"
$env:DEVICE_ID="<adb-device-id>"
$env:FIXTURE_APK="<path-to-chaos-fixture-apk>"
```

Build or download the chaos fixture app first, then point `FIXTURE_APK` at the
generated debug APK. The examples intentionally use placeholders instead of
machine-specific absolute paths. If you prefer to omit `--fixture-apk`, set
`CHAOS_FIXTURE_APK` to the same APK path.

Run one dry-run decision case:

```powershell
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
```

Recommended smoke cases:

```powershell
python scripts\chaos_ui_harness.py --case fixture_notification_permission --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
python scripts\chaos_ui_harness.py --case fixture_loading_state --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
python scripts\chaos_ui_harness.py --case fixture_error_state --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
python scripts\chaos_ui_harness.py --case chrome_search_stylus_overlay --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH"
```

Run the minimal execute-and-verify E2E smoke:

```powershell
python scripts\chaos_ui_e2e_smoke.py --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
```

Artifacts are written under:

```powershell
data\tmp\chaos\...
data\tmp\chaos_e2e\...
```

## Long-Tail Agent Smoke

Use this when you want a longer mixed run with real-ish and randomized goals.
It mixes chaos fixture blockers, real Settings read-only inspection, Chrome
random search questions, and one execute-and-verify input E2E.

```powershell
python scripts\long_tail_agent_smoke.py --iterations 18 --seed 20260502 --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK"
```

Use the Chrome torture profile for messy real web pages that can expose blank
WebView loads, JS challenges, cookie/captcha blockers, and repeated-search
mistakes:

```powershell
python scripts\long_tail_agent_smoke.py --iterations 8 --seed 20260506 --profile chrome_torture --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK" --skip-install
```

Artifacts are written under:

```powershell
data\tmp\long_tail\long_tail_<timestamp>_seed_<seed>\long_tail_report.json
```

The long-tail runner keeps screenshots, XML, summaries, decisions, and
diagnostics for every round.

## Capability Ladder Smoke

Use the capability ladder when you want to measure the runtime's current
verified ceiling instead of running one giant task with an ambiguous failure.
The ladder is staged from simple readiness checks to mixed endurance:

- `L1` readiness baseline
- `L2` blocker policy
- `L3` verified execution
- `L4` browser search surfaces and IME/stylus overlays
- `L5` complex mobile web pages
- `L6` mixed endurance

Run the full ladder:

```powershell
python scripts\capability_ladder_smoke.py --level all --repeats 1 --seed 20260507 --endurance-iterations 8 --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK" --skip-install
```

Run a single level while debugging:

```powershell
python scripts\capability_ladder_smoke.py --level L4 --repeats 1 --seed 20260507 --device-id "$env:DEVICE_ID" --adb-path "$env:ADB_PATH" --fixture-apk "$env:FIXTURE_APK" --skip-install
```

The report includes `max_stable_level`, `first_failed_level`, per-level pass
rates, false-success risk, failure labels, and artifact directories.

Artifacts are written under:

```powershell
data\tmp\capability_ladder\ladder_<timestamp>_<seed>\capability_ladder_report.json
```

## Run Report Dashboard

Use the report summarizer after smoke tests to inspect recent chaos, E2E,
long-tail, capability ladder, and diagnostic artifacts without manually opening
every folder:

```powershell
python scripts\summarize_runs.py --latest 10
```

Useful variants:

```powershell
python scripts\summarize_runs.py --latest 20 --failures-only
python scripts\summarize_runs.py --latest 10 --json
```

The summary shows pass/fail counts, readiness labels, selected skills, failure
labels, false-success risk, report paths, and artifact directories.

## Failure Diagnostics

Agent and harness failures write a stable diagnostic JSON report using schema
`agent.diagnostic.v1`.

Example shape:

```json
{
  "schema_version": "agent.diagnostic.v1",
  "status": "fail",
  "kind": "adb_error | unhandled_exception | agent_result_failure | chaos_harness_failure | chaos_e2e_failure",
  "human_summary": "Short explanation for humans",
  "error": {"type": "...", "message": "...", "traceback": "..."},
  "device": {
    "requested_device": "<device-id>",
    "connected": true,
    "current_focus": "...",
    "foreground_package": "...",
    "top_activity": "...",
    "crash_log_tail": "..."
  },
  "artifacts": {
    "diagnostic_report_path": "...",
    "screenshot_path": "...",
    "ui_dump_path": "...",
    "screen_summary_path": "..."
  }
}
```

Default diagnostic locations:

```powershell
data\tmp\diagnostics\...
data\tmp\chaos\...\diagnostics\diagnostic.json
data\tmp\chaos_e2e\...\diagnostics\diagnostic.json
```

`device.crash_log_tail` is the tail of `adb logcat -b crash`. It is useful for
emulator or app crash clues, but it may include earlier crashes if the buffer
was not cleared before the run.

## Tests

Run all unit tests:

```powershell
python -m unittest discover -s tests -v
```

Run the focused reliability tests:

```powershell
python -m unittest tests.test_readiness tests.test_ui_state tests.test_procedural_skills tests.test_reasoning_orchestrator tests.test_diagnostics tests.test_adb -v
```

Syntax check:

```powershell
python -m py_compile app\readiness.py app\ui_state.py app\reasoning_orchestrator.py scripts\long_tail_agent_smoke.py
```

## Safety Notes

- The project is emulator-first.
- The runtime reads foreground UI state; it does not inspect private app data.
- Browser WebView content is often incomplete in `uiautomator` XML, so readiness
  may conservatively return `uncertain`.
- Captcha, login walls, and web challenges should normally trigger diagnosis or
  human handoff rather than automated bypass attempts.
- High-risk actions should require confirmation.

## Roadmap

- Add a visual readiness layer for Chrome/WebView pages.
- Add stronger post-action progress verification.
- Add `raw_trace -> candidate_lesson -> promoted_lesson` storage.
- Make coach mode the default user-facing experience.
- Improve failure taxonomy and recovery recommendations.
- Keep procedures generic; avoid app-specific if-else growth.
