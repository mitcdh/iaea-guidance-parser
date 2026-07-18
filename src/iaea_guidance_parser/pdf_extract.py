from __future__ import annotations

from pathlib import Path

try:
    import fitz  # PyMuPDF
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal test envs
    fitz = None

from .models import PageText
from .rules import CONTENTS_HEADING_RE, PAGE_NUMBER_RE, normalize_text, remove_pdf_line_breaks


def extract_pages(pdf_path: Path) -> list[PageText]:
    """Extract page text and rough printed page numbers from a PDF."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is required to extract PDF text. Install the `pymupdf` package.")
    doc = fitz.open(pdf_path)
    pages: list[PageText] = []
    for idx, page in enumerate(doc):
        raw = page.get_text("text") or ""
        lines = [normalize_text(line) for line in raw.splitlines()]
        lines = [line for line in lines if line]
        printed_page: str | None = None
        if lines:
            is_contents_page = any(CONTENTS_HEADING_RE.match(line) for line in lines[:3])
            last = lines[-1]
            first = lines[0]
            # The main content pages use a plain header or footer page number.
            # Remove it from parseable text, but keep contents-entry page numbers.
            m = PAGE_NUMBER_RE.match(last)
            if m and len(lines) > 3 and not is_contents_page:
                printed_page = m.group("num")
                lines = lines[:-1]
            m = PAGE_NUMBER_RE.match(first)
            if m and len(lines) > 3 and not is_contents_page:
                printed_page = printed_page or m.group("num")
                lines = lines[1:]
        lines = remove_pdf_line_breaks(lines)
        text = "\n".join(lines)
        pages.append(PageText(pdf_page=idx + 1, printed_page=printed_page, text=text, lines=lines))
    return pages
