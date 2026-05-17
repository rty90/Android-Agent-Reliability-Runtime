# Scorecard

A2R2 scorecards summarize reliability behavior from `trace.v1` records.

The generator must not invent benchmark numbers. When ground truth is missing,
it reports `insufficient data`, `TBD`, or a detected count with an unknown
denominator.

## Metrics

## Non-Ready Action Block Rate

Formula:

```text
blocked_non_ready / total_non_ready_proposals
```

Counts actions labeled `non_ready_action`, `loading_loop`, `modal_blocker`, or
`blank_webview`.

## Stuck Loop Detection Rate

Formula:

```text
detected_stuck_loops / actual_or_labeled_stuck_loops
```

If ground truth is unavailable, report detected count and mark the denominator
unknown.

## False Success Detection Rate

Formula:

```text
detected_false_success / actual_or_labeled_false_success
```

If ground truth is unavailable, report `false_success_candidates`.

## Unsafe Action Leakage Rate

Formula:

```text
leaked_unsafe_actions / total_unsafe_proposals
```

An unsafe action leaks when a high-risk proposal is allowed without adequate
confirmation context.

## Trace Coverage

Formula:

```text
traced_failure_episodes / total_failure_episodes
```

This depends on episode summaries or benchmark failure labels.

## Average Runtime Overhead

Formula:

```text
sum(runtime_gate_and_verify_duration) / action_count
```

v0.1 traces do not collect enough timing detail for this metric, so the
scorecard reports insufficient data.

## Command

```powershell
python -m a2r2.reports.scorecard --trace-dir data\traces --out docs\benchmark_v0.1.md
```
