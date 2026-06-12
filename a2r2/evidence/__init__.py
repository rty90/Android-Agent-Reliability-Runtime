"""Optional VLM evidence providers for A2R2.

Rules are the cheap sentinels; an evidence provider is the arbiter that is only
consulted for gray-zone flags (online) or post-episode review (offline). It is
strictly optional: the A2R2 core never requires it, never imports vendor SDKs
(stdlib HTTP only), and never reads API keys from anywhere but the environment.
"""

from a2r2.evidence.provider import (
    DashScopeEvidenceProvider,
    EvidenceVerdict,
    StaticEvidenceProvider,
    build_arbiter_prompt,
    parse_verdict_text,
)

__all__ = [
    "DashScopeEvidenceProvider",
    "EvidenceVerdict",
    "StaticEvidenceProvider",
    "build_arbiter_prompt",
    "parse_verdict_text",
]
