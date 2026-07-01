# IAEA Guidance Parser

This project turns local IAEA PDF folders into smaller, searchable knowledge files for a Custom GPT or another review workflow.

It is built for two separate series:

- `Security`: IAEA Nuclear Security Series
- `Safety`: IAEA Safety Standards Series

The parser does not download PDFs. It reads PDFs you already have, repairs common PDF text extraction issues such as artificial line breaks, labels each record by source location and status, and writes combined output files.

## Quick Start

Install the project:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows, activate the environment with:

```bash
.venv\Scripts\activate
```

Run Security:

```bash
iaea-guidance-parser series inputs/Security \
  --series-config configs/nuclear_security_series.yaml \
  --out outputs/Security
```

Run Safety:

```bash
iaea-guidance-parser series inputs/Safety \
  --series-config configs/nuclear_safety_series.yaml \
  --out outputs/Safety
```

If your PDFs are somewhere else, replace `inputs/Security` or `inputs/Safety` with your folder path.

## What To Upload To A Custom GPT

Create one GPT for Security and one GPT for Safety. Keeping the two series separate usually gives clearer answers.

Upload the numbered Markdown parts only:

```text
outputs/Security/series_custom_gpt_knowledge_parts/part_*.md
outputs/Safety/series_custom_gpt_knowledge_parts/part_*.md
```

Do not upload the large unsplit `series_custom_gpt_knowledge.md` if the GPT builder says the file contains too much text. Use the numbered files in `series_custom_gpt_knowledge_parts/` instead.

The manifest and QA report are optional. Upload them only if you want the GPT to answer coverage, provenance or parser-quality questions. Do not treat them as substantive IAEA guidance.

## Suggested GPT Setup

Suggested Security name:

```text
IAEA Nuclear Security Series Assistant
```

Suggested Security description:

```text
Unofficial assistant for searching and summarizing uploaded IAEA Nuclear Security Series publications. Not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications.
```

Suggested Safety name:

```text
IAEA Safety Standards Assistant
```

Suggested Safety description:

```text
Unofficial assistant for searching and summarizing uploaded IAEA Safety Standards Series publications. Not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency or official IAEA publications.
```

Paste this into the Custom GPT `Instructions` field. Replace `[Safety/Security]` with the series you are building.

```text
You are an unofficial assistant for searching and explaining uploaded IAEA [Safety/Security] publications.

You are not affiliated with, endorsed by, or a substitute for the International Atomic Energy Agency. For authoritative wording or decisions, tell users to consult the official IAEA publication and applicable national requirements.

Use the uploaded knowledge files as your source for IAEA guidance. Do not invent publication titles, paragraph numbers, requirements, recommendations, guidance or citations.

Records include these fields:
- `doc`: source publication ID.
- `record`: parsed item, such as a paragraph, requirement, table, figure caption, heading, footnote or reference.
- `status`: parser status label.
- `region`: publication region.
- `pdf`: physical PDF page or page range.
- `section`: section path when available.
- `text`: extracted publication text.

Interpret status labels as:
- `Normative`: main technical content, usually Body Section 2 onward, plus integral appendix material.
- `Informative`: annexes and footnotes.
- `Informational`: front matter, Section 1 context, headings, references, glossary, metadata and back matter.

When answering:
- Start with the answer in plain language.
- Cite the relevant `doc`, `record`, `section` and `pdf` page when available.
- Distinguish requirements, recommendations, guidance, examples and background information when the records support that distinction.
- Preserve the source meaning; do not make advice sound more mandatory than the record supports.
- If the files do not answer the question, say so.
- If exact wording matters, quote only a short relevant excerpt and cite it.

Use manifest and QA files only for coverage, provenance and parser-quality questions. Do not treat them as substantive IAEA guidance.

Figure records usually contain captions and page locations, not the visual diagram. Table records may preserve raw extracted text rather than perfect row and column layout; mention uncertainty when layout matters.

Do not browse the web unless the user explicitly asks for information outside the uploaded files. If outside information is used, clearly separate it from the uploaded IAEA material.
```

## Main Outputs

After a series run, the combined files are written directly under `outputs/Security/` or `outputs/Safety/`.

| File | Use |
| --- | --- |
| `series_custom_gpt_knowledge_parts/part_*.md` | Upload these to a Custom GPT. |
| `series_custom_gpt_knowledge.md` | One large local reference file. Often too large to upload. |
| `series_structural_index.jsonl` | Full machine-readable record set. Useful for scripts and audits. |
| `series_manifest.csv` / `series_manifest.json` | List of PDFs, metadata, counts, checksums and failures. |
| `series_qa_report.md` | Human-readable parser QA summary. |
| `qa_report.json` | Structured QA findings. |

Each PDF also gets its own folder under `outputs/<Series>/documents/` with document-level versions of the same files.

## How Records Are Labelled

Each record has:

- `doc`: source publication ID, such as `NSS-12-T-REV1` or `GSR-PART-2`
- `record`: paragraph, requirement, table, figure, heading, footnote, reference or text block
- `status`: `Normative`, `Informative` or `Informational`
- `region`: `FrontMatter`, `Body`, `Appendix`, `Annex`, `References`, `Glossary` or `BackMatter`
- `pdf`: source PDF page number or page range
- `section`: section path when available
- `text`: extracted publication text

The status rules are intentionally conservative:

- `Normative`: main technical content, usually Body Section 2 onward, plus integral appendix material.
- `Informative`: annexes and footnotes.
- `Informational`: front matter, Section 1 context, headings, references, glossary, metadata and back matter.

## Quality Checks

The parser writes QA reports after each run. These reports flag issues such as:

- possible table layout problems;
- page headers or footers that leaked into text;
- suspicious encoding or OCR damage;
- requirement or footnote text that may have been merged into another record;
- document IDs that do not match the manifest.

For high-assurance work, always verify important answers against the official PDF.

## Run One PDF

For testing a single document:

```bash
iaea-guidance-parser parse path/to/document.pdf \
  --out outputs/example-document
```

Use a document override only when needed:

```bash
iaea-guidance-parser parse path/to/document.pdf \
  --config configs/nss17t.yaml \
  --out outputs/example-document
```

## Configuration

The normal series configs are:

```text
configs/nuclear_security_series.yaml
configs/nuclear_safety_series.yaml
```

They tell the parser whether a folder is Security or Safety. The parser normally infers individual document IDs, titles and categories from the PDFs.

Use per-document overrides only when the PDF metadata cannot be inferred reliably.

## Development

Run tests with:

```bash
pytest
```

If `pytest` is not on your path:

```bash
python -m pytest
```

## Known Limits

- Tables are preserved as raw text. Complex row and column layout may still need PDF review.
- Figures are not extracted as images. The parser captures figure captions and page locations.
- Old PDFs with unusual fonts may contain OCR or encoding damage.
- This parser is not an official IAEA tool and does not replace the official publications.
