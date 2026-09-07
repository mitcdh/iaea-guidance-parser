import pytest

from iaea_guidance_parser.models import DocumentMetadata, PageText
from iaea_guidance_parser.parser import IAEAGuidanceParser, classify_status


def test_scientific_notation_survives_parsing_and_reading_export():
    from iaea_guidance_parser.exporters import _record_content_text

    text = "Activity: 1.2 × 10⁻³ Bq; ²³⁵U; H₂O; dose ≤ 10 μSv h⁻¹; x ≥ −0.5 ± 0.1; α/β ≠ γ."
    page = PageText(pdf_page=1, printed_page="1", text="", lines=["2. MEASUREMENT", f"2.1. {text}"])
    metadata = DocumentMetadata(
        document_id="NOTATION", source_file="fixture.pdf", source_sha256="0" * 64
    )
    _, records = IAEAGuidanceParser(metadata, [page]).parse()
    paragraph = next(r for r in records if r.element_type == "paragraph")
    assert paragraph.text == text
    assert _record_content_text(paragraph) == text


def test_status_classification_uses_spess_c_structure():
    section_one_status, section_one_reason = classify_status(
        element_type="paragraph",
        source_region="Body",
        element_id="1.1",
        section_path=["1. INTRODUCTION"],
    )
    assert section_one_status == "Informational"
    assert "Section 1" in section_one_reason
    assert "should not contain requirements" in section_one_reason

    body_status, body_reason = classify_status(
        element_type="paragraph",
        source_region="Body",
        element_id="2.1",
        section_path=["2. SECURITY MEASURES"],
    )
    assert body_status == "Normative"
    assert "primary technical content" in body_reason

    body_text_block_status, _ = classify_status(
        element_type="text_block",
        source_region="Body",
        section_path=["2. SECURITY MEASURES", "GENERAL"],
    )
    assert body_text_block_status == "Normative"

    appendix_status, appendix_reason = classify_status(
        element_type="paragraph",
        source_region="Appendix",
        element_id="A.1",
    )
    assert appendix_status == "Normative"
    assert "same status as the main text" in appendix_reason

    annex_status, annex_reason = classify_status(
        element_type="paragraph",
        source_region="Annex",
        element_id="I-1",
    )
    assert annex_status == "Informative"
    assert "not integral" in annex_reason


def test_parser_enters_body_without_printed_page_number():
    metadata = DocumentMetadata(
        document_id="NSS-15",
        source_file="NSS-15.pdf",
        source_sha256="abc123",
        title="Nuclear Security Recommendations",
        series_name="IAEA Nuclear Security Series",
        series_number="No. 15",
        document_family="IAEA Nuclear Security Series",
        document_category="Nuclear Security Recommendations",
        document_type="nuclear_security_recommendations",
        document_domain="nuclear_security",
    )
    parser = IAEAGuidanceParser(
        metadata,
        [
            PageText(
                pdf_page=11,
                printed_page=None,
                text="",
                lines=[
                    "1. INTRODUCTION",
                    "BACKGROUND",
                    "1.1. Introductory context.",
                    "2. OBJECTIVES",
                    "2.1. The State should establish nuclear security objectives.",
                ],
            )
        ],
        include_text_blocks=True,
    )

    _, records = parser.parse()
    paragraphs = {
        record.element_id: record for record in records if record.element_type == "paragraph"
    }
    assert paragraphs["1.1"].source_region == "Body"
    assert paragraphs["1.1"].text_status == "Informational"
    assert paragraphs["1.1"].section_path == ["1. INTRODUCTION", "BACKGROUND"]
    assert paragraphs["2.1"].source_region == "Body"
    assert paragraphs["2.1"].text_status == "Normative"
    assert paragraphs["2.1"].section_path == ["2. OBJECTIVES"]


def test_parser_keeps_safety_series_overview_before_introduction_in_front_matter():
    metadata = DocumentMetadata(
        document_id="GSR-PART-1-REV1",
        source_file="GSR.pdf",
        source_sha256="abc123",
        title="Governmental, Legal and Regulatory Framework for Safety",
        series_name="IAEA Safety Standards Series",
        series_number="No. GSR Part 1 (Rev. 1)",
        document_family="IAEA Safety Standards Series",
        document_category="General Safety Requirements",
        document_type="general_safety_requirements",
        document_domain="nuclear_safety",
    )
    parser = IAEAGuidanceParser(
        metadata,
        [
            PageText(
                pdf_page=11,
                printed_page=None,
                text="",
                lines=[
                    "1. Site Evaluation for Nuclear Installations",
                    "2. Safety of Nuclear Power Plants",
                    "2.1. Design and Construction",
                    "2.2. Commissioning and Operation",
                    "FIG. 1. The long term structure of the IAEA Safety Standards Series.",
                ],
            ),
            PageText(
                pdf_page=23,
                printed_page=None,
                text="",
                lines=[
                    "1. INTRODUCTION",
                    "BACKGROUND",
                    "1.1. Introductory safety context.",
                    "2. RESPONSIBILITIES AND FUNCTIONS OF THE GOVERNMENT",
                    "2.1. The government shall establish a national policy and strategy for safety.",
                ],
            ),
        ],
        include_text_blocks=True,
    )

    _, records = parser.parse()
    pre_intro_records = [record for record in records if record.page_start_pdf == 11]
    assert pre_intro_records
    assert {record.source_region for record in pre_intro_records} == {"FrontMatter"}
    assert {record.text_status for record in pre_intro_records} == {"Informational"}

    body_paragraph = next(
        record
        for record in records
        if record.element_type == "paragraph"
        and record.element_id == "2.1"
        and record.page_start_pdf == 23
    )
    assert body_paragraph.source_region == "Body"
    assert body_paragraph.text_status == "Normative"


def test_parser_ignores_contents_region_headings_before_body():
    metadata = DocumentMetadata(
        document_id="GSG-17",
        source_file="GSG-17.pdf",
        source_sha256="abc123",
        title="Application of the Concept of Exemption",
        series_name="IAEA Safety Standards Series",
        series_number="No. GSG-17",
        document_family="IAEA Safety Standards Series",
        document_category="General Safety Guide",
        document_type="general_safety_guide",
        document_domain="nuclear_safety",
    )
    parser = IAEAGuidanceParser(
        metadata,
        [
            PageText(
                pdf_page=15,
                printed_page=None,
                text="",
                lines=[
                    "CONTENTS",
                    "REFERENCES",
                    "ANNEX II",
                    "EXAMPLES OF DOSIMETRIC MODELS",
                ],
            ),
            PageText(
                pdf_page=17,
                printed_page="1",
                text="",
                lines=[
                    "1. INTRODUCTION",
                    "BACKGROUND",
                    "1.1. Introductory safety context.",
                    "2. THE CONCEPTS OF EXCLUSION, EXEMPTION",
                    "2.1. The regulatory body should apply the concept.",
                ],
            ),
        ],
        include_text_blocks=True,
    )

    _, records = parser.parse()
    contents_records = [record for record in records if record.page_start_pdf == 15]
    assert contents_records
    assert {record.source_region for record in contents_records} == {"FrontMatter"}
    paragraph = next(
        record
        for record in records
        if record.element_type == "paragraph" and record.element_id == "2.1"
    )
    assert paragraph.source_region == "Body"
    assert paragraph.text_status == "Normative"


def test_parser_enters_body_for_transport_style_numbering_after_contents():
    metadata = DocumentMetadata(
        document_id="SSR-6-REV2",
        source_file="SSR-6.pdf",
        source_sha256="abc123",
        title="Regulations for the Safe Transport of Radioactive Material",
        series_name="IAEA Safety Standards Series",
        series_number="No. SSR-6 (Rev. 2)",
        document_family="IAEA Safety Standards Series",
        document_category="Specific Safety Requirements",
        document_type="specific_safety_requirements",
        document_domain="nuclear_safety",
    )
    parser = IAEAGuidanceParser(
        metadata,
        [
            PageText(
                pdf_page=17,
                printed_page=None,
                text="",
                lines=[
                    "CONTENTS",
                    "SECTION I. INTRODUCTION 1 Background (101-103) . . . . . . . 1",
                ],
            ),
            PageText(
                pdf_page=21,
                printed_page="1",
                text="",
                lines=[
                    "INTRODUCTION",
                    "101. These Regulations establish standards of safety.",
                    "201. A1 shall mean the activity value of special form radioactive material.",
                ],
            ),
        ],
        include_text_blocks=True,
    )

    _, records = parser.parse()
    paragraphs = {
        record.element_id: record for record in records if record.element_type == "paragraph"
    }
    assert paragraphs["101"].source_region == "Body"
    assert paragraphs["101"].text_status == "Informational"
    assert paragraphs["201"].source_region == "Body"
    assert paragraphs["201"].text_status == "Normative"


def test_front_matter_related_publications_does_not_force_backmatter():
    metadata = DocumentMetadata(
        document_id="GSR-PART-1-REV1",
        source_file="GSR.pdf",
        source_sha256="abc123",
        title="Governmental, Legal and Regulatory Framework for Safety",
        series_name="IAEA Safety Standards Series",
        series_number="No. GSR Part 1 (Rev. 1)",
        document_family="IAEA Safety Standards Series",
        document_category="General Safety Requirements",
        document_type="general_safety_requirements",
        document_domain="nuclear_safety",
    )
    parser = IAEAGuidanceParser(
        metadata,
        [
            PageText(
                pdf_page=2,
                printed_page=None,
                text="",
                lines=[
                    "IAEA SAFETY STANDARDS",
                    "RELATED PUBLICATIONS",
                    "Safety related publications are also issued separately.",
                ],
            ),
            PageText(
                pdf_page=23,
                printed_page=None,
                text="",
                lines=[
                    "1. INTRODUCTION",
                    "1.1. Introductory safety context.",
                    "2. RESPONSIBILITIES AND FUNCTIONS OF THE GOVERNMENT",
                    "2.1. The government shall establish a national policy and strategy for safety.",
                ],
            ),
        ],
        include_text_blocks=True,
    )

    _, records = parser.parse()
    related_heading = next(record for record in records if record.text == "RELATED PUBLICATIONS")
    paragraphs = {
        record.element_id: record for record in records if record.element_type == "paragraph"
    }
    assert related_heading.source_region == "FrontMatter"
    assert paragraphs["2.1"].source_region == "Body"
    assert paragraphs["2.1"].text_status == "Normative"


def test_hyphenated_diagram_identifier_is_not_an_embedded_footnote():
    metadata = DocumentMetadata("DEMO", "demo.pdf", "source-hash")
    lines = ["2. DIAGRAM", "2.1. LH1-4 Analytical justified; LH2-4 Analytical justified."]
    page = PageText(1, "1", "\n".join(lines), lines)
    records = IAEAGuidanceParser(metadata, [page]).parse()[1]
    paragraph = next(record for record in records if record.element_id == "2.1")
    assert paragraph.text == "LH1-4 Analytical justified; LH2-4 Analytical justified."
    assert not any(record.element_type == "footnote" for record in records)


def test_suspended_compound_keeps_its_hyphen_and_word_boundary():
    metadata = DocumentMetadata(document_id="D", source_file="d.pdf", source_sha256="source")
    pages = [
        PageText(
            pdf_page=1,
            printed_page="1",
            text="",
            lines=[
                "2. COMMUNICATION",
                "2.1. Exchange pre‑ and post‑shipment notifications.",
                "2.2. Review pre‑",
                "and post‑shipment documents.",
                "2.3. Use short‑ or long‑term arrangements.",
            ],
        )
    ]
    _, records = IAEAGuidanceParser(metadata, pages).parse()
    paragraphs = {r.element_id: r.text for r in records if r.element_type == "paragraph"}
    assert "pre‑ and post‑shipment" in paragraphs["2.1"]
    assert "pre‑ and post‑shipment" in paragraphs["2.2"]
    assert "short‑ or long‑term" in paragraphs["2.3"]


def test_wrapped_url_keeps_literal_hyphen_without_inserting_a_space():
    from iaea_guidance_parser.rules import remove_pdf_line_breaks

    assert remove_pdf_line_breaks(
        ["See https://example.org/nuclear-safety-", "and-security-glossary for definitions."]
    ) == ["See https://example.org/nuclear-safety-and-security-glossary for definitions."]
    assert remove_pdf_line_breaks(["See https://example.org/source-", "document for details."]) == [
        "See https://example.org/source-document for details."
    ]


def test_reviewed_compound_keeps_its_hyphen_but_ordinary_wraps_still_join():
    metadata = DocumentMetadata("DEMO", "demo.pdf", "source-hash")
    lines = ["1. INTRODUCTION", "1.1. Consider off-", "site consequences and pro-", "tection."]
    page = PageText(1, "1", "\n".join(lines), lines)
    _, records = IAEAGuidanceParser(
        metadata, [page], parser_config={"hyphenated_words": ["off-site"]}
    ).parse()
    assert next(r.text for r in records if r.element_id == "1.1") == (
        "Consider off-site consequences and protection."
    )


@pytest.mark.parametrize("scope", ["Annex", "Appendix"])
def test_prefixed_references_keep_their_annex_or_appendix_context(scope):
    metadata = DocumentMetadata(
        document_id="DEMO", source_file="demo.pdf", source_sha256="abc", title="Demo"
    )
    pages = [
        PageText(
            pdf_page=1,
            printed_page="1",
            text="",
            lines=[
                "1. INTRODUCTION",
                "1.1. Context.",
                f"{scope} I",
                "I–1. See references below.",
                f"REFERENCES TO {scope.upper()} 1",
                "[I–1] INTERNATIONAL ATOMIC ENERGY AGENCY, First title (2018).",
                "[I–2] INTERNATIONAL ATOMIC ENERGY AGENCY, Second title (2019).",
                "[I–3] INTERNATIONAL ATOMIC",
                "ENERGY AGENCY,",
                "IAEA",
                "Safety Glossary: Terminology Used in Nuclear Safety (2019).",
            ],
        ),
        PageText(
            pdf_page=2,
            printed_page="2",
            text="",
            lines=[
                "[I–4] INTERNATIONAL ATOMIC ENERGY AGENCY, Fourth title (2019).",
                f"{scope} II",
                "II–1. Next section.",
            ],
        ),
    ]
    _, records = IAEAGuidanceParser(metadata, pages, include_text_blocks=True).parse()
    refs = [r for r in records if r.element_type == "reference"]
    assert [r.element_id for r in refs] == ["[I–1]", "[I–2]", "[I–3]", "[I–4]"]
    assert all(r.section_path == [f"{scope} I", f"REFERENCES TO {scope.upper()} 1"] for r in refs)
    assert all(r.source_region == "References" and r.text_status == "Informational" for r in refs)
    assert (
        refs[2].text
        == "INTERNATIONAL ATOMIC ENERGY AGENCY, IAEA Safety Glossary: Terminology Used in Nuclear Safety (2019)."
    )
    assert refs[-1].page_start_pdf == refs[-1].page_end_pdf == 2
    assert not any(
        r.element_type == "heading" and r.text.startswith(("[I", "ENERGY AGENCY", "IAEA"))
        for r in records
    )
    following = next(r for r in records if r.element_id == "II–1")
    assert following.section_path == [f"{scope} II"] and following.source_region == scope


@pytest.mark.parametrize("fragments", [["defence-in-", "depth."], ["defence-", "in-depth."]])
def test_reviewed_three_part_compound_survives_either_line_break(fragments):
    metadata = DocumentMetadata("DEMO", "demo.pdf", "source")
    lines = ["1. INTRODUCTION", "1.1. Preserve " + fragments[0], fragments[1]]
    page = PageText(1, "1", "\n".join(lines), lines)
    _, records = IAEAGuidanceParser(
        metadata, [page], parser_config={"hyphenated_words": ["defence-in-depth"]}
    ).parse()
    assert next(r.text for r in records if r.element_id == "1.1") == "Preserve defence-in-depth."
