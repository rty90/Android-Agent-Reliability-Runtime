# Android Agent Reliability Runtime

Project URL: https://github.com/rty90/Android-Agent-Reliability-Runtime

Android Agent Reliability Runtime is a debugging, safety, and recovery layer for
Android GUI agents. It is not trying to be yet another fully autonomous mobile
agent. The existing agent is treated as an execution kernel; this project adds
the runtime layer that decides when the agent should act, wait, stop, diagnose,
or ask a human to take over.

## Why This Exists

Mobile GUI agents often fail silently:

- They tap while the screen is still loading.
- They mistake blocked pages for task success.
- They repeat actions that do not change the UI.
- They treat one-off failures as reusable memory.
- They leave no reproducible trace when something goes wrong.

This project focuses on reliability instead of raw autonomy. The goal is not to
make agents act more. The goal is to know when they should stop.

## Runtime Loop

```text
Read screen
-> Normalize state
-> Classify readiness
-> Detect blocker / loop / risk
-> Propose action
-> Policy gate
-> Execute or ask human
-> Verify progress
-> Diagnose failure
-> Store trace
-> Promote reusable lessons only when validated
```

## Core Ideas

### Readiness First

Before a model or procedure proposes an action, the runtime classifies the
screen as:

- `ready`
- `loading`
- `blocked`
- `uncertain`
- `complete`

If the screen is not ready, normal actions are blocked. The runtime should only
allow safe actions such as `wait`, `diagnose`, or `manual_handoff`.

### Progress Is Verified

Executing a tap is not the same as making progress. A successful action should
mean:

```text
action_executed == true
state_progress_verified == true
```

If the UI does not meaningfully change after an action, the runtime should mark
the step as `no_progress`, `stuck`, or `false_success`, not success.

### Failures Are Diagnosed

Failures should produce clear, stable labels such as:

- `loading_loop`
- `permission_blocker`
- `modal_blocker`
- `wrong_page`
- `target_missing`
- `no_ui_change`
- `false_success`
- `uncertain_state`
- `unsafe_action_blocked`

### Memory Is Conservative

Raw failures should not become operational memory automatically. The intended
memory pipeline is:

```text
raw_trace -> candidate_lesson -> promoted_lesson
```

Lessons should default to hints. They should become control rules only after
repeated evidence or human approval.

## Architecture

Important modules:

- `app/utils/adb.py` - low-level ADB wrapper for device interaction.
- `app/executor.py` - execution engine for bounded skills and plans.
- `app/skills/` - atomic Android actions such as tap, type, wait, back, and search.
- `app/readiness.py` - readiness classification for ready/loading/blocked/uncertain states.
- `app/ui_facts.py` - reusable UI fact extraction helpers.
- `app/ui_policy.py` - generic blocker and policy detection.
- `app/ui_state.py` - normalized UI state and goal-progress assessment.
- `app/procedural_skills.py` - generic procedure layer for common safe actions.
- `app/reasoning_orchestrator.py` - action proposer that is now gated by readiness.
- `app/lesson_policy.py` - conservative raw trace, candidate lesson, and promotion policy.
- `app/diagnostics.py` - stable failure diagnostic reports.
- `scripts/chaos_ui_harness.py` - deterministic blocker and overlay regression harness.
- `scripts/chaos_ui_e2e_smoke.py` - minimal execute-and-verify smoke test.
- `scripts/long_tail_agent_smoke.py` - mixed long-tail test runner for messy real cases.

Guided UI reasoning order:

```text
read_screen
-> ui_facts / ui_policy / ui_state / readiness
-> procedural_skills
-> memory / model fallback
-> executor
-> verifier / diagnostics
```

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

Default fixture APK path used during development:

```powershell
F:\virtualver\app\build\outputs\apk\debug\app-debug.apk
```

Example ADB path on Windows:

```powershell
C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe
```

Run one dry-run decision case:

```powershell
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
```

Recommended smoke cases:

```powershell
python scripts\chaos_ui_harness.py --case fixture_notification_permission --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case fixture_input_surface --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case fixture_loading_state --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case fixture_error_state --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
python scripts\chaos_ui_harness.py --case chrome_search_stylus_overlay --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe"
```

Run the minimal execute-and-verify E2E smoke:

```powershell
python scripts\chaos_ui_e2e_smoke.py --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
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
python scripts\long_tail_agent_smoke.py --iterations 18 --seed 20260502 --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk"
```

Use the Chrome torture profile for messy real web pages that can expose blank
WebView loads, JS challenges, cookie/captcha blockers, and repeated-search
mistakes:

```powershell
python scripts\long_tail_agent_smoke.py --iterations 8 --seed 20260506 --profile chrome_torture --device-id emulator-5554 --adb-path "C:\Users\zhufe\AppData\Local\Android\Sdk\platform-tools\adb.exe" --fixture-apk "F:\virtualver\app\build\outputs\apk\debug\app-debug.apk" --skip-install
```

Artifacts are written under:

```powershell
data\tmp\long_tail\long_tail_<timestamp>_seed_<seed>\long_tail_report.json
```

The long-tail runner keeps screenshots, XML, summaries, decisions, and
diagnostics for every round.

## Run Report Dashboard

Use the report summarizer after smoke tests to inspect recent chaos, E2E,
long-tail, and diagnostic artifacts without manually opening every folder:

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
    "requested_device": "emulator-5554",
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
