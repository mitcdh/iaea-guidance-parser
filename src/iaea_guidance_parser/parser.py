from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any, Iterator

from .metadata import infer_metadata, load_config
from .models import DocumentMetadata, PageText, StructuralElement
from .pdf_extract import extract_pages
from .rules import (
    ANNEX_HEADING_RE,
    ANNEX_PARA_RE,
    APPENDIX_HEADING_RE,
    APPENDIX_PARA_RE,
    BODY_PARA_RE,
    CONTENTS_HEADING_RE,
    FIGURE_RE,
    FOOTNOTE_RE,
    GLOSSARY_HEADING_RE,
    KNOWN_PUBLICATION_HEADINGS,
    MAJOR_BODY_HEADING_RE,
    REFERENCE_ITEM_RE,
    REFERENCES_HEADING_RE,
    RELATED_PUBLICATIONS_RE,
    TABLE_CONT_RE,
    TABLE_RE,
    canonical_dash,
    is_all_caps_heading,
    normalize_text,
)


class IAEAGuidanceParser:
    def __init__(self, metadata: DocumentMetadata, pages: list[PageText], include_text_blocks: bool = True):
        self.meta = metadata
        self.pages = pages
        self.include_text_blocks = include_text_blocks
        self.records: list[StructuralElement] = []
        self.counter = itertools.count(1)
        self.region = "FrontMatter"
        self.current_major_heading: str | None = None
        self.current_subheading: str | None = None
        self.current_annex: str | None = None
        self.current_para_id: str | None = None

    @classmethod
    def from_pdf(cls, pdf_path: Path, config_path: Path | None = None) -> "IAEAGuidanceParser":
        config = load_config(config_path)
        return cls.from_pdf_config(pdf_path, config)

    @classmethod
    def from_pdf_config(cls, pdf_path: Path, config: dict[str, Any] | None = None) -> "IAEAGuidanceParser":
        """Create a parser for one PDF using an already-loaded config dictionary."""
        pages = extract_pages(pdf_path)
        metadata = infer_metadata(pdf_path, pages, config)
        parser_cfg = config.get("parser", {}) if config else {}
        return cls(metadata, pages, include_text_blocks=bool(parser_cfg.get("include_text_blocks", True)))

    def parse(self) -> tuple[DocumentMetadata, list[StructuralElement]]:
        active = _ActiveElement()
        active_table = _ActiveElement()

        for page in self.pages:
            for raw_line in page.lines:
                line = normalize_text(raw_line)
                if not line or self._skip_line(line):
                    continue

                # Region transition headings always terminate active prose/table elements.
                region_transition = self._detect_region_transition(line)
                if region_transition:
                    self._flush(active)
                    self._flush_table(active_table)
                    self._apply_region_transition(region_transition, line, page)
                    continue

                # Table handling has to see a continuation label before generic heading detection.
                table_match = TABLE_RE.match(line)
                if table_match:
                    self._flush(active)
                    table_id = f"TABLE {canonical_dash(table_match.group('num'))}"
                    title = re.sub(r"\s+", " ", table_match.group("title")).strip()
                    if active_table.kind == "table" and active_table.element_id == table_id:
                        active_table.lines.append(line)
                        active_table.page_end_pdf = page.pdf_page
                        active_table.page_end_printed = page.printed_page
                    else:
                        self._flush_table(active_table)
                        active_table.start(
                            kind="table",
                            element_id=table_id,
                            line=line,
                            page=page,
                            section_path=self._section_path(),
                            title=title,
                        )
                    continue

                if active_table.kind == "table":
                    table_note_seen = any(l.startswith(("Note:", "Source:")) for l in active_table.lines)
                    likely_post_table_heading = is_all_caps_heading(line) and len(active_table.lines) > 20 and table_note_seen and not TABLE_CONT_RE.search(line)
                    if self._line_starts_new_non_table_element(line) or likely_post_table_heading:
                        self._flush_table(active_table)
                        # Reprocess the same line outside table mode.
                    else:
                        active_table.lines.append(line)
                        active_table.page_end_pdf = page.pdf_page
                        active_table.page_end_printed = page.printed_page
                        continue

                figure_match = FIGURE_RE.match(line)
                if figure_match:
                    self._flush(active)
                    figure_id = f"FIG. {canonical_dash(figure_match.group('num'))}"
                    caption = re.sub(r"\s+", " ", figure_match.group("caption")).strip()
                    self._add_record(
                        element_type="figure",
                        element_id=figure_id,
                        source_region=self.region,
                        section_path=self._section_path(),
                        page_start_pdf=page.pdf_page,
                        page_end_pdf=page.pdf_page,
                        page_start_printed=page.printed_page,
                        page_end_printed=page.printed_page,
                        text=line,
                        caption=caption,
                        confidence="medium",
                        parser_notes=["Caption extracted from PDF text. Use page image review for visual content."],
                    )
                    continue

                footnote_match = self._footnote_match(line)
                if footnote_match:
                    # Do not flush the active paragraph; footnotes can interrupt a paragraph across pages.
                    self._add_record(
                        element_type="footnote",
                        element_id=footnote_match.group("num"),
                        source_region=self.region,
                        section_path=self._section_path(),
                        page_start_pdf=page.pdf_page,
                        page_end_pdf=page.pdf_page,
                        page_start_printed=page.printed_page,
                        page_end_printed=page.printed_page,
                        text=footnote_match.group("text").strip(),
                        linked_from_element_id=active.element_id or self.current_para_id,
                        confidence="low",
                        parser_notes=["Footnote detected by line pattern; verify if the page contains multiple footnotes."],
                    )
                    continue

                ref_match = REFERENCE_ITEM_RE.match(line)
                if self.region == "References" and ref_match:
                    self._flush(active)
                    active.start(
                        kind="reference",
                        element_id=f"[{ref_match.group('num')}]",
                        line=ref_match.group("text").strip(),
                        page=page,
                        section_path=self._section_path(),
                    )
                    continue

                major_heading_match = MAJOR_BODY_HEADING_RE.match(line)
                if major_heading_match and not self._is_contents_entry(line):
                    # Major numbered section heading such as "1. INTRODUCTION".
                    self._flush(active)
                    self.region = "Body"
                    self.current_major_heading = re.sub(r"\s+", " ", line).strip()
                    self.current_subheading = None
                    self._add_heading(line, page)
                    continue

                para_match = self._paragraph_match(line)
                if para_match:
                    self._flush(active)
                    para_id = canonical_dash(para_match.group("id"))
                    if self._is_body_paragraph_id(para_id) and self.region in {"FrontMatter", "BackMatter"}:
                        self.region = "Body"
                        self.current_major_heading = _fallback_body_heading(para_id)
                        self.current_subheading = None
                    self.current_para_id = para_id
                    active.start(
                        kind="paragraph",
                        element_id=para_id,
                        line=para_match.group("text").strip(),
                        page=page,
                        section_path=self._section_path(),
                    )
                    continue

                if self._is_heading_line(line) or (is_all_caps_heading(line) and self.region in {"Body", "Appendix", "Annex"}):
                    self._flush(active)
                    # Contents lines are not true section headings for downstream section paths.
                    if not CONTENTS_HEADING_RE.match(line):
                        self.current_subheading = re.sub(r"\s+", " ", line).strip()
                    self._add_heading(line, page)
                    continue

                # Continuation or residual text.
                if active.kind:
                    active.lines.append(line)
                    active.page_end_pdf = page.pdf_page
                    active.page_end_printed = page.printed_page
                elif self.include_text_blocks:
                    active.start(
                        kind="text_block",
                        element_id=None,
                        line=line,
                        page=page,
                        section_path=self._section_path(),
                    )

        self._flush(active)
        self._flush_table(active_table)
        return self.meta, self.records


    def _is_heading_line(self, line: str) -> bool:
        """Recognise known front-matter headings without treating country lists as headings."""
        if line in KNOWN_PUBLICATION_HEADINGS:
            return True
        return False

    def _skip_line(self, line: str) -> bool:
        # Decorative artifacts and ordering/footer list noise can be ignored by default.
        if line == "@":
            return True
        return False

    def _detect_region_transition(self, line: str) -> str | None:
        if APPENDIX_HEADING_RE.match(line):
            return "Appendix"
        if REFERENCES_HEADING_RE.match(line):
            return "References"
        if ANNEX_HEADING_RE.match(line):
            return "Annex"
        if GLOSSARY_HEADING_RE.match(line):
            return "Glossary"
        if RELATED_PUBLICATIONS_RE.match(line):
            if self.region != "FrontMatter":
                return "BackMatter"
            return None
        return None

    def _apply_region_transition(self, new_region: str, line: str, page: PageText) -> None:
        self.region = new_region
        if new_region == "Annex":
            m = ANNEX_HEADING_RE.match(line)
            self.current_annex = f"Annex {m.group('num')}" if m else line
            self.current_major_heading = self.current_annex
            self.current_subheading = None
        elif new_region == "Appendix":
            self.current_major_heading = "Appendix"
            self.current_subheading = None
        elif new_region in {"References", "Glossary", "BackMatter"}:
            self.current_major_heading = new_region
            self.current_subheading = None
        self._add_heading(line, page)

    def _line_starts_new_non_table_element(self, line: str) -> bool:
        return bool(
            FIGURE_RE.match(line)
            or self._paragraph_match(line)
            or self._detect_region_transition(line)
        )

    def _paragraph_match(self, line: str):
        return BODY_PARA_RE.match(line) or APPENDIX_PARA_RE.match(line) or ANNEX_PARA_RE.match(line)

    def _is_body_paragraph_id(self, para_id: str) -> bool:
        return bool(re.match(r"^\d+\.\d+$", para_id))

    def _is_contents_entry(self, line: str) -> bool:
        return ". . ." in line or bool(re.search(r"\.{3,}", line))

    def _footnote_match(self, line: str):
        m = FOOTNOTE_RE.match(line)
        if not m:
            return None
        # Avoid treating numbered list items and table rows as footnotes.
        if self._paragraph_match(line) or TABLE_RE.match(line):
            return None
        # Footnotes are most often in body/appendix/annex pages. Keep as low-confidence if detected.
        return m

    def _section_path(self) -> list[str]:
        out: list[str] = []
        if self.current_major_heading:
            out.append(self.current_major_heading)
        if self.current_subheading and self.current_subheading not in out:
            out.append(self.current_subheading)
        return out

    def _add_heading(self, line: str, page: PageText) -> None:
        self._add_record(
            element_type="heading",
            element_id=None,
            source_region=self.region,
            section_path=self._section_path(),
            page_start_pdf=page.pdf_page,
            page_end_pdf=page.pdf_page,
            page_start_printed=page.printed_page,
            page_end_printed=page.printed_page,
            text=line,
            title=line,
            confidence="medium",
        )

    def _flush(self, active: "_ActiveElement") -> None:
        if not active.kind:
            return
        text = self._clean_element_text(" ".join(active.lines))
        if text:
            self._add_record(
                element_type=active.kind,
                element_id=active.element_id,
                source_region=self.region,
                section_path=active.section_path,
                page_start_pdf=active.page_start_pdf,
                page_end_pdf=active.page_end_pdf,
                page_start_printed=active.page_start_printed,
                page_end_printed=active.page_end_printed,
                text=text,
                confidence="medium" if active.kind == "paragraph" else "low",
            )
        active.reset()

    def _flush_table(self, active: "_ActiveElement") -> None:
        if active.kind != "table":
            active.reset()
            return
        raw = "\n".join(active.lines).strip()
        if raw:
            self._add_record(
                element_type="table",
                element_id=active.element_id,
                source_region=self.region,
                section_path=active.section_path,
                page_start_pdf=active.page_start_pdf,
                page_end_pdf=active.page_end_pdf,
                page_start_printed=active.page_start_printed,
                page_end_printed=active.page_end_printed,
                text=raw,
                title=active.title,
                confidence="medium",
                parser_notes=["Table captured as raw PDF text. Verify complex row/column boundaries manually or with page image review."],
            )
        active.reset()

    def _clean_element_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        # Common PDF line-break hyphenation cleanup for ordinary words. Preserve identifiers using en dash.
        text = re.sub(r"([a-z])‑\s+([a-z])", r"\1\2", text)
        return text

    def _add_record(
        self,
        *,
        element_type: str,
        element_id: str | None,
        source_region: str,
        section_path: list[str],
        page_start_pdf: int,
        page_end_pdf: int,
        page_start_printed: str | None,
        page_end_printed: str | None,
        text: str,
        title: str | None = None,
        caption: str | None = None,
        parent_element_id: str | None = None,
        linked_from_element_id: str | None = None,
        confidence: str = "medium",
        parser_notes: list[str] | None = None,
        extra: dict | None = None,
    ) -> None:
        text_status, reason = classify_status(
            element_type=element_type,
            source_region=source_region,
            element_id=element_id,
            section_path=section_path,
        )
        n = next(self.counter)
        element_key = element_id or f"{element_type}-{n:05d}"
        record_id = f"{self.meta.document_id}:{element_type}:{element_key}:p{page_start_pdf}"
        self.records.append(
            StructuralElement(
                record_id=record_id,
                document_id=self.meta.document_id,
                document_title=self.meta.title,
                document_family=self.meta.document_family,
                document_category=self.meta.document_category,
                document_type=self.meta.document_type,
                document_domain=self.meta.document_domain,
                series_name=self.meta.series_name,
                series_number=self.meta.series_number,
                element_type=element_type,
                element_id=element_id,
                source_region=source_region,
                text_status=text_status,
                status_reason=reason,
                section_path=section_path,
                page_start_pdf=page_start_pdf,
                page_end_pdf=page_end_pdf,
                page_start_printed=page_start_printed,
                page_end_printed=page_end_printed,
                text=text,
                title=title,
                caption=caption,
                parent_element_id=parent_element_id,
                linked_from_element_id=linked_from_element_id,
                confidence=confidence,
                parser_notes=parser_notes or [],
                extra=extra or {},
            )
        )


def classify_status(
    element_type: str,
    source_region: str,
    element_id: str | None = None,
    section_path: list[str] | None = None,
) -> tuple[str, str]:
    """Classify text status using SPESS C structural guidance.

    SPESS C treats safety standards and nuclear security guidance as normative
    publications, but distinguishes integral main text/appendices from annexes,
    footnotes and introductory material.
    """
    section_path = section_path or []
    if element_type == "footnote":
        return (
            "Informative",
            "SPESS C: Footnotes provide practical examples or additional information/explanation; they are not integral and should not contain requirements, recommendations or guidance.",
        )
    if source_region == "Body":
        if _is_section_one(element_id, section_path):
            return (
                "Informational",
                "SPESS C: Section 1 introduces the publication and sets context, purpose, scope and structure; it should not contain requirements, recommendations or guidance.",
            )
        if element_type in {"paragraph", "figure", "table"}:
            return (
                "Normative",
                "SPESS C: Numbered main-text sections from Section 2 onward present the primary technical content of the safety standard or nuclear security guidance publication.",
            )
        return (
            "Informational",
            "SPESS C: Headings structure the main text; requirements, recommendations or guidance are carried by numbered technical content, not heading text alone.",
        )
    if source_region == "Appendix":
        if element_type in {"paragraph", "figure", "table"}:
            return (
                "Normative",
                "SPESS C: An appendix is an integral part of the standard or guidance and has the same status as the main text.",
            )
        return (
            "Informational",
            "SPESS C: Appendix headings structure integral appendix material; the appendix content itself has the same status as the main text.",
        )
    if source_region == "Annex":
        if element_type in {"paragraph", "figure", "table"}:
            return (
                "Informative",
                "SPESS C: Annexes provide practical examples or additional information/explanation; they are not integral and should not contain requirements, recommendations or guidance.",
            )
        return (
            "Informational",
            "SPESS C: Annex headings structure non-integral annex material, which is used for examples or additional explanation.",
        )
    return (
        "Informational",
        "SPESS C: Front matter, references, glossary, publication metadata and back matter are outside the numbered primary technical content.",
    )


def _is_section_one(element_id: str | None, section_path: list[str]) -> bool:
    if element_id and element_id.startswith("1."):
        return True
    return bool(section_path and section_path[0].startswith("1."))


def _fallback_body_heading(element_id: str) -> str:
    section = element_id.split(".", 1)[0]
    if section == "1":
        return "1. INTRODUCTION"
    return f"{section}. SECTION {section}"


class _ActiveElement:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.kind: str | None = None
        self.element_id: str | None = None
        self.lines: list[str] = []
        self.section_path: list[str] = []
        self.page_start_pdf: int = 0
        self.page_end_pdf: int = 0
        self.page_start_printed: str | None = None
        self.page_end_printed: str | None = None
        self.title: str | None = None

    def start(self, *, kind: str, element_id: str | None, line: str, page: PageText, section_path: list[str], title: str | None = None) -> None:
        self.kind = kind
        self.element_id = element_id
        self.lines = [line] if line else []
        self.section_path = list(section_path)
        self.page_start_pdf = page.pdf_page
        self.page_end_pdf = page.pdf_page
        self.page_start_printed = page.printed_page
        self.page_end_printed = page.printed_page
        self.title = title
