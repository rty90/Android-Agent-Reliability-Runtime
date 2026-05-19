from __future__ import annotations

import argparse
import html
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from a2r2.reports.scorecard import generate_scorecard, load_episode_summaries, load_trace_steps


def build_view_model(trace_dir: str, out_path: Optional[str] = None, latest: int = 200) -> Dict[str, Any]:
    root = Path(trace_dir)
    steps = load_trace_steps(root)
    steps.sort(key=lambda item: str((item.get("timestamps") or {}).get("recorded_at") or ""))
    if latest > 0:
        steps = steps[-latest:]
    summaries = {
        str(summary.get("episode_id") or ""): summary
        for summary in load_episode_summaries(root)
        if summary.get("episode_id")
    }

    output_dir = Path(out_path).resolve().parent if out_path else Path.cwd()
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for step in steps:
        grouped[str(step.get("episode_id") or "unknown_episode")].append(step)

    episodes: List[Dict[str, Any]] = []
    for episode_id, episode_steps in grouped.items():
        compact_steps = [_compact_step(step, output_dir) for step in sorted(episode_steps, key=lambda item: int(item.get("step_index") or 0))]
        summary = summaries.get(episode_id, {})
        labels = sorted(
            {
                label
                for step in compact_steps
                for label in (step.get("decision_label"), step.get("progress_label"), step.get("diagnosis_label"))
                if label
            }
        )
        episodes.append(
            {
                "episode_id": episode_id,
                "goal": summary.get("goal") or (compact_steps[0].get("goal") if compact_steps else ""),
                "agent_name": summary.get("agent_name") or _first_agent_name(episode_steps),
                "runtime_mode": summary.get("runtime_mode") or _first_runtime_mode(episode_steps),
                "final_success": summary.get("final_success"),
                "blocked_actions": sum(1 for step in compact_steps if not step.get("allowed")),
                "false_success": any(bool(step.get("false_success_candidate")) for step in compact_steps),
                "failure_labels": labels,
                "steps": compact_steps,
            }
        )
    episodes.sort(key=lambda item: (item["episode_id"]))

    counts = Counter()
    for episode in episodes:
        for step in episode["steps"]:
            if not step.get("allowed"):
                counts["blocked"] += 1
            if step.get("decision_label"):
                counts[str(step["decision_label"])] += 1
            if step.get("progress_label"):
                counts[str(step["progress_label"])] += 1
            if step.get("progress_made"):
                counts["progress_made"] += 1

    return {
        "trace_dir": str(root),
        "generated_at": _now_label(),
        "scorecard": generate_scorecard(str(root)),
        "totals": {
            "episodes": len(episodes),
            "steps": sum(len(episode["steps"]) for episode in episodes),
            "blocked": counts["blocked"],
            "progress_made": counts["progress_made"],
            "labels": dict(counts),
        },
        "episodes": episodes,
    }


def render_html(model: Mapping[str, Any]) -> str:
    payload = json.dumps(model, ensure_ascii=False, sort_keys=True)
    escaped_payload = html.escape(payload, quote=False)
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A2R2 Trace Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17202a;
      --muted: #5f6f7e;
      --line: #d9e0e6;
      --surface: #ffffff;
      --band: #f5f7f8;
      --ok: #116b5f;
      --ok-soft: #dff2ed;
      --warn: #9a5a00;
      --warn-soft: #fff1d6;
      --bad: #a33636;
      --bad-soft: #ffe1e1;
      --info: #245b8f;
      --info-soft: #e4f0fb;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--band);
      font: 14px/1.45 "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    header {
      background: #15202b;
      color: #f8fbfd;
      padding: 28px 32px 24px;
      border-bottom: 4px solid #2b7a78;
    }
    h1 { margin: 0 0 8px; font-size: 30px; font-weight: 700; letter-spacing: 0; }
    header p { margin: 0; color: #c7d3dc; max-width: 980px; }
    main { max-width: 1280px; margin: 0 auto; padding: 24px; }
    .toolbar {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto auto;
      gap: 12px;
      align-items: center;
      margin-bottom: 18px;
    }
    input, select {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: white;
      color: var(--ink);
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric, .episode {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .metric { padding: 14px; }
    .metric .value { font-size: 24px; font-weight: 700; margin-bottom: 2px; }
    .metric .label { color: var(--muted); }
    .episode { margin: 14px 0; overflow: hidden; }
    .episode-header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }
    .episode-title { font-weight: 700; overflow-wrap: anywhere; }
    .episode-subtitle { color: var(--muted); margin-top: 3px; overflow-wrap: anywhere; }
    .episode-body { padding: 10px 16px 16px; }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end; }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #f9fbfc;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .chip.ok { color: var(--ok); background: var(--ok-soft); border-color: #b8ddd4; }
    .chip.warn { color: var(--warn); background: var(--warn-soft); border-color: #edcf91; }
    .chip.bad { color: var(--bad); background: var(--bad-soft); border-color: #f1b8b8; }
    .step {
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 12px;
      background: white;
      overflow: hidden;
    }
    .step-heading {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      background: #f7f9fa;
      border-bottom: 1px solid var(--line);
    }
    .step-index {
      display: inline-grid;
      place-items: center;
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background: #20313f;
      color: white;
      font-weight: 700;
    }
    .process {
      display: grid;
      grid-template-columns: repeat(5, minmax(140px, 1fr));
      gap: 10px;
      padding: 14px 12px;
    }
    .stage {
      min-height: 128px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #ffffff;
      position: relative;
    }
    .stage::after {
      content: "";
      position: absolute;
      top: 26px;
      right: -10px;
      width: 10px;
      border-top: 2px solid var(--line);
    }
    .stage:last-child::after { display: none; }
    .stage-title {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 6px;
    }
    .stage-main { font-weight: 700; overflow-wrap: anywhere; }
    .stage-detail { color: var(--muted); margin-top: 6px; overflow-wrap: anywhere; }
    .stage.ok { border-color: #b8ddd4; background: var(--ok-soft); }
    .stage.warn { border-color: #edcf91; background: var(--warn-soft); }
    .stage.bad { border-color: #f1b8b8; background: var(--bad-soft); }
    .evidence {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
    }
    details {
      padding: 0 12px 12px;
    }
    summary {
      cursor: pointer;
      color: var(--info);
      font-weight: 600;
    }
    pre {
      max-height: 320px;
      overflow: auto;
      background: #101820;
      color: #e7eef5;
      border-radius: 6px;
      padding: 12px;
      font-size: 12px;
    }
    .shot-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 0 12px 12px;
    }
    .shot {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #f9fbfc;
    }
    .shot div { padding: 8px 10px; color: var(--muted); }
    .shot img {
      display: block;
      width: 100%;
      max-height: 360px;
      object-fit: contain;
      background: #111;
    }
    .empty {
      padding: 36px;
      text-align: center;
      color: var(--muted);
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    @media (max-width: 920px) {
      .toolbar, .summary-grid, .process, .shot-row { grid-template-columns: 1fr; }
      .episode-header, .step-heading { grid-template-columns: 1fr; }
      .chips { justify-content: flex-start; }
      .stage::after { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>A2R2 Trace Viewer</h1>
    <p>Visualizes the reliability middleware loop: observe the UI, inspect the proposed action, gate it, verify progress, and preserve the diagnosis.</p>
  </header>
  <main>
    <section class="toolbar">
      <input id="search" type="search" placeholder="Search goal, episode, action, label">
      <select id="filter">
        <option value="all">All decisions</option>
        <option value="blocked">Blocked / handoff</option>
        <option value="allowed">Allowed</option>
        <option value="unsafe_action">Unsafe action</option>
        <option value="non_ready_action">Non-ready</option>
        <option value="false_success">False success</option>
      </select>
      <select id="sort">
        <option value="episode">Sort by episode</option>
        <option value="blocked">Blocked first</option>
      </select>
    </section>
    <section id="summary" class="summary-grid"></section>
    <section id="episodes"></section>
  </main>
  <script id="trace-data" type="application/json">""" + escaped_payload + """</script>
  <script>
    const model = JSON.parse(document.getElementById('trace-data').textContent);
    const state = { search: '', filter: 'all', sort: 'episode' };
    const byId = (id) => document.getElementById(id);

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function chip(text, kind='') {
      if (!text) return '';
      return `<span class="chip ${kind}">${escapeHtml(text)}</span>`;
    }
    function metric(value, label) {
      return `<div class="metric"><div class="value">${escapeHtml(value)}</div><div class="label">${escapeHtml(label)}</div></div>`;
    }
    function labelKind(label) {
      if (!label) return '';
      if (['unsafe_action', 'false_success', 'stuck_loop'].includes(label)) return 'bad';
      if (['non_ready_action', 'modal_blocker', 'blank_webview', 'no_progress'].includes(label)) return 'warn';
      return 'ok';
    }
    function stageClass(step, name) {
      if (name === 'decision') return step.allowed ? 'ok' : (step.decision_label === 'unsafe_action' ? 'bad' : 'warn');
      if (name === 'verify') return step.progress_made ? 'ok' : 'warn';
      if (name === 'diagnosis') return labelKind(step.diagnosis_label || step.decision_label || step.progress_label);
      return '';
    }
    function evidenceList(items) {
      const values = (items || []).slice(0, 4);
      if (!values.length) return '';
      return `<ul class="evidence">${values.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
    }
    function imageBlock(title, src) {
      if (!src) return '';
      return `<div class="shot"><div>${escapeHtml(title)}</div><img src="${escapeHtml(src)}" alt="${escapeHtml(title)}"></div>`;
    }
    function stepMatches(step, episode) {
      const haystack = [
        episode.episode_id, episode.goal, step.goal, step.action_label, step.decision,
        step.decision_label, step.progress_label, step.diagnosis_label, step.before_page, step.after_page
      ].join(' ').toLowerCase();
      if (state.search && !haystack.includes(state.search.toLowerCase())) return false;
      if (state.filter === 'blocked' && step.allowed) return false;
      if (state.filter === 'allowed' && !step.allowed) return false;
      if (state.filter !== 'all' && !['blocked', 'allowed'].includes(state.filter)) {
        const labels = [step.decision_label, step.progress_label, step.diagnosis_label].filter(Boolean);
        if (!labels.includes(state.filter)) return false;
      }
      return true;
    }
    function renderSummary(visibleEpisodes) {
      const steps = visibleEpisodes.flatMap(ep => ep.steps);
      byId('summary').innerHTML = [
        metric(visibleEpisodes.length, 'Episodes visible'),
        metric(steps.length, 'Steps visible'),
        metric(steps.filter(step => !step.allowed).length, 'Blocked or handoff'),
        metric(steps.filter(step => step.progress_made).length, 'Progress made')
      ].join('');
    }
    function renderStep(step) {
      const chips = [
        chip(step.allowed ? 'allowed' : 'blocked', step.allowed ? 'ok' : 'warn'),
        chip(step.decision_label, labelKind(step.decision_label)),
        chip(step.progress_label, labelKind(step.progress_label)),
        chip(step.false_success_candidate ? 'false success candidate' : '', 'bad')
      ].join('');
      return `<article class="step">
        <div class="step-heading">
          <span class="step-index">${escapeHtml(step.step_index)}</span>
          <div>
            <div class="episode-title">${escapeHtml(step.action_label || 'action')}</div>
            <div class="episode-subtitle">${escapeHtml(step.recorded_at || '')}</div>
          </div>
          <div class="chips">${chips}</div>
        </div>
        <div class="process">
          <div class="stage ${stageClass(step, 'before')}">
            <div class="stage-title">Before Observation</div>
            <div class="stage-main">${escapeHtml(step.before_page || 'unknown page')}</div>
            <div class="stage-detail">${escapeHtml(step.before_package || '')}</div>
          </div>
          <div class="stage">
            <div class="stage-title">Proposed Action</div>
            <div class="stage-main">${escapeHtml(step.action_label)}</div>
            <div class="stage-detail">${escapeHtml(step.target_label || '')}</div>
          </div>
          <div class="stage ${stageClass(step, 'decision')}">
            <div class="stage-title">Gate Decision</div>
            <div class="stage-main">${escapeHtml(step.decision)}</div>
            <div class="stage-detail">${escapeHtml(step.decision_reason || '')}</div>
            ${evidenceList(step.decision_evidence)}
          </div>
          <div class="stage ${stageClass(step, 'verify')}">
            <div class="stage-title">Progress Verification</div>
            <div class="stage-main">${step.progress_made ? 'progress made' : 'no progress'}</div>
            <div class="stage-detail">ui=${escapeHtml(step.ui_changed)} xml=${escapeHtml(step.xml_changed)} screenshot=${escapeHtml(step.screenshot_changed)}</div>
            ${evidenceList(step.progress_evidence)}
          </div>
          <div class="stage ${stageClass(step, 'diagnosis')}">
            <div class="stage-title">Diagnosis</div>
            <div class="stage-main">${escapeHtml(step.diagnosis_label || step.decision_label || step.progress_label || 'none')}</div>
            <div class="stage-detail">${escapeHtml(step.policy_name || '')}</div>
          </div>
        </div>
        <div class="shot-row">${imageBlock('Before screenshot', step.before_screenshot)}${imageBlock('After screenshot', step.after_screenshot)}</div>
        <details><summary>Raw step JSON</summary><pre>${escapeHtml(JSON.stringify(step.raw, null, 2))}</pre></details>
      </article>`;
    }
    function renderEpisode(episode) {
      const steps = episode.steps.filter(step => stepMatches(step, episode));
      if (!steps.length) return '';
      return `<section class="episode">
        <div class="episode-header">
          <div>
            <div class="episode-title">${escapeHtml(episode.episode_id)}</div>
            <div class="episode-subtitle">${escapeHtml(episode.goal || 'No goal recorded')}</div>
          </div>
          <div class="chips">
            ${chip(`${steps.length} steps`)}
            ${chip(`${episode.blocked_actions || 0} blocked`, episode.blocked_actions ? 'warn' : '')}
            ${(episode.failure_labels || []).map(label => chip(label, labelKind(label))).join('')}
          </div>
        </div>
        <div class="episode-body">${steps.map(renderStep).join('')}</div>
      </section>`;
    }
    function render() {
      let episodes = model.episodes.map(ep => ({...ep, steps: [...ep.steps]}));
      if (state.sort === 'blocked') {
        episodes.sort((a, b) => (b.blocked_actions || 0) - (a.blocked_actions || 0));
      }
      const visible = episodes
        .map(ep => ({...ep, steps: ep.steps.filter(step => stepMatches(step, ep))}))
        .filter(ep => ep.steps.length);
      renderSummary(visible);
      byId('episodes').innerHTML = visible.map(renderEpisode).join('') || '<div class="empty">No trace steps match the current filters.</div>';
    }
    byId('search').addEventListener('input', event => { state.search = event.target.value; render(); });
    byId('filter').addEventListener('change', event => { state.filter = event.target.value; render(); });
    byId('sort').addEventListener('change', event => { state.sort = event.target.value; render(); });
    render();
  </script>
</body>
</html>
"""


def write_viewer(trace_dir: str, out_path: str, latest: int = 200) -> Dict[str, Any]:
    model = build_view_model(trace_dir=trace_dir, out_path=out_path, latest=latest)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(model), encoding="utf-8")
    return {
        "out_path": str(target),
        "trace_dir": trace_dir,
        "episodes": model["totals"]["episodes"],
        "steps": model["totals"]["steps"],
    }


def _compact_step(step: Mapping[str, Any], output_dir: Path) -> Dict[str, Any]:
    before = step.get("before_state") if isinstance(step.get("before_state"), Mapping) else {}
    after = step.get("after_state") if isinstance(step.get("after_state"), Mapping) else {}
    before_meta = before.get("metadata") if isinstance(before.get("metadata"), Mapping) else {}
    after_meta = after.get("metadata") if isinstance(after.get("metadata"), Mapping) else {}
    action = step.get("proposed_action") if isinstance(step.get("proposed_action"), Mapping) else {}
    decision = step.get("runtime_decision") if isinstance(step.get("runtime_decision"), Mapping) else {}
    verification = step.get("progress_verification") if isinstance(step.get("progress_verification"), Mapping) else {}
    diagnosis = step.get("diagnosis") if isinstance(step.get("diagnosis"), Mapping) else {}
    timestamps = step.get("timestamps") if isinstance(step.get("timestamps"), Mapping) else {}
    action_type = str(action.get("action_type") or "")
    target_label = str(action.get("target_text") or action.get("target_resource_id") or "").strip()
    decision_label = decision.get("diagnosis_label")
    progress_label = verification.get("diagnosis_label")
    diagnosis_label = diagnosis.get("label") or decision_label or progress_label
    if not bool(decision.get("allowed")) and decision_label:
        diagnosis_label = decision_label
    return {
        "episode_id": step.get("episode_id"),
        "step_index": int(step.get("step_index") or 0),
        "goal": step.get("goal"),
        "before_page": before_meta.get("page") or before.get("activity") or "unknown",
        "after_page": after_meta.get("page") or after.get("activity") or "unknown",
        "before_package": before.get("package") or before_meta.get("app"),
        "after_package": after.get("package") or after_meta.get("app"),
        "before_screenshot": _relative_asset(before.get("screenshot_path"), output_dir),
        "after_screenshot": _relative_asset(after.get("screenshot_path"), output_dir),
        "action_label": action_type if not target_label else "{0} -> {1}".format(action_type, target_label),
        "target_label": target_label,
        "decision": decision.get("decision"),
        "allowed": bool(decision.get("allowed")),
        "decision_label": decision_label,
        "decision_reason": decision.get("reason"),
        "decision_evidence": list(decision.get("evidence") or [])[:8],
        "policy_name": decision.get("policy_name"),
        "progress_made": bool(verification.get("progress_made")),
        "progress_label": progress_label,
        "progress_evidence": list(verification.get("evidence") or [])[:8],
        "ui_changed": verification.get("ui_changed"),
        "xml_changed": verification.get("xml_changed"),
        "screenshot_changed": verification.get("screenshot_changed"),
        "false_success_candidate": bool(verification.get("false_success_candidate")),
        "diagnosis_label": diagnosis_label,
        "recorded_at": timestamps.get("recorded_at"),
        "raw": dict(step),
    }


def _relative_asset(path_value: Any, output_dir: Path) -> str:
    if not path_value:
        return ""
    path = Path(str(path_value))
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        return ""
    try:
        return Path(os.path.relpath(str(path), str(output_dir))).as_posix()
    except ValueError:
        return path.as_uri()


def _first_agent_name(steps: Iterable[Mapping[str, Any]]) -> str:
    for step in steps:
        meta = step.get("agent_meta") if isinstance(step.get("agent_meta"), Mapping) else {}
        if meta.get("agent_name"):
            return str(meta["agent_name"])
    return ""


def _first_runtime_mode(steps: Iterable[Mapping[str, Any]]) -> str:
    for step in steps:
        meta = step.get("agent_meta") if isinstance(step.get("agent_meta"), Mapping) else {}
        if meta.get("runtime_mode"):
            return str(meta["runtime_mode"])
    return ""


def _now_label() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a static HTML visualization for A2R2 trace.v1 episodes.")
    parser.add_argument("--trace-dir", default="data/traces", help="Directory containing trace.v1 episodes.")
    parser.add_argument("--out", default="data/reports/a2r2_trace_viewer.html", help="HTML output path.")
    parser.add_argument("--latest", type=int, default=200, help="Maximum newest steps to embed. Use 0 for all.")
    args = parser.parse_args(argv)

    result = write_viewer(trace_dir=args.trace_dir, out_path=args.out, latest=args.latest)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
