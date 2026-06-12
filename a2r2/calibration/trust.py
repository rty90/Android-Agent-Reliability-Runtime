"""Build a (label, context) trust table from imported runs with ground truth.

v0 scope: consumes `mobilegym_a2r2_summary.json` files (each episode carries
judge ground truth + A2R2 labels). Context is the app (task_id prefix), which is
coarse but honest at current corpus size. Precision proxy is episode-outcome
conditioned: of the episodes where the label fired, how many actually failed.

Promotion discipline (enforced by tiering, documented for humans):
* the table may DOWNGRADE a rule to warn_only automatically;
* UPGRADING to enforcement always additionally requires a human-reviewed
  zero-false-positive replay — the table alone never promotes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "a2r2_trust_table.v1"

DEFAULT_MIN_SAMPLES = 5
DEFAULT_TRUST_THRESHOLD = 0.95
DEFAULT_WARN_THRESHOLD = 0.50


def load_summaries(reports_dir: str) -> List[Dict[str, Any]]:
    base = Path(reports_dir)
    paths = [base] if base.is_file() else sorted(base.rglob("mobilegym_a2r2_summary.json"))
    out: List[Dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        payload["_source_path"] = str(path)
        out.append(payload)
    return out


def _context_of(episode: Dict[str, Any]) -> str:
    task_id = str(episode.get("task_id") or "")
    return task_id.split(".")[0] if "." in task_id else (task_id or "unknown")


def build_trust_table(
    reports_dir: str,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    trust_threshold: float = DEFAULT_TRUST_THRESHOLD,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
) -> Dict[str, Any]:
    summaries = load_summaries(reports_dir)
    cells: Dict[str, Dict[str, Any]] = {}
    episodes_seen = 0
    for summary in summaries:
        for episode in summary.get("imported", []) or []:
            episodes_seen += 1
            failed = not bool(episode.get("mobilegym_success"))
            context = _context_of(episode)
            for label in episode.get("a2r2_failure_labels", []) or []:
                for ctx in (context, "*"):
                    key = "{0}|{1}".format(label, ctx)
                    cell = cells.setdefault(
                        key,
                        {"label": str(label), "context": ctx, "fired": 0, "in_failed": 0, "in_success": 0},
                    )
                    cell["fired"] += 1
                    if failed:
                        cell["in_failed"] += 1
                    else:
                        cell["in_success"] += 1

    for cell in cells.values():
        fired = cell["fired"]
        precision = cell["in_failed"] / float(fired) if fired else None
        cell["precision_vs_episode_failure"] = precision
        if fired < min_samples:
            cell["tier"] = "insufficient"
        elif precision is not None and precision >= trust_threshold:
            cell["tier"] = "trusted_candidate"
        elif precision is not None and precision < warn_threshold:
            cell["tier"] = "warn_only"
        else:
            cell["tier"] = "gray_zone"

    ordered = sorted(cells.values(), key=lambda c: (c["label"], c["context"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "reports_dir": str(reports_dir),
        "episodes_seen": episodes_seen,
        "params": {
            "min_samples": min_samples,
            "trust_threshold": trust_threshold,
            "warn_threshold": warn_threshold,
        },
        "note": (
            "Precision is episode-outcome conditioned (label fired in a failed episode). "
            "trusted_candidate still requires a human-reviewed zero-FP replay before enforcement."
        ),
        "cells": ordered,
    }


def render_markdown(table: Dict[str, Any]) -> str:
    lines = [
        "# A2R2 Trust Table",
        "",
        "Per (label, context) measured trust. Counting only — no training. The table",
        "may downgrade rules automatically; promotion to enforcement additionally",
        "requires a human-reviewed zero-FP replay.",
        "",
        "- Episodes: {0}".format(table.get("episodes_seen")),
        "- Params: {0}".format(table.get("params")),
        "",
        "| Label | Context | Fired | In failed | In success | Precision | Tier |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for cell in table.get("cells", []):
        precision = cell.get("precision_vs_episode_failure")
        lines.append(
            "| {0} | {1} | {2} | {3} | {4} | {5} | {6} |".format(
                cell.get("label"),
                cell.get("context"),
                cell.get("fired"),
                cell.get("in_failed"),
                cell.get("in_success"),
                "{0:.0%}".format(precision) if precision is not None else "n/a",
                cell.get("tier"),
            )
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Build the A2R2 trust table from imported runs.")
    parser.add_argument("--reports-dir", default="data/reports")
    parser.add_argument("--out", default="data/reports/a2r2_trust_table.md")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    args = parser.parse_args(argv)

    table = build_trust_table(args.reports_dir, min_samples=args.min_samples)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(table), encoding="utf-8")
    out_path.with_suffix(".json").write_text(
        json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("trust_table_markdown={0}".format(out_path))
    print("episodes={0} cells={1}".format(table.get("episodes_seen"), len(table.get("cells", []))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
