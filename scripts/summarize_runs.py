from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOTS = (
    Path("data/tmp/chaos"),
    Path("data/tmp/chaos_e2e"),
    Path("data/tmp/long_tail"),
    Path("data/tmp/diagnostics"),
)
REPORT_NAMES = {
    "report.json",
    "e2e_report.json",
    "long_tail_report.json",
    "diagnostic.json",
}
FAIL_STATUSES = {"fail", "failed", "error", "timeout"}


@dataclass
class RunRecord(object):
    report_type: str
    case: str
    status: str
    reason: str
    report_path: str
    artifacts_dir: str
    modified_at: str
    skill: str = ""
    backend: str = ""
    readiness_status: str = ""
    readiness_label: str = ""
    failure_label: str = ""
    false_success_risk: int = 0
    passed: int = 0
    failed: int = 0
    subcase_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _status(value: Any) -> str:
    normalized = _as_text(value).lower()
    return normalized or "unknown"


def _file_modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _decision_from(payload: Mapping[str, Any]) -> Dict[str, Any]:
    decision = payload.get("decision")
    return dict(decision) if isinstance(decision, Mapping) else {}


def _readiness_from(payload: Mapping[str, Any]) -> Dict[str, Any]:
    direct = payload.get("readiness")
    if isinstance(direct, Mapping):
        return dict(direct)
    ui_state = payload.get("ui_state")
    if isinstance(ui_state, Mapping) and isinstance(ui_state.get("readiness"), Mapping):
        return dict(ui_state["readiness"])
    return {}


def _primary_blocker_label(payload: Mapping[str, Any]) -> str:
    ui_state = payload.get("ui_state")
    if isinstance(ui_state, Mapping):
        primary = ui_state.get("primary_blocker")
        if isinstance(primary, Mapping):
            return _as_text(primary.get("type"))
    readiness = _readiness_from(payload)
    label = _as_text(readiness.get("label"))
    if readiness.get("status") in {"blocked", "loading", "uncertain"}:
        return label
    return ""


def _report_type(path: Path, payload: Mapping[str, Any]) -> str:
    if path.name == "long_tail_report.json":
        return "long_tail"
    if path.name == "e2e_report.json":
        return "chaos_e2e"
    if path.name == "diagnostic.json" or payload.get("schema_version") == "agent.diagnostic.v1":
        return "diagnostic"
    return "chaos"


def _long_tail_record(path: Path, payload: Mapping[str, Any]) -> RunRecord:
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    status = _status(payload.get("status"))
    failed = int(payload.get("failed") or sum(1 for item in results if _status((item or {}).get("status")) in FAIL_STATUSES))
    passed = int(payload.get("passed") or sum(1 for item in results if _status((item or {}).get("status")) == "pass"))
    readiness_counter: Counter[str] = Counter()
    failure_counter: Counter[str] = Counter()
    skill_counter: Counter[str] = Counter()
    false_success_risk = 0
    first_reason = _as_text(payload.get("reason"))

    for item in results:
        if not isinstance(item, Mapping):
            continue
        readiness = _readiness_from(item)
        label = _as_text(readiness.get("label"))
        status_value = _as_text(readiness.get("status"))
        if status_value:
            readiness_counter[status_value] += 1
        if label and status_value in {"blocked", "loading", "uncertain"}:
            failure_counter[label] += 1
        decision = _decision_from(item)
        skill = _as_text(decision.get("skill")) or "<complete>"
        skill_counter[skill] += 1
        if _status(item.get("status")) == "pass" and status_value in {"blocked", "loading", "uncertain"} and skill == "<complete>":
            false_success_risk += 1
        if not first_reason and _status(item.get("status")) in FAIL_STATUSES:
            first_reason = _as_text(item.get("reason"))

    readiness_status = readiness_counter.most_common(1)[0][0] if readiness_counter else ""
    failure_label = failure_counter.most_common(1)[0][0] if failure_counter else ""
    skill = ", ".join("{0}:{1}".format(key, value) for key, value in skill_counter.most_common(3))
    return RunRecord(
        report_type="long_tail",
        case=_as_text(payload.get("case")) or "long_tail_agent_smoke",
        status=status,
        reason=first_reason,
        report_path=str(path),
        artifacts_dir=_as_text(payload.get("artifacts_dir")) or str(path.parent),
        modified_at=_file_modified_at(path),
        skill=skill,
        backend=_as_text(payload.get("profile")),
        readiness_status=readiness_status,
        readiness_label=failure_label,
        failure_label=failure_label,
        false_success_risk=false_success_risk,
        passed=passed,
        failed=failed,
        subcase_count=len(results),
    )


def normalize_report(path: Path) -> Optional[RunRecord]:
    payload = _load_json(path)
    if payload is None:
        return None
    report_type = _report_type(path, payload)
    if report_type == "long_tail":
        return _long_tail_record(path, payload)

    decision = _decision_from(payload)
    readiness = _readiness_from(payload)
    failure_label = _primary_blocker_label(payload)
    status = _status(payload.get("status"))
    if report_type == "diagnostic":
        failure_label = _as_text(payload.get("kind")) or failure_label
    return RunRecord(
        report_type=report_type,
        case=_as_text(payload.get("case")) or _as_text(payload.get("kind")) or path.parent.name,
        status=status,
        reason=_as_text(payload.get("reason")) or _as_text(payload.get("human_summary")),
        report_path=str(path),
        artifacts_dir=_as_text(payload.get("artifacts_dir")) or str(path.parent),
        modified_at=_file_modified_at(path),
        skill=_as_text(decision.get("skill")) or "<complete>" if decision else "",
        backend=_as_text(decision.get("selected_backend")),
        readiness_status=_as_text(readiness.get("status")),
        readiness_label=_as_text(readiness.get("label")),
        failure_label=failure_label,
        false_success_risk=(
            1
            if status == "pass"
            and _as_text(readiness.get("status")) in {"blocked", "uncertain", "loading"}
            and (not decision or not _as_text(decision.get("skill")))
            else 0
        ),
        passed=1 if status == "pass" else 0,
        failed=1 if status in FAIL_STATUSES else 0,
        subcase_count=1,
    )


def iter_report_paths(roots: Sequence[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.json"):
            if path.name in REPORT_NAMES:
                yield path


def load_records(roots: Sequence[Path], latest: int = 10, failures_only: bool = False) -> List[RunRecord]:
    pairs: List[Tuple[float, RunRecord]] = []
    for path in iter_report_paths(roots):
        record = normalize_report(path)
        if record is None:
            continue
        if failures_only and record.status not in FAIL_STATUSES and record.failed <= 0:
            continue
        pairs.append((path.stat().st_mtime, record))
    pairs.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in pairs[: max(0, int(latest or 0))]]


def summarize_records(records: Sequence[RunRecord]) -> Dict[str, Any]:
    status_counts = Counter(record.status for record in records)
    skill_counts = Counter()
    readiness_counts = Counter()
    failure_counts = Counter()
    pass_total = 0
    fail_total = 0
    subcase_total = 0
    for record in records:
        pass_total += record.passed
        fail_total += record.failed
        subcase_total += record.subcase_count
        if record.skill:
            for chunk in record.skill.split(","):
                name = chunk.split(":", 1)[0].strip()
                if name:
                    skill_counts[name] += 1
        if record.readiness_status:
            readiness_counts[record.readiness_status] += 1
        if record.failure_label:
            failure_counts[record.failure_label] += 1

    total = pass_total + fail_total
    false_success_risk = sum(record.false_success_risk for record in records)
    return {
        "record_count": len(records),
        "subcase_count": subcase_total,
        "passed": pass_total,
        "failed": fail_total,
        "pass_rate": round(pass_total / total, 4) if total else None,
        "status_counts": dict(status_counts),
        "readiness_counts": dict(readiness_counts),
        "skill_counts": dict(skill_counts),
        "failure_labels": dict(failure_counts),
        "false_success_risk": false_success_risk,
    }


def _shorten(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _format_counts(mapping: Mapping[str, Any], limit: int = 5) -> str:
    items = sorted(mapping.items(), key=lambda item: (-int(item[1]), str(item[0])))[:limit]
    return ", ".join("{0}:{1}".format(key or "<empty>", value) for key, value in items) or "-"


def render_text_summary(records: Sequence[RunRecord]) -> str:
    summary = summarize_records(records)
    lines = [
        "Run Report Summary",
        "records={0} subcases={1} passed={2} failed={3} pass_rate={4}".format(
            summary["record_count"],
            summary["subcase_count"],
            summary["passed"],
            summary["failed"],
            "{0:.1%}".format(summary["pass_rate"]) if summary["pass_rate"] is not None else "-",
        ),
        "readiness={0}".format(_format_counts(summary["readiness_counts"])),
        "skills={0}".format(_format_counts(summary["skill_counts"])),
        "failure_labels={0}".format(_format_counts(summary["failure_labels"])),
        "false_success_risk={0}".format(summary["false_success_risk"]),
        "",
    ]
    if not records:
        lines.append("No reports found.")
        return "\n".join(lines)

    header = "{0:<19} {1:<6} {2:<13} {3:<30} {4:<17} {5:<16} {6}".format(
        "modified",
        "status",
        "type",
        "case",
        "readiness",
        "skill/backend",
        "report",
    )
    lines.append(header)
    lines.append("-" * len(header))
    for record in records:
        readiness = "/".join(part for part in (record.readiness_status, record.readiness_label) if part)
        skill_backend = "/".join(part for part in (record.skill, record.backend) if part)
        lines.append(
            "{0:<19} {1:<6} {2:<13} {3:<30} {4:<17} {5:<16} {6}".format(
                record.modified_at.replace("T", " "),
                _shorten(record.status, 6),
                _shorten(record.report_type, 13),
                _shorten(record.case, 30),
                _shorten(readiness, 17),
                _shorten(skill_backend, 16),
                record.report_path,
            )
        )
        if record.reason:
            lines.append("  reason: {0}".format(_shorten(record.reason, 140)))
        if record.failed:
            lines.append("  artifacts: {0}".format(record.artifacts_dir))
    return "\n".join(lines)


def _default_roots() -> List[Path]:
    return [REPO_ROOT / root for root in DEFAULT_REPORT_ROOTS]


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Summarize recent Android agent smoke-test reports.")
    parser.add_argument("--latest", type=int, default=10, help="Number of newest report files to show.")
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Report root to scan. Can be repeated. Defaults to data/tmp chaos/e2e/long_tail/diagnostics.",
    )
    parser.add_argument("--failures-only", action="store_true", help="Only show failed reports.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    roots = [Path(item) for item in args.root] if args.root else _default_roots()
    roots = [root if root.is_absolute() else REPO_ROOT / root for root in roots]
    records = load_records(roots=roots, latest=args.latest, failures_only=args.failures_only)
    if args.json:
        print(
            json.dumps(
                {
                    "summary": summarize_records(records),
                    "records": [record.to_dict() for record in records],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_text_summary(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
