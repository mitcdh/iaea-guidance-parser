from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DocumentMetadata:
    document_id: str
    source_file: str
    source_sha256: str
    title: str = ""
    subtitle: str = ""
    publisher: str = "International Atomic Energy Agency"
    publication_year: int | None = None
    publication_place: str = "Vienna"
    series_name: str = ""
    series_number: str = ""
    document_family: str = ""
    document_category: str = ""  # e.g. Technical Guidance
    document_type: str = ""  # e.g. technical_guidance
    document_domain: str = ""
    document_subdomain: str = ""
    sti_pub_number: str = ""
    isbn_pdf: str = ""
    language: str = "en"
    metadata_source: dict[str, Any] = field(default_factory=dict)
    page_count: int = 0
    config_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageText:
    pdf_page: int  # 1-based physical PDF page number
    printed_page: str | None
    text: str
    lines: list[str]
    # Normalized source lines whose visible alphabetic content is predominantly
    # bold.  PDF extraction otherwise discards the one signal that reliably
    # distinguishes mixed-case subsection headings from body prose.
    bold_lines: list[str] = field(default_factory=list)
    # Bold lines that are also isolated from surrounding body text by visible
    # vertical spacing.  These are safe structural-heading candidates; bold
    # quotations and emphasized running text remain only in ``bold_lines``.
    typographic_heading_lines: list[str] = field(default_factory=list)
    # Text drawn inside a detected figure region is deliberately omitted from
    # prose/table parsing.  Captions remain in ``lines`` and become figure
    # records; this list makes the source-preserving omission auditable.
    suppressed_figure_lines: list[str] = field(default_factory=list)
    # Layout evidence is kept independently of the lines fed into the grammar.
    source_lines: list[dict[str, Any]] = field(default_factory=list)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    figure_regions: list[dict[str, Any]] = field(default_factory=list)
    current_source_spans: list[dict[str, Any]] = field(default_factory=list)
    # Rich PDF spans stay internal: exports retain the existing source-line geometry.
    source_typography: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    glossary_openings: dict[str, str] = field(default_factory=dict)
    figure_regions_by_caption: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    figure_warnings: dict[str, list[str]] = field(default_factory=dict)


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
    element_type: str  # paragraph, figure, table, footnote, heading, text_block, reference
    element_id: str | None
    source_region: str  # FrontMatter, Body, Appendix, References, Annex, Glossary, BackMatter
    text_status: str  # Normative, Informative, Informational
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
