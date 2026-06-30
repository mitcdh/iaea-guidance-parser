from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .models import DocumentMetadata, StructuralElement


def write_outputs(out_dir: Path, metadata: DocumentMetadata, records: list[StructuralElement]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "metadata.json", metadata.to_dict())
    write_jsonl(out_dir / "structural_index.jsonl", [r.to_dict() for r in records])
    write_jsonl(out_dir / "custom_gpt_knowledge.jsonl", [to_custom_gpt_record(r) for r in records])
    write_markdown_knowledge(out_dir / "custom_gpt_knowledge.md", metadata, records)
    write_csv(out_dir / "structural_index_preview.csv", records)
    write_qa_report(out_dir / "qa_report.md", metadata, records)


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

    write_json(out_dir / "series_config_effective.json", series_config or {})
    write_jsonl(out_dir / "series_structural_index.jsonl", [r.to_dict() for r in all_records])
    write_jsonl(out_dir / "series_custom_gpt_knowledge.jsonl", [to_custom_gpt_record(r) for r in all_records])
    write_series_markdown_knowledge(out_dir / "series_custom_gpt_knowledge.md", series_config or {}, results)
    write_series_manifest(out_dir / "series_manifest.json", results, failures)
    write_series_manifest_csv(out_dir / "series_manifest.csv", results, failures)
    write_series_qa_report(out_dir / "series_qa_report.md", series_config or {}, results, failures)


def write_json(path: Path, obj) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def to_custom_gpt_record(r: StructuralElement) -> dict:
    header = (
        f"[{r.document_id} | {r.document_category} | {r.document_type} | "
        f"{r.series_name} {r.series_number} | {r.element_type} {r.element_id or ''} | "
        f"{r.text_status} | {r.source_region} | PDF p. {r.page_start_pdf}"
        + (f"–{r.page_end_pdf}" if r.page_end_pdf != r.page_start_pdf else "")
        + "]"
    )
    section = " > ".join(r.section_path) if r.section_path else ""
    body = r.text
    if r.caption:
        body = f"Caption: {r.caption}\n{body}"
    if r.title and r.element_type == "table":
        body = f"Table title: {r.title}\n{body}"
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
        "gpt_chunk_text": f"{header}\nSection path: {section}\nStatus basis: {r.status_reason}\n\n{body}",
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
        f.write("\n## Structural records\n")
        for r in records:
            f.write("\n---\n")
            f.write(f"record_id: {r.record_id}\n")
            f.write(f"document_id: {r.document_id}\n")
            f.write(f"document_category: {r.document_category}\n")
            f.write(f"document_type: {r.document_type}\n")
            f.write(f"element_type: {r.element_type}\n")
            f.write(f"element_id: {r.element_id or ''}\n")
            f.write(f"source_region: {r.source_region}\n")
            f.write(f"text_status: {r.text_status}\n")
            f.write(f"pdf_pages: {r.page_start_pdf}-{r.page_end_pdf}\n")
            f.write(f"section_path: {' > '.join(r.section_path)}\n")
            f.write("---\n\n")
            f.write(to_custom_gpt_record(r)["gpt_chunk_text"])
            f.write("\n")


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
    with path.open("w", encoding="utf-8") as f:
        f.write("# QA report\n\n")
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


def write_series_markdown_knowledge(path: Path, series_config: dict[str, Any], results: list[Any]) -> None:
    series = series_config.get("series", {}) or {}
    with path.open("w", encoding="utf-8") as f:
        f.write("# Combined Custom GPT Knowledge\n\n")
        if series:
            f.write("## Series metadata\n\n")
            for key in ["series_id", "series_name", "document_family", "document_domain", "document_subdomain"]:
                if series.get(key):
                    f.write(f"- {key}: {series[key]}\n")
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
            f.write(f"\n\n# Document: {meta.document_id} — {meta.title}\n\n")
            f.write(f"- Document family: {meta.document_family}\n")
            f.write(f"- Document category: {meta.document_category}\n")
            f.write(f"- Document type: {meta.document_type}\n")
            f.write(f"- Domain: {meta.document_domain}\n")
            f.write(f"- Series: {meta.series_name} {meta.series_number}\n")
            for r in result.records:
                f.write("\n---\n")
                f.write(f"record_id: {r.record_id}\n")
                f.write(f"document_id: {r.document_id}\n")
                f.write(f"document_category: {r.document_category}\n")
                f.write(f"document_type: {r.document_type}\n")
                f.write(f"element_type: {r.element_type}\n")
                f.write(f"element_id: {r.element_id or ''}\n")
                f.write(f"source_region: {r.source_region}\n")
                f.write(f"text_status: {r.text_status}\n")
                f.write(f"pdf_pages: {r.page_start_pdf}-{r.page_end_pdf}\n")
                f.write(f"section_path: {' > '.join(r.section_path)}\n")
                f.write("---\n\n")
                f.write(to_custom_gpt_record(r)["gpt_chunk_text"])
                f.write("\n")


def write_series_manifest(path: Path, results: list[Any], failures: list[Any]) -> None:
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
            "document_count_ok": len(results),
            "document_count_failed": len(failures),
            "total_record_count": sum(len(r.records) for r in results),
            "documents": docs,
        },
    )


def write_series_manifest_csv(path: Path, results: list[Any], failures: list[Any]) -> None:
    fields = [
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
        for result in results:
            meta = result.metadata
            counts = Counter(r.element_type for r in result.records)
            writer.writerow(
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
            writer.writerow({"status": "failed", "source_pdf": str(failure.source_pdf), "error": failure.error})


def write_series_qa_report(path: Path, series_config: dict[str, Any], results: list[Any], failures: list[Any]) -> None:
    all_records = [r for result in results for r in result.records]
    by_type = Counter(r.element_type for r in all_records)
    by_status = Counter(r.text_status for r in all_records)
    by_region = Counter(r.source_region for r in all_records)
    by_doc_type = Counter(r.document_type or "<missing>" for r in all_records)
    missing_type_records = [r.record_id for r in all_records if not r.document_type or not r.document_category]
    low_conf = [r for r in all_records if r.confidence == "low"]

    with path.open("w", encoding="utf-8") as f:
        f.write("# Series QA report\n\n")
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
