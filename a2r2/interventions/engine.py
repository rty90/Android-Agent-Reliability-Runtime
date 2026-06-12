"""Pure-logic intervention engine for live agent runs.

The engine never talks to a model or an environment; the host harness asks it
what to do with each proposed action and applies the answer:

    engine = InterventionEngine(goal)
    decision = engine.on_proposed(action_type, x=..., y=..., target=..., screen_key=..., claims_done=...)
    if decision.kind == "retry":   re-ask the model once (do not execute)
    if decision.kind == "note":    inject decision.note into the agent context
    if decision.kind == "none":    execute normally

Design rules: deterministic, stdlib-only, budget-limited (a wrong trigger costs
tokens, never the task), and free of task-specific logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

_POINTER_ACTIONS = {"tap", "click", "double_tap", "long_press"}
_CLAIM_ACTIONS = {"done", "complete", "finish", "success"}

REFLECT_NOTE = (
    "[reliability monitor] Your last {repeats} actions were identical on the same screen "
    "with no visible progress. If the task goal is already achieved, emit COMPLETE now; "
    "otherwise choose a different element or path."
)

VERIFY_NOTE = (
    "[reliability monitor] You are claiming the task is complete, but little screen change "
    "has been observed. Re-check the current screen against the goal once; emit COMPLETE "
    "again only if the evidence is visible."
)


@dataclass
class InterventionConfig:
    stall_repeat_threshold: int = 2      # fire reflection at the 2nd identical repeat (before loop-kill at 3)
    max_retries_per_episode: int = 3
    max_notes_per_episode: int = 2
    verify_early_complete: bool = True
    min_screens_before_complete: int = 2  # claiming done having seen <2 distinct screens is suspicious


@dataclass
class Intervention:
    kind: str  # "retry" | "note" | "none"
    note: str = ""
    reason: str = ""


@dataclass
class InterventionEngine:
    goal: str = ""
    config: InterventionConfig = field(default_factory=InterventionConfig)

    _retries_used: int = 0
    _notes_used: int = 0
    _verify_used: bool = False
    _last_key: Optional[Tuple[Any, ...]] = None
    _repeat_count: int = 0
    _screens_seen: List[str] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ API --
    def on_proposed(
        self,
        action_type: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        target: Optional[str] = None,
        screen_key: str = "",
        claims_done: bool = False,
    ) -> Intervention:
        normalized = str(action_type or "").lower()
        self._observe_screen(screen_key)

        # 1) Malformed pointer action -> retry (cannot help to execute garbage).
        if normalized in _POINTER_ACTIONS and x is None and y is None and not str(target or "").strip():
            if self._retries_used < self.config.max_retries_per_episode:
                self._retries_used += 1
                return self._emit("retry", reason="degenerate_pointer_action")

        # 2) Suspiciously early completion claim -> one verification nudge.
        if (claims_done or normalized in _CLAIM_ACTIONS) and self.config.verify_early_complete:
            if (
                not self._verify_used
                and len(set(self._screens_seen)) < self.config.min_screens_before_complete
            ):
                self._verify_used = True
                return self._emit("note", note=VERIFY_NOTE, reason="early_complete_claim")
            return self._emit("none")

        # 3) Same action repeated on the same screen -> reflection note.
        key = (screen_key, normalized, x, y, str(target or ""))
        if key == self._last_key:
            self._repeat_count += 1
        else:
            self._last_key = key
            self._repeat_count = 1
        if (
            self._repeat_count >= self.config.stall_repeat_threshold
            and self._notes_used < self.config.max_notes_per_episode
        ):
            self._notes_used += 1
            note = REFLECT_NOTE.format(repeats=self._repeat_count)
            self._repeat_count = 0  # avoid immediately re-firing on the next repeat
            return self._emit("note", note=note, reason="stall_repeat")

        return self._emit("none")

    def stats(self) -> dict:
        return {
            "retries_used": self._retries_used,
            "notes_used": self._notes_used,
            "verify_used": self._verify_used,
            "distinct_screens": len(set(self._screens_seen)),
            "events": list(self.events),
        }

    # ------------------------------------------------------------- internals --
    def _observe_screen(self, screen_key: str) -> None:
        if screen_key:
            self._screens_seen.append(str(screen_key))

    def _emit(self, kind: str, note: str = "", reason: str = "") -> Intervention:
        if kind != "none":
            self.events.append({"kind": kind, "reason": reason})
        return Intervention(kind=kind, note=note, reason=reason)
