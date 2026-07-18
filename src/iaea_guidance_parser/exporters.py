from __future__ import annotations

import csv
import hashlib
import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .models import DocumentMetadata, StructuralElement
from .qa import run_records_qa, run_series_qa, write_qa_json, write_qa_markdown


CUSTOM_GPT_MARKDOWN_PART_MAX_BYTES = 4_500_000
CUSTOM_GPT_MARKDOWN_PART_HEADER_ALLOWANCE_BYTES = 100_000
STATUS_AND_REGION_LEGEND = """## Status and region legend

Interpret `status` with `region`:

- `Normative`: numbered primary content in Body sections 2+ and integral Appendix paragraphs, figures and tables.
- `Informative`: Annex paragraphs, figures and tables, plus footnotes.
- `Informational`: front matter, Section 1 context, headings, references, glossary, metadata and back matter.

Regions: `FrontMatter`, `Body`, `Appendix`, `Annex`, `References`, `Glossary`, `BackMatter`.
"""


def write_outputs(out_dir: Path, metadata: DocumentMetadata, records: list[StructuralElement]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "metadata.json", metadata.to_dict())
    write_jsonl(out_dir / "structural_index.jsonl", [r.to_dict() for r in records])
    write_jsonl(out_dir / "custom_gpt_knowledge.jsonl", [to_custom_gpt_record(r) for r in records])
    write_markdown_knowledge(out_dir / "custom_gpt_knowledge.md", metadata, records)
    write_csv(out_dir / "structural_index_preview.csv", records)
    write_qa_report(out_dir / "qa_report.md", metadata, records)
    findings = run_records_qa(records, manifest_doc_ids={metadata.document_id}, manifest_counts={metadata.document_id: len(records)})
    write_qa_json(
        out_dir / "qa_report.json",
        findings,
        metadata={
            "parser_version": __version__,
            "document_id": metadata.document_id,
            "source_file": metadata.source_file,
            "source_sha256": metadata.source_sha256,
        },
    )


def write_series_outputs(
    out_dir: Path,
    *,
    series_config: dict[str, Any] | None,
    results: list[Any],
    failures: list[Any],
) -> None:
    """Write combined outputs for a directory/series run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    all_records: list[StructuralElement] = []
    for result in results:
        all_records.extend(result.records)
    run_info = build_series_run_info(series_config or {}, results)

    write_json(out_dir / "series_config_effective.json", series_config or {})
    write_jsonl(out_dir / "series_structural_index.jsonl", [r.to_dict() for r in all_records])
    write_jsonl(out_dir / "series_custom_gpt_knowledge.jsonl", [to_custom_gpt_record(r) for r in all_records])
    write_series_markdown_knowledge(out_dir / "series_custom_gpt_knowledge.md", series_config or {}, results, run_info=run_info)
    part_files = write_series_markdown_knowledge_parts(
        out_dir / "series_custom_gpt_knowledge_parts",
        series_config or {},
        results,
        run_info=run_info,
    )
    run_info["output_parts"] = [
        {"path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size}
        for path in part_files
    ]
    findings = run_series_qa(results, failures)
    write_series_manifest(out_dir / "series_manifest.json", results, failures, run_info=run_info)
    write_series_manifest_csv(out_dir / "series_manifest.csv", results, failures, run_info=run_info)
    write_series_qa_report(out_dir / "series_qa_report.md", series_config or {}, results, failures, findings=findings, run_info=run_info)
    write_qa_json(out_dir / "qa_report.json", findings, metadata=run_info)
    write_qa_markdown(out_dir / "qa_report.md", findings, title="Series QA report", metadata=run_info)


def write_json(path: Path, obj) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_series_run_info(series_config: dict[str, Any], results: list[Any]) -> dict[str, Any]:
    series = series_config.get("series", {}) or {}
    documents = [
        {
            "document_id": result.metadata.document_id,
            "source_pdf": str(result.source_pdf),
            "source_sha256": result.metadata.source_sha256,
            "record_count": len(result.records),
        }
        for result in sorted(results, key=lambda r: r.metadata.document_id)
    ]
    manifest_blob = json.dumps(documents, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "run_id": str(uuid.uuid4()),
        "parser_version": __version__,
        "series_id": series.get("series_id", ""),
        "series_name": series.get("series_name", ""),
        "document_manifest_checksum": hashlib.sha256(manifest_blob).hexdigest(),
        "output_parts": [],
    }


def to_custom_gpt_record(r: StructuralElement) -> dict:
    header = (
        f"[doc: {_compact_document_id(r.document_id)} | record: {format_record_label(r)} | "
        f"status: {r.text_status} | region: {r.source_region} | "
        f"pdf: {format_pdf_pages(r.page_start_pdf, r.page_end_pdf)}]"
    )
    section = " > ".join(r.section_path) if r.section_path else ""
    return {
        "record_id": r.record_id,
        "document_id": r.document_id,
        "document_title": r.document_title,
        "document_family": r.document_family,
        "document_category": r.document_category,
        "document_type": r.document_type,
        "document_domain": r.document_domain,
        "series_name": r.series_name,
        "series_number": r.series_number,
        "element_type": r.element_type,
        "element_id": r.element_id,
        "source_region": r.source_region,
        "text_status": r.text_status,
        "section_path": r.section_path,
        "page_start_pdf": r.page_start_pdf,
        "page_end_pdf": r.page_end_pdf,
        "page_start_printed": r.page_start_printed,
        "page_end_printed": r.page_end_printed,
        "gpt_chunk_text": f"{header}\nSection: {section}\n\n{_record_content_text(r)}",
    }


def write_markdown_knowledge(path: Path, metadata: DocumentMetadata, records: list[StructuralElement]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {metadata.title}\n\n")
        f.write("## Document metadata\n\n")
        f.write(f"- Document ID: {metadata.document_id}\n")
        f.write(f"- Document family: {metadata.document_family}\n")
        f.write(f"- Series: {metadata.series_name} {metadata.series_number}\n")
        f.write(f"- Document category: {metadata.document_category}\n")
        f.write(f"- Document type: {metadata.document_type}\n")
        f.write(f"- Domain: {metadata.document_domain}\n")
        f.write(f"- Publication year: {metadata.publication_year}\n")
        f.write("\n")
        f.write(STATUS_AND_REGION_LEGEND)
        f.write("\n## Structural records\n")
        for r in records:
            f.write(_record_markdown(r))


def write_csv(path: Path, records: list[StructuralElement]) -> None:
    fields = [
        "record_id",
        "document_id",
        "document_type",
        "document_category",
        "element_type",
        "element_id",
        "source_region",
        "text_status",
        "page_start_pdf",
        "page_end_pdf",
        "page_start_printed",
        "page_end_printed",
        "section_path",
        "title",
        "caption",
        "confidence",
        "text_preview",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "record_id": r.record_id,
                "document_id": r.document_id,
                "document_type": r.document_type,
                "document_category": r.document_category,
                "element_type": r.element_type,
                "element_id": r.element_id or "",
                "source_region": r.source_region,
                "text_status": r.text_status,
                "page_start_pdf": r.page_start_pdf,
                "page_end_pdf": r.page_end_pdf,
                "page_start_printed": r.page_start_printed or "",
                "page_end_printed": r.page_end_printed or "",
                "section_path": " > ".join(r.section_path),
                "title": r.title or "",
                "caption": r.caption or "",
                "confidence": r.confidence,
                "text_preview": r.text[:400].replace("\n", " "),
            })


def write_qa_report(path: Path, metadata: DocumentMetadata, records: list[StructuralElement]) -> None:
    by_type = Counter(r.element_type for r in records)
    by_status = Counter(r.text_status for r in records)
    by_region = Counter(r.source_region for r in records)
    missing_doc_type = [r.record_id for r in records if not r.document_type or not r.document_category]
    low_conf = [r for r in records if r.confidence == "low"]
    findings = run_records_qa(records, manifest_doc_ids={metadata.document_id}, manifest_counts={metadata.document_id: len(records)})
    with path.open("w", encoding="utf-8") as f:
        f.write("# QA report\n\n")
        f.write("## Run metadata\n\n")
        f.write(f"- Parser version: `{__version__}`\n")
        f.write(f"- Source SHA-256: `{metadata.source_sha256}`\n\n")
        f.write(f"Document ID: `{metadata.document_id}`\n\n")
        f.write(f"Document type: `{metadata.document_type}` ({metadata.document_category})\n\n")
        f.write(f"Series: `{metadata.series_name} {metadata.series_number}`\n\n")
        f.write("## Counts by element type\n\n")
        for k, v in sorted(by_type.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Counts by text status\n\n")
        for k, v in sorted(by_status.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Counts by source region\n\n")
        for k, v in sorted(by_region.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Document-type QA\n\n")
        if missing_doc_type:
            f.write(f"Missing document type/category on {len(missing_doc_type)} records.\n")
        else:
            f.write("All structural index and Custom GPT records include document category and document type.\n")
        f.write("\n## Low-confidence records\n\n")
        f.write(f"Low-confidence record count: {len(low_conf)}\n\n")
        for r in low_conf[:50]:
            f.write(f"- {r.record_id}: {r.element_type} p. {r.page_start_pdf}; {r.text[:120]}\n")
        f.write("\n## Automated QA findings\n\n")
        f.write(f"Finding count: {len(findings)}\n\n")
        for severity, count in sorted(Counter(f.severity for f in findings).items()):
            f.write(f"- {severity}: {count}\n")
        if findings:
            f.write("\n### Highest-severity examples\n\n")
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for finding in sorted(findings, key=lambda f: (severity_order.get(f.severity, 9), f.doc_id, f.page))[:20]:
                f.write(
                    f"- `{finding.severity}` `{finding.check}` `{finding.record_id}` p. {finding.page or '?'}: "
                    f"{finding.reason} Suggested fix: {finding.suggested_fix}\n"
                )


def write_series_markdown_knowledge(
    path: Path,
    series_config: dict[str, Any],
    results: list[Any],
    *,
    run_info: dict[str, Any] | None = None,
) -> None:
    series = series_config.get("series", {}) or {}
    with path.open("w", encoding="utf-8") as f:
        f.write("# Combined Custom GPT Knowledge\n\n")
        if run_info:
            f.write("## Run metadata\n\n")
            for key in ["run_id", "parser_version", "series_id", "document_manifest_checksum"]:
                if run_info.get(key):
                    f.write(f"- {key}: {run_info[key]}\n")
            f.write("\n")
        if series:
            f.write("## Series metadata\n\n")
            for key in ["series_id", "series_name", "document_family", "document_domain", "document_subdomain"]:
                if series.get(key):
                    f.write(f"- {key}: {series[key]}\n")
            f.write("\n")
        f.write(STATUS_AND_REGION_LEGEND)
        f.write("\n")
        f.write("## Documents included\n\n")
        for result in results:
            meta = result.metadata
            f.write(
                f"- {meta.document_id}: {meta.title} | {meta.document_category} | "
                f"{meta.document_type} | {meta.series_name} {meta.series_number} | "
                f"records: {len(result.records)}\n"
            )
        f.write("\n## Structural records\n")
        for result in results:
            meta = result.metadata
            f.write(_series_part_document_header(meta))
            for r in result.records:
                f.write(_record_markdown(r))


def write_series_markdown_knowledge_parts(
    out_dir: Path,
    series_config: dict[str, Any],
    results: list[Any],
    *,
    max_bytes: int = CUSTOM_GPT_MARKDOWN_PART_MAX_BYTES,
    run_info: dict[str, Any] | None = None,
) -> list[Path]:
    """Write upload-sized Markdown knowledge files for Custom GPT Knowledge.

    The Custom GPT builder can reject a file whose extracted text is too large,
    even when the file is under the general file-size cap. Splitting at record
    boundaries keeps each upload file readable and avoids breaking citations.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old_path in out_dir.glob("*.md"):
        old_path.unlink()

    if not results:
        (out_dir / "README.md").write_text(
            "# Custom GPT Knowledge Parts\n\nNo parsed documents were available for this run.\n",
            encoding="utf-8",
        )
        return []

    body_limit = max(1_000, max_bytes - CUSTOM_GPT_MARKDOWN_PART_HEADER_ALLOWANCE_BYTES)
    parts: list[dict[str, Any]] = []
    sections: list[str] = []
    documents: dict[str, str] = {}
    section_bytes = 0

    def flush_part() -> None:
        nonlocal sections, documents, section_bytes
        if not sections:
            return
        parts.append({"sections": sections, "documents": list(documents.values())})
        sections = []
        documents = {}
        section_bytes = 0

    for result in results:
        meta = result.metadata
        document_summary = _series_document_summary(result)
        document_header = _series_part_document_header(meta)
        document_header_bytes = _encoded_len(document_header)
        if sections and section_bytes + document_header_bytes > body_limit:
            flush_part()

        sections.append(document_header)
        documents[meta.document_id] = document_summary
        section_bytes += document_header_bytes

        for record in result.records:
            record_markdown = _record_markdown(record)
            record_bytes = _encoded_len(record_markdown)
            if sections and section_bytes + record_bytes > body_limit:
                flush_part()
                continued_header = _series_part_document_header(meta, continued=True)
                sections.append(continued_header)
                documents[meta.document_id] = document_summary
                section_bytes += _encoded_len(continued_header)

            sections.append(record_markdown)
            section_bytes += record_bytes

    flush_part()

    total_parts = len(parts)
    part_files: list[Path] = []
    for index, part in enumerate(parts, start=1):
        part_path = out_dir / f"part_{index:03d}_of_{total_parts:03d}.md"
        with part_path.open("w", encoding="utf-8") as f:
            f.write(_series_part_preamble(series_config, index, total_parts, part["documents"], run_info=run_info))
            for section in part["sections"]:
                f.write(section)
        part_files.append(part_path)

    _write_series_parts_readme(out_dir / "README.md", part_files, max_bytes)
    return part_files


def _encoded_len(value: str) -> int:
    return len(value.encode("utf-8"))


def format_pdf_pages(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def format_record_label(record: StructuralElement) -> str:
    return f"{record.element_type} {record.element_id}" if record.element_id else record.element_type


def _compact_document_id(document_id: str) -> str:
    if len(document_id) <= 48:
        return document_id
    patterns = [
        r"^(NSS-\d+(?:-+[A-Z])?(?:-REV\d+)?)",
        r"^(GSR-PART-\d+(?:-REV\d+)?)",
        r"^(SSR-\d+(?:-\d+)?(?:-REV\d+)?)",
        r"^(SSG-\d+(?:-REV\d+)?)",
        r"^(GSG-\d+)",
        r"^(GS-G-\d+)",
        r"^(RS-G-\d+)",
        r"^(WS-G-\d+)",
        r"^(TS-G-\d+)",
        r"^(SF-\d+)",
    ]
    for pattern in patterns:
        match = re.match(pattern, document_id)
        if match:
            return re.sub(r"-+", "-", match.group(1)).strip("-")
    return document_id[:48].rstrip("-")


def _record_content_text(record: StructuralElement) -> str:
    extras: list[str] = []
    if record.caption and record.caption not in record.text:
        extras.append(f"Caption: {record.caption}")
    if record.title and record.element_type == "table" and record.title not in record.text:
        extras.append(f"Table title: {record.title}")
    if extras:
        return "\n".join([*extras, record.text])
    return record.text


def _series_document_summary(result: Any) -> str:
    meta = result.metadata
    return (
        f"- {_compact_document_id(meta.document_id)} | {meta.series_number} | {meta.document_type} | "
        f"{meta.title} | records: {len(result.records)}"
    )


def _series_part_preamble(
    series_config: dict[str, Any],
    part_number: int,
    total_parts: int,
    documents: list[str],
    *,
    run_info: dict[str, Any] | None = None,
) -> str:
    series = series_config.get("series", {}) or {}
    lines = [
        f"# Combined Custom GPT Knowledge - Part {part_number:03d} of {total_parts:03d}",
        "",
        "This is one upload-sized part of the combined series knowledge file.",
        "Upload all numbered parts for this series to the same Custom GPT.",
        "",
    ]
    if series:
        lines.append("## Series metadata")
        lines.append("")
        for key in ["series_id", "series_name", "document_family", "document_domain", "document_subdomain"]:
            if series.get(key):
                lines.append(f"- {key}: {series[key]}")
        lines.append("")
    if run_info:
        lines.append("## Run metadata")
        lines.append("")
        for key in ["run_id", "parser_version", "series_id", "document_manifest_checksum"]:
            if run_info.get(key):
                lines.append(f"- {key}: {run_info[key]}")
        lines.append("")

    lines.extend(STATUS_AND_REGION_LEGEND.rstrip().splitlines())
    lines.append("")
    lines.append("## Documents in this part")
    lines.append("")
    lines.extend(documents)
    lines.append("")
    lines.append("## Structural records")
    lines.append("")
    return "\n".join(lines)


def _series_part_document_header(metadata: DocumentMetadata, *, continued: bool = False) -> str:
    continued_label = " (continued)" if continued else ""
    doc_ref = _compact_document_id(metadata.document_id)
    return (
        f"\n\n# Document: {doc_ref}{continued_label} - {metadata.title}\n\n"
        f"- Full ID: {metadata.document_id}\n"
        f"- Category: {metadata.document_category}\n"
        f"- Type: {metadata.document_type}\n"
        f"- Series: {metadata.series_name} {metadata.series_number}\n"
    )


def _record_markdown(record: StructuralElement) -> str:
    section = " > ".join(record.section_path)
    return (
        "\n---\n"
        f"doc: {_compact_document_id(record.document_id)}\n"
        f"record: {format_record_label(record)}\n"
        f"status: {record.text_status}\n"
        f"region: {record.source_region}\n"
        f"pdf: {format_pdf_pages(record.page_start_pdf, record.page_end_pdf)}\n"
        f"section: {section}\n\n"
        f"{_record_content_text(record)}\n"
    )


def _write_series_parts_readme(path: Path, part_files: list[Path], max_bytes: int) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("# Custom GPT Knowledge Parts\n\n")
        f.write(
            "Upload the numbered `part_*.md` files to the same Custom GPT. "
            "The unsplit `series_custom_gpt_knowledge.md` is retained for local use, "
            "but may be too large for the Custom GPT builder.\n\n"
        )
        f.write(f"Target maximum part size: {max_bytes:,} bytes.\n\n")
        for part_file in part_files:
            f.write(f"- `{part_file.name}` ({part_file.stat().st_size:,} bytes)\n")


def write_series_manifest(
    path: Path,
    results: list[Any],
    failures: list[Any],
    *,
    run_info: dict[str, Any] | None = None,
) -> None:
    docs = []
    for result in results:
        meta = result.metadata
        counts = Counter(r.element_type for r in result.records)
        docs.append(
            {
                "status": "ok",
                "source_pdf": str(result.source_pdf),
                "output_dir": str(result.output_dir),
                "document_id": meta.document_id,
                "document_title": meta.title,
                "document_family": meta.document_family,
                "document_category": meta.document_category,
                "document_type": meta.document_type,
                "document_domain": meta.document_domain,
                "series_name": meta.series_name,
                "series_number": meta.series_number,
                "publication_year": meta.publication_year,
                "sti_pub_number": meta.sti_pub_number,
                "isbn_pdf": meta.isbn_pdf,
                "record_count": len(result.records),
                "counts_by_element_type": dict(sorted(counts.items())),
                "source_sha256": meta.source_sha256,
            }
        )
    for failure in failures:
        docs.append({"status": "failed", "source_pdf": str(failure.source_pdf), "error": failure.error})

    write_json(
        path,
        {
            "run_id": (run_info or {}).get("run_id", ""),
            "parser_version": (run_info or {}).get("parser_version", __version__),
            "series_id": (run_info or {}).get("series_id", ""),
            "document_manifest_checksum": (run_info or {}).get("document_manifest_checksum", ""),
            "output_parts": (run_info or {}).get("output_parts", []),
            "document_count_ok": len(results),
            "document_count_failed": len(failures),
            "total_record_count": sum(len(r.records) for r in results),
            "documents": docs,
        },
    )


def write_series_manifest_csv(
    path: Path,
    results: list[Any],
    failures: list[Any],
    *,
    run_info: dict[str, Any] | None = None,
) -> None:
    fields = [
        "run_id",
        "parser_version",
        "series_id",
        "document_manifest_checksum",
        "status",
        "source_pdf",
        "output_dir",
        "document_id",
        "document_title",
        "document_family",
        "document_category",
        "document_type",
        "document_domain",
        "series_name",
        "series_number",
        "publication_year",
        "record_count",
        "paragraphs",
        "figures",
        "tables",
        "footnotes",
        "references",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        run_info = run_info or {}
        for result in results:
            meta = result.metadata
            counts = Counter(r.element_type for r in result.records)
            writer.writerow(
                {
                    "run_id": run_info.get("run_id", ""),
                    "parser_version": run_info.get("parser_version", __version__),
                    "series_id": run_info.get("series_id", ""),
                    "document_manifest_checksum": run_info.get("document_manifest_checksum", ""),
                    "status": "ok",
                    "source_pdf": str(result.source_pdf),
                    "output_dir": str(result.output_dir),
                    "document_id": meta.document_id,
                    "document_title": meta.title,
                    "document_family": meta.document_family,
                    "document_category": meta.document_category,
                    "document_type": meta.document_type,
                    "document_domain": meta.document_domain,
                    "series_name": meta.series_name,
                    "series_number": meta.series_number,
                    "publication_year": meta.publication_year or "",
                    "record_count": len(result.records),
                    "paragraphs": counts.get("paragraph", 0),
                    "figures": counts.get("figure", 0),
                    "tables": counts.get("table", 0),
                    "footnotes": counts.get("footnote", 0),
                    "references": counts.get("reference", 0),
                    "error": "",
                }
            )
        for failure in failures:
            writer.writerow(
                {
                    "run_id": run_info.get("run_id", ""),
                    "parser_version": run_info.get("parser_version", __version__),
                    "series_id": run_info.get("series_id", ""),
                    "document_manifest_checksum": run_info.get("document_manifest_checksum", ""),
                    "status": "failed",
                    "source_pdf": str(failure.source_pdf),
                    "error": failure.error,
                }
            )


def write_series_qa_report(
    path: Path,
    series_config: dict[str, Any],
    results: list[Any],
    failures: list[Any],
    *,
    findings: list[Any] | None = None,
    run_info: dict[str, Any] | None = None,
) -> None:
    all_records = [r for result in results for r in result.records]
    by_type = Counter(r.element_type for r in all_records)
    by_status = Counter(r.text_status for r in all_records)
    by_region = Counter(r.source_region for r in all_records)
    by_doc_type = Counter(r.document_type or "<missing>" for r in all_records)
    missing_type_records = [r.record_id for r in all_records if not r.document_type or not r.document_category]
    low_conf = [r for r in all_records if r.confidence == "low"]

    with path.open("w", encoding="utf-8") as f:
        f.write("# Series QA report\n\n")
        if run_info:
            f.write("## Run metadata\n\n")
            for key in ["run_id", "parser_version", "series_id", "document_manifest_checksum"]:
                if run_info.get(key):
                    f.write(f"- {key}: `{run_info[key]}`\n")
            f.write("\n")
        series = series_config.get("series", {}) or {}
        if series:
            f.write("## Series metadata\n\n")
            for key in ["series_id", "series_name", "document_family", "document_domain", "document_subdomain"]:
                if series.get(key):
                    f.write(f"- {key}: {series[key]}\n")
            f.write("\n")
        f.write("## Run summary\n\n")
        f.write(f"- Documents parsed: {len(results)}\n")
        f.write(f"- Documents failed: {len(failures)}\n")
        f.write(f"- Total structural records: {len(all_records)}\n\n")

        f.write("## Documents\n\n")
        for result in results:
            meta = result.metadata
            counts = Counter(r.element_type for r in result.records)
            f.write(
                f"- `{meta.document_id}` — {meta.title}; {meta.document_category}; "
                f"`{meta.document_type}`; records: {len(result.records)}; "
                f"paragraphs: {counts.get('paragraph', 0)}, figures: {counts.get('figure', 0)}, "
                f"tables: {counts.get('table', 0)}, footnotes: {counts.get('footnote', 0)}\n"
            )
        if failures:
            f.write("\n## Failures\n\n")
            for failure in failures:
                f.write(f"- `{failure.source_pdf}`: {failure.error}\n")

        f.write("\n## Counts by element type\n\n")
        for k, v in sorted(by_type.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Counts by text status\n\n")
        for k, v in sorted(by_status.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Counts by source region\n\n")
        for k, v in sorted(by_region.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Counts by document type\n\n")
        for k, v in sorted(by_doc_type.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n## Document-type QA\n\n")
        if missing_type_records:
            f.write(f"Missing document category/type on {len(missing_type_records)} records.\n")
            for rid in missing_type_records[:50]:
                f.write(f"- {rid}\n")
        else:
            f.write("All combined structural index and Custom GPT records include document category and document type.\n")
        f.write("\n## Low-confidence records\n\n")
        f.write(f"Low-confidence record count: {len(low_conf)}\n\n")
        for r in low_conf[:100]:
            f.write(f"- {r.record_id}: {r.element_type} p. {r.page_start_pdf}; {r.text[:120]}\n")
        findings = findings or []
        f.write("\n## Automated QA findings\n\n")
        f.write(f"Finding count: {len(findings)}\n\n")
        for severity, count in sorted(Counter(f.severity for f in findings).items()):
            f.write(f"- {severity}: {count}\n")
        if findings:
            f.write("\n### Highest-severity examples\n\n")
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            for finding in sorted(findings, key=lambda f: (severity_order.get(f.severity, 9), f.doc_id, f.page))[:20]:
                f.write(
                    f"- `{finding.severity}` `{finding.check}` `{finding.doc_id}` "
                    f"`{finding.record_id}` p. {finding.page or '?'}: {finding.reason} "
                    f"Suggested fix: {finding.suggested_fix}\n"
                )
        f.write("\n## Table Layout Warning\n\n")
        f.write(
            "Do not rely on exact table layout without PDF verification. The parser preserves raw table text, "
            "but complex row and column boundaries can require manual review.\n"
        )
