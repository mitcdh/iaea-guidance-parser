# IAEA guidance parser

This Python environment precomputes structural indexes and Custom GPT knowledge input for IAEA guidance PDFs. It is designed for two broad IAEA runs: Safety and Security.

The parser records document type in both outputs. For example, `document_category: Technical Guidance` and `document_type: technical_guidance` are included in `metadata.json`, every row of `structural_index.jsonl`, and every row/chunk in `custom_gpt_knowledge.jsonl` and `custom_gpt_knowledge.md`.

PDF text-extraction line wraps are repaired before parsing so paragraph text and GPT chunks do not retain line breaks introduced by PDF encoding.

## Install

### Option A: venv + pip

```bash
cd iaea-guidance-parser
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e .
```

### Option B: conda

```bash
cd iaea-guidance-parser
conda env create -f environment.yml
conda activate iaea-guidance-parser
pip install -e .
```

## Parse a single document

```bash
iaea-guidance-parser parse /path/to/PUB1921_web.pdf \
  --config configs/nss17t.yaml \
  --out outputs/NSS-17-T-REV1
```

Equivalent module form:

```bash
python -m iaea-guidance-parser parse /path/to/PUB1921_web.pdf \
  --config configs/nss17t.yaml \
  --out outputs/NSS-17-T-REV1
```

## Parse a whole Safety or Security directory

Use the `series` command once for the Security folder and once for the Safety folder. Each run writes one combined output set for that folder.

```bash
iaea-guidance-parser series /path/to/Security \
  --series-config configs/nuclear_security_series.yaml \
  --out outputs/Security

iaea-guidance-parser series /path/to/Safety \
  --series-config configs/nuclear_safety_series.yaml \
  --out outputs/Safety
```

This writes per-document outputs under `outputs/<Safety-or-Security>/documents/<document_id>/` and combined series outputs at `outputs/Safety/` or `outputs/Security/`.

Useful options:

```bash
# Search subdirectories, default behaviour
iaea-guidance-parser series /path/to/pdfs --recursive

# Only search the top-level directory
iaea-guidance-parser series /path/to/pdfs --no-recursive

# Restrict to a filename pattern
iaea-guidance-parser series /path/to/pdfs --pattern 'PUB*.pdf'

# Run only the first few PDFs as a test
iaea-guidance-parser series /path/to/pdfs --limit 3

# Stop on the first failed PDF instead of continuing
iaea-guidance-parser series /path/to/pdfs --fail-fast

# Add per-document YAML overrides keyed by PDF stem or filename
iaea-guidance-parser series /path/to/pdfs \
  --series-config configs/nuclear_security_series.yaml \
  --config-dir configs/per_document \
  --out outputs/Security
```

## Series-level configuration

For the Security folder, use:

```yaml
series:
  series_id: Security
  series_name: IAEA Nuclear Security Series
  document_family: IAEA Nuclear Security Series
  document_domain: nuclear_security
  publisher: International Atomic Energy Agency
  publication_place: Vienna
  language: en

parser:
  include_text_blocks: true

fallbacks:
  series_name: IAEA Nuclear Security Series
  document_family: IAEA Nuclear Security Series
  document_domain: nuclear_security
```

For the Safety folder, use:

```yaml
series:
  series_id: Safety
  series_name: IAEA Safety Standards Series
  document_family: IAEA Safety Standards Series
  document_domain: nuclear_safety

fallbacks:
  series_name: IAEA Safety Standards Series
  document_family: IAEA Safety Standards Series
  document_domain: nuclear_safety
```

Document category and type are normally inferred from each PDF cover/title page. This matters because each broad folder can contain different categories. Security categories include Nuclear Security Fundamentals, Nuclear Security Recommendations, Implementing Guides and Technical Guidance. Safety categories preserve declared categories such as Safety Fundamentals, General Safety Requirements, Specific Safety Requirements, General Safety Guide, Specific Safety Guide and older Safety Guide publications.

Use `document_defaults` only when you want to force the same metadata value onto every PDF in the directory:

```yaml
document_defaults:
  document_category: Technical Guidance
  document_type: technical_guidance
```

Per-document overrides can be embedded in the series config:

```yaml
documents:
  PUB1921_web.pdf:
    document:
      document_id: NSS-17-T-REV1
      document_category: Technical Guidance
      document_type: technical_guidance
      document_subdomain: computer_security_for_nuclear_facilities
```

Or supplied as separate files under `--config-dir`, for example `configs/per_document/PUB1921_web.yaml`.

## Outputs for a single document

Each single-document run writes:

- `metadata.json` — document-level metadata, including document family/category/type.
- `structural_index.jsonl` — one record per paragraph, figure, table, footnote, heading, reference or text block.
- `custom_gpt_knowledge.jsonl` — structured chunks for scripts, API workflows or custom retrieval systems.
- `custom_gpt_knowledge.md` — a text-forward Knowledge file for Custom GPT upload.
- `structural_index_preview.csv` — spreadsheet-friendly preview.
- `qa_report.md` — element counts and basic validation checks.

## Outputs for a series directory

A series run writes all of the single-document outputs for each PDF, plus these combined outputs:

- `series_structural_index.jsonl` — combined structural index across all parsed PDFs.
- `series_custom_gpt_knowledge.jsonl` — structured combined chunks for scripts, API workflows or custom retrieval systems.
- `series_custom_gpt_knowledge.md` — compact, text-forward combined Custom GPT input retained for local use and archival.
- `series_custom_gpt_knowledge_parts/part_*.md` — compact upload-sized Markdown parts for Custom GPT Knowledge.
- `series_manifest.json` — document inventory, metadata, SHA-256 hashes, element counts and failures.
- `series_manifest.csv` — spreadsheet-friendly series inventory.
- `series_qa_report.md` — aggregate QA report and document-type checks.
- `series_config_effective.json` — the series config used for the run.

## Uploading to a Custom GPT

For a Custom GPT, upload the numbered Markdown files in the relevant parts directory:

```text
outputs/Security/series_custom_gpt_knowledge_parts/part_*.md
outputs/Safety/series_custom_gpt_knowledge_parts/part_*.md
```

Use the Security parts for a Security GPT and the Safety parts for a Safety GPT. Upload all numbered parts for that series to the same GPT.

The unsplit `series_custom_gpt_knowledge.md` is still written because it is convenient for local search, review and archival use. It may be rejected by the Custom GPT builder with a message such as "This file contains too much text content" because the builder applies an extracted-text/indexing limit separately from ordinary file size.

The Markdown Knowledge files use compact per-record fields to reduce upload size. Each file/part includes a status and region legend, so repeated SPESS C status reasons are not written on every paragraph. Full record metadata and status reasons remain available in `series_structural_index.jsonl`.

The manifest and QA report are optional. Upload them only if you want the GPT to answer coverage or parser-quality questions such as which publications were included, whether any PDFs failed, or how the records were categorized.

Suggested Custom GPT descriptions:

```text
IAEA Nuclear Security Series Assistant

Unofficial assistant for searching and summarizing uploaded IAEA Nuclear Security Series publications. Not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications.
```

```text
IAEA Nuclear Safety Standards Assistant

Unofficial assistant for searching and summarizing uploaded IAEA Safety Standards Series publications. Not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications.
```

Every combined record preserves the document identity and type:

```json
{
  "document_id": "NSS-17-T-REV1",
  "document_family": "IAEA Nuclear Security Series",
  "document_category": "Technical Guidance",
  "document_type": "technical_guidance",
  "document_domain": "nuclear_security",
  "series_name": "IAEA Nuclear Security Series",
  "series_number": "No. 17-T (Rev. 1)",
  "element_type": "paragraph",
  "element_id": "4.10",
  "source_region": "Body",
  "text_status": "Normative"
}
```

## Classification rules

The default status rules follow the structure guidance in SPESS C:

- Section 1 body material is `Informational` because it introduces the publication and should not contain requirements, recommendations or guidance;
- main body numbered paragraphs from Section 2 onward are `Normative` because they present the primary technical content;
- appendix paragraphs are `Normative` because appendix material is integral and has the same status as the main text;
- annex paragraphs are `Informative` because annexes provide examples or additional explanation and are not integral;
- footnotes are always `Informative` because SPESS C treats them like annexes: additional information or explanation, not requirements/recommendations/guidance;
- front matter, references, glossary, publication metadata and back matter are `Informational`;
- figures and tables inherit the status of their region: Section 2+ body and appendix = normative structural elements, Section 1 and other informational regions = informational, annex = informative.

## Structural record shape

A paragraph record looks like:

```json
{
  "document_id": "NSS-17-T-REV1",
  "document_category": "Technical Guidance",
  "document_type": "technical_guidance",
  "element_type": "paragraph",
  "element_id": "4.10",
  "source_region": "Body",
  "text_status": "Normative",
  "section_path": ["4. FACILITY COMPUTER SECURITY RISK MANAGEMENT", "OUTLINE OF FACILITY COMPUTER SECURITY RISK MANAGEMENT"],
  "page_start_pdf": 38,
  "page_end_pdf": 39,
  "text": "The following are the phases of facility CSRM: ..."
}
```

## Known limitations

This is a deterministic first pass. For high-assurance use, review:

- low-confidence footnote records;
- complex tables that span multiple pages;
- figure visual content, because the parser captures captions and page locations but does not interpret diagrams;
- section headings split across multiple lines.

The parser deliberately preserves raw table text to avoid inventing row/column boundaries where the PDF extraction is ambiguous.
