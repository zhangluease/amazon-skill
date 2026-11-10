#!/usr/bin/env python3
"""Export a JSON keyword table to a filterable Excel workbook."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unicodedata import east_asian_width


def write_xlsx(output, headers, rows, percentage_columns=()):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if output.suffix.lower() != ".xlsx":
        raise ValueError("Excel output must have an .xlsx extension.")
    if not headers or any(not isinstance(header, str) or not header.strip() for header in headers):
        raise ValueError("Provide a nonempty text header for every column.")
    if set(percentage_columns) - set(headers):
        raise ValueError("Percentage columns must match existing headers.")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "关键词"
    sheet.freeze_panes = "A2"
    sheet.sheet_view.showGridLines = False
    widths = [12] * len(headers)
    for row_number, row in enumerate([headers, *rows], start=1):
        if len(row) != len(headers):
            raise ValueError(f"Row {row_number} does not match the number of headers.")
        for index, value in enumerate(row):
            cell = sheet.cell(row_number, index + 1, value)
            if isinstance(value, str):
                cell.data_type = "s"
                width = sum(2 if east_asian_width(char) in {"F", "W"} else 1 for char in value)
                widths[index] = max(widths[index], min(width + 4, 60))
                if width > 56:
                    cell.alignment = Alignment(wrap_text=True)
            if row_number > 1 and headers[index] in percentage_columns and type(value) in (int, float):
                cell.number_format = "0.00%"

    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="24364B")
    sheet.auto_filter.ref = sheet.dimensions

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".keywords-", dir=output.parent) as directory:
        temporary = Path(directory) / "keywords.xlsx"
        workbook.save(temporary)
        temporary.replace(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="JSON with headers, rows and optional percentage_columns")
    parser.add_argument("--output", required=True, type=Path, help="Path to the .xlsx workbook")
    args = parser.parse_args()
    table = json.loads(args.input.read_text(encoding="utf-8-sig"))
    try:
        write_xlsx(args.output, table["headers"], table["rows"], table.get("percentage_columns", ()))
    except ModuleNotFoundError as error:
        if error.name != "openpyxl":
            raise
        parser.exit(1, "Excel export requires openpyxl. Install it with python3 -m pip install openpyxl.\n")


if __name__ == "__main__":
    main()
