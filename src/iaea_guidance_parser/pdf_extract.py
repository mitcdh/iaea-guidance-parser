from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal test envs
    fitz = None

from .models import PageText
from .provenance import sha256_file
from .rules import (
    CONTENTS_HEADING_RE,
    PAGE_NUMBER_RE,
    match_figure_label,
    match_table_label,
    normalize_text,
)


@dataclass(frozen=True)
class _PrintedPageCandidate:
    value: str
    edge: str


@dataclass
class _RawPage:
    pdf_page: int
    lines: list[str]
    candidate: _PrintedPageCandidate | None
    bold_lines: list[str] | None = None
    typographic_heading_lines: list[str] | None = None
    suppressed_figure_lines: list[str] | None = None
    source_lines: list[dict[str, Any]] | None = None
    tables: dict[str, dict[str, Any]] | None = None
    figure_regions: list[dict[str, Any]] | None = None
    source_typography: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    figure_regions_by_caption: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    figure_warnings: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _LineInfo:
    text: str
    bbox: tuple[float, float, float, float]
    predominantly_bold: bool
    predominantly_emphasized: bool
    spans: tuple[dict[str, Any], ...] = ()


def extract_pages(pdf_path: Path, parser_config: dict[str, Any] | None = None) -> list[PageText]:
    """Extract page text and rough printed page numbers from a PDF."""
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF is required to extract PDF text. Install the `pymupdf` package."
        )
    doc = fitz.open(pdf_path)
    parser_config = parser_config or {}
    transcriptions = parser_config.get("image_transcriptions", {})
    if transcriptions and transcriptions.get("source_sha256") != sha256_file(pdf_path):
        doc.close()
        raise ValueError("Image transcriptions must match the source PDF SHA-256")
    if any(n not in range(1, len(doc) + 1) for n in transcriptions.get("pages", {})):
        doc.close()
        raise ValueError("Image transcription page is outside the source PDF")
    figure_layouts = parser_config.get("figure_layouts", {})
    if figure_layouts and figure_layouts.get("source_sha256") != sha256_file(pdf_path):
        doc.close()
        raise ValueError("Reviewed figure layouts must match the source PDF SHA-256")
    if any(n not in range(1, len(doc) + 1) for n in figure_layouts.get("pages", {})):
        doc.close()
        raise ValueError("Reviewed figure page is outside the source PDF")
    decoder_rules = parser_config.get("font_decoders", []) or []
    raw_pages: list[_RawPage] = []
    try:
        for idx, page in enumerate(doc):
            pdf_page = idx + 1
            if pdf_page in transcriptions.get("pages", {}):
                transcript = transcriptions["pages"][pdf_page]
                raw_pages.append(_transcribed_image_page(page, pdf_page, transcript))
                continue
            ignore_actual_text = pdf_page in parser_config.get("ignore_actual_text_pages", [])
            raw = _extract_page_text(
                page,
                pdf_page,
                decoder_rules,
                sort_lines=pdf_page in parser_config.get("reading_order_pages", []),
                ignore_actual_text=ignore_actual_text,
            )
            lines = [normalize_text(line) for line in raw.splitlines()]
            lines = [line for line in lines if line]
            line_info = _extract_line_info(
                page, pdf_page, decoder_rules, ignore_actual_text=ignore_actual_text
            )
            rotation = parser_config.get("page_reading_rotations", {}).get(pdf_page, 0)
            if rotation not in (0, 90, 180, 270):
                raise ValueError("Page reading rotation must be a multiple of 90 degrees")
            if rotation:
                line_info.sort(key=lambda item: _reading_order_key(item, rotation))
            line_info, repaired_caption = _join_caption_fragments(line_info)
            layouts = [
                rule
                for rule in parser_config.get("table_layouts", [])
                if pdf_page in rule["pdf_pages"]
            ]
            if layouts:
                tables, table_indices = _reviewed_table_layouts(
                    page,
                    line_info,
                    pdf_page,
                    decoder_rules,
                    layouts,
                    rotation,
                    ignore_actual_text=ignore_actual_text,
                )
            else:
                tables, table_indices = _extract_tables(
                    page, line_info, pdf_page, decoder_rules, ignore_actual_text=ignore_actual_text
                )
            reviewed_figures = figure_layouts.get("pages", {}).get(pdf_page, [])
            transcribed_regions = []
            figure_owners, figure_warnings = {}, {}
            if reviewed_figures:
                suppressed_indices, transcribed_regions = _reviewed_figure_layouts(
                    page, line_info, pdf_page, reviewed_figures
                )
                for index in suppressed_indices:
                    rule = next(
                        rule
                        for rule in reviewed_figures
                        if _inside(line_info[index].bbox, rule["bbox"])
                    )
                    figure_owners[index] = match_figure_label(rule["caption"]).canonical_id
            else:
                suppressed_indices = _figure_interior_line_indices(
                    page, line_info, owners=figure_owners, warnings=figure_warnings
                )
            # A table can contain diagrams or a figure reference. Its cells
            # already preserve the complete content and take precedence here.
            suppressed_indices -= set(table_indices)
            suppressed_figure_lines = [
                normalize_text(item.text)
                for index, item in enumerate(line_info)
                if index in suppressed_indices
            ]
            if rotation or suppressed_indices or tables or repaired_caption:
                # PyMuPDF's plain-text block order can place body prose before a
                # diagram that is visually above it.  Geometry-sorted rich lines
                # are authoritative on pages where a figure region was found.
                lines = []
                inserted = set()
                for index, item in enumerate(line_info):
                    if index in suppressed_indices:
                        continue
                    token = table_indices.get(index)
                    if token:
                        if token not in inserted:
                            lines.append(token)
                            inserted.add(token)
                    elif normalize_text(item.text):
                        lines.append(normalize_text(item.text))
            bold_lines = [
                normalize_text(item.text)
                for index, item in enumerate(line_info)
                if item.predominantly_bold
                and index not in suppressed_indices
                and normalize_text(item.text)
            ]
            typographic_heading_lines = _typographic_heading_lines(line_info, suppressed_indices)
            candidate = _printed_page_candidate(page, len(doc), lines)
            source_lines = [
                {
                    "pdf_page": pdf_page,
                    "line": index,
                    "bbox": list(item.bbox),
                    "text": normalize_text(item.text),
                    "role": "table"
                    if index in table_indices
                    else "figure"
                    if index in suppressed_indices
                    else "text",
                }
                for index, item in enumerate(line_info)
            ]
            figure_regions = [source_lines[index] for index in sorted(suppressed_indices)]
            if reviewed_figures:
                for region in figure_regions:
                    region["reviewed_layout"] = True
                figure_regions.extend(transcribed_regions)
                suppressed_figure_lines.extend(region["text"] for region in transcribed_regions)
            by_caption = {}
            for index in sorted(suppressed_indices):
                by_caption.setdefault(figure_owners[index], []).append(source_lines[index])
            for region in transcribed_regions:
                owner = region.pop("_owner_caption")
                by_caption.setdefault(owner, []).append(region)
            raw_pages.append(
                _RawPage(
                    pdf_page=pdf_page,
                    lines=lines,
                    candidate=candidate,
                    bold_lines=bold_lines,
                    typographic_heading_lines=typographic_heading_lines,
                    suppressed_figure_lines=suppressed_figure_lines,
                    source_lines=source_lines,
                    tables=tables,
                    figure_regions=figure_regions,
                    source_typography={i: list(item.spans) for i, item in enumerate(line_info)},
                    figure_regions_by_caption=by_caption,
                    figure_warnings=figure_warnings,
                )
            )
    finally:
        doc.close()

    pages: list[PageText] = []
    validated_candidates = _validated_printed_page_candidates(raw_pages)
    for raw_page in raw_pages:
        lines = list(raw_page.lines)
        printed_page: str | None = None
        is_contents_page = any(CONTENTS_HEADING_RE.match(line) for line in lines[:3])
        candidate = validated_candidates.get(raw_page.pdf_page)
        if candidate and len(lines) > 1 and not is_contents_page:
            printed_page = candidate.value
            _remove_printed_page_line(lines, candidate)
        text = "\n".join(lines)
        pages.append(
            PageText(
                pdf_page=raw_page.pdf_page,
                printed_page=printed_page,
                text=text,
                lines=lines,
                bold_lines=list(raw_page.bold_lines or []),
                typographic_heading_lines=list(raw_page.typographic_heading_lines or []),
                suppressed_figure_lines=list(raw_page.suppressed_figure_lines or []),
                source_lines=raw_page.source_lines or [],
                tables=raw_page.tables or {},
                figure_regions=raw_page.figure_regions or [],
                source_typography=raw_page.source_typography,
                glossary_openings=_glossary_openings(raw_page.source_typography),
                figure_regions_by_caption=raw_page.figure_regions_by_caption,
                figure_warnings=raw_page.figure_warnings,
            )
        )
    return pages


def _transcribed_image_page(page, pdf_page, transcript):
    """Use an explicitly reviewed transcript only on a page with no PDF text.

    Spans cover the whole source page: they do not claim glyph-level geometry.
    The provenance travels with the records so this cannot look like extraction.
    """
    if page.get_text().strip() or not (page.get_image_info() or page.get_drawings()):
        raise ValueError("Image transcription requires an image-only source page")
    if not transcript.get("reviewer") or not transcript.get("notes"):
        raise ValueError("Image transcription requires reviewer and source notes")
    lines = [normalize_text(line) for line in transcript.get("lines", [])]
    if not lines or not all(lines):
        raise ValueError("Image transcription requires non-empty source lines")
    provenance = {key: transcript[key] for key in ("reviewer", "notes")}
    return _RawPage(
        pdf_page=pdf_page,
        lines=lines,
        candidate=None,
        bold_lines=[],
        typographic_heading_lines=[],
        suppressed_figure_lines=[],
        source_lines=[
            {
                "pdf_page": pdf_page,
                "line": index,
                "bbox": list(page.rect),
                "text": text,
                "role": "reviewed_image_transcription",
                "transcription": provenance,
            }
            for index, text in enumerate(lines)
        ],
        tables={},
        figure_regions=[],
    )


def _validated_printed_page_candidates(
    raw_pages: list[_RawPage],
) -> dict[int, _PrintedPageCandidate]:
    """Reject isolated margin numerals that disagree with surrounding pagination."""
    candidates = {page.pdf_page: page.candidate for page in raw_pages if page.candidate}
    if len(candidates) < 3:
        return candidates

    offsets = Counter(page - int(candidate.value) for page, candidate in candidates.items())
    supported_offsets = {offset for offset, count in offsets.items() if count >= 3}
    validated: dict[int, _PrintedPageCandidate] = {}
    for pdf_page, candidate in candidates.items():
        value = int(candidate.value)
        has_sequential_neighbour = any(
            neighbour_page in candidates
            and int(candidates[neighbour_page].value) - value == neighbour_page - pdf_page
            for neighbour_page in (pdf_page - 1, pdf_page + 1)
        )
        if has_sequential_neighbour or pdf_page - value in supported_offsets:
            validated[pdf_page] = candidate
    return validated


def _printed_page_candidate(
    page: Any, page_count: int, lines: list[str]
) -> _PrintedPageCandidate | None:
    """Return a plausible numeral physically located in a page margin.

    PDF text order alone is unsafe for tables: isolated values such as ``2400``
    can appear first or last in extracted text.  Geometry plus a corpus-bounded
    plausibility check avoids consuming those values as page furniture.
    """
    if not lines:
        return None
    height = float(page.rect.height or 1)
    candidates: list[tuple[float, _PrintedPageCandidate]] = []
    for block in page.get_text("blocks", sort=True):
        _x0, y0, _x1, y1, text = block[:5]
        block_lines = [normalize_text(line) for line in str(text).splitlines()]
        block_lines = [line for line in block_lines if line]
        if not block_lines or len(set(block_lines)) != 1:
            continue
        match = PAGE_NUMBER_RE.fullmatch(block_lines[0])
        if not match:
            continue
        value = int(match.group("num"))
        if value < 1 or value > page_count + 20:
            continue
        if y1 <= height * 0.12 + 2:
            candidates.append((float(y1), _PrintedPageCandidate(str(value), "top")))
        elif y0 >= height * 0.88 - 2:
            candidates.append((height - float(y0), _PrintedPageCandidate(str(value), "bottom")))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _remove_printed_page_line(lines: list[str], candidate: _PrintedPageCandidate) -> None:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, line in enumerate([*lines, "<end>"]):
        if line == candidate.value and start is None:
            start = index
        elif line != candidate.value and start is not None:
            runs.append((start, index))
            start = None
    if not runs:
        return
    longest = max(end - start for start, end in runs)
    if longest > 1:
        run_start, run_end = next((start, end) for start, end in runs if end - start == longest)
    else:
        run_start, run_end = runs[0] if candidate.edge == "top" else runs[-1]
    del lines[run_start:run_end]


def _extract_page_text(
    page: Any,
    pdf_page: int,
    decoder_rules: list[dict[str, Any]],
    *,
    sort_lines: bool = False,
    ignore_actual_text: bool = False,
) -> str:
    applicable = [rule for rule in decoder_rules if _decoder_applies(rule, pdf_page)]
    if not applicable:
        flags = fitz.TEXTFLAGS_TEXT | fitz.TEXT_IGNORE_ACTUALTEXT if ignore_actual_text else None
        return page.get_text("text", sort=sort_lines, flags=flags) or ""

    output_lines: list[str] = []
    flags = fitz.TEXTFLAGS_DICT | fitz.TEXT_IGNORE_ACTUALTEXT if ignore_actual_text else None
    page_dict = page.get_text("dict", sort=True, flags=flags)
    lines = [line for block in page_dict.get("blocks", []) for line in block.get("lines", [])]
    if sort_lines:
        lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
    for line in lines:
        pieces: list[str] = []
        prior_x1: float | None = None
        for span in line.get("spans", []):
            text = span.get("text", "")
            rule = next(
                (item for item in applicable if _font_matches(item, span.get("font", ""))), None
            )
            if rule:
                text = _decode_font_span(text, rule)
            x0 = float(span.get("bbox", (0, 0, 0, 0))[0])
            if (
                prior_x1 is not None
                and x0 - prior_x1 > 2
                and pieces
                and not pieces[-1].endswith(" ")
            ):
                pieces.append(" ")
            pieces.append(text)
            prior_x1 = float(span.get("bbox", (0, 0, 0, 0))[2])
        if pieces:
            output_lines.append("".join(pieces))
    return "\n".join(output_lines)


def _extract_line_info(
    page: Any,
    pdf_page: int,
    decoder_rules: list[dict[str, Any]],
    *,
    clip=None,
    ignore_actual_text: bool = False,
) -> list[_LineInfo]:
    """Return normalized line typography and geometry without changing text order."""
    applicable = [rule for rule in decoder_rules if _decoder_applies(rule, pdf_page)]
    output: list[_LineInfo] = []
    flags = fitz.TEXTFLAGS_DICT | fitz.TEXT_IGNORE_ACTUALTEXT if ignore_actual_text else None
    page_dict = page.get_text("dict", sort=True, clip=clip, flags=flags)
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            pieces: list[str] = []
            source_spans = []
            prior_x1: float | None = None
            alphabetic_chars = 0
            bold_alphabetic_chars = 0
            emphasized_alphabetic_chars = 0
            for span in line.get("spans", []):
                text = str(span.get("text", ""))
                rule = next(
                    (item for item in applicable if _font_matches(item, span.get("font", ""))), None
                )
                if rule:
                    text = _decode_font_span(text, rule)
                source_spans.append(
                    {
                        "text": text,
                        "original_text": str(span.get("text", "")),
                        "font": str(span.get("font", "")),
                        "size": float(span.get("size", 0)),
                        "flags": int(span.get("flags", 0)),
                        "origin": list(span.get("origin", (0, 0))),
                        "bbox": list(span.get("bbox", (0, 0, 0, 0))),
                    }
                )
                bbox = span.get("bbox", (0, 0, 0, 0))
                x0 = float(bbox[0])
                if (
                    prior_x1 is not None
                    and x0 - prior_x1 > 2
                    and pieces
                    and not pieces[-1].endswith(" ")
                ):
                    pieces.append(" ")
                pieces.append(text)
                prior_x1 = float(bbox[2])

                letters = sum(1 for char in text if char.isalpha())
                alphabetic_chars += letters
                font = str(span.get("font", "")).lower()
                if int(span.get("flags", 0)) & 16 or "bold" in font:
                    bold_alphabetic_chars += letters
                if int(span.get("flags", 0)) & 18 or "bold" in font or "italic" in font:
                    emphasized_alphabetic_chars += letters
            joined = "".join(pieces)
            if not joined.strip():
                continue
            bbox = tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0)))
            output.append(
                _LineInfo(
                    text=joined,
                    bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    predominantly_bold=bool(
                        alphabetic_chars >= 4 and bold_alphabetic_chars / alphabetic_chars >= 0.8
                    ),
                    predominantly_emphasized=bool(
                        alphabetic_chars >= 4
                        and emphasized_alphabetic_chars / alphabetic_chars >= 0.8
                    ),
                    spans=tuple(source_spans),
                )
            )
    return output


def _glossary_openings(typography):
    """Find bold term prefixes; the grammar only uses these inside a glossary."""
    openings = {}
    rows = {}
    for spans in typography.values():
        for span in spans:
            rows.setdefault(round(span["origin"][1], 1), []).append(span)
    for row in rows.values():
        spans = sorted(row, key=lambda s: s["bbox"][0])
        # A justified opening can consist of several separate PDF text objects.
        spans = [dict(s) for s in spans]
        for previous, current in zip(spans, spans[1:]):
            if current["bbox"][0] - previous["bbox"][2] > 2:
                previous["text"] += " "
        prefix = []
        for span in spans:
            if not span["text"].strip():
                continue
            if not (span["flags"] & 16 or "bold" in span["font"].lower()):
                break
            prefix.append(span["text"])
        title = normalize_text("".join(prefix))
        whole = normalize_text("".join(s["text"] for s in spans))
        if (
            title.endswith(".")
            and len(title.split()) <= 12
            and title[:1].isalpha()
            and not title.isupper()
            and len(whole) > len(title)
        ):
            # Match the actual bold opening line, not the term in an italic
            # continuation such as "authorization. An authorized person ...".
            openings[title[:-1]] = whole
    return openings


def _typographic_heading_lines(lines: list[_LineInfo], suppressed_indices: set[int]) -> list[str]:
    """Select bold standalone lines while rejecting bold running quotations."""
    visible = [(index, item) for index, item in enumerate(lines) if index not in suppressed_indices]
    output: list[str] = []
    for position, (_index, item) in enumerate(visible):
        normalized = normalize_text(item.text)
        if not item.predominantly_emphasized or not normalized or not normalized[:1].isupper():
            continue
        if normalized.endswith((",", ";")):
            continue
        previous = visible[position - 1][1] if position else None
        following = visible[position + 1][1] if position + 1 < len(visible) else None
        gap_before = item.bbox[1] - previous.bbox[3] if previous else float("inf")
        gap_after = following.bbox[1] - item.bbox[3] if following else float("inf")
        if gap_before >= 6 and gap_after >= 6:
            output.append(normalized)
    return output


def _reviewed_figure_layouts(page, lines, pdf_page, layouts):
    """Keep reviewed diagram labels and notes with their exact source caption."""
    captions = [item for item in lines if match_figure_label(normalize_text(item.text))]
    expected = [normalize_text(rule["caption"]) for rule in layouts]
    labels = [match_figure_label(item.text).canonical_id for item in captions]
    if len(set(labels)) != len(labels):
        raise ValueError("Reviewed figure ownership requires unique caption labels on a page")
    if sorted(expected) != sorted(normalize_text(item.text) for item in captions):
        raise ValueError("Reviewed figure layouts must match every caption on the page exactly")
    selected, transcripts = set(), []
    for rule in layouts:
        caption = next(
            item
            for item in captions
            if normalize_text(item.text) == normalize_text(rule["caption"])
        )
        rect = fitz.Rect(rule["bbox"])
        if (
            rect.is_empty
            or rect.is_infinite
            or not page.rect.contains(rect)
            or rect.y1 > caption.bbox[1]
        ):
            raise ValueError(
                "Reviewed figure rectangle must be inside the page and above its caption"
            )
        indices = {
            i
            for i, item in enumerate(lines)
            if rect.contains(
                fitz.Point((item.bbox[0] + item.bbox[2]) / 2, (item.bbox[1] + item.bbox[3]) / 2)
            )
        }
        if selected & indices:
            raise ValueError("Reviewed figure rectangles must not share source text")
        selected.update(indices)
        transcript = rule.get("transcription")
        if transcript:
            if not all(str(transcript.get(k, "")).strip() for k in ["text", "reviewer", "notes"]):
                raise ValueError("Figure transcription requires text, reviewer and source notes")
            text = normalize_text(transcript["text"])
            if any(normalize_text(lines[i].text) == text for i in indices):
                raise ValueError("Figure transcription duplicates native source text")
            transcripts.append(
                {
                    "pdf_page": pdf_page,
                    "bbox": list(rect),
                    "text": text,
                    "role": "figure",
                    "_owner_caption": match_figure_label(rule["caption"]).canonical_id,
                    "reviewed_layout": True,
                    "transcription": {
                        "reviewer": transcript["reviewer"],
                        "notes": transcript["notes"],
                        "geometry": "whole_reviewed_figure_region",
                    },
                }
            )
    return selected, transcripts


def _figure_interior_line_indices(
    page: Any, lines: list[_LineInfo], *, owners=None, warnings=None
) -> set[int]:
    """Identify text encoded inside vector/image figures immediately above captions.

    Many IAEA diagrams are stored as hundreds of positioned text and vector
    objects.  Plain extraction flattens those objects into the preceding
    paragraph or an active table.  A caption plus a substantial, vertically
    connected drawing/image component is strong layout evidence for a figure;
    only text above the caption and inside that component's vertical extent is
    suppressed.
    """
    captions = [item for item in lines if match_figure_label(normalize_text(item.text))]
    if not captions:
        return set()

    page_width = float(page.rect.width or 1)
    page_height = float(page.rect.height or 1)
    page_area = page_width * page_height
    rectangles: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        rect = tuple(float(value) for value in drawing.get("rect", (0, 0, 0, 0)))
        if len(rect) != 4:
            continue
        x0, y0, x1, y1 = rect
        width = max(0.0, x1 - x0)
        height = max(0.0, y1 - y0)
        # Form grids are often separate zero-area strokes. Long rules connect
        # their filled header/footer bands into the same captioned figure.
        if width * height < 150 and max(width, height) < 20:
            continue
        if width * height > page_area * 0.7:
            continue
        rectangles.append((x0, y0, x1, y1))
    page_dict = page.get_text("dict", sort=True)
    for block in page_dict.get("blocks", []):
        if block.get("type") != 1:
            continue
        rect = tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0)))
        if len(rect) == 4 and rect[2] > rect[0] and rect[3] > rect[1]:
            rectangles.append((rect[0], rect[1], rect[2], rect[3]))

    owners = owners if owners is not None else {}
    warnings = warnings if warnings is not None else {}
    regions = []
    label_counts = Counter(match_figure_label(caption.text).canonical_id for caption in captions)
    for caption in captions:
        label = match_figure_label(caption.text).canonical_id
        if label_counts[label] > 1:
            warnings.setdefault(
                label, ["Unresolved figure ownership: repeated source caption label."]
            )
            continue
        caption_y = caption.bbox[1]
        candidates = [
            rect for rect in rectangles if rect[3] <= caption_y + 2 and caption_y - rect[3] <= 400
        ]
        if not candidates:
            continue
        anchors = [
            rect for rect in candidates if rect[2] >= caption.bbox[0] and rect[0] <= caption.bbox[2]
        ]
        anchor = max(anchors or candidates, key=lambda rect: rect[3])
        if caption_y - anchor[3] > 40:
            continue
        component = [anchor]
        changed = True
        while changed:
            changed = False
            left = min(rect[0] for rect in component)
            top = min(rect[1] for rect in component)
            right = max(rect[2] for rect in component)
            bottom = max(rect[3] for rect in component)
            for rect in candidates:
                if rect in component:
                    continue
                if (
                    rect[1] <= bottom + 18
                    and rect[3] >= top - 18
                    and rect[0] <= right + 18
                    and rect[2] >= left - 18
                ):
                    component.append(rect)
                    changed = True
        left = min(rect[0] for rect in component)
        top = min(rect[1] for rect in component)
        right = max(rect[2] for rect in component)
        bottom = max(rect[3] for rect in component)
        if (right - left) * (bottom - top) < page_area * 0.05:
            continue
        regions.append((left, top, right, caption_y, match_figure_label(caption.text).canonical_id))

    suppressed: set[int] = set()
    for index, item in enumerate(lines):
        centre_y = (item.bbox[1] + item.bbox[3]) / 2
        centre_x = (item.bbox[0] + item.bbox[2]) / 2
        matches = [
            owner
            for left, top, right, bottom, owner in regions
            if left <= centre_x <= right and top <= centre_y < bottom
        ]
        if not matches and not item.text.rstrip().endswith(".") and len(item.text.split()) <= 12:
            # Short axis/legend labels can extend a little beyond the drawing.
            # Never extend through a numbered paragraph or a source caption.
            if not re.match(r"^\s*\d+\.\d+", item.text):
                matches = [
                    owner
                    for left, top, right, bottom, owner in regions
                    if left - 12 <= centre_x <= right + 12
                    and top - 12 <= centre_y < bottom
                    and (top <= centre_y or (item.bbox[2] >= left and item.bbox[0] <= right))
                ]
        if len(matches) == 1:
            normalized = normalize_text(item.text)
            if normalized and not match_figure_label(normalized):
                suppressed.add(index)
                owners[index] = matches[0]
        elif len(matches) > 1 and not match_figure_label(item.text):
            for owner in matches:
                warnings.setdefault(owner, []).append(
                    f"Unresolved figure ownership for source line {index}."
                )
    return suppressed


def _join_caption_fragments(lines: list[_LineInfo]) -> tuple[list[_LineInfo], bool]:
    """Join a caption whose first printed line is encoded as separate words.

    Older justified PDFs store 'TABLE', its number, and title words as separate
    text objects. Only a complete recognized caption on the same baseline is
    joined; ordinary columns and inline figure references are left alone.
    """
    replacements = {}
    removed = set()
    for index, line in enumerate(lines):
        if line.text.strip() not in {"TABLE", "FIG.", "Fig."}:
            continue
        same_row = [
            (i, item)
            for i, item in enumerate(lines)
            if abs(item.bbox[1] - line.bbox[1]) <= 1.5 and abs(item.bbox[3] - line.bbox[3]) <= 1.5
        ]
        same_row.sort(key=lambda pair: pair[1].bbox[0])
        text = " ".join(item.text.strip() for _, item in same_row)
        if not (match_table_label(text) or match_figure_label(text)):
            continue
        boxes = [item.bbox for _, item in same_row]
        replacements[index] = _LineInfo(
            text=text,
            bbox=(
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ),
            predominantly_bold=line.predominantly_bold,
            predominantly_emphasized=line.predominantly_emphasized,
            spans=tuple(span for _, item in same_row for span in item.spans),
        )
        removed.update(i for i, _ in same_row if i != index)
    return [replacements.get(i, item) for i, item in enumerate(lines) if i not in removed], bool(
        replacements
    )


def _inside(bbox, container) -> bool:
    """Test the centre of a text line against a layout rectangle."""
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return container[0] - 1 <= x <= container[2] + 1 and container[1] - 1 <= y <= container[3] + 1


def _reading_order_key(line, rotation):
    """Sort in the viewed orientation while retaining original source coordinates."""
    rect = fitz.Rect(line.bbox) * fitz.Matrix(rotation)
    # Superscripts change the top of a line, but not its ordinary baseline.
    return round(rect.y1, 1), rect.x0


def _cell_shading(drawings, rect):
    """Read solid rectangle fills individually: one PDF path may shade distant cells."""
    interior = fitz.Rect(rect.x0 + 0.5, rect.y0 + 0.5, rect.x1 - 0.5, rect.y1 - 0.5)
    color = None
    for drawing in drawings:
        if drawing.get("fill") is None:
            continue
        for item in drawing["items"]:
            if item[0] != "re" or not item[1].contains(interior):
                continue
            if drawing.get("fill_opacity", 1) != 1:
                raise ValueError("Reviewed cell shading requires an opaque fill")
            if len(drawing["fill"]) != 3:
                raise ValueError("Cell shading requires an RGB fill")
            color = "#" + "".join(f"{round(channel * 255):02x}" for channel in drawing["fill"])
    return color if color != "#ffffff" else None


def _validate_grid(cells, row_count, column_count, bbox):
    """A grid must cover each logical slot exactly once with source-bounded cells."""
    occupied = set()
    for cell in cells:
        if not fitz.Rect(bbox).contains(fitz.Rect(cell["bbox"])):
            raise ValueError("Reviewed cell rectangle must be inside its table")
        slots = {
            (r, c)
            for r in range(cell["row"], cell["row"] + cell["row_span"])
            for c in range(cell["column"], cell["column"] + cell["column_span"])
        }
        if not slots or occupied & slots:
            raise ValueError("Reviewed table cells have empty or overlapping spans")
        occupied.update(slots)
    if occupied != {(r, c) for r in range(row_count) for c in range(column_count)}:
        raise ValueError("Reviewed table cells must cover the declared grid exactly")


def _reviewed_table_layouts(
    page, lines, pdf_page, decoder_rules, layouts, rotation, *, ignore_actual_text=False
):
    """Read source-backed cell rectangles where printed rules omit logical subrows.

    Geometry and spans are reviewed configuration; cell text always comes from
    the source PDF. A complete non-overlapping logical grid is required.
    """
    tables, indices = {}, {}
    for rule in layouts:
        # Match the complete reviewed caption, including consecutive wrapped
        # lines. A prefix alone must not consume a different table's caption.
        candidates = []
        for i, line in enumerate(lines):
            text = normalize_text(line.text)
            if not match_table_label(text):
                continue
            indices_for_caption = [i]
            while rule["caption"].startswith(text):
                if text == rule["caption"]:
                    candidates.append(indices_for_caption)
                    break
                j = indices_for_caption[-1] + 1
                if j == len(lines):
                    break
                previous = fitz.Rect(lines[j - 1].bbox) * fitz.Matrix(rotation)
                following = fitz.Rect(lines[j].bbox) * fitz.Matrix(rotation)
                same_row = (
                    abs(following.y0 - previous.y0) <= 1.5
                    and abs(following.y1 - previous.y1) <= 1.5
                    and following.x0 >= previous.x1
                )
                next_row = following.y1 > previous.y1 and following.y0 - previous.y1 <= max(
                    previous.height, following.height
                )
                if not (same_row or next_row):
                    break
                text += " " + normalize_text(lines[j].text)
                indices_for_caption.append(j)
        label = match_table_label(rule["caption"])
        if len(candidates) != 1 or not label:
            raise ValueError(f"Page {pdf_page}: reviewed table caption does not match source")
        caption_indices = candidates[0]
        bbox = fitz.Rect(rule["bbox"])
        consumed = sorted(
            set(caption_indices + [i for i, line in enumerate(lines) if _inside(line.bbox, bbox)])
        )
        if any(i in indices for i in consumed):
            raise ValueError("Reviewed table layouts overlap")
        cells, occupied = [], set()
        shading = page.get_drawings() if rule.get("retain_cell_shading") else []
        for spec in rule["cells"]:
            rect = fitz.Rect(spec["bbox"])
            if rect.is_empty or not bbox.contains(rect):
                raise ValueError("Reviewed cell rectangle must be inside its table")
            row, column = spec["row"], spec["column"]
            row_span, column_span = spec.get("row_span", 1), spec.get("column_span", 1)
            slots = {
                (r, c)
                for r in range(row, row + row_span)
                for c in range(column, column + column_span)
            }
            if not slots or occupied & slots:
                raise ValueError("Reviewed table cells have empty or overlapping spans")
            occupied.update(slots)
            cell_lines = _extract_line_info(
                page, pdf_page, decoder_rules, clip=rect, ignore_actual_text=ignore_actual_text
            )
            cell_lines.sort(key=lambda item: _reading_order_key(item, rotation))
            cells.append(
                dict(
                    row=row,
                    column=column,
                    row_span=row_span,
                    column_span=column_span,
                    bbox=list(rect),
                    text="\n".join(normalize_text(line.text) for line in cell_lines),
                )
            )
            if shading and (color := _cell_shading(shading, rect)):
                cells[-1]["background_color"] = color
        _validate_grid(cells, rule["row_count"], rule["column_count"], bbox)
        token = f"[[TABLE:p{pdf_page}:{len(tables) + 1}]]"
        tables[token] = dict(
            element_id=f"TABLE {label.canonical_id}",
            caption=rule["caption"],
            source_label=label.raw_label,
            label_style=label.style,
            raw_text="\n".join(normalize_text(lines[i].text) for i in consumed),
            pdf_page=pdf_page,
            bbox=list(bbox),
            layout="grid",
            row_count=rule["row_count"],
            column_count=rule["column_count"],
            cells=cells,
            source_spans=[
                {"pdf_page": pdf_page, "line": i, "bbox": list(lines[i].bbox)} for i in consumed
            ],
        )
        notes = [
            normalize_text(lines[i].text)
            for i in consumed
            if i not in caption_indices
            and not any(_inside(lines[i].bbox, cell["bbox"]) for cell in cells)
        ]
        if notes:
            tables[token]["notes"] = "\n".join(notes)
        indices.update({i: token for i in consumed})
    return tables, indices


def _extract_tables(page, lines, pdf_page, decoder_rules, *, ignore_actual_text=False):
    """Capture captioned grids before their numbered cells reach the grammar.

    Each physical page remains a separate fragment with the printed table ID.
    Horizontal rules can bound a raw table without establishing its columns.
    Fully borderless tables continue through the text parser for source review.
    """
    captions = [
        (i, item) for i, item in enumerate(lines) if match_table_label(normalize_text(item.text))
    ]
    if not captions:
        return {}, {}
    tables = {}
    indices = {}
    drawings = page.get_drawings()
    grids = [
        grid
        for grid in page.find_tables(strategy="lines_strict").tables
        if grid.row_count >= 2 and grid.col_count >= 2
    ]
    regions = [(grid.bbox, grid) for grid in grids]
    regions.extend((bbox, None) for bbox in _ruled_table_bounds(page, captions, lines))
    for bbox, grid in regions:
        above = [(i, item) for i, item in captions if 0 <= bbox[1] - item.bbox[3] <= 110]
        if not above:
            continue
        caption_index, caption_line = max(above, key=lambda pair: pair[1].bbox[3])
        # Do not consume unrelated text between a distant caption and a grid.
        caption_indices = [
            i
            for i in range(caption_index, len(lines))
            if caption_line.bbox[1] <= lines[i].bbox[1] < bbox[1]
            and lines[i].bbox[3] <= bbox[1] + 2
        ]
        if len(caption_indices) > 5:
            continue
        cell_indices = [i for i, item in enumerate(lines) if _inside(item.bbox, bbox)]
        consumed = sorted(set(caption_indices + cell_indices))
        if not cell_indices or any(i in indices for i in consumed):
            continue
        caption = " ".join(normalize_text(lines[i].text) for i in caption_indices)
        label = match_table_label(caption)
        if not label:
            continue
        cells = []
        warnings = []
        seen = set()
        rows = grid.rows if grid is not None else []
        rects = [rect for row in rows for rect in row.cells if rect]
        xs = sorted({round(rect[0], 1) for rect in rects} | {round(rect[2], 1) for rect in rects})
        ys = sorted({round(rect[1], 1) for rect in rects} | {round(rect[3], 1) for rect in rects})
        for row_index, row in enumerate(rows):
            for column_index, rect in enumerate(row.cells):
                if rect is None or tuple(rect) in seen:
                    continue
                seen.add(tuple(rect))
                cell_lines = _extract_line_info(
                    page,
                    pdf_page,
                    decoder_rules,
                    clip=fitz.Rect(rect),
                    ignore_actual_text=ignore_actual_text,
                )
                cells.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "row_span": max(1, sum(rect[1] + 1 < y <= rect[3] + 1 for y in ys)),
                        "column_span": max(1, sum(rect[0] + 1 < x <= rect[2] + 1 for x in xs)),
                        "text": "\n".join(normalize_text(item.text) for item in cell_lines),
                        "bbox": list(rect),
                    }
                )
                try:
                    color = _cell_shading(drawings, fitz.Rect(rect))
                    if color:
                        cells[-1]["background_color"] = color
                except ValueError as error:
                    warnings.append(f"Unresolved table shading: {error}")
        if grid is not None:
            try:
                _validate_grid(cells, grid.row_count, grid.col_count, bbox)
            except ValueError as error:
                warnings.append(f"Unresolved table grid: {error}")
                cells, grid = [], None
        note_indices = []
        bottom = bbox[3]
        for i, item in enumerate(lines):
            if not (
                0 <= item.bbox[1] - bottom <= 18
                and bbox[0] - 2 <= item.bbox[0]
                and item.bbox[2] <= bbox[2] + 2
            ):
                continue
            value = normalize_text(item.text)
            if (not note_indices and not re.match(r"^Notes?:\s", value, re.I)) or (
                note_indices
                and (
                    match_table_label(value)
                    or match_figure_label(value)
                    or re.match(r"^\d+\.", value)
                    or item.predominantly_bold
                )
            ):
                break
            note_indices.append(i)
            bottom = item.bbox[3]
        if note_indices:
            consumed = sorted(set(consumed + note_indices))
            bbox = (bbox[0], bbox[1], bbox[2], bottom)
        token = f"[[TABLE:p{pdf_page}:{len(tables) + 1}]]"
        tables[token] = {
            "element_id": f"TABLE {label.canonical_id}",
            "caption": caption,
            "source_label": label.raw_label,
            "label_style": label.style,
            "raw_text": "\n".join(normalize_text(lines[i].text) for i in consumed),
            "pdf_page": pdf_page,
            "bbox": list(bbox),
            "layout": "grid" if grid is not None else "ruled",
            "row_count": grid.row_count if grid is not None else None,
            "column_count": grid.col_count if grid is not None else None,
            "cells": cells,
            "source_spans": [
                {"pdf_page": pdf_page, "line": i, "bbox": list(lines[i].bbox)} for i in consumed
            ],
            **({"warnings": sorted(set(warnings))} if warnings else {}),
            **(
                {"notes": "\n".join(normalize_text(lines[i].text) for i in note_indices)}
                if note_indices
                else {}
            ),
        }
        indices.update({i: token for i in consumed})
    return tables, indices


def _ruled_table_bounds(page, captions, lines):
    """Bound tables with horizontal rules without inventing their columns.

    Require three aligned, page-wide rules below a nearby caption. Captions
    delimit candidates so two successive tables cannot become one region.
    These regions keep rows out of adjacent prose even when the PDF stores
    the caption after its cells. Their raw text still needs visual review.
    """
    segments = sorted(
        (
            tuple(drawing["rect"])
            for drawing in page.get_drawings()
            if drawing["rect"].height <= 2 and drawing["rect"].width > 2
        ),
        key=lambda rect: (round(rect[1]), rect[0]),
    )
    # A single printed rule can be stored as one segment per column.
    rules = []
    for rect in segments:
        if rules and abs(rect[1] - rules[-1][1]) <= 1 and rect[0] <= rules[-1][2] + 2:
            left, top, right, bottom = rules[-1]
            rules[-1] = (min(left, rect[0]), top, max(right, rect[2]), max(bottom, rect[3]))
        else:
            rules.append(rect)
    rules = [rect for rect in rules if rect[2] - rect[0] >= page.rect.width * 0.4]
    boundaries = [
        line.bbox[1]
        for line in lines
        if match_table_label(line.text) or match_figure_label(line.text)
    ]
    bounds = []
    for _, caption in captions:
        next_caption = min(
            (y for y in boundaries if y > caption.bbox[3]), default=page.rect.height * 0.9
        )
        below = [rect for rect in rules if caption.bbox[3] <= rect[1] < next_caption]
        if not below or below[0][1] - caption.bbox[3] > 110:
            continue
        first = below[0]
        aligned = [
            rect for rect in below if abs(rect[0] - first[0]) <= 4 and abs(rect[2] - first[2]) <= 4
        ]
        if len({round(rect[1], 1) for rect in aligned}) < 3:
            continue
        bounds.append(
            (
                min(caption.bbox[0], first[0]),
                first[1],
                max(rect[2] for rect in aligned),
                aligned[-1][3],
            )
        )
    return bounds


def _decoder_applies(rule: dict[str, Any], pdf_page: int) -> bool:
    pages = rule.get("pdf_pages") or []
    if pdf_page in pages:
        return True
    page_range = rule.get("pdf_page_range") or []
    return len(page_range) == 2 and int(page_range[0]) <= pdf_page <= int(page_range[1])


def _font_matches(rule: dict[str, Any], font: str) -> bool:
    fonts = rule.get("fonts") or [rule.get("font")]
    return font in {str(value) for value in fonts if value}


def _decode_font_span(text: str, rule: dict[str, Any]) -> str:
    offset = int(rule.get("ascii_offset", 0))
    replacements = {str(key): str(value) for key, value in (rule.get("replacements") or {}).items()}
    decoded: list[str] = []
    for char in text:
        if char in replacements:
            decoded.append(replacements[char])
            continue
        codepoint = ord(char)
        if char != " " and offset and 1 <= codepoint <= 126 and codepoint + offset <= 126:
            decoded.append(chr(codepoint + offset))
        else:
            decoded.append(char)
    return "".join(decoded)
