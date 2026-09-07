from iaea_guidance_parser.metadata import deep_merge, infer_metadata
from iaea_guidance_parser.models import PageText


def test_deep_merge_nested_config():
    base = {
        "document": {"series_name": "A", "document_domain": "x"},
        "parser": {"include_text_blocks": True},
    }
    override = {"document": {"document_domain": "y"}}
    merged = deep_merge(base, override)
    assert merged["document"]["series_name"] == "A"
    assert merged["document"]["document_domain"] == "y"
    assert base["document"]["document_domain"] == "x"


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
        {
            "document": {
                "series_name": "IAEA Nuclear Security Series",
                "document_domain": "nuclear_security",
            }
        },
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
        {
            "document": {
                "series_name": "IAEA Nuclear Security Series",
                "document_domain": "nuclear_security",
            }
        },
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
    specific_requirements = infer_metadata(
        specific_requirements_pdf, [specific_requirements_page], {}
    )
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
        {
            "document": {
                "series_name": "IAEA Nuclear Security Series",
                "document_domain": "nuclear_security",
            }
        },
    )
    assert (
        security_metadata.title
        == "Nuclear Security Recommendations On Nuclear And Other Radioactive Material Out Of Regulatory Control"
    )

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
        {
            "document": {
                "series_name": "IAEA Safety Standards Series",
                "document_domain": "nuclear_safety",
            }
        },
    )
    assert safety_metadata.title == "Governmental, Legal And Regulatory Framework For Safety"

    inferred_safety_metadata = infer_metadata(
        safety_pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text="IAEA SAFETY STANDARDS AND RELATED PUBLICATIONS\nSecurity related publications are issued in the IAEA Nuclear Security Series.",
                lines=[
                    "IAEA SAFETY STANDARDS AND RELATED PUBLICATIONS",
                    "Security related publications are issued in the IAEA Nuclear Security Series.",
                ],
            )
        ],
        {},
    )
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
    nss_pdf = (
        tmp_path / "NSS 46-T Security of Nuclear and Other Radioactive Material in Transport.pdf"
    )
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
        {
            "document": {
                "series_name": "IAEA Nuclear Security Series",
                "document_domain": "nuclear_security",
            }
        },
    )
    assert nss_metadata.title == "Security Of Nuclear And Other Radioactive Material In Transport"

    safety_pdf = (
        tmp_path / "GS-G-3.1 Application of the Management System for Facilities and Activities.pdf"
    )
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
        {
            "document": {
                "series_name": "IAEA Safety Standards Series",
                "document_domain": "nuclear_safety",
            }
        },
    )
    assert (
        safety_metadata.title
        == "Application Of The Management System For Facilities And Activities"
    )


def test_title_inference_uses_descriptive_filename_for_incompatible_short_gibberish(tmp_path):
    pdf = (
        tmp_path
        / "NSS 3 Monitoring for Radioactive Material in International Mail Transported by Public Postal Operators.pdf"
    )
    pdf.write_bytes(b"not a real pdf")
    pages = [
        PageText(
            pdf_page=1,
            printed_page=None,
            text="JOTUSVNFOUT DPOTFOTVT",
            lines=["JOTUSVNFOUT DPOTFOTVT"],
        )
    ]

    metadata = infer_metadata(pdf, pages, {})

    assert (
        metadata.title
        == "Monitoring For Radioactive Material In International Mail Transported By Public Postal Operators"
    )
    assert metadata.metadata_source["title"] == "filename_replacing_incompatible_inference"


def test_title_inference_uses_compatible_filename_to_restore_omitted_title_words(tmp_path):
    pdf = (
        tmp_path
        / "SSG-26 Advisory Material for the IAEA Regulations for the Safe Transport of Radioactive Material (2018 Edition).pdf"
    )
    pdf.write_bytes(b"not a real pdf")
    pages = [
        PageText(
            pdf_page=1,
            printed_page=None,
            text="ADVISORY MATERIAL FOR THE SAFE TRANSPORT OF RADIOACTIVE MATERIAL",
            lines=["ADVISORY MATERIAL FOR THE SAFE TRANSPORT OF RADIOACTIVE MATERIAL"],
        )
    ]

    metadata = infer_metadata(pdf, pages, {})

    assert (
        metadata.title
        == "Advisory Material For The Iaea Regulations For The Safe Transport Of Radioactive Material (2018 Edition)"
    )


def test_title_inference_excludes_cover_category_and_subtype_labels(tmp_path):
    pdf = tmp_path / "NSS 5 Identification of Radioactive Sources and Devices.pdf"
    pdf.write_bytes(b"not a real pdf")
    pages = [
        PageText(
            pdf_page=1,
            printed_page=None,
            text="IDENTIFICATION OF\nRADIOACTIVE SOURCES AND\nDEVICES\nREFERENCE MANUAL\nTECHNICAL GUIDANCE",
            lines=[
                "IDENTIFICATION OF",
                "RADIOACTIVE SOURCES AND",
                "DEVICES",
                "REFERENCE MANUAL",
                "TECHNICAL GUIDANCE",
            ],
        )
    ]

    metadata = infer_metadata(pdf, pages, {})

    assert metadata.title == "Identification Of Radioactive Sources And Devices"

    single_line_pdf = tmp_path / "NSS 7 Nuclear Security Culture.pdf"
    single_line_pdf.write_bytes(b"not a real pdf")
    single_line_metadata = infer_metadata(
        single_line_pdf,
        [
            PageText(
                pdf_page=1,
                printed_page=None,
                text="NUCLEAR SECURITY CULTURE\nIMPLEMENTING GUIDE",
                lines=["NUCLEAR SECURITY CULTURE", "IMPLEMENTING GUIDE"],
            )
        ],
        {},
    )
    assert single_line_metadata.title == "Nuclear Security Culture"


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
        {
            "document": {
                "series_name": "IAEA Safety Standards Series",
                "document_domain": "nuclear_safety",
            }
        },
    )
    assert metadata.title == "Storage Of Radioactive Waste"
    assert metadata.metadata_source["title"] == "filename"


def test_computer_security_title_special_case_is_not_overbroad(tmp_path):
    pdf = (
        tmp_path
        / "NSS 33-T Computer Security of Instrumentation and Control Systems at Nuclear Facilities.pdf"
    )
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
        {
            "document": {
                "series_name": "IAEA Nuclear Security Series",
                "document_domain": "nuclear_security",
            }
        },
    )
    assert (
        metadata.title
        == "Computer Security Of Instrumentation And Control Systems At Nuclear Facilities"
    )
