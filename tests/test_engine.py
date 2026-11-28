"""Automatic checks on made-up data: run with  python -m pytest tests"""
import datetime as dt
import json
import os

import pytest

from invoiceauto import app
from invoiceauto.engine import build_run, Settings, CARRIER_CJ
from invoiceauto.outputs import lists
from invoiceauto.refdata import RefData
from invoiceauto.shipping import read_shipping, C_WEIGHT
from tests import synthetic


@pytest.fixture
def run(tmp_path):
    ref = RefData.load(synthetic.make_base(str(tmp_path / 'Base')))
    lines = read_shipping(synthetic.make_shipping(str(tmp_path / 'in.xlsx')))
    return build_run(lines, ref, Settings(work_date=dt.date(2026, 11, 24), carrier=CARRIER_CJ))


def test_shipments_and_tracking_numbers(run):
    assert len(run.shipments) == 3
    # sorted by first item name: Alpha (ORD-2, qty 3), Beta (ORD-1), Gamma (ORD-3)
    assert [s.order_no for s in run.shipments] == ['ORD-2', 'ORD-1', 'ORD-3']
    hawbs = [s.hawb for s in run.shipments]
    assert hawbs[0] == '600000000013'
    assert len(set(hawbs)) == 3
    for h in hawbs:                                   # CJ check digit: body mod 7 shifted by a constant
        assert (int(h[-1]) - int(h[:-1])) % 7 == (3 - 60000000001) % 7


def test_lines_sorted_by_name_inside_order(run):
    ord1 = next(s for s in run.shipments if s.order_no == 'ORD-1')
    assert [l.item.name for l in ord1.lines] == ['Alpha Cream 50ml', 'Beta Candy 100g']


def test_prices_weights_fees(run):
    ord1 = next(s for s in run.shipments if s.order_no == 'ORD-1')
    assert [l.weight for l in ord1.lines] == [0.5, 0.5]           # Base weight x qty
    first = ord1.lines[0]
    assert first.delivery_fee == round(2.0 + 5.0 * 1.0, 2)        # basic + per kg x total weight
    assert first.packing_fee == 1.0
    assert ord1.lines[1].delivery_fee == 0


def test_invoices_per_company(run):
    assert [i.invoice_no for i in run.invoices] == ['AAA-26112401', 'BBB-26112401']
    assert run.export_invoice_no == 'EMG-26112401'


def test_warnings(run):
    w = '\n'.join(run.warnings)
    assert '브랜드명(H열)에 한글' in w                    # Gamma Tea brand 감마
    assert 'X12' in w and '개인통관고유부호' in w
    assert '02-123-4567' in w                              # not a mobile number
    assert '1098765432' not in w                           # number cell without leading 0 is fine
    assert 'Gamma Tea" 신고단가가 0' in w
    assert 'Gamma Tea" HS코드가 없습니다' in w


def test_weight_merge_and_cr_upload(run):
    rows = lists.weight_merged_rows(run)
    ord1 = [r for r in rows if r[1] == run.shipments[1].hawb]
    assert [r[C_WEIGHT] for r in ord1] == [1, 0]
    ws = lists.cr_upload(run).active
    assert ws['A1'].value == '순번' and ws['AC1'].value == 'Euromall GmbH'
    data = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(data) == 4
    # order fields only on the first line of a tracking number
    hawb_col = [r[2] for r in data]
    assert hawb_col.count(None) == 1
    assert data[0][10] == '01234'                           # zip kept as 5 digits
    assert all(r[22] for r in data)                         # item name on every line


def test_lock(tmp_path):
    d = str(tmp_path)
    app.write_lock(d)
    assert app.read_lock(d) is None                         # our own lock
    with open(os.path.join(d, app.LOCK_NAME), 'w') as f:
        json.dump({'computer': 'OTHER-PC', 'pid': 1, 'heartbeat': dt.datetime.now().isoformat()}, f)
    assert app.read_lock(d)['computer'] == 'OTHER-PC'
    old = (dt.datetime.now() - dt.timedelta(minutes=app.LOCK_STALE_MINUTES + 1)).isoformat()
    with open(os.path.join(d, app.LOCK_NAME), 'w') as f:
        json.dump({'computer': 'OTHER-PC', 'pid': 1, 'heartbeat': old}, f)
    assert app.read_lock(d) is None


def test_company_summary():
    from invoiceauto import db
    rows = [
        {'ymd': '2026-11-01', 'company_uid': 'AAA', 'status_cd': '0', 'profomal_amount': 100, 'release_amount': 110,
         'deposit_amount': 50},
        {'ymd': '2026-10-01', 'company_uid': 'AAA', 'status_cd': '0', 'profomal_amount': 0, 'release_amount': 20,
         'deposit_amount': 0},
        {'ymd': '2026-11-02', 'company_uid': 'BBB', 'status_cd': '1', 'profomal_amount': 0, 'release_amount': 80,
         'deposit_amount': 80},
        {'ymd': '2026-11-02', 'company_uid': 'EMG', 'status_cd': '0', 'profomal_amount': 0, 'release_amount': 999,
         'deposit_amount': 0},
    ]
    s = db.company_summary(rows)
    assert [x['company_uid'] for x in s] == ['AAA', 'BBB']
    assert s[0]['open'] == 80.0 and s[0]['unpaid_count'] == 2 and s[0]['oldest_unpaid'] == '2026-10-01'
    assert s[1]['open'] == 0.0 and s[1]['deposit'] == 80.0


def test_old_backups(tmp_path):
    import time
    root = tmp_path
    d = root / 'db' / 'backup'
    d.mkdir(parents=True)
    old = time.time() - 200 * 86400
    for i in range(25):
        p = d / f'euromall_{i:02d}.db'
        p.write_bytes(b'x' * 10)
        os.utime(p, (old + i, old + i))
    fresh = d / 'euromall_new.db'
    fresh.write_bytes(b'x')
    found = app.old_backups(app.Folder(str(root)), days=90, keep=20)
    names = sorted(os.path.basename(p) for p, _ in found)
    assert names == [f'euromall_{i:02d}.db' for i in range(6)]   # the 20 newest (fresh one included) are kept
    app.remove_backups([p for p, _ in found])
    assert len(os.listdir(d)) == 20


def test_update_version_compare():
    from invoiceauto import update
    assert update.is_newer('v12', '11')
    assert not update.is_newer('v12', '12')
    assert update.is_newer('v3', 'dev')
