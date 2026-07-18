from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .exporters import STATUS_AND_REGION_LEGEND, _compact_document_id


ERROR_LABELS = {
    "metadata_title_mismatch",
    "control_or_garbage_chars",
    "likely_ocr_symbol_error",
    "table_flattened_or_contaminated",
    "figure_or_diagram_text_leakage",
    "heading_fragmentation",
    "section_status_mismatch",
    "record_id_collision_or_ambiguity",
}

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
STATUS_VALUES = {"Normative", "Informative", "Informational"}
CORE_RECORD_FIELDS = ["doc", "record", "status", "region", "pdf", "section"]

DISALLOWED_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
REPLACEMENT_CHAR = "\ufffd"
POUND_SIGN = "\u00a3"

OCR_SYMBOL_RE = re.compile(
    rf"(?:(?:\b[A-Z]{{1,4}}\b|TI|CSI)\s*)?{POUND_SIGN}\s*\d|"
    rf"\b(?:TI|CSI)\s*{POUND_SIGN}\s*\d",
    re.IGNORECASE,
)

TABLE_CONTAMINATION_RE = re.compile(
    r"(?<!^)\b(?:MARKINGS|REFERENCES|GLOSSARY|APPENDIX|ANNEX|FIG\.\s+\d+|"
    r"\d{1,2}\.\d{1,2}\.\s+[A-Z][A-Z\s]{8,})\b"
)

NEXT_SECTION_IN_TABLE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.\s+[A-Z][A-Z\s]{8,}")
NOTE_THEN_MARKING_PROSE_RE = re.compile(
    r"\bNote:\s+.*(?:--|\u2014)For each package|\bNote:\s+.*Each package",
    re.IGNORECASE | re.DOTALL,
)

FIGURE_LABELS = {
    "ACTIVITY",
    "CONTENTS",
    "INDEX",
    "MINIMUM DIMENSION",
    "RADIOACTIVE",
}

STANDALONE_HEADINGS = {
    "CONTENTS",
    "FOREWORD",
    "REFERENCES",
    "GLOSSARY",
    "ABBREVIATIONS",
    "DEFINITIONS",
}

TITLE_SKIP_PATTERNS = [
    re.compile(r"^IAEA\b", re.IGNORECASE),
    re.compile(r"^INTERNATIONAL ATOMIC ENERGY AGENCY\b", re.IGNORECASE),
    re.compile(r"^NUCLEAR SECURITY SERIES\b", re.IGNORECASE),
    re.compile(r"^COPYRIGHT NOTICE\b", re.IGNORECASE),
    re.compile(r"^STI/PUB", re.IGNORECASE),
    re.compile(r"^ISBN\b", re.IGNORECASE),
    re.compile(r"^ISSN\b", re.IGNORECASE),
    re.compile(r"^VIENNA\b", re.IGNORECASE),
    re.compile(r"^UPU$", re.IGNORECASE),
    re.compile(r"^WCO$", re.IGNORECASE),
    re.compile(r"^JOINTLY SPONSORED", re.IGNORECASE),
]

CATEGORY_LINE_RE = re.compile(
    r"^(?:Technical Guidance|Reference Manual|Implementing Guides?|"
    r"Nuclear Security Recommendations|Nuclear Security Fundamentals)$",
    re.IGNORECASE,
)

SERIES_PREFIX_RE = re.compile(
    r"^(?:NSS|NST|IAEA)\s*[\dA-Z./() -]*\s+",
    re.IGNORECASE,
)


@dataclass
class AuditConfig:
    repo_root: Path = Path(".")
    parts_dir: Path = Path("outputs/Security/series_custom_gpt_knowledge_parts")
    manifest_csv: Path = Path("outputs/Security/series_manifest.csv")
    qa_report: Path = Path("outputs/Security/series_qa_report.md")
    output_dir: Path = Path("build")
    title_similarity_threshold: float = 0.45
    title_auto_fix_similarity: float = 0.78
    garbage_alpha_ratio_threshold: float = 0.25
    garbage_punctuation_ratio_threshold: float = 0.42
    figure_context_window: int = 4
    use_pdftotext: bool = True

    def resolve(self) -> "AuditConfig":
        root = self.repo_root.resolve()
        return AuditConfig(
            repo_root=root,
            parts_dir=_resolve_under(root, self.parts_dir),
            manifest_csv=_resolve_under(root, self.manifest_csv),
            qa_report=_resolve_under(root, self.qa_report),
            output_dir=_resolve_under(root, self.output_dir),
            title_similarity_threshold=self.title_similarity_threshold,
            title_auto_fix_similarity=self.title_auto_fix_similarity,
            garbage_alpha_ratio_threshold=self.garbage_alpha_ratio_threshold,
            garbage_punctuation_ratio_threshold=self.garbage_punctuation_ratio_threshold,
            figure_context_window=self.figure_context_window,
            use_pdftotext=self.use_pdftotext,
        )


@dataclass
class KnowledgeRecord:
    part_file: str
    doc: str
    record: str
    status: str
    region: str
    pdf: str
    section: str
    text: str
    original_record_index: int
    stable_internal_id: str = ""
    official_record_label: str = ""
    record_type: str = ""
    layout_ambiguous: bool = False
    layout_text: bool = False
    manual_review_labels: list[str] = field(default_factory=list)
    auto_fixed_labels: list[str] = field(default_factory=list)

    def to_raw_dict(self) -> dict[str, Any]:
        return {
            "part_file": self.part_file,
            "doc": self.doc,
            "record": self.record,
            "status": self.status,
            "region": self.region,
            "pdf": self.pdf,
            "section": self.section,
            "text": self.text,
            "original_record_index": self.original_record_index,
        }

    def to_clean_dict(self) -> dict[str, Any]:
        row = self.to_raw_dict()
        row.update(
            {
                "stable_internal_id": self.stable_internal_id,
                "record_type": self.record_type,
                "official_record_label": self.official_record_label,
                "layout_ambiguous": self.layout_ambiguous,
                "layout_text": self.layout_text,
                "manual_review_labels": self.manual_review_labels,
                "auto_fixed_labels": self.auto_fixed_labels,
            }
        )
        return row


@dataclass
class Candidate:
    doc: str
    record: str
    pdf: str
    section: str
    error_label: str
    severity: str
    confidence: float
    evidence: str
    suggested_action: str
    part_file: str = ""
    original_record_index: int | None = None
    stable_internal_id: str = ""
    auto_fixed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc": sanitize_report_text(self.doc),
            "record": sanitize_report_text(self.record),
            "pdf": sanitize_report_text(self.pdf),
            "section": sanitize_report_text(self.section),
            "error_label": self.error_label,
            "severity": self.severity,
            "confidence": f"{self.confidence:.2f}",
            "evidence": sanitize_report_text(self.evidence),
            "suggested_action": sanitize_report_text(self.suggested_action),
            "part_file": sanitize_report_text(self.part_file),
            "original_record_index": self.original_record_index or "",
            "stable_internal_id": self.stable_internal_id,
            "auto_fixed": self.auto_fixed,
        }


@dataclass
class Correction:
    doc: str
    record: str
    pdf: str
    section: str
    rule_name: str
    confidence: float
    before_text: str
    after_text: str
    provenance: dict[str, Any]
    original_record_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc": self.doc,
            "record": self.record,
            "pdf": self.pdf,
            "section": self.section,
            "rule_name": self.rule_name,
            "confidence": f"{self.confidence:.2f}",
            "before_text": self.before_text,
            "after_text": self.after_text,
            "provenance": self.provenance,
            "original_record_index": self.original_record_index or "",
        }


@dataclass
class AuditResult:
    records_raw: list[KnowledgeRecord]
    records_clean: list[KnowledgeRecord]
    candidates: list[Candidate]
    corrections: list[Correction]
    manual_review: list[Candidate]
    summary: dict[str, Any]


def run_audit(config: AuditConfig | None = None) -> AuditResult:
    cfg = (config or AuditConfig()).resolve()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = load_manifest(cfg.manifest_csv, cfg.repo_root)
    records_raw = parse_markdown_parts(cfg.parts_dir)
    write_jsonl(cfg.output_dir / "records_raw.jsonl", (r.to_raw_dict() for r in records_raw))

    records_clean = clone_records(records_raw)
    assign_stable_internal_ids(records_clean)

    qa_expected_count = parse_qa_record_count(cfg.qa_report)
    manifest_record_count = sum(int(row.get("record_count") or 0) for row in manifest_rows.values())

    title_overrides, metadata_candidates, metadata_corrections = detect_metadata_title_mismatches(
        manifest_rows,
        cfg,
    )

    candidates: list[Candidate] = [*metadata_candidates]
    corrections: list[Correction] = [*metadata_corrections]

    candidates.extend(detect_control_or_garbage(records_clean, cfg))
    candidates.extend(detect_ocr_symbol_errors(records_clean))
    candidates.extend(detect_table_contamination(records_clean))
    candidates.extend(detect_figure_text_leakage(records_clean, cfg))
    candidates.extend(detect_heading_fragmentation(records_clean))
    candidates.extend(detect_record_id_collisions(records_clean))

    corrections.extend(apply_control_character_repairs(records_clean, candidates))
    corrections.extend(apply_layout_text_repairs(records_clean, candidates))
    corrections.extend(log_stable_id_repairs(candidates))

    status_candidates = detect_section_status_mismatches(records_clean)
    candidates.extend(status_candidates)
    corrections.extend(apply_status_repairs(records_clean, status_candidates))

    mark_candidate_internal_ids(candidates, records_clean)
    mark_auto_fixed_candidates(candidates, corrections)
    propagate_candidate_labels(records_clean, candidates)

    manual_review = [candidate for candidate in candidates if not candidate.auto_fixed]

    write_jsonl(cfg.output_dir / "records_clean.jsonl", (r.to_clean_dict() for r in records_clean))
    write_jsonl(cfg.output_dir / "error_candidates.jsonl", (c.to_dict() for c in candidates))
    write_csv_rows(cfg.output_dir / "error_candidates.csv", (c.to_dict() for c in candidates))
    write_jsonl(cfg.output_dir / "corrections_applied.jsonl", (c.to_dict() for c in corrections))
    write_csv_rows(cfg.output_dir / "manual_review_required.csv", (c.to_dict() for c in manual_review))

    summary = build_summary(
        records_raw=records_raw,
        records_clean=records_clean,
        candidates=candidates,
        corrections=corrections,
        manual_review=manual_review,
        qa_expected_count=qa_expected_count,
        manifest_record_count=manifest_record_count,
    )
    write_json(cfg.output_dir / "audit_summary.json", summary)
    write_clean_markdown_parts(cfg.output_dir, records_clean, manifest_rows, title_overrides)
    write_clean_qa_report(
        cfg.output_dir / "series_qa_report.clean.md",
        summary,
        candidates,
        manual_review,
        corrections,
        records_clean,
    )

    return AuditResult(
        records_raw=records_raw,
        records_clean=records_clean,
        candidates=candidates,
        corrections=corrections,
        manual_review=manual_review,
        summary=summary,
    )


def parse_markdown_parts(parts_dir: Path) -> list[KnowledgeRecord]:
    part_files = sorted(parts_dir.glob("part_*.md"))
    records: list[KnowledgeRecord] = []
    original_index = 0
    for part_path in part_files:
        content = part_path.read_text(encoding="utf-8", errors="replace")
        _, marker, body = content.partition("## Structural records")
        if not marker:
            continue
        for block in re.split(r"\n---\n", body):
            record = parse_record_block(block, part_path.name, original_index + 1)
            if record is None:
                continue
            original_index += 1
            record.original_record_index = original_index
            records.append(record)
    return records


def parse_record_block(block: str, part_file: str, original_record_index: int) -> KnowledgeRecord | None:
    lines = block.strip("\n").splitlines()
    if not lines:
        return None

    values: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            break
        match = re.match(r"^(doc|record|status|region|pdf|section):\s*(.*)$", line)
        if not match:
            return None
        values[match.group(1)] = match.group(2)
        index += 1

    if not set(CORE_RECORD_FIELDS).issubset(values):
        return None

    text = strip_generated_document_header("\n".join(lines[index:]).rstrip())
    record = KnowledgeRecord(
        part_file=part_file,
        doc=values["doc"],
        record=values["record"],
        status=values["status"],
        region=values["region"],
        pdf=values["pdf"],
        section=values["section"],
        text=text,
        original_record_index=original_record_index,
    )
    record.record_type, record.official_record_label = split_record_label(record.record)
    return record


def load_manifest(manifest_csv: Path, repo_root: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with manifest_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "ok":
                continue
            compact_id = _compact_document_id(row.get("document_id", ""))
            row = dict(row)
            row["compact_document_id"] = compact_id
            row["resolved_source_pdf"] = str(resolve_source_pdf(repo_root, row.get("source_pdf", "")) or "")
            rows[compact_id] = row
    return rows


def detect_metadata_title_mismatches(
    manifest_rows: dict[str, dict[str, str]],
    cfg: AuditConfig,
) -> tuple[dict[str, str], list[Candidate], list[Correction]]:
    title_overrides: dict[str, str] = {}
    candidates: list[Candidate] = []
    corrections: list[Correction] = []
    for doc, row in manifest_rows.items():
        current_title = row.get("document_title", "")
        source_pdf = row.get("source_pdf", "")
        source_pdf_path = Path(row.get("resolved_source_pdf") or "")
        filename_title = title_from_source_filename(source_pdf)
        pdf_title = title_from_pdf_text(source_pdf_path, cfg) if source_pdf_path else ""
        series_title = title_from_series_number(row.get("series_number", ""))
        best_title = choose_best_title(filename_title, pdf_title, series_title)
        if not best_title:
            continue

        similarity = title_similarity(current_title, best_title)
        token_overlap = title_token_overlap(current_title, best_title)
        title_is_bad = (
            current_title == "Jotusvnfout Dpotfotvt"
            or contains_disallowed_control(current_title)
            or similarity < cfg.title_similarity_threshold
            or token_overlap < 0.25
        )
        if not title_is_bad:
            continue

        confidence = 0.95 if pdf_title and title_similarity(pdf_title, filename_title) >= 0.75 else 0.86
        source_evidence = "source_pdf filename"
        if pdf_title:
            source_evidence = "source_pdf title-page text and source_pdf filename"
        severity = "critical" if similarity < 0.25 or current_title == "Jotusvnfout Dpotfotvt" else "high"
        auto_fix = confidence >= cfg.title_auto_fix_similarity and bool(source_pdf_path)
        candidates.append(
            Candidate(
                doc=doc,
                record="<document_metadata>",
                pdf="",
                section="",
                error_label="metadata_title_mismatch",
                severity=severity,
                confidence=confidence,
                evidence=(
                    f"document_title={current_title!r}; resolved_title={best_title!r}; "
                    f"similarity={similarity:.2f}; evidence={source_evidence}; source_pdf={source_pdf}"
                ),
                suggested_action=(
                    "auto-fix document title from verified source PDF metadata"
                    if auto_fix
                    else "manual review document title against source PDF"
                ),
                auto_fixed=auto_fix,
            )
        )
        if auto_fix:
            title_overrides[doc] = best_title
            corrections.append(
                Correction(
                    doc=doc,
                    record="<document_metadata>",
                    pdf="",
                    section="",
                    rule_name="metadata_title_from_source_pdf",
                    confidence=confidence,
                    before_text=current_title,
                    after_text=best_title,
                    provenance={
                        "source_pdf": source_pdf,
                        "resolved_source_pdf": str(source_pdf_path),
                        "series_number": row.get("series_number", ""),
                        "pdf_title_page_candidate": pdf_title,
                        "filename_title_candidate": filename_title,
                    },
                )
            )
    return title_overrides, candidates, corrections


def detect_control_or_garbage(records: list[KnowledgeRecord], cfg: AuditConfig) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        control_fields = fields_with_disallowed_controls(record)
        if control_fields:
            candidates.append(
                record_candidate(
                    record,
                    "control_or_garbage_chars",
                    "low",
                    0.96,
                    "disallowed control characters in " + ", ".join(control_fields),
                    "auto-remove non-semantic control characters",
                    auto_fixed=True,
                )
            )

        labels: list[str] = []
        if REPLACEMENT_CHAR in record.text:
            labels.append("replacement character")
        if PRIVATE_USE_RE.search(record.text):
            labels.append("private-use glyph")
        if looks_like_garbage(record.text, cfg):
            labels.append("high punctuation or low-alpha OCR text")
        if not labels:
            continue
        candidates.append(
            record_candidate(
                record,
                "control_or_garbage_chars",
                "medium",
                0.72,
                "; ".join(labels),
                "manual review unreadable OCR text",
            )
        )
    return candidates


def detect_ocr_symbol_errors(records: list[KnowledgeRecord]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        matches = OCR_SYMBOL_RE.findall(record.text)
        if not matches:
            continue
        candidates.append(
            record_candidate(
                record,
                "likely_ocr_symbol_error",
                "high",
                0.74,
                f"suspicious symbol near numeric threshold: {short_preview(record.text)}",
                "manual PDF verification required before changing threshold or table symbol",
            )
        )
    return candidates


def detect_table_contamination(records: list[KnowledgeRecord]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        if record.record_type != "table":
            continue
        reasons: list[str] = []
        if TABLE_CONTAMINATION_RE.search(record.text):
            reasons.append("table text contains possible following heading/prose")
        if record.text.count("(cont.)") > 1:
            reasons.append("continued table headings flattened into one record")
        if NEXT_SECTION_IN_TABLE_RE.search(record.text):
            reasons.append("next section heading appears inside table record")
        if NOTE_THEN_MARKING_PROSE_RE.search(record.text):
            reasons.append("table note is followed by package-marking prose from adjacent content")
        if not reasons:
            continue
        record.layout_ambiguous = True
        candidates.append(
            record_candidate(
                record,
                "table_flattened_or_contaminated",
                "medium",
                0.80,
                "; ".join(reasons) + f": {short_preview(record.text)}",
                "preserve raw text and mark layout_ambiguous; verify table layout in source PDF",
            )
        )
    return candidates


def detect_figure_text_leakage(records: list[KnowledgeRecord], cfg: AuditConfig) -> list[Candidate]:
    candidates: list[Candidate] = []
    last_figure_by_doc_page: dict[tuple[str, str], int] = {}

    for index, record in enumerate(records):
        if record.record_type == "figure":
            last_figure_by_doc_page[(record.doc, record.pdf)] = index
            continue

        if not is_possible_figure_label(record):
            continue

        figure_index = last_figure_by_doc_page.get((record.doc, record.pdf))
        if figure_index is None or index - figure_index > cfg.figure_context_window:
            continue

        record.layout_text = True
        candidates.append(
            record_candidate(
                record,
                "figure_or_diagram_text_leakage",
                "medium",
                0.82,
                f"short label-like record follows a figure on the same PDF page: {record.text!r}",
                "mark as layout_text and keep text for provenance; do not treat as substantive guidance",
                auto_fixed=True,
            )
        )
    return candidates


def detect_heading_fragmentation(records: list[KnowledgeRecord]) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_pairs: set[tuple[int, int]] = set()
    for left, right in zip(records, records[1:]):
        if (left.original_record_index, right.original_record_index) in seen_pairs:
            continue
        if not are_heading_fragments(left, right):
            continue
        seen_pairs.add((left.original_record_index, right.original_record_index))
        candidates.append(
            record_candidate(
                left,
                "heading_fragmentation",
                "medium",
                0.70,
                f"consecutive heading fragments on same page: {left.text!r} + {right.text!r}",
                "manual review or parser-level heading merge; not auto-merged because section hierarchy may change",
            )
        )
    return candidates


def detect_section_status_mismatches(records: list[KnowledgeRecord]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        expected = expected_status(record)
        if expected is None or expected == record.status:
            continue
        confidence = 0.93 if is_unambiguous_status_context(record, expected) else 0.68
        auto = confidence >= 0.90
        candidates.append(
            record_candidate(
                record,
                "section_status_mismatch",
                "medium" if auto else "high",
                confidence,
                f"status={record.status!r}; expected={expected!r}; region={record.region!r}; section={record.section!r}",
                "auto-fix status from deterministic region/section rule"
                if auto
                else "manual review status against source section context",
                auto_fixed=auto,
            )
        )
    return candidates


def detect_record_id_collisions(records: list[KnowledgeRecord]) -> list[Candidate]:
    counts = Counter((record.doc, record.record) for record in records)
    candidates: list[Candidate] = []
    emitted: set[tuple[str, str]] = set()
    for record in records:
        key = (record.doc, record.record)
        if counts[key] < 2 or key in emitted:
            continue
        emitted.add(key)
        candidates.append(
            record_candidate(
                record,
                "record_id_collision_or_ambiguity",
                "low",
                0.95,
                f"{counts[key]} records share official record label {record.record!r} in {record.doc}",
                "preserve official label and use stable_internal_id for unique internal references",
                auto_fixed=True,
            )
        )
    return candidates


def apply_control_character_repairs(
    records: list[KnowledgeRecord],
    candidates: list[Candidate],
) -> list[Correction]:
    correction_indices = {
        candidate.original_record_index
        for candidate in candidates
        if candidate.error_label == "control_or_garbage_chars"
        and candidate.auto_fixed
        and candidate.original_record_index is not None
    }
    corrections: list[Correction] = []
    for record in records:
        if record.original_record_index not in correction_indices:
            continue
        changed_fields = fields_with_disallowed_controls(record)
        before = control_repair_snapshot(record, changed_fields)
        record.doc = remove_disallowed_controls(record.doc)
        record.record = remove_disallowed_controls(record.record)
        record.status = remove_disallowed_controls(record.status)
        record.region = remove_disallowed_controls(record.region)
        record.pdf = remove_disallowed_controls(record.pdf)
        record.section = remove_disallowed_controls(record.section)
        record.text = remove_disallowed_controls(record.text)
        after = control_repair_snapshot(record, changed_fields)
        if after == before:
            continue
        record.auto_fixed_labels.append("control_or_garbage_chars")
        corrections.append(
            Correction(
                doc=record.doc,
                record=record.record,
                pdf=record.pdf,
                section=record.section,
                rule_name="remove_non_semantic_control_characters",
                confidence=0.96,
                before_text=before,
                after_text=after,
                provenance={
                    "part_file": record.part_file,
                    "original_record_index": record.original_record_index,
                },
                original_record_index=record.original_record_index,
            )
        )
    return corrections


def apply_layout_text_repairs(
    records: list[KnowledgeRecord],
    candidates: list[Candidate],
) -> list[Correction]:
    layout_indices = {
        candidate.original_record_index
        for candidate in candidates
        if candidate.error_label == "figure_or_diagram_text_leakage"
        and candidate.original_record_index is not None
    }
    corrections: list[Correction] = []
    for record in records:
        if record.original_record_index not in layout_indices:
            continue
        before = f"layout_text={record.layout_text}; status={record.status}"
        record.layout_text = True
        if record.status != "Informational":
            record.status = "Informational"
        record.auto_fixed_labels.append("figure_or_diagram_text_leakage")
        corrections.append(
            Correction(
                doc=record.doc,
                record=record.record,
                pdf=record.pdf,
                section=record.section,
                rule_name="mark_figure_layout_text_informational",
                confidence=0.82,
                before_text=before,
                after_text=f"layout_text={record.layout_text}; status={record.status}",
                provenance={
                    "part_file": record.part_file,
                    "original_record_index": record.original_record_index,
                    "text": record.text,
                },
                original_record_index=record.original_record_index,
            )
        )
    return corrections


def log_stable_id_repairs(candidates: list[Candidate]) -> list[Correction]:
    corrections: list[Correction] = []
    for candidate in candidates:
        if candidate.error_label != "record_id_collision_or_ambiguity" or not candidate.auto_fixed:
            continue
        corrections.append(
            Correction(
                doc=candidate.doc,
                record=candidate.record,
                pdf=candidate.pdf,
                section=candidate.section,
                rule_name="add_stable_internal_id",
                confidence=candidate.confidence,
                before_text=candidate.record,
                after_text=candidate.stable_internal_id,
                provenance={
                    "part_file": candidate.part_file,
                    "original_record_index": candidate.original_record_index,
                    "reason": "official record labels are preserved; stable_internal_id disambiguates repeated labels",
                },
                original_record_index=candidate.original_record_index,
            )
        )
    return corrections


def apply_status_repairs(
    records: list[KnowledgeRecord],
    status_candidates: list[Candidate],
) -> list[Correction]:
    auto_indices = {
        candidate.original_record_index
        for candidate in status_candidates
        if candidate.auto_fixed and candidate.original_record_index is not None
    }
    corrections: list[Correction] = []
    for record in records:
        if record.original_record_index not in auto_indices:
            continue
        expected = expected_status(record)
        if expected is None or expected == record.status:
            continue
        before = record.status
        record.status = expected
        record.auto_fixed_labels.append("section_status_mismatch")
        corrections.append(
            Correction(
                doc=record.doc,
                record=record.record,
                pdf=record.pdf,
                section=record.section,
                rule_name="status_from_region_section_rule",
                confidence=0.93,
                before_text=before,
                after_text=expected,
                provenance={
                    "part_file": record.part_file,
                    "original_record_index": record.original_record_index,
                    "region": record.region,
                    "section": record.section,
                },
                original_record_index=record.original_record_index,
            )
        )
    return corrections


def assign_stable_internal_ids(records: list[KnowledgeRecord]) -> None:
    sequences: defaultdict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for record in records:
        record.record_type, record.official_record_label = split_record_label(record.record)
        section_hash = hashlib.sha1(record.section.encode("utf-8", errors="replace")).hexdigest()[:8]
        page = parse_pdf_start(record.pdf)
        key = (record.doc, record.record_type, record.official_record_label, section_hash, page)
        sequences[key] += 1
        record.stable_internal_id = "__".join(
            [
                slug(record.doc),
                slug(record.record_type),
                slug(record.official_record_label or "none"),
                f"p{page or 'unknown'}",
                section_hash,
                f"{sequences[key]:04d}",
            ]
        )


def mark_candidate_internal_ids(candidates: list[Candidate], records: list[KnowledgeRecord]) -> None:
    by_index = {record.original_record_index: record for record in records}
    for candidate in candidates:
        if candidate.stable_internal_id or candidate.original_record_index is None:
            continue
        record = by_index.get(candidate.original_record_index)
        if record:
            candidate.stable_internal_id = record.stable_internal_id


def mark_auto_fixed_candidates(candidates: list[Candidate], corrections: list[Correction]) -> None:
    correction_keys = {
        (correction.doc, correction.record, correction.original_record_index, label_from_rule(correction.rule_name))
        for correction in corrections
    }
    for candidate in candidates:
        if candidate.auto_fixed:
            continue
        if (
            candidate.doc,
            candidate.record,
            candidate.original_record_index,
            candidate.error_label,
        ) in correction_keys:
            candidate.auto_fixed = True


def propagate_candidate_labels(records: list[KnowledgeRecord], candidates: list[Candidate]) -> None:
    by_index = {record.original_record_index: record for record in records}
    for candidate in candidates:
        if candidate.original_record_index is None:
            continue
        record = by_index.get(candidate.original_record_index)
        if not record:
            continue
        target = record.auto_fixed_labels if candidate.auto_fixed else record.manual_review_labels
        if candidate.error_label not in target:
            target.append(candidate.error_label)


def expected_status(record: KnowledgeRecord) -> str | None:
    if record.layout_text:
        return "Informational"
    if record.record_type == "heading":
        return "Informational"
    if record.record_type == "footnote":
        return "Informative"
    if record.region in {"FrontMatter", "References", "Glossary", "BackMatter"}:
        return "Informational"
    if record.region == "Annex":
        return "Informative"
    if record.region == "Appendix":
        return "Normative"
    if record.region == "Body":
        if is_section_one(record.section, record.official_record_label):
            return "Informational"
        if record.record_type in {"paragraph", "text_block", "table", "figure"}:
            return "Normative"
    return None


def is_unambiguous_status_context(record: KnowledgeRecord, expected: str) -> bool:
    if expected not in STATUS_VALUES:
        return False
    if record.layout_text:
        return True
    if record.record_type == "heading":
        return True
    if record.region in {"FrontMatter", "References", "Glossary", "BackMatter", "Annex", "Appendix"}:
        return True
    if record.region == "Body" and is_section_one(record.section, record.official_record_label):
        return True
    if record.region == "Body" and record.record_type in {"paragraph", "text_block", "table", "figure"}:
        return True
    return False


def build_summary(
    *,
    records_raw: list[KnowledgeRecord],
    records_clean: list[KnowledgeRecord],
    candidates: list[Candidate],
    corrections: list[Correction],
    manual_review: list[Candidate],
    qa_expected_count: int | None,
    manifest_record_count: int,
) -> dict[str, Any]:
    candidate_counts = Counter(candidate.error_label for candidate in candidates)
    severity_counts = Counter(candidate.severity for candidate in candidates)
    manual_counts = Counter(candidate.error_label for candidate in manual_review)
    return {
        "raw_record_count": len(records_raw),
        "clean_record_count": len(records_clean),
        "qa_expected_record_count": qa_expected_count,
        "manifest_record_count": manifest_record_count,
        "qa_record_count_matches": qa_expected_count in {None, len(records_raw)},
        "manifest_record_count_matches": manifest_record_count == len(records_raw),
        "candidate_count": len(candidates),
        "manual_review_count": len(manual_review),
        "auto_fixed_record_or_metadata_count": len(corrections),
        "counts_by_error_label": dict(sorted(candidate_counts.items())),
        "counts_by_severity": dict(sorted(severity_counts.items(), key=lambda item: SEVERITY_RANK.get(item[0], 9))),
        "manual_review_by_error_label": dict(sorted(manual_counts.items())),
        "clean_records_with_layout_ambiguous": sum(1 for r in records_clean if r.layout_ambiguous),
        "clean_records_with_layout_text": sum(1 for r in records_clean if r.layout_text),
    }


def write_clean_markdown_parts(
    output_dir: Path,
    records: list[KnowledgeRecord],
    manifest_rows: dict[str, dict[str, str]],
    title_overrides: dict[str, str],
) -> None:
    records_by_part: defaultdict[str, list[KnowledgeRecord]] = defaultdict(list)
    for record in records:
        records_by_part[record.part_file].append(record)

    part_names = sorted(records_by_part)
    total = len(part_names)
    for index, part_name in enumerate(part_names, start=1):
        part_records = records_by_part[part_name]
        out_path = output_dir / part_name.replace(".md", ".clean.md")
        with out_path.open("w", encoding="utf-8") as f:
            f.write(f"# Clean Custom GPT Knowledge - Part {index:03d} of {total:03d}\n\n")
            f.write("Generated by the deterministic Security corpus audit workflow.\n")
            f.write("Original generated files are not overwritten.\n\n")
            f.write(STATUS_AND_REGION_LEGEND)
            f.write("\n## Documents in this part\n\n")
            for doc in sorted({record.doc for record in part_records}):
                row = manifest_rows.get(doc, {})
                title = title_overrides.get(doc) or row.get("document_title") or doc
                series_number = row.get("series_number", "")
                document_type = row.get("document_type", "")
                count = sum(1 for record in part_records if record.doc == doc)
                f.write(f"- {doc} | {series_number} | {document_type} | {title} | records in part: {count}\n")
            f.write("\n## Structural records\n")
            last_doc = ""
            seen_docs: set[str] = set()
            for record in part_records:
                if record.doc != last_doc:
                    row = manifest_rows.get(record.doc, {})
                    title = title_overrides.get(record.doc) or row.get("document_title") or record.doc
                    continued = " (continued)" if record.doc in seen_docs else ""
                    f.write(f"\n\n# Document: {record.doc}{continued} - {title}\n\n")
                    if row:
                        f.write(f"- Full ID: {row.get('document_id', '')}\n")
                        f.write(f"- Category: {row.get('document_category', '')}\n")
                        f.write(f"- Type: {row.get('document_type', '')}\n")
                        f.write(f"- Series: {row.get('series_name', '')} {row.get('series_number', '')}\n")
                    seen_docs.add(record.doc)
                    last_doc = record.doc
                f.write(record_to_markdown(record))


def write_clean_qa_report(
    path: Path,
    summary: dict[str, Any],
    candidates: list[Candidate],
    manual_review: list[Candidate],
    corrections: list[Correction],
    records: list[KnowledgeRecord],
) -> None:
    by_doc_candidates = Counter(candidate.doc for candidate in candidates)
    by_doc_records = Counter(record.doc for record in records)
    concentrations = []
    for doc, count in by_doc_candidates.items():
        record_count = by_doc_records.get(doc, 0)
        rate = count / record_count if record_count else 0.0
        concentrations.append((rate, count, record_count, doc))

    with path.open("w", encoding="utf-8") as f:
        f.write("# Clean Series QA Report\n\n")
        f.write("This report is generated by `tools/audit_security_knowledge.py`.\n\n")
        f.write("## Record Counts\n\n")
        f.write(f"- Raw records parsed: {summary['raw_record_count']}\n")
        f.write(f"- Clean records written: {summary['clean_record_count']}\n")
        f.write(f"- QA report expected records: {summary['qa_expected_record_count']}\n")
        f.write(f"- Manifest record count: {summary['manifest_record_count']}\n")
        f.write(f"- QA count matches raw parse: {summary['qa_record_count_matches']}\n")
        f.write(f"- Manifest count matches raw parse: {summary['manifest_record_count_matches']}\n\n")

        f.write("## Audit Summary\n\n")
        f.write(f"- Error candidates: {summary['candidate_count']}\n")
        f.write(f"- Automatic corrections logged: {summary['auto_fixed_record_or_metadata_count']}\n")
        f.write(f"- Manual review records: {summary['manual_review_count']}\n")
        f.write(f"- Layout ambiguous table records: {summary['clean_records_with_layout_ambiguous']}\n")
        f.write(f"- Figure/layout text records: {summary['clean_records_with_layout_text']}\n\n")

        f.write("## Counts by Error Label\n\n")
        for label, count in summary["counts_by_error_label"].items():
            f.write(f"- {label}: {count}\n")

        f.write("\n## Counts by Severity\n\n")
        for severity, count in summary["counts_by_severity"].items():
            f.write(f"- {severity}: {count}\n")

        f.write("\n## Top 20 Highest-Severity Examples\n\n")
        for candidate in sorted(candidates, key=candidate_sort_key)[:20]:
            f.write(
                f"- {candidate.severity} | {candidate.error_label} | {candidate.doc} | "
                f"{candidate.record} | p. {candidate.pdf}: {sanitize_report_text(candidate.evidence)[:260]}\n"
            )

        f.write("\n## Documents with Highest Error Concentration\n\n")
        for rate, count, record_count, doc in sorted(concentrations, reverse=True)[:20]:
            f.write(f"- {doc}: {count} candidates / {record_count} records ({rate:.1%})\n")

        f.write("\n## Automatic Corrections\n\n")
        if not corrections:
            f.write("No automatic corrections were applied.\n")
        for correction in corrections[:50]:
            f.write(
                f"- {correction.rule_name} | {correction.doc} | {correction.record} | "
                f"index {correction.original_record_index or ''}\n"
            )

        f.write("\n## Manual Review\n\n")
        f.write(f"Manual review required: {len(manual_review)} candidates.\n\n")
        for candidate in sorted(manual_review, key=candidate_sort_key)[:50]:
            f.write(
                f"- {candidate.severity} | {candidate.error_label} | {candidate.doc} | "
                f"{candidate.record} | p. {candidate.pdf}: {sanitize_report_text(candidate.suggested_action)}\n"
            )

        f.write("\n## Table Layout Warning\n\n")
        f.write(
            "Do not rely on exact table layout without PDF verification. Flattened PDF extraction can "
            "merge columns, continuation rows, notes, following paragraphs, or figure text into one record.\n"
        )


def record_to_markdown(record: KnowledgeRecord) -> str:
    return (
        "\n---\n"
        f"doc: {record.doc}\n"
        f"record: {record.record}\n"
        f"status: {record.status}\n"
        f"region: {record.region}\n"
        f"pdf: {record.pdf}\n"
        f"section: {record.section}\n\n"
        f"{record.text}\n"
    )


def write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    default_fields = [
        "doc",
        "record",
        "pdf",
        "section",
        "error_label",
        "severity",
        "confidence",
        "evidence",
        "suggested_action",
        "part_file",
        "original_record_index",
        "stable_internal_id",
        "auto_fixed",
    ]
    fields = default_fields
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def clone_records(records: list[KnowledgeRecord]) -> list[KnowledgeRecord]:
    clones: list[KnowledgeRecord] = []
    for record in records:
        clone = KnowledgeRecord(**record.to_raw_dict())
        clone.record_type, clone.official_record_label = split_record_label(clone.record)
        clones.append(clone)
    return clones


def record_candidate(
    record: KnowledgeRecord,
    error_label: str,
    severity: str,
    confidence: float,
    evidence: str,
    suggested_action: str,
    *,
    auto_fixed: bool = False,
) -> Candidate:
    if error_label not in ERROR_LABELS:
        raise ValueError(f"Unknown audit error label: {error_label}")
    return Candidate(
        doc=record.doc,
        record=record.record,
        pdf=record.pdf,
        section=record.section,
        error_label=error_label,
        severity=severity,
        confidence=confidence,
        evidence=evidence,
        suggested_action=suggested_action,
        part_file=record.part_file,
        original_record_index=record.original_record_index,
        stable_internal_id=record.stable_internal_id,
        auto_fixed=auto_fixed,
    )


def resolve_source_pdf(repo_root: Path, source_pdf: str) -> Path | None:
    source = Path(source_pdf)
    candidates = [
        repo_root / source,
        repo_root / "inputs" / source,
        repo_root / "inputs" / source.name,
    ]
    if len(source.parts) >= 2:
        candidates.append(repo_root / "inputs" / source.parts[-2] / source.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def title_from_source_filename(source_pdf: str) -> str:
    stem = Path(source_pdf).stem
    stem = re.sub(r"^NSS\s+\d+(?:-[A-Z])?(?:\s*\([^)]+\))?\s+", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^NSS-\d+(?:-[A-Z])?\s+", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem).strip(" -")
    return title_case(stem)


def title_from_series_number(series_number: str) -> str:
    value = re.sub(r"^No\.\s*[\w./() -]+", "", series_number, flags=re.IGNORECASE).strip()
    value = re.sub(
        r"\b(?:Technical Guidance|Reference Manual|Implementing Guides?|Nuclear Security Recommendations|"
        r"Nuclear Security Fundamentals)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bJointly sponsored by\b.*$", "", value, flags=re.IGNORECASE).strip()
    return title_case(value)


def title_from_pdf_text(source_pdf_path: Path, cfg: AuditConfig) -> str:
    if not cfg.use_pdftotext or not source_pdf_path.exists() or not shutil.which("pdftotext"):
        return ""
    try:
        result = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "4", str(source_pdf_path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return extract_title_from_pdf_front_text(result.stdout)


def extract_title_from_pdf_front_text(text: str) -> str:
    lines = [normalize_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    for start, line in enumerate(lines[:40]):
        if not CATEGORY_LINE_RE.match(line):
            continue
        collected: list[str] = []
        for candidate in lines[start + 1 : start + 12]:
            if CATEGORY_LINE_RE.match(candidate):
                continue
            if any(pattern.search(candidate) for pattern in TITLE_SKIP_PATTERNS):
                break
            if len(candidate) <= 2:
                break
            if has_mojibake(candidate):
                break
            collected.append(candidate)
            if len(" ".join(collected)) >= 140:
                break
        title = title_case(" ".join(collected))
        if len(title) >= 20:
            return title
    return ""


def choose_best_title(filename_title: str, pdf_title: str, series_title: str) -> str:
    candidates = [candidate for candidate in [pdf_title, filename_title, series_title] if len(candidate) >= 10]
    if not candidates:
        return ""
    if pdf_title and filename_title and title_similarity(pdf_title, filename_title) >= 0.70:
        return pdf_title
    if filename_title:
        return filename_title
    return candidates[0]


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()


def title_token_overlap(left: str, right: str) -> float:
    left_tokens = set(tokenize_title(left))
    right_tokens = set(tokenize_title(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def normalize_title(value: str) -> str:
    return " ".join(tokenize_title(value))


def tokenize_title(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def title_case(value: str) -> str:
    value = normalize_line(value)
    if not value:
        return ""
    return " ".join(word[:1].upper() + word[1:].lower() for word in value.split())


def normalize_line(value: str) -> str:
    value = value.replace("\x00", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_generated_document_header(text: str) -> str:
    """Remove exporter document headers that sit between Markdown record blocks.

    In the generated knowledge parts, a new `# Document:` heading is written
    before the next record delimiter. When records are parsed by delimiter, that
    inter-record heading otherwise becomes trailing text on the previous record.
    """
    return re.split(r"\n{2,}# Document:\s+", text, maxsplit=1)[0].rstrip()


def sanitize_report_text(value: str) -> str:
    value = DISALLOWED_CONTROL_RE.sub(" ", value)
    value = PRIVATE_USE_RE.sub(" ", value)
    value = value.replace(REPLACEMENT_CHAR, "?")
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip()


def contains_disallowed_control(value: str) -> bool:
    return bool(DISALLOWED_CONTROL_RE.search(value))


def fields_with_disallowed_controls(record: KnowledgeRecord) -> list[str]:
    fields = {
        "doc": record.doc,
        "record": record.record,
        "status": record.status,
        "region": record.region,
        "pdf": record.pdf,
        "section": record.section,
        "text": record.text,
    }
    return [name for name, value in fields.items() if contains_disallowed_control(value)]


def control_repair_snapshot(record: KnowledgeRecord, fields: list[str]) -> str:
    if not fields:
        return ""
    return json.dumps(
        {field: getattr(record, field) for field in fields},
        ensure_ascii=False,
        sort_keys=True,
    )


def remove_disallowed_controls(value: str) -> str:
    value = DISALLOWED_CONTROL_RE.sub(" ", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def looks_like_garbage(text: str, cfg: AuditConfig) -> bool:
    stripped = text.strip()
    if len(stripped) < 24:
        return False
    nonspace = [char for char in stripped if not char.isspace()]
    if not nonspace:
        return False
    alpha = sum(char.isalpha() for char in nonspace)
    punctuation = sum(not char.isalnum() for char in nonspace)
    alpha_ratio = alpha / len(nonspace)
    punctuation_ratio = punctuation / len(nonspace)
    if alpha_ratio < cfg.garbage_alpha_ratio_threshold and punctuation_ratio > cfg.garbage_punctuation_ratio_threshold:
        return True
    if has_mojibake(stripped) and punctuation_ratio > 0.18:
        return True
    return False


def has_mojibake(text: str) -> bool:
    controls = len(DISALLOWED_CONTROL_RE.findall(text))
    odd_symbols = sum(1 for char in text if ord(char) > 127 and not char.isalpha() and not char.isspace())
    return controls >= 2 or odd_symbols >= max(5, len(text) // 20)


def is_possible_figure_label(record: KnowledgeRecord) -> bool:
    text = normalize_line(record.text)
    if not text:
        return False
    if text in FIGURE_LABELS:
        return True
    if re.fullmatch(r"\d{1,2}", text):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:mm|cm|m)", text, re.IGNORECASE):
        return True
    if record.record_type == "heading" and text in FIGURE_LABELS:
        return True
    return False


def are_heading_fragments(left: KnowledgeRecord, right: KnowledgeRecord) -> bool:
    if left.record_type != "heading" or right.record_type != "heading":
        return False
    if left.doc != right.doc or left.pdf != right.pdf or left.region != right.region:
        return False
    if left.text.strip().upper() in STANDALONE_HEADINGS or right.text.strip().upper() in STANDALONE_HEADINGS:
        return False
    if not left.text.strip() or not right.text.strip():
        return False
    combined = f"{left.text.strip()} {right.text.strip()}"
    if len(combined) > 160:
        return False
    if not (looks_like_heading_fragment(left.text) and looks_like_heading_fragment(right.text)):
        return False
    if left.section and right.section and left.section == right.section:
        return False
    return True


def looks_like_heading_fragment(text: str) -> bool:
    value = normalize_line(text)
    if len(value) < 4:
        return False
    letters = [char for char in value if char.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(char.isupper() for char in letters) / len(letters)
    return upper_ratio >= 0.80 and not value.endswith(".")


def is_section_one(section: str, official_label: str) -> bool:
    value = section.strip()
    label = official_label.strip()
    return (
        bool(re.match(r"^1(?:\.|\s)", value))
        or bool(re.match(r"^1(?:\.\d+)*$", label))
        or bool(re.match(r"^1\.\d+", label))
    )


def split_record_label(record: str) -> tuple[str, str]:
    parts = record.split(" ", 1)
    if len(parts) == 1:
        return record, ""
    return parts[0], parts[1]


def parse_pdf_start(pdf: str) -> str:
    match = re.match(r"(\d+)", pdf.strip())
    return match.group(1) if match else ""


def short_preview(text: str, limit: int = 220) -> str:
    value = normalize_line(text)
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "none"


def parse_qa_record_count(qa_report: Path) -> int | None:
    if not qa_report.exists():
        return None
    match = re.search(r"Total structural records:\s*(\d+)", qa_report.read_text(encoding="utf-8", errors="replace"))
    return int(match.group(1)) if match else None


def candidate_sort_key(candidate: Candidate) -> tuple[int, float, str, str]:
    return (
        SEVERITY_RANK.get(candidate.severity, 9),
        -candidate.confidence,
        candidate.doc,
        str(candidate.original_record_index or ""),
    )


def label_from_rule(rule_name: str) -> str:
    if rule_name == "metadata_title_from_source_pdf":
        return "metadata_title_mismatch"
    if rule_name == "remove_non_semantic_control_characters":
        return "control_or_garbage_chars"
    if rule_name == "mark_figure_layout_text_informational":
        return "figure_or_diagram_text_leakage"
    if rule_name == "status_from_region_section_rule":
        return "section_status_mismatch"
    if rule_name == "add_stable_internal_id":
        return "record_id_collision_or_ambiguity"
    return rule_name


def _resolve_under(root: Path, value: Path) -> Path:
    return value if value.is_absolute() else root / value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit and repair generated Security Custom GPT knowledge files.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--parts-dir", type=Path, default=Path("outputs/Security/series_custom_gpt_knowledge_parts"))
    parser.add_argument("--manifest-csv", type=Path, default=Path("outputs/Security/series_manifest.csv"))
    parser.add_argument("--qa-report", type=Path, default=Path("outputs/Security/series_qa_report.md"))
    parser.add_argument("--output-dir", type=Path, default=Path("build"))
    parser.add_argument("--no-pdftotext", action="store_true", help="Disable local pdftotext title-page checks.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    result = run_audit(
        AuditConfig(
            repo_root=args.repo_root,
            parts_dir=args.parts_dir,
            manifest_csv=args.manifest_csv,
            qa_report=args.qa_report,
            output_dir=args.output_dir,
            use_pdftotext=not args.no_pdftotext,
        )
    )
    print(json.dumps(result.summary, indent=2, ensure_ascii=False))
