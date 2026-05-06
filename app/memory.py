from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.lesson_policy import apply_lesson_safety
from app.task_types import extract_message_body


class SQLiteMemory(object):
    """Minimal verified-memory storage for the MVP."""

    def __init__(self, db_path: str = "data/memory.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _managed_connect(self):
        return closing(self._connect())

    def _initialize(self) -> None:
        with self._managed_connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    preference_key TEXT NOT NULL UNIQUE,
                    preference_value TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS successful_trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    app TEXT,
                    intent TEXT NOT NULL,
                    steps_summary TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS failure_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    app TEXT,
                    intent TEXT NOT NULL,
                    steps_summary TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    confidence REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_name TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    source_app TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    last_seen_timestamp TEXT NOT NULL,
                    UNIQUE(contact_name, phone_number)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ui_shortcuts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    app TEXT NOT NULL DEFAULT '',
                    page TEXT NOT NULL,
                    intent_key TEXT NOT NULL,
                    skill TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    target_label TEXT NOT NULL DEFAULT '',
                    target_key TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_timestamp TEXT NOT NULL,
                    UNIQUE(task_type, app, page, intent_key, skill, target_label, target_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS interaction_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    app TEXT NOT NULL DEFAULT '',
                    page TEXT NOT NULL DEFAULT '',
                    state_tags_json TEXT NOT NULL,
                    action_skill TEXT NOT NULL,
                    action_template_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_timestamp TEXT NOT NULL,
                    UNIQUE(task_type, app, page, state_tags_json, action_skill, action_template_json)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_interventions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    app TEXT,
                    page TEXT,
                    intent TEXT NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    resolution_label TEXT NOT NULL,
                    before_tags_json TEXT NOT NULL,
                    after_tags_json TEXT NOT NULL,
                    before_summary_json TEXT NOT NULL,
                    after_summary_json TEXT NOT NULL,
                    recent_actions_json TEXT NOT NULL,
                    before_screenshot_path TEXT,
                    after_screenshot_path TEXT,
                    before_ui_dump_path TEXT,
                    after_ui_dump_path TEXT,
                    user_note TEXT,
                    timestamp TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS manual_reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT,
                    app TEXT,
                    page TEXT,
                    intent TEXT NOT NULL,
                    trigger_reason TEXT NOT NULL,
                    resolution_label TEXT NOT NULL,
                    failed_skill TEXT,
                    failed_args_json TEXT NOT NULL,
                    agent_actions_json TEXT NOT NULL,
                    before_tags_json TEXT NOT NULL,
                    after_tags_json TEXT NOT NULL,
                    reflection_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS learned_procedures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    app TEXT NOT NULL DEFAULT '',
                    intent_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    procedure_json TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    verified INTEGER NOT NULL DEFAULT 0,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_timestamp TEXT NOT NULL,
                    UNIQUE(task_type, app, intent_key, title)
                )
                """
            )
            self._ensure_column(conn, "successful_trajectories", "task_type", "TEXT")
            self._ensure_column(conn, "failure_patterns", "task_type", "TEXT")
            conn.commit()

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_type: str,
    ) -> None:
        rows = conn.execute("PRAGMA table_info({0})".format(table_name)).fetchall()
        existing = [row[1] for row in rows]
        if column_name not in existing:
            conn.execute(
                "ALTER TABLE {0} ADD COLUMN {1} {2}".format(
                    table_name, column_name, column_type
                )
            )

    def save_user_preference(
        self, preference_key: str, preference_value: str, confidence: float = 1.0
    ) -> None:
        timestamp = self._utc_now_iso()
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (preference_key, preference_value, confidence, timestamp)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(preference_key) DO UPDATE SET
                    preference_value = excluded.preference_value,
                    confidence = excluded.confidence,
                    timestamp = excluded.timestamp
                """,
                (preference_key, preference_value, confidence, timestamp),
            )
            conn.commit()

    @staticmethod
    def _normalize_intent_key(intent: str) -> str:
        normalized = re.sub(r"\s+", " ", (intent or "").strip().lower())
        return normalized[:240]

    @staticmethod
    def _normalize_app_key(app: Optional[str]) -> str:
        candidate = str(app or "").strip()
        if not candidate:
            return ""
        if any(marker in candidate for marker in (" ", "=", "mCurrentFocus", "Window{")):
            return ""
        return candidate

    @staticmethod
    def _goal_looks_search(goal: str) -> bool:
        normalized = str(goal or "").strip().lower()
        return any(
            marker in normalized
            for marker in (
                "search ",
                "search for",
                "find ",
                "find videos",
                "find video",
                "look up",
                "look for",
                "browse ",
            )
        )

    @staticmethod
    def _goal_looks_video_search(goal: str) -> bool:
        normalized = str(goal or "").strip().lower()
        return "video" in normalized or "videos" in normalized

    @staticmethod
    def _known_search_terms(goal: str) -> List[str]:
        normalized = str(goal or "").strip().lower()
        aliases = {
            "bilibili": ("bilibili", "b站"),
            "youtube": ("youtube",),
            "wikipedia": ("wikipedia",),
            "amazon": ("amazon",),
            "github": ("github",),
            "reddit": ("reddit",),
            "facebook": ("facebook",),
        }
        matches: List[str] = []
        for canonical, variants in aliases.items():
            if any(variant in normalized for variant in variants):
                matches.append(canonical)
        return matches

    @classmethod
    def _extract_search_query(cls, goal: str) -> str:
        quoted = extract_message_body(goal)
        if quoted:
            return quoted

        normalized = str(goal or "").strip().lower()
        patterns = (
            r"(?:find|look\s+for|look\s+up|search(?:\s+for)?)(?:\s+videos?)?(?:\s+about|\s+for)?\s+(.+)",
            r"(?:videos?\s+about)\s+(.+)",
        )
        query = ""
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                query = match.group(1).strip(" .,!?:;")
                break
        if not query:
            return ""

        query = re.sub(r"^(on|in|with)\s+", "", query).strip()
        query = re.sub(r"\s+(on|in)\s+(chrome|browser|web)\b.*$", "", query).strip()
        if not query:
            return ""

        known_terms = cls._known_search_terms(goal)
        if known_terms and not any(term in query for term in known_terms):
            query = "{0} {1}".format(known_terms[0], query).strip()
        return query[:120]

    @staticmethod
    def _candidate_text(candidate: Dict[str, Any]) -> str:
        return " ".join(
            str(candidate.get(key) or "").strip().lower()
            for key in ("label", "resource_id", "content_desc", "class_name", "hint")
        )

    @classmethod
    def _find_best_input_candidate(
        cls,
        screen_summary: Dict[str, Any],
        focused_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        best: Optional[Dict[str, Any]] = None
        best_score = -1
        for candidate in screen_summary.get("possible_targets", []):
            if not isinstance(candidate, dict):
                continue
            class_name = str(candidate.get("class_name") or "").lower()
            if "edittext" not in class_name:
                continue
            if focused_only and not bool(candidate.get("focused")):
                continue
            score = 0
            if bool(candidate.get("focused")):
                score += 4
            if bool(candidate.get("clickable")):
                score += 1
            combined = cls._candidate_text(candidate)
            if any(marker in combined for marker in ("search", "url", "query", "address", "find")):
                score += 2
            if score > best_score:
                best = candidate
                best_score = score
        return best

    @classmethod
    def build_interaction_tags(
        cls,
        screen_summary: Dict[str, Any],
        goal: str = "",
        recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> List[str]:
        tags: List[str] = []
        page = str((screen_summary or {}).get("page") or "").strip().lower()
        if page:
            tags.append("page:{0}".format(page))
        current_domain = str((screen_summary or {}).get("current_domain") or "").strip().lower()
        if current_domain:
            tags.append("domain:{0}".format(current_domain))
        current_url = str((screen_summary or {}).get("current_url") or "").strip().lower()
        if "/search" in current_url and ("keyword=" in current_url or "q=" in current_url):
            tags.append("state:site_search_results")
        if "bilibili" in current_domain:
            tags.append("site:bilibili")

        candidates = list((screen_summary or {}).get("possible_targets", []))
        if any("edittext" in str((candidate or {}).get("class_name") or "").lower() for candidate in candidates):
            tags.append("state:has_input")

        focused_input = cls._find_best_input_candidate(screen_summary, focused_only=True)
        if focused_input:
            tags.append("state:focused_input")

        search_input = cls._find_best_input_candidate(screen_summary, focused_only=False)
        if search_input and any(
            marker in cls._candidate_text(search_input) for marker in ("search", "url", "query", "address", "find")
        ):
            tags.append("state:search_input")
            if any(marker in cls._candidate_text(search_input) for marker in ("url_bar", "location_bar")):
                tags.append("state:browser_search_surface")

        text_corpus = " ".join(str(item or "").strip().lower() for item in screen_summary.get("visible_text", []))
        if any(marker in text_corpus for marker in ("try out your stylus", "write here", "use your stylus")) and sum(
            1 for marker in ("cancel", "next", "reset", "delete", "select", "insert") if marker in text_corpus
        ) >= 2:
            tags.append("state:input_blocked_overlay")

        clickable_buttons = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and bool(candidate.get("clickable"))
        ]
        if len(clickable_buttons) >= 2 and any(
            "button" in str((candidate or {}).get("class_name") or "").lower() for candidate in clickable_buttons
        ):
            tags.append("state:dialog_like")

        if cls._goal_looks_search(goal):
            tags.append("intent:search")
        if cls._goal_looks_video_search(goal):
            tags.append("intent:video_search")
        if extract_message_body(goal):
            tags.append("intent:quoted_text")
        if cls._extract_search_query(goal):
            tags.append("intent:has_search_query")

        repeated = cls._recent_repeated_tap_target(recent_actions or [])
        if repeated:
            tags.append("state:repeated_tap")
            if any(marker in repeated for marker in ("search", "url", "query", "address", "find")):
                tags.append("state:repeated_search_tap")

        return sorted(set(tags))

    @staticmethod
    def _recent_repeated_tap_target(recent_actions: Sequence[Dict[str, Any]]) -> str:
        taps = [
            action for action in (recent_actions or [])
            if str((action or {}).get("action") or "").strip() == "tap" and bool((action or {}).get("success"))
        ]
        if len(taps) < 2:
            return ""
        last = taps[-1]
        prev = taps[-2]
        last_target = str(last.get("detail") or last.get("target") or "").strip().lower()
        prev_target = str(prev.get("detail") or prev.get("target") or "").strip().lower()
        if last_target and last_target == prev_target:
            return last_target
        return ""

    @classmethod
    def _generalize_action_template(
        cls,
        goal: str,
        screen_summary: Dict[str, Any],
        skill: str,
        args: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if skill == "type_text":
            template: Dict[str, Any] = {}
            target_id = str(args.get("target_id") or "").strip()
            if target_id:
                target = None
                for candidate in screen_summary.get("possible_targets", []):
                    if isinstance(candidate, dict) and str(candidate.get("target_id") or "").strip() == target_id:
                        target = candidate
                        break
                if target and bool(target.get("focused")):
                    template["target_strategy"] = "focused_input"
                elif target and any(
                    marker in cls._candidate_text(target) for marker in ("search", "url", "query", "address", "find")
                ):
                    template["target_strategy"] = "search_input"
            if cls._goal_looks_search(goal) and cls._extract_search_query(goal):
                template["text_source"] = "search_query"
                template["press_enter"] = True
                template["dismiss_overlays_first"] = True
            else:
                quoted = extract_message_body(goal)
                if quoted and str(args.get("text") or "") == quoted:
                    template["text_source"] = "quoted_text"
            if template.get("text_source"):
                return template
        if skill == "search_in_app":
            template = {}
            if cls._goal_looks_search(goal) and str(args.get("query") or "").strip():
                template["query_source"] = "search_query"
                if bool(args.get("prefer_intent")):
                    template["prefer_intent"] = True
                return template
        return None

    @classmethod
    def _hydrate_action_template(
        cls,
        goal: str,
        screen_summary: Dict[str, Any],
        skill: str,
        template: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        hydrated = dict(template or {})
        text_source = str(hydrated.pop("text_source", "") or "").strip().lower()
        if skill == "type_text":
            if text_source == "search_query":
                text_value = cls._extract_search_query(goal)
            elif text_source == "quoted_text":
                text_value = extract_message_body(goal) or ""
            else:
                text_value = ""
            if not text_value:
                return None
            hydrated["text"] = text_value

            strategy = str(hydrated.pop("target_strategy", "") or "").strip().lower()
            target = None
            if strategy == "focused_input":
                target = cls._find_best_input_candidate(screen_summary, focused_only=True)
            elif strategy == "search_input":
                target = cls._find_best_input_candidate(screen_summary, focused_only=False)
            if target:
                hydrated["target"] = target.get("label") or target.get("hint") or "Input"
                target_id = str(target.get("target_id") or "").strip()
                if target_id:
                    hydrated["target_id"] = target_id
                    hydrated["action_id"] = "type:{0}".format(target_id)
            return hydrated
        if skill == "search_in_app":
            query_source = str(hydrated.pop("query_source", "") or "").strip().lower()
            if query_source == "search_query":
                query_value = cls._extract_search_query(goal)
            else:
                query_value = ""
            if not query_value:
                return None
            hydrated["query"] = query_value
            return hydrated
        return hydrated

    def remember_ui_shortcut(
        self,
        task_type: str,
        app: str,
        page: str,
        intent: str,
        skill: str,
        args: Dict[str, Any],
        confidence: float = 1.0,
    ) -> bool:
        if not task_type or not page or not intent or not skill:
            return False

        timestamp = self._utc_now_iso()
        safe_args = dict(args or {})
        args_json = json.dumps(safe_args, ensure_ascii=False, sort_keys=True)
        target_label = str(safe_args.get("target") or "").strip()
        target_key = str(safe_args.get("target_key") or "").strip()
        intent_key = self._normalize_intent_key(intent)

        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO ui_shortcuts
                (task_type, app, page, intent_key, skill, args_json, target_label, target_key, confidence, use_count, last_seen_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(task_type, app, page, intent_key, skill, target_label, target_key) DO UPDATE SET
                    args_json = excluded.args_json,
                    confidence = CASE
                        WHEN excluded.confidence > ui_shortcuts.confidence THEN excluded.confidence
                        ELSE ui_shortcuts.confidence
                    END,
                    use_count = ui_shortcuts.use_count + 1,
                    last_seen_timestamp = excluded.last_seen_timestamp
                """,
                (
                    task_type,
                    self._normalize_app_key(app),
                    page,
                    intent_key,
                    skill,
                    args_json,
                    target_label,
                    target_key,
                    float(confidence),
                    timestamp,
                ),
            )
            conn.commit()
        return True

    def remember_interaction_pattern(
        self,
        task_type: str,
        app: str,
        page: str,
        goal: str,
        screen_summary: Dict[str, Any],
        recent_actions: Optional[Sequence[Dict[str, Any]]],
        skill: str,
        args: Dict[str, Any],
        confidence: float = 1.0,
    ) -> bool:
        if not task_type or not skill:
            return False
        state_tags = self.build_interaction_tags(
            screen_summary=screen_summary,
            goal=goal,
            recent_actions=recent_actions,
        )
        if len(state_tags) < 2:
            return False
        template = self._generalize_action_template(goal, screen_summary, skill, args or {})
        if not template:
            return False
        timestamp = self._utc_now_iso()
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO interaction_patterns
                (task_type, app, page, state_tags_json, action_skill, action_template_json, confidence, use_count, last_seen_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(task_type, app, page, state_tags_json, action_skill, action_template_json) DO UPDATE SET
                    confidence = CASE
                        WHEN excluded.confidence > interaction_patterns.confidence THEN excluded.confidence
                        ELSE interaction_patterns.confidence
                    END,
                    use_count = interaction_patterns.use_count + 1,
                    last_seen_timestamp = excluded.last_seen_timestamp
                """,
                (
                    task_type,
                    self._normalize_app_key(app),
                    str(page or "").strip(),
                    json.dumps(state_tags, ensure_ascii=False, sort_keys=True),
                    skill,
                    json.dumps(template, ensure_ascii=False, sort_keys=True),
                    float(confidence),
                    timestamp,
                ),
            )
            conn.commit()
        return True

    def find_interaction_pattern(
        self,
        task_type: str,
        app: Optional[str],
        page: str,
        goal: str,
        screen_summary: Optional[Dict[str, Any]] = None,
        recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not task_type:
            return None
        summary = screen_summary or {}
        current_tags = self.build_interaction_tags(summary, goal=goal, recent_actions=recent_actions)
        if len(current_tags) < 2:
            return None
        app_key = self._normalize_app_key(app)
        page_key = str(page or "").strip()
        with self._managed_connect() as conn:
            rows = conn.execute(
                """
                SELECT app, page, state_tags_json, action_skill, action_template_json, confidence, use_count, last_seen_timestamp
                FROM interaction_patterns
                WHERE task_type = ? AND (app = ? OR app = '') AND (page = ? OR page = '')
                ORDER BY CASE WHEN app = ? THEN 0 ELSE 1 END,
                         CASE WHEN page = ? THEN 0 ELSE 1 END,
                         confidence DESC, use_count DESC, last_seen_timestamp DESC
                LIMIT 12
                """,
                (task_type, app_key, page_key, app_key, page_key),
            ).fetchall()
        current_tag_set = set(current_tags)
        best_match: Optional[Dict[str, Any]] = None
        best_score = 0
        for row in rows:
            pattern_tags = json.loads(row[2]) if row[2] else []
            if not isinstance(pattern_tags, list):
                continue
            overlap = len(current_tag_set.intersection(set(pattern_tags)))
            if overlap < 2:
                continue
            template = json.loads(row[4]) if row[4] else {}
            hydrated_args = self._hydrate_action_template(goal, summary, str(row[3]), template)
            if hydrated_args is None:
                continue
            score = overlap * 10 + int(row[6])
            if score <= best_score:
                continue
            best_score = score
            best_match = {
                "app": row[0],
                "page": row[1],
                "state_tags": pattern_tags,
                "skill": str(row[3]),
                "args": hydrated_args,
                "confidence": float(row[5]),
                "use_count": int(row[6]),
                "last_seen_timestamp": row[7],
                "match_score": score,
            }
        return best_match

    @staticmethod
    def _target_matches(candidate: Dict[str, Any], target_label: str) -> bool:
        lowered = (target_label or "").strip().lower()
        if not lowered:
            return False
        values = [
            candidate.get("label"),
            candidate.get("resource_id"),
            candidate.get("content_desc"),
        ]
        for value in values:
            text = str(value or "").strip().lower()
            if not text:
                continue
            if text == lowered:
                return True
            if len(lowered) <= 4:
                continue
            if lowered in text or text in lowered:
                return True
        return False

    @staticmethod
    def _target_key_alias_matches(candidate: Dict[str, Any], target_key: str) -> bool:
        alias = (target_key or "").strip().lower()
        if alias != "new_note":
            return True
        values = [
            candidate.get("label"),
            candidate.get("resource_id"),
            candidate.get("content_desc"),
        ]
        combined = " ".join(str(value or "").strip().lower() for value in values)
        if "sort note" in combined or "browse_text_note" in combined or "browse_list_note" in combined:
            return False
        return any(
            marker in combined
            for marker in (
                "create a note",
                "take a note",
                "new text note",
                "new_note_button",
            )
        )

    @classmethod
    def _screen_has_target(cls, screen_summary: Dict[str, Any], target_label: str) -> bool:
        lowered = (target_label or "").strip().lower()
        if not lowered:
            return False
        for item in screen_summary.get("possible_targets", []):
            if cls._target_matches(item or {}, lowered):
                return True
        for item in screen_summary.get("visible_text", []):
            text = str(item).strip().lower()
            if text and (text == lowered or lowered in text or text in lowered):
                return True
        return False

    @classmethod
    def _screen_has_clickable_target(
        cls,
        screen_summary: Dict[str, Any],
        target_label: str,
        target_key: str = "",
    ) -> bool:
        candidates = [target_label, target_key]
        for item in screen_summary.get("possible_targets", []):
            candidate = item or {}
            if not bool(candidate.get("clickable")):
                continue
            if target_key and not cls._target_key_alias_matches(candidate, target_key):
                continue
            for target in candidates:
                if cls._target_matches(candidate, target):
                    return True
        return False

    def find_ui_shortcut(
        self,
        task_type: str,
        app: Optional[str],
        page: str,
        intent: str,
        screen_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not task_type or not page or not intent:
            return None

        intent_key = self._normalize_intent_key(intent)
        app_key = self._normalize_app_key(app)
        with self._managed_connect() as conn:
            rows = conn.execute(
                """
                SELECT app, page, intent_key, skill, args_json, target_label, target_key, confidence, use_count, last_seen_timestamp
                FROM ui_shortcuts
                WHERE task_type = ? AND page = ? AND intent_key = ? AND (app = ? OR app = '')
                ORDER BY CASE WHEN app = ? THEN 0 ELSE 1 END, confidence DESC, use_count DESC, last_seen_timestamp DESC
                LIMIT 5
                """,
                (task_type, page, intent_key, app_key, app_key),
            ).fetchall()

        summary = screen_summary or {}
        for row in rows:
            args = json.loads(row[4]) if row[4] else {}
            skill = row[3]
            target_label = row[5] or str(args.get("target") or "").strip()
            target_key = row[6] or str(args.get("target_key") or "").strip()
            if skill == "tap" and not self._screen_has_clickable_target(summary, target_label, target_key):
                continue
            if target_label and not self._screen_has_target(summary, target_label):
                continue
            if not target_label and target_key and not self._screen_has_target(summary, target_key):
                continue
            return {
                "app": row[0],
                "page": row[1],
                "intent_key": row[2],
                "skill": skill,
                "args": args,
                "target_label": target_label,
                "target_key": target_key,
                "confidence": float(row[7]),
                "use_count": int(row[8]),
                "last_seen_timestamp": row[9],
            }
        return None

    def add_successful_trajectory(
        self,
        task_type: str,
        app: str,
        intent: str,
        steps_summary: str,
        confidence: float,
        verified: bool,
    ) -> bool:
        if not verified:
            return False
        timestamp = self._utc_now_iso()
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO successful_trajectories
                (task_type, app, intent, steps_summary, success, timestamp, confidence, verified)
                VALUES (?, ?, ?, ?, 1, ?, ?, 1)
                """,
                (task_type, app, intent, steps_summary, timestamp, confidence),
            )
            conn.commit()
        return True

    def add_failure_pattern(
        self,
        task_type: str,
        app: str,
        intent: str,
        steps_summary: str,
        confidence: float,
    ) -> None:
        timestamp = self._utc_now_iso()
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO failure_patterns
                (task_type, app, intent, steps_summary, success, timestamp, confidence)
                VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (task_type, app, intent, steps_summary, timestamp, confidence),
            )
            conn.commit()

    def add_manual_intervention_episode(
        self,
        task_type: str,
        app: str,
        page: str,
        intent: str,
        trigger_reason: str,
        resolution_label: str,
        before_summary: Dict[str, Any],
        after_summary: Dict[str, Any],
        recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
        before_screenshot_path: str = "",
        after_screenshot_path: str = "",
        before_ui_dump_path: str = "",
        after_ui_dump_path: str = "",
        user_note: str = "",
        confidence: float = 1.0,
    ) -> None:
        timestamp = self._utc_now_iso()
        before_tags = self.build_interaction_tags(before_summary or {}, goal=intent, recent_actions=recent_actions)
        after_tags = self.build_interaction_tags(after_summary or {}, goal=intent, recent_actions=recent_actions)
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO manual_interventions
                (
                    task_type, app, page, intent, trigger_reason, resolution_label,
                    before_tags_json, after_tags_json, before_summary_json, after_summary_json,
                    recent_actions_json, before_screenshot_path, after_screenshot_path,
                    before_ui_dump_path, after_ui_dump_path, user_note, timestamp, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_type,
                    self._normalize_app_key(app),
                    str(page or "").strip(),
                    intent,
                    str(trigger_reason or "").strip(),
                    str(resolution_label or "manual_continue").strip(),
                    json.dumps(before_tags, ensure_ascii=False, sort_keys=True),
                    json.dumps(after_tags, ensure_ascii=False, sort_keys=True),
                    json.dumps(before_summary or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(after_summary or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(list(recent_actions or []), ensure_ascii=False, sort_keys=True),
                    before_screenshot_path,
                    after_screenshot_path,
                    before_ui_dump_path,
                    after_ui_dump_path,
                    user_note,
                    timestamp,
                    float(confidence),
                ),
            )
            conn.commit()

    def add_manual_reflection(
        self,
        task_type: str,
        app: str,
        page: str,
        intent: str,
        trigger_reason: str,
        resolution_label: str,
        failed_skill: str,
        failed_args: Dict[str, Any],
        agent_actions: Optional[Sequence[Dict[str, Any]]],
        before_summary: Dict[str, Any],
        after_summary: Dict[str, Any],
        reflection: Dict[str, Any],
        confidence: float = 1.0,
    ) -> None:
        timestamp = self._utc_now_iso()
        before_tags = self.build_interaction_tags(before_summary or {}, goal=intent, recent_actions=agent_actions)
        after_tags = self.build_interaction_tags(after_summary or {}, goal=intent, recent_actions=agent_actions)
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO manual_reflections
                (
                    task_type, app, page, intent, trigger_reason, resolution_label,
                    failed_skill, failed_args_json, agent_actions_json, before_tags_json,
                    after_tags_json, reflection_json, timestamp, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_type,
                    self._normalize_app_key(app),
                    str(page or "").strip(),
                    intent,
                    str(trigger_reason or "").strip(),
                    str(resolution_label or "manual_continue").strip(),
                    str(failed_skill or "").strip(),
                    json.dumps(failed_args or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(list(agent_actions or []), ensure_ascii=False, sort_keys=True),
                    json.dumps(before_tags, ensure_ascii=False, sort_keys=True),
                    json.dumps(after_tags, ensure_ascii=False, sort_keys=True),
                    json.dumps(reflection or {}, ensure_ascii=False, sort_keys=True),
                    timestamp,
                    float(confidence),
                ),
            )
            conn.commit()

    def upsert_learned_procedure(
        self,
        task_type: str,
        app: str,
        intent: str,
        title: str,
        procedure: Dict[str, Any],
        source_refs: Optional[Sequence[Dict[str, Any]]] = None,
        confidence: float = 0.8,
        verified: bool = False,
    ) -> bool:
        if not task_type or not intent or not title or not procedure:
            return False
        steps = procedure.get("steps")
        if not isinstance(steps, list) or not steps:
            return False
        safe_procedure = apply_lesson_safety(procedure, verified=verified)
        timestamp = self._utc_now_iso()
        intent_key = self._normalize_intent_key(intent)
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO learned_procedures
                (
                    task_type, app, intent_key, title, procedure_json,
                    source_refs_json, confidence, verified, use_count, last_seen_timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(task_type, app, intent_key, title) DO UPDATE SET
                    procedure_json = excluded.procedure_json,
                    source_refs_json = excluded.source_refs_json,
                    confidence = CASE
                        WHEN excluded.confidence > learned_procedures.confidence THEN excluded.confidence
                        ELSE learned_procedures.confidence
                    END,
                    verified = CASE
                        WHEN excluded.verified > learned_procedures.verified THEN excluded.verified
                        ELSE learned_procedures.verified
                    END,
                    use_count = learned_procedures.use_count + 1,
                    last_seen_timestamp = excluded.last_seen_timestamp
                """,
                (
                    task_type,
                    self._normalize_app_key(app),
                    intent_key,
                    str(title or "").strip()[:160],
                    json.dumps(safe_procedure, ensure_ascii=False, sort_keys=True),
                    json.dumps(list(source_refs or []), ensure_ascii=False, sort_keys=True),
                    float(confidence),
                    1 if verified else 0,
                    timestamp,
                ),
            )
            conn.commit()
        return True

    def get_relevant_learned_procedures(
        self,
        task_type: str,
        intent: str,
        app: Optional[str] = None,
        limit: int = 3,
        verified_only: bool = True,
    ) -> List[Dict[str, Any]]:
        if not task_type or not intent:
            return []
        app_key = self._normalize_app_key(app)
        intent_key = self._normalize_intent_key(intent)
        wildcard = "%{0}%".format(intent_key)
        clauses = ["task_type = ?", "(intent_key = ? OR intent_key LIKE ? OR ? LIKE '%' || intent_key || '%')"]
        params: List[Any] = [task_type, intent_key, wildcard, intent_key]
        if app_key:
            clauses.append("(app = ? OR app = '')")
            params.append(app_key)
        if verified_only:
            clauses.append("verified = 1")
        query = """
            SELECT task_type, app, intent_key, title, procedure_json, source_refs_json,
                   confidence, verified, use_count, last_seen_timestamp
            FROM learned_procedures
            WHERE {0}
            ORDER BY verified DESC, confidence DESC, use_count DESC, last_seen_timestamp DESC
            LIMIT ?
        """.format(" AND ".join(clauses))
        params.append(limit)
        with self._managed_connect() as conn:
            rows = conn.execute(query, params).fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            procedure = json.loads(row[4]) if row[4] else {}
            steps = procedure.get("steps") if isinstance(procedure, dict) else []
            items.append(
                {
                    "task_type": row[0],
                    "app": row[1],
                    "intent": row[2],
                    "title": row[3],
                    "steps_summary": self._procedure_steps_summary(steps),
                    "procedure": procedure,
                    "source_refs": json.loads(row[5]) if row[5] else [],
                    "success": True,
                    "timestamp": row[9],
                    "confidence": row[6],
                    "verified": bool(row[7]),
                    "use_count": int(row[8]),
                    "source": "learned_procedure",
                }
            )
        return items

    @staticmethod
    def _procedure_steps_summary(steps: Any) -> str:
        if not isinstance(steps, list):
            return ""
        labels: List[str] = []
        for step in steps[:8]:
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or step.get("skill") or "").strip()
            target = str(step.get("target") or step.get("label") or "").strip()
            if target:
                labels.append("{0} {1}".format(action, target).strip())
            elif action:
                labels.append(action)
        if len(steps) > 8:
            labels.append("...{0} more".format(len(steps) - 8))
        return " > ".join(labels)

    def list_learned_procedures(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._managed_connect() as conn:
            rows = conn.execute(
                """
                SELECT task_type, app, intent_key, title, procedure_json, source_refs_json,
                       confidence, verified, use_count, last_seen_timestamp
                FROM learned_procedures
                ORDER BY verified DESC, confidence DESC, last_seen_timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items: List[Dict[str, Any]] = []
        for row in rows:
            procedure = json.loads(row[4]) if row[4] else {}
            items.append(
                {
                    "task_type": row[0],
                    "app": row[1],
                    "intent": row[2],
                    "title": row[3],
                    "procedure": procedure,
                    "source_refs": json.loads(row[5]) if row[5] else [],
                    "confidence": row[6],
                    "verified": bool(row[7]),
                    "use_count": int(row[8]),
                    "timestamp": row[9],
                    "source": "learned_procedure",
                }
            )
        return items

    def list_manual_interventions(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._managed_connect() as conn:
            rows = conn.execute(
                """
                SELECT task_type, app, page, intent, trigger_reason, resolution_label,
                       before_tags_json, after_tags_json, before_screenshot_path, after_screenshot_path,
                       before_ui_dump_path, after_ui_dump_path, user_note, timestamp, confidence
                FROM manual_interventions
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            items.append(
                {
                    "task_type": row[0],
                    "app": row[1],
                    "page": row[2],
                    "intent": row[3],
                    "trigger_reason": row[4],
                    "resolution_label": row[5],
                    "before_tags": json.loads(row[6]) if row[6] else [],
                    "after_tags": json.loads(row[7]) if row[7] else [],
                    "before_screenshot_path": row[8],
                    "after_screenshot_path": row[9],
                    "before_ui_dump_path": row[10],
                    "after_ui_dump_path": row[11],
                    "user_note": row[12],
                    "timestamp": row[13],
                    "confidence": row[14],
                    "source": "manual_intervention",
                }
            )
        return items

    def list_manual_reflections(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._managed_connect() as conn:
            rows = conn.execute(
                """
                SELECT task_type, app, page, intent, trigger_reason, resolution_label,
                       failed_skill, failed_args_json, agent_actions_json, before_tags_json,
                       after_tags_json, reflection_json, timestamp, confidence
                FROM manual_reflections
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            items.append(
                {
                    "task_type": row[0],
                    "app": row[1],
                    "page": row[2],
                    "intent": row[3],
                    "trigger_reason": row[4],
                    "resolution_label": row[5],
                    "failed_skill": row[6],
                    "failed_args": json.loads(row[7]) if row[7] else {},
                    "agent_actions": json.loads(row[8]) if row[8] else [],
                    "before_tags": json.loads(row[9]) if row[9] else [],
                    "after_tags": json.loads(row[10]) if row[10] else [],
                    "reflection": json.loads(row[11]) if row[11] else {},
                    "timestamp": row[12],
                    "confidence": row[13],
                    "source": "manual_reflection",
                }
            )
        return items

    def clear_guided_ui_learning(self) -> Dict[str, int]:
        counts = {
            "successful_trajectories": 0,
            "failure_patterns": 0,
            "manual_interventions": 0,
            "manual_reflections": 0,
            "interaction_patterns": 0,
            "learned_procedures": 0,
        }
        with self._managed_connect() as conn:
            for table_name in (
                "successful_trajectories",
                "failure_patterns",
                "manual_interventions",
                "manual_reflections",
                "learned_procedures",
            ):
                counts[table_name] = conn.execute(
                    "SELECT COUNT(*) FROM {0} WHERE task_type = ?".format(table_name),
                    ("guided_ui_task",),
                ).fetchone()[0]
                conn.execute(
                    "DELETE FROM {0} WHERE task_type = ?".format(table_name),
                    ("guided_ui_task",),
                )
            counts["interaction_patterns"] = conn.execute(
                "SELECT COUNT(*) FROM interaction_patterns WHERE task_type = ?",
                ("guided_ui_task",),
            ).fetchone()[0]
            conn.execute(
                "DELETE FROM interaction_patterns WHERE task_type = ?",
                ("guided_ui_task",),
            )
            conn.commit()
        return counts

    def upsert_contact(
        self,
        contact_name: str,
        phone_number: str,
        source_app: str = "contacts_provider",
        confidence: float = 1.0,
    ) -> None:
        timestamp = self._utc_now_iso()
        with self._managed_connect() as conn:
            conn.execute(
                """
                INSERT INTO known_contacts
                (contact_name, phone_number, source_app, confidence, last_seen_timestamp)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(contact_name, phone_number) DO UPDATE SET
                    source_app = excluded.source_app,
                    confidence = excluded.confidence,
                    last_seen_timestamp = excluded.last_seen_timestamp
                """,
                (contact_name, phone_number, source_app, confidence, timestamp),
            )
            conn.commit()

    def list_contacts(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._managed_connect() as conn:
            cursor = conn.execute(
                """
                SELECT contact_name, phone_number, source_app, confidence, last_seen_timestamp
                FROM known_contacts
                ORDER BY confidence DESC, last_seen_timestamp DESC, contact_name ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            {
                "contact_name": row[0],
                "phone_number": row[1],
                "source_app": row[2],
                "confidence": row[3],
                "last_seen_timestamp": row[4],
            }
            for row in rows
        ]

    def get_contact_by_name(self, contact_name: str) -> Optional[Dict[str, Any]]:
        with self._managed_connect() as conn:
            cursor = conn.execute(
                """
                SELECT contact_name, phone_number, source_app, confidence, last_seen_timestamp
                FROM known_contacts
                WHERE lower(contact_name) = lower(?)
                ORDER BY confidence DESC, last_seen_timestamp DESC
                LIMIT 1
                """,
                (contact_name,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "contact_name": row[0],
            "phone_number": row[1],
            "source_app": row[2],
            "confidence": row[3],
            "last_seen_timestamp": row[4],
        }

    def get_relevant_contacts(self, query: str, limit: int = 3) -> List[Dict[str, Any]]:
        normalized = (query or "").strip().lower()
        if not normalized:
            return self.list_contacts(limit=limit)

        wildcard = "%{0}%".format(normalized.replace(" ", "%"))
        with self._managed_connect() as conn:
            cursor = conn.execute(
                """
                SELECT contact_name, phone_number, source_app, confidence, last_seen_timestamp
                FROM known_contacts
                WHERE lower(contact_name) LIKE ?
                   OR replace(lower(contact_name), ' ', '') LIKE replace(?, ' ', '')
                   OR lower(phone_number) LIKE ?
                ORDER BY confidence DESC, last_seen_timestamp DESC
                LIMIT ?
                """,
                (wildcard, wildcard, wildcard, limit),
            )
            rows = cursor.fetchall()
        return [
            {
                "contact_name": row[0],
                "phone_number": row[1],
                "source_app": row[2],
                "confidence": row[3],
                "last_seen_timestamp": row[4],
            }
            for row in rows
        ]

    def get_best_contact(self, prefer_ascii: bool = True) -> Optional[Dict[str, Any]]:
        contacts = self.list_contacts(limit=20)
        if prefer_ascii:
            for contact in contacts:
                if all(ord(char) < 128 for char in contact["contact_name"]):
                    return contact
        return contacts[0] if contacts else None

    def get_relevant_successes(
        self,
        task_type: str,
        app: Optional[str] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        return self._get_relevant_rows(
            table_name="successful_trajectories",
            task_type=task_type,
            app=app,
            limit=limit,
            verified_only=True,
        )

    def get_relevant_failures(
        self,
        task_type: str,
        app: Optional[str] = None,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        return self._get_relevant_rows(
            table_name="failure_patterns",
            task_type=task_type,
            app=app,
            limit=limit,
            verified_only=False,
        )

    def _get_relevant_rows(
        self,
        table_name: str,
        task_type: str,
        app: Optional[str],
        limit: int,
        verified_only: bool,
    ) -> List[Dict[str, Any]]:
        clauses = ["task_type = ?"]
        params: List[Any] = [task_type]
        if app:
            clauses.append("(app = ? OR app IS NULL OR app = '')")
            params.append(app)
        if verified_only and table_name == "successful_trajectories":
            clauses.append("verified = 1")
        query = """
            SELECT task_type, app, intent, steps_summary, success, timestamp, confidence
            FROM {0}
            WHERE {1}
            ORDER BY confidence DESC, timestamp DESC
            LIMIT ?
        """.format(table_name, " AND ".join(clauses))
        params.append(limit)

        with self._managed_connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "task_type": row[0],
                "app": row[1],
                "intent": row[2],
                "steps_summary": row[3],
                "success": bool(row[4]),
                "timestamp": row[5],
                "confidence": row[6],
                "source": "success" if table_name == "successful_trajectories" else "failure",
            }
            for row in rows
        ]

    def get_relevant_memories(
        self, intent: str, app: Optional[str] = None, limit: int = 3
    ) -> List[Dict[str, Any]]:
        intent_like = "%{0}%".format(intent)
        params = [intent_like]
        app_clause = ""
        if app:
            app_clause = " OR app = ?"
            params.append(app)

        query = """
            SELECT task_type, app, intent, steps_summary, success, timestamp, confidence, 'success' AS source
            FROM successful_trajectories
            WHERE verified = 1 AND (intent LIKE ? {0})
            UNION ALL
            SELECT task_type, app, intent, steps_summary, success, timestamp, confidence, 'failure' AS source
            FROM failure_patterns
            WHERE intent LIKE ? {0}
            UNION ALL
            SELECT task_type, app, intent,
                   trigger_reason || ' -> ' || resolution_label AS steps_summary,
                   1 AS success, timestamp, confidence, 'manual_intervention' AS source
            FROM manual_interventions
            WHERE intent LIKE ? {0}
            ORDER BY confidence DESC, timestamp DESC
            LIMIT ?
        """.format(app_clause)
        union_params = params + params + params + [limit]

        with self._managed_connect() as conn:
            cursor = conn.execute(query, union_params)
            rows = cursor.fetchall()

        memories = []
        for row in rows:
            memories.append(
                {
                    "task_type": row[0],
                    "app": row[1],
                    "intent": row[2],
                    "steps_summary": row[3],
                    "success": bool(row[4]),
                    "timestamp": row[5],
                    "confidence": row[6],
                    "source": row[7],
                }
            )
        return memories
