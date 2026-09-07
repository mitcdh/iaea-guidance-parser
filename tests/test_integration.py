"""PDF regressions for automatic replacements of reviewed layout rules."""

import fitz
import pytest

from iaea_guidance_parser.audit import _source_pages, displayed_page_text
from iaea_guidance_parser.models import DocumentMetadata
from iaea_guidance_parser.parser import IAEAGuidanceParser
from iaea_guidance_parser.pdf_extract import extract_pages
from iaea_guidance_parser.provenance import sha256_file


def parse(pdf, config=None):
    pages = extract_pages(pdf, config)
    return IAEAGuidanceParser(
        DocumentMetadata("TEST", str(pdf), "test"), pages, parser_config=config
    ).parse()[1]


def text(page, x, y, value, size=11, font="tiro"):
    page.insert_text((x, y), value, fontsize=size, fontname=font)
    return x + fitz.get_text_length(value, fontname=font, fontsize=size)


def test_reviewed_superscripts_preserve_position_without_rewriting_plain_digits(tmp_path):
    pdf = tmp_path / "powers.pdf"
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page()
            text(page, 60, 60, "1. INTRODUCTION", font="tibo")
            x = text(page, 60, 100, "1.1. Value: 10")
            x = text(page, x, 96, "-3", size=7)
            text(page, x, 100, "; ordinary 104; NA")
            x = text(page, 60, 130, "NA")
            text(page, x, 126, "d", size=7)
        doc.save(pdf)
    config = {
        "reviewed_superscripts": {
            "source_sha256": sha256_file(pdf),
            "pages": [1],
            "reviewer": "Test reviewer",
            "notes": "Source shows raised exponent and note label.",
        }
    }
    original = extract_pages(pdf)
    pages = extract_pages(pdf, config)
    assert any("10⁻³; ordinary 104" in line for line in pages[0].lines)
    assert "NAᵈ" in pages[0].lines
    assert pages[1] == original[1]
    spans = [s for line in pages[0].source_typography.values() for s in line]
    assert any(s["text"] == "⁻³" and s["original_text"] == "-3" for s in spans)
    records = IAEAGuidanceParser(
        DocumentMetadata("POWERS", str(pdf), sha256_file(pdf)), pages
    ).parse()[1]
    assert any("10⁻³; ordinary 104" in r.text for r in records)
    config["reviewed_superscripts"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source PDF SHA-256"):
        extract_pages(pdf, config)


def test_reviewed_superscripts_reject_unsupported_raised_characters(tmp_path):
    pdf = tmp_path / "unknown-power.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        x = text(page, 60, 100, "10")
        text(page, x, 96, "q", size=7)
        doc.save(pdf)
    config = {
        "reviewed_superscripts": {
            "source_sha256": sha256_file(pdf),
            "pages": [1],
            "reviewer": "Test reviewer",
            "notes": "A letter with no supported superscript mapping.",
        }
    }
    with pytest.raises(ValueError, match="unsupported reviewed superscript"):
        extract_pages(pdf, config)


def test_inline_marker_owns_note_before_a_later_paragraph(tmp_path):
    pdf = tmp_path / "notes.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        text(page, 60, 60, "1. INTRODUCTION", font="tibo")
        x = text(page, 60, 100, "1.1. Radiation risks")
        x = text(page, x, 96, "1", size=7)
        text(page, x, 100, " require assessment.")
        text(page, 60, 150, "1.2. A different paragraph follows.")
        x = text(page, 70, 696, "1 ", size=6)
        text(page, x, 700, "This note defines the radiation risks.", size=9)
        doc.save(pdf)
    records = parse(pdf)
    note = next(r for r in records if r.element_type == "footnote")
    assert note.linked_from_element_id == "1.1"
    assert note.extra["inline_anchor"]["pdf_page"] == 1
    assert "risks1" in next(r.text for r in records if r.element_id == "1.1")


@pytest.mark.parametrize(
    "prefix,suffix",
    [
        ("10", ""),
        ("m", ""),
        ("H", "O"),
        ("U", ""),
        ("cm", ""),
        ("km", ""),
        ("mSv", ""),
        ("Pu", "+"),
        ("Co", ""),
    ],
)
def test_scientific_superscript_is_not_a_note_anchor(tmp_path, prefix, suffix):
    pdf = tmp_path / "exponent.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        text(page, 60, 60, "1. INTRODUCTION", font="tibo")
        x = text(page, 60, 100, "1.1. The value is " + prefix)
        x = text(page, x, 96, "2", size=7)
        text(page, x, 100, suffix + ".")
        x = text(page, 70, 696, "2 ", size=6)
        text(page, x, 700, "A note whose inline marker is missing.", size=9)
        doc.save(pdf)
    note = next(r for r in parse(pdf) if r.element_type == "footnote")
    assert note.linked_from_element_id is None
    assert any(n.startswith("Unresolved PDF footnote anchor") for n in note.parser_notes)


def test_glossary_typography_does_not_split_body_emphasis(tmp_path):
    pdf = tmp_path / "glossary.pdf"
    with fitz.open() as doc:
        for title in ["1. INTRODUCTION", "GLOSSARY"]:
            page = doc.new_page()
            text(page, 60, 60, title, font="tibo")
            for y, term in [(100, "radioactive material."), (140, "regulatory body.")]:
                x = text(page, 60, y, term, font="tibo")
                text(page, x, y, " A definition follows here.")
        doc.save(pdf)
    records = parse(pdf)
    definitions = [
        r for r in records if r.element_id in {"radioactive material", "regulatory body"}
    ]
    assert len(definitions) == 2
    assert all(r.page_start_pdf == 2 and r.source_region == "Glossary" for r in definitions)


def test_wrapped_heading_uses_geometry_but_distant_headings_stay_separate(tmp_path):
    pdf = tmp_path / "headings.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        text(page, 60, 60, "1. INTRODUCTION", font="tibo")
        text(page, 60, 100, "1.1. Introductory text.")
        text(page, 60, 150, "2. NUCLEAR SECURITY", font="tibo")
        text(page, 60, 164, "RESPONSIBILITIES", font="tibo")
        text(page, 60, 200, "2.1. Substantive text.")
        text(page, 60, 250, "INDEPENDENT HEADING", font="tibo")
        text(page, 60, 280, "ANOTHER HEADING", font="tibo")
        doc.save(pdf)
    headings = [r.text for r in parse(pdf) if r.element_type == "heading"]
    assert "2. NUCLEAR SECURITY RESPONSIBILITIES" in headings
    assert not any("INDEPENDENT HEADING ANOTHER" in h for h in headings)


def test_adjacent_figures_keep_their_own_labels(tmp_path):
    pdf = tmp_path / "figures.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        for left, name in [(50, "LEFT"), (350, "RIGHT")]:
            page.draw_rect(fitz.Rect(left, 100, left + 200, 300))
            text(page, left + 20, 150, name)
        text(page, 350, 320, "FIG. 2. Right diagram")
        text(page, 50, 320, "FIG. 1. Left diagram")
        text(page, 50, 360, "1.1. Body prose below the figures.")
        doc.save(pdf)
    figures = {r.element_id: r for r in parse(pdf) if r.element_type == "figure"}
    assert [r["text"] for r in figures["FIG. 1"].extra["visual_text_regions"]] == ["LEFT"]
    assert [r["text"] for r in figures["FIG. 2"].extra["visual_text_regions"]] == ["RIGHT"]


def test_automatic_grid_preserves_a_blank_shaded_cell(tmp_path):
    pdf = tmp_path / "grid.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        text(page, 60, 80, "TABLE 1. Source ratings")
        page.draw_rect(fitz.Rect(160, 100, 260, 140), fill=(1, 0, 0), color=None)
        for x in [60, 160, 260]:
            page.draw_line((x, 100), (x, 180))
        for y in [100, 140, 180]:
            page.draw_line((60, y), (260, y))
        text(page, 70, 125, "A")
        text(page, 70, 165, "B")
        text(page, 170, 165, "C")
        text(page, 60, 195, "Note: Blank red means high risk.", size=9)
        text(page, 60, 218, "1.1. This is body prose.")
        doc.save(pdf)
    table = next(r for r in parse(pdf) if r.element_type == "table")
    cells = table.extra["table"]["cells"]
    assert len(cells) == 4
    blank = next(c for c in cells if c["row"] == 0 and c["column"] == 1)
    assert blank["text"] == "" and blank["background_color"] == "#ff0000"
    assert table.extra["table"]["notes"] == "Note: Blank red means high risk."
    assert "This is body prose." not in table.text


def test_pdf_mathematical_characters_and_raised_exponent_survive(tmp_path):
    pdf = tmp_path / "notation.pdf"
    expression = "μSv h⁻¹ ≤ 5 × 10⁻³; ²³⁵U; H₂O; α ≥ β; −1 ± 2"
    with fitz.open() as doc:
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        writer.append((60, 60), "1. INTRODUCTION", font=fitz.Font("tibo"), fontsize=11)
        writer.append((60, 100), "1.1. " + expression, font=fitz.Font("tiro"), fontsize=11)
        writer.write_text(page)
        doc.save(pdf)
    pages = extract_pages(pdf)
    assert expression in pages[0].text
    assert expression in next(r.text for r in parse(pdf) if r.element_id == "1.1")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_independent_text_uses_displayed_crop_at_every_rotation(tmp_path, rotation):
    pdf = tmp_path / "spread.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=400)
        text(page, 50, 100, "HIDDEN OPPOSING PAGE")
        text(page, 350, 100, "VISIBLE TECHNICAL TEXT")
        page.set_cropbox(fitz.Rect(300, 0, 600, 400))
        page.set_rotation(rotation)
        doc.save(pdf)
    extracted = displayed_page_text(pdf, 1)
    assert "VISIBLE" in extracted and "HIDDEN" not in extracted
    assert "HIDDEN" not in _source_pages(pdf, tmp_path / "cache")[0]


def test_repeated_lines_and_joined_hyphens_keep_distinct_source_geometry(tmp_path):
    pdf = tmp_path / "source-lines.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        text(page, 60, 60, "1. INTRODUCTION", font="tibo")
        text(page, 60, 100, "1.1. A mono-")
        text(page, 60, 114, "energetic source.")
        text(page, 60, 128, "Repeated observation.")
        text(page, 60, 142, "Repeated observation.")
        doc.save(pdf)
    record = next(r for r in parse(pdf) if r.element_id == "1.1")
    assert "monoenergetic" in record.text
    assert len(record.extra["source_spans"]) == 4
    assert len({tuple(s["bbox"]) for s in record.extra["source_spans"]}) == 4


def test_heading_style_supports_nesting_without_overriding_explicit_parents(tmp_path):
    pdf = tmp_path / "hierarchy.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        text(page, 60, 60, "1. INTRODUCTION", font="tibo")
        text(page, 60, 100, "1.1. Introductory material.")
        text(page, 60, 150, "ASSESSMENT METHODS")
        text(page, 60, 190, "Narrative approach", font="tiit")
        text(page, 60, 230, "1.2. A description of this approach.")
        doc.save(pdf)
    record = next(r for r in parse(pdf) if r.element_id == "1.2")
    assert record.section_path == ["1. INTRODUCTION", "ASSESSMENT METHODS", "Narrative approach"]
    config = {
        "subheading_parents": {"1. INTRODUCTION": {"Narrative approach": ["REVIEWED PARENT"]}}
    }
    record = next(r for r in parse(pdf, config) if r.element_id == "1.2")
    assert record.section_path == ["1. INTRODUCTION", "REVIEWED PARENT", "Narrative approach"]
    config["subheading_parents"]["1. INTRODUCTION"]["Narrative approach"] = []
    record = next(r for r in parse(pdf, config) if r.element_id == "1.2")
    assert record.section_path == ["1. INTRODUCTION", "Narrative approach"]


def test_repeated_figure_label_cannot_claim_both_diagrams(tmp_path):
    pdf = tmp_path / "ambiguous-figures.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        for left in [50, 350]:
            page.draw_rect(fitz.Rect(left, 100, left + 200, 300))
            text(page, left + 20, 150, "DIAGRAM LABEL")
            text(page, left, 320, "FIG. 1. Repeated caption")
        doc.save(pdf)
    figures = [r for r in parse(pdf) if r.element_type == "figure"]
    assert len(figures) == 2
    assert all(
        any(n.startswith("Unresolved figure ownership") for n in r.parser_notes) for r in figures
    )
    assert all(not r.extra.get("visual_text_regions") for r in figures)
    assert all(
        sum(n.startswith("Unresolved figure ownership") for n in r.parser_notes) == 1
        for r in figures
    )
