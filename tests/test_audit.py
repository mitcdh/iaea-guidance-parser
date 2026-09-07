import json
import shutil

import fitz
import pytest

from iaea_guidance_parser.audit import run_audit, tokens
from iaea_guidance_parser.exporters import write_outputs, write_series_outputs
from iaea_guidance_parser.series import parse_one_document


@pytest.fixture
def tiny_series(tmp_path):
    source = tmp_path / "inputs"
    source.mkdir()
    pdf = source / "example.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "2. EXAMPLE")
    page.insert_text((72, 110), "2.1. Every source word should remain traceable.")
    document.save(pdf)
    document.close()
    parsed = tmp_path / "parsed"
    result = parse_one_document(
        pdf_path=pdf,
        pdf_root=source,
        out_root=parsed,
        series_config={"document_defaults": {"document_id": "DEMO"}},
    )
    write_outputs(result.output_dir, result.metadata, result.records)
    write_series_outputs(parsed, series_config={}, results=[result], failures=[])
    return source, parsed, tmp_path / "audit"


def test_audit_normalization_preserves_technical_operators():
    assert tokens("moni-\ntoring ≤ 10 mSv / h") == tokens("monitoring ≤ 10 mSv / h")
    assert tokens("≤ 10") != tokens("< 10")
    assert tokens("10") != tokens("100")


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler required for source comparison")
def test_source_audit_checks_exports_and_leaves_outputs_untouched(tiny_series):
    source, parsed, out = tiny_series
    before = {p: p.read_bytes() for p in parsed.rglob("*") if p.is_file()}
    summary = run_audit(source, parsed, out)
    assert summary["pages_checked"] == 1
    assert summary["visual_pages_reviewed"] == 0
    assert summary["visual_pages_pending"] == 1
    assert not any("mismatch" in check for check in summary["by_check"])
    assert before == {p: p.read_bytes() for p in parsed.rglob("*") if p.is_file()}


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler required for source comparison")
def test_audit_detects_tampered_markdown_and_missing_source(tiny_series):
    source, parsed, out = tiny_series
    part = next((parsed / "series_custom_gpt_knowledge_parts").glob("part_*.md"))
    part.write_text(part.read_text().replace("Every source word", "Altered wording"))
    summary = run_audit(source, parsed, out)
    assert summary["by_check"]["markdown_content_mismatch"] == 1
    assert summary["by_check"]["part_hash_mismatch"] == 1
    next(source.glob("*.pdf")).unlink()
    summary = run_audit(source, parsed, out)
    assert summary["by_check"]["missing_source"] == 1


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler required for source comparison")
def test_visual_review_is_invalidated_by_source_or_output_change(tiny_series):
    import csv

    source, parsed, out = tiny_series
    run_audit(source, parsed, out)
    row = next(csv.DictReader((out / "audit_coverage.csv").open()))
    row.update(
        pdf_page=int(row["pdf_page"]),
        visual_review="reviewed",
        notes="Checked synthetic source page.",
        schema_version=2,
        method="visual",
        verification_status="verified",
        evidence=[{"kind": "synthetic_source_image_comparison"}],
    )
    reviews = out / "reviews.jsonl"
    reviews.write_text(json.dumps(row) + "\n")
    assert run_audit(source, parsed, out, reviews=reviews)["visual_pages_reviewed"] == 1
    row["output_sha256"] = "stale"
    reviews.write_text(json.dumps(row) + "\n")
    assert run_audit(source, parsed, out, reviews=reviews)["visual_pages_reviewed"] == 0


def test_export_audit_rejects_a_table_marker_absorbed_into_a_heading(tiny_series):
    from iaea_guidance_parser.audit import _check_exports, _load_records

    _, parsed, _ = tiny_series
    records = _load_records(parsed / "series_structural_index.jsonl")
    records["DEMO"][0]["text"] += " [[TABLE:p1:1]]"
    entries = json.loads((parsed / "series_manifest.json").read_text())["documents"]
    findings = _check_exports(parsed, records, entries)
    assert any(finding["check"] == "unconsumed_layout_marker" for finding in findings)


@pytest.mark.parametrize("note_state", ["none", "parsed", "absorbed"])
def test_automated_structural_verification_is_not_counted_as_visual(tiny_series, note_state):
    with_footnote = note_state != "none"
    source, parsed, out = tiny_series
    pdf = next(source.glob("*.pdf"))
    with fitz.open() as doc:
        doc.new_page()
        page = doc.new_page()
        page.insert_text((72, 72), "1. INTRODUCTION")
        page.insert_text((72, 110), "1.1. Every source word should remain traceable.")
        if with_footnote:
            page.insert_text((72, 700), "1 This note needs a verified source anchor.", fontsize=8)
        doc.new_page()
        doc.set_toc([[1, "1. INTRODUCTION", 2]])
        data = doc.tobytes()
    pdf.write_bytes(data)
    result = parse_one_document(
        pdf_path=pdf,
        pdf_root=source,
        out_root=parsed,
        series_config={"document_defaults": {"document_id": "DEMO", "title": "Synthetic document"}},
    )
    assert any(r.element_type == "footnote" for r in result.records) == with_footnote
    if note_state == "absorbed":
        note = next(r for r in result.records if r.element_type == "footnote")
        paragraph = next(r for r in result.records if r.element_type == "paragraph")
        paragraph.text += f" {note.element_id} {note.text}"
        result.records.remove(note)
    write_outputs(result.output_dir, result.metadata, result.records)
    write_series_outputs(parsed, series_config={}, results=[result], failures=[])
    summary = run_audit(source, parsed, out)
    assert summary["verification_by_method"].get("automated_structural", 0) == (
        0 if with_footnote else 1
    )
    if with_footnote:
        import csv

        rows = list(csv.DictReader((out / "audit_coverage.csv").open()))
        assert "source_footnote_candidate" in rows[1]["visual_reasons"]
        assert rows[1]["missing_token_count"] == "0"
        assert rows[1]["verification_status"] == "pending"
    assert summary["visual_pages_reviewed"] == 0
    assert summary["metadata_documents_verified"] == 0
    assert summary["boundary_documents_verified"] == 0


@pytest.mark.parametrize("region", ["Glossary", "Body"])
def test_outline_match_does_not_verify_definition_segmentation(tiny_series, region):
    source, parsed, out = tiny_series
    pdf = next(source.glob("*.pdf"))
    with fitz.open() as doc:
        doc.new_page()
        page = doc.new_page()
        page.insert_text((72, 72), "DEFINITIONS")
        page.insert_text((72, 110), "alpha. First definition.")
        page.insert_text((72, 140), "beta. Second definition.")
        doc.new_page()
        doc.set_toc([[1, "DEFINITIONS", 2]])
        pdf.write_bytes(doc.tobytes())
    result = parse_one_document(
        pdf_path=pdf,
        pdf_root=source,
        out_root=parsed,
        series_config={
            "document_defaults": {"document_id": "DEMO", "title": "Synthetic document"},
            "parser": {"page_regions": {2: {"region": region, "section": "DEFINITIONS"}}},
        },
    )
    # All words and the outline heading match, but the terms remain one text block.
    write_outputs(result.output_dir, result.metadata, result.records)
    write_series_outputs(parsed, series_config={}, results=[result], failures=[])
    summary = run_audit(source, parsed, out)
    import csv

    row = list(csv.DictReader((out / "audit_coverage.csv").open()))[1]
    assert row["missing_token_count"] == "0"
    assert "definitions" in row["visual_reasons"]
    assert row["verification_status"] == "pending"
    assert not summary["verification_by_method"].get("automated_structural")
