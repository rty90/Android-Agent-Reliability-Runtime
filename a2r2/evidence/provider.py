"""Evidence providers: ask a VLM whether a flagged step is actually reasonable.

The provider answers two narrow, judge-style questions about one step:

* reasonable      -- does the proposed action make sense on this screen?
* goal_evidence   -- if the agent claims completion, does the screen support it?

Implementations:

* StaticEvidenceProvider   -- deterministic, for tests and dry runs.
* DashScopeEvidenceProvider -- OpenAI-compatible chat endpoint (DashScope
  compatible-mode by default) using only the standard library. API key comes
  from the environment (DASHSCOPE_API_KEY); it is never logged or stored.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-vl-plus"


@dataclass
class EvidenceVerdict:
    reasonable: Optional[bool] = None
    goal_evidence: Optional[bool] = None
    reason: str = ""
    raw: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reasonable": self.reasonable,
            "goal_evidence": self.goal_evidence,
            "reason": self.reason,
        }


def build_arbiter_prompt(goal: str, action_desc: str, claims_done: bool = False) -> str:
    questions = [
        "1) Is this action reasonable on the current screen for the stated goal?",
    ]
    if claims_done:
        questions.append("2) The agent claims the task is complete. Does the screen show evidence of completion?")
    return (
        "You are a reliability auditor for a mobile GUI agent. Judge one step only;"
        " do not plan or suggest alternatives.\n"
        "Goal: {0}\n"
        "Proposed action: {1}\n"
        "{2}\n"
        'Answer with strict JSON: {{"reasonable": true/false, "goal_evidence": true/false/null,'
        ' "reason": "<one short sentence>"}}'
    ).format(goal, action_desc, "\n".join(questions))


def parse_verdict_text(text: str) -> EvidenceVerdict:
    """Tolerantly extract the JSON verdict from a model response."""
    raw = str(text or "")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return EvidenceVerdict(reason="unparseable response", raw=raw[:500])
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return EvidenceVerdict(reason="invalid JSON in response", raw=raw[:500])

    def _tri(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes"}:
                return True
            if lowered in {"false", "no"}:
                return False
        return None

    return EvidenceVerdict(
        reasonable=_tri(payload.get("reasonable")),
        goal_evidence=_tri(payload.get("goal_evidence")),
        reason=str(payload.get("reason") or "")[:300],
        raw=raw[:500],
    )


class StaticEvidenceProvider:
    """Deterministic provider for tests/dry runs: replays queued verdicts."""

    def __init__(self, verdicts: Optional[List[EvidenceVerdict]] = None, default: Optional[EvidenceVerdict] = None) -> None:
        self._queue = list(verdicts or [])
        self._default = default or EvidenceVerdict(reasonable=True, reason="static default")
        self.calls: List[Dict[str, Any]] = []

    def assess(self, goal: str, action_desc: str, screenshot_path: Optional[str] = None, claims_done: bool = False) -> EvidenceVerdict:
        self.calls.append(
            {"goal": goal, "action_desc": action_desc, "screenshot_path": screenshot_path, "claims_done": claims_done}
        )
        return self._queue.pop(0) if self._queue else self._default


class DashScopeEvidenceProvider:
    """OpenAI-compatible chat-completions provider using stdlib HTTP only."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.model = model
        self.base_url = (base_url or os.environ.get("DASHSCOPE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY") or ""
        self.timeout_s = float(timeout_s)
        if not self._api_key:
            raise ValueError("DASHSCOPE_API_KEY is not set; the evidence provider needs an API key from the environment.")

    def assess(self, goal: str, action_desc: str, screenshot_path: Optional[str] = None, claims_done: bool = False) -> EvidenceVerdict:
        prompt = build_arbiter_prompt(goal, action_desc, claims_done=claims_done)
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        image_url = _image_data_url(screenshot_path)
        if image_url:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 300,
        }
        request = urllib.request.Request(
            "{0}/chat/completions".format(self.base_url),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {0}".format(self._api_key),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception as exc:  # network/HTTP errors become a null verdict, never a crash
            return EvidenceVerdict(reason="provider_error: {0}".format(str(exc)[:200]))
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return EvidenceVerdict(reason="unexpected response shape", raw=json.dumps(payload)[:500])
        return parse_verdict_text(text if isinstance(text, str) else json.dumps(text))


def _image_data_url(screenshot_path: Optional[str]) -> Optional[str]:
    if not screenshot_path:
        return None
    path = Path(screenshot_path)
    if not path.exists() or not path.is_file():
        return None
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:image/{0};base64,{1}".format(suffix, encoded)
