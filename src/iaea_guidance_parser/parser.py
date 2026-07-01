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
    BACKMATTER_HEADING_RE,
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
    REQUIREMENT_RE,
    TABLE_CONT_RE,
    TABLE_RE,
    canonical_dash,
    is_all_caps_heading,
    normalize_text,
    PAGE_NUMBER_RE,
)

CONTINUATION_HEADING_WORDS = {"OF", "THE", "AND", "FOR", "IN", "TO", "WITH", "ON", "FROM", "BY"}
PAGE_FURNITURE_PAIR_RE = re.compile(r"^(?:Appendix|Annex)\s+[IVXLCDM]+$|^REFERENCES$|^INTERNATIONAL ATOMIC ENERGY.*$", re.I)
PAGE_FURNITURE_COMBINED_RE = re.compile(
    r"^\d{1,3}\s+(?:Appendix\s+[IVXLCDM]+|Annex\s+[IVXLCDM]+|REFERENCES|INTERNATIONAL ATOMIC ENERGY.*)$",
    re.I,
)
PUBLICATION_NUMBER_FURNITURE_RE = re.compile(r"^No\.\s+\d+$", re.I)
EMBEDDED_REQUIREMENT_RE = re.compile(r"\bRequirement\s+(?P<num>\d+[A-Z]?):\s*", re.I)
EMBEDDED_FOOTNOTE_RE = re.compile(
    r"(?<![\w\]])(?P<num>\d{1,2})\s+"
    r"(?=(?:The|In|For|See|This|Where|If|According|A|An|Although|When|‘|\"))"
)


class IAEAGuidanceParser:
    def __init__(self, metadata: DocumentMetadata, pages: list[PageText], include_text_blocks: bool = True):
        self.meta = metadata
        self.pages = pages
        self.include_text_blocks = include_text_blocks
        self.records: list[StructuralElement] = []
        self.counter = itertools.count(1)
        self.synthetic_table_counter = itertools.count(1)
        self.region = "FrontMatter"
        self.current_major_heading: str | None = None
        self.current_subheading: str | None = None
        self.current_annex: str | None = None
        self.current_para_id: str | None = None
        self.body_started = False
        self.in_contents = False

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
            for line in self._prepared_page_lines(page):
                if not line or self._skip_line(line):
                    continue

                if CONTENTS_HEADING_RE.match(line):
                    self.in_contents = True
                elif self.in_contents and self._is_contents_entry(line):
                    continue

                if active_table.kind == "table":
                    if self._should_end_active_table(active_table, line):
                        self._flush_table(active_table)
                    elif self._should_keep_active_table_line(active_table, line):
                        active_table.lines.append(line)
                        active_table.page_end_pdf = page.pdf_page
                        active_table.page_end_printed = page.printed_page
                        continue

                # Region transition headings always terminate active prose/table elements.
                region_transition = self._detect_region_transition(line)
                if region_transition:
                    self._flush(active)
                    self._flush_table(active_table)
                    self._apply_region_transition(region_transition, line, page)
                    continue

                active_outline_table = self._outline_table_start_from_active(active, line)
                if active_outline_table:
                    self._flush(active)
                    self._flush_table(active_table)
                    table_id, title = active_outline_table
                    active_table.start(
                        kind="table",
                        element_id=table_id,
                        line=line,
                        page=page,
                        section_path=self._section_path(),
                        title=title,
                    )
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

                synthetic_table = self._synthetic_table_start(line)
                if synthetic_table:
                    self._flush(active)
                    self._flush_table(active_table)
                    table_id, title = synthetic_table
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
                    likely_post_table_heading = (
                        is_all_caps_heading(line)
                        and len(active_table.lines) > 20
                        and not TABLE_CONT_RE.search(line)
                        and not self._looks_like_table_header_line(line)
                    )
                    is_table_line = self._should_keep_active_table_line(active_table, line)
                    if (self._line_starts_new_non_table_element(line) and not is_table_line) or likely_post_table_heading:
                        self._flush_table(active_table)
                        # Reprocess the same line outside table mode.
                    else:
                        active_table.lines.append(line)
                        active_table.page_end_pdf = page.pdf_page
                        active_table.page_end_printed = page.printed_page
                        continue

                requirement_match = REQUIREMENT_RE.match(line)
                if requirement_match and not self._is_contents_noise(line, page):
                    self._flush(active)
                    req_id = requirement_match.group("num")
                    active.start(
                        kind="requirement",
                        element_id=req_id,
                        line=line,
                        page=page,
                        section_path=self._section_path(),
                    )
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
                if major_heading_match and self._should_treat_as_body_heading(line, major_heading_match, page):
                    # Major numbered section heading such as "1. INTRODUCTION".
                    self._flush(active)
                    self._enter_body()
                    self.current_major_heading = re.sub(r"\s+", " ", line).strip()
                    self.current_subheading = None
                    self._add_heading(line, page)
                    continue

                para_match = self._paragraph_match(line)
                if para_match and not self._is_contents_noise(line, page):
                    self._flush(active)
                    para_id = canonical_dash(para_match.group("id"))
                    if self._should_enter_body_for_paragraph(para_id, page):
                        self._enter_body()
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

                if active.kind == "paragraph" and self._is_short_subheading_line(line):
                    self._flush(active)
                    self.current_subheading = re.sub(r"\s+", " ", line).strip()
                    self._add_heading(line, page)
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

    def _prepared_page_lines(self, page: PageText) -> list[str]:
        lines = [normalize_text(line) for line in page.lines]
        lines = [line for line in lines if line]
        filtered: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            prev_line = lines[i - 1] if i > 0 else ""

            if self._is_page_furniture_line(line, prev_line, next_line):
                if PAGE_NUMBER_RE.match(line) and PAGE_FURNITURE_PAIR_RE.match(next_line):
                    i += 2
                else:
                    i += 1
                continue

            if next_line and self._should_merge_heading_lines(line, next_line):
                filtered.append(f"{line} {next_line}")
                i += 2
                continue

            filtered.append(line)
            i += 1
        return filtered

    def _is_page_furniture_line(self, line: str, prev_line: str, next_line: str) -> bool:
        if PAGE_FURNITURE_COMBINED_RE.match(line):
            return True
        if PAGE_NUMBER_RE.match(line) and PAGE_FURNITURE_PAIR_RE.match(next_line):
            return True
        if PUBLICATION_NUMBER_FURNITURE_RE.match(line) and BACKMATTER_HEADING_RE.match(next_line):
            return True
        return bool(PAGE_FURNITURE_PAIR_RE.match(line) and PAGE_NUMBER_RE.match(prev_line))

    def _should_merge_heading_lines(self, line: str, next_line: str) -> bool:
        if not next_line or len(line) > 140 or len(next_line) > 120:
            return False
        if not (is_all_caps_heading(next_line) or next_line.isupper()):
            return False
        major = MAJOR_BODY_HEADING_RE.match(line)
        heading_text = major.group("title") if major else line
        if not (is_all_caps_heading(line) or major):
            return False
        words = re.findall(r"[A-Za-z]+", heading_text.upper())
        if not words:
            return False
        if words[-1] in CONTINUATION_HEADING_WORDS:
            return True
        return not major and is_all_caps_heading(line) and len(line) >= 28 and len(next_line) >= 8


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
        if not self.body_started and self.region == "FrontMatter":
            return None
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
        if BACKMATTER_HEADING_RE.match(line):
            return "BackMatter"
        return None

    def _apply_region_transition(self, new_region: str, line: str, page: PageText) -> None:
        self.region = new_region
        if new_region == "Annex":
            m = ANNEX_HEADING_RE.match(line)
            self.current_annex = f"Annex {m.group('num')}" if m and m.group("num") else "Annex"
            self.current_major_heading = self.current_annex
            self.current_subheading = None
        elif new_region == "Appendix":
            m = APPENDIX_HEADING_RE.match(line)
            self.current_major_heading = f"Appendix {m.group('num')}" if m and m.group("num") else "Appendix"
            self.current_subheading = None
        elif new_region in {"References", "Glossary", "BackMatter"}:
            self.current_major_heading = new_region
            self.current_subheading = None
        self._add_heading(line, page)

    def _line_starts_new_non_table_element(self, line: str) -> bool:
        return bool(
            FIGURE_RE.match(line)
            or REQUIREMENT_RE.match(line)
            or self._paragraph_match(line)
            or MAJOR_BODY_HEADING_RE.match(line)
            or self._detect_region_transition(line)
        )

    def _should_treat_as_body_heading(self, line: str, match, page: PageText) -> bool:
        if self._is_contents_noise(line, page):
            return False
        para_match = BODY_PARA_RE.match(line)
        if para_match and re.match(r"^\d{3}(?:\.\d+)?$", para_match.group("id")):
            return False
        if self.region == "Body":
            return True
        if self.region not in {"FrontMatter", "BackMatter"}:
            return False
        num = match.group("num")
        title = re.sub(r"\s+", " ", match.group("title")).strip().upper()
        # The front matter often contains numbered catalogue or series-structure
        # entries. Only the actual first publication section should open Body.
        return num == "1" and title.startswith("INTRODUCTION")

    def _should_enter_body_for_paragraph(self, para_id: str, page: PageText) -> bool:
        if not self._is_body_paragraph_id(para_id):
            return False
        if self.region not in {"FrontMatter", "BackMatter"}:
            return False
        # A real publication body normally starts at para. 1.1. Later numbered
        # entries before that point are usually front-matter series overviews.
        if para_id.startswith("1."):
            return True
        return bool(page.printed_page and re.match(r"^\d{3}(?:\.\d+)?$", para_id))

    def _enter_body(self) -> None:
        self.region = "Body"
        self.body_started = True
        self.in_contents = False

    def _paragraph_match(self, line: str):
        return BODY_PARA_RE.match(line) or APPENDIX_PARA_RE.match(line) or ANNEX_PARA_RE.match(line)

    def _is_body_paragraph_id(self, para_id: str) -> bool:
        return bool(re.match(r"^(?:\d+\.\d+|\d{3}(?:\.\d+)?)$", para_id))

    def _is_contents_entry(self, line: str) -> bool:
        return ". . ." in line or bool(re.search(r"\.{3,}", line))

    def _is_contents_noise(self, line: str, page: PageText) -> bool:
        return self._is_contents_entry(line) or (self.in_contents and page.printed_page is None)

    def _footnote_match(self, line: str):
        m = FOOTNOTE_RE.match(line)
        if not m:
            return None
        # Avoid treating numbered list items and table rows as footnotes.
        if self._paragraph_match(line) or TABLE_RE.match(line):
            return None
        # Footnotes are most often in body/appendix/annex pages. Keep as low-confidence if detected.
        return m

    def _is_short_subheading_line(self, line: str) -> bool:
        if not line or line.endswith((".", ";", ":")):
            return False
        if self._line_starts_new_non_table_element(line) or self._footnote_match(line):
            return False
        words = line.split()
        if not (1 <= len(words) <= 8):
            return False
        letters = [ch for ch in line if ch.isalpha()]
        if len(letters) < 4:
            return False
        if is_all_caps_heading(line):
            return True
        title_words = sum(1 for word in words if word[:1].isupper() and word[1:].islower())
        lower_words = sum(1 for word in words if word.islower())
        lower_joiners = {"and", "or", "of", "the", "in", "to", "for", "with"}
        joiners = sum(1 for word in words if word.lower() in lower_joiners)
        return title_words >= 1 and title_words + lower_words + joiners == len(words)

    def _looks_like_table_cell_paragraph(self, line: str) -> bool:
        if self._looks_like_multilevel_outline_number(line):
            return True
        match = self._paragraph_match(line)
        if not match:
            return False
        text = match.group("text").strip()
        if not text:
            return True
        if len(text) > 90:
            return False
        if text.endswith((".", ";", ":")):
            return False
        if re.search(r"\b(?:shall|should|must|is|are|was|were|has|have|may)\b", text, flags=re.I):
            return False
        words = text.split()
        if len(words) > 8:
            return False
        return any(ch.isalpha() for ch in text)

    def _looks_like_table_header_line(self, line: str) -> bool:
        return bool(
            re.match(r"^APPENDIX\s+[IVXLCDM]+$", line, flags=re.I)
            or re.match(r"^TABLE\s+\d+:\s*$", line, flags=re.I)
        )

    def _synthetic_table_start(self, line: str) -> tuple[str, str] | None:
        """Capture outline-style annex blocks before paragraph segmentation.

        Some publications use annex material that is visually a table or course
        outline but has no "TABLE N." caption in extracted text. If left to the
        paragraph matcher, outline labels such as 1.1. become fake paragraphs.
        """
        normalized = re.sub(r"\s+", " ", line).strip()
        if re.match(r"^TYPICAL TABLE OF CONTENTS(?:\b|$)", normalized, flags=re.I):
            return "TYPICAL TABLE OF CONTENTS", normalized
        if self.region == "Annex" and normalized.lower() == "module outline":
            return f"MODULE OUTLINE {next(self.synthetic_table_counter):04d}", normalized
        return None

    def _outline_table_start_from_active(self, active: "_ActiveElement", line: str) -> tuple[str, str] | None:
        if self.region != "Annex" or active.kind != "paragraph":
            return None
        if not self._looks_like_outline_table_line(line):
            return None
        active_text = self._clean_element_text(" ".join(active.lines))
        if not re.search(r"\bfollowing outline:\s*$", active_text, flags=re.I):
            return None
        return f"OUTLINE {next(self.synthetic_table_counter):04d}", "Outline"

    def _should_keep_active_table_line(self, active_table: "_ActiveElement", line: str) -> bool:
        if self._looks_like_table_header_line(line) or self._looks_like_table_cell_paragraph(line):
            return True
        if self._is_synthetic_table(active_table) and self._looks_like_outline_table_line(line):
            return True
        return False

    def _should_end_active_table(self, active_table: "_ActiveElement", line: str) -> bool:
        if not self._is_synthetic_table(active_table):
            return False
        if active_table.title and active_table.title.lower() == "module outline":
            return bool(re.match(r"^[A-Z]\.$", line))
        return False

    def _is_synthetic_table(self, active_table: "_ActiveElement") -> bool:
        return bool(
            active_table.element_id
            and active_table.element_id.startswith(("MODULE OUTLINE", "TYPICAL TABLE OF CONTENTS"))
        )

    def _looks_like_multilevel_outline_number(self, line: str) -> bool:
        return bool(re.match(r"^\d+(?:\.\d+){1,8}\.\s*(?:\S.*)?$", line))

    def _looks_like_outline_table_line(self, line: str) -> bool:
        if self._looks_like_multilevel_outline_number(line):
            return True
        return bool(
            re.match(r"^\d+\.\s+\S", line)
            or re.match(r"^\d+\.$", line)
            or re.match(r"^CHAPTER\s+\d+[:.]\s+\S", line, flags=re.I)
        )

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
            self._add_text_records_from_active(active, text)
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

    def _add_text_records_from_active(self, active: "_ActiveElement", text: str) -> None:
        for kind, element_id, segment_text in self._split_requirement_segments(active.kind, active.element_id, text):
            cleaned_text, footnotes = self._extract_embedded_footnotes(segment_text)
            if cleaned_text:
                self._add_record(
                    element_type=kind,
                    element_id=element_id,
                    source_region=self.region,
                    section_path=active.section_path,
                    page_start_pdf=active.page_start_pdf,
                    page_end_pdf=active.page_end_pdf,
                    page_start_printed=active.page_start_printed,
                    page_end_printed=active.page_end_printed,
                    text=cleaned_text,
                    confidence="medium" if kind in {"paragraph", "requirement"} else "low",
                )
            for footnote_id, footnote_text in footnotes:
                self._add_record(
                    element_type="footnote",
                    element_id=footnote_id,
                    source_region=self.region,
                    section_path=active.section_path,
                    page_start_pdf=active.page_start_pdf,
                    page_end_pdf=active.page_end_pdf,
                    page_start_printed=active.page_start_printed,
                    page_end_printed=active.page_end_printed,
                    text=footnote_text,
                    linked_from_element_id=element_id or active.element_id,
                    confidence="low",
                    parser_notes=["Footnote body split from running paragraph text; verify anchor placement if exact citation is required."],
                )

    def _split_requirement_segments(self, kind: str, element_id: str | None, text: str) -> list[tuple[str, str | None, str]]:
        if kind == "requirement":
            match = EMBEDDED_REQUIREMENT_RE.search(text)
            return [("requirement", match.group("num") if match else element_id, text)]
        if kind not in {"paragraph", "text_block"}:
            return [(kind, element_id, text)]

        matches = list(EMBEDDED_REQUIREMENT_RE.finditer(text))
        if not matches:
            return [(kind, element_id, text)]

        segments: list[tuple[str, str | None, str]] = []
        if matches[0].start() > 0:
            segments.append((kind, element_id, text[: matches[0].start()].strip()))
        for idx, match in enumerate(matches):
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            segments.append(("requirement", match.group("num"), text[match.start() : end].strip()))
        return [segment for segment in segments if segment[2]]

    def _extract_embedded_footnotes(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        matches = list(EMBEDDED_FOOTNOTE_RE.finditer(text))
        if not matches:
            return text, []

        clean_parts: list[str] = []
        footnotes: list[tuple[str, str]] = []
        cursor = 0
        for idx, match in enumerate(matches):
            clean_parts.append(text[cursor : match.start()])
            segment_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body_segment = text[match.end() : segment_end]
            body, trailing = self._split_footnote_body(body_segment, has_next=idx + 1 < len(matches))
            if body:
                footnotes.append((match.group("num"), self._clean_element_text(body)))
            if trailing:
                clean_parts.append(trailing)
            cursor = segment_end
        clean_parts.append(text[cursor:])
        cleaned = self._clean_element_text(" ".join(part.strip() for part in clean_parts if part.strip()))
        return cleaned, footnotes

    def _split_footnote_body(self, segment: str, *, has_next: bool) -> tuple[str, str]:
        segment = segment.strip()
        if has_next or not segment:
            return segment, ""
        for match in re.finditer(r"([.!?][’”\"]?)\s+(?=(?:—|–|-|with\b|[a-z]))", segment):
            if match.start() >= 20:
                return segment[: match.end(1)].strip(), segment[match.end() :].strip()
        return segment, ""

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
        record_id = f"{self.meta.document_id}:{element_type}:{element_key}:p{page_start_pdf}:r{n:05d}"
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
        if element_type in {"paragraph", "requirement", "figure", "table", "text_block"}:
            return (
                "Normative",
                "SPESS C: Numbered main-text sections from Section 2 onward present the primary technical content of the safety standard or nuclear security guidance publication.",
            )
        return (
            "Informational",
            "SPESS C: Headings structure the main text; requirements, recommendations or guidance are carried by numbered technical content, not heading text alone.",
        )
    if source_region == "Appendix":
        if element_type in {"paragraph", "requirement", "figure", "table", "text_block"}:
            return (
                "Normative",
                "SPESS C: An appendix is an integral part of the standard or guidance and has the same status as the main text.",
            )
        return (
            "Informational",
            "SPESS C: Appendix headings structure integral appendix material; the appendix content itself has the same status as the main text.",
        )
    if source_region == "Annex":
        if element_type in {"paragraph", "requirement", "figure", "table", "text_block"}:
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
    if element_id and re.match(r"^1\d{2}(?:\.\d+)?$", element_id):
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
