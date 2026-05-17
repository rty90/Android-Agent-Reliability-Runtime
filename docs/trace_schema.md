# trace.v1 Schema

`trace.v1` is a JSONL schema. Each line is one runtime step. Binary artifacts
such as screenshots and XML dumps are referenced by path, not embedded.

## Step Fields

- `schema_version`: always `trace.v1`.
- `trace_id`: stable id for this trace recording session.
- `episode_id`: stable id for the wrapped task episode.
- `step_index`: 1-based step number.
- `goal`: original task goal supplied to the external agent.
- `agent_meta`: metadata about the wrapped agent and runtime mode.
- `before_state`: observation before the proposed action.
- `proposed_action`: action proposed by the external agent.
- `runtime_decision`: A2R2 pre-action gate result.
- `after_state`: observation after execution or simulated fallback.
- `progress_verification`: A2R2 post-action verification.
- `diagnosis`: compact labels derived from decision and verification.
- `timestamps`: observation and record timestamps.
- `artifacts_dir`: directory for optional screenshots/XML files.

## Example

```json
{
  "schema_version": "trace.v1",
  "trace_id": "trace_abc123",
  "episode_id": "episode_demo",
  "step_index": 1,
  "goal": "open an app and search",
  "agent_meta": {"agent_name": "dummy_external_agent"},
  "before_state": {
    "screenshot_path": null,
    "xml_path": null,
    "activity": null,
    "package": null,
    "ui_tree_hash": "ready",
    "timestamp": "2026-05-13T00:00:00+00:00",
    "metadata": {"visible_text": ["Search"]}
  },
  "proposed_action": {
    "action_type": "tap",
    "x": null,
    "y": null,
    "target_text": "Search",
    "target_resource_id": null,
    "raw": null
  },
  "runtime_decision": {
    "decision": "allow",
    "allowed": true,
    "reason": "The proposed action passed A2R2 v0.1 gates.",
    "diagnosis_label": null,
    "evidence": [],
    "fallback_action": null,
    "confidence": 0.9,
    "policy_name": "ReliabilityRuntime"
  },
  "after_state": {"ui_tree_hash": "results"},
  "progress_verification": {
    "progress_made": true,
    "ui_changed": true,
    "xml_changed": null,
    "screenshot_changed": null,
    "agent_claimed_success": false,
    "false_success_candidate": false,
    "diagnosis_label": null,
    "evidence": ["ui_tree_hash_changed"]
  },
  "diagnosis": {"label": null, "decision_label": null, "progress_label": null},
  "timestamps": {"recorded_at": "2026-05-13T00:00:01+00:00"},
  "artifacts_dir": "data/traces/episode_demo/artifacts"
}
```

## Episode Summary

`episode_summary.json` stores episode-level rollups:

- `episode_id`
- `goal`
- `agent_name`
- `runtime_mode`
- `final_success`
- `agent_claimed_success`
- `false_success`
- `failure_labels`
- `steps_total`
- `blocked_actions`
- `stuck_loop_detected`
- `trace_complete`
