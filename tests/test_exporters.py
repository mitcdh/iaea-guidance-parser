from types import SimpleNamespace

from iaea_guidance_parser.exporters import write_series_markdown_knowledge_parts
from iaea_guidance_parser.models import DocumentMetadata, StructuralElement


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
