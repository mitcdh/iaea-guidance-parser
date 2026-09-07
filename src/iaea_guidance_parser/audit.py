"""Compare canonical records with independently extracted source pages.

This is a review tool, not a second parser or an automatic correction pass.
Token differences are candidates: page images decide whether a difference is
an extraction defect, legitimate normalization, or intentionally excluded text.
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import subprocess
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz

from .outline import structural_proof
from .provenance import fingerprint, runtime_identity, sha256_file
from .reviews import (
    apply_dispositions,
    content_fingerprint,
    finding_id,
    matching_review,
    metadata_fingerprint,
    validated_equivalent_review,
)

TOKEN_RE = re.compile(r"\w+(?:[.\-]\w+)*|[≤≥<>±=×÷/%+−]", re.UNICODE)
SOURCE_CAPTION_RE = re.compile(r"^\s*(?:TABLE|FIG\.|Fig\.)\s+[\dA-ZIVXLC]", re.MULTILINE)
SOURCE_FOOTNOTE_RE = re.compile(
    r"""^[ \t]*(?:\d{1,2}|[*†‡])[ \t]+(?=[^\W\d_]|[‘“"'])""", re.MULTILINE
)


def tokens(text: str) -> list[str]:
    """Normalize typography while retaining numbers and technical operators."""
    text = re.sub(r"(?<=[a-z])[-‑]\s*\n\s*(?=[a-z])", "", text)
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    return TOKEN_RE.findall(text.casefold())


DISPLAY_POLICY = "displayed-cropbox-v1"


def displayed_page_text(pdf: Path, number: int) -> str:
    """Clip Poppler text explicitly; -cropbox alone can retain opposing spread text."""
    with fitz.open(pdf) as document:
        if not 1 <= number <= len(document):
            raise ValueError("Source page number must be inside the PDF")
        page = document[number - 1]
        media = fitz.Rect(0, 0, page.mediabox.width, page.mediabox.height)
        crop = fitz.Rect(page.cropbox)
        crop.x0 -= page.mediabox.x0
        crop.x1 -= page.mediabox.x0
        rotation = fitz.Matrix(page.rotation)
        origin = (media * rotation).top_left
        crop = crop * rotation
        x, y = math.floor(crop.x0 - origin.x), math.floor(crop.y0 - origin.y)
        width = math.ceil(crop.x1 - origin.x) - x
        height = math.ceil(crop.y1 - origin.y) - y
    result = subprocess.run(
        [
            "pdftotext",
            "-f",
            str(number),
            "-l",
            str(number),
            "-r",
            "72",
            "-x",
            str(x),
            "-y",
            str(y),
            "-W",
            str(width),
            "-H",
            str(height),
            "-layout",
            "-enc",
            "UTF-8",
            str(pdf),
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=180,
    )
    return result.stdout.decode("utf-8").removesuffix("\f")


def _source_pages(pdf: Path, cache: Path | None = None) -> list[str]:
    cached = cache / DISPLAY_POLICY / (sha256_file(pdf) + ".json") if cache else None
    if cached and cached.exists():
        payload = json.loads(cached.read_text())
        if payload.get("text_sha256") == fingerprint(payload["pages"]):
            return payload["pages"]
    completed = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
        capture_output=True,
        check=True,
        timeout=180,
    )
    pages = completed.stdout.decode("utf-8").split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    with fitz.open(pdf) as document:
        for number, page in enumerate(document, 1):
            if page.cropbox != page.mediabox:
                pages[number - 1] = displayed_page_text(pdf, number)
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(
            json.dumps({"pages": pages, "text_sha256": fingerprint(pages)}, ensure_ascii=False)
        )
    return pages


def _record_source_text(record: dict[str, Any]) -> str:
    text = record["text"]
    label = record.get("extra", {}).get("source_label", "")
    if not label and record["element_type"] in {"footnote", "reference"}:
        label = record.get("element_id") or ""
    if label and not text.startswith(label):
        text = label + " " + text
    visual_text = record.get("extra", {}).get("visual_text_regions", [])
    if visual_text:
        text += "\n" + "\n".join(region["text"] for region in visual_text)
    return text


def _finding(doc, page, check, reason, *, severity="medium", **evidence):
    return {
        "document_id": doc,
        "pdf_page": page,
        "check": check,
        "severity": severity,
        "disposition": "unresolved",
        "reason": reason,
        "evidence": evidence,
    }


def _load_records(path: Path) -> dict[str, list[dict[str, Any]]]:
    documents = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            documents[record["document_id"]].append(record)
    return dict(documents)


def _source_path(entry, source_root, pdfs):
    supplied = Path(entry["source_pdf"])
    if supplied.is_file() and supplied.resolve().is_relative_to(source_root.resolve()):
        return supplied
    matches = [p for p in pdfs if p.name == supplied.name]
    return matches[0] if len(matches) == 1 else None


def _page_risks(page, source_text, records, pdf_page, last_page, title, source_heading=False):
    reasons = set()
    if SOURCE_CAPTION_RE.search(source_text):
        reasons.add("source_caption")
    if any(r["element_type"] in {"table", "figure"} for r in records):
        reasons.add("parsed_table_or_figure")
    # An outline proves headings, but cannot establish a note's source anchor.
    if any(r["element_type"] == "footnote" for r in records):
        reasons.add("footnote")
    # Independent labels also catch notes that were absorbed into ordinary prose.
    # Numbered lists may also match: ambiguity requires review, not a guessed role.
    if SOURCE_FOOTNOTE_RE.search(source_text):
        reasons.add("source_footnote_candidate")
    # Bookmark headings do not prove individual term/definition relationships.
    if any(r["source_region"] == "Glossary" for r in records) or re.search(
        r"^\s*(?:DEFINITIONS|GLOSSARY)\s*$", source_text, re.MULTILINE
    ):
        reasons.add("definitions")
    if source_heading or any(r["element_type"] == "heading" for r in records):
        reasons.add("structure")
    compact_source = " ".join(tokens(source_text))
    compact_title = " ".join(tokens(title))
    if pdf_page == 1 or (
        pdf_page <= 10
        and (
            (compact_title and compact_title in compact_source)
            or re.search(r"ISBN|STI/PUB", source_text)
        )
    ):
        reasons.add("metadata")
    if pdf_page == last_page:
        reasons.add("end_of_document")
    # Source geometry also finds captionless grids that the parser may miss.
    content = fitz.Rect(
        page.rect.width * 0.04,
        page.rect.height * 0.05,
        page.rect.width * 0.96,
        page.rect.height * 0.95,
    )
    drawings = [
        item
        for item in page.get_drawings()
        if content.contains(item["rect"]) and max(item["rect"].width, item["rect"].height) > 20
    ]
    if len(drawings) >= 4 or any(
        item["rect"].get_area() > page.rect.get_area() * 0.03 for item in drawings
    ):
        reasons.add("source_drawing")
    images = [
        item
        for item in page.get_image_info()
        if fitz.Rect(item["bbox"]).get_area() > page.rect.get_area() * 0.03
    ]
    if images:
        reasons.add("source_image")
    if len(tokens(source_text)) < 5 and (images or drawings):
        reasons.add("little_extractable_text")
    return reasons


def _review_pages(pdf, entry, records, review_entries, metadata=None, cache=None):
    metadata = metadata or {}
    source = _source_pages(pdf, cache)
    doc_id = entry["document_id"]
    source_hash = sha256_file(pdf)
    by_page = defaultdict(list)
    findings = []
    for record in records:
        start, end = record["page_start_pdf"], record["page_end_pdf"]
        if start < 1 or end < start or end > len(source):
            findings.append(
                _finding(
                    doc_id,
                    start,
                    "page_range",
                    "Invalid record page range.",
                    severity="high",
                    record_id=record["record_id"],
                )
            )
        for number in range(max(1, start), min(end, len(source)) + 1):
            by_page[number].append(record)
    ledger = []
    with fitz.open(pdf) as document:
        if len(source) != len(document):
            findings.append(
                _finding(
                    doc_id,
                    0,
                    "extraction_page_count",
                    "Poppler and PDF page counts differ.",
                    severity="high",
                    poppler=len(source),
                    pdf=len(document),
                )
            )
        source_heading_pages = {entry[2] for entry in document.get_toc()}
        for number, page in enumerate(document, 1):
            source_text = source[number - 1] if number <= len(source) else ""
            page_records = by_page[number]
            for record in page_records:
                for note in record.get("parser_notes", []):
                    if record["page_start_pdf"] == number and note.startswith(
                        ("Unresolved figure", "Unresolved table")
                    ):
                        findings.append(
                            _finding(
                                doc_id,
                                number,
                                "layout_ownership_ambiguous",
                                note,
                                record_id=record["record_id"],
                                element_id=record["element_id"],
                            )
                        )
                if record["page_start_pdf"] == number and any(
                    note.startswith("Unresolved PDF footnote anchor")
                    for note in record.get("parser_notes", [])
                ):
                    findings.append(
                        _finding(
                            doc_id,
                            number,
                            "footnote_anchor_ambiguous",
                            "PDF note has no uniquely supported inline anchor.",
                            record_id=record["record_id"],
                            element_id=record["element_id"],
                        )
                    )
            output_text = "\n".join(_record_source_text(r) for r in page_records)
            expected = Counter(tokens(source_text))
            available = Counter(tokens(output_text))
            missing = expected - available
            # Margin numerals are expected to disappear from substantive text.
            printed_labels = {
                str(r[key])
                for r in page_records
                for key, page_key in (
                    ("page_start_printed", "page_start_pdf"),
                    ("page_end_printed", "page_end_pdf"),
                )
                if r.get(key) is not None and r[page_key] == number
            }
            margin_numbers = set()
            for block in page.get_text("blocks"):
                value = str(block[4]).strip()
                if (
                    value.isdigit()
                    and value in printed_labels
                    and (
                        block[3] <= page.rect.height * 0.12 + 2
                        or block[1] >= page.rect.height * 0.88 - 2
                    )
                ):
                    margin_numbers.add(value)
            excluded = {}
            for token in margin_numbers:
                if missing[token]:
                    excluded[token] = 1
                    missing[token] -= 1
                    if not missing[token]:
                        del missing[token]
            risks = _page_risks(
                page,
                source_text,
                page_records,
                number,
                len(document),
                entry.get("document_title", ""),
                source_heading=number in source_heading_pages,
            )
            if any(
                note.startswith("Unresolved")
                for r in page_records
                for note in r.get("parser_notes", [])
            ):
                risks.add("association")
            if missing:
                risks.add("text_difference")
                findings.append(
                    _finding(
                        doc_id,
                        number,
                        "source_text_difference",
                        "Source tokens are not accounted for within overlapping record page ranges.",
                        missing_tokens=dict(missing),
                        expected_tokens=sum(expected.values()),
                        source_excerpt=source_text[:700],
                    )
                )
            if len(expected) >= 5 and not page_records:
                risks.add("unrepresented_page")
            if re.search(r"[\ufffd\ue000-\uf8ff]", output_text):
                risks.add("unresolved_glyph")
                findings.append(
                    _finding(
                        doc_id,
                        number,
                        "unresolved_glyph",
                        "Output contains a replacement or private-use character.",
                        severity="high",
                    )
                )
            page_output_hash = content_fingerprint(page_records)
            review = matching_review(
                review_entries.get((doc_id, number), {}),
                source_hash=source_hash,
                records=page_records,
                metadata=metadata,
            )
            proof = (
                structural_proof(document, number, page_records, source_text)
                if risks == {"structure"}
                else None
            )
            # Only this independently recomputed proof can establish automation.
            if review and review["method"] == "automated_structural":
                if not proof or review.get("evidence") != [proof]:
                    review = None
            if review and review["method"] == "equivalent_evidence":
                reference_page = review.get("reference_pdf_page")
                reference = matching_review(
                    review_entries.get((doc_id, reference_page), {}),
                    source_hash=source_hash,
                    records=by_page.get(reference_page, []),
                    metadata=metadata,
                )
                if not validated_equivalent_review(
                    review,
                    reference,
                    target_records=page_records,
                    reference_records=by_page.get(reference_page, []),
                    metadata=metadata,
                ):
                    review = None
            if not review and proof:
                review = {
                    "schema_version": 2,
                    "method": "automated_structural",
                    "verification_status": "verified",
                    "notes": "Exact bookmark tree, wording, geometry, order and paths agree.",
                    "evidence": [proof],
                    "dispositions": {},
                }
            page_findings = [f for f in findings if f["pdf_page"] == number]
            apply_dispositions(page_findings, review)
            reviewed = bool(review and review["method"] == "visual")
            method = review["method"] if review else ""
            status = (
                review["verification_status"] if review else "pending" if risks else "not_scheduled"
            )
            if (
                any(f["disposition"] == "unresolved" for f in page_findings)
                and status == "verified"
            ):
                status = "blocked"
            ledger.append(
                {
                    "document_id": doc_id,
                    "pdf_page": number,
                    "source_sha256": source_hash,
                    "output_sha256": page_output_hash,
                    "metadata_sha256": metadata_fingerprint(metadata),
                    "verification_method": method,
                    "verification_status": status,
                    "verification_evidence": json.dumps(
                        review.get("evidence", []) if review else [], ensure_ascii=False
                    ),
                    "source_token_count": sum(expected.values()),
                    "missing_token_count": sum(missing.values()),
                    "excluded_margin_numerals": json.dumps(excluded, ensure_ascii=False),
                    "record_count": len(page_records),
                    "text_check": "checked",
                    "visual_reasons": ";".join(sorted(risks)),
                    "visual_review": "reviewed"
                    if reviewed
                    else "not_required"
                    if status == "verified"
                    else "pending"
                    if risks
                    else "not_scheduled",
                    "notes": review.get("notes", "") if review else "",
                }
            )
    # Whole-document multiplicity catches duplication masked by page windows.
    expected = Counter(tokens("\n".join(source)))
    actual = Counter(tokens("\n".join(_record_source_text(r) for r in records)))
    extra = actual - expected
    if extra:
        findings.append(
            _finding(
                doc_id,
                0,
                "output_token_difference",
                "Output token multiplicities exceed the independent extraction; review reading order, normalization and duplication.",
                extra_tokens=dict(extra),
            )
        )
    document_review = matching_review(
        review_entries.get((doc_id, 0), {}),
        source_hash=source_hash,
        records=records,
        metadata=metadata,
    )
    if document_review and document_review["method"] != "visual":
        document_review = None
    apply_dispositions([f for f in findings if f["pdf_page"] == 0], document_review)
    document_coverage = {
        "document_id": doc_id,
        "pdf_page": 0,
        "source_sha256": source_hash,
        "output_sha256": content_fingerprint(records),
        "metadata_sha256": metadata_fingerprint(metadata),
        "metadata_verified": bool(document_review and document_review.get("metadata_verified")),
        "boundaries_verified": bool(document_review and document_review.get("boundaries_verified")),
        "verification_status": document_review["verification_status"]
        if document_review
        else "pending",
    }
    return ledger, findings, document_coverage


def _check_exports(parsed, records_by_doc, entries):
    findings = []
    manifest_ids = [entry["document_id"] for entry in entries]
    if len(set(manifest_ids)) != len(manifest_ids):
        findings.append(
            _finding(
                "",
                0,
                "duplicate_manifest_id",
                "Manifest document IDs are not unique.",
                severity="high",
            )
        )
    for doc_id in records_by_doc.keys() - set(manifest_ids):
        findings.append(
            _finding(
                doc_id,
                0,
                "orphan_records",
                "Canonical records have no manifest document.",
                severity="high",
            )
        )
    record_ids = [record["record_id"] for records in records_by_doc.values() for record in records]
    if len(set(record_ids)) != len(record_ids):
        findings.append(
            _finding(
                "",
                0,
                "duplicate_record_id",
                "Canonical record IDs are not unique.",
                severity="high",
            )
        )
    for entry in entries:
        doc_id = entry["document_id"]
        records = records_by_doc.get(doc_id, [])
        for record in records:
            if re.search(r"\[\[TABLE:p\d+:\d+\]\]", json.dumps(record)):
                findings.append(
                    _finding(
                        doc_id,
                        record["page_start_pdf"],
                        "unconsumed_layout_marker",
                        "An internal table marker escaped into the exported record.",
                        severity="high",
                        record_id=record["record_id"],
                    )
                )
        from .series import safe_path_component

        per_doc = parsed / "documents" / safe_path_component(doc_id) / "structural_index.jsonl"
        if not per_doc.is_file():
            findings.append(
                _finding(
                    doc_id,
                    0,
                    "missing_document_output",
                    "Document index is missing.",
                    severity="high",
                )
            )
        elif [json.loads(line) for line in per_doc.read_text().splitlines()] != records:
            findings.append(
                _finding(
                    doc_id,
                    0,
                    "document_index_mismatch",
                    "Document and combined records differ.",
                    severity="high",
                )
            )
        if len(records) != entry["record_count"]:
            findings.append(
                _finding(
                    doc_id,
                    0,
                    "record_count_mismatch",
                    "Record count differs from manifest.",
                    severity="high",
                )
            )
    # Check the actual serialization, not just counts or a hash of a manifest.
    from .exporters import _record_markdown, to_custom_gpt_record
    from .models import StructuralElement

    all_records = [r for entry in entries for r in records_by_doc.get(entry["document_id"], [])]
    knowledge = parsed / "series_custom_gpt_knowledge.jsonl"
    expected = [to_custom_gpt_record(StructuralElement(**r)) for r in all_records]
    if (
        not knowledge.is_file()
        or [json.loads(line) for line in knowledge.read_text().splitlines()] != expected
    ):
        findings.append(
            _finding(
                "",
                0,
                "knowledge_index_mismatch",
                "Knowledge JSONL differs from canonical records.",
                severity="high",
            )
        )
    parts = sorted((parsed / "series_custom_gpt_knowledge_parts").glob("part_*.md"))
    expected_blocks = [_record_markdown(StructuralElement(**r)).strip() for r in all_records]
    actual_blocks = []
    for part in parts:
        text = part.read_text(encoding="utf-8")
        # A generated document heading may follow the preceding record.
        for match in re.finditer(
            r"\n---\ndoc: .*?(?=\n---\ndoc: |\n\n# Document: |\Z)", text, re.DOTALL
        ):
            actual_blocks.append(match.group(0).strip())
    if actual_blocks != expected_blocks:
        findings.append(
            _finding(
                "",
                0,
                "markdown_content_mismatch",
                "Markdown record content/order differs from canonical records.",
                severity="high",
                expected_blocks=len(expected_blocks),
                actual_blocks=len(actual_blocks),
            )
        )
    return findings


def run_audit(
    source_root: Path,
    parsed: Path,
    out: Path,
    *,
    reviews: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Audit one complete series; never rewrite or automatically repair records."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("Source auditing requires Poppler's pdftotext executable.")
    audit_runtime = runtime_identity()
    manifest_path = parsed / "series_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records_by_doc = _load_records(parsed / "series_structural_index.jsonl")
    entries = [entry for entry in manifest["documents"] if entry["status"] == "ok"]
    pdfs = sorted(p for p in source_root.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    review_entries = {}
    if reviews:
        for line in reviews.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            review_entries[(row["document_id"], row["pdf_page"])] = row
    out.mkdir(parents=True, exist_ok=True)
    findings = _check_exports(parsed, records_by_doc, entries)
    represented = set()
    ledger = []
    document_ledger = []
    for entry in entries:
        doc_id = entry["document_id"]
        if progress:
            progress(f"Auditing {doc_id}")
        pdf = _source_path(entry, source_root, pdfs)
        if pdf is None:
            findings.append(
                _finding(
                    doc_id, 0, "missing_source", "Source PDF missing or ambiguous.", severity="high"
                )
            )
            continue
        represented.add(pdf.resolve())
        if sha256_file(pdf) != entry["source_sha256"]:
            findings.append(
                _finding(
                    doc_id,
                    0,
                    "source_hash_mismatch",
                    "PDF differs from parsed source.",
                    severity="high",
                )
            )
        try:
            from .series import safe_path_component

            metadata_path = parsed / "documents" / safe_path_component(doc_id) / "metadata.json"
            metadata = json.loads(metadata_path.read_text())
            pages, page_findings, document_coverage = _review_pages(
                pdf,
                entry,
                records_by_doc.get(doc_id, []),
                review_entries,
                metadata,
                out / "source_text_cache",
            )
            document_ledger.append(document_coverage)
            ledger.extend(pages)
            findings.extend(page_findings)
        except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as exc:
            findings.append(_finding(doc_id, 0, "source_audit_failure", str(exc), severity="high"))
    for pdf in pdfs:
        if pdf.resolve() not in represented:
            findings.append(
                _finding(
                    "",
                    0,
                    "unparsed_source",
                    "Source PDF is absent from the manifest.",
                    severity="high",
                    source_pdf=str(pdf),
                )
            )
    for part in manifest.get("output_parts", []):
        path = parsed / "series_custom_gpt_knowledge_parts" / Path(part["path"]).name
        if not path.is_file() or sha256_file(path) != part["sha256"]:
            findings.append(
                _finding(
                    "",
                    0,
                    "part_hash_mismatch",
                    "Markdown part hash differs from manifest.",
                    severity="high",
                    path=str(path),
                )
            )
    for finding in findings:
        finding["finding_id"] = finding_id(finding)
    summary = {
        "verification_by_method": dict(
            Counter(row["verification_method"] for row in ledger if row["verification_method"])
        ),
        "verification_by_status": dict(Counter(row["verification_status"] for row in ledger)),
        "metadata_documents_verified": sum(r["metadata_verified"] for r in document_ledger),
        "boundary_documents_verified": sum(r["boundaries_verified"] for r in document_ledger),
        "source_documents": len(pdfs),
        "manifest_documents": len(entries),
        "documents_checked": len({row["document_id"] for row in ledger}),
        "pages_checked": len(ledger),
        "findings": len(findings),
        "by_check": dict(Counter(row["check"] for row in findings)),
        "by_disposition": dict(Counter(row["disposition"] for row in findings)),
        "integrity_failures": sum(
            row["check"]
            not in {"source_text_difference", "output_token_difference", "unresolved_glyph"}
            for row in findings
        ),
        "visual_pages_reviewed": sum(row["visual_review"] == "reviewed" for row in ledger),
        "visual_pages_pending": sum(row["visual_review"] == "pending" for row in ledger),
        "manifest_sha256": sha256_file(manifest_path),
        "records_sha256": sha256_file(parsed / "series_structural_index.jsonl"),
        "audit_runtime": audit_runtime,
    }
    with (out / "audit_document_coverage.jsonl").open("w", encoding="utf-8") as stream:
        for row in document_ledger:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "audit_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (out / "audit_findings.jsonl").open("w", encoding="utf-8") as stream:
        for finding in findings:
            stream.write(json.dumps(finding, ensure_ascii=False) + "\n")
    with (out / "audit_coverage.csv").open("w", encoding="utf-8", newline="") as stream:
        if ledger:
            writer = csv.DictWriter(stream, fieldnames=list(ledger[0]))
            writer.writeheader()
            writer.writerows(ledger)
    report = [
        "# Source audit",
        "",
        f"Checked {summary['documents_checked']} documents and {len(ledger)} physical PDF pages.",
        "",
        f"Visual review: {summary['visual_pages_reviewed']} reviewed; {summary['visual_pages_pending']} pending.",
        "",
        "Token comparisons are screening evidence, not a semantic accuracy score. Page windows can overlap, and independent PDF engines can disagree. Inspect source images to resolve differences.",
        "",
        f"Verification methods: {summary['verification_by_method']}. Statuses: {summary['verification_by_status']}.",
        "",
        f"Document checks: metadata {summary['metadata_documents_verified']}; boundaries {summary['boundary_documents_verified']} of {len(document_ledger)} verified.",
        "",
        "Automated structural evidence is recorded separately from visual inspection. A recorded inspection can remain blocked by an unresolved finding.",
        "",
        "## Findings",
        "",
    ]
    report.extend(f"- {check}: {count}" for check, count in sorted(summary["by_check"].items()))
    report += [
        "",
        "See `audit_findings.jsonl` for evidence and `audit_coverage.csv` for every page. A visual review is accepted only when its source and output hashes match.",
        "",
    ]
    (out / "audit_report.md").write_text("\n".join(report), encoding="utf-8")
    return summary
