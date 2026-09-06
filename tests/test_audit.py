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
