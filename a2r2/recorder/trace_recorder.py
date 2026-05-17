from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from a2r2.types import EpisodeSummary, StepTrace


class TraceRecorder:
    """Agent-agnostic trace.v1 JSONL recorder."""

    def __init__(
        self,
        root_dir: str = "data/traces",
        episode_id: Optional[str] = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.episode_id = episode_id or "episode_{0}".format(uuid.uuid4().hex[:12])
        self.episode_dir = self.root_dir / self.episode_id
        self.artifacts_dir = self.episode_dir / "artifacts"
        self.steps_path = self.episode_dir / "steps.jsonl"
        self.summary_path = self.episode_dir / "episode_summary.json"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._steps_written = 0

    def record_step(self, step: StepTrace) -> Dict[str, Any]:
        payload = step.to_dict() if hasattr(step, "to_dict") else dict(step)
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        with self.steps_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        self._steps_written += 1
        return payload

    def write_summary(self, summary: EpisodeSummary) -> Dict[str, Any]:
        payload = summary.to_dict() if hasattr(summary, "to_dict") else dict(summary)
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.summary_path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.summary_path)
        return payload

    def load_steps(self) -> List[Dict[str, Any]]:
        if not self.steps_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with self.steps_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
        return records

    def export_trace(self) -> Dict[str, str]:
        return {
            "episode_id": self.episode_id,
            "episode_dir": str(self.episode_dir),
            "steps_path": str(self.steps_path),
            "summary_path": str(self.summary_path),
            "artifacts_dir": str(self.artifacts_dir),
        }
