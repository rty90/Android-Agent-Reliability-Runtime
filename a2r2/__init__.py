"""A2R2: Android Agent Reliability Runtime.

A2R2 is a process-level reliability middleware for Android GUI agents. It does
not plan tasks or replace an agent; it gates proposed actions, verifies
progress, and records reproducible traces.
"""

from a2r2.runtime import ReliabilityRuntime
from a2r2.types import (
    EpisodeSummary,
    Observation,
    ProgressVerification,
    ProposedAction,
    RuntimeConfig,
    RuntimeDecision,
    StepTrace,
)

__all__ = [
    "EpisodeSummary",
    "Observation",
    "ProgressVerification",
    "ProposedAction",
    "ReliabilityRuntime",
    "RuntimeConfig",
    "RuntimeDecision",
    "StepTrace",
]
