from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .exporters import write_outputs, write_series_outputs
from .metadata import load_config
from .parser import IAEAGuidanceParser
from .series import FailedDocumentResult, discover_pdfs, parse_one_document

app = typer.Typer(help="Precompute structural indexes and Custom GPT input files for IAEA guidance PDFs.")
console = Console()


@app.command()
def parse(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, help="Path to source PDF."),
    out: Path = typer.Option(Path("outputs"), "--out", "-o", help="Output directory."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", exists=True, dir_okay=False, help="YAML metadata/rules config."),
) -> None:
    """Parse a single IAEA guidance PDF."""
    parser = IAEAGuidanceParser.from_pdf(pdf, config)
    metadata, records = parser.parse()
    write_outputs(out, metadata, records)
    console.print(f"[green]Wrote {len(records)} structural records to {out}[/green]")
    console.print(f"Document type: [bold]{metadata.document_type}[/bold] ({metadata.document_category})")
    console.print("Key outputs: structural_index.jsonl, custom_gpt_knowledge.jsonl, custom_gpt_knowledge.md, qa_report.md")


@app.command("batch")
def batch_parse(
    pdf_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="Directory containing PDFs."),
    out: Path = typer.Option(Path("outputs_batch"), "--out", "-o", help="Batch output directory."),
    config_dir: Optional[Path] = typer.Option(None, "--config-dir", exists=True, file_okay=False, help="Optional directory of YAML configs keyed by PDF stem."),
) -> None:
    """Parse all PDFs in a directory."""
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise typer.BadParameter(f"No PDFs found in {pdf_dir}")
    for pdf in pdfs:
        cfg = None
        if config_dir:
            possible = config_dir / f"{pdf.stem}.yaml"
            cfg = possible if possible.exists() else None
        doc_out = out / pdf.stem
        parser = IAEAGuidanceParser.from_pdf(pdf, cfg)
        metadata, records = parser.parse()
        write_outputs(doc_out, metadata, records)
        console.print(f"[green]{pdf.name}[/green]: {len(records)} records -> {doc_out}")


@app.command("series")
def parse_series(
    pdf_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="Directory containing PDFs from one publication series."),
    out: Path = typer.Option(Path("outputs_series"), "--out", "-o", help="Series output directory."),
    series_config: Optional[Path] = typer.Option(
        None,
        "--series-config",
        "-s",
        exists=True,
        dir_okay=False,
        help="YAML config with series-level metadata defaults and optional per-document overrides.",
    ),
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        exists=True,
        file_okay=False,
        help="Optional directory of per-document YAML configs keyed by PDF stem or filename.",
    ),
    pattern: str = typer.Option("*.pdf", "--pattern", help="Glob pattern for PDFs, e.g. '*.pdf' or 'PUB*.pdf'."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Search subdirectories recursively."),
    fail_fast: bool = typer.Option(False, "--fail-fast/--continue-on-error", help="Stop on first failed PDF."),
    limit: Optional[int] = typer.Option(None, "--limit", min=1, help="Optional maximum number of PDFs to parse, useful for test runs."),
) -> None:
    """Parse a directory of PDFs as one series and write combined GPT-ready outputs."""
    cfg = load_config(series_config)
    pdfs = discover_pdfs(pdf_dir, pattern=pattern, recursive=recursive)
    if limit:
        pdfs = pdfs[:limit]
    if not pdfs:
        raise typer.BadParameter(f"No PDFs found in {pdf_dir} matching pattern {pattern!r}")

    out.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []

    console.print(f"[bold]Parsing {len(pdfs)} PDF(s) from series directory:[/bold] {pdf_dir}")
    for i, pdf in enumerate(pdfs, start=1):
        try:
            console.print(f"[{i}/{len(pdfs)}] {pdf.name}")
            result = parse_one_document(
                pdf_path=pdf,
                pdf_root=pdf_dir,
                out_root=out,
                series_config=cfg,
                config_dir=config_dir,
            )
            write_outputs(result.output_dir, result.metadata, result.records)
            results.append(result)
            console.print(
                f"  [green]ok[/green] {result.metadata.document_id}: {len(result.records)} records; "
                f"type={result.metadata.document_type or '<missing>'}"
            )
        except Exception as exc:  # pragma: no cover - defensive CLI handling
            failure = FailedDocumentResult(source_pdf=pdf, error=f"{type(exc).__name__}: {exc}")
            failures.append(failure)
            console.print(f"  [red]failed[/red] {pdf.name}: {failure.error}")
            if fail_fast:
                raise

    write_series_outputs(out, series_config=cfg, results=results, failures=failures)

    table = Table(title="Series parse summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("PDFs discovered", str(len(pdfs)))
    table.add_row("Documents parsed", str(len(results)))
    table.add_row("Documents failed", str(len(failures)))
    table.add_row("Structural records", str(sum(len(r.records) for r in results)))
    console.print(table)
    console.print(f"[green]Combined outputs written to {out}[/green]")
    console.print("Key combined outputs: series_structural_index.jsonl, series_custom_gpt_knowledge.jsonl, series_custom_gpt_knowledge.md, series_manifest.csv, series_qa_report.md")
