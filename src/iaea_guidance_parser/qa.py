from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import StructuralElement
from .rules import classify_status

STATUS_VALUES = {"Normative", "Informative", "Informational"}
REGION_VALUES = {"FrontMatter", "Body", "Appendix", "Annex", "References", "Glossary", "BackMatter"}
CONTINUATION_HEADING_WORDS = {"OF", "THE", "AND", "FOR", "IN", "TO", "WITH", "ON", "FROM", "BY"}

REQUIREMENT_MARKER_RE = re.compile(r"\bRequirement\s+\d+[A-Z]?:", re.I)
FOOTNOTE_BODY_RE = re.compile(r"(?<!\S)\d{1,2}\s+(?:The|In|For|See|This|Where|If|According)\b")
PAGE_FURNITURE_RE = re.compile(
    r"\b\d{1,3}\s+(?:Appendix\s+[IVXLC]+|Annex\s+[IVXLC]+|REFERENCES|INTERNATIONAL ATOMIC ENERGY[^.;]*)\b",
    re.I,
)
# Three or four dots are common editorial ellipses. TOC dot leaders are much
# longer, so require a run that is unlikely to occur in quoted prose.
TOC_ENTRY_RE = re.compile(r"(?:\.\s*){6,}\s*\d+\s*$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SUBSTITUTION_CIPHER_RE = re.compile(r"(?:,\$\(\$|6Dihw\\|3Ulqflsohv|9'0\)%6\s+7\)'96-8=)")
REPLACEMENT_CHARACTER_RE = re.compile(r"\ufffd")
PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")

DEFAULT_QA_CONFIG = {
    "paragraph_collision_threshold": 3,
    "short_text_chars": 90,
    "top_examples": 20,
}


@dataclass
class QAFinding:
    check: str
    severity: str
    doc_id: str
    record_id: str
    element_type: str
    element_id: str | None
    page: str
    section: str
    reason: str
    suggested_fix: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_series_qa(
    results: list[Any],
    failures: list[Any] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> list[QAFinding]:
    """Run deterministic, source-preserving QA checks over a parsed series."""
    failures = failures or []
    records = [record for result in results for record in result.records]
    manifest_doc_ids = {result.metadata.document_id for result in results}
    manifest_counts = {result.metadata.document_id: len(result.records) for result in results}
    findings = run_records_qa(
        records,
        manifest_doc_ids=manifest_doc_ids,
        manifest_counts=manifest_counts,
        config=config,
    )
    for failure in failures:
        findings.append(
            QAFinding(
                check="parse_failure",
                severity="critical",
                doc_id="",
                record_id="",
                element_type="",
                element_id=None,
                page="",
                section="",
                reason=f"{failure.source_pdf}: {failure.error}",
                suggested_fix="Review the parser traceback and source PDF; this document is absent from the combined output.",
            )
        )
    return findings


def run_records_qa(
    records: list[StructuralElement],
    *,
    manifest_doc_ids: set[str] | None = None,
    manifest_counts: dict[str, int] | None = None,
    config: dict[str, Any] | None = None,
) -> list[QAFinding]:
    qa_config = {**DEFAULT_QA_CONFIG, **(config or {})}
    manifest_doc_ids = manifest_doc_ids or {record.document_id for record in records}
    findings: list[QAFinding] = []

    _check_schema(records, manifest_doc_ids, findings)
    _check_requirement_boundaries(records, findings)
    _check_footnote_contamination(records, findings)
    _check_page_furniture(records, findings)
    _check_heading_splits(records, findings)
    _check_toc_pollution(records, findings)
    _check_table_paragraph_collisions(records, findings, qa_config)
    _check_table_contamination(records, findings)
    _check_status_region(records, findings)
    _check_text_quality(records, findings)
    _check_local_id_scope(records, findings, qa_config)
    _check_manifest_consistency(records, manifest_doc_ids, manifest_counts or {}, findings)
    _check_record_id_collisions(records, findings)
    return findings


def summarize_findings(findings: Iterable[QAFinding]) -> dict[str, Any]:
    finding_list = list(findings)
    return {
        "total_findings": len(finding_list),
        "by_check": dict(sorted(Counter(f.check for f in finding_list).items())),
        "by_severity": dict(sorted(Counter(f.severity for f in finding_list).items())),
        "by_document": dict(sorted(Counter(f.doc_id or "<series>" for f in finding_list).items())),
    }


def write_qa_json(
    path: Path, findings: list[QAFinding], *, metadata: dict[str, Any] | None = None
) -> None:
    payload = {
        "metadata": metadata or {},
        "summary": summarize_findings(findings),
        "findings": [finding.to_dict() for finding in findings],
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_qa_markdown(
    path: Path,
    findings: list[QAFinding],
    *,
    title: str = "QA report",
    metadata: dict[str, Any] | None = None,
    top_examples: int = 20,
) -> None:
    summary = summarize_findings(findings)
    by_severity = Counter(f.severity for f in findings)
    by_check = Counter(f.check for f in findings)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(
        findings,
        key=lambda f: (severity_order.get(f.severity, 9), f.doc_id, f.page, f.record_id),
    )

    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        if metadata:
            f.write("## Run metadata\n\n")
            for key, value in metadata.items():
                if value not in (None, "", [], {}):
                    f.write(f"- {key}: `{value}`\n")
            f.write("\n")
        f.write("## Summary\n\n")
        f.write(f"- Total findings: {summary['total_findings']}\n")
        for severity in ["critical", "high", "medium", "low"]:
            f.write(f"- {severity}: {by_severity.get(severity, 0)}\n")
        f.write("\n## Counts by check\n\n")
        if by_check:
            for check, count in sorted(by_check.items()):
                f.write(f"- {check}: {count}\n")
        else:
            f.write("No QA findings.\n")
        f.write("\n## Highest severity examples\n\n")
        if not sorted_findings:
            f.write("No examples to report.\n")
        for finding in sorted_findings[:top_examples]:
            section = finding.section or "<none>"
            f.write(
                f"- `{finding.severity}` `{finding.check}` `{finding.doc_id}` "
                f"`{finding.record_id}` p. {finding.page or '?'}; section: {section}. "
                f"{finding.reason} Suggested fix: {finding.suggested_fix}\n"
            )
        f.write("\n## Table Layout Warning\n\n")
        f.write(
            "Do not rely on exact table layout without PDF verification. The parser preserves raw table text, "
            "but complex row and column boundaries can require manual review.\n"
        )


def _finding(
    record: StructuralElement, check: str, severity: str, reason: str, suggested_fix: str
) -> QAFinding:
    return QAFinding(
        check=check,
        severity=severity,
        doc_id=record.document_id,
        record_id=record.record_id,
        element_type=record.element_type,
        element_id=record.element_id,
        page=_page_range(record),
        section=" > ".join(record.section_path),
        reason=reason,
        suggested_fix=suggested_fix,
    )


def _page_range(record: StructuralElement) -> str:
    return (
        str(record.page_start_pdf)
        if record.page_start_pdf == record.page_end_pdf
        else f"{record.page_start_pdf}-{record.page_end_pdf}"
    )


def _check_schema(
    records: list[StructuralElement], manifest_doc_ids: set[str], findings: list[QAFinding]
) -> None:
    for record in records:
        if record.text_status not in STATUS_VALUES:
            findings.append(
                _finding(
                    record,
                    "schema_validation",
                    "critical",
                    f"Invalid status `{record.text_status}`.",
                    "Fix status classification.",
                )
            )
        if record.source_region not in REGION_VALUES:
            findings.append(
                _finding(
                    record,
                    "schema_validation",
                    "critical",
                    f"Invalid region `{record.source_region}`.",
                    "Fix region detection.",
                )
            )
        if record.document_id not in manifest_doc_ids:
            findings.append(
                _finding(
                    record,
                    "schema_validation",
                    "critical",
                    "Record doc ID is absent from the manifest.",
                    "Regenerate the series from one manifest.",
                )
            )
        if not record.text.strip():
            findings.append(
                _finding(
                    record,
                    "schema_validation",
                    "medium",
                    "Record text is empty.",
                    "Suppress empty records or inspect source extraction.",
                )
            )


def _check_requirement_boundaries(
    records: list[StructuralElement], findings: list[QAFinding]
) -> None:
    by_doc: dict[str, list[StructuralElement]] = defaultdict(list)
    for record in records:
        by_doc[record.document_id].append(record)
        if record.element_type not in {"paragraph", "text_block"}:
            continue
        match = REQUIREMENT_MARKER_RE.search(record.text)
        if not match:
            continue
        severity = "high" if match.start() > 0 else "medium"
        findings.append(
            _finding(
                record,
                "requirement_boundary",
                severity,
                "Requirement marker appears inside a paragraph/text block instead of a requirement record.",
                "Split at the requirement marker and emit a `requirement` record.",
            )
        )

    for doc_id, doc_records in by_doc.items():
        doc_type = (doc_records[0].document_type or "").lower()
        if "requirements" not in doc_type:
            continue
        if len(doc_records) < 30:
            continue
        if any(
            record.element_type == "paragraph"
            and record.element_id
            and re.match(r"^\d{3}(?:\.\d+)*$", record.element_id)
            for record in doc_records
        ):
            # Older requirements publications (notably the Transport
            # Regulations) express requirements directly in three-digit
            # numbered paragraphs rather than "Requirement N:" blocks.
            continue
        if not any(record.element_type == "requirement" for record in doc_records):
            sample = doc_records[0]
            findings.append(
                _finding(
                    sample,
                    "requirement_boundary",
                    "high",
                    "Safety Requirements document has no explicit requirement records.",
                    "Inspect requirement heading detection and split requirement blocks before paragraph reconstruction.",
                )
            )


def _check_footnote_contamination(
    records: list[StructuralElement], findings: list[QAFinding]
) -> None:
    footnotes_by_doc = Counter(
        record.document_id for record in records if record.element_type == "footnote"
    )
    contaminated_docs: set[str] = set()
    for record in records:
        if record.element_type not in {"paragraph", "requirement", "text_block"}:
            continue
        if not FOOTNOTE_BODY_RE.search(record.text):
            continue
        contaminated_docs.add(record.document_id)
        severity = "high" if record.text_status == "Normative" else "medium"
        findings.append(
            _finding(
                record,
                "footnote_contamination",
                severity,
                "Footnote-like body text appears inside substantive content.",
                "Split footnote bodies into `footnote` records or manually verify if the number is a list item.",
            )
        )
    for doc_id in contaminated_docs:
        if footnotes_by_doc.get(doc_id):
            continue
        sample = next(record for record in records if record.document_id == doc_id)
        findings.append(
            _finding(
                sample,
                "footnote_contamination",
                "medium",
                "Footnote-like text was found, but the document has zero footnote records.",
                "Review footnote detection for this PDF.",
            )
        )


def _check_page_furniture(records: list[StructuralElement], findings: list[QAFinding]) -> None:
    for record in records:
        if record.element_type not in {"paragraph", "requirement", "text_block"}:
            continue
        if PAGE_FURNITURE_RE.search(record.text):
            severity = "high" if record.source_region in {"Body", "Appendix"} else "low"
            findings.append(
                _finding(
                    record,
                    "header_footer_leakage",
                    severity,
                    "Page header/footer marker appears inside content.",
                    "Strip repeated page furniture before paragraph reconstruction.",
                )
            )


def _check_heading_splits(records: list[StructuralElement], findings: list[QAFinding]) -> None:
    previous: StructuralElement | None = None
    reported_path_fragments: set[tuple[str, str]] = set()
    for record in records:
        if (
            previous
            and previous.document_id == record.document_id
            and previous.page_start_pdf == record.page_start_pdf
        ):
            if (
                previous.element_type == "heading"
                and record.element_type == "heading"
                and _ends_with_continuation_word(previous.text)
            ):
                findings.append(
                    _finding(
                        previous,
                        "heading_split",
                        "medium",
                        "Adjacent heading records appear to be a wrapped heading.",
                        "Merge contiguous heading fragments before updating section paths.",
                    )
                )
        fragment = next(
            (part for part in record.section_path if _ends_with_continuation_word(part)), None
        )
        fragment_key = (record.document_id, fragment or "")
        if fragment and fragment_key not in reported_path_fragments:
            reported_path_fragments.add(fragment_key)
            findings.append(
                _finding(
                    record,
                    "heading_split",
                    "medium",
                    "Section path contains a fragment ending in a continuation word.",
                    "Rebuild section hierarchy after heading-fragment repair.",
                )
            )
        previous = record


def _check_toc_pollution(records: list[StructuralElement], findings: list[QAFinding]) -> None:
    for record in records:
        if record.source_region == "Body" and TOC_ENTRY_RE.search(record.text):
            findings.append(
                _finding(
                    record,
                    "toc_pollution",
                    "high",
                    "Body record contains dot leaders typical of a table of contents.",
                    "Keep TOC entries in FrontMatter or suppress them from substantive records.",
                )
            )
        if any(
            TOC_ENTRY_RE.search(part) or re.search(r"\(\d+(?:[–-]\d+)?\)\s*\d+$", part)
            for part in record.section_path
        ):
            findings.append(
                _finding(
                    record,
                    "toc_pollution",
                    "medium",
                    "Section path appears to contain TOC text.",
                    "Prevent TOC headings from updating the active Body section stack.",
                )
            )


def _check_table_paragraph_collisions(
    records: list[StructuralElement], findings: list[QAFinding], config: dict[str, Any]
) -> None:
    threshold = int(config["paragraph_collision_threshold"])
    short_text_chars = int(config["short_text_chars"])
    paragraph_groups: dict[tuple[str, str, str, str], list[StructuralElement]] = defaultdict(list)
    by_document: dict[str, list[StructuralElement]] = defaultdict(list)
    for record in records:
        by_document[record.document_id].append(record)
        if record.element_type == "paragraph" and record.element_id:
            scope = record.parent_element_id or ""
            if record.source_region in {"Appendix", "Annex"} and record.section_path:
                scope = record.section_path[0]
            paragraph_groups[
                (record.document_id, record.source_region, scope, record.element_id)
            ].append(record)

    for group in paragraph_groups.values():
        if len(group) <= threshold:
            continue
        short_records = [record for record in group if len(record.text) <= short_text_chars]
        if len(short_records) < max(2, len(group) // 2):
            continue
        for record in short_records[:threshold]:
            findings.append(
                _finding(
                    record,
                    "table_paragraph_collision",
                    "medium",
                    "Repeated short paragraph ID looks like table cell text parsed as paragraphs.",
                    "Detect table regions before paragraph segmentation and preserve these rows in a table record.",
                )
            )

    for table in (record for record in records if record.element_type == "table"):
        short_paragraphs = [
            record
            for record in by_document[table.document_id]
            if record.element_type == "paragraph"
            and table.page_start_pdf <= record.page_start_pdf <= table.page_end_pdf
            and len(record.text) <= short_text_chars
        ]
        if len(short_paragraphs) > threshold:
            findings.append(
                _finding(
                    table,
                    "table_paragraph_collision",
                    "medium",
                    "Table page has many very short paragraph records.",
                    "Review whether table cell labels escaped the table detector.",
                )
            )


def _check_table_contamination(records: list[StructuralElement], findings: list[QAFinding]) -> None:
    embedded_figure = re.compile(r"(?m)^FIG\.\s+[A-Z0-9IVXLCDM.–()]+[.:]\s+")
    embedded_region = re.compile(
        r"(?m)^(?:REFERENCES|GLOSSARY|RELATED PUBLICATIONS|CONTRIBUTORS TO DRAFTING AND REVIEW)$"
    )
    for table in (record for record in records if record.element_type == "table"):
        if embedded_figure.search(table.text):
            findings.append(
                _finding(
                    table,
                    "table_contamination",
                    "high",
                    "A figure caption appears inside a table record.",
                    "End the table at the figure boundary and keep diagram text out of the table payload.",
                )
            )
        elif embedded_region.search(table.text):
            findings.append(
                _finding(
                    table,
                    "table_contamination",
                    "high",
                    "A publication region heading appears inside a table record.",
                    "End the table before the region transition and rebuild the section boundary.",
                )
            )


def _check_status_region(records: list[StructuralElement], findings: list[QAFinding]) -> None:
    for record in records:
        expected_status, _ = classify_status(
            element_type=record.element_type,
            source_region=record.source_region,
            element_id=record.element_id,
            section_path=record.section_path,
        )
        if record.text_status != expected_status:
            findings.append(
                _finding(
                    record,
                    "status_region_consistency",
                    "critical",
                    f"Status is `{record.text_status}` but rule-based status is `{expected_status}`.",
                    "Reclassify status using region, section number and element type rules.",
                )
            )


def _check_text_quality(records: list[StructuralElement], findings: list[QAFinding]) -> None:
    for record in records:
        if CONTROL_RE.search(record.text):
            findings.append(
                _finding(
                    record,
                    "text_quality",
                    "low",
                    "Disallowed control character appears in record text.",
                    "Remove non-printing control characters with no semantic value.",
                )
            )
        if SUBSTITUTION_CIPHER_RE.search(record.text):
            findings.append(
                _finding(
                    record,
                    "font_substitution_cipher",
                    "high",
                    "Text contains a known substitution-font cipher signature.",
                    "Add a font-, document- and page-scoped decoder only after verifying the source glyph mapping.",
                )
            )
        if REPLACEMENT_CHARACTER_RE.search(record.text):
            findings.append(
                _finding(
                    record,
                    "replacement_character",
                    "high",
                    "Text contains a Unicode replacement character.",
                    "Verify the affected glyph against the source page before repairing it.",
                )
            )
        if PRIVATE_USE_RE.search(record.text):
            findings.append(
                _finding(
                    record,
                    "private_use_glyph",
                    "high",
                    "Text contains a private-use glyph with no portable semantic meaning.",
                    "Review the page image and preserve the source until the glyph can be resolved unambiguously.",
                )
            )
        hyphenation = re.search(r"\b[A-Za-z]{3,}-\s+(?P<right>[a-z]{3,})", record.text)
        if hyphenation and hyphenation.group("right") not in {"and", "und"}:
            findings.append(
                _finding(
                    record,
                    "text_quality",
                    "low",
                    "Hyphenation artifact remains in record text.",
                    "Join PDF line-break hyphenation where it is clearly an ordinary word break.",
                )
            )
        printed_values = [record.page_start_printed, record.page_end_printed]
        implausible = [
            value
            for value in printed_values
            if value and value.isdigit() and int(value) > record.page_end_pdf + 20
        ]
        if implausible:
            findings.append(
                _finding(
                    record,
                    "printed_page_plausibility",
                    "high",
                    f"Printed page value `{implausible[0]}` is implausible for PDF page {record.page_end_pdf}.",
                    "Use page-margin geometry and neighbouring-page evidence before removing a numeral as page furniture.",
                )
            )


def _check_local_id_scope(
    records: list[StructuralElement],
    findings: list[QAFinding],
    config: dict[str, Any],
) -> None:
    """Flag heavily repeated body labels that have no explicit local parent."""
    threshold = int(config["paragraph_collision_threshold"])
    groups: dict[tuple[str, str, str], list[StructuralElement]] = defaultdict(list)
    for record in records:
        if record.element_type != "paragraph" or not record.element_id:
            continue
        groups[(record.document_id, record.source_region, record.element_id)].append(record)

    for (_doc_id, region, element_id), group in groups.items():
        if region != "Body" or len(group) <= threshold:
            continue
        if any(record.parent_element_id or record.extra.get("local_scope") for record in group):
            continue
        findings.append(
            _finding(
                group[0],
                "duplicate_local_id_without_scope",
                "medium",
                f"Paragraph label `{element_id}` occurs {len(group)} times in Body without a local parent scope.",
                "Verify whether the source restarts numbering inside schedules, forms or table-like regions and configure a local scope if it does.",
            )
        )


def _check_manifest_consistency(
    records: list[StructuralElement],
    manifest_doc_ids: set[str],
    manifest_counts: dict[str, int],
    findings: list[QAFinding],
) -> None:
    record_doc_ids = {record.document_id for record in records}
    for missing_doc_id in sorted(manifest_doc_ids - record_doc_ids):
        findings.append(
            QAFinding(
                check="manifest_consistency",
                severity="critical",
                doc_id=missing_doc_id,
                record_id="",
                element_type="",
                element_id=None,
                page="",
                section="",
                reason="Manifest document has no records.",
                suggested_fix="Regenerate the series or inspect parse failure logs.",
            )
        )
    for extra_doc_id in sorted(record_doc_ids - manifest_doc_ids):
        sample = next(record for record in records if record.document_id == extra_doc_id)
        findings.append(
            _finding(
                sample,
                "manifest_consistency",
                "critical",
                "Record document ID is absent from manifest.",
                "Fail closed; regenerate outputs from a single parse run.",
            )
        )
    if manifest_counts:
        actual_counts = Counter(record.document_id for record in records)
        for doc_id, expected_count in manifest_counts.items():
            if actual_counts.get(doc_id, 0) != expected_count:
                sample = next((record for record in records if record.document_id == doc_id), None)
                if sample:
                    findings.append(
                        _finding(
                            sample,
                            "manifest_consistency",
                            "critical",
                            f"Manifest record count {expected_count} differs from parsed count {actual_counts.get(doc_id, 0)}.",
                            "Regenerate manifest and parts together from the same run.",
                        )
                    )


def _check_record_id_collisions(
    records: list[StructuralElement], findings: list[QAFinding]
) -> None:
    counts = Counter(record.record_id for record in records)
    for record in records:
        if counts[record.record_id] > 1:
            findings.append(
                _finding(
                    record,
                    "record_id_collision_or_ambiguity",
                    "critical",
                    "Record ID is duplicated.",
                    "Add a stable internal ID component while preserving official paragraph numbers separately.",
                )
            )


def _ends_with_continuation_word(text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", text.upper())
    return bool(words and words[-1] in CONTINUATION_HEADING_WORDS)
