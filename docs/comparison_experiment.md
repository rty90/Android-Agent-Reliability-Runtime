# A2R2 Comparison Experiment

This comparison is designed to make the runtime effect visible before running a larger benchmark.

It is not a final task-success benchmark. It is a controlled failure-visibility demo:

- Baseline: the external agent proposal is treated as directly executed.
- + A2R2: the same proposal is gated before execution, verified after execution, diagnosed, and traced.

## What To Show

Use the scripted comparison demo first:

```powershell
cd "F:\mobile agents"
python scripts\a2r2_comparison_demo.py
```

The script writes:

- `comparison.json`
- `comparison.md`
- `comparison.html`
- one A2R2 `trace.v1` episode under `data/traces`

The visual effect should be simple:

| Failure mode | Baseline | + A2R2 |
|---|---|---|
| Loading/non-ready action | action leaks through | action is blocked or converted to wait |
| Unsafe send/delete-style action | action leaks through | manual handoff or block |
| Repeated no-progress action | silently repeats | labeled as no_progress or stuck_loop |
| False done claim | accepted as success | labeled as false_success candidate |
| Reproducibility | no step trace | trace.v1 JSONL with diagnosis |

## How To Scale It

After the scripted demo, run live smoke on a test device:

```powershell
$traceRoot = "data\traces\exp_live_$(Get-Date -Format yyyyMMdd_HHmmss)"
python scripts\a2r2_live_gate_smoke.py --proposal wait --out $traceRoot
python scripts\a2r2_live_gate_smoke.py --proposal dangerous-send --out $traceRoot
python scripts\a2r2_live_gate_smoke.py --proposal tap-first --out $traceRoot
python scripts\a2r2_live_gate_smoke.py --proposal done --out $traceRoot
python -m a2r2.reports.trace_viewer --trace-dir $traceRoot --out data\reports\a2r2_trace_viewer.html --latest 100
python -m a2r2.reports.scorecard --trace-dir $traceRoot --out data\reports\a2r2_scorecard.md
```

For a larger experiment, keep the same comparison framing but replace the scripted proposals with real external-agent proposals.
Do not compare on final task success first. Compare on failure observability:

- unsafe action leakage
- non-ready action leakage
- false success candidates
- stuck/no-progress diagnosis
- trace coverage

