"""A2R2 MiniBench: offline, deterministic trap-app evidence harness.

MiniBench defines small, fully scripted Android-like trap scenarios with a
declared ground-truth expectation for each one, runs them through the real
``ReliabilityRuntime``, and scores how reliably the runtime intercepts or
diagnoses each known failure mode. Because every scenario carries ground truth,
the resulting scorecard reports real denominators instead of "insufficient
data", and it measures false interventions (over-blocking) on control cases.

MiniBench is not an Android GUI agent and does not touch a device. It is a
deterministic evidence generator for the A2R2 reliability runtime.
"""

from a2r2.bench.minibench import (
    EXPECT_ALLOW,
    EXPECT_GATE_BLOCK,
    EXPECT_VERIFY_FLAG,
    TrapScenario,
    TrapStep,
    build_scenarios,
    generate_scorecard,
    render_html,
    render_markdown,
    run_minibench,
)

__all__ = [
    "EXPECT_ALLOW",
    "EXPECT_GATE_BLOCK",
    "EXPECT_VERIFY_FLAG",
    "TrapScenario",
    "TrapStep",
    "build_scenarios",
    "generate_scorecard",
    "render_html",
    "render_markdown",
    "run_minibench",
]
