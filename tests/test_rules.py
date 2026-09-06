from iaea_guidance_parser.rules import (
    ANNEX_HEADING_RE,
    ANNEX_PARA_RE,
    APPENDIX_PARA_RE,
    BODY_PARA_RE,
    FIGURE_RE,
    TABLE_RE,
    is_all_caps_heading,
    match_figure_label,
    match_paragraph_label,
    match_table_label,
    normalize_text,
    remove_pdf_line_breaks,
)


def test_paragraph_patterns():
    assert BODY_PARA_RE.match("1.1. Nuclear security seeks to prevent")
    assert BODY_PARA_RE.match("3.3.15. Defence in depth")
    assert BODY_PARA_RE.match("3.3.15. Defence in depth").group("id") == "3.3.15"
    assert BODY_PARA_RE.match("101. These Regulations establish standards of safety")
    assert BODY_PARA_RE.match("101.1. Radiation and radioactive substances are natural")
    assert BODY_PARA_RE.match("220A. Additional transport provision")
    assert BODY_PARA_RE.match("220A.1. Nested transport provision")
    assert APPENDIX_PARA_RE.match("A.64. The operator should consider")
    assert APPENDIX_PARA_RE.match("A.16.3. The analysis should include").group("id") == "A.16.3"
    assert ANNEX_PARA_RE.match("III–21. As noted in para. III–13")
    assert ANNEX_PARA_RE.match("III-21. As noted in para. III-13")
    assert ANNEX_PARA_RE.match("III.21. As noted in para. III.13")


def test_normalize_text_removes_non_printing_pdf_control_bytes():
    assert normalize_text("IAEAL\x0814–00939") == "IAEAL 14–00939"


def test_structural_labels():
    assert FIGURE_RE.match("FIG. III–1. Physical and logical boundary zone requirements")
    assert TABLE_RE.match("TABLE III–1. LIST OF SYSTEMS: EXAMPLE")
    assert TABLE_RE.match("TABLE II-3. TRANSPORT INDEX LIMITS")
    assert TABLE_RE.match("TABLE 7A: EXCEPTED PACKAGES")
    assert TABLE_RE.match("TABLE 4.1. MULTILEVEL CAPTION")
    assert FIGURE_RE.match("FIG. 4. A NUMERIC FIGURE")
    assert FIGURE_RE.match("FIG. A−2: AN APPENDIX FIGURE")
    assert FIGURE_RE.match("FIG. I–1(a). Sample format for a performance test plan")
    assert ANNEX_HEADING_RE.match("Annex")
    assert ANNEX_HEADING_RE.match("Annex III")


def test_equations_are_not_all_caps_headings():
    assert not is_all_caps_heading("HRC = IE1 * S1 + IE2 * S2")
    assert not is_all_caps_heading("P P P P")


def test_structural_label_matching_preserves_source_style_and_canonicalizes_dashes():
    paragraph = match_paragraph_label("III-21. Annex text")
    table = match_table_label("TABLE II-3. Limits")
    figure = match_figure_label("FIG. A−2: Flow")

    assert paragraph and paragraph.canonical_id == "III–21"
    assert paragraph.raw_label == "III-21."
    assert table and table.canonical_id == "II–3"
    assert table.raw_label == "TABLE II-3."
    assert table.style == "roman_dash_period"
    assert figure and figure.canonical_id == "A–2"
    assert figure.raw_label == "FIG. A−2:"
    assert figure.style == "appendix_dash_colon"


def test_remove_pdf_line_breaks_joins_wrapped_prose_but_preserves_structural_starts():
    lines = remove_pdf_line_breaks(
        [
            "1.1. The operating organization should estab-",
            "lish arrangements for safety.",
            "1.2. A new paragraph starts here.",
            "TABLE 1. IMPORTANT VALUES",
            "Row A",
        ]
    )
    assert lines == [
        "1.1. The operating organization should establish arrangements for safety.",
        "1.2. A new paragraph starts here.",
        "TABLE 1. IMPORTANT VALUES",
        "Row A",
    ]

    numbered_prose = remove_pdf_line_breaks(
        [
            "The measures described in Sections",
            "4 and 5 should be implemented.",
            "5 The categorization system is described in the cited reference.",
        ]
    )
    assert numbered_prose == [
        "The measures described in Sections 4 and 5 should be implemented.",
        "5 The categorization system is described in the cited reference.",
    ]
