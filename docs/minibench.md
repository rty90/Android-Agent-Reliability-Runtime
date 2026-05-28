# A2R2 MiniBench (offline trap-app evidence)

MiniBench is an offline, deterministic benchmark for the A2R2 reliability
runtime. It defines small scripted Android-like "trap" scenarios, each with a
declared ground truth, and runs them through the real
`a2r2.ReliabilityRuntime`. Because the ground truth is known by construction,
the scorecard reports **real denominators** instead of "insufficient data", and
it measures **false interventions** (over-blocking) on control scenarios.

MiniBench does **not** touch a device and is **not** an Android GUI agent. It is
a reproducible evidence generator for the runtime — the deterministic
counterpart to the live ADB gate smoke.

## Why it exists

The Baseline-vs-A2R2 comparison demo shows *that* the runtime intercepts
failures, but it has no ground-truth denominator, so the generic scorecard
(`a2r2.reports.scorecard`) can only report stuck-loop and false-success counts
as "insufficient data". MiniBench closes that gap: each scenario states which
failure mode it encodes (or that the runtime should not intervene at all), so
every detection rate has a known denominator and over-blocking is measurable.

## Scenario catalogue

Each scenario is a tiny episode: `before` observation -> proposed action ->
`after` observation, evaluated against an `expectation`.

| Expectation | Meaning |
|---|---|
| `gate_block` | `check_before_action` must not allow the action (and should emit the expected diagnosis label). |
| `verify_flag` | The gate allows the action, but `verify_after_action` must flag the expected label. |
| `allow` | Control: the runtime must **not** intervene (used to measure over-blocking). |

Covered failure modes (denominators are the count of traps per mode):

- `non_ready_action` — loading text, `clickable=false`, `enabled=false`,
  content-poor XML, missing XML + screenshot.
- `loading_loop` — the same UI hash repeats across steps (caught at the gate).
- `modal_blocker` — permission dialog and alert dialog (manual handoff).
- `blank_webview` — a WebView shell with almost no content.
- `unsafe_action` — `Delete account`, `Send`, Chinese `确认支付`, and a
  high-risk keyword while the screen is non-ready (block vs. handoff).
- `no_progress` — an allowed tap that changes nothing.
- `stuck_loop` — the same action repeated without progress (caught at verify).
- `false_success` — a `done` claim and a raw success claim without a goal marker.
- Controls — a ready benign tap, a **confirmed** high-risk Send (must be
  allowed), and a tap that makes real progress.

## Metrics

The MiniBench scorecard (`minibench.v1`) reports:

- `overall_trap_detection_rate`, `overall_label_accuracy`
- per-mode rates: `non_ready_action_block_rate`, `modal_blocker_handoff_rate`,
  `loading_loop_detection_rate`, `blank_webview_detection_rate`,
  `no_progress_detection_rate`, `stuck_loop_detection_rate`,
  `false_success_detection_rate`
- `unsafe_action_interception_rate` and its complement `unsafe_action_leakage_rate`
- `false_intervention_rate` — controls that were wrongly blocked / total controls

## How to run

```powershell
cd "F:\mobile agents"
python scripts\a2r2_minibench.py
start data\reports\a2r2_minibench_<timestamp>\minibench.html
```

Useful flags:

- `--out-dir data\reports\a2r2_minibench` — fixed report directory.
- `--trace-dir data\traces` — where trace.v1 episodes are written (defaults to
  the shared corpus; each scenario is one episode).
- `--repeat N` — run every scenario N times to grow the trace corpus toward the
  50–200-trace target. Detection rates are unchanged; denominators scale by N.

The benchmark also emits standard `trace.v1` episodes, so the trace viewer and
the generic scorecard consume them:

```powershell
python -m a2r2.reports.trace_viewer --trace-dir data\traces --out data\reports\a2r2_trace_viewer.html
python -m a2r2.reports.scorecard --trace-dir data\traces --out data\reports\a2r2_scorecard.md
```

## Sample result

A clean run over the built-in catalogue (21 scenarios = 18 traps + 3 controls):

| Metric | Value |
|---|---:|
| Overall trap detection | 100% (18/18) |
| Diagnosis label accuracy | 100% (18/18) |
| Unsafe action interception | 100% (4/4) |
| Unsafe action leakage | 0% (0/4) |
| Non-ready action block | 100% (5/5) |
| Modal blocker handoff | 100% (2/2) |
| Loading loop detection | 100% (1/1) |
| Blank WebView detection | 100% (1/1) |
| No-progress detection | 100% (2/2) |
| Stuck loop detection | 100% (1/1) |
| False success detection | 100% (2/2) |
| False intervention (over-block) | 0% (0/3) |

100% is expected here: the scenarios are designed to be detectable by the
current rules. MiniBench earns its keep as a **regression guard and ground-truth
denominator source** — a policy change that breaks a label, or a new trap the
rules cannot yet catch, drops a number below 100% (or raises false
intervention above 0%). It is not a claim that A2R2 catches every real-world
failure.

## Notes and known nuances

- For `gate_block` scenarios the action is not executed, so `after == before`
  and the per-step verify naturally reports `no_progress`. The MiniBench scorer
  evaluates blocked traps on the **gate** decision, not the verify label, so this
  is cosmetic; in the trace the gate label is preserved under
  `diagnosis.decision_label`.
- The same loop is caught two ways depending on the screen signature:
  `loading_loop` (identical UI hash) is intercepted at the gate, while
  `stuck_loop` (same action, shifting transient hashes) is caught at verify.
  Both keep the agent from looping forever.
- No package-specific logic lives in MiniBench or the policies; scenarios only
  construct generic Android-like UI states. See
  [failure_taxonomy.md](failure_taxonomy.md) for label definitions and
  [scorecard.md](scorecard.md) for the generic (no-ground-truth) scorecard.
