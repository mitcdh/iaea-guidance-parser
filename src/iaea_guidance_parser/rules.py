from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

# PDF text maps use several visually equivalent dash characters in official
# identifiers.  U+2212 occurs in a small number of otherwise ordinary figure
# captions, so include it as an input spelling and canonicalize it below.
DASH_CLASS = r"[-‑‒–—−]"

BODY_ID_PATTERN = r"(?:\d{3}[A-Z](?:\.\d+)+|\d+(?:\.\d+)+|\d{3}[A-Z]?)"
APPENDIX_ID_PATTERN = rf"A(?:\.|{DASH_CLASS})\d+(?:\.\d+)*"
ANNEX_ID_PATTERN = rf"[IVXLCDM]+(?:\.|{DASH_CLASS})\d+(?:\.\d+)*"
CAPTION_ID_BASE_PATTERN = (
    rf"(?:\d+(?:\.\d+)*[A-Za-z]?|"
    rf"[IVXLCDM]+(?:\.|{DASH_CLASS})\d+[A-Za-z]?|"
    rf"A(?:\.|{DASH_CLASS})\d+|"
    rf"[IVXLCDM]+{DASH_CLASS}[IVXLCDM]+)"
)
CAPTION_ID_PATTERN = rf"{CAPTION_ID_BASE_PATTERN}(?:\([a-z]\))?"

BODY_PARA_RE = re.compile(rf"^(?P<id>{BODY_ID_PATTERN})\.(?:\s+|$)(?P<text>.*)")
APPENDIX_PARA_RE = re.compile(rf"^(?P<id>{APPENDIX_ID_PATTERN})\.(?:\s+|$)(?P<text>.*)")
ANNEX_PARA_RE = re.compile(rf"^(?P<id>{ANNEX_ID_PATTERN})\.(?:\s+|$)(?P<text>.*)")
SUBPARA_RE = re.compile(r"^\((?:[a-z]|[ivxlcdm]+|\d+)\)\s+")

FIGURE_RE = re.compile(
    rf"^(?P<label>FIG\.|Fig\.)\s+(?P<num>{CAPTION_ID_PATTERN})(?P<terminator>[.:])\s*(?P<caption>.*)"
)
TABLE_RE = re.compile(
    rf"^TABLE\s+(?P<num>{CAPTION_ID_PATTERN})(?P<terminator>[.:])\s*(?P<title>.*)"
)
TABLE_CONT_RE = re.compile(r"\(cont\.\)", flags=re.IGNORECASE)
REFERENCE_ITEM_RE = re.compile(r"^\[(?P<num>\d+)\]\s*(?P<text>.*)")
FOOTNOTE_RE = re.compile(r"^(?P<num>\d{1,2})\s+(?P<text>[A-Z‘“\"][^\n]{8,})")
REQUIREMENT_RE = re.compile(
    r"^(?P<label>Requirement)\s+(?P<num>\d+[A-Z]?):\s*(?P<text>.*)", flags=re.IGNORECASE
)
PAGE_NUMBER_RE = re.compile(r"^(?P<num>\d{1,4})$")

MAJOR_BODY_HEADING_RE = re.compile(r"^(?P<num>\d+)\.\s+(?P<title>[A-Z].*)")
ANNEX_HEADING_RE = re.compile(r"^Annex(?:\s+(?P<num>[IVXLCDM]+))?\s*$", flags=re.IGNORECASE)
APPENDIX_HEADING_RE = re.compile(r"^Appendix(?:\s+(?P<num>[IVXLCDM]+))?\s*$", flags=re.IGNORECASE)
REFERENCES_HEADING_RE = re.compile(r"^REFERENCES\s*$")
GLOSSARY_HEADING_RE = re.compile(r"^GLOSSARY\s*$")
RELATED_PUBLICATIONS_RE = re.compile(r"^RELATED PUBLICATIONS\s*$")
CONTENTS_HEADING_RE = re.compile(r"^CONTENTS\s*$")
BACKMATTER_HEADING_RE = re.compile(
    r"^(?:CONTRIBUTORS TO DRAFTING AND REVIEW|BODIES FOR THE ENDORSEMENT.*|ORDERING LOCALLY)\s*$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class StructuralLabel:
    """A source label split from its payload and normalized for stable IDs."""

    kind: Literal["paragraph", "table", "figure"]
    canonical_id: str
    raw_label: str
    text: str
    style: str
    terminator: str

    def group(self, name: str) -> str:
        """Compatibility helper for parser code historically using regex matches."""
        values = {
            "id": self.canonical_id,
            "num": self.canonical_id,
            "text": self.text,
            "title": self.text,
            "caption": self.text,
            "terminator": self.terminator,
            "label": self.raw_label.split(maxsplit=1)[0] if self.kind == "figure" else "",
        }
        return values[name]


def match_paragraph_label(line: str) -> StructuralLabel | None:
    """Match a well-formed paragraph label without applying region policy."""
    for style, pattern in (
        ("body", BODY_PARA_RE),
        ("appendix", APPENDIX_PARA_RE),
        ("annex", ANNEX_PARA_RE),
    ):
        match = pattern.match(line)
        if not match:
            continue
        source_id = match.group("id")
        return StructuralLabel(
            kind="paragraph",
            canonical_id=canonical_dash(source_id),
            raw_label=f"{source_id}.",
            text=match.group("text").strip(),
            style=style,
            terminator=".",
        )
    return None


def match_table_label(line: str) -> StructuralLabel | None:
    match = TABLE_RE.match(line)
    if not match:
        return None
    source_id = match.group("num")
    terminator = match.group("terminator")
    return StructuralLabel(
        kind="table",
        canonical_id=canonical_dash(source_id),
        raw_label=f"TABLE {source_id}{terminator}",
        text=match.group("title").strip(),
        style=_caption_style(source_id, terminator),
        terminator=terminator,
    )


def match_figure_label(line: str) -> StructuralLabel | None:
    match = FIGURE_RE.match(line)
    if not match:
        return None
    source_id = match.group("num")
    terminator = match.group("terminator")
    return StructuralLabel(
        kind="figure",
        canonical_id=canonical_dash(source_id),
        raw_label=f"{match.group('label')} {source_id}{terminator}",
        text=match.group("caption").strip(),
        style=_caption_style(source_id, terminator),
        terminator=terminator,
    )


def match_unterminated_label(
    line: str,
    *,
    kind: Literal["paragraph", "table", "figure"],
    raw_label: str,
    canonical_id: str,
) -> StructuralLabel | None:
    """Match one reviewed label exception without broadening the grammar."""
    if line == raw_label:
        payload = ""
    elif line.startswith(f"{raw_label} "):
        payload = line[len(raw_label) :].strip()
    else:
        return None
    return StructuralLabel(
        kind=kind,
        canonical_id=canonical_dash(canonical_id),
        raw_label=raw_label,
        text=payload,
        style="configured_exception",
        terminator="",
    )


def _caption_style(source_id: str, terminator: str) -> str:
    if re.match(rf"^[IVXLCDM]+{DASH_CLASS}[IVXLCDM]+$", source_id):
        base = "roman_roman"
    elif re.match(rf"^[IVXLCDM]+{DASH_CLASS}\d+", source_id):
        base = "roman_dash"
    elif re.match(r"^[IVXLCDM]+\.\d+", source_id):
        base = "roman_dot"
    elif re.match(rf"^A{DASH_CLASS}\d+", source_id):
        base = "appendix_dash"
    elif re.match(r"^A\.\d+", source_id):
        base = "appendix_dot"
    elif re.match(r"^\d+(?:\.\d+)+", source_id):
        base = "numeric_multilevel"
    elif re.match(r"^\d+[A-Za-z]$", source_id):
        base = "numeric_suffix"
    else:
        base = "numeric"
    return f"{base}_{'colon' if terminator == ':' else 'period'}"


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
    "CONTRIBUTORS TO DRAFTING AND REVIEW",
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
    # PDF font maps sometimes expose non-printing C0/C1 bytes in otherwise
    # valid text (for example between an ISBN label and its value). They have
    # no semantic value, and retaining them damages JSON/Markdown knowledge
    # output and downstream search.
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]+", " ", s)
    # Keep en dashes and non-breaking hyphens; collapse other horizontal Unicode whitespace.
    s = re.sub(r"[^\S\n]+", " ", s)
    return s.strip()


def remove_pdf_line_breaks(
    lines: list[str],
    *,
    structural_start: Callable[[str], bool] | None = None,
    preserve_after: Callable[[str], bool] | None = None,
) -> list[str]:
    """Join lines that are likely PDF text-extraction wraps, not document breaks."""
    repaired: list[str] = []
    for raw_line in lines:
        line = normalize_text(raw_line)
        if not line:
            continue
        if repaired and _should_join_pdf_wrapped_line(
            repaired[-1],
            line,
            structural_start=structural_start,
            preserve_after=preserve_after,
        ):
            repaired[-1] = _join_wrapped_text(repaired[-1], line)
        else:
            repaired.append(line)
    return repaired


def _should_join_pdf_wrapped_line(
    previous: str,
    current: str,
    *,
    structural_start: Callable[[str], bool] | None = None,
    preserve_after: Callable[[str], bool] | None = None,
) -> bool:
    starts_structural = structural_start or _starts_new_structural_line
    if starts_structural(current):
        return False
    if match_table_label(previous):
        return False
    previous_figure = match_figure_label(previous)
    if previous_figure and previous_figure.text.endswith((".", "?", "!")):
        return False
    if preserve_after and preserve_after(previous):
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
        or FOOTNOTE_RE.match(line)
        or REQUIREMENT_RE.match(line)
        or REFERENCE_ITEM_RE.match(line)
        or APPENDIX_HEADING_RE.match(line)
        or ANNEX_HEADING_RE.match(line)
        or REFERENCES_HEADING_RE.match(line)
        or GLOSSARY_HEADING_RE.match(line)
        or RELATED_PUBLICATIONS_RE.match(line)
        or CONTENTS_HEADING_RE.match(line)
        or BACKMATTER_HEADING_RE.match(line)
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
    return KNOWN_DOCUMENT_CATEGORIES.get(
        category, re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")
    )


def is_all_caps_heading(line: str) -> bool:
    if not line or len(line) < 4:
        return False
    if FIGURE_RE.match(line) or TABLE_RE.match(line):
        return False
    if BODY_PARA_RE.match(line) or APPENDIX_PARA_RE.match(line) or ANNEX_PARA_RE.match(line):
        return False
    if re.search(r"[=+*]", line):
        return False
    words = re.findall(r"[A-Za-z]+", line)
    if words and all(len(word) == 1 for word in words):
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if len(letters) < 4:
        return False
    uppercase_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
    return uppercase_ratio > 0.82


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
    if element_id and re.match(r"^1\d{2}[A-Z]?(?:\.\d+)*$", element_id):
        return True
    return bool(section_path and section_path[0].startswith("1."))
