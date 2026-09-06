"""Create a small synthetic PDF for parser demonstrations.

Usage:
    python examples/create_demo.py --out tmp/demo.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

import fitz

WATERMARK = "Synthetic demonstration — not IAEA guidance"
PAGE_WIDTH = 595
PAGE_HEIGHT = 842


def register_builtin_fonts(page: fitz.Page) -> None:
    page.insert_font(fontname="demo-regular", fontbuffer=fitz.Font("helv").buffer)
    page.insert_font(fontname="demo-bold", fontbuffer=fitz.Font("hebo").buffer)


def add_text(
    page: fitz.Page, point: tuple[float, float], text: str, *, size: float, bold: bool = False
) -> None:
    page.insert_text(
        point,
        text,
        fontsize=size,
        fontname="demo-bold" if bold else "demo-regular",
        color=(0.08, 0.08, 0.08),
    )


def add_footer(page: fitz.Page, page_number: int) -> None:
    add_text(page, (PAGE_WIDTH / 2 - 4, PAGE_HEIGHT - 34), str(page_number), size=10)


def draw_checklist(page: fitz.Page) -> None:
    left, top, right, bottom = 48, 132, 547, 300
    middle = 300
    row_edges = (top, 188, 244, bottom)
    table_rect = fitz.Rect(left, top, right, bottom)
    page.draw_rect(table_rect, color=(0.15, 0.15, 0.15), width=1)
    page.draw_line((middle, top), (middle, bottom), color=(0.15, 0.15, 0.15), width=1)
    for edge in row_edges[1:-1]:
        page.draw_line((left, edge), (right, edge), color=(0.15, 0.15, 0.15), width=1)

    cells = (
        ("Item", "Example"),
        ("2.1. Label inside a table", "Preserve the cell relationship"),
        ("Source page", "2"),
    )
    for row_index, (item, example) in enumerate(cells):
        y0, y1 = row_edges[row_index], row_edges[row_index + 1]
        page.insert_textbox(
            fitz.Rect(left + 8, y0 + 12, middle - 8, y1 - 8),
            item,
            fontsize=10,
            fontname="demo-bold" if row_index == 0 else "demo-regular",
            color=(0.08, 0.08, 0.08),
        )
        page.insert_textbox(
            fitz.Rect(middle + 8, y0 + 12, right - 8, y1 - 8),
            example,
            fontsize=10,
            fontname="demo-bold" if row_index == 0 else "demo-regular",
            color=(0.08, 0.08, 0.08),
        )


def create_demo(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    document.set_metadata(
        {
            "title": "IAEA Guidance Parser Demonstration",
            "subject": WATERMARK,
            "author": "IAEA Guidance Parser demonstration",
        }
    )

    page_one = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    register_builtin_fonts(page_one)
    add_text(page_one, (48, 42), WATERMARK, size=11, bold=True)
    add_text(page_one, (48, 82), "IAEA Guidance Parser Demonstration", size=18, bold=True)
    add_text(page_one, (48, 142), "1. INTRODUCTION", size=14, bold=True)
    add_text(
        page_one,
        (48, 174),
        "1.1. This synthetic document demonstrates source tracing.",
        size=11,
    )
    add_text(page_one, (48, 242), "2. WORKED EXAMPLE", size=14, bold=True)
    add_text(
        page_one,
        (48, 274),
        "2.1. A record keeps its source label and physical PDF page.",
        size=11,
    )
    add_footer(page_one, 1)

    page_two = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    register_builtin_fonts(page_two)
    add_text(page_two, (48, 42), WATERMARK, size=11, bold=True)
    add_text(page_two, (48, 102), "TABLE 1. DEMONSTRATION CHECKLIST", size=14, bold=True)
    draw_checklist(page_two)
    add_footer(page_two, 2)

    document.save(output)
    document.close()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a synthetic PDF for parser demonstrations."
    )
    parser.add_argument("--out", type=Path, default=Path("tmp/demo.pdf"))
    args = parser.parse_args()
    print(create_demo(args.out))


if __name__ == "__main__":
    main()
