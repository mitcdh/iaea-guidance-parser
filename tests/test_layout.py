"""Real, tiny PDFs exercise geometry without depending on the private corpus."""

import fitz
import pytest

from iaea_guidance_parser.exporters import _record_content_text
from iaea_guidance_parser.models import DocumentMetadata, PageText
from iaea_guidance_parser.parser import IAEAGuidanceParser
from iaea_guidance_parser.pdf_extract import extract_pages
from iaea_guidance_parser.reviews import content_fingerprint


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


def test_opt_in_reading_order_uses_visible_positions_on_selected_pages(tmp_path):
    pdf = tmp_path / "object-order.pdf"
    with fitz.open() as document:
        for _ in range(2):
            page = document.new_page(width=600, height=800)
            page.insert_text((70, 700), "www.example.org")
            page.insert_text((70, 70), "PUBLICATION TITLE")
            page.insert_text((70, 100), "Publication text.")
        document.save(pdf)
    pages = extract_pages(pdf, {"reading_order_pages": [1]})
    assert pages[0].lines == ["PUBLICATION TITLE", "Publication text.", "www.example.org"]
    assert pages[1].lines == ["www.example.org", "PUBLICATION TITLE", "Publication text."]


def test_reviewed_actual_text_override_preserves_glyph_text_and_geometry(tmp_path):
    pdf = tmp_path / "duplicate-marker.pdf"
    with fitz.open() as document:
        for _ in range(2):
            page = document.new_page()
            page.insert_text((70, 70), "- ")
            stream_id = page.get_contents()[0]
            stream = document.xref_stream(stream_id)
            document.update_stream(
                stream_id, b"/Span << /ActualText (- -) >> BDC\n" + stream + b"\nEMC"
            )
            page.insert_text((85, 70), "A single visible list item.")
        document.save(pdf)
    pages = extract_pages(pdf, {"ignore_actual_text_pages": [1]})
    assert " ".join(pages[0].lines) == "- A single visible list item."
    assert " ".join(pages[1].lines) == "- - A single visible list item."
    assert [line["text"] for line in pages[0].source_lines] == pages[0].lines


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


def test_opt_in_reading_order_sorts_decoded_lines_inside_one_pdf_block():
    from iaea_guidance_parser.pdf_extract import _extract_page_text

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 120), "Bottom", fontsize=22)
        page.insert_text((72, 90), "Top", fontsize=22)
        rules = [{"pdf_pages": [1], "fonts": ["Helvetica"], "ascii_offset": 0}]
        assert _extract_page_text(page, 1, rules) == "Bottom\nTop"
        assert _extract_page_text(page, 1, rules, sort_lines=True) == "Top\nBottom"


def test_reviewed_reading_rotation_orders_plain_text_without_rotating_coordinates(tmp_path):
    pdf = tmp_path / "rotated-prose.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        page.insert_text((160, 500), "Second line", rotate=90)
        page.insert_text((120, 500), "First line", rotate=90)
        document.save(pdf)
    page = extract_pages(pdf, {"page_reading_rotations": {1: 90}})[0]
    assert page.lines == ["First line", "Second line"]
    assert page.source_lines[0]["bbox"][0] < page.source_lines[1]["bbox"][0]
    with pytest.raises(ValueError, match="multiple of 90"):
        extract_pages(pdf, {"page_reading_rotations": {1: 45}})


def test_reviewed_rotated_table_preserves_subrows_blanks_and_source_coordinates(tmp_path):
    pdf = tmp_path / "rotated-table.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        for x, y, text in [
            (80, 500, "TABLE 1. DEMO"),
            (120, 490, "Material"),
            (120, 290, "Category"),
            (155, 490, "Uranium"),
            (155, 290, "First line"),
            (170, 290, "second line"),
        ]:
            page.insert_text((x, y), text, rotate=90)
        document.save(pdf)
    cells = [
        {"row": 0, "column": 0, "bbox": [100, 300, 135, 500]},
        {"row": 0, "column": 1, "bbox": [100, 100, 135, 300]},
        {"row": 1, "column": 0, "row_span": 2, "bbox": [135, 300, 250, 500]},
        {"row": 1, "column": 1, "bbox": [135, 100, 190, 300]},
        {"row": 2, "column": 1, "bbox": [190, 100, 250, 300]},
    ]
    config = {
        "page_reading_rotations": {1: 90},
        "table_layouts": [
            {
                "pdf_pages": [1],
                "caption": "TABLE 1. DEMO",
                "bbox": [100, 100, 250, 500],
                "row_count": 3,
                "column_count": 2,
                "cells": cells,
            }
        ],
    }
    _, records = IAEAGuidanceParser.from_pdf_config(pdf, {"parser": config}).parse()
    table = next(r for r in records if r.element_type == "table")
    actual = table.extra["table"]["cells"]
    assert [(c["row"], c["column"], c["row_span"], c["text"]) for c in actual] == [
        (0, 0, 1, "Material"),
        (0, 1, 1, "Category"),
        (1, 0, 2, "Uranium"),
        (1, 1, 1, "First line\nsecond line"),
        (2, 1, 1, ""),
    ]
    assert [c["bbox"] for c in actual] == [c["bbox"] for c in cells]
    assert 'rowspan="2"' in _record_content_text(table)
    assert "<td></td>" in _record_content_text(table)
    assert "<td>First line<br>second line</td>" in _record_content_text(table)
    assert not any(r.element_type == "heading" for r in records)
    # A declared grid with a missing cell cannot silently supply misleading relationships.
    config["table_layouts"][0]["cells"] = cells[:-1]
    with pytest.raises(ValueError, match="cover the declared grid exactly"):
        extract_pages(pdf, config)


@pytest.mark.parametrize("caption_gap", [15, 80])
@pytest.mark.parametrize("fragmented", [False, True])
def test_reviewed_wrapped_table_caption_and_notes_survive_export(tmp_path, caption_gap, fragmented):
    pdf = tmp_path / "wrapped-table.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        for point, text in [
            ((70, 60), "2. DEMONSTRATION"),
            ((70, 100), "TABLE 1. OUTCOMES AND"),
            ((70, 100 + caption_gap), "RESPONSE ACTIONS (cont.)"),
            ((80, 205), "Outcome"),
            ((280, 205), "Action"),
            ((80, 250), "Public information"),
            ((280, 250), "Provide advice.a"),
            ((70, 300), "a Use the approved advice."),
            ((70, 370), "2.1. Prose after the table."),
        ]:
            if fragmented and point == (70, 100):
                for x, word in [(70, "TABLE 1."), (150, "OUTCOMES"), (250, "AND")]:
                    page.insert_text((x, 100), word)
            else:
                page.insert_text(point, text)
        document.save(pdf)
    config = {
        "table_layouts": [
            {
                "pdf_pages": [1],
                "caption": "TABLE 1. OUTCOMES AND RESPONSE ACTIONS (cont.)",
                "bbox": [70, 185, 530, 320],
                "row_count": 2,
                "column_count": 2,
                "cells": [
                    {"row": row, "column": col, "bbox": [x0, y0, x1, y1]}
                    for row, (y0, y1) in enumerate([(185, 225), (225, 275)])
                    for col, (x0, x1) in enumerate([(70, 270), (270, 530)])
                ],
            }
        ]
    }
    if caption_gap == 80:
        # Matching words far apart cannot establish a wrapped caption.
        with pytest.raises(ValueError, match="caption does not match source"):
            extract_pages(pdf, config)
        return
    _, records = IAEAGuidanceParser.from_pdf_config(pdf, {"parser": config}).parse()
    table = next(r for r in records if r.element_type == "table")
    assert table.title == config["table_layouts"][0]["caption"]
    assert [c["text"] for c in table.extra["table"]["cells"]] == [
        "Outcome",
        "Action",
        "Public information",
        "Provide advice.a",
    ]
    assert table.extra["table"]["notes"] == "a Use the approved advice."
    exported = _record_content_text(table)
    assert exported.count("a Use the approved advice.") == 1
    assert exported.index("</table>") < exported.index("a Use the approved advice.")
    assert "RESPONSE ACTIONS (cont.)" in exported
    assert next(r for r in records if r.element_id == "2.1").text == "Prose after the table."
    assert "2.1" not in table.text
    config["table_layouts"][0]["caption"] = "TABLE 1. OUTCOMES AND AN INVENTED ENDING"
    with pytest.raises(ValueError, match="caption does not match source"):
        extract_pages(pdf, config)


@pytest.mark.parametrize("retain_shading", [False, True])
def test_reviewed_cell_shading_keeps_blank_ratings_and_ignores_gaps(tmp_path, retain_shading):
    pdf = tmp_path / "shaded-ratings.pdf"
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        page.insert_text((70, 100), "TABLE 1. RATINGS")
        shape = page.new_shape()
        # A single compound path covers the first and third cells, not the middle.
        shape.draw_rect(fitz.Rect(70, 150, 170, 200))
        shape.draw_rect(fitz.Rect(270, 150, 370, 200))
        shape.finish(fill=(1, 0, 0), color=None)
        shape.commit()
        document.save(pdf)
    rule = {
        "pdf_pages": [1],
        "caption": "TABLE 1. RATINGS",
        "bbox": [70, 150, 370, 200],
        "row_count": 1,
        "column_count": 3,
        "retain_cell_shading": retain_shading,
        "cells": [
            {"row": 0, "column": c, "bbox": [70 + c * 100, 150, 170 + c * 100, 200]}
            for c in range(3)
        ],
    }
    _, records = IAEAGuidanceParser.from_pdf_config(
        pdf, {"parser": {"table_layouts": [rule]}}
    ).parse()
    table = next(r for r in records if r.element_type == "table")
    cells = table.extra["table"]["cells"]
    assert [c["text"] for c in cells] == ["", "", ""]
    assert [c.get("background_color") for c in cells] == (
        ["#ff0000", None, "#ff0000"] if retain_shading else [None, None, None]
    )
    exported = _record_content_text(table)
    assert exported.count('style="background-color: #ff0000"') == (2 if retain_shading else 0)
    assert "<td></td>" in exported
    assert ("not printed cell text" in exported) == retain_shading
    if retain_shading:
        original = content_fingerprint([table.to_dict()])
        cells[0]["background_color"] = "#ffff00"
        assert content_fingerprint([table.to_dict()]) != original


def test_reviewed_cell_shading_rejects_transparency():
    from iaea_guidance_parser.pdf_extract import _cell_shading

    rect = fitz.Rect(0, 0, 100, 100)
    with pytest.raises(ValueError, match="opaque fill"):
        _cell_shading([{"fill": (1, 0, 0), "fill_opacity": 0.5, "items": [("re", rect, 1)]}], rect)


def test_reviewed_figure_keeps_native_labels_notes_and_transcription_with_caption(tmp_path):
    from iaea_guidance_parser.provenance import sha256_file

    pdf = tmp_path / "figure.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=500)
        page.insert_text((40, 40), "2. DEMONSTRATION", fontname="hebo")
        page.insert_text((40, 65), "2.1. Prose must remain separate.")
        page.insert_text((80, 120), "Native label")
        page.insert_text((40, 160), "Note: These values are illustrative.")
        page.insert_text((40, 185), "FIG. 1. Risk example.")
        page.insert_text((40, 220), "2.2. Prose following the diagram.")
        document.save(pdf)
    layout = {
        "caption": "FIG. 1. Risk example.",
        "bbox": [35, 80, 365, 170],
        "transcription": {
            "text": "RISK",
            "reviewer": "Fixture reviewer",
            "notes": "Synthetic reviewed label.",
        },
    }
    config = {"figure_layouts": {"source_sha256": sha256_file(pdf), "pages": {1: [layout]}}}
    _, records = IAEAGuidanceParser.from_pdf_config(pdf, {"parser": config}).parse()
    figure = next(r for r in records if r.element_type == "figure")
    regions = figure.extra["visual_text_regions"]
    assert [r["text"] for r in regions] == [
        "Native label",
        "Note: These values are illustrative.",
        "RISK",
    ]
    assert regions[-1]["transcription"]["geometry"] == "whole_reviewed_figure_region"
    assert all("Note:" not in r.text and "Native label" not in r.text for r in records)
    exported = _record_content_text(figure)
    assert "Source note: includes reviewed transcription" in exported and "RISK" in exported
    assert "Note: These values" in exported
    config["figure_layouts"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        extract_pages(pdf, config)
    config["figure_layouts"]["source_sha256"] = sha256_file(pdf)
    layout["caption"] = "FIG. 2. Wrong target."
    with pytest.raises(ValueError, match="every caption"):
        extract_pages(pdf, config)
    layout["caption"] = "FIG. 1. Risk example."
    layout["transcription"]["text"] = "Native label"
    with pytest.raises(ValueError, match="duplicates native"):
        extract_pages(pdf, config)
    layout["transcription"]["text"] = "RISK"
    layout["transcription"]["reviewer"] = ""
    with pytest.raises(ValueError, match="reviewer"):
        extract_pages(pdf, config)
    layout.pop("transcription")
    layout["bbox"] = [35, 80, 365, 200]
    with pytest.raises(ValueError, match="above its caption"):
        extract_pages(pdf, config)


def test_reviewed_image_transcription_is_hash_bound_and_keeps_explicit_provenance(tmp_path):
    from iaea_guidance_parser.provenance import sha256_file

    pdf = tmp_path / "image-cover.pdf"
    with fitz.open() as source:
        page = source.new_page(width=300, height=400)
        page.insert_text((40, 80), "Reviewed cover")
        bitmap = page.get_pixmap().tobytes("png")
    with fitz.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=bitmap)
        document.new_page().insert_text((50, 80), "Native text")
        document.save(pdf)
    config = {
        "image_transcriptions": {
            "source_sha256": sha256_file(pdf),
            "pages": {
                1: {
                    "lines": ["Reviewed cover"],
                    "reviewer": "Fixture reviewer",
                    "notes": "Compared with the synthetic cover image.",
                }
            },
        }
    }
    _, records = IAEAGuidanceParser.from_pdf_config(pdf, {"parser": config}).parse()
    record = next(r for r in records if "Reviewed cover" in r.text)
    assert record.extra["reviewed_image_transcriptions"] == [
        {
            "pdf_page": 1,
            "geometry": "whole_page",
            "reviewer": "Fixture reviewer",
            "notes": "Compared with the synthetic cover image.",
        }
    ]
    assert record.extra["source_spans"][0]["bbox"] == [0, 0, 300, 400]
    assert "Source note: reviewed transcription" in _record_content_text(record)
    renamed = tmp_path / "renamed.pdf"
    renamed.write_bytes(pdf.read_bytes())
    assert extract_pages(renamed, config)[0].lines == ["Reviewed cover"]
    config["image_transcriptions"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        extract_pages(pdf, config)
    config["image_transcriptions"]["source_sha256"] = sha256_file(pdf)
    transcript = config["image_transcriptions"]["pages"].pop(1)
    config["image_transcriptions"]["pages"][2] = transcript
    with pytest.raises(ValueError, match="image-only"):
        extract_pages(pdf, config)
    config["image_transcriptions"]["pages"] = {1: {"lines": ["Unreviewed"]}}
    with pytest.raises(ValueError, match="reviewer and source notes"):
        extract_pages(pdf, config)
