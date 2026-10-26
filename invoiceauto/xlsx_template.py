"""Helpers for filling the Excel templates in the Base folder."""
import copy
import re

from openpyxl.utils import get_column_letter
from openpyxl.worksheet.cell_range import CellRange

_REF = re.compile(r"(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(])")


def _shift_formula(formula: str, after_row: int, n: int) -> str:
    """Moves row references below after_row down by n, like Excel does when rows are inserted."""
    def repl(m):
        row = int(m.group(4))
        if row > after_row:
            row += n
        return f'{m.group(1)}{m.group(2)}{m.group(3)}{row}'
    # leave quoted strings alone
    parts = formula.split('"')
    for i in range(0, len(parts), 2):
        parts[i] = _REF.sub(repl, parts[i])
    return '"'.join(parts)


def insert_rows(ws, after_row: int, n: int, style_row: int = None):
    """Inserts n rows after after_row, copying the style of style_row (default after_row).

    Unlike openpyxl's insert_rows this also moves merged ranges and row heights and
    fixes formula references, so the footer and its SUM formulas stay intact.
    """
    if n <= 0:
        return
    style_row = style_row or after_row
    max_row, max_col = ws.max_row, ws.max_column

    # formulas anywhere in the sheet that point below the insertion point
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith('='):
                c.value = _shift_formula(c.value, after_row, n)

    merged = [CellRange(str(r)) for r in ws.merged_cells.ranges]
    for r in merged:
        ws.unmerge_cells(str(r))

    for r in range(max_row, after_row, -1):
        for col in range(1, max_col + 1):
            src = ws.cell(r, col)
            dst = ws.cell(r + n, col)
            dst.value = src.value
            dst._style = copy.copy(src._style)
            if src.hyperlink:
                dst.hyperlink = copy.copy(src.hyperlink)
        h = ws.row_dimensions[r].height
        ws.row_dimensions[r + n].height = h

    for r in range(after_row + 1, after_row + n + 1):
        for col in range(1, max_col + 1):
            dst = ws.cell(r, col)
            dst.value = None
            dst.hyperlink = None
            dst._style = copy.copy(ws.cell(style_row, col)._style)
        ws.row_dimensions[r].height = ws.row_dimensions[style_row].height

    for r in merged:
        if r.min_row > after_row:
            r.shift(row_shift=n)
        ws.merge_cells(r.coord)


def replace_placeholders(ws, values: dict):
    """Replaces cells whose whole text is a #placeholder. Unknown or empty values clear the cell."""
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith('#'):
                key = c.value.strip()
                if key in values:
                    v = values[key]
                    c.value = v if v not in ('', None) else None


def col(letter: str) -> int:
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letter)


def letter(idx: int) -> str:
    return get_column_letter(idx)


def find_cell(ws, value: str):
    """(row, column) of the first cell whose text is value."""
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.strip() == value.strip():
                return c.row, c.column
    raise ValueError(f'{value!r} not found in the template')


def find_row(ws, column: int, value: str) -> int:
    for r in range(1, ws.max_row + 1):
        if ws.cell(r, column).value == value:
            return r
    raise ValueError(f'{value!r} not found in column {column} of the template')
