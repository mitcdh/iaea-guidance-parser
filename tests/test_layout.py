"""Real, tiny PDFs exercise geometry without depending on the private corpus."""

import fitz
import pytest

from iaea_guidance_parser.exporters import _record_content_text
from iaea_guidance_parser.models import DocumentMetadata, PageText
from iaea_guidance_parser.parser import IAEAGuidanceParser
from iaea_guidance_parser.pdf_extract import extract_pages


def make_table_pdf(path):
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((70, 60), "2. DEMONSTRATION", fontname="hebo", fontsize=14)
    page.insert_text((70, 90), "2.1. Ordinary prose before the table.")
    page.insert_text((70, 130), "TABLE 1. A CHECKLIST")
    for x in (70, 300, 540):
        page.draw_line((x, 150), (x, 300))
    for y in (150, 200, 250, 300):
        page.draw_line((70, y), (540, y))
    for point, text in [
        ((80, 175), "Item"),
        ((310, 175), "Meaning"),
        ((80, 225), "2.2. Numbered cell"),
        ((310, 225), "Keep the relationship"),
        ((80, 275), "Blank value"),
    ]:
        page.insert_text(point, text)
    page.insert_text((70, 340), "2.3. Ordinary prose after the table.")
    document.save(path)
    document.close()


def make_merged_grid_pdf(path):
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((70, 125), "TABLE 1. MERGED GRID")
    page.draw_line((70, 150), (520, 150))
    page.draw_line((70, 300), (520, 300))
    page.draw_line((70, 150), (70, 300))
    page.draw_line((520, 150), (520, 300))
    page.draw_line((220, 200), (220, 300))
    page.draw_line((370, 150), (370, 300))
    page.draw_line((70, 200), (520, 200))
    page.draw_line((220, 250), (520, 250))
    for point, text in [
        ((80, 180), "Merged heading"),
        ((380, 180), "Right"),
        ((80, 240), "Vertical label"),
        ((230, 230), "Row 1"),
        ((380, 230), "Value 1"),
        ((230, 280), "Row 2"),
        ((380, 280), "Value 2"),
    ]:
        page.insert_text(point, text)
    document.save(path)
    document.close()


def parse_heading_followed_by_table(lines):
    table_marker = "[[TABLE:p1:1]]"
    table_text = "TABLE 1. EXAMPLE\nFirst cell\nSecond cell"
    page = PageText(
        pdf_page=1,
        printed_page="1",
        text="\n".join([*lines, table_marker]),
        lines=[*lines, table_marker],
        tables={
            table_marker: {
                "element_id": "TABLE 1",
                "caption": "TABLE 1. EXAMPLE",
                "source_label": "TABLE 1.",
                "label_style": "arabic",
                "raw_text": table_text,
                "pdf_page": 1,
                "bbox": [60, 150, 540, 250],
                "layout": "grid",
                "row_count": 2,
                "column_count": 2,
                "cells": [],
                "source_spans": [],
            }
        },
    )
    metadata = DocumentMetadata(
        document_id="LAYOUT-TEST",
        source_file="LAYOUT-TEST.pdf",
        source_sha256="abc123",
        title="Layout test",
        series_name="IAEA Safety Standards Series",
        series_number="No. LAYOUT-TEST",
        document_family="IAEA Safety Standards Series",
        document_category="Safety Guide",
        document_type="safety_guide",
        document_domain="nuclear_safety",
    )
    return IAEAGuidanceParser(metadata, [page]).parse()[1], table_marker, table_text


def test_numbered_cells_remain_in_table_and_empty_cells_survive(tmp_path):
    pdf = tmp_path / "example.pdf"
    make_table_pdf(pdf)
    parser = IAEAGuidanceParser.from_pdf_config(pdf, {"document": {"document_id": "DEMO"}})
    _, records = parser.parse()
    table = next(record for record in records if record.element_type == "table")
    assert not any(record.element_id == "2.2" for record in records)
    assert any(record.element_id == "2.3" for record in records)
    assert table.extra["table"]["row_count"] == 3
    assert table.extra["table"]["column_count"] == 2
    assert len(table.extra["table"]["cells"]) == 6
    assert table.extra["table"]["cells"][-1]["text"] == ""
    assert "<td></td>" in _record_content_text(table)
    assert table.extra["source_spans"]
    assert all("[[TABLE:" not in record.text for record in records)


def test_reusing_a_parser_does_not_duplicate_records(tmp_path):
    pdf = tmp_path / "example.pdf"
    make_table_pdf(pdf)
    parser = IAEAGuidanceParser.from_pdf(pdf)
    first = [record.to_dict() for record in parser.parse()[1]]
    second = [record.to_dict() for record in parser.parse()[1]]
    assert first == second


def test_figure_suppression_preserves_prose_in_an_adjacent_column(tmp_path):
    pdf = tmp_path / "figure.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(60, 120, 310, 310))
    page.draw_rect(fitz.Rect(350, 120, 560, 300))
    page.insert_text((80, 160), "DIAGRAM INTERIOR")
    page.insert_text((350, 160), "2.1. Adjacent body prose.")
    page.insert_text((60, 330), "FIG. 1. Example diagram")
    document.save(pdf)
    document.close()
    extracted = extract_pages(pdf)[0]
    assert "DIAGRAM INTERIOR" in extracted.suppressed_figure_lines
    assert "2.1. Adjacent body prose." in extracted.lines


def test_footer_near_margin_boundary_is_removed_with_sequence_evidence(tmp_path):
    pdf = tmp_path / "footers.pdf"
    document = fitz.open()
    for number in (1, 2, 3):
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 100), f"2.{number}. Page content.")
        page.insert_text((60, 715), str(number))
    document.save(pdf)
    document.close()
    pages = extract_pages(pdf)
    assert [page.printed_page for page in pages] == ["1", "2", "3"]
    assert all(str(i) not in page.lines for i, page in enumerate(pages, 1))


def test_caption_words_encoded_as_separate_objects_are_reassembled(tmp_path):
    pdf = tmp_path / "fragmented.pdf"
    document = fitz.open()
    page = document.new_page()
    for x, word in [(60, "TABLE"), (110, "4."), (145, "SOURCE"), (210, "CAPTION")]:
        page.insert_text((x, 80), word)
    page.insert_text((60, 110), "First cell and its meaning")
    document.save(pdf)
    document.close()
    parser = IAEAGuidanceParser.from_pdf(pdf)
    tables = [record for record in parser.parse()[1] if record.element_type == "table"]
    assert len(tables) == 1
    assert tables[0].element_id == "TABLE 4"
    assert "SOURCE CAPTION" in tables[0].text


def test_separate_grid_strokes_bound_a_form_figure(tmp_path):
    pdf = tmp_path / "form.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for x in (60, 300, 500):
        page.draw_line((x, 120), (x, 320))
    for y in (120, 180, 250, 320):
        page.draw_line((60, y), (500, y))
    page.insert_text((70, 160), "FORM FIELD")
    page.insert_text((60, 345), "FIG. 1. Example form")
    page.insert_text((60, 390), "2.1. Actual prose beneath the form.")
    document.save(pdf)
    document.close()
    records = IAEAGuidanceParser.from_pdf(pdf).parse()[1]
    figure = next(record for record in records if record.element_type == "figure")
    assert any(region["text"] == "FORM FIELD" for region in figure.extra["visual_text_regions"])
    assert not any(
        record.element_type == "heading" and record.text == "FORM FIELD" for record in records
    )
    assert any(record.element_id == "2.1" for record in records)


def test_horizontal_rules_bound_a_table_despite_pdf_object_order(tmp_path):
    pdf = tmp_path / "ruled.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((60, 60), "2. DEMONSTRATION", fontname="hebo")
    # The source stores body text, cells, then caption, unlike visual order.
    page.insert_text((60, 400), "This unnumbered prose is below the table.")
    for y in (150, 200, 300):
        page.draw_line((60, y), (300, y))
        page.draw_line((300, y), (540, y))
    page.insert_text((70, 175), "Item")
    page.insert_text((300, 175), "Meaning")
    page.insert_text((70, 225), "2.1. Numbered cell")
    page.insert_text((300, 225), "Value")
    page.insert_text((60, 130), "TABLE 1. EXAMPLE")
    document.save(pdf)
    document.close()
    records = IAEAGuidanceParser.from_pdf(pdf).parse()[1]
    table = next(record for record in records if record.element_type == "table")
    assert "2.1. Numbered cell" in table.text
    assert "Value" in table.text
    assert "unnumbered prose" not in table.text
    assert any("unnumbered prose" in record.text for record in records)
    assert table.extra["table"]["layout"] == "ruled"
    assert table.extra["table"]["cells"] == []
    assert "Numbered cell" in _record_content_text(table)
    assert "<table>" not in _record_content_text(table)


def test_ruled_table_regions_stop_before_the_next_caption(tmp_path):
    pdf = tmp_path / "two-tables.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    for number, top in ((1, 150), (2, 400)):
        page.insert_text((60, top - 20), f"TABLE {number}. EXAMPLE")
        for y in (top, top + 50, top + 100):
            page.draw_line((60, y), (540, y))
        page.insert_text((70, top + 25), f"Table {number} content")
    document.save(pdf)
    document.close()
    records = IAEAGuidanceParser.from_pdf(pdf).parse()[1]
    tables = [record for record in records if record.element_type == "table"]
    assert len(tables) == 2
    assert "Table 2 content" not in tables[0].text
    assert "Table 1 content" not in tables[1].text


def test_grid_cell_spans_are_preserved_in_html(tmp_path):
    pdf = tmp_path / "merged-grid.pdf"
    make_merged_grid_pdf(pdf)
    records = IAEAGuidanceParser.from_pdf(pdf).parse()[1]
    table = next(record for record in records if record.element_type == "table")
    cells = table.extra["table"]["cells"]
    merged_heading = next(cell for cell in cells if cell["text"] == "Merged heading")
    vertical_label = next(cell for cell in cells if cell["text"] == "Vertical label")
    html = _record_content_text(table)
    assert merged_heading["row_span"] == 1
    assert merged_heading["column_span"] == 2
    assert vertical_label["row_span"] == 2
    assert vertical_label["column_span"] == 1
    assert '<td colspan="2">Merged heading</td>' in html
    assert '<td rowspan="2">Vertical label</td>' in html


def test_raw_ruled_table_skips_html_cell_rendering(tmp_path):
    pdf = tmp_path / "raw-ruled.pdf"
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((60, 130), "TABLE 1. RAW RULED")
    for y in (150, 200, 250):
        page.draw_line((60, y), (540, y))
    page.insert_text((70, 175), "A raw row")
    page.insert_text((70, 225), "Another raw row")
    document.save(pdf)
    document.close()
    records = IAEAGuidanceParser.from_pdf(pdf).parse()[1]
    table = next(record for record in records if record.element_type == "table")
    content = _record_content_text(table)
    assert table.extra["table"]["layout"] == "ruled"
    assert table.extra["table"]["cells"] == []
    assert "<table>" not in content
    assert "<td" not in content
    assert "A raw row" in content


@pytest.mark.parametrize(
    "heading_lines",
    [
        ["1. INTRODUCTION", "PREPAREDNESS AND RESPONSE FOR"],
        [
            "1. INTRODUCTION",
            "PREPAREDNESS AND RESPONSE FOR",
            "NUCLEAR EMERGENCIES",
        ],
    ],
    ids=["second-line-boundary", "third-line-boundary"],
)
def test_table_marker_after_all_caps_heading_is_not_merged(heading_lines):
    records, table_marker, table_text = parse_heading_followed_by_table(heading_lines)
    table = next(record for record in records if record.element_type == "table")
    assert all(
        table_marker not in record.text
        and table_marker not in (record.title or "")
        and table_marker not in (record.caption or "")
        and all(table_marker not in section for section in record.section_path)
        and table_marker not in repr(record.to_dict())
        for record in records
    )
    assert table.text == table_text
