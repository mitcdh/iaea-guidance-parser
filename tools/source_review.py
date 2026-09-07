#!/usr/bin/env python3
"""Prepare frozen, bounded source-review packets and merge checked worker results.

Run with the project environment: .venv/bin/python tools/source_review.py --help
Workers read packet.json and its images, and write result.json only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from hashlib import sha256
from pathlib import Path

import fitz

from iaea_guidance_parser.audit import DISPLAY_POLICY, displayed_page_text
from iaea_guidance_parser.provenance import fingerprint, sha256_file
from iaea_guidance_parser.reviews import (
    content_fingerprint,
    finding_id,
    metadata_fingerprint,
    validate_result,
)

CHECKLIST = [
    "Display each source image as an image using image((await tools.view_image({path: imagePath})).image_url). A path or data URL shown as text is not inspection.",
    "Preserve word boundaries and meaningful hyphens: preand does not match pre- and. Do not silently repair or excuse a changed word.",
    "A claimed omission or duplicate needs the exact substring and record. Multi-page overlap alone is not duplication.",
    "Compare all visible assigned-page text with parsed text: omissions, duplicates, order, symbols and units.",
    "Check heading roles, hierarchy and section/appendix/annex transitions; emphasis or form labels are not automatically headings.",
    "Check tables: every row/column association, blanks, merged cells, captions, notes, anchors and continuation. Raw text passes only if relationships remain explicit and unambiguous.",
    "Check figure text, captions, source locations and continuation associations. Figure-image export is outside scope.",
    "Check physical/printed page references and footnote anchors. For metadata pages compare supplied publication fields with visible evidence.",
    "Account for every assigned discrepancy. Hidden objects, exclusions and ambiguous glyphs require coordinator review; never guess or silently normalize them.",
    "A pass covers every applicable check on the owned page. Missing context or any unresolved issue is an exception, not a pass.",
]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publication(parsed, doc_id):
    manifest = read_json(parsed / "series_manifest.json")
    entry = next(e for e in manifest["documents"] if e["document_id"] == doc_id)
    from iaea_guidance_parser.series import safe_path_component

    folder = parsed / "documents" / safe_path_component(doc_id)
    records = [json.loads(s) for s in (folder / "structural_index.jsonl").read_text().splitlines()]
    metadata = read_json(folder / "metadata.json")
    pdf = Path(entry["source_pdf"]).resolve()
    if sha256_file(pdf) != entry["source_sha256"]:
        raise ValueError("Source differs from parsed manifest")
    return pdf, records, metadata


def page_records(records, page):
    return [r for r in records if r["page_start_pdf"] <= page <= r["page_end_pdf"]]


def cache_page(pdf, cache, page, *, rotation=0, dpi=120, render=True):
    """Cache independent Poppler evidence; verify cached artifacts before reuse."""
    if rotation not in (0, 90, 180, 270) or dpi <= 0:
        raise ValueError("Evidence requires a quarter-turn rotation and positive resolution")
    source_hash = sha256_file(pdf)
    folder = cache / source_hash / DISPLAY_POLICY / f"p{page:04d}-r{rotation}-dpi{dpi}"
    folder.mkdir(parents=True, exist_ok=True)
    text = folder / "source.txt"
    image = folder / "source.png"
    stamp = folder / "evidence.json"
    previous = read_json(stamp) if stamp.exists() else {}
    render_pdf, render_page = pdf, page
    if rotation:
        render_pdf, render_page = folder / "rotated.pdf", 1
        if not render_pdf.exists() or previous.get("rotated_sha256") != sha256_file(render_pdf):
            with fitz.open(pdf) as original, fitz.open() as rotated:
                rotated.insert_pdf(original, from_page=page - 1, to_page=page - 1)
                rotated[0].set_rotation((rotated[0].rotation + rotation) % 360)
                rotated.save(render_pdf)
    if not text.exists() or previous.get("text_sha256") != sha256_file(text):
        text.write_text(displayed_page_text(render_pdf, render_page), encoding="utf-8")
    if render and (not image.exists() or previous.get("image_sha256") != sha256_file(image)):
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                str(render_page),
                "-l",
                str(render_page),
                "-r",
                str(dpi),
                "-singlefile",
                "-cropbox",
                "-png",
                str(render_pdf),
                str(folder / "source"),
            ],
            check=True,
            capture_output=True,
        )
    result = {
        "source_sha256": source_hash,
        "pdf_page": page,
        "rotation": rotation,
        "dpi": dpi,
        "extraction_policy": DISPLAY_POLICY,
        "text_path": str(text.resolve()),
        "text_sha256": sha256_file(text),
    }
    if rotation:
        result["rotated_sha256"] = sha256_file(render_pdf)
    if render:
        result.update(image_path=str(image.resolve()), image_sha256=sha256_file(image))
    write_json(stamp, result)
    return result


def compact_record(record, *, geometry_pages=()):
    fields = (
        "record_id",
        "element_type",
        "element_id",
        "source_region",
        "text_status",
        "section_path",
        "page_start_pdf",
        "page_end_pdf",
        "page_start_printed",
        "page_end_printed",
        "text",
        "title",
        "caption",
        "parent_element_id",
        "linked_from_element_id",
        "extra",
    )
    result = {k: record[k] for k in fields if record.get(k) not in (None, [], {})}
    result["extra"] = {k: v for k, v in record.get("extra", {}).items() if k != "source_spans"}
    if geometry_pages:
        result["extra"]["source_spans"] = [
            span
            for span in record.get("extra", {}).get("source_spans", [])
            if span["pdf_page"] in geometry_pages
        ]
    return result


def prepare(
    *,
    parsed,
    audit,
    work,
    doc_id,
    pages,
    category,
    context=(),
    rotation=0,
    mode="visual",
    document_check=False,
    cache=None,
    geometry=False,
):
    if len(set(pages + list(context))) > (4 if mode == "visual" else 12):
        raise ValueError("Assignment exceeds page/item limit including context")
    if set(pages) & set(context) or len(pages) != len(set(pages)):
        raise ValueError("Page ownership must be unique")
    pdf, records, metadata = publication(parsed, doc_id)
    with fitz.open(pdf) as document:
        if any(n < 1 or n > len(document) for n in pages + list(context)):
            raise ValueError("Assigned page is outside the source PDF")
    findings = [json.loads(s) for s in (audit / "audit_findings.jsonl").read_text().splitlines()]
    coverage = {
        int(r["pdf_page"]): r
        for r in csv.DictReader((audit / "audit_coverage.csv").open())
        if r["document_id"] == doc_id
    }
    owned_pages = [0] if document_check else pages
    assignment_id = f"{doc_id}-{category}-" + "-".join(map(str, pages))
    assignments = work / "assignments"
    assignments.mkdir(parents=True, exist_ok=True)
    for path in assignments.glob("*/binding.json"):
        old = read_json(path)
        if (
            old["document_id"] == doc_id
            and path.parent.name != assignment_id
            and set(old["owned_pages"]) & set(owned_pages)
        ):
            raise ValueError(f"Page already owned by {path.parent.name}")
    folder = assignments / assignment_id
    folder.mkdir(exist_ok=True)
    if (folder / "packet.json").exists():
        raise ValueError(
            "Assignment is frozen; use existing packet or archive it before replacement"
        )
    items, bindings = [], []
    for number in sorted(set(pages + list(context))):
        evidence = cache_page(
            pdf, cache or work / "cache", number, rotation=rotation, render=mode == "visual"
        )
        selected = page_records(records, number)
        relevant = [
            dict(f, finding_id=finding_id(f))
            for f in findings
            if f["document_id"] == doc_id and f["pdf_page"] == number
        ]
        items.append(
            {
                "id": f"p{number}",
                "owned": number in pages and not document_check,
                "pdf_page": number,
                "image": evidence.get("image_path"),
                "source_text": Path(evidence["text_path"]).read_text(),
                "records": [
                    compact_record(r, geometry_pages=pages + list(context) if geometry else ())
                    for r in selected
                ],
                "risks": coverage[number]["visual_reasons"],
                "findings": relevant,
            }
        )
        bindings.append(
            dict(
                evidence,
                output_sha256=content_fingerprint(selected),
                legacy_output_sha256=fingerprint(selected),
            )
        )
    checklist = CHECKLIST
    if document_check:
        from collections import Counter

        from iaea_guidance_parser.audit import _record_source_text, _source_pages, tokens

        relevant = [
            dict(f, finding_id=finding_id(f))
            for f in findings
            if f["document_id"] == doc_id and f["pdf_page"] == 0
        ]
        terms = {term for f in relevant for term in f["evidence"].get("extra_tokens", {})}
        for record in records:
            if terms.intersection(tokens(_record_source_text(record))) and not any(
                record["page_start_pdf"] <= n <= record["page_end_pdf"] for n in pages
            ):
                raise ValueError(
                    "Document finding has output occurrences outside supplied context; split the investigation"
                )
        source_count = Counter(tokens("\n".join(_source_pages(pdf))))
        output_count = Counter(tokens("\n".join(_record_source_text(r) for r in records)))
        items.append(
            {
                "id": "document",
                "owned": True,
                "pdf_page": 0,
                "findings": relevant,
                "counts": {
                    t: {"source": source_count[t], "output": output_count[t]} for t in sorted(terms)
                },
            }
        )
        bindings.append(
            {
                "pdf_page": 0,
                "output_sha256": content_fingerprint(records),
                "context_pages": list(bindings),
            }
        )
        checklist = [
            *CHECKLIST[:3],
            "Verify only the owned document-wide findings using the complete multiplicities and supplied context. Context pages are not owned or certified.",
            "Pass only when every excess term is faithfully represented in the source. Missing context, changes in wording, or unsupported normalization are exceptions.",
            "Metadata and document boundaries are separate checks and are not certified by this assignment.",
        ]
    # Share each overlapping record once, even when it spans all context pages.
    # Keep context records: a continuation may have been incorrectly detached
    # into a new record, so matching identifiers cannot establish relevance.
    unique_records = {}
    for item in items:
        selected = item.pop("records", [])
        for record in selected:
            unique_records[record["record_id"]] = record
        item["record_ids"] = [r["record_id"] for r in selected]
    packet = {
        "assignment_id": assignment_id,
        "document_id": doc_id,
        "category": category,
        "mode": mode,
        "checklist": checklist,
        "result_schema": {
            "assignment_id": assignment_id,
            "checklist_completed": True,
            "passes": ["pN"],
            "exceptions": [
                {
                    "id": "pN",
                    "status": "uncertain",
                    "references": ["pN / record_id"],
                    "evidence": "<=120 words",
                }
            ],
            "summary": "<=150 words",
        },
        "metadata": metadata,
        "items": items,
        "records": unique_records,
    }
    words = len(json.dumps(packet, ensure_ascii=False).split())
    if (mode == "text" or document_check) and words > 2500:
        raise ValueError(f"Text assignment has {words} supplied words; split it")
    write_json(folder / "packet.json", packet)
    write_json(
        folder / "binding.json",
        {
            "assignment_id": assignment_id,
            "document_id": doc_id,
            "parsed": str(parsed.resolve()),
            "source_pdf": str(pdf),
            "source_sha256": sha256_file(pdf),
            "metadata_sha256": metadata_fingerprint(metadata),
            "owned_pages": owned_pages,
            "evidence": bindings,
            "packet_sha256": sha256_file(folder / "packet.json"),
            "supplied_words": words,
            "image_count": sum(bool(i.get("image")) for i in items),
            "checklist_sha256": fingerprint(checklist),
        },
    )
    return folder.resolve()


def merge(folder, ledger, *, parsed=None, reviewer="Comparison reviewer"):
    """Merge only current, complete results. Exceptions remain explicit blockers."""
    packet, binding = read_json(folder / "packet.json"), read_json(folder / "binding.json")
    if sha256_file(folder / "packet.json") != binding["packet_sha256"]:
        raise ValueError("Frozen packet changed")
    result = validate_result(packet, read_json(folder / "result.json"))
    pdf, records, metadata = publication(parsed or Path(binding["parsed"]), binding["document_id"])
    if (
        sha256_file(pdf) != binding["source_sha256"]
        or metadata_fingerprint(metadata) != binding["metadata_sha256"]
    ):
        raise ValueError("Stale source or metadata")
    # Context images are part of the decision even when their pages are not owned.
    for evidence in binding["evidence"]:
        for context in evidence.get("context_pages", [evidence]):
            number = context["pdf_page"]
            if number and content_fingerprint(page_records(records, number)) != context.get(
                "output_sha256"
            ):
                raise ValueError(f"Stale parsed page {number} in supplied evidence")
            for kind in ("text", "image"):
                if kind + "_path" in context:
                    path = Path(context[kind + "_path"])
                    if not path.is_file() or sha256_file(path) != context[kind + "_sha256"]:
                        raise ValueError("Cached evidence changed")
    rows = [json.loads(s) for s in ledger.read_text().splitlines()] if ledger.exists() else []
    decisions = {i["id"]: i for i in result["exceptions"]}
    sampling_path = folder / "sample.json"
    sampling = read_json(sampling_path) if sampling_path.exists() else None
    if sampling:
        if sampling.get("result_sha256") != sha256_file(folder / "result.json"):
            raise ValueError("Sample refers to a different worker result")
        if sampling.get("status") == "failed":
            if not sampling.get("reason"):
                raise ValueError("Failed sample needs an evidence-bearing reason")
            for item_id in result["passes"]:
                decisions[item_id] = {
                    "id": item_id,
                    "status": "uncertain",
                    "references": sampling.get("references", []),
                    "evidence": "Reopened after failed assignment sample: " + sampling["reason"],
                }

    adjudication_path = folder / "adjudication.json"
    adjudication = read_json(adjudication_path) if adjudication_path.exists() else {}
    if adjudication and adjudication.get("result_sha256") != sha256_file(folder / "result.json"):
        raise ValueError("Adjudication refers to a different worker result")
    for item in packet["items"]:
        if not item["owned"]:
            continue
        number = item["pdf_page"]
        evidence = next((e for e in binding["evidence"] if e["pdf_page"] == number), None)
        if evidence is None:
            raise ValueError(f"Missing evidence binding for owned page {number}")
        if (
            content_fingerprint(records if number == 0 else page_records(records, number))
            != evidence["output_sha256"]
        ):
            raise ValueError(f"Stale parsed page {number}")
        contexts = evidence.get("context_pages", [evidence])
        if number:
            contexts = [e for e in binding["evidence"] if e["pdf_page"] > 0]
        exception = decisions.get(item["id"])
        row = {
            "schema_version": 2,
            "document_id": binding["document_id"],
            "pdf_page": number,
            "source_sha256": binding["source_sha256"],
            "output_sha256": evidence["output_sha256"],
            "metadata_sha256": binding["metadata_sha256"],
            "method": "visual" if packet["mode"] == "visual" else "automated_structural",
            "verification_status": "blocked" if exception or item.get("findings") else "verified",
            "metadata_verified": False,
            "reviewer": reviewer,
            "assignment_id": binding["assignment_id"],
            "notes": exception["evidence"]
            if exception
            else "All applicable fixed-checklist comparisons passed.",
            "evidence": contexts,
            "dispositions": {},
            "exception": exception,
            "sampling": sampling,
            "checklist_sha256": binding["checklist_sha256"],
        }
        approval = adjudication.get("decisions", {}).get(item["id"])
        if approval:
            if approval.get("status") not in {
                "verified",
                "blocked",
                "inspected",
            } or not approval.get("notes"):
                raise ValueError("Adjudication needs status and source-backed notes")
            row.update(
                verification_status=approval["status"],
                notes=approval["notes"],
                dispositions=approval.get("dispositions", {}),
                adjudication=approval,
                metadata_verified=approval.get("metadata_verified", False),
                boundaries_verified=approval.get("boundaries_verified", False),
            )
        item_findings = item.get("findings", [])
        if item.get("finding"):  # Early pilot document packets used a singular field.
            item_findings = [item["finding"]]
        assigned_findings = {finding_id(f) for f in item_findings}
        if set(row["dispositions"]) - assigned_findings:
            raise ValueError("Adjudication refers to an unassigned finding")
        if row["verification_status"] == "verified" and any(
            row["dispositions"].get(key, {}).get("status")
            not in {"corrected", "source_confirmed_false_positive", "intentional_exclusion"}
            or not row["dispositions"].get(key, {}).get("reason")
            for key in assigned_findings
        ):
            row["verification_status"] = "blocked"
        if (
            number == 0
            and row["verification_status"] == "verified"
            and not (row.get("metadata_verified") and row.get("boundaries_verified"))
        ):
            row["verification_status"] = "inspected"
        if packet["mode"] != "visual":
            raise ValueError(
                "Text results need coordinator interpretation; they are not automated structural proof"
            )
        rows = [
            r for r in rows if (r["document_id"], r["pdf_page"]) != (row["document_id"], number)
        ]
        rows.append(row)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    temporary = ledger.with_suffix(".tmp")
    temporary.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    temporary.replace(ledger)
    return {"passes": len(result["passes"]), "exceptions": len(result["exceptions"])}


def samples(work):
    """Choose a deterministic 2% of visual passes, at least one per document."""
    by_doc = {}
    for path in sorted((work / "assignments").glob("*/result.json")):
        packet = read_json(path.parent / "packet.json")
        result = validate_result(packet, read_json(path))
        if packet["mode"] != "visual":
            continue
        for item in packet["items"]:
            if item["pdf_page"] > 0 and item["id"] in result["passes"]:
                row = {
                    "document_id": packet["document_id"],
                    "pdf_page": item["pdf_page"],
                    "assignment": str(path.parent.resolve()),
                    "id": item["id"],
                    "image": item.get("image"),
                }
                by_doc.setdefault(packet["document_id"], []).append(row)
    selected = []
    for rows in by_doc.values():
        ordered = sorted(
            rows,
            key=lambda row: sha256(f"{row['document_id']}:{row['pdf_page']}".encode()).hexdigest(),
        )
        selected.extend(ordered[: max(1, math.ceil(len(rows) * 0.02))])
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_cli = commands.add_parser("prepare")
    for key in ("parsed", "audit", "work"):
        prepare_cli.add_argument("--" + key, type=Path, required=True)
    prepare_cli.add_argument("--doc-id", required=True)
    prepare_cli.add_argument("--pages", type=int, nargs="+", required=True)
    prepare_cli.add_argument("--document-check", action="store_true")
    prepare_cli.add_argument("--cache", type=Path, help="Reuse evidence across pilot/recheck runs")
    prepare_cli.add_argument("--context", type=int, nargs="*", default=[])
    prepare_cli.add_argument("--category", required=True)
    prepare_cli.add_argument("--rotation", type=int, choices=[0, 90, 180, 270], default=0)
    prepare_cli.add_argument("--mode", choices=["visual", "text"], default="visual")
    prepare_cli.add_argument(
        "--geometry", action="store_true", help="Include assigned source spans"
    )
    merge_cli = commands.add_parser("merge")
    merge_cli.add_argument("folder", type=Path)
    merge_cli.add_argument("--ledger", type=Path, required=True)
    merge_cli.add_argument("--parsed", type=Path, help="Validate against current canonical outputs")
    sample_cli = commands.add_parser("sample")
    sample_cli.add_argument("--work", type=Path, required=True)
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "sample":
        print(json.dumps(samples(**args), indent=2))
    else:
        print(prepare(**args) if command == "prepare" else merge(**args))


if __name__ == "__main__":
    main()
