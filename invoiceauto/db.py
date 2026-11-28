"""euromall.db access: account book, stock and the work table of the last run.

The table layout is the old program's and is not changed, so both programs can use the same file.
"""
import os
import shutil
import sqlite3
from contextlib import closing
from typing import List, Optional

from .engine import Run, MODE_PROFORMA
from .shipping import C_MOBILE, C_ADDR, C_ADDR_DETAIL, C_CUSTOMS_NO
from .util import text

STATUS_UNPAID, STATUS_PAID, STATUS_CANCELLED = '0', '1', '2'
STATUS_LABELS = {STATUS_UNPAID: '미결제', STATUS_PAID: '결제', STATUS_CANCELLED: '취소'}
AUTO_REMARK = 'auto:'      # remark prefix of stock rows written by a run, so a re-run replaces them


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def backup(path, folder=None) -> str:
    """Copies the database next to itself (db/backup/<name>_YYYYmmdd_HHMMSS.db) before a run writes to it."""
    import datetime as dt
    folder = folder or os.path.join(os.path.dirname(path), 'backup')
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    dst = os.path.join(folder, f'{stem}_{dt.datetime.now():%Y%m%d_%H%M%S}.db')
    shutil.copy2(path, dst)
    return dst


def record_run(path, run: Run):
    """Writes the run to the account book and the stock table in one transaction."""
    s = run.settings
    ymd = s.work_date.isoformat()
    amount_col = 'profomal_amount' if s.mode == MODE_PROFORMA else 'release_amount'
    entries = [(inv.company.uid, inv.invoice_no, inv.total) for inv in run.invoices]
    entries.append(('EMG', run.export_invoice_no, round(sum(i.total for i in run.invoices), 2)))

    with closing(connect(path)) as con, con:
        for uid, invoice_no, total in entries:
            cur = con.execute(
                f'UPDATE tb_account_book SET {amount_col} = ? WHERE ymd = ? AND company_uid = ? AND invoice_no = ?',
                (total, ymd, uid, invoice_no))
            if cur.rowcount == 0:
                con.execute(
                    'INSERT INTO tb_account_book (ymd, company_uid, invoice_no, release_amount, deposit_amount,'
                    ' deposit_ymd, status_cd, remark, profomal_amount) VALUES (?, ?, ?, ?, 0, NULL, ?, NULL, ?)',
                    (ymd, uid, invoice_no, 0 if s.mode == MODE_PROFORMA else total, STATUS_UNPAID,
                     total if s.mode == MODE_PROFORMA else 0))

        # stock: one outgoing row per company and item, replacing what an earlier run of the same day wrote
        tag = AUTO_REMARK + run.export_invoice_no
        con.execute("DELETE FROM tb_stock WHERE ymd = ? AND type = 'O' AND remark = ?", (ymd, tag))
        totals = {}
        for inv in run.invoices:
            for l in inv.lines:
                key = (inv.company.uid, l.line.display_name(l.item))
                row = totals.setdefault(key, [inv.company.name, 0, l.price])
                row[1] += l.qty
        for (uid, item_name), (cname, qty, price) in sorted(totals.items()):
            con.execute(
                'INSERT INTO tb_stock (ymd, type, company_uid, company_name, item_name, quantity, item_price,'
                ' amount, remark) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (ymd, 'O', uid, cname, item_name, qty, price, round(price * qty, 2), tag))

        # work table of the last run (the old program kept it too)
        con.execute('DELETE FROM tb_invoice')
        for sh in run.shipments:
            for i, l in enumerate(sh.lines):
                con.execute(
                    'INSERT INTO tb_invoice (company_uid, item_name, item_seq, quantity, price, amount, order_no,'
                    ' invoice_no, recv_name, gross_weight, total_box, delivery_fee_amount, packing_fee_amount,'
                    ' en_name, item_code, barcode, url, singo_unit_price, export_hs_code, flug_nr, mawb_nr,'
                    ' mobile, address1, address2, customs_clearance_number) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (sh.company.uid, l.line.display_name(l.item), i + 1, l.qty, l.price, l.goods_amount,
                     sh.order_no, sh.hawb, l.line.recv_name, l.weight, sh.boxes if i == 0 else 0,
                     l.delivery_fee, l.packing_fee, sh.recv_name_en,
                     l.item.item_code if l.item else '', l.item.barcode if l.item else '',
                     l.item.url if l.item else '', l.declared_price,
                     l.item.export_hs_code if l.item else '',
                     l.item.barcode if l.item else '',     # the old program kept the barcode and URL here too
                     l.item.url if l.item else '',
                     text(l.line.get(C_MOBILE)), text(l.line.get(C_ADDR)), text(l.line.get(C_ADDR_DETAIL)),
                     text(l.line.get(C_CUSTOMS_NO))))


# --- screens -------------------------------------------------------------------------------------

def companies(path) -> List[str]:
    with closing(connect(path)) as con:
        return [r[0] for r in con.execute('SELECT company_uid FROM tb_company ORDER BY company_uid')]


def account_rows(path, date_from, date_to, company: Optional[str] = None):
    q = ('SELECT seq, ymd, company_uid, invoice_no, status_cd, profomal_amount, release_amount, deposit_amount,'
         ' deposit_ymd, remark FROM tb_account_book WHERE ymd BETWEEN ? AND ?')
    args = [date_from, date_to]
    if company:
        q += ' AND company_uid = ?'
        args.append(company)
    with closing(connect(path)) as con:
        return [dict(r) for r in con.execute(q + ' ORDER BY ymd, company_uid, invoice_no', args)]


def save_account_row(path, row: dict):
    cols = ['ymd', 'company_uid', 'invoice_no', 'status_cd', 'profomal_amount', 'release_amount',
            'deposit_amount', 'deposit_ymd', 'remark']
    with closing(connect(path)) as con, con:
        if row.get('seq'):
            con.execute(f'UPDATE tb_account_book SET {", ".join(c + " = ?" for c in cols)} WHERE seq = ?',
                        [row.get(c) for c in cols] + [row['seq']])
        else:
            con.execute(f'INSERT INTO tb_account_book ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                        [row.get(c) for c in cols])


def delete_account_row(path, seq):
    with closing(connect(path)) as con, con:
        con.execute('DELETE FROM tb_account_book WHERE seq = ?', (seq,))


def stock_rows(path, date_from, date_to, company: Optional[str] = None):
    q = ('SELECT seq, ymd, type, company_uid, company_name, item_name, quantity, item_price, amount, remark'
         ' FROM tb_stock WHERE ymd BETWEEN ? AND ?')
    args = [date_from, date_to]
    if company:
        q += ' AND company_uid = ?'
        args.append(company)
    with closing(connect(path)) as con:
        return [dict(r) for r in con.execute(q + ' ORDER BY ymd, company_uid, item_name', args)]


def save_stock_row(path, row: dict):
    cols = ['ymd', 'type', 'company_uid', 'company_name', 'item_name', 'quantity', 'item_price', 'amount', 'remark']
    with closing(connect(path)) as con, con:
        if row.get('seq'):
            con.execute(f'UPDATE tb_stock SET {", ".join(c + " = ?" for c in cols)} WHERE seq = ?',
                        [row.get(c) for c in cols] + [row['seq']])
        else:
            con.execute(f'INSERT INTO tb_stock ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                        [row.get(c) for c in cols])


def delete_stock_row(path, seq):
    with closing(connect(path)) as con, con:
        con.execute('DELETE FROM tb_stock WHERE seq = ?', (seq,))


def company_summary(rows):
    """Per company: 견적, 청구, 입금 and 받을 돈 (unpaid 청구 − 입금), from account_rows. EMG (the export total) is left out."""
    out = {}
    for r in rows:
        uid = r.get('company_uid') or ''
        if uid == 'EMG':
            continue
        s = out.setdefault(uid, {'company_uid': uid, 'count': 0, 'proforma': 0.0, 'release': 0.0, 'deposit': 0.0,
                                 'open': 0.0, 'unpaid_count': 0, 'oldest_unpaid': None})
        rel, dep = float(r.get('release_amount') or 0), float(r.get('deposit_amount') or 0)
        s['count'] += 1
        s['proforma'] += float(r.get('profomal_amount') or 0)
        s['release'] += rel
        s['deposit'] += dep
        if str(r.get('status_cd')) == STATUS_UNPAID and rel:
            s['open'] += rel - dep
            s['unpaid_count'] += 1
            ymd = r.get('ymd')
            if ymd and (s['oldest_unpaid'] is None or ymd < s['oldest_unpaid']):
                s['oldest_unpaid'] = ymd
    for s in out.values():
        for k in ('proforma', 'release', 'deposit', 'open'):
            s[k] = round(s[k], 2)
    return sorted(out.values(), key=lambda s: (-s['open'], s['company_uid'].upper()))
