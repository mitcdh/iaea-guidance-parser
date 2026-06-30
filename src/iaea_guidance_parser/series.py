from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .metadata import deep_merge, load_config
from .models import DocumentMetadata, StructuralElement
from .parser import IAEAGuidanceParser


@dataclass
class ParsedDocumentResult:
    source_pdf: Path
    output_dir: Path
    metadata: DocumentMetadata
    records: list[StructuralElement]

    @property
    def counts_by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.records:
            out[r.element_type] = out.get(r.element_type, 0) + 1
        return out


@dataclass
class FailedDocumentResult:
    source_pdf: Path
    error: str


def discover_pdfs(pdf_dir: Path, pattern: str = "*.pdf", recursive: bool = True) -> list[Path]:
    """Return PDF files in stable order.

    The suffix check is intentionally case-insensitive so files ending in .PDF
    are included on case-sensitive file systems.
    """
    iterator = pdf_dir.rglob(pattern) if recursive else pdf_dir.glob(pattern)
    return sorted(p for p in iterator if p.is_file() and p.suffix.lower() == ".pdf")


def safe_path_component(value: str, fallback: str = "document") -> str:
    value = value.strip() or fallback
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value or fallback


def build_document_config(
    *,
    pdf_path: Path,
    pdf_root: Path,
    series_config: dict[str, Any] | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the effective parser config for one PDF.

    Precedence, from lowest to highest:
    1. series-level defaults under `series` and `parser`;
    2. `document_defaults` and `fallbacks` in the series config;
    3. per-document overrides under `documents` in the series config;
    4. optional YAML file in `config_dir` named by PDF stem or file name.

    Document category/type are inferred from each PDF unless explicitly supplied
    under `document_defaults`, per-document overrides or config-dir YAML. A
    `fallbacks.document_category` is only used when the parser cannot infer the
    category from the publication itself.
    """
    series_config = series_config or {}
    series = series_config.get("series", {}) or {}

    base_document = {
        k: v
        for k, v in {
            "series_name": series.get("series_name"),
            "document_family": series.get("document_family") or series.get("series_name"),
            "document_domain": series.get("document_domain"),
            "document_subdomain": series.get("document_subdomain"),
            "publisher": series.get("publisher"),
            "publication_place": series.get("publication_place"),
            "language": series.get("language"),
        }.items()
        if v not in (None, "")
    }

    base_config: dict[str, Any] = {
        "document": base_document,
        "parser": series_config.get("parser", {}) or {},
        "fallbacks": series_config.get("fallbacks", {}) or {},
    }

    # Optional convenience keys for fallback values at series level.
    fallback_updates = {
        k: v
        for k, v in {
            "series_name": series.get("series_name"),
            "document_family": series.get("document_family") or series.get("series_name"),
            "document_domain": series.get("document_domain"),
            "document_subdomain": series.get("document_subdomain"),
            "document_category": series.get("default_document_category"),
            "document_type": series.get("default_document_type"),
        }.items()
        if v not in (None, "")
    }
    base_config = deep_merge(base_config, {"fallbacks": fallback_updates})

    # `document_defaults` intentionally forces values across every document.
    # Use `fallbacks` instead when values should apply only if inference fails.
    if series_config.get("document_defaults"):
        base_config = deep_merge(base_config, {"document": series_config.get("document_defaults") or {}})

    embedded_override = _find_embedded_document_override(pdf_path, pdf_root, series_config.get("documents"))
    file_override = _find_config_file_override(pdf_path, config_dir)
    return deep_merge(base_config, embedded_override, file_override)


def parse_one_document(
    *,
    pdf_path: Path,
    pdf_root: Path,
    out_root: Path,
    series_config: dict[str, Any] | None = None,
    config_dir: Path | None = None,
):
    cfg = build_document_config(
        pdf_path=pdf_path,
        pdf_root=pdf_root,
        series_config=series_config,
        config_dir=config_dir,
    )
    parser = IAEAGuidanceParser.from_pdf_config(pdf_path, cfg)
    metadata, records = parser.parse()
    doc_dir = out_root / "documents" / safe_path_component(metadata.document_id, pdf_path.stem)
    return ParsedDocumentResult(source_pdf=pdf_path, output_dir=doc_dir, metadata=metadata, records=records)


def _find_config_file_override(pdf_path: Path, config_dir: Path | None) -> dict[str, Any]:
    if not config_dir:
        return {}
    candidates = [
        config_dir / f"{pdf_path.name}.yaml",
        config_dir / f"{pdf_path.stem}.yaml",
        config_dir / f"{pdf_path.name}.yml",
        config_dir / f"{pdf_path.stem}.yml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return load_config(candidate)
    return {}


def _find_embedded_document_override(pdf_path: Path, pdf_root: Path, documents_config: Any) -> dict[str, Any]:
    if not documents_config:
        return {}

    try:
        rel = str(pdf_path.relative_to(pdf_root))
    except ValueError:
        rel = pdf_path.name

    keys = {pdf_path.name, pdf_path.stem, rel, rel.replace("\\", "/")}

    if isinstance(documents_config, dict):
        for key in keys:
            value = documents_config.get(key)
            if value:
                return _coerce_document_override(value)
        return {}

    if isinstance(documents_config, list):
        for item in documents_config:
            if not isinstance(item, dict):
                continue
            item_keys = {
                str(item.get("source_file", "")),
                str(item.get("file", "")),
                str(item.get("filename", "")),
                str(item.get("stem", "")),
                str(item.get("pdf", "")),
            }
            if keys.intersection(item_keys):
                return _coerce_document_override(item)
    return {}


def _coerce_document_override(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if any(k in value for k in ("document", "parser", "fallbacks")):
        return value
    return {"document": value}
