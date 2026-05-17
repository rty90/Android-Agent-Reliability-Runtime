# A2R2 Project Scope

A2R2 is a process-level reliability middleware for Android GUI agents.

It wraps an existing agent as a black box. The external agent proposes actions;
A2R2 gates those actions before execution, verifies progress afterward, labels
failures, and records traces that make failures reproducible.

## A2R2 Does

- Gate proposed actions before execution.
- Verify real progress after execution.
- Diagnose failures using structured labels.
- Export reproducible step-level traces.
- Produce reliability scorecards.

## A2R2 Does Not Do

- Plan tasks.
- Replan after failure.
- Decompose goals.
- Replace AndroidWorld.
- Optimize final task success directly.
- Train or fine-tune VLMs in v0.1.
- Encode app-specific business logic.

## Boundary

Existing agent code, including proposers, planners, and executors, can live
outside A2R2. The runtime's authority is limited to process reliability:
observe, gate, verify, diagnose, and record.

The existing `app/reasoning_orchestrator.py` should be treated as a proposer or
legacy agent component. It must not become the A2R2 runtime decision authority.
