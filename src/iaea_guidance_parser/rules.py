from __future__ import annotations

import re

# Accept both hyphen-minus, non-breaking hyphen, figure dash and en dash.
DASH_CLASS = r"[-‑–—]"

BODY_PARA_RE = re.compile(r"^(?P<id>\d+\.\d+)\.\s*(?P<text>.*)")
APPENDIX_PARA_RE = re.compile(r"^(?P<id>A\.\d+)\.\s*(?P<text>.*)")
ANNEX_PARA_RE = re.compile(rf"^(?P<id>[IVXLCDM]+{DASH_CLASS}\d+)\.\s*(?P<text>.*)")
SUBPARA_RE = re.compile(r"^\((?:[a-z]|[ivxlcdm]+|\d+)\)\s+")

FIGURE_RE = re.compile(rf"^(?P<label>FIG\.|Fig\.)\s+(?P<num>(?:[IVXLCDM]+{DASH_CLASS})?\d+)\.\s*(?P<caption>.*)")
TABLE_RE = re.compile(rf"^TABLE\s+(?P<num>(?:[IVXLCDM]+{DASH_CLASS})?\d+)\.\s*(?P<title>.*)")
TABLE_CONT_RE = re.compile(r"\(cont\.\)", flags=re.IGNORECASE)
REFERENCE_ITEM_RE = re.compile(r"^\[(?P<num>\d+)\]\s*(?P<text>.*)")
FOOTNOTE_RE = re.compile(r"^(?P<num>\d{1,2})\s+(?P<text>[A-Z][^\n]{8,}|[A-Za-z].{8,})")
PAGE_NUMBER_RE = re.compile(r"^(?P<num>\d{1,4})$")

MAJOR_BODY_HEADING_RE = re.compile(r"^(?P<num>\d+)\.\s+(?P<title>[A-Z].*)")
ANNEX_HEADING_RE = re.compile(r"^Annex\s+(?P<num>[IVXLCDM]+)\s*$", flags=re.IGNORECASE)
APPENDIX_HEADING_RE = re.compile(r"^Appendix\s*$", flags=re.IGNORECASE)
REFERENCES_HEADING_RE = re.compile(r"^REFERENCES\s*$")
GLOSSARY_HEADING_RE = re.compile(r"^GLOSSARY\s*$")
RELATED_PUBLICATIONS_RE = re.compile(r"^RELATED PUBLICATIONS\s*$")
CONTENTS_HEADING_RE = re.compile(r"^CONTENTS\s*$")

KNOWN_DOCUMENT_CATEGORIES = {
    "Nuclear Security Fundamentals": "nuclear_security_fundamentals",
    "Nuclear Security Recommendations": "nuclear_security_recommendations",
    "Implementing Guides": "implementing_guides",
    "Implementing Guide": "implementing_guides",
    "Technical Guidance": "technical_guidance",
    "Safety Fundamentals": "safety_fundamentals",
    "General Safety Requirements": "general_safety_requirements",
    "Specific Safety Requirements": "specific_safety_requirements",
    "Safety Requirements": "safety_requirements",
    "General Safety Guide": "general_safety_guide",
    "General Safety Guides": "general_safety_guide",
    "Specific Safety Guide": "specific_safety_guide",
    "Specific Safety Guides": "specific_safety_guide",
    "Safety Guide": "safety_guide",
    "Safety Guides": "safety_guide",
}

KNOWN_PUBLICATION_HEADINGS = {
    "IAEA NUCLEAR SECURITY SERIES",
    "CATEGORIES IN THE IAEA NUCLEAR SECURITY SERIES",
    "IAEA SAFETY STANDARDS",
    "IAEA SAFETY STANDARDS SERIES",
    "IAEA SAFETY SERIES",
    "DRAFTING AND REVIEW",
    "COPYRIGHT NOTICE",
    "FOREWORD",
    "EDITORAL NOTE",
    "EDITORIAL NOTE",
    "CONTENTS",
    "RELATED PUBLICATIONS",
    "ORDERING LOCALLY",
}


def normalize_text(s: str) -> str:
    """Normalize whitespace while preserving publication identifiers such as I-1 / I–1."""
    replacements = {
        "\u00a0": " ",
        "\t": " ",
        "\ufeff": "",
        "\u200b": "",
        "\u2028": "\n",
        "\u2029": "\n",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    # Keep en dashes and non-breaking hyphens; collapse other horizontal Unicode whitespace.
    s = re.sub(r"[^\S\n]+", " ", s)
    return s.strip()


def remove_pdf_line_breaks(lines: list[str]) -> list[str]:
    """Join lines that are likely PDF text-extraction wraps, not document breaks."""
    repaired: list[str] = []
    for raw_line in lines:
        line = normalize_text(raw_line)
        if not line:
            continue
        if repaired and _should_join_pdf_wrapped_line(repaired[-1], line):
            repaired[-1] = _join_wrapped_text(repaired[-1], line)
        else:
            repaired.append(line)
    return repaired


def _should_join_pdf_wrapped_line(previous: str, current: str) -> bool:
    if _starts_new_structural_line(current):
        return False
    if TABLE_RE.match(previous):
        return False
    if previous in KNOWN_PUBLICATION_HEADINGS:
        return False
    if is_all_caps_heading(previous):
        return False
    return _looks_like_wrapped_prose(previous, current)


def _starts_new_structural_line(line: str) -> bool:
    return bool(
        BODY_PARA_RE.match(line)
        or APPENDIX_PARA_RE.match(line)
        or ANNEX_PARA_RE.match(line)
        or MAJOR_BODY_HEADING_RE.match(line)
        or FIGURE_RE.match(line)
        or TABLE_RE.match(line)
        or REFERENCE_ITEM_RE.match(line)
        or APPENDIX_HEADING_RE.match(line)
        or ANNEX_HEADING_RE.match(line)
        or REFERENCES_HEADING_RE.match(line)
        or GLOSSARY_HEADING_RE.match(line)
        or RELATED_PUBLICATIONS_RE.match(line)
        or CONTENTS_HEADING_RE.match(line)
        or line in KNOWN_PUBLICATION_HEADINGS
        or is_all_caps_heading(line)
    )


def _looks_like_wrapped_prose(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous.endswith((".", ":", ";", "?", "!")) and current[:1].isupper():
        return False
    return any(ch.isalpha() for ch in previous) and any(ch.isalpha() for ch in current)


def _join_wrapped_text(previous: str, current: str) -> str:
    if re.search(r"[a-z][-‑]$", previous) and re.match(r"^[a-z]", current):
        return previous[:-1] + current
    return f"{previous} {current}"


def canonical_dash(s: str) -> str:
    return re.sub(DASH_CLASS, "–", s)


def slugify_category(category: str) -> str:
    return KNOWN_DOCUMENT_CATEGORIES.get(category, re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_"))


def is_all_caps_heading(line: str) -> bool:
    if not line or len(line) < 4:
        return False
    if FIGURE_RE.match(line) or TABLE_RE.match(line):
        return False
    if BODY_PARA_RE.match(line) or APPENDIX_PARA_RE.match(line) or ANNEX_PARA_RE.match(line):
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if len(letters) < 4:
        return False
    uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
    return uppercase_ratio > 0.82
