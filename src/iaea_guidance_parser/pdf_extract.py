from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal test envs
    fitz = None

from .models import PageText
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


@dataclass(frozen=True)
class _LineInfo:
    text: str
    bbox: tuple[float, float, float, float]
    predominantly_bold: bool
    predominantly_emphasized: bool


def extract_pages(pdf_path: Path, parser_config: dict[str, Any] | None = None) -> list[PageText]:
    """Extract page text and rough printed page numbers from a PDF."""
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF is required to extract PDF text. Install the `pymupdf` package."
        )
    doc = fitz.open(pdf_path)
    parser_config = parser_config or {}
    decoder_rules = parser_config.get("font_decoders", []) or []
    raw_pages: list[_RawPage] = []
    try:
        for idx, page in enumerate(doc):
            pdf_page = idx + 1
            raw = _extract_page_text(page, pdf_page, decoder_rules)
            lines = [normalize_text(line) for line in raw.splitlines()]
            lines = [line for line in lines if line]
            line_info = _extract_line_info(page, pdf_page, decoder_rules)
            line_info, repaired_caption = _join_caption_fragments(line_info)
            tables, table_indices = _extract_tables(page, line_info, pdf_page, decoder_rules)
            suppressed_indices = _figure_interior_line_indices(page, line_info)
            # A table can contain diagrams or a figure reference. Its cells
            # already preserve the complete content and take precedence here.
            suppressed_indices -= set(table_indices)
            suppressed_figure_lines = [
                normalize_text(item.text)
                for index, item in enumerate(line_info)
                if index in suppressed_indices
            ]
            if suppressed_indices or tables or repaired_caption:
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
            )
        )
    return pages


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


def _extract_page_text(page: Any, pdf_page: int, decoder_rules: list[dict[str, Any]]) -> str:
    applicable = [rule for rule in decoder_rules if _decoder_applies(rule, pdf_page)]
    if not applicable:
        return page.get_text("text") or ""

    output_lines: list[str] = []
    page_dict = page.get_text("dict", sort=True)
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
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
    page: Any, pdf_page: int, decoder_rules: list[dict[str, Any]], *, clip=None
) -> list[_LineInfo]:
    """Return normalized line typography and geometry without changing text order."""
    applicable = [rule for rule in decoder_rules if _decoder_applies(rule, pdf_page)]
    output: list[_LineInfo] = []
    page_dict = page.get_text("dict", sort=True, clip=clip)
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            pieces: list[str] = []
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
                )
            )
    return output


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


def _figure_interior_line_indices(page: Any, lines: list[_LineInfo]) -> set[int]:
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

    regions: list[tuple[float, float, float, float]] = []
    for caption in captions:
        caption_y = caption.bbox[1]
        candidates = [
            rect for rect in rectangles if rect[3] <= caption_y + 2 and caption_y - rect[3] <= 400
        ]
        if not candidates:
            continue
        anchor = max(candidates, key=lambda rect: rect[3])
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
        regions.append((left, top, right, caption_y))

    suppressed: set[int] = set()
    for index, item in enumerate(lines):
        centre_y = (item.bbox[1] + item.bbox[3]) / 2
        centre_x = (item.bbox[0] + item.bbox[2]) / 2
        if any(
            left <= centre_x <= right and top <= centre_y < bottom
            for left, top, right, bottom in regions
        ):
            normalized = normalize_text(item.text)
            if normalized and not match_figure_label(normalized):
                suppressed.add(index)
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
        )
        removed.update(i for i, _ in same_row if i != index)
    return [replacements.get(i, item) for i, item in enumerate(lines) if i not in removed], bool(
        replacements
    )


def _inside(bbox, container) -> bool:
    """Test the centre of a text line against a layout rectangle."""
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return container[0] - 1 <= x <= container[2] + 1 and container[1] - 1 <= y <= container[3] + 1


def _extract_tables(page, lines, pdf_page, decoder_rules):
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
                cell_lines = _extract_line_info(page, pdf_page, decoder_rules, clip=fitz.Rect(rect))
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
