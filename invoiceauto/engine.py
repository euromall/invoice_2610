"""Turns shipping lines into shipments (one per 관리번호/HAWB) with prices and fees."""
import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .names import romanize_name
from .refdata import Company, Item, RefData
from .shipping import Line, C_HAWB, C_UNIT_PRICE, C_HS
from .util import text, num, yymmdd

CARRIER_POST = 'post'   # 우체국
CARRIER_CJ = 'cj'       # CJ대한통운

MODE_PROFORMA = 'proforma'   # 견적서용
MODE_BILL = 'bill'           # 청구서용


@dataclass
class Settings:
    work_date: dt.date
    mode: str = MODE_PROFORMA
    carrier: str = CARRIER_CJ
    auto_hawb: bool = True            # 송장번호 자동입력
    save_pdf: bool = True
    apply_accounting: bool = True     # 회계반영
    make_person_invoice: bool = False
    make_export_per_company: bool = False   # 업체별 수출용 인보이스
    make_cargo_weight_merge: bool = True    # 적하목록 취합 무게병합
    invoice_seq: int = 1

    @property
    def delivery_date(self):
        return self.work_date + dt.timedelta(days=1)


@dataclass
class ItemLine:
    line: Line
    item: Optional[Item]
    qty: int
    weight: float          # kg charged for this line (only the shipment's first line carries weight)
    price: float           # 판매가 per unit (0 when the item is not in Base.xlsx)
    declared_price: float  # 신고 단가, shown in the preis column
    delivery_fee: float = 0.0
    packing_fee: float = 0.0
    is_first: bool = False

    @property
    def goods_amount(self):          # Waren
        return round(self.price * self.qty, 2)

    @property
    def liefer(self):
        return round(self.delivery_fee + self.packing_fee, 2)

    @property
    def total(self):                 # Gesamt
        return round(self.goods_amount + self.liefer, 2)

    @property
    def hs_code(self):
        if self.item and self.item.export_hs_code:
            return self.item.export_hs_code
        return text(self.line.get(C_HS)).strip()


@dataclass
class Shipment:
    order_no: str
    company: Company
    lines: List[ItemLine]
    hawb: str = ''
    recv_name_en: str = ''

    @property
    def first(self) -> Line:
        return self.lines[0].line

    @property
    def weight(self):
        return sum(l.weight for l in self.lines)

    @property
    def boxes(self):
        return max(1, self.first.boxes)

    @property
    def total(self):
        return round(sum(l.total for l in self.lines), 2)


@dataclass
class CompanyInvoice:
    company: Company
    invoice_no: str
    shipments: List[Shipment]

    @property
    def total(self):
        return round(sum(s.total for s in self.shipments), 2)

    @property
    def lines(self):
        return [l for s in self.shipments for l in s.lines]


@dataclass
class Run:
    settings: Settings
    shipments: List[Shipment]            # in HAWB order
    invoices: List[CompanyInvoice]       # one per company, sorted by company uid
    warnings: List[str] = field(default_factory=list)

    @property
    def export_invoice_no(self):
        return f'EMG-{yymmdd(self.settings.work_date)}{self.settings.invoice_seq:02d}'

    @property
    def export_invoices(self):
        """Companies on the EMG commercial invoice (everything except delivery-only customers)."""
        return [inv for inv in self.invoices if not inv.company.is_delivery_only]

    @property
    def export_total(self):
        return round(sum(i.total for i in self.export_invoices), 2)


def next_hawb(hawb: str, carrier: str, check_offset: int) -> str:
    """Next tracking number. CJ numbers end in a mod-7 check digit; post office numbers just count up."""
    if carrier == CARRIER_CJ and len(hawb) >= 2:
        body = int(hawb[:-1]) + 1
        return f'{body:0{len(hawb) - 1}d}{(body + check_offset) % 7}'
    return f'{int(hawb) + 1:0{len(hawb)}d}'


def cj_check_offset(hawb: str) -> int:
    body, check = int(hawb[:-1]), int(hawb[-1])
    return (check - body) % 7


def build_run(lines: List[Line], ref: RefData, settings: Settings) -> Run:
    warnings = []

    # group lines into shipments by 관리번호 (input order kept inside a shipment)
    groups: Dict[str, List[Line]] = {}
    for ln in lines:
        groups.setdefault(ln.order_no, []).append(ln)

    shipments = []
    for order_no, glines in groups.items():
        company = ref.company_for_sender(glines[0].sender)
        if company is None:
            warnings.append(f'{glines[0].row}행: 보내는사람 "{glines[0].sender}"이(가) Base.xlsx info에 없습니다.')
            continue
        item_lines = []
        for i, ln in enumerate(glines):
            item = ref.item(ln.item_name)
            if item is None and company.invoice_type != 'L':
                warnings.append(f'{ln.row}행: 상품 "{ln.item_name}"이(가) Base.xlsx newPrice에 없습니다.')
            if ln.weight > 0:
                weight = ln.weight
            elif item is not None:
                weight = item.weight * ln.qty
            else:
                weight = 0.0
            item_lines.append(ItemLine(
                line=ln, item=item, qty=ln.qty, weight=weight,
                price=item.price if item else 0.0,
                declared_price=item.singo_unit_price if item else num(ln.get(C_UNIT_PRICE)),
            ))
        shipment = Shipment(order_no=order_no, company=company, lines=item_lines)
        _apply_fees(shipment)
        shipment.recv_name_en = romanize_name(glines[0].recv_name)
        shipments.append(shipment)

    # tracking numbers: shipments sorted by first item name, larger quantity first, then order number
    shipments.sort(key=lambda s: (s.lines[0].line.item_name, -s.lines[0].qty, s.order_no))   # case-sensitive, like the old program
    # Tracking numbers. When every order already has one in the file they are kept as they are.
    # Otherwise the first number in the file is the starting number and all orders are numbered from it.
    for s in shipments:
        s.hawb = next((text(l.line.get(C_HAWB)).strip() for l in s.lines if text(l.line.get(C_HAWB)).strip()), '')
    if settings.auto_hawb and not all(s.hawb for s in shipments):
        start = next((text(ln.get(C_HAWB)).strip() for ln in lines if text(ln.get(C_HAWB)).strip()), '')
        if start:
            offset = cj_check_offset(start) if settings.carrier == CARRIER_CJ else 0
            hawb = start
            for s in shipments:
                s.hawb = hawb
                hawb = next_hawb(hawb, settings.carrier, offset)
    missing = [s.order_no for s in shipments if not s.hawb]
    if missing:
        warnings.append('송장번호가 없는 주문: ' + ', '.join(missing))

    # per-company invoices
    by_company: Dict[str, List[Shipment]] = {}
    for s in shipments:
        by_company.setdefault(s.company.uid, []).append(s)
    invoices = []
    suffix = f'{yymmdd(settings.work_date)}{settings.invoice_seq:02d}'
    for uid in sorted(by_company, key=str.upper):
        ships = [s for s in shipments if s.company.uid == uid]   # tracking-number order
        invoices.append(CompanyInvoice(company=ships[0].company, invoice_no=f'{uid}-{suffix}', shipments=ships))

    return Run(settings=settings, shipments=shipments, invoices=invoices, warnings=warnings)


def _apply_fees(s: Shipment):
    """Delivery and packing fees go on the shipment's first line only."""
    c = s.company
    first = s.lines[0]
    first.is_first = True
    total_weight = sum(l.weight for l in s.lines)
    first.delivery_fee = round(c.basic_delivery_fee + c.delivery_fee * total_weight, 2)
    first.packing_fee = round(c.packing_fee * s.boxes, 2)
