"""Compares generated workbooks with the old program's results, cell by cell.

usage: python -m tests.compare <expected_dir> <generated_dir> [max_diffs_per_file]
"""
import sys

from invoiceauto.compare import compare_folders


def main(exp_dir, gen_dir, limit=15):
    shown = {}
    for d in compare_folders(exp_dir, gen_dir):
        if d.status != '다름':
            print(f'{d.status:6} {d.file}')
            continue
        n = shown.get(d.file, 0)
        if n == 0:
            print(f'다름   {d.file}')
        if n < limit:
            print(f'           {d.cell:>6}: old={str(d.old)[:45]!r} new={str(d.new)[:45]!r}')
        shown[d.file] = n + 1


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 15)
