import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from datetime import date
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from zipfile import ZipFile

import pymupdf

import learned_formats
from release_info import VERSION, export_diagnostics, record_error
from ui_theme import COLORS, FONT_BOLD, Busy, InlineDialog, Tooltip, ask_yes_no, resort, show_notice, show_toast, table_tools


DATE = r"(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})"
DATE_LABEL = r"Rechnungsdatum|Ausstellungsdatum|Invoice Date|Issue Date|발행일(?:자)?|작성일(?:자)?"
TOTAL_LABEL = (
    r"Gesamtpreis[ -]*Brutto|Gesamt[ -]*Brutto|Bruttogesamtbetrag|Bruttobetrag|"
    r"Rechnungsbetrag(?:\s+brutto)?|Endbetrag|Grand Total|Invoice Total|"
    r"Total incl\.? (?:VAT|tax)|청구\s*총액|합계\s*금액|총\s*청구\s*금액"
)
# Used only when none of the labels above yields an amount, so invoices that
# were recognised before v1.1.1 keep the same result.
TOTAL_LABEL_FALLBACK = (
    r"Gesamtbetrag(?:\s+brutto)?|Gesamtsumme(?:\s+brutto)?|Bruttosumme|Rechnungssumme|Endsumme|Gesamtpreis|"
    r"Zahlbetrag|Zahlungsbetrag|zu\s+zahlender\s+Betrag|zu\s+zahlen|"
    r"Total\s+(?:due|amount)|Amount\s+due|Balance\s+due"
)
# v1.6: weaker words, used only when the fallback labels above find nothing (‘USt. Gesamt’ is often the VAT line).
# ‘Rechnungsbetrag (inklusive Umsatzsteuer) 7,87 EUR’ (DHL) or ‘Gesamtbetrag inkl. MwSt.: …’: a short note after the label.
LABEL_NOTE = r"(?:[ \t]*\([^()\n]{1,60}\)|[ \t]+inkl\.?[ \t]*(?:MwSt|USt|Umsatzsteuer)\.?)?"
TOTAL_LABEL_WEAK = r"Gesamt|Summe(?:\s+brutto)?|Total"
MONTH_WORDS = (r"Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|"
               r"January|February|March|May|June|July|October|December|"
               r"Jan|Feb|Mär|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Okt|Oct|Nov|Dez|Dec")
LONG_DATE = rf"\d{{1,2}}\.?[ \t]+(?:{MONTH_WORDS})\.?[ \t]+\d{{4}}|(?:{MONTH_WORDS})\.?[ \t]+\d{{1,2}},?[ \t]+\d{{4}}"
WEEKDAYS = r"Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag"
LEGAL_FORM = (r"GmbH(?:[ \t]*&[ \t]*Co\.?[ \t]*(?:KGaA|KG|OHG))?|AG|KGaA|KG|OHG|UG(?:[ \t]*\(haftungsbeschränkt\))?|"
              r"e\.[ \t]?K\.|GbR|SE|mbH|S\.?[ \t]?[àa]\.?[ \t]?r\.?[ \t]?l\.?|Ltd\.?|Limited|Inc\.?|LLC|B\.V\.|S\.A\.|S\.r\.l\.")
# The sender's one-line address at the top of a letter, e.g. ‘ENTEGA Plus GmbH, Postfach 11 07 61, 64222 Darmstadt’.
SENDER_LINES = (
    rf"^[ \t]*((?:[^\W\d_][\w.&'’+-]*[ \t]+){{0,6}}?(?:{LEGAL_FORM}))(?![\w])[^\n]{{0,90}}?\b\d{{5}}[ \t]+[^\W\d_]",
    r"^[ \t]*([^\W\d_][^\n,|]{2,50}?)[ \t]+[-–|][ \t]+[^\n]{3,60}?[ \t]\d+[ \t]?\w?[ \t]+[-–|][ \t]+\d{5}[ \t]+[^\W\d_]",
)
CURRENCY = r"Euro|EUR|USD|GBP|KRW|CHF|€|\$|£|₩|원"
NUMBER = r"-?\d+(?:[.,'’\u00a0 ]\d{3})*(?:[.,]\d{2})?"
MONEY = rf"(?:(?P<before>{CURRENCY})[ \t]*(?P<first>{NUMBER})|(?P<last>{NUMBER})[ \t]*(?P<after>{CURRENCY}))(?![\w.,])"
VENDOR_LABEL = r"Lieferant|Verkäufer|Rechnungssteller|Supplier|Seller|공급자|업체명"
# Sellers recognised by the first lines of the document (pattern, seller name).
# Add more without changing code in %LOCALAPPDATA%\InvoiceRenamer\seller-patterns.json,
# for example {"^\\s*Muster GmbH\\b": "Muster GmbH"}.
HEADER_SELLERS = (
    (r"^\s*greenist GmbH\b", "greenist GmbH"),
    (r"^\s*Jejo Media\s+Jeff Mittner\b", "Jejo Media"),
)


def unique(values):
    values = set(values)
    return values.pop() if len(values) == 1 else ""


def normalized_date(value):
    parts = re.split(r"[-./]", value)
    if len(parts[0]) != 4:
        parts.reverse()
    try:
        return date(*map(int, parts)).isoformat()
    except (ValueError, TypeError):
        return ""


def normalized_money(match):
    currency = (match["before"] or match["after"]).upper()
    currency = {"€": "EUR", "EURO": "EUR", "£": "GBP", "₩": "KRW", "원": "KRW"}.get(currency, currency)
    raw = match["first"] or match["last"]
    cents = re.search(r"[.,](\d{2})$", raw)
    if cents:
        whole = re.sub(r"[^\d-]", "", raw[:cents.start()])
        amount = Decimal(f"{whole}.{cents[1]}")
    else:
        amount = Decimal(re.sub(r"[^\d-]", "", raw))
    if currency == "KRW" and amount == amount.to_integral_value():
        return f"{amount:.0f} {currency}"
    return f"{amount:.2f} {currency}"


def user_data_folder():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InvoiceRenamer"


def header_sellers():
    patterns = list(HEADER_SELLERS)
    path = user_data_folder() / "seller-patterns.json"
    try:
        extra = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return patterns
    if isinstance(extra, dict):
        for pattern, name in extra.items():
            if isinstance(pattern, str) and isinstance(name, str) and name.strip():
                try:
                    re.compile(pattern)
                except re.error:
                    continue
                patterns.append((pattern, name.strip()))
    return patterns


def any_date(value):
    return normalized_date(value) or learned_formats.parse_date(value)


def sender_line(layouts):
    """Seller from the one-line sender address near the top (first page order), or ''."""
    for layout in reversed(layouts):
        top = "\n".join([line for line in layout.splitlines() if line.strip()][:40])
        for pattern in SENDER_LINES:
            match = re.search(f"(?im){pattern}", top)
            if match:
                return " ".join(match[1].split())
    return ""


def invoice_fields(layouts):
    dates = []
    amounts = []
    fallback_amounts = []
    weak_amounts = []
    known_sellers = header_sellers()
    sellers = []
    labelled_sellers = []
    alternate_dates = []
    for layout in layouts:
        dates.extend(re.findall(rf"(?im)\b(?:{DATE_LABEL})(?:\s*/\s*Lieferdatum)?\s*[:：]?[ \t]*(?:\n[ \t]*)?({DATE}|{LONG_DATE})(?![\d])", layout))
        alternate_dates.extend(re.findall(rf"(?im)^[ \t]*(?:{WEEKDAYS}),[ \t]*({LONG_DATE})[ \t]*$", layout))
        labelled_sellers.extend(re.findall(r"(?im)\bVerkauft\s+von[ \t]*:?[ \t]*(?:\n[ \t]*)?([^\W\d_][^\n]*?)(?:[ \t]{2,}|[ \t]*$)", layout))
        for line in layout.splitlines():
            fallback = re.search(rf"(?i)^\s*Rechnung\b(?:\s+Nr\.?\s*:?\s*[\w/-]+)?[ \t]+(?:[^\d\n]+?\bden[ \t]+)?({DATE})\b", line)
            if fallback:
                alternate_dates.append(fallback[1])
        for match in re.finditer(rf"(?im)\b(?:{TOTAL_LABEL})\b{LABEL_NOTE}[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?{MONEY}", layout):
            amounts.append(normalized_money(match))
        for match in re.finditer(rf"(?im)\b(?:{TOTAL_LABEL_FALLBACK})\b{LABEL_NOTE}[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?{MONEY}", layout):
            fallback_amounts.append(normalized_money(match))
        for match in re.finditer(rf"(?im)\b(?:{TOTAL_LABEL_WEAK})\b[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?{MONEY}", layout):
            weak_amounts.append(normalized_money(match))
        sellers.extend(re.findall(rf"(?im)^\s*(?:{VENDOR_LABEL})\s*[:：][ \t]*([^\n]+)", layout))
        header = "\n".join(line for line in layout.splitlines() if line.strip())
        header = "\n".join(header.splitlines()[:12])
        for pattern, name in known_sellers:
            if re.search(f"(?im){pattern}", header):
                sellers.append(name)
    seller = unique(" ".join(seller.split()) for seller in sellers)
    if not seller and not sellers:
        seller = unique(" ".join(seller.split()) for seller in labelled_sellers) or sender_line(layouts)
    total = unique(amounts) if amounts else unique(fallback_amounts) if fallback_amounts else unique(weak_amounts)
    return unique(filter(None, map(any_date, dates or alternate_dates))), seller, total


def tessdata_folder():
    candidates = [
        Path(__file__).resolve().parent / "tessdata",
        Path(getattr(sys, "_MEIPASS", "")) / "tessdata" if getattr(sys, "frozen", False) else None,
        user_data_folder() / "tessdata",
        os.environ.get("TESSDATA_PREFIX"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tessdata",
        Path("/usr/share/tesseract-ocr/5/tessdata"),
    ]
    for candidate in filter(None, candidates):
        folder = Path(candidate)
        languages = [code for code in ("deu", "eng", "kor") if (folder / f"{code}.traineddata").is_file()]
        if languages:
            return str(folder), "+".join(languages)
    return None, ""


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
EVIDENCE_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
EXCLUDED_FOLDER = "제외"
STATEMENT_FOLDER = "명세서"
DUPLICATE_FOLDER = "중복"  # duplicate invoices set aside by 중복 정리 (never deleted)  # imported bank/card statements, kept apart from evidence
INVOICE_WORDS = (
    "rechnung", "rechnungsnummer", "invoice", "receipt", "quittung", "beleg", "kassenbon", "ust", "mwst",
    "umsatzsteuer", "steuernummer", "vat", "netto", "brutto", "gesamt", "betrag", "summe", "total", "iban",
    "zahlbar", "fällig", "영수증", "세금계산서", "청구서", "합계", "공급가액", "부가세",
)


def invoice_likeness(text):
    """Rough score: how many invoice words appear in the text."""
    words = text.casefold()
    return sum(1 for word in INVOICE_WORDS if re.search(rf"(?<!\w){re.escape(word)}", words))


# The renaming screen also lists XRechnung XML files. Monthly close keeps its own list.
RENAMER_EXTENSIONS = EVIDENCE_EXTENSIONS | {".xml"}


def structured_fields(path):
    """Return (fields, number, credit) from XRechnung/ZUGFeRD data, or None."""
    from tax_documents import read_structured
    from xml.etree.ElementTree import ParseError

    if path.suffix.lower() not in (".pdf", ".xml"):
        return None
    try:
        meta = read_structured(path)
    except (OSError, RuntimeError, ValueError, ParseError):
        return None
    if not meta:
        return None
    total = ""
    currency = meta.get("currency", "").upper()
    try:
        amount = Decimal(meta.get("amount", ""))
    except ArithmeticError:
        amount = None
    if amount is not None and re.fullmatch(r"[A-Z]{3}", currency):
        total = f"{amount:.0f} {currency}" if currency == "KRW" and amount == amount.to_integral_value() else f"{amount:.2f} {currency}"
    seller = " ".join(meta.get("seller", "").split())
    return (meta.get("day", ""), seller, total), meta.get("number", ""), bool(meta.get("credit"))


def open_evidence(path):
    if Path(path).suffix.lower() not in IMAGE_EXTENSIONS:
        return pymupdf.open(path)
    with pymupdf.open(path) as image:
        return pymupdf.open("pdf", image.convert_to_pdf())


def read_pdf_pages(path):
    if Path(path).suffix.lower() == ".xml":
        return
    with open_evidence(path) as document:
        for page in document:
            text = page.get_text()
            sorted_text = page.get_text(sort=True)
            note = ""
            if len(re.findall(r"\w", text)) < 30:
                folder, language = tessdata_folder()
                if not folder:
                    note = "스캔·사진 · OCR 언어 데이터 필요"
                else:
                    try:
                        textpage = page.get_textpage_ocr(language=language, dpi=250, full=True, tessdata=folder)
                        recognized = page.get_text(sort=True, textpage=textpage)
                        if recognized.strip():
                            text = page.get_text(textpage=textpage)
                            sorted_text = recognized
                    except (RuntimeError, ValueError):
                        note = "OCR 실패 · 직접 확인"
            yield text, sorted_text, note


def text_layer(path):
    """Text of all pages without OCR (fast), '' when unreadable or XML."""
    if Path(path).suffix.lower() == ".xml":
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
    try:
        with open_evidence(path) as document:
            return "\n".join(page.get_text() for page in document)
    except (OSError, RuntimeError, ValueError):
        return ""


INVOICE_NUMBER = (r"(?im)\b(?:Rechnungsnummer|Rechnungs-Nummer|Rechnungs-?Nr\.?|Rechnung\s+Nr\.?|Beleg-?Nr\.?|Invoice\s+(?:No\.?|Number|#)|인보이스\s*번호)"
                  r"\s*[:#]?[ \t]*(?:\n[ \t]*)?([\w/-]*\d[\w/-]*)\b")


def invoice_parts(page_texts):
    """Page ranges [(first, last), ...] when one PDF holds several invoices (each starting with ‘Seite 1 von N’
    and carrying its own invoice number), else []. Amazon puts every marketplace seller of an order in one file."""
    starts = [index for index, text in enumerate(page_texts)
              if re.search(r"(?i)\b(?:Seite|Page)\s+1\s+(?:von|of)\s+\d+\b", text)]
    if len(starts) < 2 or starts[0] != 0:
        return []
    ranges = [(start, end - 1) for start, end in zip(starts, starts[1:] + [len(page_texts)])]
    numbers = [unique(re.findall(INVOICE_NUMBER, "\n".join(page_texts[first:last + 1]))) for first, last in ranges]
    return ranges if all(numbers) and len(set(numbers)) == len(numbers) else []


def split_pdf(path, ranges):
    """Write each invoice of a combined PDF to its own file next to it; the original goes to the 제외 folder.
    Returns the new paths."""
    path = Path(path)
    created = []
    try:
        with open_evidence(path) as document:
            for index, (first, last) in enumerate(ranges, 1):
                target = free_name(path.parent, f"{path.stem}_{index}{path.suffix}")
                with pymupdf.open() as part:
                    part.insert_pdf(document, from_page=first, to_page=last)
                    part.save(target)
                created.append(target)
        keep = path.parent / EXCLUDED_FOLDER
        keep.mkdir(exist_ok=True)
        move_evidence(path, free_name(keep, f"{path.stem}__나누기전_원본{path.suffix}"))
    except (OSError, RuntimeError, ValueError):
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return created


def date_from_filename(path):
    """Issue date taken from a leading 20260702 / 2026-07-02 in the file name, or ''."""
    match = re.match(r"(20\d{2})-?(0[1-9]|1[0-2])-?(0[1-9]|[12]\d|3[01])(?=[_\- .])", Path(path).name)
    if not match:
        return ""
    value = "-".join(match.groups())
    return value if normalized_date(value) == value else ""


def read_invoice_details(path, info=None):
    """info (optional dict) receives 'invoice_like': False when the content does not look like an invoice."""
    info = info if info is not None else {}
    info["invoice_like"] = True
    structured = structured_fields(Path(path))
    if structured and (all(structured[0]) or Path(path).suffix.lower() == ".xml"):
        fields, number, correction = structured
        override = load_invoice_edits().get(digest(path))
        if override:
            fields = tuple(override[:3])
            if len(override) > 3:
                number = override[3]
        return fields, "", number, correction
    if Path(path).suffix.lower() == ".xml":
        return ("", "", ""), "XML 인보이스 형식 확인", "", False
    pages = list(read_pdf_pages(path))
    layouts = ["\n".join(page[index] for page in pages) for index in (0, 1)]
    scan_note = next((note for _, _, note in pages if note), "")
    parts = invoice_parts([page[0] for page in pages])
    if parts:
        info["parts"] = parts
        scan_note = f"인보이스 {len(parts)}개가 한 파일에 있음 · 더블클릭해서 나누기"
    fields = invoice_fields(layouts)
    text = layouts[0]
    if len(re.findall(r"\w", text)) >= 30 and not scan_note:
        score = invoice_likeness(text) + (2 if fields[2] else 0)
        info["invoice_like"] = score >= 3
    numbers = []
    correction = False
    for layout in layouts:
        numbers.extend(re.findall(INVOICE_NUMBER, layout))
        correction |= bool(re.search(r"(?im)^\s*(?:Gutschrift|Stornorechnung|Credit Note)\b", layout))
    override = load_invoice_edits().get(digest(path))
    number = unique(numbers)
    checks = {}  # field -> why the value needs a look (shown as ‘확인 필요’)
    if len(set(numbers)) > 1:
        checks["인보이스 번호"] = f"번호 후보 {len(set(numbers))}개 중 고르지 못함"
    before = fields, number
    fields, number, learned = learned_formats.apply(layouts[1] + "\n" + layouts[0], fields, number)
    if learned:
        info["learned"] = learned
        info["invoice_like"] = True
        for index, name in enumerate(("발행일", "업체명", "총액")):
            if fields[index] and fields[index] != before[0][index]:
                checks[name] = f"배운 양식 ‘{learned}’로 읽음"
        if number and number != before[1]:
            checks["인보이스 번호"] = f"배운 양식 ‘{learned}’로 읽음"
    if not fields[0]:
        fields = (date_from_filename(path), *fields[1:])
        if fields[0]:
            checks["발행일"] = "파일명에서 가져옴"
    if not number:
        number = number_from_filename(path, text)
        if number:
            checks["인보이스 번호"] = "파일명에서 가져옴"
    if override:
        fields, scan_note = tuple(override[:3]), ""
        info["invoice_like"] = True
        if len(override) > 3:
            number = override[3]
        checks = {}
    info["checks"] = checks
    return fields, scan_note, number, correction


def number_from_filename(path, text):
    """An invoice number in the original file name that is also printed in the document (e.g. ‘RE_612088028.pdf’).

    Only numbers with at least 5 characters and a digit that appear in the text count; one printed right after an
    invoice-number label wins. Dates in the name (2026-07-31, 20260731) are skipped."""
    stem = Path(path).stem
    found = []
    for token in re.findall(r"[A-Za-z]*\d[\w/-]*", stem):
        token = token.strip("_-")
        for part in dict.fromkeys((token, *re.split(r"[_]", token))):
            if len(part) < 5 or not re.search(r"\d", part) or re.fullmatch(r"20\d{2}-?\d{2}-?\d{2}", part):
                continue
            if re.search(rf"(?<![\w]){re.escape(part)}(?![\w])", text):
                found.append(part)
    if not found:
        return ""
    for part in found:
        if re.search(rf"(?i)(?:nummer|nr\.?|no\.?|number|#)\s*[:#]?\s*{re.escape(part)}", text):
            return part
    found = list(dict.fromkeys(found))
    return found[0] if len(found) == 1 else ""


def read_invoice(path):
    fields, note, _, _ = read_invoice_details(path)
    return fields, note


def source_search_terms(value, kind):
    if kind == "발행일":
        day = date.fromisoformat(value)
        return tuple(dict.fromkeys((value, day.strftime("%d.%m.%Y"), day.strftime("%d.%m.%y"), day.strftime("%d/%m/%Y"),
                                    *sorted(learned_formats.date_spellings(value), key=len, reverse=True),
                                    f"{day.strftime('%d')} {learned_formats.GERMAN_MONTHS[day.month - 1]} {day.year}")))
    if kind == "총액":
        raw = value.split()[0]
        amount = Decimal(raw)
        dot = format(amount, ".2f")
        comma = dot.replace(".", ",")
        grouped = format(amount, ",.2f")
        return tuple(dict.fromkeys((grouped.translate(str.maketrans(",.", ".,")), grouped, comma, dot, raw)))
    return (value,)


CURRENCY_CHOICES = ("EUR", "USD", "GBP", "CHF", "KRW")


def money_text(raw, currency="EUR"):
    """‘7,87’ / ‘7.87’ / ‘1.234,56’ / ‘7,87 EUR’ → ‘7.87 EUR’ (currency from the text, else the chosen one); '' if not a number."""
    raw = " ".join(str(raw).replace("\u00a0", " ").split())
    if not raw:
        return ""
    match = re.fullmatch(MONEY, raw, re.IGNORECASE)
    if not match and re.fullmatch(r"-?[\d.,'’ ]+", raw):
        number = re.sub(r"([.,])(\d)$", r"\g<1>\g<2>0", raw)  # 7,8 → 7,80
        match = re.fullmatch(MONEY, f"{number} {currency}", re.IGNORECASE)
    return normalized_money(match) if match else ""


class MoneyEntry(ttk.Frame):
    """Amount box (numbers only, 7,87 or 7.87) with a currency choice that starts at EUR."""

    def __init__(self, parent, value="", width=22):
        super().__init__(parent)
        self.entry = ttk.Entry(self, width=width)
        self.entry.pack(side="left")
        self.currency = ttk.Combobox(self, values=CURRENCY_CHOICES, width=6)
        self.currency.pack(side="left", padx=(6, 0))
        self.set(value)

    def set(self, value):
        text = money_text(value) if value else ""
        number, _, code = text.partition(" ")
        self.entry.delete(0, "end")
        self.entry.insert(0, number.replace(".", ",") if number else str(value or "").strip())
        self.currency.set(code or "EUR")

    def get(self):
        """‘7.87 EUR’ or '' when the box does not hold a number."""
        return money_text(self.entry.get(), self.currency.get().strip().upper() or "EUR")

    def focus_set(self):
        self.entry.focus_set()


def pick_value(kind, text):
    """The value of one field inside text picked from the PDF with the mouse."""
    text = " ".join(text.split())
    if kind == "발행일":
        for token in re.findall(rf"{DATE}|{LONG_DATE}", text):
            value = normalized_date(token) or learned_formats.parse_date(token)
            if value:
                return value
        return ""
    if kind == "총액":
        match = None
        for match in re.finditer(MONEY, text, re.IGNORECASE):
            pass
        if match:
            return normalized_money(match)
        numbers = re.findall(r"-?\d{1,3}(?:[.,']\d{3})*[.,]\d{2}|-?\d+[.,]\d{2}", text)
        return money_text(numbers[-1]) if numbers else ""
    return text


def source_locations(path, value, kind):
    if Path(path).suffix.lower() == ".xml":
        return []
    terms = source_search_terms(value, kind)
    hits = []
    with open_evidence(path) as doc:
        for index, page in enumerate(doc):
            textpage = page.get_textpage()
            if len(re.findall(r"\w", page.get_text(textpage=textpage))) < 30:
                folder, languages = tessdata_folder()
                if folder:
                    try:
                        textpage = page.get_textpage_ocr(language=languages, dpi=200, full=True, tessdata=folder)
                    except (OSError, RuntimeError, ValueError):
                        pass
            seen = set()
            words = page.get_text("words", textpage=textpage)
            for term in terms:
                for box in page.search_for(term, textpage=textpage):
                    if kind == "총액" and any(
                        pymupdf.Rect(*word[:4]).contains(box) and word[4][0].isdigit()
                        and re.sub(r"[^\d]", "", term) != re.sub(r"[^\d]", "", word[4])
                        for word in words
                    ):
                        continue
                    coords = tuple(box)
                    if coords not in seen:
                        seen.add(coords)
                        hits.append((index, coords, term))
    return hits


def source_preview(path, index, coordinates):
    with open_evidence(path) as doc:
        page = doc[index]
        if coordinates is None:  # whole page, small enough for the panel
            scale = min(750 / max(1, page.rect.width), 620 / max(1, page.rect.height))
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
        area = pymupdf.Rect(coordinates)
        clip = pymupdf.Rect(page.rect.x0, area.y0 - 65, page.rect.x1, area.y1 + 65) & page.rect
        page.draw_rect(area + (-2, -2, 2, 2), color=(0.9, 0.38, 0.05), width=1.8, overlay=True)
        scale = min(2.0, 750 / max(1, clip.width))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False).tobytes("png")


def open_file(path):
    """Open a file in the default program (where its text can be selected and copied)."""
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as error:
        messagebox.showerror("열기 실패", f"{Path(path).name}: {error}")


def make_copyable(text):
    """Read-only Text that still lets the user select and copy (Ctrl+C, Ctrl+A, right-click)."""
    def key(event):
        if event.state & 0x4 and event.keysym.lower() in ("c", "a"):
            if event.keysym.lower() == "a":
                text.tag_add("sel", "1.0", "end")
                return "break"
            return None
        if event.keysym in ("Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End"):
            return None
        return "break"

    def copy(all_text=False):
        try:
            value = text.get("1.0", "end-1c") if all_text else text.get("sel.first", "sel.last")
        except tk.TclError:
            value = text.get("1.0", "end-1c")
        text.clipboard_clear()
        text.clipboard_append(value)

    menu = tk.Menu(text, tearoff=False)
    menu.add_command(label="복사", command=copy)
    menu.add_command(label="전체 복사", command=lambda: copy(True))
    text.bind("<Key>", key)
    text.bind("<<Paste>>", lambda event: "break")
    text.bind("<<Cut>>", lambda event: "break")
    text.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))


FIELD_NAMES = ("발행일", "업체명", "총액", "인보이스 번호")
REQUIRED_FIELDS = ("발행일", "업체명", "총액")
LEARN_KEYS = {"발행일": "date", "총액": "total", "인보이스 번호": "number"}


def field_from_label(label, text):
    """The field a picked value probably belongs to, from the words next to it (‘Rechnungs-Nummer’ → 인보이스 번호).

    '' when the words do not clearly name one, or the picked text does not look like that field (no date, no amount)."""
    if not label:
        return ""
    scores = {name: learned_formats.label_score(label, key) for name, key in LEARN_KEYS.items()}
    best = max(scores, key=scores.get)  # ties: 발행일, 총액 before 번호 (‘Rechnungsdatum’ also holds ‘rechnung’)
    if scores[best] < 2:
        return ""
    if best in ("발행일", "총액") and not pick_value(best, text):
        return ""
    return best


def value_problems(path, values):
    """{field: why} for values that are not printed in the PDF text layer (fast check without OCR)."""
    text = " ".join(text_layer(path).split()).casefold()
    if len(re.findall(r"\w", text)) < 30:
        return {}
    problems = {}
    for name, kind in (("발행일", "발행일"), ("총액", "총액"), ("인보이스 번호", "인보이스 번호")):
        value = (values.get(name) or "").strip()
        if not value:
            continue
        try:
            terms = source_search_terms(value, kind)
        except (ValueError, ArithmeticError):
            problems[name] = "값의 형식을 알아보지 못함"
            continue
        if not any(" ".join(term.split()).casefold() in text for term in terms if term):
            problems[name] = "원본에서 이 값을 찾지 못함"
    seller = (values.get("업체명") or "").strip()
    first = next((word for word in re.findall(r"\w{3,}", seller)), "")
    if first and first.casefold() not in text:
        problems["업체명"] = "원본에 같은 이름이 없음 (파일명·배운 이름일 수 있음)"
    return problems


def show_source_locations(parent, path, fields, on_value=None, start=None, on_save=None, anchors=None, status=None):
    """Where a value was read in the original, with page buttons and zoom.

    on_value(kind, value): when given, a box to type or pick (drag a box on the page, or right-click a word) the value
    of the chosen field; ‘편집 칸에 넣기’ hands it back to the edit form and moves on to the next missing field.
    start: the field shown first (default: the first missing one). on_save: adds ‘저장하고 닫기’ (saves the edit form).
    anchors: dict that receives {"date"|"total"|"number": words next to the picked box} for learning the layout.
    status(field) -> short state such as ‘누락’, ‘확인 필요’, ‘확인함’ shown in the field list."""
    from pdf_view import PdfView

    window = InlineDialog(parent)
    window.title("추출값 원본 위치" + (" · 값 넣기" if on_value else ""))
    window.geometry("1000x720")
    window.transient(parent)
    window.grab_set()

    def state(name):
        if status:
            return status(name)
        return "" if (fields.get(name) or "").strip() else "누락"

    def missing(name):
        return not (fields.get(name) or "").strip()

    names = list(fields)
    if on_value:  # missing fields first, so the list doubles as a to-do list
        order = list(names)
        names.sort(key=lambda name: (not missing(name), name not in REQUIRED_FIELDS, order.index(name)))
    controls = ttk.Frame(window, padding=(10, 8))
    controls.pack(fill="x")
    choice = ttk.Combobox(controls, state="readonly", width=22)
    choice.pack(side="left")

    def labels():
        choice.configure(values=[f"{name} · {state(name)}" if state(name) else name for name in names])

    chosen = [names[0]]  # the field being filled (the list text changes with its state, so keep the name here)

    def kind_now():
        return chosen[0]

    def select(name):
        chosen[0] = name
        labels()
        choice.current(names.index(name))

    def listed(_event=None):
        index = choice.current()
        if 0 <= index < len(names):
            select(names[index])
        refresh()

    first = start if start in names else next((name for name in names if missing(name)), names[0]) if on_value else (
        start if start in names else names[0])
    select(first)
    display = tk.StringVar()
    hits = []
    position = 0
    inputs = ttk.Frame(window, padding=(10, 0, 10, 6))
    value_box = ttk.Entry(inputs, width=40)
    money_box = MoneyEntry(inputs)
    picked = tk.StringVar()
    last_pick = {}  # field -> (value put in the box, words next to it)

    def box_value(kind):
        return money_box.get() if kind == "총액" else value_box.get().strip()

    def fill_box(kind, value):
        if kind == "총액":
            money_box.set(value)
        else:
            value_box.delete(0, "end")
            value_box.insert(0, value)

    def picked_text(page, area, text):
        if not text:
            picked.set("상자 안에서 글자를 찾지 못했어요. 스캔 문서면 직접 적어 주세요.")
            return
        kind = kind_now()
        label = PdfView.label_near(view.document[page], area) if view.document is not None else ""
        note = ""
        guess = field_from_label(label, text)
        if on_value and guess and guess != kind:
            fits = bool(pick_value(kind, text)) if kind in ("발행일", "총액") else True
            if kind == "업체명":
                note = f" · 옆 글자 ‘{label}’를 보면 ‘{guess}’ 같아요. 맞으면 위 목록에서 바꾸세요."
            elif not fits or learned_formats.label_score(label, LEARN_KEYS[kind]) <= 0:
                note = f" · 옆 글자 ‘{label}’를 보고 항목을 ‘{kind}’에서 ‘{guess}’(으)로 바꿨어요."
                kind = guess
                select(kind)
                show_input()
        value = pick_value(kind, text)
        picked.set(f"고른 글자: {text[:80]}")
        if not on_value:
            return
        value = value or text
        fill_box(kind, value)
        last_pick[kind] = (box_value(kind), label)
        if kind in ("발행일", "총액") and not pick_value(kind, text):
            display.set(f"고른 글자에서 {kind}을(를) 알아보지 못했어요. 칸의 값을 고쳐 적거나 다시 골라 주세요." + note)
        else:
            display.set(f"‘{kind}’ 칸에 넣을 값: {box_value(kind) or value}"
                        + (f" (옆 글자 ‘{label}’)" if label else "") + " · ‘편집 칸에 넣기’를 누르세요." + note)

    def word_menu(event, page, word, rect, line):
        menu = tk.Menu(window, tearoff=False)
        menu.add_command(label=f"고른 글자: {word[:40]}", state="disabled")
        if on_value:
            put_menu = tk.Menu(menu, tearoff=False)
            label = PdfView.label_near(view.document[page], rect)
            for name in names:
                if name in ("발행일", "총액"):
                    value = pick_value(name, line) or pick_value(name, word)
                elif name == "업체명":
                    value = line
                else:
                    value = word.strip(":#")
                if not value:
                    put_menu.add_command(label=f"{name}: (알아보지 못함)", state="disabled")
                    continue

                def put(name=name, value=value):
                    select(name)
                    show_input()
                    fill_box(name, value)
                    last_pick[name] = (box_value(name), label)
                    hand_over()

                put_menu.add_command(label=f"{name}: {value[:40]}", command=put)
            menu.add_cascade(label="이 값으로 넣기", menu=put_menu)
        menu.add_separator()
        for text, value in (("글자 복사", word), ("줄 전체 복사", line)):
            menu.add_command(label=text, command=lambda value=value: (window.clipboard_clear(), window.clipboard_append(value)))
        menu.tk_popup(event.x_root, event.y_root)

    view = PdfView(window, on_pick=picked_text, on_menu=word_menu)

    def show_input():
        if not on_value:
            return
        kind = kind_now()
        value_box.pack_forget()
        money_box.pack_forget()
        fill_box(kind, fields.get(kind, ""))
        (money_box if kind == "총액" else value_box).pack(side="left", after=input_label)
        input_label.configure(text=kind)

    def hand_over(advance=True):
        kind = kind_now()
        value = box_value(kind)
        if kind == "총액" and not value:
            show_notice(window, "총액은 숫자로 적어 주세요. 예: 7,87", kind="warn")
            return False
        if kind == "발행일" and value and not normalized_date(value):
            value = pick_value(kind, value)
            if not value:
                show_notice(window, "발행일은 2026-07-31 또는 31.07.2026처럼 적어 주세요.", kind="warn")
                return False
            fill_box(kind, value)
        fields[kind] = value
        on_value(kind, value)
        pick = last_pick.pop(kind, None)
        if anchors is not None and kind in LEARN_KEYS and pick and pick[0] == value and pick[1]:
            anchors[LEARN_KEYS[kind]] = pick[1]
        message = f"‘{kind}’ 칸에 넣었어요: {value}"
        following = next((name for name in names if missing(name) and name in REQUIRED_FIELDS), "") or next(
            (name for name in names if missing(name)), "")
        if advance and following:
            select(following)
            refresh()
            picked.set(message + f" · 다음 빈 칸: {following}")
        else:
            select(kind)
            picked.set(message + (" · 필수 항목이 모두 찼어요." + (" ‘저장하고 닫기’를 누르세요." if on_save else "")
                                  if not any(missing(name) for name in REQUIRED_FIELDS if name in names) else ""))
        return True

    def save_and_close():
        kind = kind_now()
        value = box_value(kind)
        if value and value != (fields.get(kind) or "") and not hand_over(advance=False):
            return
        empty = [name for name in REQUIRED_FIELDS if name in names and missing(name)]
        if empty:
            select(empty[0])
            refresh()
            show_notice(window, f"아직 비어 있어요: {', '.join(empty)}. 원본에서 골라 넣은 뒤 저장하세요.", kind="warn")
            return
        close()
        on_save()

    if on_value:
        inputs.pack(fill="x")
        input_label = ttk.Label(inputs, text="값")
        input_label.pack(side="left", padx=(0, 8))
        ttk.Button(inputs, text="편집 칸에 넣기", style="Primary.TButton", command=hand_over).pack(side="left", padx=8)
        if on_save:
            ttk.Button(inputs, text="저장하고 닫기", command=save_and_close).pack(side="left")
        value_box.bind("<Return>", lambda event: (hand_over(), "break")[1])
        money_box.entry.bind("<Return>", lambda event: (hand_over(), "break")[1])
        ttk.Label(window, textvariable=picked, foreground=COLORS["muted"], padding=(10, 0)).pack(anchor="w")
    else:
        ttk.Label(window, textvariable=picked, foreground=COLORS["muted"], padding=(10, 0)).pack(anchor="w")
    ttk.Label(window, textvariable=display, padding=(10, 0, 10, 4)).pack(anchor="w")
    view.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def all_boxes():
        boxes = {}
        for page, box, _ in hits:
            boxes.setdefault(page, []).append(pymupdf.Rect(box))
        return boxes

    def show(delta=0):
        nonlocal position
        kind = kind_now()
        if not hits:
            if on_value and missing(kind):
                display.set(f"‘{kind}’ 값이 아직 없어요. 쪽을 넘겨 보며 값을 마우스로 끌어 고르거나, 글자를 오른쪽 클릭하거나, 직접 적으세요.")
            else:
                display.set("원본에서 이 값을 찾지 못했어요. 쪽을 넘겨 보며 값을 마우스로 끌어 고르거나 직접 적으세요."
                            if on_value else "원본에서 같은 표기를 찾지 못했어요. 쪽을 넘겨 확인하세요.")
            if view.path != path:
                view.load(path, 0)
            else:
                view.show(view.page, boxes={})
            return
        position = (position + delta) % len(hits)
        page, box, matched = hits[position]
        display.set(f"{page + 1}쪽 · 표기 {matched} · 찾은 곳 {position + 1}/{len(hits)}")
        if view.path != path:
            view.load(path, page, all_boxes(), pymupdf.Rect(box))
        else:
            view.show(page, pymupdf.Rect(box), all_boxes())

    def refresh(_event=None):
        nonlocal hits, position
        kind = kind_now()
        value = fields.get(kind)
        position = 0
        show_input()
        try:
            hits = source_locations(path, value, kind) if value else []
        except (OSError, ValueError, RuntimeError, ArithmeticError) as error:
            hits = []
            display.set(f"원본 위치 검색 실패 · {error}")
        show()

    for text, callback in (("이전 위치", lambda: show(-1)), ("다음 위치", lambda: show(1)), ("PDF 열기", lambda: open_file(path))):
        ttk.Button(controls, text=text, command=callback).pack(side="left", padx=4)
    ttk.Label(controls, text="끌어서 고르기 · 글자 오른쪽 클릭 = 넣기 메뉴" if on_value else "같은 값이 여러 곳에 있으면 모두 주황 상자로 보여요.",
              foreground=COLORS["muted"]).pack(side="left", padx=8)

    def close():
        window.destroy()
        if isinstance(parent, tk.Toplevel):
            parent.grab_set()
        elif isinstance(parent, InlineDialog) and parent.winfo_exists():
            parent.after_idle(parent.grab_set)

    window.protocol("WM_DELETE_WINDOW", close)
    choice.bind("<<ComboboxSelected>>", listed)
    refresh()
    return window


class ValueForm:
    """The four value rows of an edit form: box, state (누락 / 확인 필요 / 확인함) and a ‘고르기’ button each.

    ‘고르기’ (or a double-click on an empty box) opens the original on that field; picked values come back here.
    checks: {field: why it needs a look} from reading. anchors: learned-label hints collected while picking."""

    LABELS = ("발행일 (YYYY-MM-DD)", "업체명", "총액 (숫자만, 예: 7,87)", "인보이스 번호")

    def __init__(self, body, path, values, checks=None, first_row=1, on_save=None, number_label="인보이스 번호 (선택)"):
        self.body, self.path, self.on_save = body, Path(path), on_save
        self.checked = set()
        self.anchors = {}
        self.checks = dict(checks or {})
        self.entries = []
        self.states = []
        pickable = self.path.suffix.lower() != ".xml"
        for offset, (label, value) in enumerate(zip((*self.LABELS[:3], number_label), values)):
            row = first_row + offset
            name = FIELD_NAMES[offset]
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            entry = MoneyEntry(body, value, width=30) if offset == 2 else ttk.Entry(body, width=40)
            if offset != 2:
                entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="w")
            box = entry.entry if offset == 2 else entry
            box.bind("<KeyRelease>", lambda event, name=name: self.typed(name), add="+")
            if pickable:
                box.bind("<Double-Button-1>", lambda event, name=name: self.pick(name) if not self.value(name) else None,
                         add="+")
            mark = ttk.Label(body, text="", font=FONT_BOLD, width=11)
            mark.grid(row=row, column=2, sticky="w", padx=(8, 4))
            Tooltip(mark, lambda name=name: self.reason(name))
            if pickable:
                ttk.Button(body, text="고르기", style="Small.TButton", command=lambda name=name: self.pick(name)).grid(
                    row=row, column=3, sticky="w")
            self.entries.append(entry)
            self.states.append(mark)
        if pickable:
            try:
                for name, why in value_problems(self.path, self.values()).items():
                    self.checks.setdefault(name, why)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("value_check", error)
        self.update()

    def value(self, name):
        entry = self.entries[FIELD_NAMES.index(name)]
        if name == "총액":
            return entry.get() or entry.entry.get().strip()
        return entry.get().strip()

    def values(self):
        return {name: self.value(name) for name in FIELD_NAMES}

    def raw(self):
        """(발행일, 업체명, 총액, 번호) as typed; 총액 as ‘7.87 EUR’ or '' when not a number."""
        return tuple(entry.get().strip() for entry in self.entries)

    def state(self, name):
        if not self.value(name):
            return "비어 있음" if name == "인보이스 번호" else "누락"
        if name in self.checked:
            return "확인함"
        if name in self.checks:
            return "확인 필요"
        return ""

    def reason(self, name):
        state = self.state(name)
        if state == "누락":
            return "필수 항목이 비어 있어요. ‘고르기’를 누르거나 빈 칸을 더블클릭해 원본에서 골라요."
        if state == "비어 있음":
            return "번호가 없어요. 250€ 넘는 인보이스는 번호가 필요해요."
        if state == "확인 필요":
            return f"확인 필요: {self.checks[name]}. 맞으면 그대로 두고, 아니면 ‘고르기’로 원본에서 골라요."
        if state == "확인함":
            return "직접 고르거나 고친 값이에요."
        return "자동으로 읽은 값이에요."

    def update(self):
        colours = {"누락": COLORS["red"], "비어 있음": COLORS["muted"], "확인 필요": COLORS["amber"], "확인함": COLORS["green"]}
        for name, mark in zip(FIELD_NAMES, self.states):
            state = self.state(name)
            mark.configure(text=("✓ " if state == "확인함" else "● " if state else "") + state,
                           foreground=colours.get(state, COLORS["muted"]))

    def typed(self, name):
        self.checked.add(name)
        self.update()

    def put(self, name, value):
        entry = self.entries[FIELD_NAMES.index(name)]
        if name == "총액":
            entry.set(value)
        else:
            entry.delete(0, "end")
            entry.insert(0, value)
        self.checked.add(name)
        self.update()

    def pick(self, start=None):
        """Open the original on one field (default: the first missing one)."""
        return show_source_locations(self.dialog(), self.path, self.values(),
                                     on_value=self.put, start=start, on_save=self.on_save, anchors=self.anchors,
                                     status=self.state)

    def dialog(self):
        widget = self.body
        while widget is not None and not isinstance(widget, (InlineDialog, tk.Toplevel)):
            widget = widget.master
        return widget or self.body.winfo_toplevel()

    def first_missing(self):
        return next((name for name in FIELD_NAMES if self.state(name) == "누락"), None)


def markdown_value(value):
    return re.sub(r"([\\&<>\[\]*_~\x60])", r"\\\1", " ".join(value.split()))


def export_markdown(path, fields, number):
    content, incomplete = markdown_text(path, fields, number)
    return write_markdown(path, content), incomplete


def markdown_text(path, fields, number):
    """Markdown summary and page text of an evidence file: (content, incomplete)."""
    issued, seller, total = fields
    lines = [MARKDOWN_HEADING, "", "- PDF 파일명: " + markdown_value(path.name)]
    if path.suffix.lower() == ".xml":
        lines[2] = "- 원본 파일명: " + markdown_value(path.name)
        lines.append("- 원본 형식: XRechnung XML (전자 인보이스 원본, 페이지 본문 없음)")
    for label, value in (("업체명", seller), ("발행일", issued), ("총액", total), ("인보이스 번호", number)):
        if value:
            lines.append(f"- {label}: {markdown_value(value)}")
    incomplete = False
    for index, (_text, text, note) in enumerate(read_pdf_pages(path), 1):
        lines.extend(("", f"## {index}페이지", ""))
        if note or not text.strip():
            incomplete = True
            lines.append(f"> {note or '추출된 텍스트 없음'}")
            lines.append("")
        if text.strip():
            fence = "~" * max(3, 1 + max((len(run) for run in re.findall(r"~+", text)), default=0))
            lines.extend((fence, text.rstrip(), fence))
    return "\n".join(lines) + "\n", incomplete


def write_markdown(path, content):
    target = path.with_suffix(".md")
    if target.is_file() and not target.is_symlink() and generated_markdown(target):
        temporary = target.with_name(target.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.replace(temporary, target)  # refresh the program's own summary
        return target
    index = 2
    while True:
        try:
            output = target.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            target = path.with_name(f"{path.stem}_{index}.md")
            index += 1
            continue
        try:
            with output:
                output.write(content)
        except OSError:
            target.unlink(missing_ok=True)
            raise
        return target


# Names before v1.1.1: 2026-11-25_58.39 EUR_Seller__번호_R1__원본__invoice.pdf
# They are still recognised so that their original name is kept.
ORIGINAL_SEPARATOR = "__원본__"
DUPLICATE_SEPARATOR = "__중복_"
NUMBER_SEPARATOR = "__번호_"
# Names from v1.1.1 (same as the advisor ZIP):
#   58.39_26-11-25_greenist GmbH_Nr_R1044176_invoice.pdf
#   [611-001]_58.39_26-11-25_greenist GmbH_Nr_R1044176_invoice.pdf   (after matching)
NR_SEPARATOR = "_Nr_"
MONTH_FOLDER = r"\d{4}-(?:0[1-9]|1[0-2])"
FILENAME_DATE = r"\d{4}-\d{2}-\d{2}"
FILENAME_TOTAL = r"-?\d+(?:\.\d{2})? (?:[A-Z]{3}|\$)"
TRANSACTION_PREFIX = r"\[\d{3}-\d{3}\]_"
SHORT_TOTAL = r"-?\d+(?:\.\d{1,2})?(?: [A-Z]{3})?"
SHORT_DATE = r"\d{2}-\d{2}-\d{2}"
DUPLICATE_SUFFIX = r" \(\d+\)"
_DIGESTS = {}


def cached_digest(path):
    path = Path(path)
    try:
        info = path.stat()
    except OSError:
        return None
    key = (str(path), info.st_size, info.st_mtime_ns)
    if key not in _DIGESTS:
        try:
            _DIGESTS[key] = digest(path)
        except OSError:
            return None
    return _DIGESTS[key]


def original_names_path():
    return user_data_folder() / "original-names.json"


def load_original_names():
    try:
        data = json.loads(original_names_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember_original_name(path, checksum=None):
    """Keep the name a file had before the first rename, keyed by its content hash."""
    checksum = checksum or cached_digest(path)
    if not checksum:
        return
    names = load_original_names()
    if checksum in names:
        return
    names[checksum] = original_stem(path)
    storage = original_names_path()
    storage.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=storage.parent, mode="w", encoding="utf-8", delete=False) as output:
            temporary = Path(output.name)
            json.dump(names, output, ensure_ascii=False, indent=2)
        temporary.replace(storage)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def short_parts(stem):
    """Split a v1.1.1 name into (total, yy-mm-dd, rest) without the transaction prefix and duplicate number."""
    stem = re.sub(rf"^{TRANSACTION_PREFIX}", "", stem)
    stem = re.sub(rf"{DUPLICATE_SUFFIX}$", "", stem)
    match = re.fullmatch(rf"({SHORT_TOTAL})_({SHORT_DATE})_(.+)", stem)
    if not match:
        return None
    try:
        day = date(2000 + int(match[2][:2]), int(match[2][3:5]), int(match[2][6:])).isoformat()
    except ValueError:
        return None
    return match[1], day, match[3]


def split_rest(rest, original=None):
    """Return (seller, original) from 'Seller_Nr_123_original'."""
    if original and rest.endswith("_" + original) and len(rest) > len(original) + 1:
        head = rest[:-len(original) - 1]
        seller = head.split(NR_SEPARATOR, 1)[0] if NR_SEPARATOR in head else head
        return seller, original
    if NR_SEPARATOR in rest:
        seller, _, tail = rest.partition(NR_SEPARATOR)
        _, _, original = tail.partition("_")
        return seller, original or tail
    seller, _, original = rest.partition("_")
    return seller, original or seller


def filename_fields(source):
    stem = Path(source).stem
    prefix, separator, original = stem.partition(ORIGINAL_SEPARATOR)
    if separator and original:
        current = re.fullmatch(
            rf"({FILENAME_DATE})_({FILENAME_TOTAL})_(.+?)(?:{NUMBER_SEPARATOR}[^\s]+?)?(?:{DUPLICATE_SEPARATOR}\d+)?", prefix
        )
        if current and normalized_date(current[1]):
            return current[1], current[3], current[2]
    previous = re.fullmatch(rf"({FILENAME_TOTAL})_(.+?)_({FILENAME_DATE})(?:_.*)?", prefix)
    if previous and normalized_date(previous[3]):
        return previous[3], previous[2], previous[1]
    parts = short_parts(stem)
    if parts:
        total, day, rest = parts
        known = load_original_names().get(cached_digest(source)) if Path(source).is_file() else None
        seller, _ = split_rest(rest, known)
        total = total if " " in total else f"{total} EUR"
        amount, currency = total.split(" ", 1)
        if "." not in amount:
            amount += "" if currency == "KRW" else ".00"
        elif len(amount.split(".")[1]) == 1:
            amount += "0"
        return day, seller, f"{amount} {currency}"
    return None


def original_stem(source):
    stem = Path(source).stem
    _, separator, original = stem.partition(ORIGINAL_SEPARATOR)
    if separator and original and filename_fields(source):
        return original
    parts = short_parts(stem)
    if parts:
        known = load_original_names().get(cached_digest(source)) if Path(source).is_file() else None
        if known:
            return known
        return split_rest(parts[2])[1]
    return stem


def number_label(number, existing_name):
    if not number or re.search(rf"(?<![^\W_]){re.escape(number)}(?![^\W_])", existing_name, re.IGNORECASE):
        return ""
    safe = re.sub(r"[^\w.-]", "-", number).strip(" .-")[:40]
    return NUMBER_SEPARATOR + safe if safe else ""


def short_total(total):
    amount, _, currency = total.partition(" ")
    return amount if currency in ("", "EUR") else f"{amount} {currency}"


def evidence_stem(issued, seller, total, number, original, transaction_id=""):
    """58.39_26-11-25_Seller_Nr_123_original, optionally prefixed with [611-001]_."""
    clean = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .") for value in (seller, original)]
    seller, original = clean
    seller = seller.replace(NR_SEPARATOR, "_Nr-")
    reference = number_label(number, original).replace(NUMBER_SEPARATOR, NR_SEPARATOR)
    day = date.fromisoformat(issued)
    label = f"{short_total(total)}_{day:%y-%m-%d}_{seller}{reference}_{original}"
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", label).strip(" .")
    return (f"[{transaction_id}]_" if transaction_id else "") + label


def proposed_name(fields, source=None, number=""):
    if not all(fields):
        return ""
    parts = [re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .") for value in fields]
    if not all(parts):
        return ""
    issued, seller, total = parts
    try:
        date.fromisoformat(issued)
    except ValueError:
        return ""
    seller = seller[:90].rstrip(" .")
    if source is None:
        return evidence_stem(issued, seller, total, number, "invoice") + ".pdf"
    suffix = Path(source).suffix
    prefix = re.match(TRANSACTION_PREFIX, Path(source).name)
    stem = evidence_stem(issued, seller, total, number, original_stem(source),
                         prefix.group(0)[1:-2] if prefix else "")
    if len(stem.encode("utf-16-le")) // 2 + len(suffix) > 255:
        return ""
    return stem + suffix


def reserve_name(folder, name, source, occupied):
    candidate = name
    number = 2
    suffix = Path(name).suffix
    stem = Path(name).stem
    if source.parent == folder and re.fullmatch(
        rf"{re.escape(stem)}{DUPLICATE_SUFFIX}{re.escape(suffix)}", source.name, re.IGNORECASE
    ):
        return source
    while candidate.casefold() in occupied and not (
        source.parent == folder and candidate.casefold() == source.name.casefold()
    ):
        candidate = f"{stem} ({number}){suffix}"
        number += 1
    if len(candidate.encode("utf-16-le")) // 2 > 255:
        return None
    occupied.add(candidate.casefold())
    return folder / candidate


def invoice_identity(fields, number):
    """(seller, date, amount, number) used to spot the same invoice saved twice, or None if incomplete."""
    day, seller, amount = fields
    seller = re.sub(r"[\W_]+", "", seller.casefold())
    number = re.sub(r"\s+", "", (number or "").casefold())
    if not (day and seller and amount and number):
        return None
    return seller, day, amount.replace(",", ".").upper(), number


def duplicate_groups(entries):
    """Group files that are the same invoice.

    entries: iterable of (key, path, fields, number, checksum). Two files belong together when their
    content is identical, or when seller, issue date, total and invoice number all match.
    Returns a list of groups; each group is a list of entries, the file to keep first."""
    parent = {}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    entries = list(entries)
    seen = {}
    for entry in entries:
        key, _, fields, number, checksum = entry
        parent[key] = key
        for token in (("hash", checksum) if checksum else None, ("fields", invoice_identity(fields, number))):
            if token is None or token[1] is None:
                continue
            if token in seen:
                parent[find(key)] = find(seen[token])
            else:
                seen[token] = key
    groups = {}
    for entry in entries:
        groups.setdefault(find(entry[0]), []).append(entry)

    def keep_first(entry):
        path = entry[1]
        matched = path.name.startswith("[") or path.parent.name.startswith("매칭완료_")
        try:
            received = path.stat().st_mtime_ns  # the copy that arrived first is kept
        except OSError:
            received = 0
        return (not matched, received, len(path.parts), path.name.casefold())

    return [sorted(group, key=keep_first) for group in groups.values() if len(group) > 1]


MARKDOWN_HEADING = "# 인보이스"


def generated_markdown(path):
    """True when path is a Markdown summary written by this program."""
    try:
        with path.open(encoding="utf-8") as stream:
            return stream.readline().strip() == MARKDOWN_HEADING
    except (OSError, UnicodeDecodeError):
        return False


def move_sidecar(source, target):
    """Move the program's Markdown summary along with its evidence file and update the file name line.

    Returns the new Markdown path, or None when there was no summary or it could not be moved."""
    old, new = source.with_suffix(".md"), target.with_suffix(".md")
    if old == new or not old.is_file() or old.is_symlink() or not generated_markdown(old) or new.exists():
        return None
    try:
        text = old.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^- (PDF|원본) 파일명: .*$", lambda match: f"- {match[1]} 파일명: " + markdown_value(target.name), text, count=1)
        with new.open("x", encoding="utf-8", newline="\n") as output:
            output.write(text)
        old.unlink()
    except OSError as error:
        record_error("markdown_move", error)
        new.unlink(missing_ok=True) if new.exists() and old.exists() else None
        return None
    return new


def move_evidence(source, target):
    """Move an evidence file without overwriting, taking its Markdown summary with it."""
    move_without_overwrite(source, target)
    move_sidecar(source, target)


def move_without_overwrite(source, target):
    if os.name == "nt":
        source.rename(target)
    else:
        os.link(source, target)
        try:
            source.unlink()
        except OSError:
            target.unlink()
            raise


def settings_path():
    return user_data_folder() / "settings.json"


def load_settings():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(changes):
    data = {**load_settings(), **changes}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def default_work_folder():
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "월마감"


def work_folder():
    """The fixed folder where invoices and statements are kept (v1.7)."""
    saved = load_settings().get("work_folder")
    return Path(saved) if isinstance(saved, str) and saved.strip() else default_work_folder()


def import_mode():
    """‘copy’ (default: originals stay where they are) or ‘move’."""
    return "move" if load_settings().get("import_mode") == "move" else "copy"


def free_name(folder, name, taken=()):
    """name, or ‘name (2).pdf’ etc. when that name is already used in folder."""
    candidate = Path(name)
    index = 2
    while (folder / candidate.name).exists() or (folder / candidate.with_suffix(".md").name).exists() \
            or candidate.name.casefold() in taken:
        candidate = Path(f"{Path(name).stem} ({index}){Path(name).suffix}")
        index += 1
    return folder / candidate.name


def move_across(source, target):
    """move_evidence that also works between drives (copy, check, then delete the original)."""
    try:
        move_evidence(source, target)
        return
    except OSError:
        if target.exists() or not source.is_file():
            raise
    checksum = digest(source)
    shutil.copy2(source, target)
    try:
        if digest(target) != checksum:
            raise OSError("복사한 파일 내용이 원본과 달라요.")
        source.unlink()
    except OSError:
        target.unlink(missing_ok=True)
        raise
    move_sidecar(source, target)


def import_evidence(sources, folder, copy=False, progress=None):
    """Move (or copy) picked evidence files into the work folder.
    Returns (history: (original, new, checksum), already there, errors). Undo applies to moves only."""
    folder.mkdir(parents=True, exist_ok=True)
    history, inside, errors, taken = [], 0, [], set()
    root = folder.resolve()
    sources = list(sources)
    for position, source in enumerate(sources, 1):
        source = Path(source)
        if progress:
            progress(position, len(sources), source.name)
        try:
            if source.is_symlink() or not source.is_file() or source.suffix.lower() not in RENAMER_EXTENSIONS:
                continue
            if source.resolve().is_relative_to(root):
                inside += 1
                continue
            target = free_name(folder, source.name, taken)
            taken.add(target.name.casefold())
            checksum = digest(source)
            if copy:
                shutil.copy2(source, target)
                if digest(target) != checksum:
                    target.unlink(missing_ok=True)
                    raise OSError("복사한 파일 내용이 원본과 달라요.")
            else:
                move_across(source, target)
        except OSError as error:
            errors.append(f"{source.name}: {error}")
            continue
        history.append((source, target, checksum))
    return history, inside, errors


def evidence_in(folder):
    """Evidence files in a folder and its subfolders, leaving out folders the program keeps apart."""
    skip = {EXCLUDED_FOLDER, STATEMENT_FOLDER, DUPLICATE_FOLDER}
    return sorted(path for path in Path(folder).rglob("*")
                  if path.suffix.lower() in RENAMER_EXTENSIONS and path.is_file() and not path.is_symlink()
                  and not skip & set(path.relative_to(folder).parts[:-1]))


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def invoice_edits_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InvoiceRenamer" / "invoice-edits.json"


class EditsFileError(ValueError):
    pass


def load_invoice_edits():
    path = invoice_edits_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise EditsFileError(f"수정값 파일이 손상됐어요. 백업에서 복원하거나 파일을 확인하세요: {path} ({error})") from error


def save_invoice_edit(path, fields):
    storage = invoice_edits_path()
    edits = load_invoice_edits()
    edits[digest(path)] = fields
    storage.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=storage.parent, mode="w", encoding="utf-8", delete=False) as output:
            temporary = Path(output.name)
            json.dump(edits, output, ensure_ascii=False, indent=2)
        temporary.replace(storage)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def undo_moves(history, progress=None):
    restored, failed = [], []
    for position, (original, current, checksum) in enumerate(reversed(history), 1):
        if progress:
            progress(position, len(history), current.name)
        try:
            if current.is_symlink() or digest(current) != checksum:
                raise OSError("이동한 파일이 변경됐어요.")
            original.parent.mkdir(parents=True, exist_ok=True)
            move_evidence(current, original)
        except OSError as error:
            failed.append((original, current, checksum, str(error)))
        else:
            restored.append((current, original))
    return restored, failed


def organize_invoice(source, target, fields, number):
    checksum = digest(source)
    remember_original_name(source, checksum)
    target.parent.mkdir(exist_ok=True)
    move_evidence(source, target)
    try:
        _, incomplete = export_markdown(target, fields, number)
    except (OSError, RuntimeError, ValueError) as error:
        try:
            move_evidence(target, source)
        except OSError as rollback_error:
            return checksum, False, f"마크다운 저장 및 PDF 복원 실패 ({error} / {rollback_error})"
        raise
    return checksum, True, "OCR 본문 확인 필요" if incomplete else ""


def check_installation(folder=None):
    results = []
    tests = ("폴더 쓰기", "텍스트 PDF 읽기", "독일어 스캔 OCR", "ZIP 저장")
    try:
        with tempfile.TemporaryDirectory(prefix="invoice-check-", dir=folder) as directory:
            working = Path(directory)
            results.append((tests[0], "정상"))
            pdf_path = working / "test.pdf"
            try:
                with pymupdf.open() as document:
                    page = document.new_page()
                    page.insert_text((60, 65), "Rechnung Gesamtbetrag 58.39 EUR", fontsize=24)
                    pixmap = page.get_pixmap(dpi=200)
                    document.save(pdf_path)
                with pymupdf.open(pdf_path) as document:
                    if "58.39" not in document[0].get_text():
                        raise ValueError("테스트 금액을 읽지 못했어요.")
                results.append((tests[1], "정상"))
            except (OSError, ValueError, RuntimeError) as error:
                results.append((tests[1], f"실패 · {error}"))
                pixmap = None
            try:
                tessdata, langs = tessdata_folder()
                if "deu" not in langs.split("+"):
                    results.append((tests[2], "독일어 파일 없음 · 인터넷에 연결하고 Start.bat을 다시 실행하세요."))
                elif pixmap is None:
                    results.append((tests[2], "PDF 읽기 오류를 먼저 해결하세요."))
                else:
                    with pymupdf.open() as image_doc:
                        page = image_doc.new_page()
                        page.insert_image(page.rect, pixmap=pixmap)
                        textpage = page.get_textpage_ocr(language="deu", dpi=200, full=True, tessdata=tessdata)
                        if "58.39" not in page.get_text(textpage=textpage):
                            raise ValueError("테스트 금액을 읽지 못했어요.")
                    results.append((tests[2], "정상"))
            except (OSError, ValueError, RuntimeError) as error:
                results.append((tests[2], f"실패 · Start.bat을 다시 실행한 뒤 확인하세요. ({error})"))
            try:
                output = working / "test.zip"
                with ZipFile(output, "w") as archive:
                    archive.writestr("test.txt", "OK")
                with ZipFile(output) as archive:
                    if archive.read("test.txt") != b"OK":
                        raise OSError("ZIP에서 테스트 파일을 읽지 못했어요.")
                results.append((tests[3], "정상"))
            except OSError as error:
                results.append((tests[3], f"실패 · 저장 공간과 폴더 권한을 확인하세요. ({error})"))
    except OSError as error:
        results.append((tests[0], f"실패 · 쓸 수 있는 폴더를 선택하세요. ({error})"))
    return results


class App:
    """Step 1: collect evidence, check what was read, rename and file it."""

    def __init__(self, root, practice_folder=None, container=None, on_change=None):
        from ui_theme import FONT_TITLE, configure_tags, more_menu

        self.root = root
        self.rows = {}
        self.targets = {}
        self.invoice_like = {}
        self.parts = {}  # combined PDFs: path -> page ranges of each invoice
        self.checks = {}  # path -> {field: why it needs a look}, from reading (file name, learned layout, ...)
        self.last_moves = []
        self.inbox_mode = False
        self.folder = None
        self.practice_folder = practice_folder
        self.on_change = on_change
        self.on_work_folder = None  # set by the main window to keep steps 2-5 on the same work folder
        self.on_manage_reasons = None  # set by the main window: ‘증빙 없이 처리’ reasons (step 3)
        self.on_manage_mail = None  # mailboxes for the mail search
        self.on_account_order = None  # order of bank accounts and cards for numbering
        if container is None:
            title = f"인보이스 PDF 파일명 변경 · v{VERSION}"
            root.title(f"[연습 모드] {title}" if practice_folder else title)
            root.geometry("1120x560")
            container = root
            self.root.report_callback_exception = self.report_callback_error
        head = ttk.Frame(container, padding=(18, 14, 18, 4))
        head.pack(fill="x")
        ttk.Label(head, text="1. 증빙 모으기·정리", style="Title.TLabel", font=FONT_TITLE).pack(anchor="w")
        ttk.Label(head, style="Muted.TLabel", text=(
            "가져온 파일은 작업 폴더에 모아 발행일·업체명·총액·번호를 읽어요. "
            "빨간 행은 필수 누락, 회색 행은 인보이스가 아닌 것 같은 파일이에요.")).pack(anchor="w")
        toolbar = ttk.Frame(container, padding=(18, 8, 18, 4))
        toolbar.pack(fill="x")
        self.import_buttons = (ttk.Button(toolbar, text="파일 가져오기", command=self.import_files),
                               ttk.Button(toolbar, text="폴더 가져오기", command=self.import_folder))
        self.import_buttons[0].pack(side="left")
        self.import_buttons[1].pack(side="left", padx=6)
        self.update_import_labels()
        ttk.Button(toolbar, text="편집", command=self.edit).pack(side="left", padx=6)
        self.apply_button = ttk.Button(toolbar, text="파일명 변경", command=self.apply, style="Primary.TButton")
        self.apply_button.pack(side="left", padx=6)
        ttk.Button(toolbar, text="원본·내용 보기", command=self.view_contents).pack(side="left", padx=6)
        ttk.Button(toolbar, text="중복 정리", command=self.clean_duplicates).pack(side="left", padx=6)
        more_menu(toolbar, "더보기 ▾", [
            ("작업 폴더 열기 (탐색기)", self.reveal_work_folder),
            ("환경설정 (작업 폴더·복사/이동)", self.open_settings),
            ("다른 폴더 열기 (옮기지 않고 보기)", self.choose),
            ("새 인보이스 (미분류)", self.open_inbox),
            ("배운 인보이스 양식", self.manage_learned),
            ("인보이스 아닌 파일 → 제외 폴더", self.exclude_selected),
            ("마크다운 다시 만들기", self.save_markdown),
            ("정리 되돌리기", self.undo),
        ]).pack(side="left", padx=6)
        options = ttk.Frame(container, padding=(18, 0, 18, 6))
        options.pack(fill="x")
        self.include_subfolders = tk.BooleanVar()
        ttk.Checkbutton(options, text="하위 폴더 포함", variable=self.include_subfolders, command=self.load_folder).pack(side="left")
        self.by_month = tk.BooleanVar()
        ttk.Checkbutton(options, text="발행월 폴더로 이동", variable=self.by_month, command=self.refresh).pack(side="left", padx=12)
        self.view = tk.StringVar(value="전체")
        for label in ("전체", "필수 누락", "인보이스 아님 의심", "변경 가능"):
            ttk.Radiobutton(options, text=label, value=label, variable=self.view, command=self.refresh,
                            style="Chip.TRadiobutton").pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value="‘파일 가져오기’나 ‘폴더 가져오기’로 인보이스를 작업 폴더에 넣으세요. 여러 행은 Ctrl 또는 Shift로 선택할 수 있어요.")
        if container is root:
            ttk.Label(root, textvariable=self.status, style="Status.TLabel").pack(fill="x", side="bottom")
        frame = ttk.Frame(container, padding=(18, 0, 18, 8))
        frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(frame, columns=("check", "old", "new", "status"), show="headings")
        for column, label, width in zip(self.table["columns"], ("☐", "현재 파일명", "변경할 파일명", "읽기 결과·상태"),
                                        (38, 320, 470, 260)):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, minwidth=38 if column == "check" else 110,
                              stretch=column != "check", anchor="center" if column == "check" else "w")
        self.table.heading("check", command=self.toggle_all)
        table_tools(self.table, "step1-files", stretch="new", skip=("check",))
        across = ttk.Scrollbar(frame, orient="horizontal", command=self.table.xview)
        self.table.configure(xscrollcommand=across.set)
        across.pack(side="bottom", fill="x")
        configure_tags(self.table)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda event: self.edit())
        if not practice_folder:
            self.folder = work_folder()  # v1.7: invoices live in one fixed work folder
            root.after(150, lambda: self.folder == work_folder() and not self.rows and self.use_work_folder())
        self.table.bind("<Control-a>", lambda event: self.table.selection_set(self.table.get_children()))
        self.table.bind("<<TreeviewSelect>>", lambda event: self.show_checks())
        self.table.bind("<Button-1>", self.click_check, add=True)
        if practice_folder:
            self.folder = practice_folder / "Belege"
            self.load_folder()
            self.status.set("연습 모드 · 예시 증빙을 열었어요. 왼쪽 ‘2. 명세서 불러오기’에서 예시 명세서도 불러올 수 있어요.")
        else:
            self.root.after_idle(self.first_check)

    def show_checks(self):
        """Mirror the row selection in the check-box column."""
        chosen = set(self.table.selection())
        rows = self.table.get_children()
        for row in rows:
            self.table.set(row, "check", "☑" if row in chosen else "☐")
        self.table.heading("check", text="☑" if rows and len(chosen) >= len(rows) else "☐")

    def click_check(self, event):
        """A click in the check-box column adds or removes that row without clearing the others."""
        if self.table.identify_region(event.x, event.y) != "cell" or self.table.identify_column(event.x) != "#1":
            return None
        row = self.table.identify_row(event.y)
        if not row:
            return None
        if row in self.table.selection():
            self.table.selection_remove(row)
        else:
            self.table.selection_add(row)
        return "break"

    def toggle_all(self):
        rows = self.table.get_children()
        if rows and len(self.table.selection()) >= len(rows):
            self.table.selection_remove(*rows)
        else:
            self.table.selection_set(rows)

    def notice(self, message, kind="info", title="안내"):
        """Show a guidance message as a pop-up inside the window and keep it in the status line."""
        self.status.set(message.replace("\n", " "))
        show_notice(self.root, message, title=title, kind=kind)

    def report_callback_error(self, error_type, error, traceback):
        record_error("gui", error)
        messagebox.showerror("화면 오류", "동작 중 오류가 발생했어요. ‘오류 진단 저장’에서 현재 세션의 진단을 저장할 수 있어요.", parent=self.root)

    def open_practice(self):
        try:
            command = [sys.executable, "--practice"] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve()), "--practice"]
            subprocess.Popen(command)
        except OSError as error:
            record_error("gui", error)
            messagebox.showerror("연습 모드 실행 실패", str(error), parent=self.root)

    def save_diagnostics(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                           initialfile="InvoiceRenamer-Diagnose.json", filetypes=[("오류 진단", "*.json")])
        if not path:
            return
        try:
            export_diagnostics(Path(path))
        except OSError as error:
            messagebox.showerror("진단 저장 실패", str(error), parent=self.root)
            return
        self.status.set("오류 진단을 저장했어요. 거래, 메모, 파일 경로는 포함되지 않아요.")

    def diagnose(self):
        if self.folder is not None and not self.practice_folder:
            try:
                self.folder.mkdir(parents=True, exist_ok=True)  # the work folder may not exist yet on the first start
            except OSError:
                pass
        results = check_installation(self.folder)
        where = str(self.folder) if self.folder else "시스템 임시 폴더"
        messagebox.showinfo("설치·작동 점검", f"쓰기 검사 위치 · {where}\n\n" + "\n".join(
            f"{name} · {result}" for name, result in results
        ), parent=self.root)

    def first_check(self):
        directory = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InvoiceRenamer"
        flag = directory / "first-check-v1"
        if not flag.exists():
            self.diagnose()
            try:
                directory.mkdir(parents=True, exist_ok=True)
                flag.touch()
            except OSError:
                pass

    def open_inbox(self):
        if self.folder is None:
            selected = filedialog.askdirectory(parent=self.root, title="인보이스 상위 폴더 선택")
            if not selected:
                return
            self.folder = Path(selected)
        inbox = self.folder / "미분류"
        try:
            inbox.mkdir(exist_ok=True)
            if not inbox.resolve().is_relative_to(self.folder.resolve()):
                raise OSError("미분류 폴더가 선택한 폴더 바깥에 있어요.")
        except OSError as error:
            messagebox.showerror("미분류 폴더 열기 실패", str(error), parent=self.root)
            return
        self.inbox_mode = True
        self.load_folder()
        self.table.selection_set(tuple(self.targets))
        self.status.set(f"{inbox} · 정리 가능한 {len(self.targets)}개 선택됨 · 경로 확인 후 ‘정리하기 (미분류)’")

    def use_work_folder(self, load=True):
        """Open the fixed work folder (created when missing)."""
        folder = work_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.status.set(f"작업 폴더를 만들지 못했어요: {folder} ({error}) · 더보기 → ‘작업 폴더 바꾸기’")
            return False
        if self.folder != folder:
            self.rows.clear()
        self.folder = folder
        self.inbox_mode = False
        if load:
            self.load_folder()
        return True

    def import_files(self, names=None):
        if self.practice_folder:
            self.notice("연습 모드에서는 파일을 가져오지 않아요. 예시 증빙으로 연습하세요.")
            return
        names = names or filedialog.askopenfilenames(parent=self.root, title="작업 폴더로 옮길 인보이스 고르기 (여러 개 가능)",
                                                    filetypes=[("인보이스 (PDF·사진·XML)", "*.pdf *.jpg *.jpeg *.png *.xml"),
                                                               ("모든 파일", "*.*")])
        if names:
            self.finish_import([Path(name) for name in names])

    def import_folder(self, selected=None):
        if self.practice_folder:
            self.notice("연습 모드에서는 파일을 가져오지 않아요. 예시 증빙으로 연습하세요.")
            return
        selected = selected or filedialog.askdirectory(parent=self.root, title="인보이스가 든 폴더 고르기 (하위 폴더 포함, 작업 폴더로 옮겨요)")
        if not selected:
            return
        source, target = Path(selected).resolve(), work_folder().resolve()
        if source.is_relative_to(target) or target.is_relative_to(source):
            self.notice("작업 폴더이거나 작업 폴더를 품은 폴더예요. 다른 폴더를 고르세요.", kind="warn")
            return
        try:
            files = evidence_in(source)
        except OSError as error:
            self.notice(f"폴더를 읽지 못했어요: {error}", kind="error")
            return
        if not files:
            self.notice("고른 폴더에 PDF·사진·XML 인보이스가 없어요.")
            return
        self.finish_import(files)

    def finish_import(self, files):
        if not self.use_work_folder(load=False):
            return
        copy = import_mode() == "copy"
        with Busy(self.root, "작업 폴더로 복사하고 있어요" if copy else "작업 폴더로 옮기고 있어요") as busy:
            history, inside, errors = import_evidence(files, self.folder, copy=copy, progress=busy.step)
        if history and not copy:
            self.last_moves = history
        self.load_folder()
        lines = [f"{len(history)}개를 작업 폴더로 {'복사했어요. 원본은 그대로 있어요' if copy else '옮겼어요'}.\n{self.folder}"]
        if inside:
            lines.append(f"이미 작업 폴더 안에 있는 파일 {inside}개는 그대로 뒀어요.")
        if errors:
            lines.append(f"옮기지 못한 파일 {len(errors)}개: " + " / ".join(errors[:3]))
        if history:
            lines.append("빨간 행은 편집에서 고치고, 체크한 뒤 ‘파일명 변경’을 누르세요.")
            lines.append("가져오기 방식(복사·이동)은 도구 → ‘환경설정’에서 바꿔요." if copy
                         else "원래 자리로 돌리려면 더보기 → ‘정리 되돌리기’.")
        self.notice("\n".join(lines), kind="warn" if errors else "info", title="가져오기 완료")
        if self.on_change:
            self.on_change()

    def reveal_work_folder(self):
        if not self.use_work_folder(load=self.folder != work_folder()):
            return
        try:
            if os.name == "nt":
                os.startfile(self.folder)
            else:
                subprocess.Popen(["xdg-open", str(self.folder)])
        except OSError as error:
            self.notice(f"탐색기를 열지 못했어요: {error}\n{self.folder}", kind="warn")

    def open_settings(self):
        """Settings panel: work folder and how files are brought in."""
        dialog = InlineDialog(self.root)
        dialog.title("환경설정")
        dialog.geometry("720x420")
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="작업 폴더", font=FONT_BOLD).grid(row=0, column=0, sticky="w")
        folder_text = tk.StringVar(value=str(work_folder()))
        ttk.Label(body, textvariable=folder_text, wraplength=640).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 6))
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Button(buttons, text="작업 폴더 바꾸기", command=lambda: (self.change_work_folder(), folder_text.set(str(work_folder())))
                   ).pack(side="left")
        ttk.Button(buttons, text="탐색기로 열기", command=self.reveal_work_folder).pack(side="left", padx=8)
        ttk.Label(body, text="인보이스·명세서 가져오기 방식", font=FONT_BOLD).grid(row=3, column=0, sticky="w", pady=(22, 4))
        mode = tk.StringVar(value=import_mode())

        def save_mode():
            try:
                save_settings({"import_mode": mode.get()})
            except OSError as error:
                show_notice(dialog, f"설정을 저장하지 못했어요: {error}", kind="error")
                return
            self.status.set("가져오기 방식: " + ("복사 (원본 그대로)" if mode.get() == "copy" else "이동"))
            self.update_import_labels()

        for row, (value, label) in enumerate((
                ("copy", "복사 — 원본 파일은 그대로 두고 작업 폴더에 사본을 만들어요 (추천, 테스트할 때 안전)"),
                ("move", "이동 — 원본 파일을 작업 폴더로 옮겨요 (파일이 한 곳에만 남아요, 정리 되돌리기 가능)")), 4):
            ttk.Radiobutton(body, text=label, value=value, variable=mode, command=save_mode).grid(
                row=row, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(body, style="Muted.TLabel", wraplength=640, text=(
            "같은 방식이 1단계 ‘파일 가져오기·폴더 가져오기’와 2단계 명세서 불러오기에 함께 적용돼요. "
            "바꾼 설정은 바로 저장돼요.")).grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        if self.on_manage_reasons:
            ttk.Label(body, text="증빙 없이 처리 사유", font=FONT_BOLD).grid(row=7, column=0, sticky="w", pady=(22, 4))
            ttk.Button(body, text="사유 관리 (한국어·독일어·추가)",
                       command=lambda: (dialog.destroy(), self.on_manage_reasons())).grid(row=8, column=0, sticky="w")
        if self.on_manage_mail or self.on_account_order:
            ttk.Label(body, text="메일 검색·계좌 순서", font=FONT_BOLD).grid(row=9, column=0, sticky="w", pady=(22, 4))
            row = ttk.Frame(body)
            row.grid(row=10, column=0, columnspan=3, sticky="w")
            if self.on_manage_mail:
                ttk.Button(row, text="메일 등록 (Gmail·한메일)",
                           command=lambda: (dialog.destroy(), self.on_manage_mail())).pack(side="left")
            if self.on_account_order:
                ttk.Button(row, text="계좌 순서 정하기",
                           command=lambda: (dialog.destroy(), self.on_account_order())).pack(side="left", padx=6)

    def update_import_labels(self):
        """‘파일 가져오기 (복사)’ or ‘(이동)’: the button says what happens to the originals."""
        word = "복사" if import_mode() == "copy" else "이동"
        for button, label in zip(getattr(self, "import_buttons", ()), ("파일 가져오기", "폴더 가져오기")):
            button.configure(text=f"{label} ({word})")

    def change_work_folder(self):
        selected = filedialog.askdirectory(parent=self.root, title="새 작업 폴더 고르기", initialdir=str(work_folder()))
        if not selected:
            return
        try:
            save_settings({"work_folder": str(Path(selected))})
        except OSError as error:
            self.notice(f"설정을 저장하지 못했어요: {error}", kind="error")
            return
        self.use_work_folder()
        self.notice(f"작업 폴더를 바꿨어요.\n{self.folder}\n이전 작업 폴더의 파일은 옮기지 않았어요. 필요하면 ‘폴더 가져오기’로 옮기세요.",
                    title="작업 폴더")
        if self.on_work_folder:
            self.on_work_folder(self.folder)

    def choose(self):
        if self.practice_folder:
            self.folder = self.practice_folder / "Belege"
            self.load_folder()
            return
        selected = filedialog.askdirectory(parent=self.root)
        if not selected:
            return
        self.folder = Path(selected)
        self.inbox_mode = False
        self.rows.clear()
        self.load_folder()

    def load_folder(self):
        if self.folder is None:
            return
        scanned = {row[0]: row for row in self.rows.values()}
        stale = {key for key in self.rows if self.table.exists(key)} | set(self.table.get_children())
        if stale:
            self.table.delete(*stale)
        self.rows.clear()
        try:
            root = self.folder.resolve()
            if self.inbox_mode:
                candidates = (self.folder / "미분류").rglob("*")
            else:
                candidates = self.folder.rglob("*") if self.include_subfolders.get() else self.folder.iterdir()
            paths = sorted(
                path for path in candidates
                if path.suffix.lower() in RENAMER_EXTENSIONS and not path.is_symlink() and path.is_file()
                and path.resolve().is_relative_to(root)
                and not {EXCLUDED_FOLDER, STATEMENT_FOLDER, DUPLICATE_FOLDER} & set(path.relative_to(self.folder).parts[:-1])
            )
        except OSError as error:
            self.status.set(str(error))
            return
        try:
            load_invoice_edits()
        except EditsFileError as error:
            messagebox.showerror("수정값 파일 오류", str(error), parent=self.root)
            self.status.set("수정값 파일 오류 · 파일을 확인한 뒤 폴더를 다시 여세요.")
            return
        self.status.set("PDF를 읽고 있어요…")
        self.root.update_idletasks()
        busy = Busy(self.root, "증빙을 읽고 있어요") if len(paths) > 3 else None
        for index, path in enumerate(paths):
            if path in scanned:
                self.rows[str(index)] = scanned[path]
                self.table.insert("", "end", iid=str(index))
                continue
            if busy:
                busy.step(index + 1, len(paths), path.name)
            info = {}
            try:
                fields, note, number, _ = read_invoice_details(path, info)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("pdf_read", error)
                fields, note, number = ("", "", ""), "PDF 읽기 실패", ""
            self.invoice_like[path] = info.get("invoice_like", True)
            self.checks[path] = info.get("checks", {})
            if info.get("parts"):
                self.parts[path] = info["parts"]
            else:
                self.parts.pop(path, None)
            self.rows[str(index)] = (path, fields, note, number)
            self.table.insert("", "end", iid=str(index))
        if busy:
            busy.close()
        self.refresh()
        groups = duplicate_groups((key, path, fields, number, cached_digest(path))
                                  for key, (path, fields, _, number) in self.rows.items())
        hint = f"  |  중복 의심 {len(groups)}묶음 · ‘중복 정리’를 눌러 확인하세요" if groups else ""
        self.status.set(f"{self.folder}  |  증빙 {len(paths)}개{hint}")

    def row_check(self, path, fields, note, number):
        """Return (text, tag) describing how well the evidence was read."""
        if note:
            return f"확인 필요 · {note}", "bad"
        if not self.invoice_like.get(path, True):
            return "인보이스 아님 의심 · 내용 확인", "muted"
        missing = [label for label, value in zip(("발행일", "업체명", "총액"), fields) if not value]
        small = False
        if not number:
            amount, _, currency = fields[2].partition(" ")
            try:
                small = currency == "EUR" and abs(Decimal(amount)) <= 250
            except ArithmeticError:
                small = False
            if not small:
                missing.append("인보이스 번호")
        if missing:
            return "필수 누락: " + ", ".join(missing), "bad"
        checks = getattr(self, "checks", {}).get(path) or {}
        if checks:
            return "확인 필요 · " + ", ".join(f"{name} {why}" for name, why in checks.items()), "warn"
        if small:
            return "번호 없음 · 250€ 이하 소액영수증 확인", "warn"
        return "", "ok"

    def refresh(self):
        organize = self.inbox_mode or self.by_month.get()
        button = "정리하기 (미분류)" if self.inbox_mode else "정리하기" if organize else "파일명 변경"
        self.apply_button.configure(text=button)
        self.targets.clear()
        if self.folder is None:
            return
        occupied = {}
        counts = {"bad": 0, "muted": 0, "ok": 0, "warn": 0}
        shown = []
        view = self.view.get() if hasattr(self, "view") else "전체"
        for key, (path, fields, note, number) in self.rows.items():
            source_label = str(path.relative_to(self.folder))
            check, tag = self.row_check(path, fields, note, number)
            counts[tag] += 1
            folder = self.folder / fields[0][:7] if organize and fields[0] else path.parent
            name = proposed_name(fields, path, number)
            keep_name = organize and not self.inbox_mode and bool(fields[0]) and not name
            if keep_name:
                name = path.name
            target_label, status = "", check
            if name and not note:
                try:
                    if folder not in occupied:
                        occupied[folder] = {item.name.casefold() for item in folder.iterdir()} if folder.exists() else set()
                    target = reserve_name(folder, name, path, occupied[folder])
                except OSError:
                    target, status, tag = None, "확인 필요 · 대상 폴더 열기 실패", "bad"
                    name = ""
                if name and target is None:
                    status, tag = "확인 필요 · 파일명이 너무 김", "bad"
                elif name:
                    target_label = str(target.relative_to(self.folder))
                    if target == path:
                        done = "이름 정리 완료"
                    else:
                        self.targets[key] = target
                        done = "이동 가능 · 기존 이름 사용" if keep_name else "변경 가능"
                    status = f"{check} · {done}" if check else done
            elif not check:
                status, tag = "확인 필요 · 파일명이 너무 김", "bad"
            self.table.item(key, values=("☑" if key in self.table.selection() else "☐", source_label, target_label, status),
                            tags=(tag,))
            visible = (view == "전체" or (view == "필수 누락" and tag == "bad")
                       or (view == "인보이스 아님 의심" and tag == "muted") or (view == "변경 가능" and key in self.targets))
            if visible:
                shown.append(key)
        self.table.detach(*self.table.get_children())
        for index, key in enumerate(shown):
            self.table.move(key, "", index)
        resort(self.table)
        self.summary = counts
        if self.on_change:
            self.on_change()

    def clean_duplicates(self):
        """Show invoices saved more than once and move the extra copies into the 중복 folder."""
        if self.folder is None or not self.rows:
            self.notice("먼저 증빙 폴더를 여세요.")
            return
        entries = []
        with Busy(self.root, "같은 인보이스를 찾고 있어요") as busy:
            for position, (key, (path, fields, note, number)) in enumerate(list(self.rows.items()), 1):
                if position % 10 == 1 or position == len(self.rows):
                    busy.step(position, len(self.rows), path.name)
                checksum = cached_digest(path)
                entries.append((key, path, fields, number, checksum))
        groups = duplicate_groups(entries)
        if not groups:
            self.notice("중복으로 보이는 인보이스가 없어요.\n내용이 같거나, 업체·발행일·총액·번호가 모두 같은 파일을 찾아요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("중복 인보이스 정리")
        dialog.geometry("1000x600")
        dialog.grab_set()
        ttk.Label(dialog, text=f"같은 인보이스로 보이는 묶음 {len(groups)}개예요. 체크한 파일은 규칙대로 파일명을 바꿔 ‘{DUPLICATE_FOLDER}’ 폴더로 옮겨요(삭제하지 않아요).\n"
                               "내용이 완전히 같은 사본은 미리 체크했어요. ‘정보만 같음’은 독촉장·재발행본일 수 있으니 열어 보고 고르세요. 묶음마다 한 개는 꼭 남아요.",
                  wraplength=950, padding=(14, 10, 14, 6)).pack(anchor="w")
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=14)
        columns = ("move", "group", "file", "day", "seller", "amount", "number", "kind")
        table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for column, label, width in zip(columns, ("옮김", "묶음", "파일", "발행일", "업체", "총액", "번호", "판정"),
                                        (50, 50, 290, 90, 150, 95, 110, 110)):
            table.heading(column, text=label)
            table.column(column, width=width, minwidth=40, anchor="center" if column in ("move", "group") else "w")
        bar = ttk.Scrollbar(frame, command=table.yview)
        table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        table.tag_configure("keep", background=COLORS.get("ok_bg", "#e8f5ec"))
        table.tag_configure("move", foreground=COLORS["muted"])
        marked, rows, members = {}, {}, {}
        for number, group in enumerate(groups, 1):
            keep_hash = group[0][4]
            members[number] = [entry[0] for entry in group]
            for index, entry in enumerate(group):
                key, path, fields, invoice_number, checksum = entry
                exact = index > 0 and checksum and checksum == keep_hash
                kind = "남길 파일" if index == 0 else "내용 동일" if exact else "정보만 같음"
                marked[key] = bool(exact)
                rows[key] = (number, path, fields, invoice_number, kind)

        def fill():
            table.delete(*table.get_children())
            for key, (number, path, fields, invoice_number, kind) in rows.items():
                table.insert("", "end", iid=key, tags=("move" if marked[key] else "keep" if kind == "남길 파일" else "",),
                             values=("☑" if marked[key] else "☐", number, str(path.relative_to(self.folder)), *fields, invoice_number, kind))
            count = sum(marked.values())
            info.set(f"옮길 파일 {count}개")

        def toggle(event):
            key = table.identify_row(event.y)
            if not key or table.identify_column(event.x) != "#1":
                return
            group = rows[key][0]
            if not marked[key] and all(marked[other] for other in members[group] if other != key):
                show_notice(dialog, "묶음마다 한 개는 남겨야 해요.")
                return "break"
            marked[key] = not marked[key]
            fill()
            table.selection_set(key)
            return "break"

        def open_selected(_event=None):
            selection = table.selection()
            if selection:
                open_evidence(rows[selection[0]][1])

        def apply():
            chosen = [key for key, value in marked.items() if value]
            if not chosen:
                show_notice(dialog, "옮길 파일을 체크하세요.")
                return
            target_folder = self.folder / DUPLICATE_FOLDER
            history, errors = [], []
            try:
                target_folder.mkdir(exist_ok=True)
                occupied = {item.name.casefold() for item in target_folder.iterdir()}
            except OSError as error:
                messagebox.showerror("중복 폴더 만들기 실패", str(error), parent=dialog)
                return
            busy = Busy(dialog, "중복 폴더로 옮기고 있어요")
            for position, key in enumerate(chosen, 1):
                _, path, fields, invoice_number, _ = rows[key]
                busy.step(position, len(chosen), path.name)
                name = proposed_name(fields, path, invoice_number) or path.name  # same naming rule as 파일명 변경
                target = reserve_name(target_folder, name, path, occupied)
                try:
                    if target is None:
                        raise OSError("파일명이 너무 길어요.")
                    checksum = digest(path)
                    remember_original_name(path, checksum)
                    move_evidence(path, target)
                    history.append((path, target, checksum))
                except OSError as error:
                    errors.append(f"{path.name}: {error}")
            busy.close()
            if history:
                self.last_moves = history
            dialog.destroy()
            self.load_folder()
            self.status.set(f"‘{DUPLICATE_FOLDER}’ 폴더로 {len(history)}개 이동 · 실패 {len(errors)}개 · 더보기 → 정리 되돌리기로 되돌릴 수 있어요")
            if errors:
                messagebox.showerror("이동 실패", "\n".join(errors[:8]), parent=self.root)

        table.bind("<Button-1>", toggle)
        table.bind("<Double-1>", open_selected)
        info = tk.StringVar()
        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(fill="x", padx=14, pady=10)
        ttk.Button(footer, text="파일 열기", command=open_selected).pack(side="left")
        tk.Label(footer, textvariable=info, background=COLORS["bg"], foreground=COLORS["muted"]).pack(side="left", padx=12)
        ttk.Button(footer, text="체크한 파일을 중복 폴더로 이동", style="Primary.TButton", command=apply).pack(side="right")
        fill()

    def exclude_selected(self):
        selection = [key for key in self.table.selection() if key in self.rows]
        if not selection or self.folder is None:
            self.notice("제외 폴더로 옮길 파일을 선택하세요. ‘인보이스 아님 의심’ 보기에서 고르면 편해요.")
            return
        if not ask_yes_no("제외 폴더로 이동", f"선택한 파일 {len(selection)}개를 ‘{EXCLUDED_FOLDER}’ 폴더로 옮길까요?\n"
                                   "파일은 지워지지 않고, ‘정리 되돌리기’로 되돌릴 수 있어요.", parent=self.root):
            return
        target_folder = self.folder / EXCLUDED_FOLDER
        history, errors = [], []
        try:
            target_folder.mkdir(exist_ok=True)
            occupied = {item.name.casefold() for item in target_folder.iterdir()}
        except OSError as error:
            messagebox.showerror("제외 폴더 만들기 실패", str(error), parent=self.root)
            return
        busy = Busy(self.root, "제외 폴더로 옮기고 있어요")
        for position, key in enumerate(selection, 1):
            path = self.rows[key][0]
            busy.step(position, len(selection), path.name)
            target = reserve_name(target_folder, path.name, path, occupied)
            try:
                if target is None:
                    raise OSError("파일명이 너무 길어요.")
                checksum = digest(path)
                move_evidence(path, target)
                history.append((path, target, checksum))
            except OSError as error:
                errors.append(f"{path.name}: {error}")
        busy.close()
        if history:
            self.last_moves = history
        self.load_folder()
        self.status.set(f"제외 폴더로 {len(history)}개 이동 · 실패 {len(errors)}개")
        if errors:
            messagebox.showerror("이동 실패", "\n".join(errors[:8]), parent=self.root)

    def learn_from(self, path, fields, number, anchors=None):
        """Remember this seller's layout and re-read the other files that are still incomplete.

        anchors: {"date"|"total"|"number": words next to the box the user dragged} — the label to learn.
        Before anything is stored the user sees which words were learned and what the other files would get."""
        self.refresh()
        if path.suffix.lower() == ".xml":
            return
        try:
            pages = list(read_pdf_pages(path))
            text = "\n".join(page[1] for page in pages) + "\n" + "\n".join(page[0] for page in pages)
            entry, reason = learned_formats.learn(text, path.name, fields, number, anchors=anchors, save=False)
        except (OSError, RuntimeError, ValueError) as error:
            record_error("learn_format", error)
            entry, reason = None, str(error)
        if not entry:
            self.status.set(f"수정값을 저장했어요 · {reason}")
            return
        known = {"date": "발행일", "total": "총액", "number": "번호"}
        words = " · ".join(f"{known[key]} 앞 ‘{label}’" for key, label in entry["labels"].items())
        previous = learned_formats.load_formats()
        try:
            learned_formats.remember(entry)
            changes = self.reread_incomplete(entry["marker"], preview=True)
        except OSError as error:
            record_error("learn_format", error)
            self.status.set(f"수정값을 저장했어요 · 양식 저장 실패: {error}")
            return
        lines = [f"‘{entry['marker']}’ 인보이스에서 이렇게 읽을게요: {words}"]
        warnings = []
        if changes:
            lines.append(f"\n같은 업체 파일 {len(changes)}개가 새로 채워져요 (‘확인 필요’로 표시):")
            for _key, row in changes[:8]:
                issued, seller, total = row[1]
                lines.append(f"• {row[0].name[:46]} → {issued or '?'} · {total or '?'} · 번호 {row[3] or '-'}")
            if len(changes) > 8:
                lines.append(f"… 외 {len(changes) - 8}개")
            if any(not row[1][0] or not row[1][2] for _key, row in changes):
                warnings.append("일부 파일은 여전히 빈 칸이 있어요. 값이 이상하면 ‘배우지 않기’를 누르고 원본에서 직접 골라 주세요.")
        else:
            lines.append("\n지금 목록에서 새로 채워질 다른 파일은 없어요. 다음에 이 업체 인보이스가 오면 이렇게 읽어요.")
        if not ask_yes_no("이 양식을 배울까요?", "\n".join(lines), parent=self.root, warnings=warnings,
                          undo="나중에 ‘배운 인보이스 양식’에서 지울 수 있어요.", ok="배우기", cancel="배우지 않기"):
            learned_formats.save_formats(previous)
            self.status.set("수정값만 저장했어요 · 양식은 배우지 않았어요.")
            return
        for key, row in changes:
            self.rows[key] = row[:4]
            self.invoice_like[row[0]] = True
            self.checks[row[0]] = row[4]
        self.refresh()
        self.status.set(f"‘{entry['marker']}’ 양식을 배웠어요 · 같은 업체 {len(changes)}개 채움(확인 필요 표시)")

    def reread_incomplete(self, marker="", preview=False):
        """Read again the files that still miss a required field (after a layout was learned).

        marker: only files whose text layer contains it are read again (the text layer is checked first
        without OCR, which is fast); scans without a text layer are skipped. Shows progress.
        preview=True: nothing changes; returns [(key, (path, fields, note, number, checks))] that would change."""
        todo = [(key, row) for key, row in self.rows.items() if not (all(row[1]) and row[3])]
        changes = []
        if not todo:
            return [] if preview else 0
        folded = marker.casefold()
        with Busy(self.root, "같은 업체 인보이스를 다시 읽고 있어요") as busy:
            for index, (key, (path, fields, note, number)) in enumerate(todo, 1):
                busy.step(index, len(todo), path.name)
                if folded and folded not in text_layer(path).casefold():
                    continue
                info = {}
                try:
                    new_fields, new_note, new_number, _ = read_invoice_details(path, info)
                except (OSError, RuntimeError, ValueError):
                    continue
                if info.get("learned") and (new_fields, new_number) != (fields, number):
                    checks = dict(info.get("checks", {}))
                    for name, old, new in zip(("발행일", "업체명", "총액"), fields, new_fields):
                        if old != new:
                            checks.setdefault(name, "배운 양식으로 다시 읽음")
                    changes.append((key, (path, new_fields, new_note, new_number, checks)))
        if preview:
            return changes
        for key, row in changes:
            self.rows[key] = row[:4]
            self.invoice_like[row[0]] = True
            self.checks[row[0]] = row[4]
        self.refresh()
        return len(changes)

    def manage_learned(self):
        """List of learned seller layouts; wrong ones can be deleted."""
        dialog = InlineDialog(self.root)
        dialog.title("배운 인보이스 양식")
        dialog.geometry("900x480")
        dialog.grab_set()
        ttk.Label(dialog, text="편집에서 인보이스를 한 번 고치면 그 업체 양식을 배워요. PDF에 ‘표시 단어’가 있으면 그 양식으로 읽어요. "
                               "잘못 배운 양식은 지우고 다시 고치면 돼요.", wraplength=860, padding=(14, 10, 14, 6)).pack(anchor="w")
        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(side="bottom", fill="x", padx=14, pady=10)
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=14)
        columns = ("업체명", "표시 단어", "발행일 앞 단어", "총액 앞 단어", "번호 앞 단어", "배운 파일")
        table = ttk.Treeview(frame, columns=columns, show="headings")
        for column, width in zip(columns, (140, 120, 130, 130, 120, 220)):
            table.heading(column, text=column)
            table.column(column, width=width, minwidth=60)
        table.pack(fill="both", expand=True)

        def fill():
            table.delete(*table.get_children())
            for index, entry in enumerate(learned_formats.load_formats()):
                labels = entry.get("labels", {})
                table.insert("", "end", iid=str(index), values=(entry.get("seller", ""), entry["marker"], labels.get("date", ""),
                                                                labels.get("total", ""), labels.get("number", ""),
                                                                f"{entry.get('learned_from', '')} · {entry.get('at', '')}"))

        def delete():
            selection = table.selection()
            if not selection:
                show_notice(dialog, "지울 양식을 고르세요.")
                return
            formats = learned_formats.load_formats()
            drop = {int(key) for key in selection}
            learned_formats.save_formats([entry for index, entry in enumerate(formats) if index not in drop])
            fill()

        ttk.Button(footer, text="선택 양식 지우기", command=delete).pack(side="left")
        fill()

    def edit_many(self, selection):
        """Fill the same issue date, seller or currency into several files at once (empty fields stay as read)."""
        dialog = InlineDialog(self.root)
        dialog.title(f"여러 파일 한 번에 입력 · {len(selection)}개")
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="x")
        ttk.Label(body, text="적은 칸만 고른 파일 모두에 들어가요. 비운 칸은 파일마다 읽은 값을 그대로 둬요.\n"
                             "예: Amazon 인보이스를 모두 고르고 업체명에 ‘Amazon EU S.à r.l.’을 적으세요.",
                  wraplength=520).grid(row=0, columnspan=2, sticky="w", pady=(0, 10))
        entries = []
        for index, label in enumerate(("업체명", "발행일 (YYYY-MM-DD)", "통화 (총액이 숫자만 있을 때, 예: EUR)"), 1):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", padx=(0, 12), pady=4)
            entry = ttk.Entry(body, width=36)
            entry.grid(row=index, column=1, sticky="w")
            entries.append(entry)

        def save():
            seller, day, currency = (entry.get().strip() for entry in entries)
            if not (seller or day or currency):
                show_notice(dialog, "한 칸 이상 적어 주세요.")
                return
            if day and normalized_date(day) != day:
                show_notice(dialog, "발행일은 2026-07-02처럼 적어 주세요.")
                return
            if currency and not re.fullmatch(r"[A-Za-z]{3}", currency):
                show_notice(dialog, "통화는 EUR처럼 세 글자로 적어 주세요.")
                return
            done, failed = 0, []
            for key in selection:
                path, fields, note, number = self.rows[key]
                total = fields[2]
                if currency and total and not re.search(r"[A-Za-z]{3}$", total):
                    total = f"{total} {currency.upper()}"
                edited = (day or fields[0], seller or fields[1], total)
                try:
                    save_invoice_edit(path, (*edited, number))
                except (OSError, ValueError) as error:
                    failed.append(f"{path.name}: {error}")
                    continue
                self.rows[key] = (path, edited, "" if all(edited) else note, number)
                done += 1
            dialog.destroy()
            self.refresh()
            missing = sum(1 for key in selection if self.row_check(*self.rows[key])[1] == "bad")
            extra = f"\n아직 필수 항목이 빈 파일 {missing}개는 ‘필수 누락’ 보기에서 확인하세요." if missing else ""
            self.notice(f"{done}개 파일에 입력했어요.{extra}", title="한 번에 입력 완료")
            if failed:
                messagebox.showerror("저장 실패", "\n".join(failed[:8]), parent=self.root)

        footer = ttk.Frame(dialog, padding=(16, 0, 16, 14))
        footer.pack(fill="x")
        ttk.Button(footer, text="고른 파일에 적용", command=save).pack(side="right")
        entries[0].focus_set()

    def split_combined(self, path):
        parts = self.parts[path]
        pages = ", ".join(f"{first + 1}" + (f"~{last + 1}" if last > first else "") + "쪽" for first, last in parts)
        if not ask_yes_no("인보이스 나누기", (
                f"{path.name}\n이 파일에는 판매자가 다른 인보이스 {len(parts)}개가 들어 있어요 ({pages}).\n"
                f"인보이스마다 따로 파일로 나눌까요?\n원본은 ‘제외’ 폴더에 그대로 보관해요."), parent=self.root):
            return
        try:
            created = split_pdf(path, parts)
        except (OSError, RuntimeError, ValueError) as error:
            record_error("split_pdf", error)
            messagebox.showerror("나누기 실패", str(error), parent=self.root)
            return
        self.parts.pop(path, None)
        self.load_folder()
        self.notice(f"{len(created)}개 파일로 나눴어요: " + ", ".join(target.name for target in created)
                    + f"\n원본은 ‘{EXCLUDED_FOLDER}’ 폴더에 있어요.", title="나누기 완료")

    def edit(self):
        selection = [key for key in self.table.selection() if key in self.rows]
        if len(selection) > 1:
            self.edit_many(selection)
            return
        if len(selection) != 1:
            self.notice("편집할 파일을 고르세요. 여러 개를 고르면 업체명 등을 한 번에 넣을 수 있어요.")
            return
        key = selection[0]
        path, fields, _, number = self.rows[key]
        if self.parts.get(path):
            self.split_combined(path)
            return
        dialog = InlineDialog(self.root)
        dialog.title("인보이스 정보 편집")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack()
        ttk.Label(body, text=path.name).grid(row=0, columnspan=4, sticky="w", pady=(0, 8))

        def save():
            values = form.raw()
            day = normalized_date(values[0]) if re.fullmatch(DATE, values[0]) else pick_value("발행일", values[0])
            if not day or not values[1] or not values[2]:
                show_notice(dialog, "발행일, 업체명, 총액(숫자)을 입력하세요. 통화는 옆 칸에서 골라요(기본 EUR).\n"
                                    "빨간 ‘누락’ 칸은 ‘고르기’로 원본에서 고를 수 있어요.", kind="warn")
                return
            edited = day, values[1], values[2]
            if not proposed_name(edited):
                show_notice(dialog, "파일명에 사용할 업체명을 입력하세요.", kind="warn")
                return
            try:
                save_invoice_edit(path, (*edited, values[3]))
            except (OSError, ValueError) as error:
                show_notice(dialog, f"수정값을 저장하지 못했어요: {error}", kind="error")
                return
            self.rows[key] = (path, edited, "", values[3])
            self.invoice_like[path] = True
            self.checks[path] = {}
            dialog.destroy()
            self.learn_from(path, edited, values[3], anchors=form.anchors)

        form = ValueForm(body, path, (*fields, number), checks=self.checks.get(path), on_save=save)
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        if path.suffix.lower() != ".xml":
            ttk.Button(buttons, text="원본에서 보기·값 고르기", command=lambda: form.pick(form.first_missing())).pack(side="left")
        ttk.Button(buttons, text="저장", style="Primary.TButton", command=save).pack(side="right")
        ttk.Label(body, text="빨강 = 비어 있음 · 주황 = 확인 필요(말풍선에 이유) · 초록 = 직접 고른 값. 빈 칸을 더블클릭해도 원본이 열려요.",
                  foreground=COLORS["muted"]).grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))
        dialog.bind("<Return>", lambda event: save())
        form.entries[0].focus_set()

    def apply(self):
        changes = []
        for key in self.table.selection():
            path = self.rows[key][0]
            if key in self.targets:
                changes.append((key, path, self.targets[key]))
        if not changes:
            self.notice("변경 가능한 행을 선택하세요.")
            return
        count = len(changes)
        prompt = f"선택한 PDF {count}개를 발행월 폴더로 옮기고 이름을 바꾼 뒤 마크다운(.md)도 함께 저장할까요?"
        if not self.by_month.get() and not self.inbox_mode:
            prompt = f"선택한 PDF {count}개의 파일명을 바꾸고 마크다운(.md)도 함께 저장할까요?"
        if not ask_yes_no("PDF 정리", prompt):
            return
        errors = []
        history = []
        markdown = 0
        busy = Busy(self.root, "파일명을 바꾸고 있어요")
        for position, (key, path, target) in enumerate(changes, 1):
            busy.step(position, count, path.name)
            try:
                if path.is_symlink() or not target.resolve().is_relative_to(self.folder.resolve()):
                    raise OSError("선택한 인보이스 폴더 안의 파일과 경로만 정리할 수 있어요.")
                checksum, saved, warning = organize_invoice(path, target, self.rows[key][1], self.rows[key][3])
                markdown += int(saved)
                if warning:
                    errors.append(f"{path.name}: {warning}")
            except (OSError, RuntimeError, ValueError) as error:
                record_error("file_move", error)
                errors.append(f"{path.name}: {error}")
                continue
            self.rows[key] = (target, *self.rows[key][1:])
            self.invoice_like[target] = self.invoice_like.pop(path, True)
            self.checks[target] = self.checks.pop(path, {})
            history.append((path, target, checksum))
        busy.close()
        if history:
            self.last_moves = history
        self.refresh()
        self.status.set(f"{len(history)}개 변경 완료 · 마크다운 {markdown}개 저장 · 확인/실패 {len(errors)}개")
        if errors:
            messagebox.showerror("변경 실패", "\n".join(errors[:8]))
        elif history:
            self.notice(f"파일 {len(history)}개의 이름을 바꾸고 마크다운 {markdown}개를 PDF 옆에 저장했어요.\n"
                        "다음: 왼쪽 ‘2. 명세서 불러오기’로 가세요.", title="정리 완료")

    def undo(self):
        if not self.last_moves:
            self.notice("되돌릴 정리 작업이 없어요.")
            return
        if not ask_yes_no("정리 되돌리기", f"마지막 작업에서 옮긴 PDF {len(self.last_moves)}개를 되돌릴까요?"):
            return
        with Busy(self.root, "정리를 되돌리고 있어요") as busy:
            restored, failed = undo_moves(self.last_moves, busy.step)
        self.last_moves = [item[:3] for item in reversed(failed)]
        paths = dict(restored)
        if self.folder:
            for key, row in list(self.rows.items()):
                if row[0] in paths:
                    self.rows[key] = (paths[row[0]], *row[1:])
            self.load_folder()
        self.status.set(f"{len(restored)}개 복원 · 실패 {len(failed)}개")
        if failed:
            messagebox.showwarning("복원 실패", "\n".join(f"{item[1].name}: {item[3]}" for item in failed[:8]))

    def open_close(self):
        from monthly_close import MonthlyClose

        if getattr(self, "close_window", None) and self.close_window.winfo_exists():
            self.close_window.lift()
            return
        self.close_window = MonthlyClose(self.root, self.folder, self.practice_folder).root

    def view_contents(self):
        """Show the original page and its Markdown text side by side inside the window."""
        selection = list(self.table.selection())
        if not selection:
            self.notice("볼 파일을 한 개 이상 선택하세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("원본·내용 보기")
        dialog.geometry("1200x700")
        dialog.grab_set()
        top = tk.Frame(dialog, background=COLORS["bg"], padx=14, pady=8)
        top.pack(fill="x")
        name = tk.StringVar()
        tk.Label(top, textvariable=name, font=FONT_BOLD, background=COLORS["bg"], anchor="w").pack(side="left")
        footer = tk.Frame(dialog, background=COLORS["bg"], padx=14, pady=10)
        footer.pack(side="bottom", fill="x")
        panes = ttk.Panedwindow(dialog, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=14)
        left = tk.Frame(panes, background="#e9edf2")
        right = tk.Frame(panes, background=COLORS["panel"])
        panes.add(left, weight=1)
        panes.add(right, weight=1)
        canvas = tk.Canvas(left, background="#e9edf2", highlightthickness=0)
        scroll = ttk.Scrollbar(left, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        text = tk.Text(right, wrap="word", font=("Consolas", 10))
        text.pack(fill="both", expand=True)
        make_copyable(text)
        page_info = tk.StringVar()
        state = {"file": 0, "page": 0, "image": None, "content": ""}

        def render():
            canvas.delete("all")
            path, fields, _, number = self.rows[selection[state["file"]]]
            name.set(f"{state['file'] + 1}/{len(selection)} · {path.name}")
            try:
                if path.suffix.lower() == ".xml":
                    canvas.create_text(20, 20, anchor="nw", text="XRechnung XML은 그림 원본이 없어요. 오른쪽 내용을 확인하세요.")
                    pages = 0
                else:
                    with pymupdf.open(path) as document:
                        pages = len(document)
                        state["page"] = max(0, min(state["page"], pages - 1))
                        page = document[state["page"]]
                        width = max(320, canvas.winfo_width() - 20)
                        zoom = min(2.5, width / max(1, page.rect.width))
                        png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
                    state["image"] = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
                    canvas.create_image(10, 10, anchor="nw", image=state["image"])
                    canvas.configure(scrollregion=(0, 0, state["image"].width() + 20, state["image"].height() + 20))
                page_info.set(f"{state['page'] + 1} / {pages} 페이지" if pages else "")
                content, incomplete = markdown_text(path, fields, number)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("markdown", error)
                content, incomplete = f"읽지 못했어요: {error}", True
            state["content"] = content
            text.delete("1.0", "end")
            text.insert("1.0", content)
            hint.set("본문 일부를 읽지 못했어요 · 원본을 확인하세요" if incomplete else "")

        def move(kind, step):
            state[kind] += step
            if kind == "file":
                state["file"] %= len(selection)
                state["page"] = 0
            render()

        def save():
            path = self.rows[selection[state["file"]]][0]
            try:
                target = write_markdown(path, state["content"])
            except OSError as error:
                messagebox.showerror("마크다운 저장 실패", str(error), parent=dialog)
                return
            self.status.set(f"마크다운 저장 · {target.name}")
            hint.set(f"저장했어요: {target.name}")

        hint = tk.StringVar()
        ttk.Button(top, text="◀ 이전 파일", command=lambda: move("file", -1), style="Small.TButton").pack(side="right")
        ttk.Button(top, text="다음 파일 ▶", command=lambda: move("file", 1), style="Small.TButton").pack(side="right", padx=4)
        ttk.Button(footer, text="◀", command=lambda: move("page", -1), style="Small.TButton").pack(side="left")
        tk.Label(footer, textvariable=page_info, background=COLORS["bg"]).pack(side="left", padx=6)
        ttk.Button(footer, text="▶", command=lambda: move("page", 1), style="Small.TButton").pack(side="left")
        tk.Label(footer, textvariable=hint, background=COLORS["bg"], foreground=COLORS["amber"]).pack(side="left", padx=12)
        ttk.Button(footer, text="마크다운 파일로 저장", style="Primary.TButton", command=save).pack(side="right")
        dialog.after(120, render)

    def save_markdown(self):
        selection = self.table.selection()
        if not selection:
            self.notice("마크다운으로 저장할 PDF를 선택하세요.")
            return
        self.status.set("PDF 본문을 읽고 있어요…")
        self.root.update_idletasks()
        errors = []
        partial = 0
        for key in selection:
            path, fields, _, number = self.rows[key]
            try:
                _, incomplete = export_markdown(path, fields, number)
                partial += int(incomplete)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("markdown", error)
                errors.append(f"{path.name}: {error}")
        saved = len(selection) - len(errors)
        self.status.set(f"마크다운 {saved}개 저장 · 본문 확인 필요 {partial}개 · 실패 {len(errors)}개")
        if errors:
            messagebox.showerror("마크다운 저장 실패", "\n".join(errors[:8]), parent=self.root)
        else:
            extra = f"\n스캔 본문을 일부 못 읽은 파일이 {partial}개 있어요. ‘원본·내용 보기’로 확인하세요." if partial else ""
            self.notice(f"마크다운 {saved}개를 PDF와 같은 폴더에 같은 이름(.md)으로 저장했어요.{extra}", title="저장 완료")


def run_practice():
    examples = Path(__file__).resolve().parent / "Beispiele"
    if not examples.is_dir():
        raise FileNotFoundError("예시 자료 폴더가 없어요.")
    with tempfile.TemporaryDirectory(prefix="invoice-practice-") as folder:
        workspace = Path(folder)
        session = workspace / "Beispiele"
        shutil.copytree(examples, session)
        former = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(workspace / "data")
        try:
            from ui_shell import run_shell
            run_shell(practice_folder=session)
        finally:
            if former is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = former


if __name__ == "__main__":
    sys.modules["invoice_renamer"] = sys.modules[__name__]
    if "--practice" in sys.argv[1:]:
        run_practice()
    else:
        from ui_shell import run_shell
        run_shell()
