"""Compare MobileGym ground-truth outcomes against A2R2 detected labels.

Reads one or more `mobilegym_a2r2_summary.json` files produced by
`a2r2.adapters.mobilegym.import_mobilegym_run` and produces a benchmark report
that puts MobileGym's external ground truth next to A2R2's process-level labels.

The point is to answer, on external ground truth: can A2R2 detect reliability
failures (false success, side effects, no progress / stuck loops) from a recorded
trajectory, without being the agent?

No numbers are fabricated. Everything is derived from imported run data; metrics
with no direct MobileGym ground truth are reported as `n/a` rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

SCHEMA_VERSION = "a2r2_mobilegym_benchmark.v1"

# A2R2 labels that correspond (approximately) to a MobileGym side-effect concern.
_SIDE_EFFECT_LABELS = frozenset({"unsafe_action"})
_NO_PROGRESS_LABELS = frozenset({"no_progress", "stuck_loop", "loading_loop"})


def load_summaries(reports_dir: str) -> List[Dict[str, Any]]:
    base = Path(reports_dir)
    paths: List[Path]
    if base.is_file():
        paths = [base]
    else:
        paths = sorted(base.rglob("mobilegym_a2r2_summary.json"))
    summaries: List[Dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_source_path"] = str(path)
        summaries.append(payload)
    return summaries


def _episodes(summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    episodes: List[Dict[str, Any]] = []
    for summary in summaries:
        for item in summary.get("imported", []) or []:
            if isinstance(item, Mapping):
                episodes.append(dict(item))
    return episodes


def _has_label(episode: Mapping[str, Any], labels: frozenset) -> bool:
    return any(str(label) in labels for label in episode.get("a2r2_failure_labels", []) or [])


def _confusion(episodes: List[Dict[str, Any]], gt_key: str, detect) -> Dict[str, Any]:
    tp = fn = fp = tn = 0
    for ep in episodes:
        gt = bool(ep.get(gt_key))
        det = bool(detect(ep))
        if gt and det:
            tp += 1
        elif gt and not det:
            fn += 1
        elif not gt and det:
            fp += 1
        else:
            tn += 1
    recall = tp / float(tp + fn) if (tp + fn) else None
    precision = tp / float(tp + fp) if (tp + fp) else None
    return {
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "ground_truth": tp + fn,
        "detected": tp + fp,
        "recall": recall,
        "precision": precision,
    }


def build_benchmark(reports_dir: str) -> Dict[str, Any]:
    summaries = load_summaries(reports_dir)
    episodes = _episodes(summaries)
    total = len(episodes)

    false_complete = _confusion(episodes, "mobilegym_false_complete", lambda e: e.get("a2r2_false_success"))
    side_effects = _confusion(
        episodes, "mobilegym_unexpected_side_effects", lambda e: _has_label(e, _SIDE_EFFECT_LABELS)
    )
    overdue = _confusion(
        episodes,
        "mobilegym_overdue_termination",
        lambda e: e.get("a2r2_no_termination_after_progress"),
    )
    no_progress_detected = sum(1 for e in episodes if _has_label(e, _NO_PROGRESS_LABELS))
    episodes_total_declared = sum(int(s.get("episodes_total") or 0) for s in summaries)
    episodes_imported = sum(int(s.get("episodes_imported") or 0) for s in summaries)
    episodes_infra_error = sum(int(s.get("episodes_infra_error") or 0) for s in summaries)

    return {
        "schema_version": SCHEMA_VERSION,
        "reports_dir": str(reports_dir),
        "runs": len(summaries),
        "episodes_compared": total,
        "episodes_infra_error_excluded": episodes_infra_error,
        "metrics": {
            "false_complete": {
                "ground_truth": false_complete["ground_truth"],
                "a2r2_detected": false_complete["detected"],
                "confusion": false_complete,
                "note": "A2R2 false_success vs MobileGym false_complete",
            },
            "unexpected_side_effects": {
                "ground_truth": side_effects["ground_truth"],
                "a2r2_detected": side_effects["detected"],
                "confusion": side_effects,
                "note": "A2R2 risk labels ({0}); mapping is approximate".format(
                    ", ".join(sorted(_SIDE_EFFECT_LABELS))
                ),
            },
            "overdue_termination": {
                "ground_truth": overdue["ground_truth"],
                "a2r2_detected": overdue["detected"],
                "confusion": overdue,
                "note": "A2R2 no-termination-after-progress vs MobileGym OT (progress=1 without success); A2R2 signal is broader by design",
            },
            "no_progress_or_stuck": {
                "ground_truth": None,
                "a2r2_detected": no_progress_detected,
                "note": "MobileGym exposes no direct ground truth; reported as n/a",
            },
            "trace_coverage": {
                "ground_truth": episodes_total_declared,
                "a2r2_detected": episodes_imported,
                "note": "episodes with an A2R2 trace.v1 written",
            },
        },
        "episodes": episodes,
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return "{0:.0%}".format(value)
    return str(value)


def render_markdown(bench: Dict[str, Any]) -> str:
    m = bench.get("metrics", {})
    fc = m.get("false_complete", {})
    fc_conf = fc.get("confusion", {})
    se = m.get("unexpected_side_effects", {})
    se_conf = se.get("confusion", {})
    ot = m.get("overdue_termination", {})
    ot_conf = ot.get("confusion", {})
    np_ = m.get("no_progress_or_stuck", {})
    tc = m.get("trace_coverage", {})

    lines = [
        "# A2R2 vs MobileGym Reliability Benchmark v0.1",
        "",
        "External ground truth (MobileGym judge) next to A2R2 process-level labels.",
        "All numbers are derived from imported MobileGym runs; none are fabricated.",
        "",
        "- Runs compared: {0}".format(bench.get("runs")),
        "- Episodes compared: {0}".format(bench.get("episodes_compared")),
        "- Infra-error episodes excluded (no agent decisions): {0}".format(
            bench.get("episodes_infra_error_excluded", 0)
        ),
        "",
        "| Metric | MobileGym Ground Truth | A2R2 Detected | Notes |",
        "|---|---:|---:|---|",
        "| False Complete Episodes | {0} | {1} | recall {2}, precision {3} (TP {4} / FN {5} / FP {6}) |".format(
            _fmt(fc.get("ground_truth")), _fmt(fc.get("a2r2_detected")),
            _fmt(fc_conf.get("recall")), _fmt(fc_conf.get("precision")),
            fc_conf.get("tp"), fc_conf.get("fn"), fc_conf.get("fp"),
        ),
        "| Unexpected Side Effects | {0} | {1} | {2} |".format(
            _fmt(se.get("ground_truth")), _fmt(se.get("a2r2_detected")),
            "{0}; recall {1}, precision {2} (TP {3} / FN {4} / FP {5})".format(
                se.get("note"),
                _fmt(se_conf.get("recall")),
                _fmt(se_conf.get("precision")),
                se_conf.get("tp"),
                se_conf.get("fn"),
                se_conf.get("fp"),
            ),
        ),
        "| Overdue Termination | {0} | {1} | {2} |".format(
            _fmt(ot.get("ground_truth")), _fmt(ot.get("a2r2_detected")),
            "recall {0}, precision {1} (TP {2} / FN {3} / FP {4})".format(
                _fmt(ot_conf.get("recall")), _fmt(ot_conf.get("precision")),
                ot_conf.get("tp"), ot_conf.get("fn"), ot_conf.get("fp"),
            ),
        ),
        "| No Progress / Stuck Loop | {0} | {1} | {2} |".format(
            _fmt(np_.get("ground_truth")), _fmt(np_.get("a2r2_detected")), np_.get("note"),
        ),
        "| Trace Coverage | {0} | {1} | {2} |".format(
            _fmt(tc.get("ground_truth")), _fmt(tc.get("a2r2_detected")), tc.get("note"),
        ),
        "",
        "## False complete vs A2R2 false_success (per episode)",
        "",
        "| Task | Trial | MobileGym FC | A2R2 false_success | A2R2 labels |",
        "|---|---:|---:|---:|---|",
    ]
    for ep in bench.get("episodes", []):
        lines.append(
            "| {0} | {1} | {2} | {3} | `{4}` |".format(
                ep.get("task_id"),
                ep.get("trial_id"),
                bool(ep.get("mobilegym_false_complete")),
                bool(ep.get("a2r2_false_success")),
                ep.get("a2r2_failure_labels") or [],
            )
        )
    if not bench.get("episodes"):
        lines.append("| (no imported episodes yet) | | | | |")
    lines.extend([
        "",
        "## Side effects vs A2R2 risk labels (per episode)",
        "",
        "| Task | Trial | MobileGym USE | A2R2 risk label | A2R2 labels |",
        "|---|---:|---:|---:|---|",
    ])
    for ep in bench.get("episodes", []):
        lines.append(
            "| {0} | {1} | {2} | {3} | `{4}` |".format(
                ep.get("task_id"),
                ep.get("trial_id"),
                bool(ep.get("mobilegym_unexpected_side_effects")),
                _has_label(ep, _SIDE_EFFECT_LABELS),
                ep.get("a2r2_failure_labels") or [],
            )
        )
    if not bench.get("episodes"):
        lines.append("| (no imported episodes yet) | | | | |")
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Benchmark A2R2 labels against MobileGym ground truth.")
    parser.add_argument(
        "--reports-dir",
        default="data/reports/mobilegym_import",
        help="Dir (scanned recursively) or file with mobilegym_a2r2_summary.json.",
    )
    parser.add_argument(
        "--out",
        default="data/reports/mobilegym_benchmark_v0.1.md",
        help="Markdown output path (also writes .json). Point at docs/ for the real deliverable.",
    )
    args = parser.parse_args(argv)

    bench = build_benchmark(args.reports_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(bench), encoding="utf-8")
    out_path.with_suffix(".json").write_text(
        json.dumps(bench, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("mobilegym_benchmark_markdown={0}".format(out_path))
    print(
        "episodes={0} false_complete_gt={1} a2r2_false_success={2}".format(
            bench.get("episodes_compared"),
            bench["metrics"]["false_complete"]["ground_truth"],
            bench["metrics"]["false_complete"]["a2r2_detected"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
