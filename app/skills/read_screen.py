from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Mapping, Optional

from app.affordances import build_affordance_graph
from app.demo_config import DemoMessageConfig
from app.overlay_detector import detect_system_overlay
from app.skills.base import BaseSkill, SkillContext
from app.skills.targeting import normalize_text
from app.ui_facts import find_search_surface


def _parse_bounds(bounds: str) -> Optional[Dict[str, int]]:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not match:
        return None
    left, top, right, bottom = [int(value) for value in match.groups()]
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "center_x": (left + right) // 2,
        "center_y": (top + bottom) // 2,
    }


def _build_candidate(node: ET.Element) -> Optional[Dict[str, Any]]:
    text_value = (node.attrib.get("text") or "").strip()
    content_desc = (node.attrib.get("content-desc") or "").strip()
    resource_id = (node.attrib.get("resource-id") or "").strip()
    hint = (node.attrib.get("hint") or "").strip()
    label = text_value or content_desc or resource_id
    if not label:
        return None

    source = "text"
    confidence = 0.95
    if not text_value and content_desc:
        source = "content_desc"
        confidence = 0.85
    elif not text_value and not content_desc and resource_id:
        source = "resource_id"
        confidence = 0.70

    return {
        "label": label,
        "bounds": _parse_bounds(node.attrib.get("bounds", "")),
        "resource_id": resource_id,
        "content_desc": content_desc,
        "class_name": node.attrib.get("class", ""),
        "clickable": node.attrib.get("clickable", "false") == "true",
        "focusable": node.attrib.get("focusable", "false") == "true",
        "focused": node.attrib.get("focused", "false") == "true",
        "enabled": node.attrib.get("enabled", "true") == "true",
        "hint": hint,
        "confidence": confidence,
        "source": source,
    }


def detect_page_name(
    visible_text, focus: str, runtime_config: Optional[DemoMessageConfig]
) -> Optional[str]:
    if not runtime_config:
        return None

    corpus = normalize_text(" ".join(visible_text) + " " + (focus or ""))
    best_name = None
    best_score = 0
    for page_name, profile in runtime_config.page_profiles.items():
        score = 0
        for app_hint in getattr(profile, "app_hints", ()):
            if normalize_text(app_hint) in corpus:
                score += 2
        for keyword in profile.keywords:
            if normalize_text(keyword) in corpus:
                score += 1
        if score > best_score:
            best_name = page_name
            best_score = score

    if best_score <= 0:
        return None
    return best_name


def _first_package(root: ET.Element) -> str:
    for node in root.iter("node"):
        package_name = (node.attrib.get("package") or "").strip()
        if package_name:
            return package_name
    return ""


def _normalize_url(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    if " " in value or "." not in value:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://{0}".format(value)
    parsed = urlparse(value)
    if not parsed.netloc:
        return ""
    return parsed.geturl()


def _extract_browser_url(root: ET.Element) -> str:
    for node in root.iter("node"):
        resource_id = (node.attrib.get("resource-id") or "").strip()
        if resource_id != "com.android.chrome:id/url_bar":
            continue
        url = _normalize_url(node.attrib.get("text") or "")
        if url:
            return url
    return ""


def _domain_from_url(url: str) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower()


def _package_from_focus(focus: str) -> str:
    match = re.search(r"\s(u\d+\s+)?([A-Za-z0-9_.]+)/", focus or "")
    if match:
        return match.group(2)
    return ""


def _detect_browser_page(
    visible_text,
    possible_targets,
    current_package: str,
    current_url: str,
) -> Optional[str]:
    if current_package != "com.android.chrome":
        return None
    domain = _domain_from_url(current_url)
    url_lower = current_url.lower()
    if "/search" in url_lower and ("keyword=" in url_lower or "q=" in url_lower):
        if "bilibili" in domain:
            return "bilibili_search_results"
        return "browser_site_search_results"
    if domain:
        if "bilibili" in domain:
            return "bilibili_site"
        return "browser_site"
    corpus = normalize_text(" ".join(str(item or "") for item in visible_text))
    if any("url_bar" in str((target or {}).get("resource_id") or "").lower() for target in possible_targets):
        if "search google or type url" in corpus or "trending searches" in corpus:
            return "browser_search"
    return "browser_page"


def _should_probe_system_overlay(summary: Dict[str, Any]) -> bool:
    if find_search_surface(summary):
        return True
    for candidate in summary.get("possible_targets", []):
        if not isinstance(candidate, dict):
            continue
        class_name = normalize_text(candidate.get("class_name") or "")
        if "edittext" not in class_name:
            continue
        if candidate.get("focused"):
            return True
        combined = normalize_text(
            " ".join(
                str(candidate.get(key) or "")
                for key in ("label", "resource_id", "content_desc", "hint")
            )
        )
        if any(marker in combined for marker in ("search", "url", "query", "address")):
            return True
    return False


def _read_system_overlay(adb, summary: Dict[str, Any]) -> Dict[str, Any]:
    if not _should_probe_system_overlay(summary):
        return detect_system_overlay(summary)
    if not hasattr(adb, "shell"):
        return detect_system_overlay(summary)
    try:
        window_dump = adb.shell("dumpsys window", check=False, timeout=3)
    except Exception as exc:
        return {
            "present": False,
            "scope": "system",
            "type": "probe_failed",
            "blocks_input": False,
            "confidence": 0.0,
            "recommended_recovery": "none",
            "evidence": ["dumpsys window failed: {0}".format(type(exc).__name__)],
        }
    try:
        input_method_dump = adb.shell("dumpsys input_method", check=False, timeout=3)
    except Exception:
        input_method_dump = ""
    return detect_system_overlay(summary, window_dump=window_dump, input_method_dump=input_method_dump)


def read_screen_summary(
    adb,
    xml_path: str,
    runtime_config: Optional[DemoMessageConfig] = None,
) -> Dict[str, Any]:
    try:
        dump_path = adb.dump_ui_xml(xml_path)
    except Exception as exc:
        focus = adb.get_current_focus()
        current_package = _package_from_focus(focus)
        summary = {
            "app": current_package or "unknown",
            "current_package": current_package,
            "current_url": None,
            "current_domain": None,
            "page": "unobservable_screen",
            "visible_text": [],
            "possible_targets": [],
            "possible_target_total_count": 0,
            "possible_targets_truncated": False,
            "focus": focus,
            "ui_dump_path": str(xml_path),
            "ui_dump_error": "{0}: {1}".format(type(exc).__name__, exc),
            "xml_text": "",
        }
        summary["system_overlay"] = _read_system_overlay(adb, summary)
        summary["affordance_graph"] = build_affordance_graph(summary)
        return summary

    raw_xml = Path(str(dump_path)).read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ET.ElementTree(ET.fromstring(raw_xml))
    except ET.ParseError as exc:
        focus = adb.get_current_focus()
        current_package = _package_from_focus(focus)
        summary = {
            "app": current_package or "unknown",
            "current_package": current_package,
            "current_url": None,
            "current_domain": None,
            "page": "unobservable_screen",
            "visible_text": [],
            "possible_targets": [],
            "possible_target_total_count": 0,
            "possible_targets_truncated": False,
            "focus": focus,
            "ui_dump_path": str(dump_path),
            "ui_dump_error": "ParseError: {0}".format(exc),
            "xml_text": raw_xml[:2000],
        }
        summary["system_overlay"] = _read_system_overlay(adb, summary)
        summary["affordance_graph"] = build_affordance_graph(summary)
        return summary
    root = tree.getroot()
    current_package = _first_package(root)
    current_url = _extract_browser_url(root)
    current_domain = _domain_from_url(current_url)

    visible_text = []
    possible_targets = []
    for node in root.iter("node"):
        candidate = _build_candidate(node)
        if not candidate:
            continue
        candidate["target_id"] = "n{0:03d}".format(len(possible_targets) + 1)
        visible_text.append(candidate["label"])
        possible_targets.append(candidate)

    focus = adb.get_current_focus()
    app_name = current_package or "unknown"
    if app_name == "unknown" and "/" in focus:
        app_name = _package_from_focus(focus) or app_name

    browser_page = _detect_browser_page(visible_text, possible_targets, current_package, current_url)
    detected_page = detect_page_name(visible_text, focus, runtime_config)
    target_limit = 50
    summary = {
        "app": app_name,
        "current_package": current_package,
        "current_url": current_url,
        "current_domain": current_domain,
        "page": browser_page or detected_page or Path(str(dump_path)).stem,
        "visible_text": visible_text[:target_limit],
        "possible_targets": possible_targets[:target_limit],
        "possible_target_total_count": len(possible_targets),
        "possible_targets_truncated": len(possible_targets) > target_limit,
        "focus": focus,
        "ui_dump_path": str(dump_path),
    }
    summary["system_overlay"] = _read_system_overlay(adb, summary)
    summary["affordance_graph"] = build_affordance_graph(summary)
    return summary


class ReadScreenSkill(BaseSkill):
    name = "read_screen"

    def execute(self, args: Mapping[str, Any], context: SkillContext) -> Dict[str, Any]:
        prefix = args.get("prefix", "read_screen")
        dump_path = Path("data/tmp")
        dump_path.mkdir(parents=True, exist_ok=True)
        xml_path = dump_path / "{0}.xml".format(prefix)
        summary = read_screen_summary(
            context.adb,
            str(xml_path),
            runtime_config=context.runtime_config,
        )
        context.state.update_screen_summary(summary)
        return self.result(success=True, detail="Screen summary refreshed.", data=summary)
