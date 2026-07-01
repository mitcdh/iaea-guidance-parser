from types import SimpleNamespace

from iaea_guidance_parser.exporters import write_series_markdown_knowledge_parts
from iaea_guidance_parser.models import PageText
from iaea_guidance_parser.models import DocumentMetadata, StructuralElement
from iaea_guidance_parser.metadata import deep_merge, infer_metadata
from iaea_guidance_parser.parser import IAEAGuidanceParser, classify_status
from iaea_guidance_parser.rules import (
    ANNEX_HEADING_RE,
    ANNEX_PARA_RE,
    APPENDIX_PARA_RE,
    BODY_PARA_RE,
    FIGURE_RE,
    TABLE_RE,
    remove_pdf_line_breaks,
)
from iaea_guidance_parser.series import safe_path_component


def test_paragraph_patterns():
    assert BODY_PARA_RE.match("1.1. Nuclear security seeks to prevent")
    assert BODY_PARA_RE.match("101. These Regulations establish standards of safety")
    assert BODY_PARA_RE.match("101.1. Radiation and radioactive substances are natural")
    assert APPENDIX_PARA_RE.match("A.64. The operator should consider")
    assert ANNEX_PARA_RE.match("III–21. As noted in para. III–13")


def test_structural_labels():
    assert FIGURE_RE.match("FIG. III–1. Physical and logical boundary zone requirements")
    assert TABLE_RE.match("TABLE III–1. LIST OF SYSTEMS: EXAMPLE")
    assert ANNEX_HEADING_RE.match("Annex")
    assert ANNEX_HEADING_RE.match("Annex III")

def test_deep_merge_nested_config():
    base = {"document": {"series_name": "A", "document_domain": "x"}, "parser": {"include_text_blocks": True}}
    override = {"document": {"document_domain": "y"}}
    merged = deep_merge(base, override)
    assert merged["document"]["series_name"] == "A"
    assert merged["document"]["document_domain"] == "y"
    assert base["document"]["document_domain"] == "x"


def test_safe_path_component():
    assert safe_path_component("NSS 17-T (Rev. 1)") == "NSS-17-T-Rev.-1"


def test_safety_metadata_inference(tmp_path):
    pdf = tmp_path / "SSG-64.pdf"
    pdf.write_bytes(b"not a real pdf")
    page = PageText(
        pdf_page=1,
        printed_page=None,
        text="\n".join(
            [
                "IAEA Safety Standards Series No. SSG-64",
                "Specific Safety Guide",
                "PROTECTION AGAINST INTERNAL HAZARDS",
                "VIENNA, 2021",
                "STI/PUB/1920",
            ]
        ),
        lines=[],
    )
    metadata = infer_metadata(pdf, [page], {})
    assert metadata.series_name == "IAEA Safety Standards Series"
    assert metadata.series_number == "No. SSG–64"
    assert metadata.document_id == "SSG-64"
    assert metadata.document_domain == "nuclear_safety"
    assert metadata.document_category == "Specific Safety Guide"
    assert metadata.document_type == "specific_safety_guide"


def test_safety_requirements_and_fundamentals_type_inference(tmp_path):
    requirements_pdf = tmp_path / "GSR Part 4 Safety Assessment for Facilities and Activities.pdf"
    requirements_pdf.write_bytes(b"not a real pdf")
    requirements_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="\n".join(
            [
                "IAEA Safety Standards Series No. GSR Part 4 (Rev. 1)",
                "General Safety Requirements",
                "SAFETY ASSESSMENT FOR FACILITIES AND ACTIVITIES",
            ]
        ),
        lines=[],
    )
    requirements = infer_metadata(requirements_pdf, [requirements_page], {})
    assert requirements.document_category == "General Safety Requirements"
    assert requirements.document_type == "general_safety_requirements"

    fundamentals_pdf = tmp_path / "SF-1 Fundamental Safety Principles.pdf"
    fundamentals_pdf.write_bytes(b"not a real pdf")
    fundamentals_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="\n".join(
            [
                "IAEA Safety Standards Series No. SF-1",
                "Safety Fundamentals",
                "FUNDAMENTAL SAFETY PRINCIPLES",
            ]
        ),
        lines=[],
    )
    fundamentals = infer_metadata(fundamentals_pdf, [fundamentals_page], {})
    assert fundamentals.document_category == "Safety Fundamentals"
    assert fundamentals.document_type == "safety_fundamentals"


def test_nuclear_security_recommendations_type_inference(tmp_path):
    pdf = tmp_path / "NSS 13 Nuclear Security Recommendations on Physical Protection.pdf"
    pdf.write_bytes(b"not a real pdf")
    page = PageText(
        pdf_page=1,
        printed_page=None,
        text="\n".join(
            [
                "IAEA Nuclear Security Series No. 13",
                "NUCLEAR SECURITY RECOMMENDATIONS",
                "ON PHYSICAL PROTECTION OF NUCLEAR MATERIAL",
            ]
        ),
        lines=[],
    )
    metadata = infer_metadata(pdf, [page], {})
    assert metadata.document_category == "Nuclear Security Recommendations"
    assert metadata.document_type == "nuclear_security_recommendations"
    assert metadata.series_number == "No. 13"
    assert metadata.document_id == "NSS-13"


def test_nuclear_security_series_number_compaction_prevents_title_inflated_ids(tmp_path):
    revised_pdf = tmp_path / "NSS 12-T (Rev. 1) Model Academic Curriculum in Nuclear Security.pdf"
    revised_pdf.write_bytes(b"not a real pdf")
    revised_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="\n".join(
            [
                "IAEA Nuclear Security Series No. 12-T (Rev. 1) Technical Guidance",
                "MODEL ACADEMIC CURRICULUM IN NUCLEAR SECURITY",
            ]
        ),
        lines=[],
    )
    revised = infer_metadata(
        revised_pdf,
        [revised_page],
        {"document": {"series_name": "IAEA Nuclear Security Series", "document_domain": "nuclear_security"}},
    )
    assert revised.series_number == "No. 12–T (Rev. 1)"
    assert revised.document_id == "NSS-12-T-REV1"

    technical_pdf = tmp_path / "NSS 34-T Security of Nuclear Material in Transport.pdf"
    technical_pdf.write_bytes(b"not a real pdf")
    technical_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="IAEA Nuclear Security Series No. 34 –T Technical Guidance\nSECURITY OF NUCLEAR MATERIAL IN TRANSPORT",
        lines=[],
    )
    technical = infer_metadata(
        technical_pdf,
        [technical_page],
        {"document": {"series_name": "IAEA Nuclear Security Series", "document_domain": "nuclear_security"}},
    )
    assert technical.series_number == "No. 34–T"
    assert technical.document_id == "NSS-34-T"


def test_declared_and_prefix_safety_categories_are_preserved(tmp_path):
    specific_requirements_pdf = tmp_path / "SSR-2.1 Safety of Nuclear Power Plants Design.pdf"
    specific_requirements_pdf.write_bytes(b"not a real pdf")
    specific_requirements_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="IAEA Safety Standards Series No. SSR-2/1 (Rev. 1)\nSpecific Safety Requirements",
        lines=[],
    )
    specific_requirements = infer_metadata(specific_requirements_pdf, [specific_requirements_page], {})
    assert specific_requirements.document_category == "Specific Safety Requirements"
    assert specific_requirements.document_type == "specific_safety_requirements"

    general_guide_pdf = tmp_path / "GSG-12 Organization Management and Staffing.pdf"
    general_guide_pdf.write_bytes(b"not a real pdf")
    general_guide_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="IAEA Safety Standards Series No. GSG-12\nGeneral Safety Guide",
        lines=[],
    )
    general_guide = infer_metadata(general_guide_pdf, [general_guide_page], {})
    assert general_guide.document_category == "General Safety Guide"
    assert general_guide.document_type == "general_safety_guide"

    old_guide_pdf = tmp_path / "RS-G-1.9 Categorization of Radioactive Sources.pdf"
    old_guide_pdf.write_bytes(b"not a real pdf")
    old_guide_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="IAEA Safety Standards Series No. RS-G-1.9\nSafety Guide",
        lines=[],
    )
    old_guide = infer_metadata(old_guide_pdf, [old_guide_page], {})
    assert old_guide.document_category == "Safety Guide"
    assert old_guide.document_type == "safety_guide"


def test_title_inference_ignores_generic_front_matter_headings(tmp_path):
    security_pdf = tmp_path / "NSS 15.pdf"
    security_pdf.write_bytes(b"not a real pdf")
    security_metadata = infer_metadata(
        security_pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text="CATEGORIES IN THE IAEA NUCLEAR SECURITY SERIES\nDRAFTING AND REVIEW",
                lines=["CATEGORIES IN THE IAEA NUCLEAR SECURITY SERIES", "DRAFTING AND REVIEW"],
            ),
            PageText(
                pdf_page=2,
                printed_page=None,
                text="\n".join(
                    [
                        "NUCLEAR SECURITY",
                        "RECOMMENDATIONS",
                        "ON NUCLEAR AND OTHER",
                        "RADIOACTIVE MATERIAL",
                        "OUT OF REGULATORY CONTROL",
                    ]
                ),
                lines=[
                    "NUCLEAR SECURITY",
                    "RECOMMENDATIONS",
                    "ON NUCLEAR AND OTHER",
                    "RADIOACTIVE MATERIAL",
                    "OUT OF REGULATORY CONTROL",
                ],
            ),
        ],
        {"document": {"series_name": "IAEA Nuclear Security Series", "document_domain": "nuclear_security"}},
    )
    assert security_metadata.title == "Nuclear Security Recommendations On Nuclear And Other Radioactive Material Out Of Regulatory Control"

    safety_pdf = tmp_path / "GSR Part 1.pdf"
    safety_pdf.write_bytes(b"not a real pdf")
    safety_metadata = infer_metadata(
        safety_pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text="IAEA SAFETY STANDARDS AND RELATED PUBLICATIONS\nRELATED PUBLICATIONS\nSecurity related publications are issued in the IAEA Nuclear Security Series.",
                lines=[
                    "IAEA SAFETY STANDARDS AND RELATED PUBLICATIONS",
                    "RELATED PUBLICATIONS",
                    "Security related publications are issued in the IAEA Nuclear Security Series.",
                ],
            ),
            PageText(
                pdf_page=2,
                printed_page=None,
                text="GOVERNMENTAL, LEGAL\nAND REGULATORY\nFRAMEWORK FOR SAFETY",
                lines=["GOVERNMENTAL, LEGAL", "AND REGULATORY", "FRAMEWORK FOR SAFETY"],
            ),
        ],
        {"document": {"series_name": "IAEA Safety Standards Series", "document_domain": "nuclear_safety"}},
    )
    assert safety_metadata.title == "Governmental, Legal And Regulatory Framework For Safety"

    inferred_safety_metadata = infer_metadata(safety_pdf, [
        PageText(
            pdf_page=1,
            printed_page=None,
            text="IAEA SAFETY STANDARDS AND RELATED PUBLICATIONS\nSecurity related publications are issued in the IAEA Nuclear Security Series.",
            lines=[
                "IAEA SAFETY STANDARDS AND RELATED PUBLICATIONS",
                "Security related publications are issued in the IAEA Nuclear Security Series.",
            ],
        )
    ], {})
    assert inferred_safety_metadata.series_name == "IAEA Safety Standards Series"


def test_nuclear_security_suffix_type_inference(tmp_path):
    guidance_pdf = tmp_path / "NSS 46-T Security of Nuclear Material in Transport.pdf"
    guidance_pdf.write_bytes(b"not a real pdf")
    guidance_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="IAEA Nuclear Security Series No. 46-T\nSECURITY OF NUCLEAR MATERIAL IN TRANSPORT",
        lines=[],
    )
    guidance = infer_metadata(guidance_pdf, [guidance_page], {})
    assert guidance.document_category == "Technical Guidance"
    assert guidance.document_type == "technical_guidance"

    implementing_pdf = tmp_path / "NSS 35-G Security during the Lifetime of a Nuclear Facility.pdf"
    implementing_pdf.write_bytes(b"not a real pdf")
    implementing_page = PageText(
        pdf_page=1,
        printed_page=None,
        text="IAEA Nuclear Security Series No. 35-G\nSECURITY DURING THE LIFETIME OF A NUCLEAR FACILITY",
        lines=[],
    )
    implementing = infer_metadata(implementing_pdf, [implementing_page], {})
    assert implementing.document_category == "Implementing Guides"
    assert implementing.document_type == "implementing_guides"


def test_title_inference_skips_generic_category_page_and_garbled_cover(tmp_path):
    nss_pdf = tmp_path / "NSS 46-T Security of Nuclear and Other Radioactive Material in Transport.pdf"
    nss_pdf.write_bytes(b"not a real pdf")
    nss_metadata = infer_metadata(
        nss_pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text="\n".join(
                    [
                        "RECOMMENDATIONS",
                        "IMPLEMENTING GUIDE",
                        "NUCLEAR SECURITY FUNDAMENTALS",
                        "TECHNICAL GUIDANCE",
                    ]
                ),
                lines=[
                    "RECOMMENDATIONS",
                    "IMPLEMENTING GUIDE",
                    "NUCLEAR SECURITY FUNDAMENTALS",
                    "TECHNICAL GUIDANCE",
                ],
            ),
            PageText(
                pdf_page=3,
                printed_page=None,
                text="\n".join(
                    [
                        "SECURITY OF NUCLEAR AND",
                        "OTHER RADIOACTIVE MATERIAL",
                        "IN TRANSPORT",
                    ]
                ),
                lines=[
                    "SECURITY OF NUCLEAR AND",
                    "OTHER RADIOACTIVE MATERIAL",
                    "IN TRANSPORT",
                ],
            ),
        ],
        {"document": {"series_name": "IAEA Nuclear Security Series", "document_domain": "nuclear_security"}},
    )
    assert nss_metadata.title == "Security Of Nuclear And Other Radioactive Material In Transport"

    safety_pdf = tmp_path / "GS-G-3.1 Application of the Management System for Facilities and Activities.pdf"
    safety_pdf.write_bytes(b"not a real pdf")
    safety_metadata = infer_metadata(
        safety_pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text=",$($\x036Dihw\\\x036Wdqgdugv $SSOLFDWLRQ\x03RI",
                lines=[",$($\x036Dihw\\\x036Wdqgdugv", "$SSOLFDWLRQ\x03RI"],
            ),
            PageText(
                pdf_page=3,
                printed_page=None,
                text="\n".join(
                    [
                        "APPLICATION OF THE",
                        "MANAGEMENT SYSTEM FOR",
                        "FACILITIES AND ACTIVITIES",
                    ]
                ),
                lines=[
                    "APPLICATION OF THE",
                    "MANAGEMENT SYSTEM FOR",
                    "FACILITIES AND ACTIVITIES",
                ],
            ),
        ],
        {"document": {"series_name": "IAEA Safety Standards Series", "document_domain": "nuclear_safety"}},
    )
    assert safety_metadata.title == "Application Of The Management System For Facilities And Activities"


def test_title_inference_uses_filename_when_pdf_title_is_unusable(tmp_path):
    pdf = tmp_path / "WS-G-6.1 Storage of Radioactive Waste.pdf"
    pdf.write_bytes(b"not a real pdf")
    metadata = infer_metadata(
        pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text=",$($\x036Dihw\\\x036Wdqgdugv 6WRUDJH\x03RI\x03\x035DGLRDFWLYH\x03:DVWH",
                lines=[",$($\x036Dihw\\\x036Wdqgdugv", "6WRUDJH\x03RI\x03\x035DGLRDFWLYH\x03:DVWH"],
            )
        ],
        {"document": {"series_name": "IAEA Safety Standards Series", "document_domain": "nuclear_safety"}},
    )
    assert metadata.title == "Storage Of Radioactive Waste"
    assert metadata.metadata_source["title"] == "filename"


def test_computer_security_title_special_case_is_not_overbroad(tmp_path):
    pdf = tmp_path / "NSS 33-T Computer Security of Instrumentation and Control Systems at Nuclear Facilities.pdf"
    pdf.write_bytes(b"not a real pdf")
    metadata = infer_metadata(
        pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text="\n".join(
                    [
                        "COMPUTER SECURITY OF",
                        "INSTRUMENTATION AND CONTROL SYSTEMS",
                        "AT NUCLEAR FACILITIES",
                    ]
                ),
                lines=[
                    "COMPUTER SECURITY OF",
                    "INSTRUMENTATION AND CONTROL SYSTEMS",
                    "AT NUCLEAR FACILITIES",
                ],
            )
        ],
        {"document": {"series_name": "IAEA Nuclear Security Series", "document_domain": "nuclear_security"}},
    )
    assert metadata.title == "Computer Security Of Instrumentation And Control Systems At Nuclear Facilities"


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
    paragraphs = {record.element_id: record for record in records if record.element_type == "paragraph"}
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
        record for record in records if record.element_type == "paragraph" and record.element_id == "2.1" and record.page_start_pdf == 23
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
    paragraph = next(record for record in records if record.element_type == "paragraph" and record.element_id == "2.1")
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
    paragraphs = {record.element_id: record for record in records if record.element_type == "paragraph"}
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
    paragraphs = {record.element_id: record for record in records if record.element_type == "paragraph"}
    assert related_heading.source_region == "FrontMatter"
    assert paragraphs["2.1"].source_region == "Body"
    assert paragraphs["2.1"].text_status == "Normative"


def test_series_markdown_knowledge_parts_split_on_record_boundaries(tmp_path):
    metadata = DocumentMetadata(
        document_id="TEST-1",
        source_file="TEST-1.pdf",
        source_sha256="abc123",
        title="Test Publication",
        series_name="IAEA Test Series",
        series_number="No. TEST-1",
        document_family="IAEA Test Series",
        document_category="Specific Safety Guide",
        document_type="specific_safety_guide",
        document_domain="nuclear_safety",
    )
    records = [
        StructuralElement(
            record_id=f"TEST-1-{i}",
            document_id="TEST-1",
            document_title="Test Publication",
            document_family="IAEA Test Series",
            document_category="Specific Safety Guide",
            document_type="specific_safety_guide",
            document_domain="nuclear_safety",
            series_name="IAEA Test Series",
            series_number="No. TEST-1",
            element_type="paragraph",
            element_id=f"2.{i}",
            source_region="Body",
            text_status="Normative",
            status_reason="Body Section 2+ paragraph.",
            section_path=["2. TEST SECTION"],
            page_start_pdf=i,
            page_end_pdf=i,
            page_start_printed=None,
            page_end_printed=None,
            text="This is test guidance content. " + ("x" * 500),
        )
        for i in range(1, 6)
    ]
    result = SimpleNamespace(metadata=metadata, records=records)

    write_series_markdown_knowledge_parts(
        tmp_path,
        {"series": {"series_id": "Safety", "series_name": "IAEA Test Series"}},
        [result],
        max_bytes=2_000,
    )

    part_files = sorted(tmp_path.glob("part_*.md"))
    assert len(part_files) > 1
    assert (tmp_path / "README.md").exists()
    combined = "\n".join(path.read_text(encoding="utf-8") for path in part_files)
    assert "Upload all numbered parts for this series to the same Custom GPT." in combined
    assert "## Status and region legend" in combined
    assert "Document: TEST-1 (continued)" in combined
    assert "Status basis:" not in combined
    for record in records:
        assert f"record: paragraph {record.element_id}" in combined
