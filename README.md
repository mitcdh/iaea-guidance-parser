# IAEA Guidance Parser

This project turns folders of IAEA PDF publications into files that are easier to search, review and upload to a Custom GPT or other LLM tool.

It is designed for two separate runs:

- `Security` — IAEA Nuclear Security Series publications.
- `Safety` — IAEA Safety Standards Series publications.

The parser does not download PDFs. Put the PDFs in local folders, run the parser once for each folder, and it will create structured output files under `outputs/`.

PDF extraction often adds artificial line breaks in the middle of sentences. This parser repairs those line breaks before writing the output, so the text is more suitable for search and Custom GPT knowledge files.

## What You Get

For each series, the parser creates:

- upload-ready Markdown parts for a Custom GPT;
- a full structured index of every paragraph, table, figure caption, heading and reference;
- a manifest showing which PDFs were included;
- a QA report with parser counts and basic checks.

For most Custom GPT use, the important files are:

```text
outputs/Security/series_custom_gpt_knowledge_parts/part_*.md
outputs/Safety/series_custom_gpt_knowledge_parts/part_*.md
```

Upload all numbered parts for the one series you want the GPT to use.

## Install

### Using venv and pip

```bash
cd iaea-standard-parser
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows, activate the virtual environment with:

```bash
.venv\Scripts\activate
```

### Using conda

```bash
cd iaea-standard-parser
conda env create -f environment.yml
conda activate iaea-guidance-parser
pip install -e .
```

## Run the Parser

Run the parser once for Security and once for Safety.

If your folders are named `Security` and `Safety` in this project directory, use:

```bash
iaea-guidance-parser series Security \
  --series-config configs/nuclear_security_series.yaml \
  --out outputs/Security

iaea-guidance-parser series Safety \
  --series-config configs/nuclear_safety_series.yaml \
  --out outputs/Safety
```

If the command `iaea-guidance-parser` is not available, use the Python module form instead:

```bash
python -m iaea_guidance_parser series Security \
  --series-config configs/nuclear_security_series.yaml \
  --out outputs/Security
```

The parser writes one folder per document under:

```text
outputs/Security/documents/
outputs/Safety/documents/
```

It also writes combined series files directly under:

```text
outputs/Security/
outputs/Safety/
```

## Upload to a Custom GPT

Create one GPT for Security and one GPT for Safety. Keep the two series separate unless you have a specific reason to combine them.

For a Security GPT, upload:

```text
outputs/Security/series_custom_gpt_knowledge_parts/part_001_of_003.md
outputs/Security/series_custom_gpt_knowledge_parts/part_002_of_003.md
outputs/Security/series_custom_gpt_knowledge_parts/part_003_of_003.md
```

For a Safety GPT, upload:

```text
outputs/Safety/series_custom_gpt_knowledge_parts/part_001_of_010.md
...
outputs/Safety/series_custom_gpt_knowledge_parts/part_010_of_010.md
```

Do not upload the large unsplit `series_custom_gpt_knowledge.md` if the Custom GPT builder rejects it with:

```text
This file contains too much text content. Please try again with a smaller file.
```

Use the numbered files in `series_custom_gpt_knowledge_parts/` instead.

The manifest and QA report are optional. Upload them only if you want the GPT to answer questions about coverage, provenance or parser quality, such as which publications were included or whether any PDFs failed. Do not treat those files as substantive IAEA guidance.

Suggested Custom GPT descriptions:

```text
IAEA Nuclear Security Series Assistant

Unofficial assistant for searching and summarizing uploaded IAEA Nuclear Security Series publications. Not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications.
```

```text
IAEA Nuclear Safety Standards Assistant

Unofficial assistant for searching and summarizing uploaded IAEA Safety Standards Series publications. Not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications.
```

### Custom GPT instructions

Paste this into the Custom GPT `Instructions` field. Replace `[Safety/Security]` and the series name with the one you are building.

```text
You are an unofficial assistant for searching, summarizing and explaining uploaded IAEA [Safety/Security] publications.

You are not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications. Always make clear that users should consult the official IAEA publication for authoritative wording and decisions.

Use only the uploaded knowledge files as the source of substantive IAEA guidance. Do not invent guidance, requirements, recommendations, publication titles, paragraph numbers or citations that are not supported by the uploaded files.

The uploaded knowledge files were generated from IAEA PDFs. They contain compact records with fields such as:
- doc
- record
- status
- region
- pdf
- section
- text

Interpret the fields as follows:
- `doc` identifies the source publication.
- `record` identifies the parsed item, such as a paragraph, table, figure caption, heading, reference or text block.
- `status` gives the parser's normative status label.
- `region` gives the source region of the publication.
- `pdf` gives the physical PDF page number or page range.
- `section` gives the section path when available.
- `text` is the extracted publication text.

Use the `status` field carefully:
- `Normative` means main technical content, usually Section 2 onward, or appendix material.
- `Informative` means supporting material such as annexes or footnotes.
- `Informational` means front matter, references, glossary, headings, back matter or introductory material.

The status labels are based on SPESS C structural guidance:
- Section 1 introduces the publication and should not contain requirements, recommendations or guidance.
- Numbered main-text sections from Section 2 onward present the primary technical content.
- Appendices are integral and have the same status as the main text.
- Annexes provide examples or additional information or explanation and are not integral.
- Footnotes provide additional information or explanation and should not contain requirements, recommendations or guidance.

When answering:
- Prefer direct answers in plain language.
- Cite the source publication, section or paragraph when available.
- Include PDF page numbers when useful.
- Distinguish clearly between requirements, recommendations, guidance, examples and background information when the uploaded records support that distinction.
- If the answer depends on exact wording, quote only a short relevant excerpt and identify where it came from.
- If the uploaded files do not contain enough information to answer, say so clearly.
- If multiple publications are relevant, compare them and cite each one separately.
- If the user asks for a list, checklist or summary, preserve the meaning of the source text and do not make it sound more mandatory than the source supports.
- If the user asks a safety, security, legal, regulatory or compliance question, remind them to verify against the official IAEA publication and applicable national requirements.

Use manifest and QA files only for coverage, provenance and parser-quality questions; do not treat them as substantive IAEA guidance.

Do not rely on parser metadata alone when the publication text itself answers the question. Use metadata to identify documents, categories and provenance, not to replace substantive source text.

Do not treat figure captions as the full content of a figure. The parser captures captions and page locations, not the visual diagram itself.

Do not treat raw table text as perfectly reconstructed row and column data if the layout is ambiguous. Explain any uncertainty and point to the source page.

Do not browse the web unless the user explicitly asks for information outside the uploaded files. If browsing or outside knowledge is used, clearly separate it from the uploaded IAEA material.

Default answer style:
- Start with the answer.
- Then give supporting citations from the uploaded files.
- Then add any caveats about status, scope or source limitations.
```

## Output Files Explained

### Combined series outputs

These are the files you will usually care about after a full Safety or Security run:

| File | What it is for |
| --- | --- |
| `series_custom_gpt_knowledge_parts/part_*.md` | Upload these numbered Markdown files to a Custom GPT. |
| `series_custom_gpt_knowledge.md` | One large local reference file. Useful for review, but often too large for Custom GPT upload. |
| `series_structural_index.jsonl` | Full structured record of the parsed series. Best for scripts, data analysis and detailed checking. |
| `series_manifest.json` | Inventory of parsed PDFs, metadata, hashes, counts and failures. |
| `series_manifest.csv` | Spreadsheet-friendly version of the manifest. |
| `series_qa_report.md` | Human-readable parser QA summary. |
| `series_config_effective.json` | The configuration used for the run. |

### Per-document outputs

Each PDF also gets its own output folder with:

| File | What it is for |
| --- | --- |
| `metadata.json` | Metadata for that publication. |
| `structural_index.jsonl` | Paragraphs, tables, figure captions, references and headings for that document. |
| `custom_gpt_knowledge.md` | Markdown knowledge text for that document only. |
| `custom_gpt_knowledge.jsonl` | Structured knowledge chunks for technical workflows. |
| `structural_index_preview.csv` | Spreadsheet preview. |
| `qa_report.md` | Document-level parser summary. |

## How the Parser Labels Content

Each record is labelled with a source region and a text status.

Source regions include:

- `FrontMatter`
- `Body`
- `Appendix`
- `Annex`
- `References`
- `Glossary`
- `BackMatter`

Text status values are:

- `Normative` — main technical content, usually Section 2 onward, plus appendices.
- `Informative` — supporting material such as annexes and footnotes.
- `Informational` — front matter, references, headings, glossary and introductory material.

The status rules are based on SPESS C:

- Section 1 introduces the publication and is treated as informational.
- Section 2 onward contains the primary technical content and is treated as normative.
- Appendices are integral to the publication and have the same status as the main text.
- Annexes provide examples or additional explanation and are informative.
- Footnotes are informative.

## Safety and Security Categories

The parser keeps the declared publication category for each document.

Security categories include:

- Nuclear Security Fundamentals
- Nuclear Security Recommendations
- Implementing Guides
- Technical Guidance

Safety categories include:

- Safety Fundamentals
- General Safety Requirements
- Specific Safety Requirements
- General Safety Guide
- Specific Safety Guide
- older Safety Guide publications

These categories appear in the structured outputs as fields such as:

```json
{
  "document_category": "Technical Guidance",
  "document_type": "technical_guidance",
  "document_domain": "nuclear_security"
}
```

## Run a Single PDF

You normally do not need this for Custom GPT preparation, but it is useful for testing one document.

```bash
iaea-guidance-parser parse /path/to/document.pdf \
  --config configs/nss17t.yaml \
  --out outputs/example-document
```

Module form:

```bash
python -m iaea_guidance_parser parse /path/to/document.pdf \
  --config configs/nss17t.yaml \
  --out outputs/example-document
```

## Configuration

The two main configuration files are:

```text
configs/nuclear_security_series.yaml
configs/nuclear_safety_series.yaml
```

They set the broad series name and domain. The parser normally infers the individual publication type from each PDF.

For example, the Security config says the folder belongs to the IAEA Nuclear Security Series:

```yaml
series:
  series_id: Security
  series_name: IAEA Nuclear Security Series
  document_family: IAEA Nuclear Security Series
  document_domain: nuclear_security

parser:
  include_text_blocks: true
```

The Safety config says the folder belongs to the IAEA Safety Standards Series:

```yaml
series:
  series_id: Safety
  series_name: IAEA Safety Standards Series
  document_family: IAEA Safety Standards Series
  document_domain: nuclear_safety

parser:
  include_text_blocks: true
```

Use per-document overrides only when the parser cannot infer a value correctly. For example:

```yaml
documents:
  PUB1921_web.pdf:
    document:
      document_id: NSS-17-T-REV1
      title: Computer Security Techniques for Nuclear Facilities
      document_category: Technical Guidance
      document_type: technical_guidance
```

## Known Limitations

This is a deterministic parser, not a legal or technical reviewer. For high-assurance work, check the source PDF.

Known limitations:

- Complex tables are kept as raw PDF text. The parser does not invent row or column structure where the PDF is ambiguous.
- Figure images are not extracted or interpreted. The parser captures figure captions and page locations.
- Some section headings may be split across multiple lines.
- Footnotes are detected by text pattern and may need review.
- Very old PDFs with unusual embedded fonts may still need manual metadata overrides.
