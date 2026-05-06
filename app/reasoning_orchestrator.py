from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.model_runtime import ModelRuntime
from app.procedural_skills import resolve_guided_ui_procedure
from app.reasoning_validator import ReasoningValidator
from app.schemas.reasoning_decision import ReasoningDecision
from app.trace_bus import TraceBus
from app.learning_flags import guided_ui_memory_expansion_enabled
from app.task_types import TASK_GUIDED_UI_TASK, extract_message_body
from app.ui_state import normalize_ui_state


class ReasoningOrchestrator(object):
    SYSTEM_PROMPT = (
        "You are a bounded Android GUI reasoning model. "
        "Return strict JSON only. "
        "Allowed decisions: execute, cloud_review, vl_review, unsupported. "
        "Allowed skills: open_app, tap, swipe, type_text, back, wait, search_in_app. "
        "Never output shell commands, adb commands, or raw chain-of-thought. "
        "Use skill=null only when the task is read-only and no action is needed. "
        "When affordance_graph.actions is present, choose one listed action whenever possible. "
        "For tap/type actions selected from affordance_graph, copy its action_id and target_id into args. "
        "Do not invent labels or coordinates when a matching action_id exists. "
        "Output exactly one JSON object with these top-level keys only: "
        "decision, task_type, skill, args, confidence, requires_confirmation, reason_summary. "
        "args must always be an object, even when empty. "
        "reason_summary must always be a short string. "
        "Do not use top-level keys such as action, target, resource_id, bounds, step, or explanation. "
        "For a tap action, use skill='tap' and put target details inside args. "
        "For a read-only task, return decision='execute', skill=null, args={}. "
        "Example action output: "
        "{\"decision\":\"execute\",\"task_type\":\"guided_ui_task\",\"skill\":\"tap\",\"args\":{\"action_id\":\"tap:n012\",\"target_id\":\"n012\",\"target\":\"Create a note\"},\"confidence\":0.90,\"requires_confirmation\":false,\"reason_summary\":\"The Create a note action is visible.\"} "
        "Example read-only output: "
        "{\"decision\":\"execute\",\"task_type\":\"guided_ui_task\",\"skill\":null,\"args\":{},\"confidence\":0.82,\"requires_confirmation\":false,\"reason_summary\":\"The current page is a Keep home screen with visible notes.\"}"
    )

    def __init__(
        self,
        validator: ReasoningValidator,
        model_runtime: ModelRuntime,
        trace_bus: TraceBus,
        rule_fallback: Callable[..., Dict[str, Any]],
    ) -> None:
        self.validator = validator
        self.model_runtime = model_runtime
        self.trace_bus = trace_bus
        self.rule_fallback = rule_fallback
        self.request_timeout_seconds = float(os.environ.get("REASONING_REQUEST_TIMEOUT_SECONDS", "20"))
        self.disable_local_text_after_failure = str(
            os.environ.get("REASONING_DISABLE_LOCAL_TEXT_AFTER_FAILURE", "1")
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._local_text_degraded = False

    def resolve(
        self,
        goal: str,
        task_type: str,
        screen_summary: Dict[str, Any],
        screenshot_path: Optional[str] = None,
        recent_actions: Optional[List[Dict[str, Any]]] = None,
        relevant_memories: Optional[List[Dict[str, Any]]] = None,
        normalized_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context_payload = normalized_context or self._build_request_payload(
            goal=goal,
            task_type=task_type,
            screen_summary=screen_summary,
            screenshot_path=screenshot_path,
            recent_actions=recent_actions,
            relevant_memories=relevant_memories,
        )
        context_payload = dict(context_payload)
        context_payload["_validation_screen_summary"] = screen_summary
        context_payload.setdefault("affordance_graph", screen_summary.get("affordance_graph") or {})
        context_payload.setdefault(
            "required_output_schema",
            {
                "decision": "execute | cloud_review | vl_review | unsupported",
                "task_type": task_type,
                "skill": "open_app | tap | swipe | type_text | back | wait | search_in_app | null",
                "args": "object; for affordance actions include action_id and target_id",
                "confidence": "float between 0.0 and 1.0",
                "requires_confirmation": "boolean",
                "reason_summary": "short string",
            },
        )
        context_payload.setdefault(
            "forbidden_top_level_keys",
            ["action", "target", "resource_id", "bounds", "step", "explanation"],
        )
        context_payload.setdefault(
            "ui_state",
            normalize_ui_state(
                goal=goal,
                task_type=task_type,
                screen_summary=screen_summary,
                recent_actions=recent_actions,
            ),
        )

        editor_type_decision = self._guided_keep_editor_type_decision(goal, task_type, screen_summary)
        if editor_type_decision:
            return self._finish(editor_type_decision, screen_summary)

        if self._guided_goal_already_complete(goal, task_type, screen_summary, context_payload):
            decision = ReasoningDecision(
                decision="execute",
                task_type=task_type,
                skill=None,
                args={},
                confidence=0.90,
                requires_confirmation=False,
                reason_summary="The requested guided UI task is already complete on the current page.",
                validation_errors=[],
                selected_backend="rule",
                fallback_used=True,
            )
            return self._finish(decision, screen_summary)

        blocker_procedure_decision = self._guided_procedure_decision(
            goal=goal,
            task_type=task_type,
            context_payload=context_payload,
            screen_summary=screen_summary,
            recent_actions=recent_actions,
            allowed_names=("clear_primary_blocker",),
        )
        if blocker_procedure_decision:
            return self._finish(blocker_procedure_decision, screen_summary)

        readiness_decision = self._guided_readiness_decision(goal, task_type, context_payload)
        if readiness_decision:
            return self._finish(readiness_decision, screen_summary)

        if guided_ui_memory_expansion_enabled():
            pattern_result = self._resolve_interaction_pattern(
                goal=goal,
                task_type=task_type,
                context_payload=context_payload,
            )
            if self._is_resolved(pattern_result["decision"], goal, task_type):
                return self._finish(pattern_result["decision"], screen_summary)

        procedure_decision = self._guided_procedure_decision(
            goal=goal,
            task_type=task_type,
            context_payload=context_payload,
            screen_summary=screen_summary,
            recent_actions=recent_actions,
        )
        if procedure_decision:
            return self._finish(procedure_decision, screen_summary)

        if self._should_use_model_first_action(task_type, context_payload):
            cloud_result = self._resolve_cloud_review(goal, task_type, context_payload, screenshot_path)
            if self._is_resolved(cloud_result["decision"], goal, task_type):
                return self._finish(cloud_result["decision"], screen_summary)

        shortcut_result = self._resolve_memory_shortcut(
            goal=goal,
            task_type=task_type,
            context_payload=context_payload,
        )
        if self._is_resolved(shortcut_result["decision"], goal, task_type):
            return self._finish(shortcut_result["decision"], screen_summary)

        pattern_result = self._resolve_interaction_pattern(
            goal=goal,
            task_type=task_type,
            context_payload=context_payload,
        )
        if self._is_resolved(pattern_result["decision"], goal, task_type):
            return self._finish(pattern_result["decision"], screen_summary)

        if self._should_use_cloud_first(goal, task_type):
            cloud_result = self._resolve_cloud_review(goal, task_type, context_payload, screenshot_path)
            if self._is_resolved(cloud_result["decision"], goal, task_type):
                return self._finish(cloud_result["decision"], screen_summary)

        local_text_result = self._resolve_local_text(goal, task_type, context_payload)
        if self._is_resolved(local_text_result["decision"], goal, task_type):
            return self._finish(local_text_result["decision"], screen_summary)

        if not self._should_use_cloud_first(goal, task_type):
            cloud_result = self._resolve_cloud_review(goal, task_type, context_payload, screenshot_path)
            if self._is_resolved(cloud_result["decision"], goal, task_type):
                return self._finish(cloud_result["decision"], screen_summary)

        local_vl_result = self._resolve_local_vl(goal, task_type, context_payload, screenshot_path)
        if self._is_resolved(local_vl_result["decision"], goal, task_type):
            return self._finish(local_vl_result["decision"], screen_summary)

        rule_decision = self._build_rule_fallback_decision(
            goal=goal,
            task_type=task_type,
            screen_summary=screen_summary,
            screenshot_path=screenshot_path,
            recent_actions=recent_actions,
            relevant_memories=relevant_memories,
        )
        return self._finish(rule_decision, screen_summary)

    def _resolve_local_text(
        self,
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if self._local_text_degraded:
            decision = ReasoningDecision(
                decision="unsupported",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Local text reasoning is skipped after an earlier failure in this run.",
                validation_errors=["local text skipped after failure"],
                selected_backend="local_text",
                fallback_used=False,
            )
            self.trace_bus.emit(
                stage="local_text.result",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary=decision.reason_summary,
                fallback_triggered=True,
            )
            self.trace_bus.emit(
                stage="validation.failed",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary="; ".join(decision.validation_errors),
                fallback_triggered=True,
            )
            return {"decision": decision}

        self.trace_bus.emit(
            stage="local_text.start",
            backend="local_text",
            success=True,
            reason_summary="Starting local text reasoning.",
            fallback_triggered=False,
        )
        runtime_state = self.model_runtime.ensure_local_text_service()
        if not runtime_state.get("available"):
            decision = ReasoningDecision(
                decision="unsupported",
                task_type=task_type,
                confidence=0.0,
                reason_summary=str(runtime_state.get("reason") or "Local text service is unavailable."),
                validation_errors=["local text service unavailable"],
                selected_backend="local_text",
                fallback_used=False,
            )
            self.trace_bus.emit(
                stage="local_text.result",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary=decision.reason_summary,
                fallback_triggered=True,
            )
            self.trace_bus.emit(
                stage="validation.failed",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary="; ".join(decision.validation_errors),
                fallback_triggered=True,
            )
            return {"decision": decision}

        try:
            raw_payload = self._call_openai_compatible_text(
                base_url=str(runtime_state["base_url"]),
                api_key=os.environ.get("LOCAL_TEXT_REASONER_API_KEY", "local"),
                model_name=os.environ.get("LOCAL_TEXT_REASONER_MODEL", ModelRuntime.DEFAULT_LOCAL_TEXT_MODEL),
                payload=context_payload,
            )
        except Exception as exc:
            self._maybe_degrade_local_text(exc)
            decision = ReasoningDecision(
                decision="unsupported",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Local text reasoning failed.",
                validation_errors=[str(exc)],
                selected_backend="local_text",
                fallback_used=False,
            )
            self.trace_bus.emit(
                stage="local_text.result",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary=decision.reason_summary,
                fallback_triggered=True,
            )
            self.trace_bus.emit(
                stage="validation.failed",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary="; ".join(decision.validation_errors),
                fallback_triggered=True,
            )
            return {"decision": decision}
        decision = self.validator.validate_payload(
            payload=raw_payload,
            expected_task_type=task_type,
            goal=goal,
            selected_backend="local_text",
            fallback_used=False,
            context=context_payload,
        )
        self.trace_bus.emit(
            stage="local_text.result",
            backend="local_text",
            success=not decision.validation_errors,
            confidence=decision.confidence,
            reason_summary=decision.reason_summary or "Local text reasoning completed.",
            fallback_triggered=not self._is_resolved(decision, goal, task_type),
        )
        if not self._is_resolved(decision, goal, task_type):
            self.trace_bus.emit(
                stage="validation.failed",
                backend="local_text",
                success=False,
                confidence=decision.confidence,
                reason_summary=self._weak_reason(decision, goal, task_type),
                fallback_triggered=True,
            )
        return {"decision": decision}

    def _resolve_memory_shortcut(
        self,
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        shortcut = context_payload.get("ui_shortcut")
        if not isinstance(shortcut, dict) or not shortcut.get("skill"):
            return {
                "decision": ReasoningDecision(
                    decision="unsupported",
                    task_type=task_type,
                    confidence=0.0,
                    reason_summary="No memory shortcut matched the current page.",
                    validation_errors=["memory shortcut unavailable"],
                    selected_backend="memory_rule",
                    fallback_used=False,
                )
            }

        decision = self.validator.validate_payload(
            payload={
                "decision": "execute",
                "task_type": task_type,
                "skill": shortcut.get("skill"),
                "args": dict(shortcut.get("args") or {}),
                "confidence": float(shortcut.get("confidence", 0.98)),
                "requires_confirmation": False,
                "reason_summary": "Verified shortcut matched the current page.",
            },
            expected_task_type=task_type,
            goal=goal,
            selected_backend="memory_rule",
            fallback_used=False,
            context=context_payload,
        )
        self.trace_bus.emit(
            stage="memory_rule.result",
            backend="memory_rule",
            success=not decision.validation_errors,
            confidence=decision.confidence,
            reason_summary=decision.reason_summary or "Memory shortcut resolved the current page.",
            fallback_triggered=not self._is_resolved(decision, goal, task_type),
        )
        return {"decision": decision}

    def _resolve_interaction_pattern(
        self,
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if task_type == TASK_GUIDED_UI_TASK and not guided_ui_memory_expansion_enabled():
            return {
                "decision": ReasoningDecision(
                    decision="unsupported",
                    task_type=task_type,
                    confidence=0.0,
                    reason_summary="Interaction-pattern memory is disabled for guided UI tasks.",
                    validation_errors=["interaction-pattern memory disabled for guided ui tasks"],
                    selected_backend="interaction_pattern",
                    fallback_used=False,
                )
            }
        pattern = context_payload.get("interaction_pattern")
        if not isinstance(pattern, dict) or not pattern.get("skill"):
            return {
                "decision": ReasoningDecision(
                    decision="unsupported",
                    task_type=task_type,
                    confidence=0.0,
                    reason_summary="No interaction pattern matched the current state.",
                    validation_errors=["interaction pattern unavailable"],
                    selected_backend="interaction_pattern",
                    fallback_used=False,
                )
            }
        decision = self.validator.validate_payload(
            payload={
                "decision": "execute",
                "task_type": task_type,
                "skill": pattern.get("skill"),
                "args": dict(pattern.get("args") or {}),
                "confidence": float(pattern.get("confidence", 0.9)),
                "requires_confirmation": False,
                "reason_summary": "Abstract interaction pattern matched the current state.",
            },
            expected_task_type=task_type,
            goal=goal,
            selected_backend="interaction_pattern",
            fallback_used=False,
            context=context_payload,
        )
        self.trace_bus.emit(
            stage="interaction_pattern.result",
            backend="interaction_pattern",
            success=not decision.validation_errors,
            confidence=decision.confidence,
            reason_summary=decision.reason_summary or "Interaction pattern resolved the current state.",
            fallback_triggered=not self._is_resolved(decision, goal, task_type),
        )
        return {"decision": decision}

    def _maybe_degrade_local_text(self, exc: Exception) -> None:
        if not self.disable_local_text_after_failure:
            return
        lowered = str(exc).strip().lower()
        if isinstance(exc, TimeoutError) or "timed out" in lowered or "timeout" in lowered:
            self._local_text_degraded = True

    def _should_use_cloud_first(self, goal: str, task_type: str) -> bool:
        if not self.model_runtime.cloud_reviewer_configured():
            return False
        if task_type == TASK_GUIDED_UI_TASK and self._is_read_only_guided_request(goal):
            return True
        return str(os.environ.get("REASONING_CLOUD_FIRST", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _resolve_cloud_review(
        self,
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
        screenshot_path: Optional[str],
    ) -> Dict[str, Any]:
        self.trace_bus.emit(
            stage="cloud_review.start",
            backend="cloud_reviewer",
            success=True,
            reason_summary="Starting cloud review stage.",
            fallback_triggered=True,
        )
        if not self.model_runtime.cloud_reviewer_configured():
            skipped = ReasoningDecision(
                decision="cloud_review",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Cloud reviewer skipped because it is not configured.",
                validation_errors=["cloud reviewer skipped"],
                selected_backend="cloud_reviewer",
                fallback_used=True,
            )
            self.trace_bus.emit(
                stage="cloud_review.result",
                backend="cloud_reviewer",
                success=False,
                confidence=skipped.confidence,
                reason_summary=skipped.reason_summary,
                fallback_triggered=True,
                extra={"skipped": True},
            )
            return {"decision": skipped}

        try:
            raw_payload = self._call_openai_compatible_review(
                base_url=self.model_runtime.cloud_reviewer_base_url(),
                api_key=self.model_runtime.cloud_reviewer_api_key(),
                model_name=self.model_runtime.cloud_reviewer_model(),
                payload=context_payload,
                screenshot_path=screenshot_path,
            )
        except Exception as exc:
            decision = ReasoningDecision(
                decision="cloud_review",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Cloud reviewer failed.",
                validation_errors=[str(exc)],
                selected_backend="cloud_reviewer",
                fallback_used=True,
            )
            self.trace_bus.emit(
                stage="cloud_review.result",
                backend="cloud_reviewer",
                success=False,
                confidence=decision.confidence,
                reason_summary=decision.reason_summary,
                fallback_triggered=True,
            )
            return {"decision": decision}
        decision = self.validator.validate_payload(
            payload=raw_payload,
            expected_task_type=task_type,
            goal=goal,
            selected_backend="cloud_reviewer",
            fallback_used=True,
            context=context_payload,
        )
        self.trace_bus.emit(
            stage="cloud_review.result",
            backend="cloud_reviewer",
            success=not decision.validation_errors,
            confidence=decision.confidence,
            reason_summary=decision.reason_summary or "Cloud review completed.",
            fallback_triggered=not self._is_resolved(decision, goal, task_type),
        )
        if not self._is_resolved(decision, goal, task_type):
            self.trace_bus.emit(
                stage="validation.failed",
                backend="cloud_reviewer",
                success=False,
                confidence=decision.confidence,
                reason_summary=self._weak_reason(decision, goal, task_type),
                fallback_triggered=True,
            )
        return {"decision": decision}

    def _resolve_local_vl(
        self,
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
        screenshot_path: Optional[str],
    ) -> Dict[str, Any]:
        if not self.model_runtime.local_vl_enabled():
            decision = ReasoningDecision(
                decision="vl_review",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Local VL skipped because it is disabled.",
                validation_errors=["local vl disabled"],
                selected_backend="local_vl",
                fallback_used=True,
            )
            return {"decision": decision}

        screenshot_file = Path(str(screenshot_path)) if screenshot_path else None
        if not screenshot_file or not screenshot_file.exists():
            decision = ReasoningDecision(
                decision="vl_review",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Local VL skipped because no screenshot was available.",
                validation_errors=["local vl skipped"],
                selected_backend="local_vl",
                fallback_used=True,
            )
            return {"decision": decision}

        self.trace_bus.emit(
            stage="local_vl.start",
            backend="local_vl",
            success=True,
            reason_summary="Starting local VL fallback.",
            fallback_triggered=True,
        )
        runtime_state = self.model_runtime.ensure_local_vl_service()
        if not runtime_state.get("available"):
            decision = ReasoningDecision(
                decision="vl_review",
                task_type=task_type,
                confidence=0.0,
                reason_summary=str(runtime_state.get("reason") or "Local VL service is unavailable."),
                validation_errors=["local vl service unavailable"],
                selected_backend="local_vl",
                fallback_used=True,
            )
            self.trace_bus.emit(
                stage="local_vl.result",
                backend="local_vl",
                success=False,
                confidence=decision.confidence,
                reason_summary=decision.reason_summary,
                fallback_triggered=True,
            )
            return {"decision": decision}

        try:
            raw_payload = self._call_openai_compatible_vl(
                base_url=str(runtime_state["base_url"]),
                api_key=os.environ.get("LOCAL_VL_REASONER_API_KEY", "local"),
                model_name=os.environ.get("LOCAL_VL_REASONER_MODEL", ModelRuntime.DEFAULT_LOCAL_VL_MODEL),
                payload=context_payload,
                screenshot_path=str(screenshot_file),
            )
        except Exception as exc:
            decision = ReasoningDecision(
                decision="vl_review",
                task_type=task_type,
                confidence=0.0,
                reason_summary="Local VL fallback failed.",
                validation_errors=[str(exc)],
                selected_backend="local_vl",
                fallback_used=True,
            )
            self.trace_bus.emit(
                stage="local_vl.result",
                backend="local_vl",
                success=False,
                confidence=decision.confidence,
                reason_summary=decision.reason_summary,
                fallback_triggered=True,
            )
            return {"decision": decision}
        decision = self.validator.validate_payload(
            payload=raw_payload,
            expected_task_type=task_type,
            goal=goal,
            selected_backend="local_vl",
            fallback_used=True,
            context=context_payload,
        )
        self.trace_bus.emit(
            stage="local_vl.result",
            backend="local_vl",
            success=not decision.validation_errors,
            confidence=decision.confidence,
            reason_summary=decision.reason_summary or "Local VL fallback completed.",
            fallback_triggered=not self._is_resolved(decision, goal, task_type),
        )
        if not self._is_resolved(decision, goal, task_type):
            self.trace_bus.emit(
                stage="validation.failed",
                backend="local_vl",
                success=False,
                confidence=decision.confidence,
                reason_summary=self._weak_reason(decision, goal, task_type),
                fallback_triggered=True,
            )
        return {"decision": decision}

    def _finish(
        self,
        decision: ReasoningDecision,
        screen_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.trace_bus.emit(
            stage="final_decision.selected",
            backend=decision.selected_backend,
            success=decision.decision == "execute" and not decision.validation_errors,
            confidence=decision.confidence,
            reason_summary=decision.reason_summary,
            fallback_triggered=decision.fallback_used,
        )
        return {
            "decision": decision,
            "trace_path": str(self.trace_bus.trace_path),
            "legacy_reasoning": decision.to_legacy_reasoning_payload(screen_summary=screen_summary),
        }

    def _build_rule_fallback_decision(
        self,
        goal: str,
        task_type: str,
        screen_summary: Dict[str, Any],
        screenshot_path: Optional[str],
        recent_actions: Optional[List[Dict[str, Any]]],
        relevant_memories: Optional[List[Dict[str, Any]]],
    ) -> ReasoningDecision:
        rule_reasoning = self.rule_fallback(
            goal=goal,
            task_type=task_type,
            screen_summary=screen_summary,
            screenshot_path=screenshot_path,
            recent_actions=recent_actions,
            relevant_memories=relevant_memories,
        )
        next_action = rule_reasoning.get("next_action") or {}
        return ReasoningDecision(
            decision="execute",
            task_type=task_type,
            skill=next_action.get("skill"),
            args=dict(next_action.get("args") or {}),
            confidence=float(rule_reasoning.get("confidence", 0.6)),
            requires_confirmation=bool(rule_reasoning.get("requires_confirmation", False)),
            reason_summary=str(rule_reasoning.get("summary") or "Rule fallback selected."),
            validation_errors=[],
            selected_backend="rule",
            fallback_used=True,
        )

    def _build_request_payload(
        self,
        goal: str,
        task_type: str,
        screen_summary: Dict[str, Any],
        screenshot_path: Optional[str],
        recent_actions: Optional[List[Dict[str, Any]]],
        relevant_memories: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        return {
            "goal": goal,
            "task_type": task_type,
            "screen_summary": screen_summary,
            "screenshot_path": screenshot_path,
            "recent_actions": recent_actions or [],
            "relevant_memories": relevant_memories or [],
            "ui_shortcut": None,
            "affordance_graph": screen_summary.get("affordance_graph") or {},
            "required_output_schema": {
                "decision": "execute | cloud_review | vl_review | unsupported",
                "task_type": task_type,
                "skill": "open_app | tap | swipe | type_text | back | wait | search_in_app | null",
                "args": "object; for affordance actions include action_id and target_id",
                "confidence": "float between 0.0 and 1.0",
                "requires_confirmation": "boolean",
                "reason_summary": "short string",
            },
            "forbidden_top_level_keys": [
                "action",
                "target",
                "resource_id",
                "bounds",
                "step",
                "explanation",
            ],
        }

    def _is_resolved(
        self,
        decision: ReasoningDecision,
        goal: str,
        task_type: str,
    ) -> bool:
        self._normalize_read_only_decision(decision, goal, task_type)
        require_action = task_type == TASK_GUIDED_UI_TASK and not self._is_read_only_guided_request(goal)
        return self.validator.is_strong_decision(decision, require_action=require_action)

    def _should_use_model_first_action(self, task_type: str, context_payload: Dict[str, Any]) -> bool:
        if task_type != TASK_GUIDED_UI_TASK:
            return False
        if not self.model_runtime.cloud_reviewer_configured():
            return False
        override = str(os.environ.get("REASONING_MODEL_FIRST_ACTIONS", "")).strip().lower()
        if override in {"0", "false", "no", "off"}:
            return False
        graph = context_payload.get("affordance_graph")
        if not isinstance(graph, dict):
            return False
        actions = graph.get("actions")
        return isinstance(actions, list) and bool(actions)

    def _normalize_read_only_decision(
        self,
        decision: ReasoningDecision,
        goal: str,
        task_type: str,
    ) -> None:
        if task_type != TASK_GUIDED_UI_TASK:
            return
        if not self._is_read_only_guided_request(goal):
            return
        if decision.decision != "execute":
            return
        decision.skill = None
        decision.args = {}

    @staticmethod
    def _is_read_only_guided_request(goal: str) -> bool:
        normalized = (goal or "").strip().lower()
        return any(
            marker in normalized
            for marker in (
                "tell me what is on",
                "what is on the current page",
                "inspect the current screen",
                "inspect the current page",
                "summarize the current screen",
                "summarize the current page",
                "read the current screen",
                "read the current page",
            )
        )

    @staticmethod
    def _guided_procedure_decision(
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
        screen_summary: Dict[str, Any],
        recent_actions: Optional[List[Dict[str, Any]]] = None,
        allowed_names: Optional[Sequence[str]] = None,
    ) -> Optional[ReasoningDecision]:
        if task_type != TASK_GUIDED_UI_TASK:
            return None
        if ReasoningOrchestrator._is_read_only_guided_request(goal):
            return None
        ui_state = context_payload.get("ui_state")
        if not isinstance(ui_state, dict):
            return None
        procedure = resolve_guided_ui_procedure(
            goal=goal,
            task_type=task_type,
            screen_summary=screen_summary,
            ui_state=ui_state,
            recent_actions=recent_actions,
            allowed_names=allowed_names,
        )
        if not procedure:
            return None
        return ReasoningDecision(
            decision="execute",
            task_type=task_type,
            skill=procedure.skill,
            args=dict(procedure.args or {}),
            confidence=procedure.confidence,
            requires_confirmation=False,
            reason_summary=procedure.reason_summary,
            validation_errors=[],
            selected_backend="procedure",
            fallback_used=True,
        )

    @staticmethod
    def _guided_readiness_decision(
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
    ) -> Optional[ReasoningDecision]:
        if task_type != TASK_GUIDED_UI_TASK:
            return None
        if ReasoningOrchestrator._is_read_only_guided_request(goal):
            return None
        ui_state = context_payload.get("ui_state")
        if not isinstance(ui_state, dict):
            return None
        readiness = ui_state.get("readiness")
        if not isinstance(readiness, dict):
            return None
        status = str(readiness.get("status") or "").strip().lower()
        if status not in {"loading", "uncertain", "blocked"}:
            return None
        reason = str(readiness.get("reason") or "The UI is not ready for normal action selection.")
        return ReasoningDecision(
            decision="execute",
            task_type=task_type,
            skill="wait",
            args={"seconds": 2, "reason": reason},
            confidence=0.88,
            requires_confirmation=False,
            reason_summary="Readiness gate selected wait: {0}".format(reason),
            validation_errors=[],
            selected_backend="readiness",
            fallback_used=True,
        )

    @staticmethod
    def _guided_goal_already_complete(
        goal: str,
        task_type: str,
        screen_summary: Dict[str, Any],
        context_payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if task_type != TASK_GUIDED_UI_TASK:
            return False
        ui_state = (context_payload or {}).get("ui_state") if isinstance(context_payload, dict) else {}
        progress = ui_state.get("goal_progress") if isinstance(ui_state, dict) else {}
        if isinstance(progress, dict) and bool(progress.get("done")):
            return True
        if isinstance(progress, dict) and ReasoningOrchestrator._goal_looks_search(goal):
            # The normalized UI state has the stricter search completion check:
            # reaching a site search page is not enough unless the requested query is present.
            return False
        normalized = (goal or "").strip().lower()
        page = str((screen_summary or {}).get("page") or "").strip().lower()
        current_url = str((screen_summary or {}).get("current_url") or "").strip().lower()
        current_domain = str((screen_summary or {}).get("current_domain") or "").strip().lower()
        requested_text = extract_message_body(goal)
        visible_text = " ".join(str(item).lower() for item in (screen_summary or {}).get("visible_text", []))
        if requested_text:
            if requested_text.lower() not in visible_text:
                return False
        if ReasoningOrchestrator._goal_looks_search(goal):
            query = ReasoningOrchestrator._extract_search_query(goal)
            query_tokens = [token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 2]
            query_hits = sum(1 for token in query_tokens if token in visible_text)
            site_terms = ("bilibili", "youtube", "github", "reddit", "wikipedia", "amazon", "facebook")
            url_hit = any(token in current_url for token in query_tokens) and (
                "/search" in current_url or "keyword=" in current_url or "q=" in current_url
            )
            domain_hit = any(term in normalized and term in current_domain for term in site_terms)
            if url_hit and (domain_hit or not any(term in normalized for term in site_terms)):
                return True
            site_hit = any(term in normalized and term in visible_text for term in site_terms) or (
                "bilibili" in normalized and "哔哩哔哩" in visible_text
            )
            if query_hits >= max(1, min(2, len(query_tokens))) and (
                "search?keyword=" in visible_text or "results" in visible_text or site_hit or page.endswith("search_results")
            ):
                return True
        if page == "keep_editor" and "keep" in normalized and (
            "create a note" in normalized
            or "create a text note" in normalized
            or "new note" in normalized
        ):
            return True
        return False

    @staticmethod
    def _guided_blocker_decision(
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
    ) -> Optional[ReasoningDecision]:
        if task_type != TASK_GUIDED_UI_TASK:
            return None
        if ReasoningOrchestrator._is_read_only_guided_request(goal):
            return None
        ui_state = context_payload.get("ui_state")
        if not isinstance(ui_state, dict):
            return None
        blocker = ui_state.get("primary_blocker")
        if not isinstance(blocker, dict):
            return None
        action = blocker.get("suggested_action")
        if not isinstance(action, dict):
            return None
        skill = str(action.get("skill") or "").strip()
        if skill not in {"tap", "back", "wait"}:
            return None
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        blocker_type = str(blocker.get("type") or "blocker").strip()
        return ReasoningDecision(
            decision="execute",
            task_type=task_type,
            skill=skill,
            args=dict(args),
            confidence=0.93,
            requires_confirmation=False,
            reason_summary="Clear blocking UI first: {0}.".format(blocker_type),
            validation_errors=[],
            selected_backend="rule",
            fallback_used=True,
        )

    @staticmethod
    def _guided_keep_editor_type_decision(
        goal: str,
        task_type: str,
        screen_summary: Dict[str, Any],
    ) -> Optional[ReasoningDecision]:
        if task_type != TASK_GUIDED_UI_TASK:
            return None
        page = str((screen_summary or {}).get("page") or "").strip().lower()
        if page != "keep_editor":
            return None
        text = extract_message_body(goal)
        if not text:
            return None
        visible_text = " ".join(str(item).lower() for item in (screen_summary or {}).get("visible_text", []))
        if text.lower() in visible_text:
            return None

        best_target: Dict[str, Any] = {}
        for target in (screen_summary or {}).get("possible_targets", []):
            if not isinstance(target, dict):
                continue
            combined = " ".join(
                str(target.get(key) or "").lower()
                for key in ("label", "resource_id", "content_desc", "class_name")
            )
            if "edit_note_text" in combined or combined.strip() == "note":
                best_target = target
                break

        args: Dict[str, Any] = {"text": text}
        if best_target:
            args["target"] = best_target.get("label") or "Note"
            target_id = best_target.get("target_id")
            if target_id:
                args["target_id"] = target_id
                args["action_id"] = "type:{0}".format(target_id)

        return ReasoningDecision(
            decision="execute",
            task_type=task_type,
            skill="type_text",
            args=args,
            confidence=0.92,
            requires_confirmation=False,
            reason_summary="The Keep editor is open; type the requested note text.",
            validation_errors=[],
            selected_backend="rule",
            fallback_used=True,
        )

    @staticmethod
    def _candidate_text(candidate: Dict[str, Any]) -> str:
        return " ".join(
            str(candidate.get(key) or "").strip().lower()
            for key in ("label", "resource_id", "content_desc", "class_name", "hint")
        )

    @classmethod
    def _find_best_input_target(
        cls,
        screen_summary: Dict[str, Any],
        focused_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        best_target: Optional[Dict[str, Any]] = None
        best_score = -1
        for target in (screen_summary or {}).get("possible_targets", []):
            if not isinstance(target, dict):
                continue
            class_name = str(target.get("class_name") or "").lower()
            if "edittext" not in class_name:
                continue
            if focused_only and not bool(target.get("focused")):
                continue
            score = 0
            if bool(target.get("focused")):
                score += 4
            if bool(target.get("clickable")):
                score += 1
            combined = cls._candidate_text(target)
            if any(marker in combined for marker in ("search", "url", "query", "address", "find")):
                score += 2
            if score > best_score:
                best_score = score
                best_target = target
        return best_target

    @classmethod
    def _text_corpus(cls, screen_summary: Dict[str, Any]) -> str:
        fragments: List[str] = []
        for item in (screen_summary or {}).get("visible_text", []):
            text = str(item or "").strip().lower()
            if text:
                fragments.append(text)
        for candidate in (screen_summary or {}).get("possible_targets", []):
            if not isinstance(candidate, dict):
                continue
            combined = cls._candidate_text(candidate)
            if combined:
                fragments.append(combined)
        return " ".join(fragments)

    @classmethod
    def _has_input_blocker_overlay(cls, screen_summary: Dict[str, Any]) -> bool:
        corpus = cls._text_corpus(screen_summary)
        if not corpus:
            return False
        strong_markers = (
            "try out your stylus",
            "write here",
            "use your stylus",
            "handwriting is automatically converted to text",
            "stylus",
        )
        if not any(marker in corpus for marker in strong_markers):
            return False
        blocker_markers = ("cancel", "next", "reset", "write", "delete", "select", "insert")
        return sum(1 for marker in blocker_markers if marker in corpus) >= 2

    @classmethod
    def _looks_like_browser_search_surface(
        cls,
        screen_summary: Dict[str, Any],
        input_target: Dict[str, Any],
    ) -> bool:
        combined = cls._candidate_text(input_target)
        if "url_bar" in combined or "location_bar" in combined:
            return True
        app_name = str((screen_summary or {}).get("app") or "").strip().lower()
        focus = str((screen_summary or {}).get("focus") or "").strip().lower()
        if any(marker in combined for marker in ("search", "url", "address")) and any(
            marker in "{0} {1}".format(app_name, focus)
            for marker in ("chrome", "browser")
        ):
            return True
        return False

    @staticmethod
    def _goal_looks_search(goal: str) -> bool:
        normalized = str(goal or "").strip().lower()
        return any(
            marker in normalized
            for marker in ("search ", "search for", "find ", "find videos", "find video", "look up", "look for")
        )

    @staticmethod
    def _goal_looks_video_search(goal: str) -> bool:
        normalized = str(goal or "").strip().lower()
        return "video" in normalized or "videos" in normalized

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
            match = __import__("re").search(pattern, normalized, __import__("re").IGNORECASE)
            if match:
                query = match.group(1).strip(" .,!?:;")
                break
        if not query:
            return ""
        query = __import__("re").sub(r"^(on|in|with)\s+", "", query).strip()
        query = __import__("re").sub(r"\s+(on|in)\s+(chrome|browser|web)\b.*$", "", query).strip()
        if not query:
            return ""
        known_terms = []
        for term in ("bilibili", "youtube", "wikipedia", "amazon", "github", "reddit", "facebook", "b站"):
            if term in normalized:
                known_terms.append("bilibili" if term == "b站" else term)
        if known_terms and not any(term in query for term in known_terms):
            query = "{0} {1}".format(known_terms[0], query).strip()
        return query[:120]

    @classmethod
    def _guided_interaction_pattern_decision(
        cls,
        goal: str,
        task_type: str,
        context_payload: Dict[str, Any],
        screen_summary: Dict[str, Any],
        recent_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[ReasoningDecision]:
        if task_type != TASK_GUIDED_UI_TASK:
            return None
        if cls._is_read_only_guided_request(goal):
            return None

        input_target = cls._find_best_input_target(screen_summary, focused_only=True) or cls._find_best_input_target(
            screen_summary,
            focused_only=False,
        )
        if not input_target:
            return None

        requested_text = extract_message_body(goal)
        press_enter = False
        dismiss_overlays_first = False
        if requested_text:
            text = requested_text
        elif cls._goal_looks_search(goal):
            combined = cls._candidate_text(input_target)
            if not any(marker in combined for marker in ("search", "url", "query", "address", "find")):
                return None
            text = cls._extract_search_query(goal)
            if not text:
                return None
            if cls._looks_like_browser_search_surface(screen_summary, input_target):
                return ReasoningDecision(
                    decision="execute",
                    task_type=task_type,
                    skill="search_in_app",
                    args={
                        "query": text,
                        "prefer_intent": True,
                        "press_enter": True,
                    },
                    confidence=0.92,
                    requires_confirmation=False,
                    reason_summary="A browser search surface is focused; open the search through a web intent.",
                    validation_errors=[],
                    selected_backend="rule",
                    fallback_used=True,
                )
            press_enter = True
            dismiss_overlays_first = True
        else:
            return None

        repeated_taps = [
            action
            for action in (recent_actions or [])
            if str((action or {}).get("action") or "").strip() == "tap" and bool((action or {}).get("success"))
        ]
        if len(repeated_taps) >= 1 and cls._goal_looks_search(goal):
            dismiss_overlays_first = True

        args: Dict[str, Any] = {
            "text": text,
            "target": input_target.get("label") or input_target.get("hint") or "Input",
        }
        target_id = str(input_target.get("target_id") or "").strip()
        if target_id:
            args["target_id"] = target_id
            args["action_id"] = "type:{0}".format(target_id)
        if press_enter:
            args["press_enter"] = True
        if dismiss_overlays_first:
            args["dismiss_overlays_first"] = True

        summary = "A focused input is available; enter the requested text."
        if cls._goal_looks_video_search(goal):
            summary = "A focused search input is available; enter the synthesized video search query."
        return ReasoningDecision(
            decision="execute",
            task_type=task_type,
            skill="type_text",
            args=args,
            confidence=0.91,
            requires_confirmation=False,
            reason_summary=summary,
            validation_errors=[],
            selected_backend="rule",
            fallback_used=True,
        )

    def _weak_reason(
        self,
        decision: ReasoningDecision,
        goal: str,
        task_type: str,
    ) -> str:
        if decision.validation_errors:
            return "; ".join(decision.validation_errors)
        if decision.decision != "execute":
            return "Decision requested escalation or unsupported outcome."
        if decision.confidence < self.validator.min_confidence:
            return "Confidence below threshold."
        if task_type == TASK_GUIDED_UI_TASK and not self._is_read_only_guided_request(goal) and not decision.skill:
            return "Action-oriented guided task is missing a bounded skill."
        return "Decision was not strong enough to use directly."

    def _call_openai_compatible_text(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        payload: Dict[str, Any],
    ) -> Any:
        from openai import OpenAI

        client = OpenAI(
            base_url=base_url,
            api_key=api_key or "local",
            timeout=self.request_timeout_seconds,
        )
        response = client.chat.completions.create(
            model=model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def _call_openai_compatible_vl(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        payload: Dict[str, Any],
        screenshot_path: str,
    ) -> Any:
        from openai import OpenAI

        image_url = self._build_data_url(screenshot_path)
        client = OpenAI(
            base_url=base_url,
            api_key=api_key or "local",
            timeout=self.request_timeout_seconds,
        )
        response = client.chat.completions.create(
            model=model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def _call_openai_compatible_review(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        payload: Dict[str, Any],
        screenshot_path: Optional[str],
    ) -> Any:
        screenshot_file = Path(str(screenshot_path)) if screenshot_path else None
        if screenshot_file and screenshot_file.exists() and self._cloud_review_uses_screenshot(model_name):
            return self._call_openai_compatible_vl(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                payload=payload,
                screenshot_path=str(screenshot_file),
            )
        return self._call_openai_compatible_text(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            payload=payload,
        )

    @staticmethod
    def _cloud_review_uses_screenshot(model_name: str) -> bool:
        override = str(os.environ.get("REASONING_CLOUD_REVIEW_SCREENSHOT", "")).strip().lower()
        if override in {"1", "true", "yes", "on"}:
            return True
        if override in {"0", "false", "no", "off"}:
            return False
        normalized = (model_name or "").strip().lower()
        return "vl" in normalized or "vision" in normalized or normalized == "qwen3.5-plus"

    @staticmethod
    def _build_data_url(screenshot_path: str) -> str:
        file_path = Path(screenshot_path)
        mime_type, _encoding = mimetypes.guess_type(str(file_path))
        mime_type = mime_type or "image/png"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return "data:{0};base64,{1}".format(mime_type, encoded)
