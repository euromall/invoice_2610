"""Reads the shipping Excel (우체국/CJ 78-column layout) into Line objects."""
from dataclasses import dataclass, field
from typing import List

import openpyxl

from .util import text, num

NCOLS = 78

# 0-based column positions in the shipping file (same layout as CargoList.xlsx)
C_NO, C_HAWB, C_SENDER, C_SENDER_TEL, C_SENDER_ADDR, C_ORDER_NO, C_BOX_CODE, C_POL = range(8)
C_RECV_NAME, C_TEL, C_MOBILE, C_ZIP, C_ADDR, C_ADDR_DETAIL, C_CUSTOMS_NO, C_MEMO = range(8, 16)
C_WIDTH, C_DEPTH, C_HEIGHT, C_WEIGHT, C_WEIGHT_UNIT, C_BOXES = 19, 20, 21, 22, 23, 24
C_CUSTOMS_DESIGNATION, C_CONTRACT_NO, C_ITEM_NAME, C_BRAND, C_UNIT_PRICE, C_QTY, C_HS = 26, 27, 28, 29, 30, 31, 32
C_POST_INVOICE = 35
C_FOOD_TYPE, C_MANUFACTURER, C_ORIGIN = 40, 41, 42


@dataclass
class Line:
    row: int                     # 1-based row in the shipping file
    raw: list                    # all 78 cell values as read
    values: dict = field(default_factory=dict)  # evaluated formula results, by column

    def get(self, col):
        return self.raw[col]

    @property
    def order_no(self):
        return text(self.raw[C_ORDER_NO]).strip()

    @property
    def sender(self):
        return text(self.raw[C_SENDER]).strip()

    @property
    def item_name(self):
        return text(self.raw[C_ITEM_NAME]).strip()

    def display_name(self, item=None):
        """Name written to the outputs: the Base.xlsx spelling when the item was found there."""
        return item.name if item else self.item_name

    @property
    def qty(self):
        return int(num(self.raw[C_QTY], 1) or 1)

    @property
    def weight(self):
        return num(self.raw[C_WEIGHT])

    @property
    def dims(self):
        return num(self.raw[C_WIDTH]), num(self.raw[C_DEPTH]), num(self.raw[C_HEIGHT])

    @property
    def boxes(self):
        return int(num(self.raw[C_BOXES], 1))

    @property
    def recv_name(self):
        return text(self.raw[C_RECV_NAME]).strip()


def read_shipping(path) -> List[Line]:
    wb = openpyxl.load_workbook(path)          # formulas as written
    ws = wb.worksheets[0]
    lines = []
    for r in ws.iter_rows(min_row=1):
        raw = [c.value for c in r][:NCOLS]
        raw += [None] * (NCOLS - len(raw))
        if not (text(raw[C_ORDER_NO]).strip() and text(raw[C_ITEM_NAME]).strip()):
            continue
        if text(raw[C_ORDER_NO]).strip() == '관리번호':   # header row
            continue
        lines.append(Line(row=r[0].row, raw=raw))
    wb.close()
    return lines
