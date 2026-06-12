# A2R2 Shadow Trace Harness (v0.2)

Shadow mode runs A2R2 alongside a real agent without interfering. The agent
executes normally; for every gated step A2R2 records what it would have decided
(`allow`, `wait`, `block`, `manual_handoff`) plus its progress verification, but
it never blocks.

This is the step that turns A2R2 from a self-proving demo into real evidence:

> Before the agent's task ultimately failed, did A2R2 flag a relevant problem
> earlier than the failure, and earlier than the agent's own guard?

Shadow mode deliberately avoids enforcement. The v0.2 goal is to prove whether
A2R2 sees trouble, not to answer who takes over after a block.

## Design

```text
real agent runs normally
        |
        v   Executor.step_observer, read-only
A2R2 observes each proposed action: would_decide + verify
        |
        v
trace.v1 episode + shadow_report.json/md/html
```

Pieces:

- `RuntimeConfig.enforce: bool = False` records shadow intent. The runtime never
  executes actions either way.
- `Executor.step_observer` is an optional read-only callback fired once per
  executed step. When `None`, behavior is unchanged. Observer exceptions are
  swallowed so shadow mode cannot break the agent.
- `a2r2/adapters/legacy_app.py` maps the legacy screen summary and `(skill,args)`
  format to generic `Observation` and `ProposedAction`. It also preserves compact
  `possible_targets` facts so replay can distinguish visible text from actual
  actionable controls.
- `a2r2/shadow/harness.py` consumes step events, asks the runtime for
  would-decisions, records trace.v1, and builds a per-episode shadow report.

## What The Report Answers

`shadow_report.v1` headline fields:

- `a2r2_prediction_matched_failure`: episode failed and A2R2 raised a concern at
  or before the first failing step.
- `timeline.a2r2_lead_over_failure`: steps between A2R2's first flag and the
  first failing step. Positive means A2R2 warned earlier.
- `timeline.a2r2_lead_over_agent_guard`: steps A2R2 led the agent's own guard
  signals such as manual intervention or agent-reported failures.
- `over_flag_count`: steps A2R2 would have blocked that actually succeeded in a
  successful episode.
- `avg_a2r2_overhead_ms_per_action`: real per-action gate+verify overhead.
- `agent_reported_failure_labels`: generic labels captured from the agent's own
  failure detail, kept separate from A2R2's prediction labels.

## Run A Shadow Episode

Requires a device or emulator:

```powershell
cd "F:\mobile agents"
python scripts\a2r2_shadow_agent_run.py `
  --task "open settings and inspect current page" `
  --task-type guided_ui_task `
  --max-steps 10 `
  --trace-dir data\traces\real_agent_shadow
```

Open the generated HTML report:

```powershell
start data\reports\a2r2_shadow_<timestamp>\shadow_report.html
```

The emitted episodes are standard `trace.v1`, so the trace viewer can consume
them too:

```powershell
python -m a2r2.reports.trace_viewer `
  --trace-dir data\traces\real_agent_shadow `
  --out data\reports\shadow_viewer.html
```

## Aggregate A Batch

Roll many single-episode reports into real-denominator metrics:

```powershell
python -m a2r2.shadow.aggregate `
  --reports-dir data\reports `
  --out data\reports\a2r2_shadow_aggregate.md
```

`shadow_aggregate.v1` reports:

- `predicted_early_rate`: failed episodes with lead > 0 / failed episodes.
- `coincident_flag_rate`: failed episodes flagged at the same step / failed
  episodes.
- `blind_spot_rate`: failures captured from the agent's own detail but not
  predicted early by A2R2 / failed episodes.
- `over_flag_rate`: over-flags / gated steps.
- lead-time stats over genuinely early hits.
- weighted average overhead per action.

## Failure Catalog

Turn failed episodes into a self-contained catalog that another person or model
can read cold:

```powershell
python -m a2r2.shadow.failure_catalog `
  --reports-dir data\reports `
  --out data\reports\a2r2_failure_catalog.md
```

The catalog groups near-identical failures by signature and records:

- goal and failing step,
- screen page/package and a visible-text sample,
- agent-reported failure detail and generic label,
- A2R2's would-decision and verify label,
- whether A2R2 predicted early, flagged coincidentally, or was blind.

## Candidate Rule Replay

Replay tests candidate rules offline before they touch live policy:

```powershell
python -m a2r2.shadow.replay `
  --reports-dir data\reports `
  --out data\reports\a2r2_replay_target_missing.md
```

For `target_missing`, replay measures two variants:

- `actionable_targets`: a named tap target is absent from
  `before_state.metadata.possible_targets`. This is the refined candidate because
  it sees icon/content-desc controls.
- `text_only_reference`: a named tap target is absent from visible text/XML. This
  reference explains why text-only matching can over-flag legitimate icon taps.

The key output is the tradeoff:

- failures predicted earlier than the failing step,
- failures caught at the failing action's gate,
- over-flag cost on successful steps,
- coverage of actionable target facts in the stored trace corpus.

If `missing_named_tap_steps` is high, the old corpus cannot validate the refined
rule. Re-collect shadow traces after the adapter preserves `possible_targets`.

## Agent-Reported Failure Capture

When a step fails, shadow classifies the agent's failure detail into a generic
category such as `target_missing`, `wrong_screen`, `input_no_effect`,
`manual_intervention_required`, `timeout`, or `agent_failure`.

This is recorded in the row and in `after_state.metadata`. It is deliberately
separate from `a2r2_prediction_matched_failure`: capturing the failure category
is not the same as predicting it early.

## Caveats

- Ground truth is still mostly per-episode. The "first failing step" uses the
  agent step's success flag as a proxy; semantic failures may need human review.
- Readiness needs the screen the agent saw. If XML and inline `xml_text` are both
  missing, A2R2 will conservatively report missing XML.
- A flag at the same step as the agent failure is weak evidence. Prioritize
  `lead > 0` and compare against the agent's own guard.
- MiniBench success does not prove real-app generalization. Use shadow batches to
  measure real over-flags and blind spots before shipping new rules.

## Suggested Experiment Loop

1. Run a small batch of tasks you can judge by watching.
2. Aggregate the shadow reports.
3. Build the failure catalog and identify repeated blind spots.
4. Test a candidate rule with replay instead of editing policy first.
5. Re-collect traces when replay shows the stored corpus lacks the needed facts.
6. Only then decide whether the candidate belongs in live policy.
