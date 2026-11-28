"""Made-up Base.xlsx and shipping file (no real customers) for the automatic tests."""
import os

import openpyxl

from invoiceauto.shipping import (NCOLS, C_HAWB, C_SENDER, C_ORDER_NO, C_BOX_CODE, C_RECV_NAME, C_MOBILE, C_ZIP,
                                  C_ADDR, C_CUSTOMS_NO, C_WEIGHT, C_ITEM_NAME, C_BRAND, C_UNIT_PRICE, C_QTY, C_HS)

COMPANIES = [
    # uid, sender, name, country, addr1, addr2, business_no, invoice_type, delivery_fee/kg, box fee, packing fee,
    # (11 unused), basic delivery fee
    ['AAA', 'Shop A', 'Test Shop A', 'KR', 'Street 1', 'Seoul', '1234567890', 'O', 5.0, 0, 1.0, None, 2.0],
    ['BBB', 'Shop B', 'Test Shop B', 'KR', 'Street 2', 'Busan', '2234567890', 'O', 4.0, 0, 0.5, None, 0.0],
]

ITEMS = [
    # A..S of newPrice: -, -, -, weight, unit price, item code, barcode, brand, name, -, price, remark, url,
    # singo unit price, singo hs, export hs, food type, manufacturer, origin
    [None, None, None, 0.5, 10.0, 'C-1', 'B000000001', 'Alpha', 'Alpha Cream 50ml', None, 12.5, None,
     'https://example.com/a', 9.99, '330499', '330499', None, 'Alpha', 'DE'],
    [None, None, None, 0.25, 4.0, 'C-2', 'B000000002', 'Beta', 'Beta Candy 100g', None, 5.25, None,
     'https://example.com/b', 3.333, '170490', '170490', None, 'Beta', 'AT'],
    [None, None, None, 1.0, 20.0, 'C-3', 'B000000003', '감마', 'Gamma Tea', None, 25.0, None,
     'https://example.com/c', 0, '', '', None, 'Gamma', 'DE'],
]

# order, sender, receiver, mobile, customs no, item, qty, weight
ORDERS = [
    ('ORD-1', 'Shop A', '홍길동', '010-1234-5678', 'P123456789012', 'Beta Candy 100g', 2, 0),
    ('ORD-1', 'Shop A', '홍길동', '010-1234-5678', 'P123456789012', 'Alpha Cream 50ml', 1, 0),
    ('ORD-2', 'Shop B', '김철수', 1098765432, 'P987654321098', 'Alpha Cream 50ml', 3, 0),
    ('ORD-3', 'Shop A', '이영희', '02-123-4567', 'X12', 'Gamma Tea', 1, 0),
]


def make_base(folder):
    os.makedirs(folder, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'info'
    ws.append(['uid'] * 27)
    for c in COMPANIES:
        ws.append(c)
    ws = wb.create_sheet('newPrice')
    ws.append(['h'] * 19)
    for it in ITEMS:
        ws.append(it)
    path = os.path.join(folder, 'Base.xlsx')
    wb.save(path)
    return path


def make_shipping(path, first_hawb='600000000013'):
    wb = openpyxl.Workbook()
    ws = wb.active
    for i, (order, sender, recv, mobile, customs, item, qty, weight) in enumerate(ORDERS):
        row = [None] * NCOLS
        row[C_HAWB] = first_hawb if i == 0 else None
        row[C_SENDER] = sender
        row[C_ORDER_NO] = order
        row[C_BOX_CODE] = '1'
        row[C_RECV_NAME] = recv
        row[C_MOBILE] = mobile
        row[C_ZIP] = 1234
        row[C_ADDR] = 'Seoul test address'
        row[C_CUSTOMS_NO] = customs
        row[C_WEIGHT] = weight
        row[C_ITEM_NAME] = item
        row[C_BRAND] = item.split()[0]
        row[C_UNIT_PRICE] = 1
        row[C_QTY] = qty
        row[C_HS] = None
        ws.append(row)
    wb.save(path)
    return path
