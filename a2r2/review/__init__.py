"""Offline retrospection over recorded episodes.

The deterministic judge owns episode-level truth; the reviewer (a VLM via an
EvidenceProvider) owns what the judge cannot answer: step-level attribution
("which step went wrong and why") and natural-language failure summaries. The
reviewer must pass an exam against human-verified cases before its labels are
trusted for training data.
"""

from a2r2.review.retrospect import build_review_plan, render_markdown, run_review, score_exam

__all__ = ["build_review_plan", "render_markdown", "run_review", "score_exam"]
