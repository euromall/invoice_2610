"""Reference data from Base.xlsx: companies (info sheet) and items (newPrice sheet)."""
from dataclasses import dataclass
from typing import Optional

import openpyxl

import unicodedata

from .util import text, num, ascii_latin


def fold(name: str) -> str:
    """Comparison key for item names: accents removed (ä -> a, È -> E), spacing normalised."""
    return ' '.join(ascii_latin(unicodedata.normalize('NFC', name)).split())


@dataclass
class Company:
    uid: str                 # 식별자 (MK, SSM, ...)
    sender: str              # 송하인명 as it appears in the shipping file
    name: str                # 업체명
    country: str
    address1: str
    address2: str
    business_no: str
    invoice_type: str        # O: 구매, I: 한국, L: 배송
    delivery_fee: float      # per kg
    delivery_box_fee: float  # per box (not used by the old program)
    packing_fee: float       # per box
    basic_delivery_fee: float
    report_br_number: str    # 영업등록번호
    report_company_name: str
    report_ceo_name: str
    report_phone_number: str
    report_address: str
    report_url: str
    type_code: str
    abroad_seller_mark: str
    abroad_seller_company_name: str
    agency_registration_number: str
    agency_company_name: str
    broker_registration_number: str
    broker_company_name: str
    order_number: str

    @property
    def is_delivery_only(self) -> bool:
        return self.invoice_type == 'L'


@dataclass
class Item:
    name: str
    weight: float
    unit_price: float        # 단가 (purchase)
    item_code: str           # 상품코드
    barcode: str             # ASIN / EAN
    brand: str
    price: float             # 판매가 = 단가 + kg당 배송비 × 무게
    remark: str
    url: str
    singo_unit_price: float  # 신고 단가
    singo_hs_code: str
    export_hs_code: str
    food_type: str
    manufacturer: str
    origin_country: str


class RefData:
    def __init__(self, companies, items):
        self.companies = companies
        self.items = items
        self._folded = None
        self._by_sender = {}
        for c in companies.values():
            self._by_sender[c.sender.strip().lower()] = c
            self._by_sender[c.uid.strip().lower()] = c

    def company_for_sender(self, sender) -> Optional[Company]:
        return self._by_sender.get(text(sender).strip().lower())

    def item(self, name) -> Optional[Item]:
        """Exact name first, then with umlauts folded (Präbiotika -> Prabiotika, as the old program did)."""
        name = text(name).strip()
        found = self.items.get(name)
        if found is None:
            if self._folded is None:
                self._folded = {}
                for k, v in self.items.items():
                    self._folded.setdefault(fold(k), v)
            found = self._folded.get(fold(name))
        return found

    @classmethod
    def load(cls, path):
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        companies = {}
        for r in list(wb['info'].iter_rows(min_row=2, values_only=True)):
            r = list(r) + [None] * 30
            uid = text(r[0]).strip()
            if not uid:
                continue
            companies[uid] = Company(
                uid=uid, sender=text(r[1]).strip(), name=text(r[2]).strip(), country=text(r[3]),
                address1=text(r[4]), address2=text(r[5]), business_no=text(r[6]),
                invoice_type=text(r[7]).strip().upper() or 'O',
                delivery_fee=num(r[8]), delivery_box_fee=num(r[9]), packing_fee=num(r[10]),
                basic_delivery_fee=num(r[12]),
                report_br_number=text(r[13]), report_company_name=text(r[14]), report_ceo_name=text(r[15]),
                report_phone_number=text(r[16]), report_address=text(r[17]), report_url=text(r[18]),
                type_code=text(r[19]), abroad_seller_mark=text(r[20]), abroad_seller_company_name=text(r[21]),
                agency_registration_number=text(r[22]), agency_company_name=text(r[23]),
                broker_registration_number=text(r[24]), broker_company_name=text(r[25]),
                order_number=text(r[26]),
            )
        items = {}
        for r in wb['newPrice'].iter_rows(min_row=2, values_only=True):
            r = list(r) + [None] * 20
            name = text(r[8]).strip()
            if not name:
                continue
            if name in items:      # same name listed twice: the old program used the first row
                continue
            items[name] = Item(
                name=name, weight=num(r[3]), unit_price=num(r[4]), item_code=text(r[5]), barcode=text(r[6]),
                brand=text(r[7]), price=num(r[10]), remark=text(r[11]), url=text(r[12]),
                singo_unit_price=num(r[13]), singo_hs_code=text(r[14]), export_hs_code=text(r[15]),
                food_type=text(r[16]), manufacturer=text(r[17]), origin_country=text(r[18]),
            )
        wb.close()
        return cls(companies, items)
