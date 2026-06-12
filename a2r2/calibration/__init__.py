"""Data-driven trust calibration for A2R2 rule labels.

Rules are static; trust in them is not. This package counts, per (label,
context), how often a label fired in episodes that actually failed versus
episodes that succeeded (ground truth from external judges such as MobileGym),
and assigns each cell a tier:

* trusted   -> high measured precision, enough samples: may justify enforcement
* gray_zone -> uncertain: escalate to an evidence provider (VLM arbiter)
* warn_only -> low measured precision: never enforce, log only
* insufficient -> not enough samples to say anything

No training, no gradients: pure counting, fully auditable, recomputed offline
after every imported batch.
"""

from a2r2.calibration.trust import build_trust_table, render_markdown

__all__ = ["build_trust_table", "render_markdown"]
