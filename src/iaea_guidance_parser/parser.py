from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

from .metadata import config_source_sha256, infer_metadata, load_config
from .models import DocumentMetadata, PageText, StructuralElement
from .pdf_extract import extract_pages
from .provenance import sha256_file
from .rules import (
    ANNEX_HEADING_RE,
    APPENDIX_HEADING_RE,
    BACKMATTER_HEADING_RE,
    BODY_PARA_RE,
    CONTENTS_HEADING_RE,
    FOOTNOTE_RE,
    GLOSSARY_HEADING_RE,
    KNOWN_PUBLICATION_HEADINGS,
    MAJOR_BODY_HEADING_RE,
    PAGE_NUMBER_RE,
    REFERENCE_ITEM_RE,
    REFERENCES_HEADING_RE,
    RELATED_PUBLICATIONS_RE,
    REQUIREMENT_RE,
    TABLE_CONT_RE,
    StructuralLabel,
    classify_status,
    is_all_caps_heading,
    is_prefixed_reference,
    match_figure_label,
    match_paragraph_label,
    match_table_label,
    match_unterminated_label,
    normalize_text,
    remove_pdf_line_breaks,
)

CONTINUATION_HEADING_WORDS = {"OF", "THE", "AND", "FOR", "IN", "TO", "WITH", "ON", "FROM", "BY"}
PAGE_FURNITURE_PAIR_RE = re.compile(
    r"^(?:Appendix|Annex)\s+[IVXLCDM]+$|^REFERENCES$|^INTERNATIONAL ATOMIC ENERGY.*$", re.I
)
PAGE_FURNITURE_COMBINED_RE = re.compile(
    r"^\d{1,3}\s+(?:Appendix\s+[IVXLCDM]+|Annex\s+[IVXLCDM]+|REFERENCES|INTERNATIONAL ATOMIC ENERGY.*)$",
    re.I,
)
PUBLICATION_NUMBER_FURNITURE_RE = re.compile(r"^No\.\s+\d+$", re.I)
EMBEDDED_REQUIREMENT_RE = re.compile(r"\bRequirement\s+(?P<num>\d+[A-Z]?):\s*", re.I)
EMBEDDED_FOOTNOTE_RE = re.compile(
    r"(?<![\w\].–—‑-])(?P<num>\d{1,2})\s+"
    r"(?=(?:The|In|For|See|This|Where|If|According|A|An|Although|When|‘|\"))"
)
# A raised digit after a unit can be an exponent. Typography alone cannot
# distinguish that from a citation, so leave these ambiguous anchors unlinked.
UNIT_SYMBOL_RE = re.compile(
    r"(?:da|[QRYZEPTGMkhdcmµμnpfazyrq])?"
    r"(?:m|g|s|A|K|mol|cd|rad|sr|Hz|N|Pa|J|W|C|V|F|Ω|S|Wb|T|H|Bq|Gy|Sv|kat|eV|L|l)"
)


class IAEAGuidanceParser:
    def __init__(
        self,
        metadata: DocumentMetadata,
        pages: list[PageText],
        include_text_blocks: bool = True,
        parser_config: dict[str, Any] | None = None,
    ):
        self.meta = metadata
        self.pages = pages
        self.include_text_blocks = include_text_blocks
        self.parser_config = parser_config or {}
        self.label_exceptions = self.parser_config.get("label_exceptions", []) or []
        self.local_scope_patterns = self.parser_config.get("local_scope_patterns", []) or []
        self.outline_regions = self.parser_config.get("outline_regions", []) or []
        self._reset_state()

    def _reset_state(self) -> None:
        self._pages_by_number = {page.pdf_page: page for page in self.pages}
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
        self._current_page: PageText | None = None
        self.current_local_scope: str | None = None
        self._heading_fragments: dict[tuple[int, str], list[str]] = {}
        self._interrupted_paragraph: StructuralElement | None = None
        self._claimed_source_lines: set[tuple[int, int]] = set()
        self._automatic_headings: set[tuple[int, str]] = set()

    @classmethod
    def from_pdf(cls, pdf_path: Path, config_path: Path | None = None) -> IAEAGuidanceParser:
        config = load_config(config_path)
        return cls.from_pdf_config(pdf_path, config)

    @classmethod
    def from_pdf_config(
        cls, pdf_path: Path, config: dict[str, Any] | None = None
    ) -> IAEAGuidanceParser:
        """Create a parser for one PDF using an already-loaded config dictionary."""
        config = config or {}
        expected_hash = config_source_sha256(config)
        if expected_hash is not None and expected_hash != sha256_file(pdf_path):
            raise ValueError(f"{pdf_path}: config source_sha256 does not match this PDF")
        # Selection is not a parser rule and must not change the config fingerprint.
        config = {key: value for key, value in config.items() if key != "match"}
        parser_cfg = config.get("parser", {})
        pages = extract_pages(pdf_path, parser_cfg)
        metadata = infer_metadata(pdf_path, pages, config)
        return cls(
            metadata,
            pages,
            include_text_blocks=bool(parser_cfg.get("include_text_blocks", True)),
            parser_config=parser_cfg,
        )

    def parse(self) -> tuple[DocumentMetadata, list[StructuralElement]]:
        """Assemble records in reading order using three independent buffers.

        Tables and footnotes can interrupt prose without ending its paragraph.
        Keep their buffers separate, and consume table continuations before
        treating numbered cells or capitalized text as new publication sections.
        """
        self._reset_state()
        buffers = _ElementBuffers()
        for page in self.pages:
            self._current_page = page
            boundary = self.parser_config.get("page_regions", {}).get(page.pdf_page)
            if boundary:
                region = boundary if isinstance(boundary, str) else boundary["region"]
                self._flush(buffers.prose)
                self._flush_table(buffers.table)
                self._flush_footnote(buffers.footnote, buffers.prose)
                self.region = region
                self.current_major_heading = None if region == "FrontMatter" else region
                if isinstance(boundary, dict):
                    self.current_major_heading = boundary.get("section", self.current_major_heading)
                self.current_subheading = None
                self.current_para_id = None
                self.current_local_scope = None
                self._interrupted_paragraph = None
            self._flush_footnote(buffers.footnote, buffers.prose)
            for line in self._prepared_page_lines(page):
                page.current_source_spans = self._source_spans(line, page)
                self._consume_line(line, page, buffers)
            self._flush_footnote(buffers.footnote, buffers.prose)

        self._flush(buffers.prose)
        self._flush_table(buffers.table)
        self._flush_footnote(buffers.footnote, buffers.prose)
        self._infer_pdf_hierarchy()
        self._resolve_pdf_footnotes()
        self._apply_reviewed_equations()
        return self.meta, self.records

    def _apply_reviewed_equations(self) -> None:
        """Linearize individually reviewed equations whose layout carries meaning.

        A fraction bar is often a drawing, absent from extracted text. These
        source-bound decisions keep the original extraction and page references;
        they are not a general mathematical normalization rule.
        """
        reviewed = self.parser_config.get("reviewed_equations", {})
        if not reviewed:
            return
        if reviewed.get("source_sha256") != self.meta.source_sha256:
            raise ValueError("Reviewed equations must match the source PDF SHA-256")
        for rule in reviewed.get("items", []):
            required = ("element_id", "original_text", "linear_text", "reviewer", "notes")
            if any(not isinstance(rule.get(k), str) or not rule[k].strip() for k in required):
                raise ValueError("Reviewed equation needs a label, both texts and source review")
            page = rule.get("pdf_page")
            if page not in self._pages_by_number:
                raise ValueError("Reviewed equation page is outside the source PDF")
            matches = [
                record
                for record in self.records
                if record.element_type == "paragraph"
                and record.element_id == rule["element_id"]
                and record.page_start_pdf <= page <= record.page_end_pdf
                and rule["original_text"] in record.text
            ]
            if len(matches) != 1 or matches[0].text.count(rule["original_text"]) != 1:
                raise ValueError("Reviewed equation must match exactly one source passage")
            record = matches[0]
            record.text = record.text.replace(rule["original_text"], rule["linear_text"], 1)
            record.extra.setdefault("reviewed_equations", []).append(dict(rule))

    def _consume_line(self, line: str, page: PageText, buffers: _ElementBuffers) -> None:
        # This order is part of the grammar: region boundaries close buffers;
        # table labels precede headings; explicit labels precede residual prose.
        if (
            self._consume_layout_table(line, page, buffers)
            or self._consume_contents(line, page, buffers)
            or self._consume_table_continuation(line, page, buffers)
            or self._consume_region(line, page, buffers)
            or self._consume_table_start(line, page, buffers)
            or self._consume_table_body(line, page, buffers)
            or self._consume_footnote_continuation(line, page, buffers)
            or self._consume_requirement_and_figure(line, page, buffers)
            or self._consume_numbered_text(line, page, buffers)
            or self._consume_heading(line, page, buffers)
        ):
            return
        self._consume_prose(line, page, buffers)

    def _source_spans(self, line: str, page: PageText) -> list[dict[str, Any]]:
        """Consume matching occurrences once, retaining distinct repeated source lines."""
        compact = re.sub(r"\s+", "", line)
        spans = []
        for source in page.source_lines:
            text = re.sub(r"\s+", "", source["text"])
            if text.endswith("-") and text not in compact and text[:-1] in compact:
                text = text[:-1]  # The existing line-joiner already removed this line-end hyphen.
            key = (page.pdf_page, source["line"])
            if (
                source["role"] in {"text", "reviewed_image_transcription"}
                and (len(text) >= 4 or text == compact)
                and text
                and text in compact
                and key not in self._claimed_source_lines
            ):
                spans.append({key: source[key] for key in ("pdf_page", "line", "bbox")})
                compact = compact.replace(text, "", 1)
                self._claimed_source_lines.add(key)
        return spans

    @staticmethod
    def _pdf_note_label(spans):
        visible = [s for s in spans if s["text"].strip()]
        if len(visible) < 2:
            return None
        first, following = visible[:2]
        label = first["text"].strip()
        if re.fullmatch(r"\d{1,2}", label) and first["size"] < following["size"] * 0.8:
            return label
        return None

    def _pdf_note_body(self, line, page):
        return any(
            normalize_text(source["text"]) == line
            and self._pdf_note_label(page.source_typography.get(source["line"], []))
            for source in page.source_lines
        )

    def _resolve_pdf_footnotes(self):
        """Resolve inline markers against final record ownership, never the latest paragraph."""
        owners = {}
        for record in self.records:
            if record.element_type not in {"paragraph", "text_block"}:
                continue
            for span in record.extra.get("source_spans", []):
                owners.setdefault((span["pdf_page"], span["line"]), []).append(record)
        for note in self.records:
            page = self._pages_by_number[note.page_start_pdf]
            if note.element_type != "footnote" or not page.source_typography:
                continue
            if "source_anchor" in note.extra:
                continue
            bodies = [
                source
                for source in page.source_lines
                if self._pdf_note_label(page.source_typography.get(source["line"], []))
                == note.element_id
            ]
            candidates = []
            for source in page.source_lines:
                spans = page.source_typography.get(source["line"], [])
                if self._pdf_note_label(spans) or source["role"] != "text":
                    continue
                for index, span in enumerate(spans):
                    if span["text"].strip() != note.element_id or not index:
                        continue
                    before = "".join(s["text"] for s in spans[:index]).rstrip()
                    previous = spans[index - 1]
                    after = "".join(s["text"] for s in spans[index + 1 :])
                    preceding_word = re.search(r"[^\W\d_]+$", before)
                    scientific_symbol = preceding_word and (
                        UNIT_SYMBOL_RE.fullmatch(preceding_word.group())
                        or re.fullmatch(r"[A-Z][a-z]?", preceding_word.group())
                    )
                    raised = bool(span["flags"] & 1) or (
                        previous["origin"][1] - span["origin"][1] > previous["size"] * 0.2
                    )
                    # A word/sentence citation is distinguishable from 10^-3, m^2,
                    # H^2O and a superscript attached to an element symbol.
                    # Short capitalized forms can denote elements (Pu, Co);
                    # ambiguous markers must remain unlinked rather than guessed.
                    if not (
                        raised
                        and span["size"] < previous["size"] * 0.8
                        and re.search(r"[^\W\d_]{2,}[.!?\u2019\u201d)]?$", before)
                        and not scientific_symbol
                        and (not after or not after[:1].isalnum())
                        and len(bodies) == 1
                        and source["bbox"][3] < bodies[0]["bbox"][1]
                    ):
                        continue
                    linked = owners.get((page.pdf_page, source["line"]), [])
                    if len(linked) == 1:
                        candidates.append((linked[0], source))
            if len(candidates) == 1:
                owner, source = candidates[0]
                note.linked_from_element_id = owner.element_id
                note.section_path = list(owner.section_path)
                note.source_region = owner.source_region
                note.extra["inline_anchor"] = {
                    "pdf_page": page.pdf_page,
                    "line": source["line"],
                    "bbox": source["bbox"],
                }
            else:
                note.linked_from_element_id = None
                note.confidence = "low"
                note.parser_notes.append(
                    "Unresolved PDF footnote anchor: no unique inline marker owner."
                )

    def _infer_pdf_hierarchy(self):
        """Use a demonstrated size/style distinction within each major section."""

        def profile(record):
            spans = [
                s
                for ref in record.extra.get("source_spans", [])
                for s in self._pages_by_number[ref["pdf_page"]].source_typography.get(
                    ref["line"], []
                )
                if any(c.isalpha() for c in s["text"])
            ]
            if not spans:
                return None
            return (
                max(s["size"] for s in spans),
                all(s["flags"] & 2 for s in spans),
                min(s["bbox"][0] for s in spans),
            )

        def contains(parent, child):
            title, (size, italic, left) = parent
            child_title, (child_size, child_italic, child_left) = child
            return (
                title.isupper()
                and child_left >= left - 2
                and (
                    size >= child_size + 1
                    or (
                        not child_title.isupper()
                        and child_italic
                        and not italic
                        and abs(size - child_size) <= 0.5
                    )
                )
            )

        root, stack, parents = None, [], {}
        for record in self.records:
            path = record.section_path
            if record.source_region not in {"Body", "Appendix", "Annex"} or not path:
                continue
            if path[0] != root:
                root, stack, parents = path[0], [], {}
            if record.element_type == "heading":
                style = profile(record)
                parents.pop(record.text, None)
                if record.text == root:
                    stack = []
                    continue
                if style:
                    entry = (record.text, style)
                    while stack and not contains(stack[-1], entry):
                        stack.pop()
                    if stack:
                        parents[record.text] = [title for title, _ in stack]
                    stack.append(entry)
            # Explicit parent paths always take precedence; inferred parents
            # never rewrite a reviewed hierarchy or cross a major boundary.
            explicit = self.parser_config.get("subheading_parents", {}).get(path[0], {})
            if len(path) == 2 and path[-1] in parents and path[-1] not in explicit:
                record.section_path = [path[0], *parents[path[-1]], path[-1]]

    @staticmethod
    def _visual_regions(line: str, page: PageText) -> list[dict[str, Any]]:
        """Associate suppressed drawing text with the next caption on its page."""
        label = match_figure_label(line)
        if label is None:
            return []
        if page.source_typography:
            return page.figure_regions_by_caption.get(label.canonical_id, [])
        captions = [source for source in page.source_lines if match_figure_label(source["text"])]
        matching = [
            source
            for source in captions
            if match_figure_label(source["text"]).canonical_id == label.canonical_id
        ]
        if len(matching) != 1:
            return []
        bottom = matching[0]["bbox"][1]
        top = max(
            (source["bbox"][3] for source in captions if source["bbox"][1] < bottom), default=0
        )
        return [source for source in page.figure_regions if top <= source["bbox"][1] < bottom]

    def _consume_layout_table(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        table = page.tables.get(line)
        if table is None:
            return False
        self._flush_before_floating_element(buffers.prose)
        self._flush_table(buffers.table)
        self._flush_footnote(buffers.footnote, buffers.prose)
        self._add_record(
            element_type="table",
            element_id=table["element_id"],
            source_region=self.region,
            section_path=self._section_path(),
            page_start_pdf=page.pdf_page,
            page_end_pdf=page.pdf_page,
            page_start_printed=page.printed_page,
            page_end_printed=page.printed_page,
            text=table["raw_text"],
            title=table["caption"],
            parent_element_id=self.current_local_scope,
            parser_notes=[
                "Captioned table bounded by source rules; cell relationships are supplied only for grids."
            ]
            + table.get("warnings", []),
            extra={
                "source_label": table["source_label"],
                "label_style": table["label_style"],
                "source_spans": table["source_spans"],
                "table": {
                    **{
                        key: table[key]
                        for key in (
                            "pdf_page",
                            "bbox",
                            "layout",
                            "row_count",
                            "column_count",
                            "cells",
                        )
                    },
                    **({"notes": table["notes"]} if table.get("notes") else {}),
                },
            },
        )
        return True

    def _consume_contents(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        if not line or self._skip_line(line):
            return True

        if (
            CONTENTS_HEADING_RE.match(line)
            and not self.body_started
            and self.region == "FrontMatter"
        ):
            self.in_contents = True
        elif self.in_contents:
            if self._is_body_start_after_contents(line, page):
                self.in_contents = False
            else:
                # A contents list can span several physical pages. Its
                # final entry number is easily mistaken for a printed
                # page footer, so printed_page cannot safely signal the
                # beginning of the publication body.
                return True
        return False

    def _consume_table_continuation(
        self, line: str, page: PageText, buffers: _ElementBuffers
    ) -> bool:
        active_table = buffers.table
        if active_table.kind == "table":
            if self._should_resume_interrupted_paragraph_after_table(active_table, line, page):
                self._flush_table(active_table)
            elif self._should_end_active_table(active_table, line):
                self._flush_table(active_table)
            elif self._should_keep_active_table_line(active_table, line):
                active_table.append(line, page)
                return True
        return False

    def _consume_region(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        active = buffers.prose
        active_table = buffers.table
        active_footnote = buffers.footnote
        # Region transition headings always terminate active prose/table elements.
        region_transition = self._detect_region_transition(line)
        if region_transition:
            self._flush(active)
            self._flush_table(active_table)
            self._flush_footnote(active_footnote, buffers.prose)
            self._interrupted_paragraph = None
            self._apply_region_transition(region_transition, line, page)
            return True

        local_scope = self._configured_local_scope(line, page)
        if local_scope:
            self._flush(active)
            self._flush_table(active_table)
            self._flush_footnote(active_footnote, buffers.prose)
            self._interrupted_paragraph = None
            self.current_local_scope = local_scope
            self.current_subheading = re.sub(r"\s+", " ", line).strip()
            self._add_heading(line, page, extra={"local_scope": local_scope})
            return True
        return False

    def _consume_table_start(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        active = buffers.prose
        active_table = buffers.table
        active_footnote = buffers.footnote
        active_outline_table = self._outline_table_start_from_active(active, line)
        if active_outline_table:
            self._flush(active)
            self._flush_table(active_table)
            self._flush_footnote(active_footnote, buffers.prose)
            table_id, title = active_outline_table
            active_table.start(
                kind="table",
                element_id=table_id,
                line=line,
                page=page,
                section_path=self._section_path(),
                title=title,
            )
            return True

        # Table handling has to see a continuation label before generic heading detection.
        table_match = self._table_match(line, page)
        if table_match:
            self._flush_before_floating_element(active)
            self._flush_footnote(active_footnote, buffers.prose)
            table_id = f"TABLE {table_match.canonical_id}"
            title = re.sub(r"\s+", " ", table_match.text).strip()
            label_extra = {
                "source_label": table_match.raw_label,
                "label_style": table_match.style,
            }
            if self.current_local_scope:
                label_extra["local_scope"] = self.current_local_scope
            if active_table.kind == "table" and active_table.element_id == table_id:
                active_table.append(line, page)
            else:
                self._flush_table(active_table)
                active_table.start(
                    kind="table",
                    element_id=table_id,
                    line=line,
                    page=page,
                    section_path=self._section_path(),
                    title=title,
                    parent_element_id=self.current_local_scope,
                    extra=label_extra,
                )
            return True

        synthetic_table = self._synthetic_table_start(line)
        configured_outline = self._configured_outline_table_start(line, page)
        synthetic_table = synthetic_table or configured_outline
        if synthetic_table:
            self._flush_before_floating_element(active)
            self._flush_table(active_table)
            self._flush_footnote(active_footnote, buffers.prose)
            table_id, title = synthetic_table
            active_table.start(
                kind="table",
                element_id=table_id,
                line=line,
                page=page,
                section_path=self._section_path(),
                title=title,
                parent_element_id=self.current_local_scope,
                extra={
                    **(
                        {"local_scope": self.current_local_scope}
                        if self.current_local_scope
                        else {}
                    ),
                    **({"configured_outline": True} if configured_outline else {}),
                },
            )
            return True
        return False

    def _consume_table_body(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        active_table = buffers.table
        if active_table.kind == "table":
            typographic_heading = self._is_typographic_heading_line(line, page)
            typographic_post_table_heading = typographic_heading and (
                not is_all_caps_heading(line) or len(active_table.lines) > 5
            )
            likely_post_table_heading = (
                (is_all_caps_heading(line) or typographic_post_table_heading)
                and (
                    typographic_post_table_heading
                    or len(active_table.lines) > 20
                    or bool(re.match(r"^STEP\s+\d+\s*:", line, re.I))
                )
                and not TABLE_CONT_RE.search(line)
                and not self._looks_like_table_header_line(line)
            )
            is_table_line = self._should_keep_active_table_line(active_table, line)
            if (
                self._line_starts_new_non_table_element(line, page) and not is_table_line
            ) or likely_post_table_heading:
                self._flush_table(active_table)
                # Reprocess the same line outside table mode.
            else:
                active_table.append(line, page)
                return True
        return False

    def _consume_footnote_continuation(
        self, line: str, page: PageText, buffers: _ElementBuffers
    ) -> bool:
        active_footnote = buffers.footnote
        footnote_match = self._footnote_match(line)
        if active_footnote.kind == "footnote":
            if footnote_match or self._line_starts_new_non_table_element(line, page):
                self._flush_footnote(active_footnote, buffers.prose)
            else:
                active_footnote.append(line, page)
                return True
        return False

    def _consume_requirement_and_figure(
        self, line: str, page: PageText, buffers: _ElementBuffers
    ) -> bool:
        active = buffers.prose
        requirement_match = REQUIREMENT_RE.match(line)
        if requirement_match and not self._is_contents_noise(line, page):
            self._flush(active)
            self._interrupted_paragraph = None
            req_id = requirement_match.group("num")
            active.start(
                kind="requirement",
                element_id=req_id,
                line=line,
                page=page,
                section_path=self._section_path(),
            )
            return True

        figure_match = self._figure_match(line, page)
        if figure_match:
            self._flush_before_floating_element(active)
            figure_id = f"FIG. {figure_match.canonical_id}"
            caption = re.sub(r"\s+", " ", figure_match.text).strip()
            figure_extra = {
                "source_label": figure_match.raw_label,
                "label_style": figure_match.style,
            }
            if self.current_local_scope:
                figure_extra["local_scope"] = self.current_local_scope
            if page.suppressed_figure_lines:
                regions = self._visual_regions(line, page)
                figure_extra["suppressed_visual_text_line_count"] = len(regions)
                figure_extra["visual_text_regions"] = regions
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
                parser_notes=[
                    "Caption extracted from PDF text. Use page image review for visual content."
                ]
                + page.figure_warnings.get(figure_match.canonical_id, []),
                parent_element_id=self.current_local_scope,
                extra=figure_extra,
            )
            return True
        return False

    def _consume_numbered_text(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        active = buffers.prose
        active_footnote = buffers.footnote
        footnote_match = self._footnote_match(line)
        if footnote_match:
            footnote_id = footnote_match.group("num")
            anchor = next(
                (
                    item
                    for item in self.parser_config.get("footnote_anchors", [])
                    if page.pdf_page in item["pdf_pages"] and item["footnote_id"] == footnote_id
                ),
                {},
            )
            # Do not flush the active paragraph; footnotes can interrupt a paragraph across pages.
            active_footnote.start(
                kind="footnote",
                element_id=footnote_id,
                line=footnote_match.group("text").strip(),
                page=page,
                section_path=anchor.get("section_path", self._section_path()),
                linked_from_element_id=anchor.get(
                    "element_id", active.element_id or self.current_para_id
                ),
                extra={"source_anchor": anchor} if anchor else {},
            )
            return True

        term = self._glossary_term(line)
        if term:
            self._flush(active)
            active.start(
                kind="text_block",
                element_id=term,
                line=line,
                page=page,
                section_path=self._section_path(),
                title=term,
            )
            return True

        ref_match = REFERENCE_ITEM_RE.match(line)
        if self.region == "References" and ref_match:
            self._flush(active)
            self._interrupted_paragraph = None
            active.start(
                kind="reference",
                element_id=f"[{ref_match.group('num')}]",
                line=ref_match.group("text").strip(),
                page=page,
                section_path=self._section_path(),
            )
            return True

        major_heading_match = MAJOR_BODY_HEADING_RE.match(line)
        if major_heading_match and self._should_treat_as_body_heading(
            line, major_heading_match, page
        ):
            # Major numbered section heading such as "1. INTRODUCTION".
            self._flush(active)
            self._interrupted_paragraph = None
            self._enter_body()
            self.current_major_heading = re.sub(r"\s+", " ", line).strip()
            self.current_subheading = None
            self._add_heading(line, page)
            return True

        para_match = self._paragraph_match(line, page)
        if para_match and not self._is_contents_noise(line, page):
            self._flush(active)
            self._interrupted_paragraph = None
            para_id = para_match.canonical_id
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
                parent_element_id=self.current_local_scope,
                extra={
                    "source_label": para_match.raw_label,
                    "label_style": para_match.style,
                    **(
                        {"local_scope": self.current_local_scope}
                        if self.current_local_scope
                        else {}
                    ),
                },
            )
            return True
        return False

    def _consume_heading(self, line: str, page: PageText, buffers: _ElementBuffers) -> bool:
        active = buffers.prose
        if active.kind == "paragraph" and self._is_short_subheading_line(line, page):
            self._flush(active)
            self._interrupted_paragraph = None
            self.current_subheading = re.sub(r"\s+", " ", line).strip()
            self._add_heading(line, page)
            return True

        if (
            self.region in {"Body", "Appendix", "Annex"}
            and active.kind in {None, "text_block"}
            and self._is_short_subheading_line(line, page)
        ):
            self._flush(active)
            self._interrupted_paragraph = None
            self.current_subheading = re.sub(r"\s+", " ", line).strip()
            self._add_heading(line, page, extra={"detected_from_typography": True})
            return True

        if self._is_heading_line(line) or (
            is_all_caps_heading(line) and self.region in {"Body", "Appendix", "Annex"}
        ):
            self._flush(active)
            self._interrupted_paragraph = None
            # Contents lines are not true section headings for downstream section paths.
            if not CONTENTS_HEADING_RE.match(line):
                self.current_subheading = re.sub(r"\s+", " ", line).strip()
            self._add_heading(line, page)
            return True
        return False

    def _consume_prose(self, line: str, page: PageText, buffers: _ElementBuffers) -> None:
        active = buffers.prose
        # Continuation or residual text.
        if active.kind:
            active.append(line, page)
        elif self._interrupted_paragraph and self._can_resume_interrupted_paragraph(line):
            active.start(
                kind="paragraph",
                element_id=self._interrupted_paragraph.element_id,
                line=line,
                page=page,
                section_path=self._interrupted_paragraph.section_path,
                parent_element_id=self._interrupted_paragraph.parent_element_id,
                extra=self._interrupted_paragraph.extra,
                resume_record=self._interrupted_paragraph,
            )
            self._interrupted_paragraph = None
        elif self.include_text_blocks:
            active.start(
                kind="text_block",
                element_id=None,
                line=line,
                page=page,
                section_path=self._section_path(),
            )

    def _prepared_page_lines(self, page: PageText) -> list[str]:
        lines = [normalize_text(line) for line in page.lines]
        lines = [line for line in lines if line]
        filtered: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if line in page.tables:
                filtered.append(line)
                i += 1
                continue
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            prev_line = lines[i - 1] if i > 0 else ""

            fragments = next(
                (
                    rule["lines"]
                    for rule in self.parser_config.get("heading_continuations", [])
                    if page.pdf_page in rule["pdf_pages"]
                    and lines[i : i + len(rule["lines"])] == rule["lines"]
                ),
                None,
            )
            if fragments:
                merged = " ".join(fragments)
                self._heading_fragments[(page.pdf_page, merged)] = fragments
                filtered.append(merged)
                i += len(fragments)
                continue

            fragments = (
                self._typographic_heading_fragments(page, lines, i)
                if self.region in {"Body", "Appendix", "Annex"} or MAJOR_BODY_HEADING_RE.match(line)
                else []
            )
            if fragments:
                merged = " ".join(fragments)
                self._heading_fragments[(page.pdf_page, merged)] = fragments
                self._automatic_headings.add((page.pdf_page, merged))
                filtered.append(merged)
                i += len(fragments)
                continue

            if self._is_page_furniture_line(line, prev_line, next_line):
                if PAGE_NUMBER_RE.match(line) and PAGE_FURNITURE_PAIR_RE.match(next_line):
                    i += 2
                else:
                    i += 1
                continue

            if (
                next_line
                and next_line not in page.tables
                and self._should_merge_heading_lines(line, next_line)
            ):
                fragments = [line, next_line]
                merged = f"{line} {next_line}"
                if (
                    i + 2 < len(lines)
                    and lines[i + 2] not in page.tables
                    and self._should_merge_heading_lines(merged, lines[i + 2])
                ):
                    fragments.append(lines[i + 2])
                    merged = f"{merged} {lines[i + 2]}"
                self._heading_fragments[(page.pdf_page, merged)] = fragments
                filtered.append(merged)
                i += len(fragments)
                continue

            filtered.append(line)
            i += 1
        return remove_pdf_line_breaks(
            filtered,
            hyphenated_words=tuple(self.parser_config.get("hyphenated_words", [])),
            structural_start=lambda candidate: self._line_is_structural_candidate(candidate, page),
            preserve_after=lambda candidate: (
                candidate in page.tables
                or self._is_heading_line(candidate)
                or bool(self._configured_outline_table_start(candidate, page))
            ),
        )

    @staticmethod
    def _typographic_heading_fragments(page, lines, start):
        """Join an isolated, aligned run of capitalized bold heading lines."""
        if not (is_all_caps_heading(lines[start]) or MAJOR_BODY_HEADING_RE.match(lines[start])):
            return []
        evidence = []
        for text in lines[start : start + 6]:
            matches = [s for s in page.source_lines if normalize_text(s["text"]) == text]
            if len(matches) != 1:
                break
            source = matches[0]
            spans = [s for s in page.source_typography.get(source["line"], []) if s["text"].strip()]
            if not spans or not all(s["flags"] & 16 for s in spans):
                break
            if evidence and (not text.isupper() or MAJOR_BODY_HEADING_RE.match(text)):
                break
            size = max(s["size"] for s in spans)
            if evidence:
                previous, prior_size = evidence[-1]
                gap = source["bbox"][1] - previous["bbox"][3]
                aligned = (
                    abs(source["bbox"][0] - previous["bbox"][0]) <= 2
                    or abs(sum(source["bbox"][::2]) - sum(previous["bbox"][::2])) <= 4
                )
                if abs(size - prior_size) > 0.5 or not aligned or not -2 <= gap <= size * 0.45:
                    break
            evidence.append((source, size))
        if len(evidence) < 2:
            return []
        first, last = evidence[0][0], evidence[-1][0]
        outside = [s for s in page.source_lines if s not in [item[0] for item in evidence]]
        before = [s["bbox"][3] for s in outside if s["bbox"][3] <= first["bbox"][1]]
        after = [s["bbox"][1] for s in outside if s["bbox"][1] >= last["bbox"][3]]
        if (before and first["bbox"][1] - max(before) < 4) or (
            after and min(after) - last["bbox"][3] < 4
        ):
            return []
        return lines[start : start + len(evidence)]

    def _line_is_structural_candidate(self, line: str, page: PageText) -> bool:
        return bool(
            line in page.tables
            or (
                (self.region == "Glossary" or any(GLOSSARY_HEADING_RE.match(s) for s in page.lines))
                and any(line.startswith(opening) for opening in page.glossary_openings.values())
            )
            or (self._lettered_list_page(page) and re.match(r"^\([a-z]\)(?:\s|$)", line))
            or self._paragraph_match(line, page)
            or self._glossary_term(line)
            or is_prefixed_reference(line)
            or self._table_match(line, page)
            or self._figure_match(line, page)
            or MAJOR_BODY_HEADING_RE.match(line)
            or FOOTNOTE_RE.match(line)
            or self._footnote_match(line)
            or REQUIREMENT_RE.match(line)
            or REFERENCE_ITEM_RE.match(line)
            or APPENDIX_HEADING_RE.match(line)
            or ANNEX_HEADING_RE.match(line)
            or REFERENCES_HEADING_RE.match(line)
            or GLOSSARY_HEADING_RE.match(line)
            or RELATED_PUBLICATIONS_RE.match(line)
            or CONTENTS_HEADING_RE.match(line)
            or BACKMATTER_HEADING_RE.match(line)
            or self._configured_local_scope(line, page)
            or self._configured_outline_table_start(line, page)
            or self._looks_like_outline_table_line(line)
            or re.match(r"^[A-Z]\.\s*$", line)
            or line in KNOWN_PUBLICATION_HEADINGS
            or self._is_heading_line(line)
            or is_all_caps_heading(line)
            or self._is_short_subheading_line(line, page)
        )

    def _is_page_furniture_line(self, line: str, prev_line: str, next_line: str) -> bool:
        if self._current_page and self._pdf_note_body(line, self._current_page):
            return False
        note = FOOTNOTE_RE.match(line)
        if (
            note
            and self._current_page
            and any(
                self._current_page.pdf_page in item["pdf_pages"]
                and item["footnote_id"] == note.group("num")
                for item in self.parser_config.get("footnote_anchors", [])
            )
        ):
            # A reviewed note beginning with the publisher name is not a footer.
            return False
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
        if (
            is_prefixed_reference(line)
            or is_prefixed_reference(next_line)
            or match_paragraph_label(next_line)
            or match_table_label(next_line)
            or match_figure_label(next_line)
            or REQUIREMENT_RE.match(next_line)
            or APPENDIX_HEADING_RE.match(next_line)
            or ANNEX_HEADING_RE.match(next_line)
            or REFERENCES_HEADING_RE.match(next_line)
            or GLOSSARY_HEADING_RE.match(next_line)
            or RELATED_PUBLICATIONS_RE.match(next_line)
            or BACKMATTER_HEADING_RE.match(next_line)
        ):
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
        next_words = re.findall(r"[A-Za-z]+", next_line.upper())
        if major and next_words and next_words[0] in {*CONTINUATION_HEADING_WORDS, "OUT"}:
            return True
        return not major and is_all_caps_heading(line) and len(line) >= 28 and len(next_line) >= 8

    def _is_heading_line(self, line: str) -> bool:
        """Recognise known front-matter headings without treating country lists as headings."""
        if self._current_page and (self._current_page.pdf_page, line) in self._automatic_headings:
            return True
        if self._current_page and any(
            self._current_page.pdf_page in rule["pdf_pages"] and line == " ".join(rule["lines"])
            for rule in self.parser_config.get("heading_continuations", [])
        ):
            return True
        if line in KNOWN_PUBLICATION_HEADINGS or (
            self.region == "Glossary" and line == "DEFINITIONS"
        ):
            return True
        return False

    def _glossary_term(self, line: str) -> str | None:
        """Keep source-verified definition labels with their own prose and notes."""
        if self.region == "Glossary":
            return next(
                (
                    term
                    for term, opening in {
                        **(self._current_page.glossary_openings if self._current_page else {}),
                        **self.parser_config.get("glossary_terms", {}),
                    }.items()
                    if line.startswith(opening)
                ),
                None,
            )
        return None

    def _skip_line(self, line: str) -> bool:
        # Printer's continuation directions are page navigation, not headings.
        if re.fullmatch(r"Text cont\. on p\. \d+\.", line):
            return True
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
        reference_heading = REFERENCES_HEADING_RE.match(line)
        self.region = new_region
        self.current_local_scope = None
        if new_region == "Annex":
            m = ANNEX_HEADING_RE.match(line)
            self.current_annex = f"Annex {m.group('num')}" if m and m.group("num") else "Annex"
            self.current_major_heading = self.current_annex
            self.current_subheading = None
        elif new_region == "Appendix":
            m = APPENDIX_HEADING_RE.match(line)
            self.current_major_heading = (
                f"Appendix {m.group('num')}" if m and m.group("num") else "Appendix"
            )
            self.current_subheading = None
        elif (
            new_region == "References"
            and reference_heading
            and reference_heading.group("scope")
            and self.current_major_heading
            and self.current_major_heading.startswith(reference_heading.group("scope").title())
        ):
            # Annex/appendix bibliographies remain under their source section.
            self.current_subheading = line
        elif new_region in {"References", "Glossary", "BackMatter"}:
            self.current_major_heading = new_region
            self.current_subheading = None
        boundary = self.parser_config.get("page_regions", {}).get(page.pdf_page)
        if (
            isinstance(boundary, dict)
            and boundary.get("region") == new_region
            and boundary.get("section") == line
        ):
            self.current_major_heading = line
        self._add_heading(line, page)

    def _line_starts_new_non_table_element(self, line: str, page: PageText | None = None) -> bool:
        return bool(
            self._figure_match(line, page)
            or REQUIREMENT_RE.match(line)
            or self._paragraph_match(line, page)
            or MAJOR_BODY_HEADING_RE.match(line)
            or self._detect_region_transition(line)
            or self._is_heading_line(line)
        )

    def _should_treat_as_body_heading(self, line: str, match, page: PageText) -> bool:
        if self._is_contents_noise(line, page):
            return False
        para_match = BODY_PARA_RE.match(line)
        if para_match and re.match(r"^\d{3}[A-Z]?(?:\.\d+)*$", para_match.group("id")):
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
        return bool(page.printed_page and re.match(r"^\d{3}[A-Z]?(?:\.\d+)*$", para_id))

    def _enter_body(self) -> None:
        self.region = "Body"
        self.body_started = True
        self.in_contents = False

    def _paragraph_match(self, line: str, page: PageText | None = None) -> StructuralLabel | None:
        match = match_paragraph_label(line)
        if match:
            return match
        return self._configured_label_match("paragraph", line, page)

    def _table_match(self, line: str, page: PageText | None = None) -> StructuralLabel | None:
        match = match_table_label(line)
        if match:
            return match
        return self._configured_label_match("table", line, page)

    def _figure_match(self, line: str, page: PageText | None = None) -> StructuralLabel | None:
        match = match_figure_label(line)
        if match:
            return match
        return self._configured_label_match("figure", line, page)

    def _configured_label_match(
        self,
        kind: str,
        line: str,
        page: PageText | None,
    ) -> StructuralLabel | None:
        active_page = page or self._current_page
        for exception in self.label_exceptions:
            if str(exception.get("element_type", "paragraph")) != kind:
                continue
            pages = exception.get("pdf_pages") or [exception.get("pdf_page")]
            pages = {int(value) for value in pages if value not in (None, "")}
            if pages and (not active_page or active_page.pdf_page not in pages):
                continue
            expected_region = exception.get("region")
            if expected_region and self.region != expected_region:
                continue
            raw_label = str(exception.get("raw_label", "")).strip()
            canonical_id = str(exception.get("canonical_id") or raw_label).strip()
            if not raw_label or not canonical_id:
                continue
            configured = match_unterminated_label(
                line,
                kind=kind,  # type: ignore[arg-type]
                raw_label=raw_label,
                canonical_id=canonical_id,
            )
            if configured:
                return configured
        return None

    def _configured_local_scope(self, line: str, page: PageText) -> str | None:
        for rule in self.local_scope_patterns:
            pages = rule.get("pdf_pages") or []
            if pages and page.pdf_page not in {int(value) for value in pages}:
                continue
            flags = 0 if rule.get("case_sensitive") else re.I
            match = re.match(str(rule.get("pattern", "")), line, flags=flags)
            if not match:
                continue
            template = str(rule.get("id_template", "{scope}"))
            values = {**match.groupdict(), "match": match.group(0)}
            try:
                return template.format(**values)
            except KeyError:
                return match.group(0)
        return None

    def _is_body_paragraph_id(self, para_id: str) -> bool:
        return bool(re.match(r"^(?:\d+(?:\.\d+)+|\d{3}[A-Z]?(?:\.\d+)*)$", para_id))

    def _is_contents_entry(self, line: str) -> bool:
        return ". . ." in line or bool(re.search(r"\.{3,}", line))

    def _is_contents_noise(self, line: str, page: PageText) -> bool:
        return self._is_contents_entry(line) or (self.in_contents and page.printed_page is None)

    def _is_body_start_after_contents(self, line: str, page: PageText) -> bool:
        major = MAJOR_BODY_HEADING_RE.match(line)
        if major:
            title = re.sub(r"\s+", " ", major.group("title")).strip().upper()
            if major.group("num") == "1" and title.startswith("INTRODUCTION"):
                return True
        paragraph = self._paragraph_match(line, page)
        if page.printed_page != "1" or not paragraph:
            return False
        paragraph_id = paragraph.canonical_id
        return paragraph_id.startswith("1.") or bool(re.match(r"^1\d{2}(?:\.|$)", paragraph_id))

    def _footnote_match(self, line: str):
        if not self._footnotes_allowed_on_page():
            return None
        m = FOOTNOTE_RE.match(line)
        if not m and self._current_page:
            # Reviewed notes can begin with a URL or lowercase text. Keep the
            # general heuristic conservative for unreviewed numbered lines.
            candidate = re.match(r"^(?P<num>\d{1,2})\s+(?P<text>\S[^\n]+)$", line)
            if candidate and (
                self._pdf_note_body(line, self._current_page)
                or any(
                    self._current_page.pdf_page in anchor["pdf_pages"]
                    and anchor["footnote_id"] == candidate.group("num")
                    for anchor in self.parser_config.get("footnote_anchors", [])
                )
            ):
                m = candidate
        if not m:
            return None
        # Avoid treating numbered list items and table rows as footnotes.
        if self._paragraph_match(line) or self._table_match(line):
            return None
        # Footnotes are most often in body/appendix/annex pages. Keep as low-confidence if detected.
        return m

    def _footnotes_allowed_on_page(self) -> bool:
        pages = self.parser_config.get("footnote_pages")
        return pages is None or bool(self._current_page and self._current_page.pdf_page in pages)

    def _is_short_subheading_line(self, line: str, page: PageText | None = None) -> bool:
        if not line or line.endswith(";"):
            return False
        if self._line_starts_new_non_table_element(line, page) or self._footnote_match(line):
            return False
        words = line.split()
        if not (1 <= len(words) <= 20) or len(line) > 180:
            return False
        letters = [ch for ch in line if ch.isalpha()]
        if len(letters) < 4:
            return False
        if is_all_caps_heading(line):
            return True
        active_page = page or self._current_page
        if not active_page:
            return False
        return self._is_typographic_heading_line(line, active_page)

    def _is_typographic_heading_line(self, line: str, page: PageText | None = None) -> bool:
        active_page = page or self._current_page
        if not active_page:
            return False
        normalized = normalize_text(line)
        return normalized in {
            normalize_text(value) for value in active_page.typographic_heading_lines
        }

    def _looks_like_table_cell_paragraph(self, line: str) -> bool:
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
            or re.match(r"^TABLE\s+\d+[A-Za-z]?:\s*$", line, flags=re.I)
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

    def _configured_outline_table_start(self, line: str, page: PageText) -> tuple[str, str] | None:
        for rule in self.outline_regions:
            page_range = rule.get("pdf_page_range") or []
            if len(page_range) == 2 and not (
                int(page_range[0]) <= page.pdf_page <= int(page_range[1])
            ):
                continue
            pages = rule.get("pdf_pages") or []
            if pages and page.pdf_page not in {int(value) for value in pages}:
                continue
            pattern = str(rule.get("start_pattern", ""))
            if pattern and not re.match(pattern, line, flags=re.I):
                continue
            return str(rule.get("element_id", "CONFIGURED OUTLINE")), str(rule.get("title", line))
        return None

    def _outline_table_start_from_active(
        self, active: _ActiveElement, line: str
    ) -> tuple[str, str] | None:
        if self.region != "Annex" or active.kind != "paragraph":
            return None
        if not self._looks_like_outline_table_line(line):
            return None
        active_text = self._clean_element_text(" ".join(active.lines))
        if not re.search(r"\bfollowing outline:\s*$", active_text, flags=re.I):
            return None
        return f"OUTLINE {next(self.synthetic_table_counter):04d}", "Outline"

    def _should_keep_active_table_line(self, active_table: _ActiveElement, line: str) -> bool:
        if self._looks_like_table_header_line(line) or self._looks_like_table_cell_paragraph(line):
            return True
        if self._is_synthetic_table(active_table) and self._looks_like_outline_table_line(line):
            return True
        return False

    def _should_end_active_table(self, active_table: _ActiveElement, line: str) -> bool:
        if not self._is_synthetic_table(active_table):
            return False
        if active_table.title and active_table.title.lower() == "module outline":
            return bool(re.match(r"^[A-Z]\.$", line))
        return False

    def _is_synthetic_table(self, active_table: _ActiveElement) -> bool:
        return bool(
            active_table.extra.get("configured_outline")
            or (
                active_table.element_id
                and active_table.element_id.startswith(
                    ("MODULE OUTLINE", "TYPICAL TABLE OF CONTENTS", "OUTLINE ")
                )
            )
        )

    def _looks_like_multilevel_outline_number(self, line: str) -> bool:
        return bool(re.match(r"^\d+(?:\.\d+){1,8}\.\s*(?:\S.*)?$", line))

    def _looks_like_outline_table_line(self, line: str) -> bool:
        if self._looks_like_multilevel_outline_number(line):
            return True
        return bool(
            re.match(r"^\d+(?:\.\d+)+\s+\S", line)
            or re.match(r"^\d+\.\s+\S", line)
            or re.match(r"^\d+\.$", line)
            or re.match(r"^CHAPTER\s+\d+[:.]\s+\S", line, flags=re.I)
        )

    def _section_path(self) -> list[str]:
        out: list[str] = []
        if self.current_major_heading:
            out.append(self.current_major_heading)
        if self.current_subheading and self.current_subheading not in out:
            parents = (
                self.parser_config.get("subheading_parents", {})
                .get(self.current_major_heading, {})
                .get(self.current_subheading, [])
            )
            if isinstance(parents, dict):
                parents = parents.get(self._current_page.pdf_page, [])
            out.extend(parent for parent in parents if parent not in out)
            out.append(self.current_subheading)
        return out

    def _add_heading(
        self, line: str, page: PageText, *, extra: dict[str, Any] | None = None
    ) -> None:
        heading_extra = dict(extra or {})
        fragments = self._heading_fragments.get((page.pdf_page, line))
        if fragments:
            heading_extra["source_fragments"] = fragments
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
            extra=heading_extra,
        )

    def _flush(self, active: _ActiveElement) -> None:
        if not active.kind:
            return
        text = self._clean_element_text(" ".join(active.lines))
        if active.resume_record is not None:
            if text:
                continuation, footnotes = self._extract_embedded_footnotes(text)
                if continuation:
                    active.resume_record.text = self._clean_element_text(
                        f"{active.resume_record.text} {continuation}"
                    )
                    active.resume_record.page_end_pdf = active.page_end_pdf
                    active.resume_record.page_end_printed = active.page_end_printed
                    existing = active.resume_record.extra.setdefault("source_spans", [])
                    existing.extend(
                        s for s in active.extra.get("source_spans", []) if s not in existing
                    )
                    note = "Paragraph resumed after an intervening table or figure."
                    if note not in active.resume_record.parser_notes:
                        active.resume_record.parser_notes.append(note)
                for footnote_id, footnote_text in footnotes:
                    self._add_record(
                        element_type="footnote",
                        element_id=footnote_id,
                        source_region=active.resume_record.source_region,
                        section_path=active.resume_record.section_path,
                        page_start_pdf=active.page_start_pdf,
                        page_end_pdf=active.page_end_pdf,
                        page_start_printed=active.page_start_printed,
                        page_end_printed=active.page_end_printed,
                        text=footnote_text,
                        linked_from_element_id=active.resume_record.element_id,
                        confidence="low",
                        parser_notes=[
                            "Footnote body split from a paragraph continuation after a floating table or figure."
                        ],
                    )
        elif text:
            self._add_text_records_from_active(active, text)
        for footnote in active.following_footnotes:
            self._add_record(**footnote)
        active.reset()

    def _flush_before_floating_element(self, active: _ActiveElement) -> None:
        """Remember incomplete prose, or an explicitly enabled lettered list."""
        if not active.kind:
            return
        text = " ".join(active.lines)
        should_resume = active.kind == "paragraph" and (
            self._text_is_incomplete(text)
            or (
                self._lettered_list_page(self._current_page) and re.search(r"(?:^|\s)\(a\)\s", text)
            )
        )
        element_id = active.element_id
        first_new_record = len(self.records)
        self._flush(active)
        self._interrupted_paragraph = None
        if not should_resume:
            return
        for record in reversed(self.records[first_new_record:]):
            if record.element_type == "paragraph" and record.element_id == element_id:
                self._interrupted_paragraph = record
                return

    def _can_resume_interrupted_paragraph(self, line: str) -> bool:
        record = self._interrupted_paragraph
        if not record:
            return False
        if self._line_starts_new_non_table_element(line, self._current_page):
            return False
        if self._is_short_subheading_line(line, self._current_page):
            return False
        if self._lettered_list_page(self._current_page) and not self._text_is_incomplete(
            record.text
        ):
            return self._lettered_list_continues(record.text, line)
        return self._text_is_incomplete(record.text) or bool(
            re.match(
                r"^(?:and|or|but|for|nor|so|yet|which|that|who|whom|whose|where|when|because|while|as|to)\b",
                line,
            )
        )

    def _lettered_list_page(self, page: PageText) -> bool:
        return page.pdf_page in self.parser_config.get("lettered_list_continuation_pages", [])

    def _lettered_list_continues(self, text: str, line: str) -> bool:
        """A reviewed list can resume only at its next consecutive letter."""
        if not self._lettered_list_page(self._current_page):
            return False
        labels = re.findall(r"(?:^|\s)\(([a-z])\)\s", text)
        following = re.match(r"^\(([a-z])\)(?:\s|$)", line)
        return bool(
            labels
            and following
            and labels == list("abcdefghijklmnopqrstuvwxyz"[: len(labels)])
            and ord(following[1]) == ord(labels[-1]) + 1
        )

    def _should_resume_interrupted_paragraph_after_table(
        self,
        active_table: _ActiveElement,
        line: str,
        page: PageText,
    ) -> bool:
        """Recognize prose continued on the page after an inserted table."""
        record = self._interrupted_paragraph
        if not record:
            return False
        # A table and its notes can interrupt a list on the same physical page.
        if self._lettered_list_continues(record.text, line):
            return True
        if page.pdf_page <= active_table.page_start_pdf:
            return False
        if not self._text_is_incomplete(record.text) or not re.match(r"^[a-z]", line):
            return False
        if len(line) < 45 or self._line_starts_new_non_table_element(line, page):
            return False
        return True

    @staticmethod
    def _text_is_incomplete(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text).strip()
        return bool(normalized) and not bool(re.search(r"[.!?;:][’”\"')\]]*$", normalized))

    def _flush_table(self, active: _ActiveElement) -> None:
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
                parent_element_id=active.parent_element_id,
                confidence="medium",
                parser_notes=[
                    "Table captured as raw PDF text. Verify complex row/column boundaries manually or with page image review."
                ],
                extra=active.extra,
            )
        active.reset()

    def _flush_footnote(self, active: _ActiveElement, prose: _ActiveElement) -> None:
        if active.kind != "footnote":
            active.reset()
            return
        text = self._clean_element_text(" ".join(active.lines))
        if text:
            footnote = dict(
                element_type="footnote",
                element_id=active.element_id,
                source_region=active.extra.get("source_anchor", {}).get(
                    "source_region", self.region
                ),
                section_path=active.section_path,
                page_start_pdf=active.page_start_pdf,
                page_end_pdf=active.page_end_pdf,
                page_start_printed=active.page_start_printed,
                page_end_printed=active.page_end_printed,
                text=text,
                linked_from_element_id=active.linked_from_element_id,
                confidence="low",
                parser_notes=[
                    "Footnote detected by line pattern; verify if the page contains multiple footnotes."
                ],
                extra=active.extra,
            )
            # Emit a footer note after the prose that precedes it, even when
            # that prose remains open for a continuation on the next page.
            if prose.kind:
                prose.following_footnotes.append(footnote)
            else:
                self._add_record(**footnote)
        active.reset()

    def _clean_element_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        # Common PDF line-break hyphenation cleanup for ordinary words. Preserve identifiers using en dash.
        text = re.sub(r"([a-z])‑\s+((?!and\b|or\b)[a-z])", r"\1\2", text)
        return text

    def _add_text_records_from_active(self, active: _ActiveElement, text: str) -> None:
        for kind, element_id, segment_text in self._split_requirement_segments(
            active.kind, active.element_id, text
        ):
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
                    parent_element_id=active.parent_element_id,
                    confidence="medium" if kind in {"paragraph", "requirement"} else "low",
                    extra=active.extra,
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
                    parser_notes=[
                        "Footnote body split from running paragraph text; verify anchor placement if exact citation is required."
                    ],
                )

    def _split_requirement_segments(
        self, kind: str, element_id: str | None, text: str
    ) -> list[tuple[str, str | None, str]]:
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
        if not self._footnotes_allowed_on_page():
            return text, []
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
            body, trailing = self._split_footnote_body(
                body_segment, has_next=idx + 1 < len(matches)
            )
            if body:
                footnotes.append((match.group("num"), self._clean_element_text(body)))
            if trailing:
                clean_parts.append(trailing)
            cursor = segment_end
        clean_parts.append(text[cursor:])
        cleaned = self._clean_element_text(
            " ".join(part.strip() for part in clean_parts if part.strip())
        )
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
        extra = dict(extra or {})
        if "source_spans" not in extra and self._current_page:
            extra["source_spans"] = list(self._current_page.current_source_spans)
        # Splitting embedded requirements/footnotes can narrow the original
        # buffer. Only retain line matches that still support this record.
        if element_type != "table":
            source_text = re.sub(r"\s+", "", extra.get("source_label", "") + text)
            matching = []
            for span in extra.get("source_spans", []):
                page = self._pages_by_number[span["pdf_page"]]
                source = page.source_lines[span["line"]]
                fragment = re.sub(r"\s+", "", source["text"])
                if fragment in source_text or (
                    fragment.endswith("-") and fragment[:-1] in source_text
                ):
                    matching.append(span)
            extra["source_spans"] = matching
        transcribed_pages = {}
        for span in extra.get("source_spans", []):
            source = self._pages_by_number[span["pdf_page"]].source_lines[span["line"]]
            if source.get("role") == "reviewed_image_transcription":
                transcribed_pages[span["pdf_page"]] = source["transcription"]
        if transcribed_pages:
            extra["reviewed_image_transcriptions"] = [
                {"pdf_page": n, "geometry": "whole_page", **review}
                for n, review in transcribed_pages.items()
            ]
        text_status, reason = classify_status(
            element_type=element_type,
            source_region=source_region,
            element_id=element_id,
            section_path=section_path,
        )
        n = next(self.counter)
        element_key = element_id or f"{element_type}-{n:05d}"
        record_id = (
            f"{self.meta.document_id}:{element_type}:{element_key}:p{page_start_pdf}:r{n:05d}"
        )
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
        self.linked_from_element_id: str | None = None
        self.parent_element_id: str | None = None
        self.extra: dict[str, Any] = {}
        self.resume_record: StructuralElement | None = None
        self.following_footnotes: list[dict[str, Any]] = []

    def start(
        self,
        *,
        kind: str,
        element_id: str | None,
        line: str,
        page: PageText,
        section_path: list[str],
        title: str | None = None,
        linked_from_element_id: str | None = None,
        parent_element_id: str | None = None,
        extra: dict[str, Any] | None = None,
        resume_record: StructuralElement | None = None,
    ) -> None:
        self.kind = kind
        self.element_id = element_id
        self.lines = [line] if line else []
        self.section_path = list(section_path)
        self.page_start_pdf = page.pdf_page
        self.page_end_pdf = page.pdf_page
        self.page_start_printed = page.printed_page
        self.page_end_printed = page.printed_page
        self.title = title
        self.linked_from_element_id = linked_from_element_id
        self.parent_element_id = parent_element_id
        self.extra = dict(extra or {})
        self.extra["source_spans"] = list(self.extra.get("source_spans", []))
        self._extend_spans(page.current_source_spans)
        self.resume_record = resume_record

    def append(self, line: str, page: PageText) -> None:
        self.lines.append(line)
        self.page_end_pdf = page.pdf_page
        self.page_end_printed = page.printed_page
        self._extend_spans(page.current_source_spans)

    def _extend_spans(self, spans: list[dict[str, Any]]) -> None:
        existing = self.extra.setdefault("source_spans", [])
        keys = {(span["pdf_page"], span["line"]) for span in existing}
        existing.extend(span for span in spans if (span["pdf_page"], span["line"]) not in keys)


class _ElementBuffers:
    """Elements that may be open simultaneously while reading a page."""

    def __init__(self) -> None:
        self.prose = _ActiveElement()
        self.table = _ActiveElement()
        self.footnote = _ActiveElement()
