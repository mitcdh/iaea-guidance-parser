from iaea_guidance_parser.models import PageText
from iaea_guidance_parser.metadata import deep_merge, infer_metadata
from iaea_guidance_parser.parser import classify_status
from iaea_guidance_parser.rules import (
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
    assert APPENDIX_PARA_RE.match("A.64. The operator should consider")
    assert ANNEX_PARA_RE.match("III–21. As noted in para. III–13")


def test_structural_labels():
    assert FIGURE_RE.match("FIG. III–1. Physical and logical boundary zone requirements")
    assert TABLE_RE.match("TABLE III–1. LIST OF SYSTEMS: EXAMPLE")

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
