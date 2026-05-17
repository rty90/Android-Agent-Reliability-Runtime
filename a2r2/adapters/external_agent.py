from __future__ import annotations

from typing import Iterable, List

from a2r2.types import Observation, ProposedAction


class DummyExternalAgent:
    """Small proposer used to demonstrate the middleware boundary."""

    def __init__(self, actions: Iterable[ProposedAction]) -> None:
        self._actions: List[ProposedAction] = list(actions)
        self._index = 0

    def propose(self, goal: str, observation: Observation) -> ProposedAction:
        if self._index >= len(self._actions):
            return ProposedAction(action_type="done", raw={"agent_claimed_success": True})
        action = self._actions[self._index]
        self._index += 1
        return action
