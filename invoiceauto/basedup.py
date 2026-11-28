"""Find and remove duplicate item names (column I) in the newPrice sheet of Base.xlsx.

The program already uses the lowest (newest) row of a name, so removing the upper copies does not change any
output when the lowest row is kept. The original file is never written: the cleaned workbook goes to a new file.
"""
import datetime as dt
import os
from dataclasses import dataclass, field

import openpyxl

from .util import text

SHEET = 'newPrice'
NAME_COL = 9            # I
FIRST, LAST = 4, 19     # D..S: every column the program reads
SHOW = [(4, '무게'), (5, '단가'), (6, '상품코드'), (8, '브랜드'), (11, '판매가'), (14, '신고단가'), (15, 'HS'), (19, '원산지')]


def _norm(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return text(v).strip()


@dataclass
class Group:
    name: str
    rows: list                       # newPrice row numbers, top to bottom
    values: dict = field(default_factory=dict)   # row -> {column: value} for D..S

    @property
    def identical(self) -> bool:
        keys = {tuple(_norm(self.values[r].get(c)) for c in range(FIRST, LAST + 1)) for r in self.rows}
        return len(keys) == 1

    @property
    def newest(self) -> int:
        return self.rows[-1]


def find_groups(path):
    """Every item name that appears on more than one newPrice row."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        by_name = {}
        for row_no, r in enumerate(wb[SHEET].iter_rows(min_row=2, max_col=LAST, values_only=True), start=2):
            r = list(r) + [None] * LAST
            name = text(r[NAME_COL - 1]).strip()
            if not name:
                continue
            g = by_name.setdefault(name, Group(name, []))
            g.rows.append(row_no)
            g.values[row_no] = {c: r[c - 1] for c in range(FIRST, LAST + 1)}
    finally:
        wb.close()
    return [g for g in by_name.values() if len(g.rows) > 1]


def rows_to_drop(groups, keep=None):
    """Rows to remove: every row of a group except the kept one (the newest unless keep[name] says otherwise)."""
    keep = keep or {}
    drop = set()
    for g in groups:
        k = keep.get(g.name, g.newest)
        drop.update(r for r in g.rows if r != k)
    return drop


def write_clean(src, dst, drop):
    """Save a copy of src without the given newPrice rows. Rows below move up with their formatting and
    formulas (relative references follow the row). Returns the number of rows removed."""
    wb = openpyxl.load_workbook(src)
    ws = wb[SHEET]
    last = ws.max_row
    drop = sorted(r for r in drop if 2 <= r <= last)
    if not drop:
        wb.save(dst)
        return 0
    # move each block of kept rows up by the number of dropped rows above it, top to bottom
    heights = {r: ws.row_dimensions[r].height for r in range(2, last + 1) if ws.row_dimensions[r].height}
    bounds = drop + [last + 1]
    for i in range(len(drop)):
        start, end = bounds[i] + 1, bounds[i + 1] - 1
        if start <= end:
            shift = i + 1
            ws.move_range(f'A{start}:{openpyxl.utils.get_column_letter(ws.max_column)}{end}',
                          rows=-shift, translate=True)
            for r in range(start, end + 1):
                ws.row_dimensions[r - shift].height = heights.get(r)
    new_last = last - len(drop)
    ws.delete_rows(new_last + 1, len(drop))
    for t in ws.tables.values():
        ref = openpyxl.worksheet.cell_range.CellRange(t.ref)
        if ref.max_row >= new_last:
            ref.max_row = max(ref.min_row + 1, ref.max_row - len(drop))
            t.ref = ref.coord
            if t.autoFilter is not None:
                t.autoFilter.ref = ref.coord
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if cell.hyperlink is not None:
                cell.hyperlink.ref = cell.coordinate     # links keep the address they were read at
    if ws.auto_filter.ref:
        ref = openpyxl.worksheet.cell_range.CellRange(ws.auto_filter.ref)
        ref.max_row = max(ref.min_row, min(ref.max_row, new_last))
        ws.auto_filter.ref = ref.coord
    wb.save(dst)
    return len(drop)


def clean_name(base_path, today=None):
    """Base\\Base_정리본_YYYYMMDD.xlsx next to the original (with _2, _3 ... if it exists)."""
    today = today or dt.date.today()
    folder = os.path.dirname(base_path)
    stem = f'Base_정리본_{today:%Y%m%d}'
    path, n = os.path.join(folder, stem + '.xlsx'), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f'{stem}_{n}.xlsx'), n + 1
    return path
