from __future__ import annotations

from iaea_guidance_parser.metadata import infer_metadata
from iaea_guidance_parser.models import DocumentMetadata, PageText, StructuralElement
from iaea_guidance_parser.parser import IAEAGuidanceParser
from iaea_guidance_parser.qa import run_records_qa


def _metadata(document_id: str = "GSR-PART-2", document_type: str = "general_safety_requirements") -> DocumentMetadata:
    return DocumentMetadata(
        document_id=document_id,
        source_file=f"{document_id}.pdf",
        source_sha256="abc123",
        title="Safety Test Publication",
        series_name="IAEA Safety Standards Series",
        series_number=f"No. {document_id}",
        document_family="IAEA Safety Standards Series",
        document_category="General Safety Requirements",
        document_type=document_type,
        document_domain="nuclear_safety",
    )


def _parse(lines: list[str], *, metadata: DocumentMetadata | None = None) -> list[StructuralElement]:
    parser = IAEAGuidanceParser(
        metadata or _metadata(),
        [PageText(pdf_page=1, printed_page="1", text="\n".join(lines), lines=lines)],
        include_text_blocks=True,
    )
    _, records = parser.parse()
    return records


def _records_by_type(records: list[StructuralElement], element_type: str) -> dict[str | None, StructuralElement]:
    return {record.element_id: record for record in records if record.element_type == element_type}


def test_requirement_line_becomes_normative_requirement_record():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "4. MANAGEMENT OF THE SUPPLY CHAIN",
            "4.32. The organization shall specify items and services that may influence safety.",
            "Requirement 11: Management of the supply chain",
            "The organization shall put in place arrangements with vendors, contractors and suppliers.",
            "4.33. Procurement specifications shall be developed.",
        ]
    )

    paragraphs = _records_by_type(records, "paragraph")
    requirements = _records_by_type(records, "requirement")
    assert "Requirement 11" not in paragraphs["4.32"].text
    assert requirements["11"].text.startswith("Requirement 11: Management of the supply chain")
    assert "The organization shall put in place arrangements" in requirements["11"].text
    assert requirements["11"].text_status == "Normative"
    assert paragraphs["4.33"].text.startswith("Procurement specifications")


def test_midline_requirement_marker_is_split_from_prior_paragraph():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "9. OPERATING ORGANIZATION",
            "9.37. The operating organization shall maintain administrative controls. Requirement 58: Training, retraining and qualification of personnel The operating organization shall ensure that activities are performed by qualified persons.",
            "9.38. The operating organization shall ensure that personnel receive training.",
        ],
        metadata=_metadata("SSR-4", "specific_safety_requirements"),
    )

    paragraphs = _records_by_type(records, "paragraph")
    requirements = _records_by_type(records, "requirement")
    assert paragraphs["9.37"].text == "The operating organization shall maintain administrative controls."
    assert requirements["58"].text.startswith("Requirement 58: Training, retraining and qualification of personnel")
    assert requirements["58"].text_status == "Normative"


def test_embedded_footnote_bodies_are_extracted_from_paragraphs():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. EMERGENCY PREPAREDNESS",
            "2.1. The arrangements apply to all threat categories. 1 The threat categories are discussed in paras 2.19 and 2.20.",
            "2.4. The hazard assessment shall categorize facilities. 2 A dangerous source can give rise to severe deterministic effects. 3 A serious emergency can affect people. 4 This term is defined in Appendix III. with arrangements maintained.",
        ],
        metadata=_metadata("GS-G-21", "general_safety_guide"),
    )

    paragraphs = _records_by_type(records, "paragraph")
    footnotes = [record for record in records if record.element_type == "footnote"]
    assert "1 The threat categories" not in paragraphs["2.1"].text
    assert "2 A dangerous source" not in paragraphs["2.4"].text
    assert "with arrangements maintained" in paragraphs["2.4"].text
    assert {record.element_id for record in footnotes} == {"1", "2", "3", "4"}
    assert {record.text_status for record in footnotes} == {"Informative"}


def test_page_furniture_pairs_are_removed_before_paragraph_reconstruction():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. PACKAGE DESIGN",
            "2.2. Administrative information should include package details.",
            "86",
            "Appendix VI",
            "Additional package details should be recorded.",
            "2.3. The next paragraph starts here.",
        ],
        metadata=_metadata("SSG-66", "specific_safety_guide"),
    )

    paragraph = _records_by_type(records, "paragraph")["2.2"]
    assert "86 Appendix VI" not in paragraph.text
    assert "Additional package details" in paragraph.text


def test_table_cell_labels_are_not_emitted_as_fake_paragraphs():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "Annex III",
            "TABLE III-1. FORMAT AND CONTENT EXAMPLE",
            "APPENDIX I",
            "TABLE 1:",
            "1.1.",
            "Administrative information",
            "2.1.",
            "Structural analysis",
            "2.2.",
            "Thermal analysis",
            "Note: This table is illustrative.",
            "III-1. This is a genuine annex paragraph outside the table.",
        ],
        metadata=_metadata("SSG-66", "specific_safety_guide"),
    )

    tables = [record for record in records if record.element_type == "table"]
    fake_paragraphs = [
        record
        for record in records
        if record.element_type == "paragraph" and record.text in {"Administrative information", "Structural analysis", "Thermal analysis"}
    ]
    assert len(tables) == 1
    assert "Structural analysis" in tables[0].text
    assert not fake_paragraphs
    assert any(record.element_id == "III–1" for record in records if record.element_type == "paragraph")


def test_bare_annex_typical_contents_is_preserved_as_table_not_paragraphs():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "Annex",
            "TYPICAL TABLE OF CONTENTS OF A SAFETY ANALYSIS REPORT",
            "CHAPTER 1: Introduction and General Considerations",
            "1.1.",
            "Introduction",
            "1.2.",
            "Project implementation",
            "3.1.4.",
            "General design basis information",
            "CONTRIBUTORS TO DRAFTING AND REVIEW",
            "A. Example",
        ],
        metadata=_metadata("SSG-61", "specific_safety_guide"),
    )

    tables = [record for record in records if record.element_type == "table"]
    table = next(record for record in tables if record.element_id == "TYPICAL TABLE OF CONTENTS")
    fake_paragraphs = [
        record
        for record in records
        if record.element_type == "paragraph" and record.text in {"Introduction", "Project implementation"}
    ]
    assert table.source_region == "Annex"
    assert "3.1.4." in table.text
    assert "Project implementation" in table.text
    assert not fake_paragraphs
    contributors = next(record for record in records if record.text == "CONTRIBUTORS TO DRAFTING AND REVIEW")
    assert contributors.source_region == "BackMatter"
    assert "CONTRIBUTORS TO DRAFTING AND REVIEW" not in table.text


def test_module_outline_is_preserved_as_table_not_repeated_paragraphs():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "Annex I",
            "NS0. INTRODUCTION TO NUCLEAR SECURITY",
            "C.",
            "Module outline",
            "1.",
            "Introduction of nuclear security and physical protection",
            "1.1. Goals and objectives",
            "1.2. Basic definitions",
            "2.1. Concept and assessment of threat",
            "D.",
            "Exercises",
            "No exercises are assigned for this module.",
        ],
        metadata=_metadata("NSS-12-T-REV1", "technical_guidance"),
    )

    table = next(record for record in records if record.element_type == "table")
    fake_outline_paragraphs = [
        record
        for record in records
        if record.element_type == "paragraph"
        and record.element_id in {"1.1", "1.2", "2.1"}
        and record.section_path[:1] == ["Annex I"]
    ]
    assert table.element_id == "MODULE OUTLINE 0001"
    assert table.source_region == "Annex"
    assert "1.1. Goals and objectives" in table.text
    assert "2.1. Concept and assessment of threat" in table.text
    assert "D." not in table.text
    assert not fake_outline_paragraphs


def test_annex_following_outline_is_preserved_as_table_not_repeated_paragraphs():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "Annex II",
            "PROPOSED MODULES FOR A CERTIFICATE PROGRAMME CURRICULUM IN NUCLEAR SECURITY",
            "II-3. Given the factors outlined above, a notional certificate programme in nuclear security may be based on the following outline:",
            "1.",
            "Introduction to nuclear security",
            "1.1. Interface of nuclear security with safety and safeguards",
            "1.4. Management of nuclear security",
            "1.4.1. International and national stakeholder cooperation in nuclear security",
            "1.4.2. Human factor in nuclear security",
            "1.4.2.1. Nuclear security culture",
            "2.",
            "Protecting material, facilities and activities",
            "2.1. Threat and vulnerability assessment",
            "2.1.1. Design basis threat",
            "No. 26",
            "ORDERING LOCALLY",
        ],
        metadata=_metadata("NSS-12-T-REV1", "technical_guidance"),
    )

    paragraphs = _records_by_type(records, "paragraph")
    table = next(record for record in records if record.element_type == "table")
    fake_outline_paragraphs = [
        record
        for record in records
        if record.element_type == "paragraph" and record.element_id in {"1.4", "2.1"}
    ]
    assert paragraphs["II–3"].text.endswith("following outline:")
    assert "1. Introduction" not in paragraphs["II–3"].text
    assert table.element_id == "OUTLINE 0001"
    assert table.source_region == "Annex"
    assert "1.4.2.1. Nuclear security culture" in table.text
    assert "2.1. Threat and vulnerability assessment" in table.text
    assert "No. 26" not in table.text
    assert not fake_outline_paragraphs
    ordering = next(record for record in records if record.text == "ORDERING LOCALLY")
    assert ordering.source_region == "BackMatter"


def test_annex_transition_and_backmatter_close_active_tables():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "Annex II",
            "TABLE II-1. REFERENCE DOCUMENTS",
            "APPENDIX I",
            "TABLE 1:",
            "1.1.",
            "Administrative information",
            "Annex III",
            "STRUCTURE OF THE PACKAGE DESIGN SAFETY",
            "REPORT FOR APPENDICES I-VI",
            "TABLE III-1. STRUCTURE OF THE PACKAGE DESIGN SAFETY REPORT",
            "APPENDIX I",
            "TABLE 1:",
            "2.1.",
            "Structural analysis",
            "CONTRIBUTORS TO DRAFTING AND REVIEW",
            "A. Example",
        ],
        metadata=_metadata("SSG-66", "specific_safety_guide"),
    )

    tables = [record for record in records if record.element_type == "table"]
    assert [table.element_id for table in tables] == ["TABLE II–1", "TABLE III–1"]
    assert tables[0].section_path == ["Annex II"]
    assert tables[1].section_path == ["Annex III", "STRUCTURE OF THE PACKAGE DESIGN SAFETY REPORT FOR APPENDICES I-VI"]
    assert "CONTRIBUTORS TO DRAFTING AND REVIEW" not in tables[1].text
    contributors = next(record for record in records if record.text == "CONTRIBUTORS TO DRAFTING AND REVIEW")
    assert contributors.source_region == "BackMatter"


def test_wrapped_heading_is_merged_before_section_path_assignment():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. SAFETY ASSESSMENT IN THE",
            "AUTHORIZATION PROCESS",
            "2.1. The safety assessment should support authorization.",
        ],
        metadata=_metadata("SSG-20-REV1", "specific_safety_guide"),
    )

    headings = [record for record in records if record.element_type == "heading"]
    paragraph = _records_by_type(records, "paragraph")["2.1"]
    assert any(record.text == "2. SAFETY ASSESSMENT IN THE AUTHORIZATION PROCESS" for record in headings)
    assert paragraph.section_path == ["2. SAFETY ASSESSMENT IN THE AUTHORIZATION PROCESS"]


def test_toc_dot_leaders_do_not_become_body_records():
    records = _parse(
        [
            "CONTENTS",
            "1. INTRODUCTION . . . . . . . . . . . . . . . . . . . . . . 1",
            "2. RESPONSIBILITIES . . . . . . . . . . . . . . . . . . . . 4",
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. RESPONSIBILITIES",
            "2.1. The government shall establish arrangements.",
        ],
        metadata=_metadata("GSR-PART-2", "general_safety_requirements"),
    )

    assert not [record for record in records if record.source_region == "Body" and ". . ." in record.text]
    assert _records_by_type(records, "paragraph")["2.1"].section_path == ["2. RESPONSIBILITIES"]


def test_short_subheading_between_paragraphs_is_not_appended_to_prior_paragraph():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. RADIATION EFFECTS",
            "2.1. Exposure can cause health effects.",
            "Deterministic effects",
            "2.2. Severe deterministic effects are considered separately.",
        ],
        metadata=_metadata("GS-G-21", "general_safety_guide"),
    )

    paragraph = _records_by_type(records, "paragraph")["2.1"]
    headings = [record for record in records if record.element_type == "heading"]
    assert "Deterministic effects" not in paragraph.text
    assert any(record.text == "Deterministic effects" for record in headings)


def test_filename_series_number_prevents_long_title_document_id(tmp_path):
    pdf = tmp_path / "SSG-4 (Rev. 1) Development and Application of Level 2 Probabilistic Safety Assessment for Nuclear Power Plants.pdf"
    pdf.write_bytes(b"not a real pdf")
    page = PageText(
        pdf_page=1,
        printed_page=None,
        text="DEVELOPMENT AND APPLICATION OF LEVEL 2 PROBABILISTIC SAFETY ASSESSMENT FOR NUCLEAR POWER PLANTS",
        lines=["DEVELOPMENT AND APPLICATION OF LEVEL 2 PROBABILISTIC SAFETY ASSESSMENT FOR NUCLEAR POWER PLANTS"],
    )

    metadata = infer_metadata(
        pdf,
        [page],
        {"document": {"series_name": "IAEA Safety Standards Series", "document_domain": "nuclear_safety"}},
    )

    assert metadata.series_number == "No. SSG–4 (Rev. 1)"
    assert metadata.document_id == "SSG-4-REV1"


def test_qa_flags_embedded_requirement_and_produces_no_false_critical_for_clean_records():
    clean_records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. RESPONSIBILITIES",
            "Requirement 1: Responsibilities",
            "The government shall establish arrangements.",
            "2.1. The government shall maintain those arrangements.",
        ]
    )
    clean_findings = run_records_qa(
        clean_records,
        manifest_doc_ids={"GSR-PART-2"},
        manifest_counts={"GSR-PART-2": len(clean_records)},
    )
    assert not [finding for finding in clean_findings if finding.severity == "critical"]

    bad = clean_records[0]
    bad_record = StructuralElement(
        **{
            **bad.to_dict(),
            "record_id": "GSR-PART-2:paragraph:4.32:p1",
            "element_type": "paragraph",
            "element_id": "4.32",
            "source_region": "Body",
            "text_status": "Normative",
            "text": "Prior text. Requirement 11: Management of the supply chain The organization shall put in place arrangements.",
            "section_path": ["4. MANAGEMENT"],
        }
    )
    bad_findings = run_records_qa([bad_record], manifest_doc_ids={"GSR-PART-2"}, manifest_counts={"GSR-PART-2": 1})
    assert any(finding.check == "requirement_boundary" for finding in bad_findings)


def test_all_parser_records_have_stable_unique_record_ids():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. RESPONSIBILITIES",
            "2.1. The government shall establish arrangements.",
            "2.2. The regulatory body shall maintain competence.",
        ]
    )
    record_ids = [record.record_id for record in records]
    assert all(record_ids)
    assert len(record_ids) == len(set(record_ids))
