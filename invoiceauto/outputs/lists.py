"""Row-per-line workbooks: 적하목록, 무게병합, 포장리스트, PackingList, 송장업로드, 수입식품신고."""
import copy
import datetime as dt

import openpyxl

from ..engine import Run, Shipment, ItemLine, MODE_PROFORMA
from ..names import romanize_name
from ..shipping import (C_NO, C_HAWB, C_BOX_CODE, C_WIDTH, C_DEPTH, C_HEIGHT, C_WEIGHT, C_BOXES,
                        C_CUSTOMS_DESIGNATION, C_CONTRACT_NO, C_BRAND, C_UNIT_PRICE, C_QTY, C_HS,
                        C_POST_INVOICE, C_FOOD_TYPE, C_MANUFACTURER, C_ORIGIN, C_RECV_NAME, C_CUSTOMS_NO,
                        C_MOBILE, C_TEL, C_ITEM_NAME, NCOLS)
from ..util import text, num, clean_number, ascii_latin
from .invoices import HANGUL

WEEKDAYS_KO = '월화수목금토일'

# header of the original 67-column shipping layout, used by 적하목록 취합_무게병합
SHIPPING_HEADER = [
    'No', 'HAWB', '보내는사람(이름)', '보내는사람(전화)', '보내는사람(주소)', '관리번호', '박스코드', 'POL', '이름(한글)',
    '전화', '휴대폰', '우편번호', '우편번호주소', '상세주소', '통관번호/사업자번호', '배송메모', '받는이 구분(1:개인,2:사업자)',
    '전자상거래(1:전자상거래 구매대행, 2:일반, 3:전자상거래 개인직접수입형)', '홈페이지주소(전자상거래-배대지 URL)', '가로(Cm)',
    '세로(Cm)', '높이(Cm)', '중량', '중량단위(1:Kg, 2:Lbs)', 'Box수량', '일반신청', '통관지정번호', 'Gmarket체결번호', '상품명',
    '브랜드', '단가', '수량', 'HS코드', 'Freight Term', 'Customer Order NO', '우체국 송장번호', '로컬 언어 상품명', '통화코드',
    '해외 SALES TAX ', '해외 내 DELIVERY FEE', '식품종류', '제조사', '제조국가코드', '영업등록번호', '상호', '대표자', '연락처',
    '주소', '쇼핑몰주소', '로컬배송(Y/N)', 'Mall ID', 'Mall Item Code', '제품URL주소', '성분', '길이단위(1:cm, 2:inch)',
    '실화주ID', 'CARR CD(운송사)', '신용/착불(0/1)', '반품여부(Y/N)', '유형코드(필수값)', '해외판매자부호', '해외판매자상호',
    '대행업체등록번호', '대행업체상호(필수값)', '중개사업자등록번호', '중개사업자상호', '주문번호(필수값)',
]

BOX_SIZE = 10   # the carrier file always gets 10 x 10 x 10 cm boxes


def _date(d):
    return dt.datetime(d.year, d.month, d.day)


def _eval_simple(formula: str, raw: list):
    """Evaluates the two formulas users keep in the shipping file: =T2*U2*V2/6000 and =AE2*AF2."""
    import re
    from openpyxl.utils import column_index_from_string
    expr = formula[1:]
    def ref(m):
        return repr(num(raw[column_index_from_string(m.group(1)) - 1]))
    expr = re.sub(r'\$?([A-Z]{1,3})\$?\d+', ref, expr)
    if not re.fullmatch(r'[\d.+\-*/() ]+', expr):
        return None
    try:
        return round(eval(expr), 12)   # arithmetic only, checked above
    except ZeroDivisionError:
        return None


def _value(v, raw):
    if isinstance(v, str) and v.startswith('='):
        r = _eval_simple(v, raw)
        return clean_number(r) if r else None
    return v


def is_food(company, item) -> bool:
    """Lines the old program reports as imported food: the company has a 영업등록번호 and the item a 식품종류 (0 counts)."""
    return bool(company.report_br_number) and item is not None and item.food_type != ''


def cargo_rows(run: Run):
    """One 78-column row per line, in tracking-number order."""
    rows = []
    seq = 0
    for s in run.shipments:
        c = s.company
        for i, l in enumerate(s.lines):
            seq += 1
            raw = list(l.line.raw)
            out = [_value(v, raw) for v in raw]
            item = l.item
            out[C_NO] = seq
            out[C_HAWB] = s.hawb
            out[C_WEIGHT] = clean_number(l.weight)
            if i == 0:
                out[C_WIDTH] = out[C_DEPTH] = out[C_HEIGHT] = BOX_SIZE
                out[C_BOXES] = s.boxes
                out[C_POST_INVOICE] = out[C_POST_INVOICE] or s.hawb
            else:
                out[C_WIDTH] = out[C_DEPTH] = out[C_HEIGHT] = 0
                out[C_BOXES] = 0
                # the old program repeats the tracking number on later lines of a bill, not of a quote
                out[C_POST_INVOICE] = None if run.settings.mode == MODE_PROFORMA else str(s.hawb)
            if item:
                out[C_CUSTOMS_DESIGNATION] = item.barcode or None
                out[C_CONTRACT_NO] = item.url or None
                out[C_BRAND] = item.brand or out[C_BRAND]
                out[C_HS] = item.singo_hs_code or out[C_HS]
            out[C_UNIT_PRICE] = clean_number(l.declared_price)
            if is_food(c, item):
                out[C_FOOD_TYPE] = item.food_type
                out[C_MANUFACTURER] = item.manufacturer or out[C_MANUFACTURER]
                out[C_ORIGIN] = item.origin_country or out[C_ORIGIN]
                out[43:49] = [c.report_br_number, c.report_company_name, c.report_ceo_name,
                              c.report_phone_number, c.report_address, c.report_url]
            out[59:66] = [c.type_code or None, c.abroad_seller_mark or None, c.abroad_seller_company_name or None,
                          c.agency_registration_number or None, c.agency_company_name or None,
                          c.broker_registration_number or None, c.broker_company_name or None]
            for k in (C_BRAND, C_MANUFACTURER):
                out[k] = ascii_latin(out[k])
            out[66] = s.order_no
            name = l.line.recv_name
            extra = [c.uid, run.shipments.index(s) + 1 if i == 0 else None, None, (item.export_hs_code if item else None),
                     romanize_name(name) if HANGUL.search(name) else None, None, None, None,
                     item.item_code if item else None, item.barcode if item else None, item.url if item else None]
            out[67:78] = extra
            rows.append([None if v == '' else v for v in out])
    return rows


def _write_rows(ws, rows, start_row, ncols, style_row=None):
    for i, row in enumerate(rows):
        r = start_row + i
        for j in range(ncols):
            cell = ws.cell(r, j + 1)
            cell.value = row[j] if j < len(row) else None
            if style_row:
                cell._style = copy.copy(ws.cell(style_row, j + 1)._style)


def cargo_list(run: Run, template_path):
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    _write_rows(ws, cargo_rows(run), 2, NCOLS)
    return wb


def cargo_weight_merge(run: Run):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    _write_rows(ws, [SHIPPING_HEADER], 1, len(SHIPPING_HEADER))
    rows = [r[:len(SHIPPING_HEADER)] for r in cargo_rows(run)]
    for r in rows:
        r[C_CONTRACT_NO] = None      # Gmarket체결번호 stays empty in the carrier layout
    _write_rows(ws, rows, 2, len(SHIPPING_HEADER))
    return wb


def packing(run: Run, template_path):
    """포장리스트: one row per line; secondary lines of a shipment leave the order columns blank."""
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    d = run.settings.work_date
    ws['K1'] = f'{d.year}년 {d.month:02d}월 {d.day:02d}일 {WEEKDAYS_KO[d.weekday()]}요일'
    label = f'{d.month:02d}-{d.day:02d}({WEEKDAYS_KO[d.weekday()]})'
    r = 2
    for s in run.shipments:
        for i, l in enumerate(s.lines):
            item = l.item
            first = i == 0
            vals = [label if first else None, s.hawb if first else None,
                    (text(l.line.get(C_BOX_CODE)) or None) if first else None,
                    s.company.uid if first else None, l.line.recv_name if first else None, l.qty,
                    item.item_code if item else None, item.barcode if item else None, item.url if item else None,
                    clean_number(l.declared_price), ascii_latin(l.line.display_name(item)),
                    f'=IF(B{r}="", "", LEFT(B{r},6)&"-"&MID(B{r},7,4)&"-"&RIGHT(B{r},4))', f'=H{r}']
            for j, v in enumerate(vals):
                cell = ws.cell(r, j + 1)
                cell.value = v
                if r > 2:
                    cell._style = copy.copy(ws.cell(2, j + 1)._style)
            r += 1
    return wb


def packing_list(run: Run, template_path):
    """PackingList: items summed over the whole run, sorted by name."""
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    ws['E1'] = _date(run.settings.work_date)
    totals = {}
    for l in sorted((l for s in run.shipments for l in s.lines), key=lambda l: l.line.row):
        key = ascii_latin(l.line.display_name(l.item))
        if key not in totals:
            totals[key] = [l, 0]
        totals[key][1] += l.qty
    r = 2
    for name in sorted(totals):        # case-sensitive, like the old program
        l, qty = totals[name]
        item = l.item
        vals = [item.item_code if item else None, item.barcode if item else None, item.url if item else None,
                clean_number(l.declared_price), name, qty]
        for j, v in enumerate(vals):
            ws.cell(r, j + 1).value = v
        r += 1
    return wb


def invoice_upload(run: Run, template_path):
    """송장업로드: tracking number per order, for registering on the shopping malls."""
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    for i, s in enumerate(run.shipments):
        r = i + 2
        short = s.order_no.split('-', 1)[1] if '-' in s.order_no else s.order_no
        for j, v in enumerate([s.order_no, s.company.uid, short, s.hawb, s.first.recv_name, s.hawb]):
            ws.cell(r, j + 1).value = v
    return wb


def food_declaration(run: Run, template_path):
    """수입식품신고: one row per food line (see is_food)."""
    wb = openpyxl.load_workbook(template_path)
    ws = wb.active
    r = 3
    nr = 0
    for s in run.shipments:
        c = s.company
        if not c.report_br_number:
            continue
        ln = s.first
        for l in s.lines:
            item = l.item
            if not is_food(c, item):
                continue
            nr += 1
            vals = [nr, 'B', None, None, None, None, s.hawb, s.order_no, ln.recv_name,
                    text(ln.get(C_CUSTOMS_NO)) or None, text(ln.get(C_MOBILE)) or text(ln.get(C_TEL)) or None,
                    item.food_type if item else None, l.qty, ascii_latin(l.line.display_name(item)),
                    ascii_latin((item.manufacturer or item.brand) if item else text(l.line.get(C_BRAND))) or None,
                    (item.origin_country if item else None) or 'DE',
                    c.report_br_number, c.report_company_name, c.report_ceo_name, c.report_phone_number,
                    c.report_address, c.report_url]
            for j, v in enumerate(vals):
                ws.cell(r, j + 1).value = v if v != '' else None
            r += 1
    return wb
