from __future__ import annotations

from iaea_guidance_parser.metadata import infer_metadata
from iaea_guidance_parser.models import DocumentMetadata, PageText, StructuralElement
from iaea_guidance_parser.parser import IAEAGuidanceParser
from iaea_guidance_parser.qa import run_records_qa


def _metadata(
    document_id: str = "GSR-PART-2", document_type: str = "general_safety_requirements"
) -> DocumentMetadata:
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


def _parse(
    lines: list[str],
    *,
    metadata: DocumentMetadata | None = None,
    parser_config: dict | None = None,
    bold_lines: list[str] | None = None,
) -> list[StructuralElement]:
    parser = IAEAGuidanceParser(
        metadata or _metadata(),
        [
            PageText(
                pdf_page=1,
                printed_page="1",
                text="\n".join(lines),
                lines=lines,
                bold_lines=bold_lines or [],
                typographic_heading_lines=bold_lines or [],
            )
        ],
        include_text_blocks=True,
        parser_config=parser_config,
    )
    _, records = parser.parse()
    return records


def _records_by_type(
    records: list[StructuralElement], element_type: str
) -> dict[str | None, StructuralElement]:
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


def test_bold_requirement_statement_remains_part_of_requirement_record():
    statement = "Managers shall demonstrate leadership for safety and commitment to safety."
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. LEADERSHIP FOR SAFETY",
            "Requirement 2: Demonstration of leadership for safety by managers",
            statement,
            "2.1. Senior managers should communicate expectations.",
        ],
        bold_lines=[statement],
    )

    requirement = _records_by_type(records, "requirement")["2"]
    assert statement in requirement.text
    assert not [
        record
        for record in records
        if record.element_type == "heading" and record.text == statement
    ]


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
    assert (
        paragraphs["9.37"].text
        == "The operating organization shall maintain administrative controls."
    )
    assert requirements["58"].text.startswith(
        "Requirement 58: Training, retraining and qualification of personnel"
    )
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


def test_multiline_footnote_is_kept_together_without_spurious_headings():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. The publication was jointly sponsored by several organizations.",
            "1 FOOD AND AGRICULTURE ORGANIZATION OF THE UNITED NATIONS,",
            "INTERNATIONAL",
            "ATOMIC",
            "ENERGY",
            "AGENCY,",
            "WORLD HEALTH ORGANIZATION, Preparedness and Response.",
            "1.2. The next substantive paragraph starts here.",
        ]
    )

    footnote = next(record for record in records if record.element_type == "footnote")
    headings = [record.text for record in records if record.element_type == "heading"]
    assert "INTERNATIONAL ATOMIC ENERGY AGENCY" in footnote.text
    assert "INTERNATIONAL" not in headings
    assert _records_by_type(records, "paragraph")["1.2"].text.startswith("The next substantive")


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
        if record.element_type == "paragraph"
        and record.text in {"Administrative information", "Structural analysis", "Thermal analysis"}
    ]
    assert len(tables) == 1
    assert "Structural analysis" in tables[0].text
    assert not fake_paragraphs
    assert any(
        record.element_id == "III–1" for record in records if record.element_type == "paragraph"
    )


def test_substantive_numbered_paragraph_ends_active_table():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "5. REGULATORY PROGRAMME",
            "TABLE 3. SECURITY LEVELS",
            "Security level A Security level B Security level C",
            "STEP 2: DETERMINE THE APPLICABLE SECURITY LEVEL",
            "5.11. If security levels are used, the regulator should specify the required performance for each level.",
            "5.12. The approach should be applied to all radioactive material.",
        ],
        metadata=_metadata("NSS-11-G-REV1", "implementing_guides"),
    )

    table = next(record for record in records if record.element_type == "table")
    paragraphs = _records_by_type(records, "paragraph")
    assert "STEP 2" not in table.text
    assert "5.11" not in table.text
    assert paragraphs["5.11"].text.startswith("If security levels are used")
    assert paragraphs["5.12"].text.startswith("The approach should be applied")


def test_multilevel_paragraph_ids_are_not_truncated_into_text():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "3. CONTENT AND STRUCTURE",
            "3.3.15. Defence in depth should be demonstrated.",
            "Appendix",
            "A.16.3. The analysis should identify initiating events.",
        ]
    )

    paragraphs = _records_by_type(records, "paragraph")
    assert paragraphs["3.3.15"].text == "Defence in depth should be demonstrated."
    assert paragraphs["A.16.3"].text == "The analysis should identify initiating events."


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
        if record.element_type == "paragraph"
        and record.text in {"Introduction", "Project implementation"}
    ]
    assert table.source_region == "Annex"
    assert "3.1.4." in table.text
    assert "Project implementation" in table.text
    assert not fake_paragraphs
    contributors = next(
        record for record in records if record.text == "CONTRIBUTORS TO DRAFTING AND REVIEW"
    )
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
    assert tables[1].section_path == [
        "Annex III",
        "STRUCTURE OF THE PACKAGE DESIGN SAFETY REPORT FOR APPENDICES I-VI",
    ]
    assert "CONTRIBUTORS TO DRAFTING AND REVIEW" not in tables[1].text
    contributors = next(
        record for record in records if record.text == "CONTRIBUTORS TO DRAFTING AND REVIEW"
    )
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
    assert any(
        record.text == "2. SAFETY ASSESSMENT IN THE AUTHORIZATION PROCESS" for record in headings
    )
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

    assert not [
        record for record in records if record.source_region == "Body" and ". . ." in record.text
    ]
    assert _records_by_type(records, "paragraph")["2.1"].section_path == ["2. RESPONSIBILITIES"]


def test_contents_continuation_page_is_ignored_even_with_false_printed_page():
    pages = [
        PageText(
            pdf_page=1,
            printed_page=None,
            text="CONTENTS\n1. INTRODUCTION . . . . . . . . 1",
            lines=["CONTENTS", "1. INTRODUCTION . . . . . . . . 1"],
        ),
        PageText(
            pdf_page=2,
            printed_page="33",
            text="4.4.1. General . . . . . . . . 17\n6.4.3. Equipment . . . . . . . . 28",
            lines=["4.4.1. General . . . . . . . . 17", "6.4.3. Equipment . . . . . . . . 28"],
        ),
        PageText(
            pdf_page=3,
            printed_page="1",
            text="Section I\nINTRODUCTION\n101.1. Actual body paragraph.",
            lines=["Section I", "INTRODUCTION", "101.1. Actual body paragraph."],
        ),
    ]
    parser = IAEAGuidanceParser(_metadata(), pages, include_text_blocks=True)
    _, records = parser.parse()

    paragraphs = _records_by_type(records, "paragraph")
    assert set(paragraphs) == {"101.1"}
    assert paragraphs["101.1"].text == "Actual body paragraph."


def test_contents_label_inside_body_does_not_restart_toc_suppression():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Actual body paragraph.",
            "2. SOURCE PACKAGE",
            "CONTENTS",
            "2.1. This paragraph follows a diagram label.",
            "2.2. Parsing continues normally.",
        ]
    )

    paragraphs = _records_by_type(records, "paragraph")
    assert {"1.1", "2.1", "2.2"} <= set(paragraphs)


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
        bold_lines=["Deterministic effects"],
    )

    paragraph = _records_by_type(records, "paragraph")["2.1"]
    headings = [record for record in records if record.element_type == "heading"]
    assert "Deterministic effects" not in paragraph.text
    assert any(record.text == "Deterministic effects" for record in headings)


def test_mixed_case_bold_heading_terminates_a_long_paragraph():
    heading = "Submission of the application for authorization"
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "3. AUTHORIZATION",
            "3.21. The regulatory body should review the application and supporting information",
            heading,
            "3.22. The applicant should submit the completed package.",
        ],
        bold_lines=[heading],
    )

    paragraph = _records_by_type(records, "paragraph")["3.21"]
    assert heading not in paragraph.text
    assert any(record.element_type == "heading" and record.text == heading for record in records)


def test_mixed_case_bold_heading_terminates_an_active_table():
    heading = "Packagings intended to be used for a single transport"
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "4. AGEING MANAGEMENT",
            "TABLE 1. PACKAGE TYPES",
            "Type A Repeated use",
            heading,
            "4.10. A wide range of packagings are designed for a single transport.",
        ],
        bold_lines=[heading],
    )

    table = _records_by_type(records, "table")["TABLE 1"]
    assert heading not in table.text
    assert any(record.element_type == "heading" and record.text == heading for record in records)


def test_incomplete_paragraph_resumes_after_table_and_figure():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "4. TRUSTWORTHINESS",
            "4.7. These are the individuals",
            "TABLE 1. EXAMPLE OF A GRADED APPROACH",
            "Identity verification x x x",
            "FIG. 2. Nuclear facility layout.",
            "for whom the most rigorous trustworthiness assessments should be conducted.",
            "4.8. The competent authority should document the approach.",
        ]
    )

    paragraph = _records_by_type(records, "paragraph")["4.7"]
    assert paragraph.text == (
        "These are the individuals for whom the most rigorous trustworthiness assessments "
        "should be conducted."
    )
    assert paragraph.page_end_pdf == 1
    assert "Paragraph resumed after an intervening table or figure." in paragraph.parser_notes


def test_incomplete_paragraph_resumes_on_page_after_inserted_table():
    metadata = _metadata("NSS-48-T", "technical_guidance")
    pages = [
        PageText(
            pdf_page=1,
            printed_page="56",
            text="",
            lines=[
                "1. INTRODUCTION",
                "1.1. Context paragraph.",
                "Annex I",
                "I–3. The event to be represented in this",
                "TABLE I–1. EXAMPLE SABOTAGE SCENARIO",
                "Action Location",
                "Disable C1 L1",
            ],
        ),
        PageText(
            pdf_page=2,
            printed_page="57",
            text="",
            lines=[
                "logic equation is a release in excess of high radiological consequences.",
                "I–4. The next annex paragraph begins here.",
            ],
        ),
    ]
    parser = IAEAGuidanceParser(metadata, pages, include_text_blocks=True)

    _, records = parser.parse()

    paragraph = _records_by_type(records, "paragraph")["I–3"]
    table = _records_by_type(records, "table")["TABLE I–1"]
    assert paragraph.text.endswith(
        "logic equation is a release in excess of high radiological consequences."
    )
    assert paragraph.page_end_pdf == 2
    assert table.page_end_pdf == 1


def test_qa_does_not_treat_attached_footnote_markers_as_footnote_bodies():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "3. PROGRAMME",
            "3.8. The competent authority should oversee the programme.1 The designated authority should issue guidance.",
        ]
    )

    findings = run_records_qa(records, manifest_doc_ids={"GSR-PART-2"})

    assert not [finding for finding in findings if finding.check == "footnote_contamination"]


def test_filename_series_number_prevents_long_title_document_id(tmp_path):
    pdf = (
        tmp_path
        / "SSG-4 (Rev. 1) Development and Application of Level 2 Probabilistic Safety Assessment for Nuclear Power Plants.pdf"
    )
    pdf.write_bytes(b"not a real pdf")
    page = PageText(
        pdf_page=1,
        printed_page=None,
        text="DEVELOPMENT AND APPLICATION OF LEVEL 2 PROBABILISTIC SAFETY ASSESSMENT FOR NUCLEAR POWER PLANTS",
        lines=[
            "DEVELOPMENT AND APPLICATION OF LEVEL 2 PROBABILISTIC SAFETY ASSESSMENT FOR NUCLEAR POWER PLANTS"
        ],
    )

    metadata = infer_metadata(
        pdf,
        [page],
        {
            "document": {
                "series_name": "IAEA Safety Standards Series",
                "document_domain": "nuclear_safety",
            }
        },
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
    bad_findings = run_records_qa(
        [bad_record], manifest_doc_ids={"GSR-PART-2"}, manifest_counts={"GSR-PART-2": 1}
    )
    assert any(finding.check == "requirement_boundary" for finding in bad_findings)


def test_qa_accepts_legacy_three_digit_requirements_and_editorial_ellipses():
    records = _parse(
        [
            "1. INTRODUCTION",
            "101. These Regulations establish safety requirements.",
            "102. The package shall be designed to remain safe when material is not dispersed... even during handling.",
        ],
        metadata=_metadata("SSR-6-REV2", "specific_safety_requirements"),
    )
    # Exercise the document-level heuristic, which only runs on larger inputs.
    records = records * 16
    findings = run_records_qa(records, manifest_doc_ids={"SSR-6-REV2"})

    assert not [finding for finding in findings if finding.check == "requirement_boundary"]
    assert not [finding for finding in findings if finding.check == "toc_pollution"]


def test_qa_reports_a_broken_section_path_once_not_for_every_record():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. PROTECTION OF",
            "2.1. First affected paragraph.",
            "2.2. Second affected paragraph.",
        ]
    )
    findings = run_records_qa(records, manifest_doc_ids={"GSR-PART-2"})

    path_findings = [
        finding
        for finding in findings
        if finding.check == "heading_split" and "Section path contains" in finding.reason
    ]
    assert len(path_findings) == 1


def test_qa_reports_known_unresolved_substitution_font_cipher():
    records = _parse(["9'0)%6 7)'96-8= -779)7 6)0%8-2+"])

    findings = run_records_qa(records, manifest_doc_ids={"GSR-PART-2"})

    assert any(finding.check == "font_substitution_cipher" for finding in findings)


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


def test_reviewed_unterminated_labels_are_page_and_region_scoped():
    config = {
        "label_exceptions": [
            {
                "element_type": "paragraph",
                "pdf_page": 1,
                "region": "Body",
                "raw_label": "4.4",
                "canonical_id": "4.4",
            }
        ]
    }
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "4. PREVENTIVE MEASURES",
            "4.4 This source paragraph omits its final label punctuation.",
            "4.5 This unreviewed label is ordinary continuation text.",
            "4.6. A conventionally punctuated paragraph.",
        ],
        parser_config=config,
    )
    paragraphs = _records_by_type(records, "paragraph")

    assert paragraphs["4.4"].extra["label_style"] == "configured_exception"
    assert "4.5 This unreviewed label" in paragraphs["4.4"].text
    assert paragraphs["4.6"].text == "A conventionally punctuated paragraph."


def test_schedule_number_restarts_are_attached_to_local_parent_scope():
    config = {
        "local_scope_patterns": [
            {"pattern": r"^SCHEDULE FOR UN (?P<scope>\d{4}):", "id_template": "UN {scope}"}
        ]
    }
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "SCHEDULE FOR UN 2915: RADIOACTIVE MATERIAL, TYPE A PACKAGE",
            "7.1. First schedule provision.",
            "SCHEDULE FOR UN 3332: RADIOACTIVE MATERIAL, TYPE A PACKAGE",
            "7.1. Second schedule provision.",
        ],
        parser_config=config,
    )
    schedule_paragraphs = [
        record
        for record in records
        if record.element_type == "paragraph" and record.element_id == "7.1"
    ]

    assert [record.parent_element_id for record in schedule_paragraphs] == ["UN 2915", "UN 3332"]
    assert [record.extra["local_scope"] for record in schedule_paragraphs] == ["UN 2915", "UN 3332"]


def test_configured_outline_is_one_table_instead_of_fake_paragraphs():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "Annex II",
            "1.0 Definitions",
            "1.1 A deliberately long outline entry that should remain inside the specimen even when it resembles substantive prose and exceeds the usual table-cell heuristic.",
            "1.2 Scope",
            "II.18. A genuine annex paragraph after the outline.",
        ],
        parser_config={
            "outline_regions": [
                {
                    "pdf_pages": [1],
                    "start_pattern": r"^1\.0\s+Definitions$",
                    "element_id": "MOU OUTLINE 1",
                    "title": "Memorandum outline",
                }
            ]
        },
    )
    table = next(record for record in records if record.element_id == "MOU OUTLINE 1")
    annex_paragraph = next(record for record in records if record.element_id == "II.18")

    assert "1.1 A deliberately long outline entry" in table.text
    assert annex_paragraph.text == "A genuine annex paragraph after the outline."


def test_three_line_heading_merge_records_original_fragments():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Context paragraph.",
            "2. CONTROL OF NUCLEAR",
            "AND OTHER RADIOACTIVE MATERIAL",
            "OUT OF REGULATORY CONTROL",
            "2.1. Technical content.",
        ]
    )
    heading = next(record for record in records if record.text.startswith("2. CONTROL OF NUCLEAR"))

    assert (
        heading.text
        == "2. CONTROL OF NUCLEAR AND OTHER RADIOACTIVE MATERIAL OUT OF REGULATORY CONTROL"
    )
    assert heading.extra["source_fragments"] == [
        "2. CONTROL OF NUCLEAR",
        "AND OTHER RADIOACTIVE MATERIAL",
        "OUT OF REGULATORY CONTROL",
    ]


def test_inline_figure_reference_is_not_a_caption_without_reviewed_exception():
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. FIG. 1 shows the management framework used in this publication.",
            "2. FRAMEWORK",
            "2.1. Technical content.",
        ]
    )

    assert not [record for record in records if record.element_type == "figure"]
    assert "FIG. 1 shows" in _records_by_type(records, "paragraph")["1.1"].text


def test_scoped_table_notes_do_not_enter_an_interrupted_paragraph():
    pages = [
        PageText(
            pdf_page=1,
            printed_page="1",
            text="",
            lines=[
                "Appendix II",
                "II.19. The criteria are conservative for many",
                "TABLE 8. DEFAULT VALUES",
                "Value A: 10",
                "Value B: 20",
            ],
        ),
        PageText(
            pdf_page=2,
            printed_page="2",
            text="",
            lines=[
                "Note: The OILs should be revised after measurement.",
                "a A note associated with a table value.",
                "b A second table note.",
            ],
        ),
        PageText(
            pdf_page=3,
            printed_page="3",
            text="",
            lines=[
                "radionuclides and should be revised when further information becomes available.",
                "II.20. The next paragraph begins here.",
            ],
        ),
    ]
    _, records = IAEAGuidanceParser(
        _metadata(),
        pages,
        parser_config={
            "outline_regions": [
                {
                    "pdf_pages": [2],
                    "start_pattern": r"^Note:\s+The OILs",
                    "element_id": "TABLE 8",
                    "title": "Notes to TABLE 8",
                }
            ]
        },
    ).parse()
    paragraph = next(r for r in records if r.element_id == "II.19")
    notes = next(r for r in records if r.element_type == "table" and r.page_start_pdf == 2)
    assert (
        paragraph.text
        == "The criteria are conservative for many radionuclides and should be revised when further information becomes available."
    )
    assert paragraph.page_start_pdf == 1 and paragraph.page_end_pdf == 3
    assert notes.element_id == "TABLE 8"
    assert "a A note" in notes.text and "b A second table note" in notes.text


def test_reviewed_lettered_list_resumes_below_table_note_on_same_page():
    pages = [
        PageText(
            pdf_page=1,
            printed_page="1",
            text="",
            lines=[
                "Annex XVII",
                "XVII–15. Analyse the actions as follows:",
                "(a) Evaluate the first action individually.",
            ],
        ),
        PageText(
            pdf_page=2,
            printed_page="2",
            text="",
            lines=[
                "TABLE XVII–5. EXAMPLE",
                "Row 1: moderate",
                "Note: Using expert judgement, assign a score.",
                "This additional line belongs only to the table note.",
                "(b)",
                "Evaluate the next action.",
                "(c)",
                "Record the resulting system",
            ],
        ),
        PageText(
            pdf_page=3,
            printed_page="3",
            text="",
            lines=[
                "effectiveness and explain the result.",
                "XVII–16. A separate paragraph.",
            ],
        ),
    ]
    config = {
        "lettered_list_continuation_pages": [2],
        "outline_regions": [
            {
                "pdf_pages": [2],
                "start_pattern": r"^Note:\s+Using expert judgement",
                "element_id": "TABLE XVII–5",
                "title": "Notes to TABLE XVII–5",
            }
        ],
    }
    _, records = IAEAGuidanceParser(_metadata(), pages, parser_config=config).parse()
    paragraph = next(r for r in records if r.element_id == "XVII–15")
    note = next(r for r in records if r.title == "Notes to TABLE XVII–5")
    assert paragraph.text == (
        "Analyse the actions as follows: (a) Evaluate the first action individually. "
        "(b) Evaluate the next action. (c) Record the resulting system "
        "effectiveness and explain the result."
    )
    assert (paragraph.page_start_pdf, paragraph.page_end_pdf) == (1, 3)
    assert " ".join(note.text.split()) == (
        "Note: Using expert judgement, assign a score. "
        "This additional line belongs only to the table note."
    )
    assert next(r for r in records if r.element_id == "XVII–16").text == "A separate paragraph."


def test_lettered_list_continuation_is_scoped_and_requires_next_label():
    for enabled, continuation, should_resume in [
        (False, "(b) Second action.", False),
        (True, "(b) Second action.", True),
        (True, "(a) A new list.", False),
        (True, "(d) A skipped label.", False),
        (True, "For another purpose.", False),
    ]:
        pages = [
            PageText(
                pdf_page=1,
                printed_page="1",
                text="",
                lines=[
                    "2. EXAMPLE",
                    "2.1. Steps: (a) First action.",
                ],
            ),
            PageText(
                pdf_page=2,
                printed_page="2",
                text="",
                lines=[
                    "FIG. 1. Example diagram.",
                    continuation,
                    "2.2. Next paragraph.",
                ],
            ),
        ]
        config = {"lettered_list_continuation_pages": [2] if enabled else [3]}
        _, records = IAEAGuidanceParser(_metadata(), pages, parser_config=config).parse()
        paragraph = next(r for r in records if r.element_id == "2.1")
        assert (continuation in paragraph.text) == should_resume


def test_configured_heading_continuation_requires_exact_lines_and_page():
    lines = [
        "3. ESSENTIAL ELEMENTS",
        "ESSENTIAL ELEMENT 12: SUSTAINING A NUCLEAR SECURITY",
        "REGIME",
        "3.12. A regime contributes to sustainability.",
    ]
    joined = " ".join(lines[1:3])
    for rule_page, fragments, expected in [
        (1, lines[1:3], True),
        (2, lines[1:3], False),
        (1, [lines[1], "OTHER"], False),
    ]:
        records = _parse(
            ["1. INTRODUCTION", "1.1. Introduction.", *lines],
            parser_config={
                "heading_continuations": [{"pdf_pages": [rule_page], "lines": fragments}]
            },
        )
        assert any(r.element_type == "heading" and r.text == joined for r in records) == expected
        paragraph = next(r for r in records if r.element_id == "3.12")
        assert (joined in paragraph.section_path) == expected


def test_configured_mixed_case_heading_separates_a_principle_from_its_explanation():
    title = "Principle 1: Responsibility for safety"
    for enabled in (False, True):
        records = _parse(
            [
                "1. INTRODUCTION",
                "1.1. Introduction.",
                "3. SAFETY PRINCIPLES",
                "3.2. The preceding paragraph ends here.",
                title,
                "The prime responsibility for safety must rest with the operator.",
                "3.3. The explanation follows.",
            ],
            parser_config={
                "heading_continuations": [{"pdf_pages": [1 if enabled else 2], "lines": [title]}]
            },
        )
        prior = next(r for r in records if r.element_id == "3.2")
        following = next(r for r in records if r.element_id == "3.3")
        assert (title in prior.text) != enabled
        assert (title in following.section_path) == enabled
        if enabled:
            statement = next(r for r in records if r.element_type == "text_block")
            assert statement.text.startswith("The prime responsibility")
            assert statement.section_path == ["3. SAFETY PRINCIPLES", title]
            assert statement.text_status == "Normative"


def test_reviewed_publisher_citation_is_a_footnote_not_page_furniture():
    records = _parse(
        [
            "FOREWORD",
            "A source publication was issued in 19931.",
            "1 INTERNATIONAL ATOMIC ENERGY AGENCY, The Safety of Nuclear",
            "Installations, Safety Series No. 110, IAEA, Vienna (1993).",
        ],
        parser_config={
            "footnote_anchors": [
                {"pdf_pages": [1], "footnote_id": "1", "element_id": None, "text": "19931"}
            ]
        },
    )
    note = next(r for r in records if r.element_type == "footnote")
    assert note.text.startswith("INTERNATIONAL ATOMIC ENERGY AGENCY")
    assert note.text.endswith("Vienna (1993).")
    assert note.extra["source_anchor"]["text"] == "19931"
    assert note.section_path == ["FOREWORD"]


def test_configured_backmatter_title_remains_the_parent_of_committee_headings():
    title = "BODIES FOR THE ENDORSEMENT OF IAEA SAFETY STANDARDS"
    records = _parse(
        [
            "1. INTRODUCTION",
            "1.1. Introduction.",
            title,
            "Commission on Safety Standards",
            "Country: Example Person.",
        ],
        parser_config={
            "page_regions": {1: {"region": "BackMatter", "section": title}},
            "heading_continuations": [
                {"pdf_pages": [1], "lines": ["Commission on Safety Standards"]}
            ],
        },
    )
    roster = next(r for r in records if r.text == "Country: Example Person.")
    assert roster.section_path == [title, "Commission on Safety Standards"]
    assert roster.source_region == "BackMatter"


def test_glossary_boundaries_preserve_terms_continuations_and_footnote_links():
    pages = [
        PageText(1, "1", "", ["1. INTRODUCTION", "1.1. Ordinary prose."]),
        PageText(
            2,
            "2",
            "",
            [
                "DEFINITIONS",
                "This section defines the terms used in the publication.",
                "radioactive material. Material covered by the current",
            ],
        ),
        PageText(
            3,
            "3",
            "",
            [
                "International Basic Safety Standards2.",
                "regulatory body. An authority that oversees facilities.",
                "Its mandate concerns",
                "radioactive material. This mention does not start a new definition.",
                "2 At the time of publication, the cited edition was current.",
            ],
        ),
        PageText(4, None, "", []),
        PageText(5, None, "", ["Orders may be sent to the publisher at 39 Alexandra Road."]),
    ]
    config = {
        "page_regions": {2: "Glossary", 5: "BackMatter"},
        "footnote_pages": [3],
        "glossary_terms": {
            "radioactive material": "radioactive material. Material covered by the current",
            "regulatory body": "regulatory body. An authority that oversees facilities.",
        },
        "footnote_anchors": [
            {
                "pdf_pages": [3],
                "footnote_id": "2",
                "element_id": "radioactive material",
                "text": "International Basic Safety Standards2.",
            }
        ],
    }
    _, records = IAEAGuidanceParser(_metadata(), pages, parser_config=config).parse()
    definition = next(r for r in records if r.element_id == "radioactive material")
    assert (
        definition.text
        == "radioactive material. Material covered by the current International Basic Safety Standards2."
    )
    assert definition.source_region == "Glossary"
    assert definition.text_status == "Informational"
    assert (definition.page_start_pdf, definition.page_end_pdf) == (2, 3)
    assert (definition.page_start_printed, definition.page_end_printed) == ("2", "3")
    assert next(r for r in records if r.element_id == "regulatory body").page_start_pdf == 3
    assert len([r for r in records if r.element_id == "radioactive material"]) == 1
    assert "This mention" in next(r for r in records if r.element_id == "regulatory body").text
    footnote = next(r for r in records if r.element_type == "footnote")
    assert footnote.linked_from_element_id == definition.element_id
    assert footnote.extra["source_anchor"] == config["footnote_anchors"][0]
    assert footnote.source_region == "Glossary"
    assert len([r for r in records if r.element_type == "footnote"]) == 1
    assert records.index(
        next(r for r in records if r.element_id == "regulatory body")
    ) < records.index(footnote)
    ordering = next(r for r in records if "Orders may" in r.text)
    assert "39 Alexandra Road" in ordering.text
    assert ordering.source_region == "BackMatter"
    assert ordering.page_start_pdf == ordering.page_end_pdf == 5
    assert not any(r.page_start_pdf < 5 <= r.page_end_pdf for r in records)


def test_footer_notes_follow_complete_prose_without_breaking_cross_page_continuation():
    pages = [
        PageText(1, "1", "", ["1. INTRODUCTION", "1.1. The scope1 includes", "1 The first note."]),
        PageText(2, "2", "", ["material2 and facilities.", "2 The second note."]),
        PageText(3, "3", "", ["1.2. The next paragraph."]),
    ]
    _, records = IAEAGuidanceParser(_metadata(), pages).parse()
    prose_and_notes = [r for r in records if r.element_type != "heading"]
    assert [r.element_id for r in prose_and_notes] == ["1.1", "1", "2", "1.2"]
    paragraph, first_note, second_note, _ = prose_and_notes
    assert paragraph.text == "The scope1 includes material2 and facilities."
    assert (paragraph.page_start_pdf, paragraph.page_end_pdf) == (1, 2)
    assert (first_note.page_start_pdf, second_note.page_start_pdf) == (1, 2)
    assert first_note.linked_from_element_id == second_note.linked_from_element_id == "1.1"
    assert first_note.text == "The first note."
    assert second_note.text == "The second note."


def test_footnote_anchor_override_is_bound_to_its_label_and_page():
    lines = [
        "1. INTRODUCTION",
        "1.1. An anchored statement.1",
        "1.2. A later paragraph.",
        "1 A note that belongs to the first paragraph.",
    ]
    for page, label, expected in [(1, "1", "1.1"), (2, "1", "1.2"), (1, "2", "1.2")]:
        records = _parse(
            lines,
            parser_config={
                "footnote_anchors": [
                    {
                        "pdf_pages": [page],
                        "footnote_id": label,
                        "element_id": "1.1",
                        "text": "An anchored statement.1",
                    }
                ]
            },
        )
        footnote = next(r for r in records if r.element_type == "footnote")
        assert footnote.linked_from_element_id == expected


def test_footnote_anchor_retains_its_section_after_a_later_heading():
    path = ["1. INTRODUCTION", "SCOPE"]
    records = _parse(
        [
            "1. INTRODUCTION",
            "SCOPE",
            "1.1. A statement with a note.1",
            "OBJECTIVE",
            "1.2. Another statement in the next subsection.",
            "1 A note belonging to the scope statement.",
        ],
        parser_config={
            "footnote_anchors": [
                {"pdf_pages": [1], "footnote_id": "1", "element_id": "1.1", "section_path": path}
            ]
        },
    )
    footnote = next(r for r in records if r.element_type == "footnote")
    assert footnote.linked_from_element_id == "1.1"
    assert footnote.section_path == path
    later = next(r for r in records if r.element_id == "1.2")
    assert later.section_path == ["1. INTRODUCTION", "OBJECTIVE"]


def test_reviewed_url_note_after_references_keeps_its_body_association():
    lines = [
        "1. INTRODUCTION",
        "1.1. Consult the database2.",
        "REFERENCES",
        "[1] A publication citation, Vienna (2019).",
        "2 https://example.org/database",
    ]
    for page, label in [(1, "2"), (2, "2"), (1, "3")]:
        records = _parse(
            lines,
            parser_config={
                "footnote_anchors": [
                    {
                        "pdf_pages": [page],
                        "footnote_id": label,
                        "element_id": "1.1",
                        "section_path": ["1. INTRODUCTION"],
                        "source_region": "Body",
                    }
                ]
            },
        )
        reference = next(r for r in records if r.element_type == "reference")
        notes = [r for r in records if r.element_type == "footnote"]
        if (page, label) == (1, "2"):
            assert reference.text == "A publication citation, Vienna (2019)."
            assert len(notes) == 1
            note = notes[0]
            assert note.text == "https://example.org/database"
            assert note.element_id == "2" and note.linked_from_element_id == "1.1"
            assert note.source_region == "Body"
            assert note.section_path == ["1. INTRODUCTION"]
            assert note.page_start_pdf == note.page_end_pdf == 1
            assert records.index(reference) < records.index(note)
        else:
            assert not notes
            assert reference.text.endswith("2 https://example.org/database")


def test_reviewed_subheading_parents_preserve_nested_paths_within_their_section():
    records = _parse(
        [
            "1. INTRODUCTION",
            "GENERAL RECOMMENDATIONS",
            "General context.",
            "SECURITY SYSTEM",
            "Systems address detection and delay.",
            "DETECTION",
            "1.1. Detection measures.",
            "DELAY",
            "1.2. Delay measures.",
            "OTHER RECOMMENDATIONS",
            "1.3. Another subsection.",
            "2. NEXT SECTION",
            "DETECTION",
            "2.1. A different use of the same heading.",
        ],
        parser_config={
            "subheading_parents": {
                "1. INTRODUCTION": {
                    "SECURITY SYSTEM": ["GENERAL RECOMMENDATIONS"],
                    "DETECTION": ["GENERAL RECOMMENDATIONS", "SECURITY SYSTEM"],
                    "DELAY": ["GENERAL RECOMMENDATIONS", "SECURITY SYSTEM"],
                }
            }
        },
    )
    paths = {r.element_id: r.section_path for r in records if r.element_type == "paragraph"}
    assert paths["1.1"] == [
        "1. INTRODUCTION",
        "GENERAL RECOMMENDATIONS",
        "SECURITY SYSTEM",
        "DETECTION",
    ]
    assert paths["1.2"][-3:] == ["GENERAL RECOMMENDATIONS", "SECURITY SYSTEM", "DELAY"]
    assert paths["1.3"] == ["1. INTRODUCTION", "OTHER RECOMMENDATIONS"]
    assert paths["2.1"] == ["2. NEXT SECTION", "DETECTION"]


def test_repeated_subheading_uses_its_reviewed_parent_on_each_source_page():
    major = "4. PROTECTION REQUIREMENTS"
    pages = [
        PageText(
            1,
            "1",
            "",
            [
                "1. INTRODUCTION",
                "1.1. Context.",
                major,
                "Requirements for the State",
                "4.1. The first task.",
            ],
        ),
        PageText(2, "2", "", ["Requirements for the State", "4.2. The later task."]),
    ]
    config = {
        "heading_continuations": [{"pdf_pages": [1, 2], "lines": ["Requirements for the State"]}],
        "subheading_parents": {
            major: {"Requirements for the State": {1: ["PREVENTION"], 2: ["RESPONSE"]}}
        },
    }
    _, records = IAEAGuidanceParser(_metadata(), pages, parser_config=config).parse()
    assert next(r for r in records if r.element_id == "4.1").section_path == [
        major,
        "PREVENTION",
        "Requirements for the State",
    ]
    assert next(r for r in records if r.element_id == "4.2").section_path == [
        major,
        "RESPONSE",
        "Requirements for the State",
    ]
