"""Invoice workbooks: per-company (Proforma/Outside/Lifer), personal, and the EMG commercial invoice."""
import datetime as dt
import re

import openpyxl

from ..engine import Run, CompanyInvoice, Shipment, ItemLine, MODE_PROFORMA
from ..names import romanize_name
from ..util import clean_number, ascii_latin
from ..xlsx_template import insert_rows, replace_placeholders, find_row, find_cell

HANGUL = re.compile(r'[가-힣]')
FIRST_COL, LAST_COL = 1, 18     # A..R


def _date(d: dt.date):
    return dt.datetime(d.year, d.month, d.day)


def _english_name(name: str) -> str:
    """Romanized name for Hangul names; Latin names are already in the Name column."""
    return romanize_name(name) if HANGUL.search(name or '') else ''


def write_item_row(ws, r: int, nr, s: Shipment, l: ItemLine):
    """One line of the A..R item table shared by every invoice template."""
    item = l.item
    ln = l.line
    values = [
        nr,                                     # A Nr.
        s.hawb,                                 # B HAWB Nr.
        s.order_no,                             # C BestellNr.
        item.barcode if item else None,         # D Flug Nr. (the old program puts the barcode here)
        item.url if item else None,             # E MAWB Nr. (the old program puts the URL here)
        ln.recv_name,                           # F Name
        _english_name(ln.recv_name) or None,    # G Name (English)
        l.hs_code if item else None,            # H HS-code
        item.item_code if item else None,       # I code
        item.barcode if item else None,         # J Barcode
        item.url if item else None,             # K url
        clean_number(l.declared_price),         # L preis
        ascii_latin(ln.display_name(item)),     # M Bezeichnung
        l.qty,                                  # N stk.
        clean_number(l.goods_amount),           # O Waren
        clean_number(l.weight),                 # P kg
        clean_number(l.liefer),                 # Q Liefer
        clean_number(l.total),                  # R Gesamt
    ]
    for i, v in enumerate(values):
        ws.cell(r, FIRST_COL + i).value = v if v != '' else None


def _fill_item_table(ws, rows):
    """rows: list of (shipment, line) or None for a blank separator. Returns (first_row, last_row)."""
    band = find_row(ws, 1, '순번 자동부여')
    insert_rows(ws, band, max(0, len(rows) - 1))
    nr = 0
    for i, entry in enumerate(rows):
        r = band + i
        if entry is None:
            for c in range(FIRST_COL, LAST_COL + 1):
                ws.cell(r, c).value = None
            continue
        nr += 1
        write_item_row(ws, r, nr, *entry)
    return band, band + max(len(rows), 1) - 1


def _set_sums(ws, first, last, start_search):
    """Rewrites the Summe / Rechnungsbetrag formulas so they cover exactly the item rows."""
    summe = find_row(ws, 13, 'Summe')
    for c in 'NOPQ':
        ws[f'{c}{summe}'] = f'=SUM({c}{first}:{c}{last})'
    ws[f'R{summe}'] = f'=O{summe}+Q{summe}'
    total_row = find_row(ws, 13, 'Rechnungsbetrag')
    for c in 'QR':
        v = ws[f'{c}{total_row}'].value
        if isinstance(v, str) and v.startswith('=SUM('):
            ws[f'{c}{total_row}'] = f'=SUM(R{first}:R{last})'


def _header(ws, run: Run, invoice_no, name, address2, address1, business_no):
    d = run.settings.work_date
    replace_placeholders(ws, {
        '#invoice_no': invoice_no, '#company': name, '#address2': address2, '#address1': address1,
        '#business_no': business_no, '#date': _date(d),
    })
    # Lieferdatum is the day after the invoice date
    lief, _ = find_cell(ws, 'Lieferdatum')
    ws.cell(lief, 18).value = _date(run.settings.delivery_date)


def company_invoice(run: Run, inv: CompanyInvoice, template_path):
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    c = inv.company
    _header(ws, run, inv.invoice_no, c.name, c.address2, c.address1, c.business_no)
    rn = find_row(ws, 1, 'Rechnung Nr. ')
    ws.cell(rn, 2).value = inv.invoice_no
    rows = [(s, l) for s in inv.shipments for l in s.lines]
    first, last = _fill_item_table(ws, rows)
    _set_sums(ws, first, last, first)
    return wb


def person_invoice(run: Run, s: Shipment, template_path, english_address: str):
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    ln = s.first
    from ..shipping import C_MOBILE, C_TEL
    from ..util import text
    phone = text(ln.get(C_MOBILE)).strip() or text(ln.get(C_TEL)).strip()
    _header(ws, run, s.order_no, _english_name(ln.recv_name) or ln.recv_name, english_address, None, phone)
    rows = [(s, l) for l in s.lines]
    first, last = _fill_item_table(ws, rows)
    _set_sums(ws, first, last, first)
    return wb


def export_invoice(run: Run, template_path):
    """EMG commercial invoice: every non-delivery-only company, a blank row between companies."""
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    d = run.settings.work_date
    replace_placeholders(ws, {'#invoice_no': run.export_invoice_no, '#date': _date(d)})
    lief, _ = find_cell(ws, 'Lieferdatum')
    ws.cell(lief, 18).value = _date(run.settings.delivery_date)

    invs = run.export_invoices
    rows = []
    for i, inv in enumerate(invs):
        if i:
            rows.append(None)
        rows.extend((s, l) for s in sorted(inv.shipments, key=lambda s: s.order_no) for l in s.lines)
    first, last = _fill_item_table(ws, rows)

    # importer table: one row per company with its invoice number and total
    imp = find_row(ws, 1, 'Importer') + 1
    insert_rows(ws, imp, max(0, len(invs) - 1))
    for i, inv in enumerate(invs):
        c = inv.company
        label = ' '.join(x for x in [c.name, c.address2] if x)
        if c.business_no:
            label += f' ({c.business_no})'
        r = imp + i
        if i:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=16)
        ws.cell(r, 1).value = label
        ws.cell(r, 17).value = inv.invoice_no
        ws.cell(r, 18).value = clean_number(inv.total)
    _set_sums(ws, first, last, first)
    return wb


def company_invoice_filename(run: Run, inv: CompanyInvoice) -> str:
    seq = f'{run.settings.work_date:%Y%m%d}{run.settings.invoice_seq:02d}'
    if inv.company.is_delivery_only:
        kind = 'Lifer_invoice'
    elif run.settings.mode == MODE_PROFORMA:
        kind = 'Profoma_invoice'
    else:
        kind = 'Outside_invoice'
    return f'{kind}_{inv.company.uid}_{seq}'


def export_invoice_filename(run: Run) -> str:
    return f'Export_invoice_EMG_{run.settings.work_date:%Y%m%d}{run.settings.invoice_seq:02d}'


def person_invoice_filename(s: Shipment) -> str:
    return f'Person_invoice_{s.hawb}_{s.order_no}'
