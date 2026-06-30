from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from .models import PageText
from .rules import PAGE_NUMBER_RE, normalize_text, remove_pdf_line_breaks


def extract_pages(pdf_path: Path) -> list[PageText]:
    """Extract page text and rough printed page numbers from a PDF."""
    doc = fitz.open(pdf_path)
    pages: list[PageText] = []
    for idx, page in enumerate(doc):
        raw = page.get_text("text") or ""
        lines = [normalize_text(line) for line in raw.splitlines()]
        lines = [line for line in lines if line]
        printed_page: str | None = None
        if lines:
            last = lines[-1]
            # The main content pages use a plain footer page number. Remove it from parseable text.
            m = PAGE_NUMBER_RE.match(last)
            if m and len(lines) > 3:
                printed_page = m.group("num")
                lines = lines[:-1]
        lines = remove_pdf_line_breaks(lines)
        text = "\n".join(lines)
        pages.append(PageText(pdf_page=idx + 1, printed_page=printed_page, text=text, lines=lines))
    return pages
