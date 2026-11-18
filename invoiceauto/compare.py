"""Compares two result folders (old program vs new program) cell by cell, and two account books."""
import datetime as dt
import os
import sqlite3
from contextlib import closing
from typing import List, NamedTuple

import openpyxl
from openpyxl.styles import Font, PatternFill


class Diff(NamedTuple):
    file: str
    status: str        # 같음 / 다름 / 기존만 / 새것만
    cell: str = ''
    old: object = None
    new: object = None


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


def compare_files(old_path, new_path):
    a = openpyxl.load_workbook(old_path).active
    b = openpyxl.load_workbook(new_path).active
    diffs = []
    for r in range(1, max(a.max_row, b.max_row) + 1):
        for c in range(1, max(a.max_column, b.max_column) + 1):
            x, y = a.cell(r, c).value, b.cell(r, c).value
            if norm(x) != norm(y):
                diffs.append((a.cell(r, c).coordinate, x, y))
    return diffs


REPORT_PREFIX = '비교결과_'


def _workbooks(folder):
    return {n for n in os.listdir(folder)
            if n.endswith('.xlsx') and not n.startswith('~$') and not n.startswith(REPORT_PREFIX)}


def compare_folders(old_dir, new_dir) -> List[Diff]:
    old, new = _workbooks(old_dir), _workbooks(new_dir)
    out = []
    for name in sorted(old | new):
        if name not in new:
            out.append(Diff(name, '기존만'))
        elif name not in old:
            out.append(Diff(name, '새것만'))
        else:
            diffs = compare_files(os.path.join(old_dir, name), os.path.join(new_dir, name))
            if not diffs:
                out.append(Diff(name, '같음'))
            out.extend(Diff(name, '다름', *d) for d in diffs)
    return out


def account_rows(path, ymd):
    with closing(sqlite3.connect(path)) as con:
        rows = con.execute('SELECT company_uid, invoice_no, profomal_amount, release_amount FROM tb_account_book'
                           ' WHERE ymd = ?', (ymd,)).fetchall()
    return {(r[0], r[1]): (r[2] or 0, r[3] or 0) for r in rows}


def compare_accounts(old_db, new_db, ymd) -> List[Diff]:
    """Account book rows of one day: 견적금액/청구금액 per invoice number."""
    a, b = account_rows(old_db, ymd), account_rows(new_db, ymd)
    out = []
    for key in sorted(set(a) | set(b)):
        label = f'회계 {ymd} {key[1]}'
        if key not in b:
            out.append(Diff(label, '기존만', '', a[key]))
        elif key not in a:
            out.append(Diff(label, '새것만', '', None, b[key]))
        elif any(abs(x - y) > 0.005 for x, y in zip(a[key], b[key])):
            out.append(Diff(label, '다름', '견적/청구', a[key], b[key]))
        else:
            out.append(Diff(label, '같음'))
    return out


def summary(diffs: List[Diff]) -> List[str]:
    files = {}
    for d in diffs:
        s = files.setdefault(d.file, [d.status, 0])
        if d.status == '다름':
            s[1] += 1
    lines = []
    for name, (status, n) in files.items():
        lines.append(f'  {status}{f" {n}칸" if n else ""}: {name}')
    return lines


# differences found when the new program was checked against the old one's results (2026-10-26, 2026-08-28, 2025-05-20);
# they are fixes of the old program, not errors of the new one
KNOWN_DIFFERENCES = [
    ('수령인 영문 이름 (인보이스 G열)', '기존 프로그램은 DB 이름표를 써서 다른 사람 이름이 들어간 경우가 있었습니다. '
                                    '새 프로그램은 한글 이름을 직접 로마자로 바꿉니다.'),
    ('16자리 주문번호 끝자리', '기존 프로그램은 숫자로 저장해 마지막 자리가 0으로 바뀌었습니다. 새 프로그램은 원래 번호 그대로입니다.'),
    ('합계 수식 범위 (=SUM)', '기존 프로그램은 빈 줄까지 범위에 넣었습니다. 새 프로그램은 상품 줄만 넣습니다. 금액은 같습니다.'),
    ('수입식품신고', '기존 프로그램은 같은 상품 줄이 여러 번 들어간 경우가 있었습니다(DB 상품표에 같은 이름이 여러 개일 때). '
                  '새 프로그램은 상품 줄마다 한 번씩 넣습니다. 넣는 기준(식품종류가 있는 상품)은 같습니다.'),
    ('적하목록 무게병합', '기존 프로그램은 상세주소·배송메모·운송장번호·식품 정보 일부가 다른 주문의 줄 값으로 섞여 있었습니다. '
                      '새 프로그램은 그 주문의 값만 씁니다.'),
    ('포장리스트', '기존 프로그램의 "(월)" 표시와 줄마다 들어간 수식을 값으로 정리했습니다.'),
    ('품목코드·바코드·URL', '기존 프로그램은 입력 엑셀의 값(이전 상품 URL이 남거나 16자리 코드 끝자리가 0으로 바뀐 값 포함)을 썼습니다. '
                         '새 프로그램은 Base.xlsx 값을 씁니다. Base.xlsx에 "Euromall"로 적힌 칸은 그대로 나옵니다.'),
    ('Base.xlsx newPrice에 같은 상품명이 여러 줄', '기존 프로그램은 위쪽 줄 값을, 새 프로그램은 맨 아래(최신) 줄 값을 씁니다. '
                                          '어느 줄을 썼는지는 작업 기록에 "여러 번 있어 맨 아래 N행(최신) 값을 썼습니다"로 나옵니다.'),
    ('Base.xlsx가 바뀐 경우', '예전 결과와 비교할 때 배송비·단가·식품종류처럼 Base.xlsx에서 오는 값은 그 뒤 Base.xlsx가 바뀌었으면 다르게 나옵니다.'),
    ('적하목록 BQ열', '새 프로그램은 주문 순번을 첫 줄에도 적습니다.'),
    ('송장업로드 (청구 모드)', '기존 프로그램은 일부 주문만 넣었습니다. 새 프로그램은 송장번호가 있는 주문을 모두 넣습니다.'),
    ('작업기록 파일', '새 프로그램은 화면에 나온 경고를 결과 폴더의 작업기록_날짜.txt에도 남깁니다.'),
]


def write_report(path, diffs: List[Diff]):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '비교'
    ws.append(['파일', '결과', '셀', '기존 프로그램', '새 프로그램'])
    for c in ws[1]:
        c.font = Font(bold=True)
    red = PatternFill('solid', fgColor='FCE4E4')
    for d in diffs:
        ws.append([d.file, d.status, d.cell, '' if d.old is None else str(d.old), '' if d.new is None else str(d.new)])
        if d.status != '같음':
            for c in ws[ws.max_row]:
                c.fill = red
    for col, w in zip('ABCDE', (55, 10, 10, 45, 45)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'

    known = wb.create_sheet('알려진 차이')
    known.append(['항목', '설명'])
    for c in known[1]:
        c.font = Font(bold=True)
    for row in KNOWN_DIFFERENCES:
        known.append(list(row))
    known.column_dimensions['A'].width = 32
    known.column_dimensions['B'].width = 110
    wb.save(path)
