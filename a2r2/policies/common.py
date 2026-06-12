from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from xml.etree import ElementTree

from a2r2.types import Observation, ProposedAction, RuntimeDecision


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def file_hash(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    target = Path(path)
    if not target.exists() or not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def read_text(path: Optional[str], limit: int = 512000) -> str:
    if not path:
        return ""
    target = Path(path)
    if not target.exists() or not target.is_file():
        return ""
    try:
        return target.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def observation_xml(observation: Observation) -> str:
    metadata = observation.metadata or {}
    for key in ("xml_text", "xml", "ui_xml"):
        if metadata.get(key):
            return str(metadata.get(key) or "")
    return read_text(observation.xml_path)


def observation_text(observation: Observation) -> str:
    metadata = observation.metadata or {}
    parts: List[str] = []
    for key in ("text", "screen_text", "summary"):
        if metadata.get(key):
            parts.append(str(metadata.get(key)))
    visible = metadata.get("visible_text")
    if isinstance(visible, (list, tuple, set)):
        parts.extend(str(item) for item in visible)
    elif visible:
        parts.append(str(visible))
    xml = observation_xml(observation)
    if xml:
        parts.append(xml)
    for item in (observation.activity, observation.package, observation.ui_tree_hash):
        if item:
            parts.append(str(item))
    return " ".join(parts)


def observation_hash(observation: Observation) -> str:
    if observation.ui_tree_hash:
        return str(observation.ui_tree_hash)
    xml_hash = stable_hash(observation_xml(observation)) if observation_xml(observation) else None
    if xml_hash:
        return xml_hash
    metadata = dict(observation.metadata or {})
    metadata.pop("timestamp", None)
    return stable_hash(
        {
            "activity": observation.activity,
            "package": observation.package,
            "text": metadata.get("visible_text") or metadata.get("text") or metadata.get("summary"),
            "screenshot_path": observation.screenshot_path,
        }
    )


def action_fingerprint(action: ProposedAction) -> str:
    raw = dict(action.raw) if isinstance(action.raw, Mapping) else {"value": action.raw}
    for secret_key in ("api_key", "token", "password", "secret"):
        raw.pop(secret_key, None)
    return stable_hash(
        {
            "action_type": action.action_type,
            "x": action.x,
            "y": action.y,
            "target_text": action.target_text,
            "target_resource_id": action.target_resource_id,
            "raw": raw,
        }
    )


# Free-text agent reasoning fields are not part of the action's risk surface;
# a high-risk word in the model's *thought* must not look like a risky action.
_NON_ACTION_RAW_KEYS = ("thought", "summary", "reasoning", "rationale", "observation", "notes")


def action_text(action: ProposedAction) -> str:
    raw = action.raw if isinstance(action.raw, Mapping) else {}
    if raw:
        raw = {key: value for key, value in raw.items() if str(key).lower() not in _NON_ACTION_RAW_KEYS}
    return " ".join(
        str(item or "")
        for item in (
            action.action_type,
            action.target_text,
            action.target_resource_id,
            json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str) if raw else "",
        )
    ).lower()


def parse_xml_nodes(xml_text: str) -> List[Dict[str, str]]:
    if not xml_text.strip():
        return []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    nodes: List[Dict[str, str]] = []
    for element in root.iter():
        attrs = {str(key).lower(): str(value) for key, value in element.attrib.items()}
        attrs["tag"] = str(element.tag)
        nodes.append(attrs)
    return nodes


def node_matches_action(node: Mapping[str, str], action: ProposedAction) -> bool:
    wanted_id = str(action.target_resource_id or "").strip().lower()
    wanted_text = str(action.target_text or "").strip().lower()
    if not wanted_id and not wanted_text:
        return False
    haystack = " ".join(
        str(node.get(key) or "").lower()
        for key in ("resource-id", "text", "content-desc", "hint", "tag")
    )
    if wanted_id and wanted_id.lower() in haystack:
        return True
    if wanted_text and wanted_text.lower() in haystack:
        return True
    return False


def history_dicts(history: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in list(history or []):
        if hasattr(item, "to_dict"):
            result.append(item.to_dict())
        elif isinstance(item, Mapping):
            result.append(dict(item))
    return result


def allow_decision(reason: str = "The proposed action passed A2R2 v0.1 gates.") -> RuntimeDecision:
    return RuntimeDecision(
        decision="allow",
        allowed=True,
        reason=reason,
        evidence=[],
        confidence=0.90,
        policy_name="ReliabilityRuntime",
    )
