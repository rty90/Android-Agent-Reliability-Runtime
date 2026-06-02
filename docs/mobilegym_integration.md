# A2R2 + MobileGym

MobileGym is a browser-hosted Android-like simulator with structured JSON state,
programmatic judges, screenshots, trajectories, and benchmark summaries. A2R2
should not replace those judges. The useful integration is:

```text
MobileGym agent run -> MobileGym judge/result -> A2R2 trace importer -> reliability scorecard
```

MobileGym tells whether the task passed, whether the agent falsely completed,
and whether unexpected side effects occurred. A2R2 adds process-level labels
over the action trajectory: no progress, false success candidate, risky action,
stuck loop, and trace.v1 replay artifacts.

## Why This Is Useful

- MobileGym avoids the Android emulator black-screen/null-root problem by using a browser simulator.
- Its deterministic judges give real denominators for false complete and unexpected side effects.
- A2R2 can be evaluated as an external reliability observer across MobileGym's existing agents.
- No VLM is required for the first integration.

## Run MobileGym

From a separate MobileGym checkout:

```powershell
git clone https://github.com/Purewhiter/mobilegym.git
cd mobilegym
npm install
pip install -r bench_env/requirements.txt
playwright install chromium
npm run build
npm run preview -- --port 4173
```

Then run a small task or suite:

```powershell
python -m bench_env.run --task-id wechat.ReadMyWxid --agent human --env-url http://localhost:4173
```

MobileGym writes:

```text
runs/<timestamp>/
  meta.json
  results.jsonl
  summary.json
  trajectory/<task_id>/
    trajectory.json
    step_001.jpg
    step_001_annot.jpg
```

## Import Into A2R2

From this repository:

```powershell
python scripts\import_mobilegym_run.py ^
  --run-dir path\to\mobilegym\runs\<timestamp> ^
  --traces-root data\traces\mobilegym_import ^
  --out-dir data\reports\mobilegym_import
```

Outputs:

```text
data/traces/mobilegym_import/<episode_id>/steps.jsonl
data/traces/mobilegym_import/<episode_id>/episode_summary.json
data/reports/mobilegym_import/mobilegym_a2r2_summary.json
data/reports/mobilegym_import/mobilegym_a2r2_summary.md
```

## Current Boundary

This is an offline importer, not a live gate inside MobileGym. That is deliberate
for the first migration step:

- MobileGym remains the environment and deterministic judge.
- A2R2 remains the external reliability observer.
- The importer does not alter MobileGym tasks, agents, rewards, or actions.
- The importer uses MobileGym route/screenshot data as observations because
  MobileGym does not expose Android XML.

## Next Step

After a few imported runs, compare:

- MobileGym `false_complete` vs A2R2 `false_success`
- MobileGym unexpected side effects vs A2R2 high-risk action labels
- MobileGym repetitive/overdue termination vs A2R2 no-progress/stuck-loop labels
- Per-step A2R2 first flag lead time relative to MobileGym failure outcome

## Benchmark Command

`a2r2.reports.mobilegym_benchmark` reads one or more imported
`mobilegym_a2r2_summary.json` files and produces a benchmark that puts MobileGym
ground truth next to A2R2 detected labels (schema `a2r2_mobilegym_benchmark.v1`):

```powershell
python scripts\mobilegym_benchmark.py `
  --reports-dir data\reports\mobilegym_import `
  --out docs\mobilegym_benchmark_v0.1.md
```

It writes Markdown + JSON with a table:

| Metric | MobileGym Ground Truth | A2R2 Detected | Notes |
|---|---:|---:|---|
| False Complete Episodes | N | M | recall/precision of A2R2 `false_success` vs MobileGym `false_complete` |
| Unexpected Side Effects | N | M | A2R2 risk labels; mapping approximate |
| No Progress / Stuck Loop | n/a | M | MobileGym has no direct ground truth |
| Trace Coverage | N | M | episodes with an A2R2 trace.v1 written |

The False Complete row reports a real confusion matrix (TP/FN/FP) so A2R2's
detection recall and precision are measured against MobileGym's judge, not
assumed. Numbers are only ever derived from imported runs; metrics without a
MobileGym ground truth are reported as `n/a` rather than fabricated. The
`docs/` output is intended to be regenerated from real imported runs.
