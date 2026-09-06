from iaea_guidance_parser.models import DocumentMetadata, PageText
from iaea_guidance_parser.parser import IAEAGuidanceParser, classify_status


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
