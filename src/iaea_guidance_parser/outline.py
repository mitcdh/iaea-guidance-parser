"""Conservative heading verification against the PDF's independent bookmark tree.

A missing/incomplete outline is a reason to inspect, never permission to infer
heading roles from matching words. This module does not change parser output.
"""

from __future__ import annotations

import re
import unicodedata

from .provenance import fingerprint


def wording(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def outline_entries(document):
    stack, entries = [], []
    for level, title, page in document.get_toc():
        if level < 1 or level > len(stack) + 1 or page < 1:
            return []
        stack = stack[: level - 1] + [title]
        entries.append({"level": level, "title": title, "page": page, "path": list(stack)})
    return entries


def structural_proof(document, page_number, records, source_text):
    """Prove every parsed heading on a page's wording, order, placement and path.

    All source bookmarks targeting the page must also be represented. Exact PDF
    line geometry is checked against emitted source spans, and independent
    Poppler text must contain each complete heading in order. Non-heading record
    paths must agree with the active bookmark path, including a preceding page's
    heading. Conservative mismatches remain visual-review work.
    """
    entries = outline_entries(document)
    expected = [e for e in entries if e["page"] == page_number]
    headings = [r for r in records if r["element_type"] == "heading"]
    if not entries or not expected or len(headings) != len(expected):
        return None
    page = document[page_number - 1]
    source = wording(source_text)
    offset = 0
    matches = []
    for record, entry in zip(headings, expected):
        if (
            record["page_start_pdf"] != page_number
            or record["page_end_pdf"] != page_number
            or wording(record["text"]) != wording(entry["title"])
            or list(map(wording, record["section_path"])) != list(map(wording, entry["path"]))
        ):
            return None
        position = source.find(wording(entry["title"]), offset)
        if position < 0:
            return None
        offset = position + len(wording(entry["title"]))
        spans = record.get("extra", {}).get("source_spans", [])
        if not spans or any(s["pdf_page"] != page_number for s in spans):
            return None
        extracted = " ".join(page.get_textbox(s["bbox"]) for s in spans)
        if wording(extracted) != wording(record["text"]):
            return None
        boxes = [s["bbox"] for s in spans]
        if matches and boxes[0][1] < matches[-1]["boxes"][-1][1]:
            return None
        matches.append({"title": entry["title"], "path": entry["path"], "boxes": boxes})
    previous = [e for e in entries if e["page"] < page_number]
    active = sorted(previous, key=lambda e: e["page"])[-1]["path"] if previous else []
    index = 0
    for record in records:
        if record["element_type"] == "heading":
            active = expected[index]["path"]
            index += 1
        if list(map(wording, record["section_path"])) != list(map(wording, active)):
            return None
        # A heading crossing a named major region must carry that source region.
        root = wording(active[0]) if active else ""
        for pattern, region in (
            (r"^appendix\b", "Appendix"),
            (r"^annex\b", "Annex"),
            (r"^references$", "References"),
            (r"^glossary$", "Glossary"),
        ):
            if re.match(pattern, root) and record["source_region"] != region:
                return None
    return {
        "kind": "pdf_bookmark_tree_v1",
        "outline_sha256": fingerprint(entries),
        "page": page_number,
        "matches": matches,
        "independent_text_sha256": fingerprint(source_text),
    }
