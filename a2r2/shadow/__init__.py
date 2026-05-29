"""A2R2 shadow-mode harness.

Shadow mode runs A2R2 *alongside* a real agent without interfering: the agent
executes normally, and for every step A2R2 records what it *would* have decided
(allow / wait / block / manual_handoff) plus its progress verification. Because
A2R2 never blocks in shadow mode, it cannot break automation -- so it can be run
against a real agent to answer one question:

    Before the agent's task ultimately failed, did A2R2 flag a relevant problem
    (no_progress / non_ready / unsafe / stuck / false_success) earlier than the
    failure -- and earlier than the agent's own built-in guards?

The session also measures real per-action A2R2 overhead and the rate at which
A2R2 would have wrongly intervened on steps that actually succeeded.
"""

from a2r2.shadow.harness import ShadowSession, render_html, render_markdown

__all__ = ["ShadowSession", "render_html", "render_markdown"]
