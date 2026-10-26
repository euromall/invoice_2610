"""Compares generated workbooks with the old program's results, cell by cell.

usage: python -m tests.compare <expected_dir> <generated_dir> [max_diffs_per_file]
"""
import datetime as dt
import os
import sys

import openpyxl


def norm(v):
    if v is None:
        return ''
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    try:
        f = float(s)
        if 'e' not in s.lower() and len(s) < 15:
            return repr(round(f, 4))
    except ValueError:
        pass
    return s


def compare_files(exp_path, gen_path):
    a = openpyxl.load_workbook(exp_path).active
    b = openpyxl.load_workbook(gen_path).active
    diffs = []
    rows = max(a.max_row, b.max_row)
    cols = max(a.max_column, b.max_column)
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            x, y = a.cell(r, c).value, b.cell(r, c).value
            if norm(x) != norm(y):
                diffs.append((a.cell(r, c).coordinate, x, y))
    return diffs


def main(exp_dir, gen_dir, limit=15):
    total = 0
    for name in sorted(os.listdir(exp_dir)):
        if not name.endswith('.xlsx'):
            continue
        gen = os.path.join(gen_dir, name)
        if not os.path.exists(gen):
            print(f'MISSING  {name}')
            total += 1
            continue
        diffs = compare_files(os.path.join(exp_dir, name), gen)
        total += len(diffs)
        print(f'{"OK      " if not diffs else "DIFF %3d" % len(diffs)} {name}')
        for coord, x, y in diffs[:limit]:
            sx, sy = str(x)[:45], str(y)[:45]
            print(f'           {coord:>6}: old={sx!r} new={sy!r}')
    extra = set(os.listdir(gen_dir)) - set(os.listdir(exp_dir))
    for name in sorted(extra):
        print(f'EXTRA    {name}')
    return total


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 15)
