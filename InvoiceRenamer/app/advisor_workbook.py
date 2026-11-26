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
                elif header[column - 1] in ("Datei im ZIP", "Belege im ZIP"):
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
