# A2R2 v0.3: From Diagnosis to Recovery (the learning loop)

v0.1 proved the runtime on designed traps (MiniBench). v0.2 proved observation
on real agents (shadow mode + MobileGym import). v0.3 closes the loop the
conversations of 2026-06 designed: **detections become recoveries, trust in
rules becomes data-driven, and every batch makes the next one smarter** — with
zero model training.

```
UI-TARS runs on MobileGym  ──────────────┐
   │ (optional, env-gated)               │
   ├─ InterventionEngine: retry bad      │ judge gives free ground truth
   │  actions / reflect on stalls /      ▼
   │  verify early COMPLETE claims    import -> trace.v1 + benchmark
   │                                      │
   │                                      ├─ trust table  (a2r2/calibration)
   │                                      ├─ reviewer     (a2r2/review + a2r2/evidence)
   └──────── notes/retries ◄──────────────┴─ training export (SFT / DPO raw)
```

## What changed in v0.3 (each item validated on the real 26-episode corpus)

| Change | Where | Validated effect (26 real UI-TARS episodes) |
|---|---|---|
| Terminal-step artifact fix | adapter `after_observation_missing` + ProgressPolicy | `no_progress` noise 17 -> 0; `false_success` 2/2 preserved |
| Screen-identity loop detection | ProgressPolicy `same_action_same_screen_repeated` | `stuck_loop` 0 -> 4 of 5 real loops (5th is a BACK-retreat across screens — different mode, logged as future candidate) |
| Degenerate-action gate | ReadinessPolicy `degenerate_action` | flags the empty-coordinate CLICK episode; 0 blocks on successful episodes (4 would-blocks, all on garbage steps) |
| Infra-error exclusion | adapter `infra_errors` | endpoint/context-overflow episodes no longer pollute agent-failure stats |
| Overdue-termination signal | adapter fields + benchmark row | OT recall 100% (3/3), precision 18% — broader than judge-OT by design, labeled as such |

Benchmark after v0.3 (run `20260602_180714`): false-complete detection
**recall 100% / precision 100% (2/2)**, stuck loops 4, all success episodes
label-free. Reports: `data/reports/mobilegym_benchmark_uitars_180714_v5.md`.

## The new packages

- **`a2r2/calibration`** — trust table: per (label, context) measured precision
  from judge ground truth. Counting only; may auto-downgrade rules to warn,
  never auto-promotes (promotion needs a human-reviewed zero-FP replay).
  `python -m a2r2.calibration.trust --reports-dir <imports> --out data/reports/a2r2_trust_table.md`
- **`a2r2/evidence`** — optional VLM arbiter (`EvidenceProvider`): judges one
  flagged step ("is this action reasonable / is there completion evidence").
  Stdlib HTTP to an OpenAI-compatible endpoint (DashScope default); key from
  `DASHSCOPE_API_KEY` env only. `StaticEvidenceProvider` for tests.
- **`a2r2/review`** — offline retrospection: reviews only A2R2-flagged steps of
  an episode via a provider, outputs `review.v1` + first-wrong-step; `score_exam`
  tests the reviewer against human-verified cases before its labels are trusted.
  `export_training.py` emits SFT samples (successes) and failure-step JSONL
  (DPO raw material — corrected actions are never invented).
- **`a2r2/interventions`** — recovery engine (pure logic, budget-limited):
  `retry` malformed pointer actions, `note` reflection on same-action stalls,
  one `verify` nudge on suspiciously-early COMPLETE claims.

## Running the A/B (once a stable endpoint is up)

Baseline (config fixed, no A2R2):

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH="F:\mobilegym\.playwright-browsers"
$env:MOBILEGYM_UITARS_HISTORY_WINDOW="5"
$env:MOBILEGYM_A2R2_INTERVENE="0"
& F:\mobilegym\.venv\Scripts\python.exe -m bench_env.run --suite wechat --agent uitars `
  --model-api-key dummy --model-base-url <endpoint>/v1 --model-name <model> `
  --env-url http://127.0.0.1:4173 --runs-dir F:\mobilegym\runs --headless --no-stream
```

Intervention arm: same command with `MOBILEGYM_A2R2_INTERVENE="1"`. The hook
lives in `F:\mobilegym\bench_env\agent\uitars.py` (env-gated; zero behavior
change when off; never raises into the agent). Then import both runs and compare
SR/FC/OT/loops — the SR delta is A2R2's measured contribution.

## Boundaries kept

No VLM training. No task knowledge in policies or interventions. The judge owns
episode truth; the reviewer must pass an exam before labeling; the trust table
can only demote automatically. Generated data stays under gitignored `data/`.
