# A2R2 Shadow Trace Harness (v0.2)

Shadow mode runs A2R2 **alongside a real agent without interfering**. The agent
executes normally; for every step A2R2 records what it *would* have decided
(allow / wait / block / manual_handoff) plus its progress verification, but it
never blocks. Because it cannot break automation, it can be pointed at a real
agent to answer the project's make-or-break question:

> Before the agent's task ultimately failed, did A2R2 flag a relevant problem
> (no_progress / non_ready / unsafe / stuck / false_success) **earlier than the
> failure** — and earlier than the agent's **own built-in guards**?

This is the step that turns A2R2 from a self-proving demo (MiniBench) into real
evidence. It deliberately does **not** do enforcement: "who takes over after a
block?" is left unanswered on purpose — v0.2 only proves A2R2 *sees* trouble.

## Design

```
real agent runs normally
        |
        v   (Executor.step_observer, read-only)
A2R2 observes each proposed action: would_decide + verify, never blocks
        |
        v
trace.v1 episode  +  shadow_report.{json,md,html}
```

Pieces:

- `RuntimeConfig.enforce: bool = False` — shadow (default) vs. a future enforce
  mode. The runtime never executes actions either way; the flag documents intent
  and is stamped into traces.
- `Executor.step_observer` (in `app/executor.py`) — an optional read-only
  callback fired once per executed step. When `None` (default) behaviour is
  unchanged; exceptions from an observer are swallowed so it can never break the
  agent.
- `a2r2/adapters/legacy_app.py` — the only place that knows the legacy screen
  summary + (skill, args) format, mapping them to generic `Observation` /
  `ProposedAction`. No package-specific logic leaks into the runtime/policies.
- `a2r2/shadow/harness.py` — `ShadowSession` consumes step events, asks the
  runtime for would-decisions, records trace.v1, and computes the report.

## What the report answers

`shadow_report.v1` headline fields:

- `a2r2_prediction_matched_failure` — episode failed **and** A2R2 raised a
  concern at or before the first failing step.
- `timeline.a2r2_lead_over_failure` — steps between A2R2's first flag and the
  first failing step (positive = earlier; this is the real prize).
- `timeline.a2r2_lead_over_agent_guard` — steps A2R2 led the agent's *own*
  guards (manual_intervention / failures). Positive means A2R2 added early
  warning beyond what the agent already does.
- `over_flag_count` — steps A2R2 would have blocked that actually succeeded on a
  successful episode. This is the **real-world false-positive** measurement that
  MiniBench cannot give.
- `avg_a2r2_overhead_ms_per_action` — real per-action gate+verify cost (this is
  why we chose the in-process hook over post-hoc replay).

## How to run (needs a device/emulator)

```powershell
cd "F:\mobile agents"
python scripts\a2r2_shadow_agent_run.py `
  --task "open settings and inspect current page" `
  --task-type guided_ui_task `
  --max-steps 10 `
  --trace-dir data\traces\real_agent_shadow
start data\reports\a2r2_shadow_<timestamp>\shadow_report.html
```

The emitted episodes are standard `trace.v1`, so the viewer and scorecard
consume them too:

```powershell
python -m a2r2.reports.trace_viewer --trace-dir data\traces\real_agent_shadow --out data\reports\shadow_viewer.html
```

## Honest caveats

- **Ground truth is per-episode** (`final_success` from the agent's result). The
  "first failing step" uses each step's own success flag, which is a proxy; for
  semantic task failure you may still need to eyeball the trace. Start with tasks
  whose success you can judge by watching.
- **Readiness needs the screen the agent saw.** At observe time A2R2 reads XML
  from the live `ui_dump_path` (or inline `xml_text`). If neither is present it
  reports `non_ready_action` (missing XML) — expected, not a bug.
- **The bar is lead time, not "flagged at all."** A flag at the same step the
  agent already failed is weak. Watch `a2r2_lead_over_failure` and
  `a2r2_lead_over_agent_guard`.
- 100% on MiniBench says nothing about real apps. Treat the first shadow runs as
  exploratory: collect 20–50 episodes, then decide whether the rules need work
  (do **not** assume they're good because MiniBench is green).

## Suggested first experiment

1. Run 3 tasks you can hand-judge; confirm the plumbing + eyeball the flags.
2. Scale to 20–50 episodes into one `--trace-dir`.
3. Read `a2r2_lead_over_failure` / `over_flag_count` across runs.
4. Only then decide whether to tighten policies or add an optional VLM evidence
   layer. See [minibench.md](minibench.md) and [architecture.md](architecture.md).
