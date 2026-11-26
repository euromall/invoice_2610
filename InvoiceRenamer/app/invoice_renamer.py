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

from release_info import VERSION, export_diagnostics, record_error
from ui_theme import InlineDialog, show_notice


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
    r"Gesamtbetrag(?:\s+brutto)?|Gesamtsumme(?:\s+brutto)?|Bruttosumme|Rechnungssumme|Endsumme|"
    r"Zahlbetrag|Zahlungsbetrag|zu\s+zahlender\s+Betrag|zu\s+zahlen|Gesamt|Summe(?:\s+brutto)?|"
    r"Total\s+(?:due|amount)|Amount\s+due|Balance\s+due|Total"
)
CURRENCY = r"EUR|USD|GBP|KRW|CHF|€|\$|£|₩|원"
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
    currency = {"€": "EUR", "£": "GBP", "₩": "KRW", "원": "KRW"}.get(currency, currency)
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


def invoice_fields(layouts):
    dates = []
    amounts = []
    fallback_amounts = []
    known_sellers = header_sellers()
    sellers = []
    alternate_dates = []
    for layout in layouts:
        dates.extend(re.findall(rf"(?im)\b(?:{DATE_LABEL})\s*[:：]?[ \t]*(?:\n[ \t]*)?({DATE})\b", layout))
        for line in layout.splitlines():
            fallback = re.search(rf"(?i)^\s*Rechnung\b(?:\s+Nr\.?\s*:?\s*[\w/-]+)?[ \t]+(?:[^\d\n]+?\bden[ \t]+)?({DATE})\b", line)
            if fallback:
                alternate_dates.append(fallback[1])
        for match in re.finditer(rf"(?im)\b(?:{TOTAL_LABEL})\b[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?{MONEY}", layout):
            amounts.append(normalized_money(match))
        for match in re.finditer(rf"(?im)\b(?:{TOTAL_LABEL_FALLBACK})\b[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?{MONEY}", layout):
            fallback_amounts.append(normalized_money(match))
        sellers.extend(re.findall(rf"(?im)^\s*(?:{VENDOR_LABEL})\s*[:：][ \t]*([^\n]+)", layout))
        header = "\n".join(line for line in layout.splitlines() if line.strip())
        header = "\n".join(header.splitlines()[:12])
        for pattern, name in known_sellers:
            if re.search(f"(?im){pattern}", header):
                sellers.append(name)
    return (
        unique(filter(None, map(normalized_date, dates or alternate_dates))),
        unique(" ".join(seller.split()) for seller in sellers),
        unique(amounts) if amounts else unique(fallback_amounts),
    )


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
    fields = invoice_fields(layouts)
    text = layouts[0]
    if len(re.findall(r"\w", text)) >= 30 and not scan_note:
        score = invoice_likeness(text) + (2 if fields[2] else 0)
        info["invoice_like"] = score >= 3
    numbers = []
    correction = False
    for layout in layouts:
        numbers.extend(re.findall(
            r"(?im)\b(?:Rechnungsnummer|Rechnung\s+Nr\.?|Invoice\s+(?:No\.?|Number|#)|인보이스\s*번호)\s*[:#]?[ \t]*(?:\n[ \t]*)?([\w/-]*\d[\w/-]*)\b",
            layout,
        ))
        correction |= bool(re.search(r"(?im)^\s*(?:Gutschrift|Stornorechnung|Credit Note)\b", layout))
    override = load_invoice_edits().get(digest(path))
    number = unique(numbers)
    if override:
        fields, scan_note = tuple(override[:3]), ""
        info["invoice_like"] = True
        if len(override) > 3:
            number = override[3]
    return fields, scan_note, number, correction


def read_invoice(path):
    fields, note, _, _ = read_invoice_details(path)
    return fields, note


def source_search_terms(value, kind):
    if kind == "발행일":
        day = date.fromisoformat(value)
        return (value, day.strftime("%d.%m.%Y"), day.strftime("%d.%m.%y"), day.strftime("%d/%m/%Y"))
    if kind == "총액":
        raw = value.split()[0]
        amount = Decimal(raw)
        dot = format(amount, ".2f")
        comma = dot.replace(".", ",")
        grouped = format(amount, ",.2f")
        return tuple(dict.fromkeys((grouped.translate(str.maketrans(",.", ".,")), grouped, comma, dot, raw)))
    return (value,)


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
        area = pymupdf.Rect(coordinates)
        clip = pymupdf.Rect(page.rect.x0, area.y0 - 65, page.rect.x1, area.y1 + 65) & page.rect
        page.draw_rect(area + (-2, -2, 2, 2), color=(0.9, 0.38, 0.05), width=1.8, overlay=True)
        scale = min(2.0, 750 / max(1, clip.width))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False).tobytes("png")


def show_source_locations(parent, path, fields):
    window = InlineDialog(parent)
    window.title("추출값 원본 위치")
    window.transient(parent)
    window.grab_set()
    controls = ttk.Frame(window, padding=10)
    controls.pack(fill="x")
    choice = ttk.Combobox(controls, values=list(fields), state="readonly", width=13)
    choice.pack(side="left")
    choice.current(0)
    display = ttk.Label(window, width=85, padding=8)
    display.pack(fill="x")
    image_label = ttk.Label(window, padding=8)
    image_label.pack()
    hits = []
    position = 0

    def show(delta=0):
        nonlocal position
        display.configure(text="")
        if not hits:
            image_label.configure(image="")
            image_label.image = None
            display.configure(text="원본에서 같은 표기를 찾지 못했어요. PDF 원본을 직접 확인하세요.")
            return
        position = (position + delta) % len(hits)
        page, box, matched = hits[position]
        display.configure(text=f"{page + 1}페이지 · 표기 {matched} · 후보 {position + 1}/{len(hits)}")
        try:
            image_label.image = tk.PhotoImage(data=base64.b64encode(source_preview(path, page, box)).decode("ascii"))
            image_label.configure(image=image_label.image)
        except (OSError, ValueError, RuntimeError, tk.TclError) as error:
            image_label.configure(image="")
            display.configure(text=f"미리보기 실패 · {error}")

    def refresh(_event=None):
        nonlocal hits, position
        kind = choice.get()
        value = fields.get(kind)
        position = 0
        try:
            hits = source_locations(path, value, kind) if value else []
        except (OSError, ValueError, RuntimeError) as error:
            hits = []
            display.configure(text=f"원본 위치 검색 실패 · {error}")
            image_label.configure(image="")
            return
        show()

    for text, callback in (("이전 위치", lambda: show(-1)), ("다음 위치", lambda: show(1))):
        ttk.Button(controls, text=text, command=callback).pack(side="left", padx=4)
    ttk.Label(window, text="같은 날짜나 금액이 여러 곳에 있으면 모두 표시해요. 주위 문구도 확인하세요.", padding=8).pack(anchor="w")

    def close():
        window.destroy()
        if isinstance(parent, tk.Toplevel):
            parent.grab_set()

    window.protocol("WM_DELETE_WINDOW", close)
    choice.bind("<<ComboboxSelected>>", refresh)
    refresh()


def markdown_value(value):
    return re.sub(r"([\\&<>\[\]*_~\x60])", r"\\\1", " ".join(value.split()))


def export_markdown(path, fields, number):
    issued, seller, total = fields
    lines = ["# 인보이스", "", "- PDF 파일명: " + markdown_value(path.name)]
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
    content = "\n".join(lines) + "\n"
    target = path.with_suffix(".md")
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
        return target, incomplete


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


def undo_moves(history):
    restored, failed = [], []
    for original, current, checksum in reversed(history):
        try:
            if current.is_symlink() or digest(current) != checksum:
                raise OSError("이동한 파일이 변경됐어요.")
            original.parent.mkdir(parents=True, exist_ok=True)
            move_without_overwrite(current, original)
        except OSError as error:
            failed.append((original, current, checksum, str(error)))
        else:
            restored.append((current, original))
    return restored, failed


def organize_invoice(source, target, fields, number):
    checksum = digest(source)
    remember_original_name(source, checksum)
    target.parent.mkdir(exist_ok=True)
    move_without_overwrite(source, target)
    try:
        _, incomplete = export_markdown(target, fields, number)
    except (OSError, RuntimeError, ValueError) as error:
        try:
            move_without_overwrite(target, source)
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
        self.last_moves = []
        self.inbox_mode = False
        self.folder = None
        self.practice_folder = practice_folder
        self.on_change = on_change
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
            "인보이스 폴더를 열면 발행일·업체명·총액·인보이스 번호를 읽어요. "
            "빨간 행은 필수 항목을 못 읽은 파일, 회색 행은 인보이스가 아닌 것 같은 파일이에요.")).pack(anchor="w")
        toolbar = ttk.Frame(container, padding=(18, 8, 18, 4))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="폴더 열기", command=self.choose).pack(side="left")
        ttk.Button(toolbar, text="새 인보이스 가져오기 (미분류)", command=self.open_inbox).pack(side="left", padx=6)
        ttk.Button(toolbar, text="선택 항목 편집", command=self.edit).pack(side="left", padx=6)
        self.apply_button = ttk.Button(toolbar, text="선택 항목 파일명 변경", command=self.apply, style="Primary.TButton")
        self.apply_button.pack(side="left", padx=6)
        more_menu(toolbar, "더보기 ▾", [
            ("인보이스 아닌 파일 → 제외 폴더", self.exclude_selected),
            ("마크다운 저장", self.save_markdown),
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
        self.status = tk.StringVar(value="폴더를 선택하세요. 여러 행은 Ctrl 또는 Shift로 선택할 수 있어요.")
        if container is root:
            ttk.Label(root, textvariable=self.status, style="Status.TLabel").pack(fill="x", side="bottom")
        frame = ttk.Frame(container, padding=(18, 0, 18, 8))
        frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(frame, columns=("old", "new", "status"), show="headings")
        for column, label, width in zip(self.table["columns"], ("현재 파일명", "변경할 파일명", "읽기 결과·상태"), (320, 470, 260)):
            self.table.heading(column, text=label)
            self.table.column(column, width=width, minwidth=110)
        configure_tags(self.table)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", lambda event: self.edit())
        self.table.bind("<Control-a>", lambda event: self.table.selection_set(self.table.get_children()))
        if practice_folder:
            self.folder = practice_folder / "Belege"
            self.load_folder()
            self.status.set("연습 모드 · 예시 증빙을 열었어요. 왼쪽 ‘2. 명세서 불러오기’에서 예시 명세서도 불러올 수 있어요.")
        else:
            self.root.after_idle(self.first_check)

    def notice(self, message, kind="info"):
        """Show a guidance message as a pop-up inside the window and keep it in the status line."""
        self.status.set(message)
        show_notice(self.root, message, kind=kind)

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
        self.status.set(f"{inbox} · 정리 가능한 {len(self.targets)}개 선택됨 · 경로 확인 후 ‘정리하고 마크다운 저장’")

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
                and path.resolve().is_relative_to(root) and EXCLUDED_FOLDER not in path.relative_to(self.folder).parts[:-1]
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
        for index, path in enumerate(paths):
            if path in scanned:
                self.rows[str(index)] = scanned[path]
                self.table.insert("", "end", iid=str(index))
                continue
            if index % 5 == 0:
                self.status.set(f"증빙을 읽고 있어요… {index + 1}/{len(paths)}")
                self.root.update_idletasks()
            info = {}
            try:
                fields, note, number, _ = read_invoice_details(path, info)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("pdf_read", error)
                fields, note, number = ("", "", ""), "PDF 읽기 실패", ""
            self.invoice_like[path] = info.get("invoice_like", True)
            self.rows[str(index)] = (path, fields, note, number)
            self.table.insert("", "end", iid=str(index))
        self.refresh()
        self.status.set(f"{self.folder}  |  증빙 {len(paths)}개")

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
        if small:
            return "번호 없음 · 250€ 이하 소액영수증 확인", "warn"
        return "", "ok"

    def refresh(self):
        organize = self.inbox_mode or self.by_month.get()
        button = "정리하고 마크다운 저장" if self.inbox_mode else "선택 항목 정리" if organize else "선택 항목 파일명 변경"
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
            self.table.item(key, values=(source_label, target_label, status), tags=(tag,))
            visible = (view == "전체" or (view == "필수 누락" and tag == "bad")
                       or (view == "인보이스 아님 의심" and tag == "muted") or (view == "변경 가능" and key in self.targets))
            if visible:
                shown.append(key)
        self.table.detach(*self.table.get_children())
        for index, key in enumerate(shown):
            self.table.move(key, "", index)
        self.summary = counts
        if self.on_change:
            self.on_change()

    def exclude_selected(self):
        selection = [key for key in self.table.selection() if key in self.rows]
        if not selection or self.folder is None:
            self.notice("제외 폴더로 옮길 파일을 선택하세요. ‘인보이스 아님 의심’ 보기에서 고르면 편해요.")
            return
        if not messagebox.askyesno("제외 폴더로 이동", f"선택한 파일 {len(selection)}개를 ‘{EXCLUDED_FOLDER}’ 폴더로 옮길까요?\n"
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
        for key in selection:
            path = self.rows[key][0]
            target = reserve_name(target_folder, path.name, path, occupied)
            try:
                if target is None:
                    raise OSError("파일명이 너무 길어요.")
                checksum = digest(path)
                move_without_overwrite(path, target)
                history.append((path, target, checksum))
            except OSError as error:
                errors.append(f"{path.name}: {error}")
        if history:
            self.last_moves = history
        self.load_folder()
        self.status.set(f"제외 폴더로 {len(history)}개 이동 · 실패 {len(errors)}개")
        if errors:
            messagebox.showerror("이동 실패", "\n".join(errors[:8]), parent=self.root)

    def edit(self):
        selection = self.table.selection()
        if len(selection) != 1:
            self.notice("편집할 PDF 한 개를 선택하세요.")
            return
        key = selection[0]
        path, fields, _, number = self.rows[key]
        dialog = InlineDialog(self.root)
        dialog.title("인보이스 정보 편집")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack()
        ttk.Label(body, text=path.name).grid(row=0, columnspan=2, sticky="w", pady=(0, 8))
        entries = []
        for index, (label, value) in enumerate(zip(
            ("발행일 (YYYY-MM-DD)", "업체명", "총액 (예: 58.39 EUR)", "인보이스 번호 (선택)"), (*fields, number)
        ), 1):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", padx=(0, 12), pady=4)
            entry = ttk.Entry(body, width=40)
            entry.insert(0, value)
            entry.grid(row=index, column=1)
            entries.append(entry)

        def save():
            values = tuple(entry.get().strip() for entry in entries)
            amount = re.fullmatch(MONEY, values[2], re.IGNORECASE)
            if not re.fullmatch(DATE, values[0]) or not normalized_date(values[0]) or not values[1] or not amount:
                messagebox.showerror("입력 확인", "발행일, 업체명, 통화가 포함된 총액을 입력하세요.", parent=dialog)
                return
            edited = normalized_date(values[0]), values[1], normalized_money(amount)
            if not proposed_name(edited):
                messagebox.showerror("입력 확인", "파일명에 사용할 업체명을 입력하세요.", parent=dialog)
                return
            try:
                save_invoice_edit(path, (*edited, values[3]))
            except (OSError, ValueError) as error:
                messagebox.showerror("수정값 저장 실패", str(error), parent=dialog)
                return
            self.rows[key] = (path, edited, "", values[3])
            self.invoice_like[path] = True
            self.refresh()
            dialog.destroy()

        ttk.Button(body, text="원본 위치 확인", command=lambda: show_source_locations(
            dialog, path, {"발행일": fields[0], "총액": fields[2], "인보이스 번호": number},
        )).grid(row=5, column=0, sticky="w", pady=(10, 0))
        ttk.Button(body, text="저장", command=save).grid(row=5, column=1, sticky="e", pady=(10, 0))
        dialog.bind("<Return>", lambda event: save())
        entries[0].focus_set()

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
        prompt = f"선택한 PDF {count}개를 발행월 폴더로 옮기고 가능한 파일의 이름을 변경할까요?"
        if not self.by_month.get():
            prompt = f"선택한 PDF {count}개의 파일명을 변경할까요?"
        if self.inbox_mode:
            prompt = f"선택한 PDF {count}개의 이름을 변경하고 발행월별로 옮긴 뒤 마크다운을 저장할까요?"
        if not messagebox.askyesno("PDF 정리", prompt):
            return
        errors = []
        history = []
        markdown = 0
        for key, path, target in changes:
            try:
                if path.is_symlink() or not target.resolve().is_relative_to(self.folder.resolve()):
                    raise OSError("선택한 인보이스 폴더 안의 파일과 경로만 정리할 수 있어요.")
                if self.inbox_mode:
                    checksum, saved, warning = organize_invoice(path, target, self.rows[key][1], self.rows[key][3])
                    markdown += int(saved)
                    if warning:
                        errors.append(f"{path.name}: {warning}")
                else:
                    checksum = digest(path)
                    remember_original_name(path, checksum)
                    target.parent.mkdir(exist_ok=True)
                    move_without_overwrite(path, target)
            except OSError as error:
                record_error("file_move", error)
                errors.append(f"{path.name}: {error}")
                continue
            self.rows[key] = (target, *self.rows[key][1:])
            self.invoice_like[target] = self.invoice_like.pop(path, True)
            history.append((path, target, checksum))
        if history:
            self.last_moves = history
        self.refresh()
        detail = f" · 마크다운 {markdown}개 저장" if self.inbox_mode else ""
        self.status.set(f"{len(history)}개 변경 완료{detail} · 확인/실패 {len(errors)}개")
        if errors:
            messagebox.showerror("변경 실패", "\n".join(errors[:8]))

    def undo(self):
        if not self.last_moves:
            self.notice("되돌릴 정리 작업이 없어요.")
            return
        if not messagebox.askyesno("정리 되돌리기", f"마지막 작업에서 옮긴 PDF {len(self.last_moves)}개를 되돌릴까요?"):
            return
        restored, failed = undo_moves(self.last_moves)
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
        self.status.set(f"마크다운 {len(selection) - len(errors)}개 저장 · 본문 확인 필요 {partial}개 · 실패 {len(errors)}개")
        if errors:
            messagebox.showerror("마크다운 저장 실패", "\n".join(errors[:8]), parent=self.root)


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
