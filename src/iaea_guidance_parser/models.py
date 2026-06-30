from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class DocumentMetadata:
    document_id: str
    source_file: str
    source_sha256: str
    title: str = ""
    subtitle: str = ""
    publisher: str = "International Atomic Energy Agency"
    publication_year: Optional[int] = None
    publication_place: str = "Vienna"
    series_name: str = ""
    series_number: str = ""
    document_family: str = ""
    document_category: str = ""  # e.g. Technical Guidance
    document_type: str = ""      # e.g. technical_guidance
    document_domain: str = ""
    document_subdomain: str = ""
    sti_pub_number: str = ""
    isbn_pdf: str = ""
    language: str = "en"
    metadata_source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageText:
    pdf_page: int          # 1-based physical PDF page number
    printed_page: str | None
    text: str
    lines: list[str]


@dataclass
class StructuralElement:
    record_id: str
    document_id: str
    document_title: str
    document_family: str
    document_category: str
    document_type: str
    document_domain: str
    series_name: str
    series_number: str
    element_type: str       # paragraph, figure, table, footnote, heading, text_block, reference
    element_id: str | None
    source_region: str      # FrontMatter, Body, Appendix, References, Annex, Glossary, BackMatter
    text_status: str        # Normative, Informative, Informational
    status_reason: str
    section_path: list[str]
    page_start_pdf: int
    page_end_pdf: int
    page_start_printed: str | None
    page_end_printed: str | None
    text: str
    title: str | None = None
    caption: str | None = None
    parent_element_id: str | None = None
    linked_from_element_id: str | None = None
    confidence: str = "medium"
    parser_notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
