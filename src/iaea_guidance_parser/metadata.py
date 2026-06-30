from __future__ import annotations

import hashlib
import re
from pathlib import Path
from copy import deepcopy
from typing import Any

import yaml

from .models import DocumentMetadata, PageText
from .rules import KNOWN_DOCUMENT_CATEGORIES, canonical_dash, slugify_category


DOCUMENT_CATEGORY_ALIASES = {
    "Implementing Guide": "Implementing Guides",
    "Implementing Guides": "Implementing Guides",
    "General Safety Guides": "General Safety Guide",
    "Specific Safety Guides": "Specific Safety Guide",
    "Safety Guides": "Safety Guide",
}

DOCUMENT_CATEGORY_LABELS = tuple(dict.fromkeys([*KNOWN_DOCUMENT_CATEGORIES.keys(), *DOCUMENT_CATEGORY_ALIASES.keys()]))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def deep_merge(*configs: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively merge dictionaries without mutating any input.

    Later dictionaries win. This is used to combine a series-level config,
    per-document overrides and command-line supplied defaults.
    """
    merged: dict[str, Any] = {}
    for cfg in configs:
        if not cfg:
            continue
        merged = _deep_merge_two(merged, cfg)
    return merged


def _deep_merge_two(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_two(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def infer_metadata(pdf_path: Path, pages: list[PageText], config: dict[str, Any] | None = None) -> DocumentMetadata:
    config = config or {}
    cfg_doc = config.get("document", {}) or {}
    cfg_fallback = config.get("fallbacks", {}) or {}
    first_text = "\n".join(p.text for p in pages[:10])
    all_text = "\n".join(p.text for p in pages[:20])

    title = cfg_doc.get("title") or _infer_title(first_text) or cfg_fallback.get("title", "")
    inferred_series_name = _infer_series_name(first_text)
    series_name = cfg_doc.get("series_name") or inferred_series_name or cfg_fallback.get("series_name", "")
    series_number = cfg_doc.get("series_number") or _infer_series_number(first_text) or cfg_fallback.get("series_number", "")
    domain = cfg_doc.get("document_domain") or cfg_fallback.get("document_domain") or _infer_document_domain(first_text, series_name)
    inferred_category = _infer_category(
        first_text,
        series_number=series_number,
        document_domain=domain,
        source_name=pdf_path.name,
    )
    category = cfg_doc.get("document_category") or inferred_category or cfg_fallback.get("document_category", "")
    document_type = cfg_doc.get("document_type") or (slugify_category(category) if category else cfg_fallback.get("document_type", ""))
    year = cfg_doc.get("publication_year") or _infer_year(first_text) or cfg_fallback.get("publication_year")
    sti = cfg_doc.get("sti_pub_number") or _infer_sti(all_text) or cfg_fallback.get("sti_pub_number", "")
    isbn_pdf = cfg_doc.get("isbn_pdf") or _infer_isbn_pdf(all_text) or cfg_fallback.get("isbn_pdf", "")
    family = cfg_doc.get("document_family") or series_name or cfg_fallback.get("document_family", "")
    document_id = cfg_doc.get("document_id") or _make_document_id(series_number, title, series_name, domain)

    return DocumentMetadata(
        document_id=document_id,
        source_file=str(pdf_path),
        source_sha256=sha256_file(pdf_path),
        title=title,
        subtitle=cfg_doc.get("subtitle", ""),
        publisher=cfg_doc.get("publisher", "International Atomic Energy Agency"),
        publication_year=int(year) if year else None,
        publication_place=cfg_doc.get("publication_place", "Vienna"),
        series_name=series_name,
        series_number=series_number,
        document_family=family,
        document_category=category,
        document_type=document_type,
        document_domain=domain,
        document_subdomain=cfg_doc.get("document_subdomain") or cfg_fallback.get("document_subdomain", ""),
        sti_pub_number=sti,
        isbn_pdf=isbn_pdf,
        language=cfg_doc.get("language", "en"),
        metadata_source={
            "document_id": "config" if cfg_doc.get("document_id") else "inferred",
            "title": "config" if cfg_doc.get("title") else "inferred",
            "series_name": "config" if cfg_doc.get("series_name") else "inferred",
            "series_number": "config" if cfg_doc.get("series_number") else "inferred",
            "document_category": "config" if cfg_doc.get("document_category") else ("inferred" if inferred_category else "fallback"),
            "document_type": "config" if cfg_doc.get("document_type") else ("inferred_from_category" if category else "fallback"),
        },
    )


def _infer_title(text: str) -> str:
    # Title page usually presents title as 2-4 lines after category.
    if "COMPUTER SECURITY" in text and "NUCLEAR FACILITIES" in text:
        return "Computer Security Techniques for Nuclear Facilities"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = [line for line in lines if line.isupper() and len(line) > 8 and "IAEA" not in line and "SERIES" not in line]
    return " ".join(candidates[:3]).title() if candidates else ""


def _infer_series_name(text: str) -> str:
    if re.search(r"IAEA\s+Nuclear\s+Security\s+Series|NUCLEAR SECURITY SERIES", text, flags=re.I):
        return "IAEA Nuclear Security Series"
    if re.search(r"IAEA\s+Safety\s+Standards\s+Series|SAFETY STANDARDS SERIES", text, flags=re.I):
        return "IAEA Safety Standards Series"
    if re.search(r"IAEA\s+Safety\s+Series|SAFETY SERIES", text, flags=re.I):
        return "IAEA Safety Series"
    return ""


def _infer_series_number(text: str) -> str:
    pats = [
        r"IAEA\s+Nuclear\s+Security\s+Series\s+No\.\s*([^\n]+)",
        r"NUCLEAR\s+SECURITY\s+SERIES\s+No\.\s*([^\n]+)",
        r"IAEA\s+Safety\s+Standards\s+Series\s+No\.\s*([^\n]+)",
        r"SAFETY\s+STANDARDS\s+SERIES\s+No\.\s*([^\n]+)",
        r"IAEA\s+Safety\s+Series\s+No\.\s*([^\n]+)",
        r"SAFETY\s+SERIES\s+No\.\s*([^\n]+)",
    ]
    for pat in pats:
        m = re.search(pat, text, flags=re.I)
        if m:
            raw = m.group(1).strip()
            raw = raw.split("\n")[0].strip()
            return "No. " + canonical_dash(raw).replace("‑", "-")
    return ""


def _infer_document_domain(text: str, series_name: str) -> str:
    haystack = f"{series_name}\n{text}"
    if re.search(r"nuclear\s+security|NUCLEAR SECURITY", haystack, flags=re.I):
        return "nuclear_security"
    if re.search(r"safety\s+standards|nuclear\s+safety|SAFETY STANDARDS|SAFETY SERIES", haystack, flags=re.I):
        return "nuclear_safety"
    return ""


def _infer_category(text: str, series_number: str = "", document_domain: str = "", source_name: str = "") -> str:
    # Prefer standalone cover/title-page occurrences. The standard NSS front
    # matter includes a generic list of all publication categories, so a simple
    # substring search will often pick the wrong category.
    early_lines = [_clean_category_line(line) for line in text.splitlines()[:120] if line.strip()]
    source_haystack = "\n".join([series_number, source_name])

    family = _infer_category_family(source_haystack, document_domain)
    if family:
        return family

    for line in early_lines:
        for cat in DOCUMENT_CATEGORY_LABELS:
            if _same_category_line(line, cat):
                return _canonical_category(cat)

    for line in _category_candidate_lines(early_lines):
        family = _infer_category_family(line, document_domain)
        if family:
            return family

    # Next, allow very short lines that contain only a category plus small title
    # page adornments, while still avoiding explanatory category-list sentences.
    explanatory_words = {"specify", "provide", "provides", "set out", "issued", "focus", "basis"}
    for line in early_lines:
        low = line.lower()
        if len(line) > 70 or any(word in low for word in explanatory_words):
            continue
        for cat in DOCUMENT_CATEGORY_LABELS:
            if re.search(rf"\b{re.escape(cat)}\b", line, flags=re.I):
                return _canonical_category(cat)

    # Last resort: search the whole text, but still require a standalone line so
    # that the generic category list is less likely to dominate.
    for line in (_clean_category_line(line) for line in text.splitlines() if line.strip()):
        for cat in DOCUMENT_CATEGORY_LABELS:
            if _same_category_line(line, cat):
                return _canonical_category(cat)
    return ""


def _category_candidate_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    generic_category_page_seen = False
    for line in lines:
        if re.search(r"\bCATEGORIES IN THE IAEA NUCLEAR SECURITY SERIES\b", line, flags=re.I):
            generic_category_page_seen = True
            continue
        if generic_category_page_seen:
            continue
        if len(line) <= 90:
            out.append(line)
    return out


def _infer_category_family(text: str, document_domain: str = "") -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    normalized = canonical_dash(compact).replace("–", "-")

    if re.search(r"\bNuclear Security Fundamentals\b", compact, flags=re.I):
        return "Nuclear Security Fundamentals"
    if re.search(r"\bNuclear Security Recommendations\b", compact, flags=re.I):
        return "Nuclear Security Recommendations"
    if re.search(r"\bTechnical Guidance\b", compact, flags=re.I):
        return "Technical Guidance"
    if re.search(r"\bImplementing Guides?\b", compact, flags=re.I):
        return "Implementing Guides"

    if re.search(r"\bSafety Fundamentals\b", compact, flags=re.I) or re.search(r"\bSF-\d+\b", normalized, flags=re.I):
        return "Safety Fundamentals"
    if re.search(r"\b(?:GSR|GS-R)\b", normalized, flags=re.I):
        return "General Safety Requirements"
    if re.search(r"\bSSR\b", normalized, flags=re.I):
        return "Specific Safety Requirements"
    if re.search(r"\bNS-R\b", normalized, flags=re.I):
        return "Safety Requirements"
    if re.search(r"\b(?:GSG|GS-G)\b", normalized, flags=re.I):
        return "General Safety Guide"
    if re.search(r"\bSSG\b", normalized, flags=re.I):
        return "Specific Safety Guide"
    if re.search(r"\b(?:WS-G|NS-G|RS-G|TS-G)\b", normalized, flags=re.I):
        return "Safety Guide"
    if re.search(r"\bGeneral\s*Safety Requirements\b", compact, flags=re.I):
        return "General Safety Requirements"
    if re.search(r"\bSpecific\s*Safety Requirements\b", compact, flags=re.I):
        return "Specific Safety Requirements"
    if re.search(r"\bSafety Requirements\b", compact, flags=re.I):
        return "Safety Requirements"
    if re.search(r"\bGeneral\s*Safety Guides?\b", compact, flags=re.I):
        return "General Safety Guide"
    if re.search(r"\bSpecific\s*Safety Guides?\b", compact, flags=re.I):
        return "Specific Safety Guide"
    if re.search(r"\bSafety Guides?\b", compact, flags=re.I):
        return "Safety Guide"

    if document_domain == "nuclear_security":
        if re.search(r"\b(?:NSS)?\s*\d+\s*-\s*(?:G|G\s*REV\d+)\b", normalized, flags=re.I):
            return "Implementing Guides"
        if re.search(r"\b(?:NSS)?\s*\d+\s*-\s*(?:T|T\s*REV\d+)\b", normalized, flags=re.I):
            return "Technical Guidance"
    return ""


def _canonical_category(category: str) -> str:
    return DOCUMENT_CATEGORY_ALIASES.get(category, category)


def _clean_category_line(line: str) -> str:
    line = line.strip().strip(" .")
    line = re.sub(r"^[\s•●\-*\x07]+", "", line).strip().strip(" .")
    return line


def _same_category_line(line: str, category: str) -> bool:
    return _clean_category_line(line).lower() == category.lower()


def _infer_year(text: str) -> int | None:
    for pat in [r"VIENNA,\s*(19\d{2}|20\d{2})", r"©\s*IAEA,\s*(19\d{2}|20\d{2})"]:
        m = re.search(pat, text)
        if m:
            return int(m.group(1))
    return None


def _infer_sti(text: str) -> str:
    m = re.search(r"STI/PUB/\d+", text)
    return m.group(0) if m else ""


def _infer_isbn_pdf(text: str) -> str:
    m = re.search(r"ISBN\s+([0-9–\-]+)\s*\(pdf\)", text, flags=re.I)
    return m.group(1) if m else ""


def _make_document_id(series_number: str, title: str, series_name: str = "", document_domain: str = "") -> str:
    if series_number:
        s = canonical_dash(series_number).replace("–", "-")
        s = re.sub(r"(?i)^No\.\s*", "", s).strip()
        if document_domain == "nuclear_security" or "security" in series_name.lower():
            if not re.match(r"(?i)^NSS[-_]", s):
                s = f"NSS-{s}"
        elif document_domain == "nuclear_safety" or "safety" in series_name.lower():
            if re.match(r"^\d", s):
                s = f"SAFETY-{s}"
        s = re.sub(r"(?i)\(\s*Rev\.?\s*(\d+)\s*\)", r"Rev\1", s)
        s = re.sub(r"(?i)Rev\.?\s*(\d+)", r"Rev\1", s)
        s = s.replace(".", "")
        s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
        return s.upper()
    s = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").upper()
    return s[:80] or "IAEA-DOCUMENT"
