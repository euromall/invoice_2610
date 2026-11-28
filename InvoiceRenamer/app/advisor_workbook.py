import csv
import io
import re
from decimal import Decimal
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


MONEY_COLUMNS = {
    "Betrag", "Brutto", "Netto", "USt", "Zugeordneter Betrag", "Nicht zugeordneter Betrag"
}
SHEET_NAMES = {
    "01_Abgleich.csv": "Abgleich",
    "03_Offene_Posten.csv": "Offene Posten",
    "06_Nachreichungen.csv": "Nachreichungen",
    "02_Belegdetails.csv": "Belege",
    "10_Anlagen.csv": "Anlagen",
    "11_Endkontrolle.csv": "Endkontrolle",
    "12_Rueckfragen.csv": "Rückfragen",
    "13_Kasse.csv": "Kasse",
}


def localized_csv(raw):
    rows = list(csv.reader(io.StringIO(raw), delimiter=";"))
    if not rows:
        return raw
    numeric = {index for index, name in enumerate(rows[0]) if name in MONEY_COLUMNS}
    for row in rows[1:]:
        for index in numeric:
            if index < len(row) and re.fullmatch(r"-?\d+(?:\.\d+)?", row[index]):
                row[index] = row[index].replace(".", ",")
    out = io.StringIO(newline="")
    csv.writer(out, delimiter=";").writerows(rows)
    return out.getvalue()


def advisor_workbook(tables, members):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for filename, title in SHEET_NAMES.items():
        raw = tables.get(filename)
        if raw is None:
            continue
        rows = list(csv.reader(io.StringIO(raw), delimiter=";"))
        if not rows:
            continue
        sheet = workbook.create_sheet(title)
        header = rows[0]
        for row_index, row in enumerate(rows, 1):
            for column, value in enumerate(row, 1):
                if not value:
                    continue
                cell = sheet.cell(row_index, column)
                if row_index > 1 and header[column - 1] in MONEY_COLUMNS and re.fullmatch(r"-?\d+(?:[,.]\d+)?", value):
                    cell.value = Decimal(value.replace(",", "."))
                    cell.number_format = "#,##0.00"
                else:
                    cell.value = value
                    cell.data_type = "s"
                if row_index == 1:
                    cell.font = Font(color="FFFFFF", bold=True)
                    cell.fill = PatternFill("solid", fgColor="24394D")
                elif header[column - 1] in ("Datei im ZIP", "Belege im ZIP", "Beleg im ZIP"):
                    paths = value.split(" | ")
                    if len(paths) == 1 and paths[0] in members:
                        cell.hyperlink = quote(paths[0], safe="/")
                        cell.style = "Hyperlink"
                    elif len(paths) > 1:
                        cell.alignment = Alignment(wrap_text=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            width = max((len(str(cell.value or "")) for cell in column), default=10)
            sheet.column_dimensions[get_column_letter(column[0].column)].width = min(max(13, width + 2), 62)
    result = io.BytesIO()
    workbook.save(result)
    return result.getvalue()


def cost_rows(records, sellers=None, accounts=None):
    """v1.14 ‘비용 집계’: one row per bank/card transaction of every month that was loaded once (records keep date,
    amount and payee). Internal transfers and card bill payments are left out. sellers: {record key: invoice seller}.
    Returns [{number, month, day, account, vendor, payee, amount, currency}]."""
    sellers, accounts = sellers or {}, accounts or {}
    rows = []
    for key, record in records.items():
        if not isinstance(record, dict) or record.get("transfer") or not record.get("period") or record.get("amount") in (None, ""):
            continue
        try:
            amount = Decimal(str(record["amount"]))
        except ArithmeticError:
            continue
        payee = " ".join(str(record.get("merchant", "")).split(" | ")[0].split())
        rows.append({"number": record.get("number", ""), "month": record["period"], "day": record.get("day", ""),
                     "account": accounts.get(key, ""), "vendor": sellers.get(key) or payee or "(이름 없음)",
                     "payee": payee, "amount": amount, "currency": record.get("currency") or "EUR"})
    return sorted(rows, key=lambda row: (row["month"], row["number"]))


def cost_workbook(rows):
    """Excel for the owner: 업체별 · 월별 · 업체×월 · 거래. Positive amounts are costs, negative ones refunds/credits."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    header_font, header_fill = Font(color="FFFFFF", bold=True), PatternFill("solid", fgColor="24394D")

    def sheet(title, header, lines, money_from=None, widths=None):
        page = workbook.create_sheet(title)
        page.append(header)
        for cell in page[1]:
            cell.font, cell.fill = header_font, header_fill
        for line in lines:
            page.append(line)
        for row in page.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, Decimal) or (money_from is not None and cell.column > money_from and isinstance(cell.value, (int, float))):
                    cell.number_format = "#,##0.00"
        for index, width in enumerate(widths or [], 1):
            page.column_dimensions[get_column_letter(index)].width = width
        page.freeze_panes = "A2"
        return page

    by_vendor, by_month, pivot = {}, {}, {}
    months = sorted({row["month"] for row in rows})
    for row in rows:
        cost, credit = (row["amount"], Decimal(0)) if row["amount"] > 0 else (Decimal(0), row["amount"])
        vendor = by_vendor.setdefault((row["vendor"], row["currency"]), [0, Decimal(0), Decimal(0)])
        month = by_month.setdefault((row["month"], row["currency"]), [0, Decimal(0), Decimal(0)])
        for total in (vendor, month):
            total[0] += 1
            total[1] += cost
            total[2] += credit
        cells = pivot.setdefault((row["vendor"], row["currency"]), {})
        cells[row["month"]] = cells.get(row["month"], Decimal(0)) + row["amount"]
    vendor_lines = sorted(([name, currency, count, cost, credit, cost + credit] for (name, currency), (count, cost, credit) in by_vendor.items()),
                          key=lambda line: (line[1], -line[5], line[0].casefold()))
    sheet("업체별", ["업체", "통화", "건수", "지출", "환불·입금", "순액"], vendor_lines, widths=[42, 8, 8, 14, 14, 14])
    month_lines = [[month, currency, count, cost, credit, cost + credit] for (month, currency), (count, cost, credit) in sorted(by_month.items())]
    sheet("월별", ["마감월", "통화", "건수", "지출", "환불·입금", "순액"], month_lines, widths=[12, 8, 8, 14, 14, 14])
    pivot_lines = []
    for (name, currency), cells in sorted(pivot.items(), key=lambda item: (item[0][1], -sum(item[1].values()), item[0][0].casefold())):
        values = [cells.get(month, Decimal(0)) for month in months]
        pivot_lines.append([name, currency, *values, sum(values, Decimal(0))])
    sheet("업체×월", ["업체", "통화", *months, "합계"], pivot_lines, widths=[42, 8, *[13] * len(months), 14])
    sheet("거래", ["거래번호", "마감월", "날짜", "계좌·카드", "업체 (인보이스)", "명세서 거래처", "금액", "통화"],
          [[row["number"], row["month"], row["day"], row["account"], row["vendor"], row["payee"], row["amount"], row["currency"]] for row in rows],
          widths=[11, 10, 12, 20, 36, 36, 13, 8])
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()
