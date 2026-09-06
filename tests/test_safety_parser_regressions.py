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
