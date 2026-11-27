import csv
import base64
import hashlib
import shutil
import getpass
import io
import json
import os
import re
import tempfile
import tkinter as tk
import webbrowser
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
from xml.etree.ElementTree import ParseError

import pymupdf
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import quote, urlencode

from invoice_renamer import DATE, EVIDENCE_EXTENSIONS, MoneyEntry, pick_value, import_mode, make_copyable, open_file, source_preview, MONEY, MONTH_FOLDER, STATEMENT_FOLDER, digest, evidence_stem, filename_fields, invoice_edits_path, load_invoice_edits, move_evidence, move_without_overwrite, open_evidence, number_label, normalized_date, normalized_money, original_stem, read_invoice_details, remember_original_name, read_pdf_pages, show_source_locations, undo_moves
from tax_documents import pdf_tax_fields, read_structured
from advisor_workbook import advisor_workbook, localized_csv
from release_info import VERSION, record_error
from ui_theme import (COLORS, FONT_BOLD, FONT_SMALL, FONT_TITLE, Busy, InlineDialog, ask_yes_no, configure_tags, resort, show_notice,
                      show_toast, table_tools, transaction_tag)
from workspace_backup import add_attachment, attachment_path, create_backup, restore_workspace


@dataclass(frozen=True)
class Transaction:
    key: str
    day: date
    merchant: str
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Invoice:
    key: str
    path: Path
    day: date | None
    seller: str
    amount: Decimal | None
    currency: str
    number: str
    credit: bool
    tax_fields: dict | None = None


WESTERN_CHARACTERS = set("äöüÄÖÜßéèêëàâáçñóòôõúùûíìîïÉÈÀÁÇÑÓÚ€£°§´’‘“”„–—…·µ×\u00a0")


def decode_statement(data, legacy=False):
    """Decode bank exports. German Latin-1/cp1252 is preferred over cp949 when it reads cleanly.

    legacy=True keeps the pre-1.1.1 order for files that were already imported, because
    transaction keys include the merchant text and must stay the same.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if legacy:
        for encoding in ("utf-8-sig", "cp949", "cp1252"):
            try:
                return data.decode(encoding)
            except UnicodeError:
                continue
        raise ValueError("CSV 문자 인코딩을 읽을 수 없어요.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeError:
        pass
    western = None
    try:
        western = data.decode("cp1252")
    except UnicodeError:
        pass
    if western is not None:
        special = [char for char in western if ord(char) > 127]
        if special and sum(char in WESTERN_CHARACTERS for char in special) / len(special) >= 0.9:
            return western
    try:
        return data.decode("cp949")
    except UnicodeError:
        pass
    if western is not None:
        return western
    raise ValueError("CSV 문자 인코딩을 읽을 수 없어요.")


def read_csv(path, legacy=False):
    content = decode_statement(path.read_bytes(), legacy)
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(content), dialect))
    if len(rows) < 2 or len(rows[0]) < 3:
        raise ValueError("첫 행에는 열 이름이, 다음 행부터는 카드 거래가 있어야 해요.")
    return rows[0], rows[1:]


def read_statement_pdf(path):
    groups = []
    with pymupdf.open(path) as document:
        if len(document):
            first = document[0].get_text(sort=True)
            if "americanexpress.de" in first.lower() and "Abrechnungszeitraum" in first:
                return read_amex_statement(document, first)
            if "Deutsche Kreditbank AG" in first and "Ihre Abrechnung vom" in first:
                return read_dkb_statement(document)
            if "Deutsche Bank AG" in first and "Alter Saldo per" in first:
                return read_deutsche_bank_statement(document, first)
        for page in document:
            tables = page.find_tables().tables or page.find_tables(
                vertical_strategy="text", horizontal_strategy="text", min_words_vertical=2
            ).tables
            for table in tables:
                rows = [[" ".join((cell or "").split()) for cell in row] for row in table.extract()]
                if rows and len(rows[0]) >= 3 and any(
                    re.fullmatch(DATE, cell) for row in rows for cell in row
                ):
                    groups.append(rows)
    if not groups:
        raise ValueError("PDF 거래 표를 찾지 못했어요. 은행 또는 카드사 CSV를 사용하세요.")
    header = groups[0][0]
    result = []
    for index, rows in enumerate(groups):
        candidate = rows[0]
        if index == 0 or candidate == header:
            rows = rows[1:]
        if any(len(row) != len(header) for row in rows):
            raise ValueError("PDF에 서로 다른 표가 있어 거래 열을 확정할 수 없어요. CSV를 사용하세요.")
        result.extend(rows)
    if not result:
        raise ValueError("PDF 거래 행이 없어요.")
    return [value or f"Spalte {i}" for i, value in enumerate(header, 1)], result


def read_amex_statement(document, first_page):
    period = re.search(
        r"Abrechnungszeitraum\s+vom\s+\d{2}\.\d{2}\.(?:\d{4}|\d{2})\s+bis\s+(\d{2}\.\d{2}\.(?:\d{4}|\d{2}))",
        first_page,
    )
    if not period:
        raise ValueError("American Express 명세서의 종료일을 찾지 못했어요.")
    day, month, year = period[1].split(".")
    end = date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))

    def full_date(short):
        day, month = map(int, short.split("."))
        possibilities = []
        for year in (end.year - 1, end.year, end.year + 1):
            try:
                possibilities.append(date(year, month, day))
            except ValueError:
                continue
        return min(possibilities, key=lambda value: abs(value - end)).isoformat()

    start = r"^\s*(\d{2}\.\d{2})\s+(\d{2}\.\d{2})\s+"
    transaction = re.compile(start + r"(.+?)\s{2,}(-?[\d.]+,\d{2})(?:\s+(GUTSCHRIFT))?\s*$")
    rows = []
    for index, page in enumerate(document, 1):
        for line in page.get_text(sort=True).splitlines():
            match = transaction.fullmatch(line)
            if match:
                charge = match[4]
                if match[5]:
                    charge = "-" + charge.lstrip("-")
                rows.append([full_date(match[1]), match[3].strip(), charge, "EUR", full_date(match[2])])
            elif re.match(start, line):
                raise ValueError(f"American Express 명세서 {index}페이지에 읽지 못한 거래가 있어요.")
            elif rows and re.search(r"(?:^|\s)GUTSCHRIFT\s*$", line):
                rows[-1][2] = "-" + rows[-1][2].lstrip("-")
    if not rows:
        raise ValueError("American Express 명세서에서 거래를 찾지 못했어요.")
    return ["Umsatzdatum", "Beschreibung", "Betrag", "Währung", "Buchungsdatum"], rows


def read_dkb_statement(document):
    date_token = r"\d{2}\.\d{2}\.\d{2}"
    start = rf"^\s*({date_token})\s+({date_token})\s+"
    line_pattern = re.compile(start + r"(.+?)\s{2,}([\d.]+,\d{2})\s+([SH])\s*$")
    rows = []
    for index, page in enumerate(document, 1):
        for line in page.get_text(sort=True).splitlines():
            match = line_pattern.fullmatch(line)
            if not match:
                if re.match(start, line):
                    raise ValueError(f"DKB 명세서 {index}페이지에 읽지 못한 거래가 있어요.")
                continue
            purchase, booking, details, amount, side = match.groups()
            dates = [normalized_date(value[:-2] + "20" + value[-2:]) for value in (purchase, booking)]
            if not all(dates):
                raise ValueError(f"DKB 명세서 {index}페이지의 거래일을 읽지 못했어요.")
            rows.append([dates[0], details, ("-" if side == "H" else "") + amount, "EUR", dates[1]])
    if not rows:
        raise ValueError("DKB 명세서에서 거래를 찾지 못했어요.")
    return ["Kaufdatum", "Beschreibung", "Betrag", "Währung", "Buchungsdatum"], rows


def read_deutsche_bank_statement(document, first):
    opening = re.search(r"Alter Saldo per\s+(\d{2}\.\d{2}\.\d{4})", first)
    if not opening:
        raise ValueError("Deutsche Bank 명세서에서 시작 기준일을 찾지 못했어요.")
    reference = date.fromisoformat(normalized_date(opening[1]))
    start = r"^\s*(\d{2}\.\d{2})\.\s+(\d{2}\.\d{2})\.\s+"
    line_pattern = re.compile(start + r"(.+?)\s{2,}([+-])\s*([\d.]+,\d{2})\s*$")
    continuation = re.compile(r"^\s*(\d{4})\s+(\d{4})\s+(.+?)\s*$")
    rows = []
    for index, page in enumerate(document, 1):
        lines = page.get_text(sort=True).splitlines()
        for position, line in enumerate(lines):
            match = line_pattern.fullmatch(line)
            if not match:
                if re.match(start, line):
                    raise ValueError(f"Deutsche Bank 명세서 {index}페이지에 읽지 못한 거래가 있어요.")
                continue
            booked, valued, details, side, amount = match.groups()
            following = continuation.fullmatch(lines[position + 1]) if position + 1 < len(lines) else None
            if following:
                dates = [normalized_date(f"{short}.{year}") for short, year in zip((booked, valued), following.groups()[:2])]
                details = f"{details} | {following[3]}"
            else:
                dates = []
                for short in (booked, valued):
                    options = [normalized_date(f"{short}.{year}") for year in (reference.year - 1, reference.year, reference.year + 1)]
                    dates.append(min(filter(None, options), key=lambda value: abs(date.fromisoformat(value) - reference), default=""))
            if not all(dates):
                raise ValueError(f"Deutsche Bank 명세서 {index}페이지의 거래일을 읽지 못했어요.")
            # Match the card convention used by this app. Expenses are positive.
            rows.append([dates[0], details, ("" if side == "-" else "-") + amount, "EUR", dates[1]])
    if not rows:
        raise ValueError("Deutsche Bank 명세서에서 거래를 찾지 못했어요.")
    return ["Buchungsdatum", "Beschreibung", "Betrag", "Währung", "Valuta"], rows


def german_amount(value):
    """58.39 -> '58,39', 1204.55 -> '1.204,55' (sign dropped)."""
    return format(abs(value), ",.2f").translate(str.maketrans(",.", ".,"))


def statement_lines(page):
    """Words of a PDF page grouped into visual lines: [(rect, [words])]."""
    lines = []
    for word in sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        middle = (word[1] + word[3]) / 2
        if lines and abs(lines[-1][2] - middle) <= 2.5:
            lines[-1][1].append(word)
        else:
            lines.append([None, [word], middle])
    result = []
    for _, words, _ in lines:
        words.sort(key=lambda w: w[0])
        rect = pymupdf.Rect(min(w[0] for w in words), min(w[1] for w in words),
                            max(w[2] for w in words), max(w[3] for w in words))
        result.append((rect, [w[4] for w in words]))
    return result


def numbered_statement(path, entries, placed=None):
    """Return (pdf bytes, marked count) for a copy of a PDF statement with transaction numbers.

    placed: optional dict that receives {number: page index} for every marked number.

    entries: [(number, Transaction)]. A line is marked when it starts with the transaction's
    day.month and contains its amount. The number goes right of the amount (the end of the line),
    or left of the line or above it when the page margin is too narrow. The original file is not changed.
    """
    remaining = sorted(entries, key=lambda entry: (entry[1].day, entry[0]))
    marked = 0
    red = (0.78, 0.19, 0.19)
    with pymupdf.open(path) as document:
        for page in document:
            for rect, words in statement_lines(page):
                if not words:
                    continue
                first = words[0]
                for position, (number, item) in enumerate(remaining):
                    amount = german_amount(item.amount)
                    if not first.startswith(item.day.strftime("%d.%m")):
                        continue
                    if not any(word.lstrip("+-") == amount or word.lstrip("+-").rstrip("SH") == amount for word in words):
                        continue
                    label = f"[{number}]"
                    size = 9
                    width = pymupdf.get_text_length(label, fontname="hebo", fontsize=size)
                    beside = True
                    if page.rect.x1 - rect.x1 >= width + 6:  # right after the amount
                        x = rect.x1 + 4
                    elif rect.x0 - page.rect.x0 >= width + 6:
                        x = rect.x0 - width - 3
                    else:
                        beside = False
                        x = max(page.rect.x0 + 2, rect.x0)
                        page.draw_rect(pymupdf.Rect(x - 1, rect.y0 - size - 1, x + width + 1, rect.y0), color=None,
                                       fill=(1, 1, 1), overlay=True)
                        page.insert_text((x, rect.y0 - 1.5), label, fontsize=size, fontname="hebo", color=red)
                    if beside:
                        page.insert_text((x, rect.y1 - 1.5), label, fontsize=size, fontname="hebo", color=red)
                    if placed is not None:
                        placed[number] = page.number
                    marked += 1
                    del remaining[position]
                    break
        if len(document):
            first_page = document[0]
            note = f"Kopie mit Belegnummern ({marked} von {len(entries)} markiert) - Original unverändert im ZIP"
            first_page.insert_text((first_page.rect.x0 + 12, first_page.rect.y0 + 10), note, fontsize=6.5,
                                   fontname="helv", color=red)
        data = document.tobytes(garbage=3, deflate=True)
    return data, marked


def stamp_page(page, label, size=10):
    """Write label in a white box at the bottom centre of the page as the reader sees it (also on rotated pages)."""
    width = pymupdf.get_text_length(label, fontname="hebo", fontsize=size)
    visible = page.rect  # already the size as shown, rotation applied
    box = pymupdf.Rect(visible.x0 + (visible.width - width) / 2 - 4, visible.y1 - size - 10,
                       visible.x0 + (visible.width + width) / 2 + 4, visible.y1 - 4)
    box = box * page.derotation_matrix
    box.normalize()
    page.draw_rect(box, color=(0.78, 0.19, 0.19), fill=(1, 1, 1), width=0.6, overlay=True)
    point = pymupdf.Point(visible.x0 + (visible.width - width) / 2, visible.y1 - 8) * page.derotation_matrix
    page.insert_text(point, label, fontsize=size, fontname="hebo", color=(0.78, 0.19, 0.19), rotate=page.rotation)


def numbered_evidence(path, numbers):
    """A copy of an evidence file (PDF or photo) as a PDF document with ‘608-004’ (or ‘608-012 / 608-013’)
    stamped at the bottom centre of every page. The caller closes the document. The original is not changed."""
    document = open_evidence(path)
    if not document.is_pdf:
        converted = pymupdf.open("pdf", document.convert_to_pdf())
        document.close()
        document = converted
    label = " / ".join(numbers)
    for page in document:
        stamp_page(page, label)
    return document


def info_page(target, lines, title="Hinweis"):
    """Append an A4 page with a few lines of (German) text to a PDF document."""
    page = target.new_page(width=595, height=842)
    page.insert_text((56, 90), title, fontsize=16, fontname="hebo", color=(0.14, 0.22, 0.30))
    y = 124
    for line in lines:
        for part in [line[i:i + 95] for i in range(0, max(len(line), 1), 95)]:
            page.insert_text((56, y), part, fontsize=11, fontname="helv")
            y += 17
        y += 4
    return page


def statement_check(path, transactions):
    with pymupdf.open(path) as document:
        pages = [page.get_text(sort=True) for page in document]
    first = pages[0] if pages else ""
    text = "\n".join(pages)
    money = r"([\d.]+,\d{2})"
    def amount(value):
        return Decimal(value.replace(".", "").replace(",", "."))
    positive = sum((item.amount for item in transactions if item.amount > 0), Decimal(0))
    negative = -sum((item.amount for item in transactions if item.amount < 0), Decimal(0))
    if "americanexpress.de" in first.lower():
        pattern = r"(?m)^\s*" + r"\s+".join([money] * 5) + r"\s+-\s+\+\s+="
        summary = re.search(pattern, first)
        if summary:
            old, credits, charges, balance, _ = map(amount, summary.groups())
            ok = (positive, negative, old + positive - negative) == (charges, credits, balance)
            return (f"검산 {'일치' if ok else '불일치'} · 청구 {positive:.2f}/{charges:.2f} EUR · 차감 {negative:.2f}/{credits:.2f} EUR", not ok)
    dkb = "Deutsche Kreditbank AG" in first
    db = "Deutsche Bank AG" in first
    if dkb or db:
        if dkb:
            opening = re.search(r"Saldo letzte Abrechnung[^\n]*?" + money + r"\s+([SH])\b", first)
            closing = re.search(r"Neuer Saldo[^\n]*?" + money + r"\s+([SH])\b", text)
            def signed(match):
                return amount(match[1]) * (-1 if match[2] == "H" else 1)
        else:
            suffix = r"[^\n]*(?:\n[^\n]*){1,3}?\bEUR\s+([+-])\s*" + money
            opening = re.search(r"Alter Saldo per" + suffix, first)
            closing = re.search(r"Neuer Saldo" + suffix, text)
            def signed(match):
                return amount(match[2]) * (-1 if match[1] == "-" else 1)
        if opening and closing:
            original = signed(closing)
            computed = signed(opening) + (positive - negative if dkb else negative - positive)
            return (f"검산 {'일치' if computed == original else '불일치'} · 계산 잔액 {computed:.2f} / 원본 {original:.2f} EUR", computed != original)
    if "americanexpress.de" in first.lower() or dkb or db:
        return "원본 합계를 읽지 못했어요. 명세서와 직접 비교하세요.", True
    return "이 PDF 양식은 원본 합계 자동 검산을 지원하지 않아요.", False


def refund_balance(transactions):
    if len(transactions) < 2 or len({item.currency for item in transactions}) != 1:
        raise ValueError("같은 통화의 결제와 환불을 선택하세요.")
    if sum(item.amount > 0 for item in transactions) != 1 or any(item.amount == 0 for item in transactions):
        raise ValueError("결제 한 건과 음수로 표시된 환불을 한 건 이상 선택하세요.")
    remaining = sum(item.amount for item in transactions)
    if remaining < 0:
        raise ValueError("환불 합계가 결제 금액보다 커요. 선택한 거래를 확인하세요.")
    return remaining


def unique_pairs(pairs):
    left = Counter(a for a, _ in pairs)
    right = Counter(b for _, b in pairs)
    return [(a, b) for a, b in pairs if left[a] == right[b] == 1]


PERIOD_AUTO = "자동 (거래일의 월)"


def period_choices(today=None):
    """Choices for the closing month: automatic, then this month back to 24 months ago, and next month."""
    today = today or date.today()
    months = []
    year, month = today.year, today.month + 1
    if month == 13:
        year, month = year + 1, 1
    for _ in range(26):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return [PERIOD_AUTO, *months]


def ordered_accounts(names, settings=None):
    """Account order from settings.json → ‘account_order’, with accounts not listed yet added at the end."""
    if settings is None:
        try:
            from invoice_renamer import load_settings
            settings = load_settings()
        except Exception:
            settings = {}
    order = [name for name in settings.get("account_order") or [] if isinstance(name, str) and name]
    for name in names:
        if name and name not in order:
            order.append(name)
    return order


def save_account_order(order):
    from invoice_renamer import save_settings
    save_settings({"account_order": [name for name in order if name]})


def assign_numbers(records, transactions, period=""):
    counts = Counter()
    for record in records.values():
        if "number" in record and "period" in record:
            month = record["period"]
            counts[month] = max(counts[month], int(record["number"].rsplit("-", 1)[1]))
    for item in transactions:
        record = records.setdefault(item.key, {})
        if "number" not in record:
            month = period or item.day.strftime("%Y-%m")
            counts[month] += 1
            record["period"] = month
            record["number"] = f"{month[3]}{month[5:]}-{counts[month]:03d}"


def card_amount(raw, currency):
    raw = raw.strip().replace("\u202f", " ").replace("−", "-")
    negative = raw.startswith("(") and raw.endswith(")") or raw.endswith("-")
    if negative:
        raw = raw[1:-1] if raw.startswith("(") else raw[:-1].rstrip()
    match = re.fullmatch(MONEY, raw, re.IGNORECASE)
    if not match and re.fullmatch(r"-?[\d.,'’ \u00a0]+", raw):
        if re.fullmatch(r"[A-Za-z]{3}", currency):
            match = re.fullmatch(MONEY, f"{raw} EUR", re.IGNORECASE)
            if match:
                amount = Decimal(normalized_money(match).split()[0])
                return (-abs(amount) if negative else amount), currency.upper()
        else:
            match = re.fullmatch(MONEY, f"{raw} {currency}", re.IGNORECASE)
    if not match:
        raise ValueError(f"금액 또는 통화 확인 ({raw})")
    value, code = normalized_money(match).rsplit(" ", 1)
    amount = Decimal(value)
    return (-abs(amount) if negative else amount), code


def parse_transactions(rows, columns, default_currency, date_order, account="", flip_sign=False):
    date_col, merchant_col, amount_col, currency_col = columns
    transactions, errors = [], []
    occurrences = Counter()
    for line, row in enumerate(rows, 2):
        if not any(cell.strip() for cell in row):
            continue
        try:
            raw_date = row[date_col].strip()
            if not re.fullmatch(DATE, raw_date):
                raise ValueError(f"날짜 확인 ({raw_date})")
            parts = re.split(r"[-./]", raw_date)
            if len(parts[0]) != 4 and date_order == "MM/DD/YYYY":
                raw_date = f"{parts[1]}.{parts[0]}.{parts[2]}"
            day = date.fromisoformat(normalized_date(raw_date))
            merchant = row[merchant_col].strip()
            if not merchant:
                raise ValueError("가맹점이 비어 있어요.")
            currency = (row[currency_col].strip() if currency_col is not None else "") or default_currency
            amount, currency = card_amount(row[amount_col], currency)
            if flip_sign:
                amount = -amount
            identity = json.dumps([account, day.isoformat(), merchant, str(amount.normalize()), currency], ensure_ascii=False)
            occurrences[identity] += 1
            key = hashlib.sha256(f"{identity}:{occurrences[identity]}".encode()).hexdigest()
            transactions.append(Transaction(key, day, merchant, amount, currency))
        except (ValueError, IndexError) as error:
            errors.append((line, str(error)))
    return transactions, errors


MATCHED_FOLDER = r"매칭완료_\d{2}(?:0[1-9]|1[0-2])"


def index_pdfs(folder, progress=None):
    """progress(index, total, name) is called for each file (for a progress bar)."""
    result = []
    issues = []
    paths = list(folder.iterdir())
    for child in paths.copy():
        if child.is_dir() and not child.is_symlink() and re.fullmatch(MONTH_FOLDER, child.name):
            try:
                paths.extend(child.iterdir())
            except OSError:
                issues.append(child.name)
    for child in paths.copy():
        if child.is_dir() and not child.is_symlink() and re.fullmatch(MATCHED_FOLDER, child.name):
            try:
                paths.extend(child.iterdir())
            except OSError:
                issues.append(child.name)
    edits = load_invoice_edits()
    paths = sorted(paths)
    for position, path in enumerate(paths, 1):
        if not path.is_file() or path.suffix.lower() not in (EVIDENCE_EXTENSIONS | {".xml"}):
            continue
        if progress:
            progress(position, len(paths), path.name)
        try:
            key = digest(path)
        except OSError:
            issues.append(path.name)
            continue
        try:
            meta = read_structured(path) if path.suffix.lower() in (".pdf", ".xml") else {}
            if meta:
                day, seller, number, credit = (meta[k] for k in ("day", "seller", "number", "credit"))
                amount, currency = meta["amount"] or None, meta["currency"]
            elif path.suffix.lower() in EVIDENCE_EXTENSIONS:
                (day, seller, total), _, number, credit = read_invoice_details(path)
                named = filename_fields(path)
                if named and key not in edits:
                    day, seller, total = named
                amount, currency = total.rsplit(" ", 1) if total else (None, "")
                with open_evidence(path) as document:
                    meta = pdf_tax_fields("\n".join(page.get_text(sort=True) for page in document))
                # PDF headings alone do not determine the financial sign.
                if credit:
                    meta["special"] = "Gutschrift/Storno prüfen"
                credit = False
            else:
                issues.append(path.name)
                continue
            result.append(Invoice(key, path, date.fromisoformat(day) if day else None,
                                  seller, Decimal(amount) if amount is not None else None,
                                  currency, number, credit, meta))
        except (OSError, ValueError, RuntimeError, ParseError, ArithmeticError):
            result.append(Invoice(key, path, None, "", None, "", "", False))
            issues.append(path.name)
    return result, issues


def merchant_key(merchant):
    return "".join(char for char in merchant.casefold() if char.isalnum())


def amazon_marketplace(merchant, invoice):
    """Paid to Amazon (AMAZON EU, AMZN Mktp …) for an invoice of a marketplace seller: the names differ by design."""
    key = merchant_key(merchant)
    if "amazon" not in key and "amzn" not in key:
        return False
    fields = invoice.tax_fields or {}
    order = f"{fields.get('order', '')} {invoice.number} {invoice.path.name}"
    return bool(fields.get("amazon") or "amazon" in merchant_key(invoice.seller)
                or re.search(r"\b\d{3}-\d{7}-\d{7}\b", order) or "amazon" in invoice.path.name.casefold())


def signed_total(invoice):
    return -abs(invoice.amount) if invoice.credit and invoice.amount is not None else invoice.amount


def matching_reference(description, invoice):
    for label, value in (("인보이스 번호", invoice.number), ("주문번호", (invoice.tax_fields or {}).get("order", ""))):
        if len(value) >= 4 and any(char.isdigit() for char in value):
            if re.search(rf"(?<![^\W_]){re.escape(value)}(?![^\W_])", description, re.IGNORECASE):
                return label
    return ""


CANDIDATE_DAYS = 45  # step 3 lists same-amount evidence within this many days of the payment first


def candidates(transaction, invoices, aliases):
    merchant = merchant_key(transaction.merchant)
    known = aliases.get(merchant, [])
    ranked = []
    for invoice in invoices:
        if invoice.currency != transaction.currency or signed_total(invoice) != transaction.amount:
            continue
        distance = abs((invoice.day - transaction.day).days) if invoice.day else 9999
        seller = merchant_key(invoice.seller)
        named = bool(seller and (seller in known or seller in merchant or merchant in seller)) or amazon_marketplace(transaction.merchant, invoice)
        ranked.append((not matching_reference(transaction.merchant, invoice), not named,
                       distance, invoice.path.name.casefold(), invoice))
    return [row[-1] for row in sorted(ranked, key=lambda row: row[:-1])]


def gmail_url(merchant, day):
    safe = merchant.replace('"', " ").replace("\n", " ").replace("\r", " ")
    term = f"from:{safe}" if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", safe) else f'"{safe}"'
    search = f'has:attachment filename:pdf after:{day - timedelta(days=14)} before:{day + timedelta(days=15)} {term}'
    return f"https://mail.google.com/mail/u/0/#search/{quote(search, safe='')}"


MAILBOX_KINDS = {"gmail": "Gmail", "daum": "한메일"}


def mailboxes(settings=None):
    """Registered mailboxes from settings.json → ‘mailboxes’: [{"kind": "gmail"|"daum", "label", "index"}]."""
    if settings is None:
        try:
            from invoice_renamer import load_settings
            settings = load_settings()
        except Exception:
            settings = {}
    boxes = [box for box in settings.get("mailboxes") or [] if isinstance(box, dict) and box.get("kind") in MAILBOX_KINDS]
    return boxes or [{"kind": "gmail", "label": "Gmail", "index": 0}]


def mail_search_url(box, amount=None, merchant="", day=None, before=14, after=15, attachment=False):
    """Web search URL of a mailbox. amount (Decimal) is searched the German way (30,27) and also as 30.27 in Gmail;
    merchant is added when given. Daum searches one term, so it uses the amount when there is one."""
    comma = format(abs(amount), ".2f").replace(".", ",") if amount is not None else ""
    start = day - timedelta(days=before) if day else None
    end = day + timedelta(days=after) if day else None
    safe = merchant.replace('"', " ").replace("\n", " ").replace("\r", " ").strip()
    if box.get("kind") == "daum":
        term = comma or safe
        if start and end:
            fields = {"after": start.strftime("%Y%m%d"), "before": end.strftime("%Y%m%d")}
            fields.update({name: term for name in ("body", "cc", "file", "from", "subject", "to")})
            fields["folderId"] = "ALL"
            return "https://mail.daum.net/top/SEARCH?" + urlencode(dict(sorted(fields.items())))
        return "https://mail.daum.net/top/SEARCH?" + urlencode({"query": term})
    parts = []
    if comma:
        parts.append(f'("{comma}" OR "{comma.replace(",", ".")}")')
    if safe:
        parts.append(f"from:{safe}" if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", safe) else f'"{safe}"')
    if attachment:
        parts.append("has:attachment")
    if start and end:
        parts.append(f"after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}")
    index = int(box.get("index", 0) or 0)
    return f"https://mail.google.com/mail/u/{index}/#search/{quote(' '.join(parts), safe='')}"


def export_value(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


FOLLOW_UP = {
    "": "",
    "요청 중": "Beleg angefordert",
    "수신 완료": "Beleg erhalten",
    "세무사 확인 필요": "Steuerliche Prüfung erforderlich",
    "증빙 확보 불가": "Beleg nicht beschaffbar",
}
DIFFERENCE_REASONS = {
    "": "", "부분결제": "Teilzahlung", "수수료": "Gebühr", "환율 차이": "Kursdifferenz",
    "조기결제 할인": "Skonto", "기타": "Sonstiges",
}
DELIVERY_STATES = ("작성 완료", "전달함", "회신 대기", "추가 요청", "처리 완료")
GERMAN_TEMPLATES = {
    "인보이스 요청 중": "Die Rechnung wurde angefordert.",
    "다음 주까지 제출 예정": "Der Beleg wird voraussichtlich bis nächste Woche nachgereicht.",
    "세무사 검토 요청": "Bitte steuerlich prüfen.",
}


# What goes into the advisor ZIP: (id, name shown, Korean description, required)
PACKAGE_PARTS = [
    ("Belege", "Belege/ (증빙 폴더)", "거래에 연결된 인보이스·영수증. 파일명 앞 [거래번호]로 거래와 연결돼요.", True),
    ("Abgleich.xlsx", "Abgleich.xlsx", "엑셀 대조표. 아래 표들이 모두 시트로 들어 있고, 증빙 파일로 바로 가는 링크가 있어요.", True),
    ("01_Abgleich.csv", "01_Abgleich.csv", "거래별 대조 결과: 거래번호·날짜·금액·상태·세무사 전달 메모.", True),
    ("Kontoauszuege", "Kontoauszuege/ (명세서 원본)", "은행·카드 명세서 원본 파일.", False),
    ("nummeriert", "Kontoauszuege/…_nummeriert.pdf", "PDF 명세서에 거래번호를 찍은 사본 (원본은 그대로).", False),
    ("Druckmappe.pdf", "Druckmappe.pdf", "인쇄용 PDF 한 개: 명세서 쪽마다 그 쪽 번호의 인보이스(번호 찍힌 사본)를 이어 붙였어요. 원본은 Belege/에 그대로.", False),
    ("Anlagen", "Anlagen/ (보조 자료)", "메모·기록에서 거래에 붙인 보조 자료 (확인 메일 등).", False),
    ("02_Belegdetails.csv", "02_Belegdetails.csv", "증빙별 상세: 발행일·업체·인보이스 번호·순액·세액.", False),
    ("03_Offene_Posten.csv", "03_Offene_Posten.csv", "미해결 거래: 증빙 없음·확인 필요인 거래만 모은 표.", False),
    ("04_Importfehler.csv", "04_Importfehler.csv", "명세서에서 읽지 못한 행, 끝까지 읽지 못한 증빙 PDF.", False),
    ("05_Kontoauszuege.csv", "05_Kontoauszuege.csv", "ZIP에 든 명세서 파일 목록과 위변조 확인용 해시(SHA-256).", False),
    ("06_Nachreichungen.csv", "06_Nachreichungen.csv", "지난 제출 이후 바뀐 거래 (추가 제출용).", False),
    ("07_Bewirtung.csv", "07_Bewirtung.csv", "접대비(Bewirtung) 증빙의 날짜·장소·참석자·목적.", False),
    ("08_Betragszuordnung.csv", "08_Betragszuordnung.csv", "한 증빙을 여러 거래에 나눈 금액과 차액 사유.", False),
    ("09_Rechnungskorrekturen.csv", "09_Rechnungskorrekturen.csv", "수정 인보이스(Gutschrift·Storno)와 원래 인보이스 연결.", False),
    ("10_Anlagen.csv", "10_Anlagen.csv", "보조 자료 목록.", False),
    ("11_Endkontrolle.csv", "11_Endkontrolle.csv", "최종 검수 기록: 검수한 거래의 결과(검수 완료·다시 확인)와 날짜.", False),
    ("Hinweise.txt", "Hinweise.txt", "세무사에게 드리는 짧은 독일어 안내문 (파일 구성 설명).", False),
]


def package_part(member):
    """PACKAGE_PARTS id of a ZIP member name."""
    if member.startswith("Kontoauszuege/"):
        return "nummeriert" if member.endswith("_nummeriert.pdf") else "Kontoauszuege"
    return member.split("/", 1)[0] if "/" in member else member


DEFAULT_PHRASES = [("요청 중", GERMAN_TEMPLATES["인보이스 요청 중"]),
                   ("다음 주 제출", GERMAN_TEMPLATES["다음 주까지 제출 예정"]),
                   ("검토 요청", GERMAN_TEMPLATES["세무사 검토 요청"])]


def german_draft(source, due, follow_up):
    if due and normalized_date(due) != due:
        raise ValueError("예정일은 YYYY-MM-DD 형식으로 입력하세요.")
    lines = []
    if follow_up == "수신 완료":
        lines.append("Der Beleg liegt vor.")
    elif follow_up == "요청 중" or re.search(r"(인보이스|영수증|증빙|청구서).*요청", source):
        lines.append("Der Beleg wurde angefordert.")
    if follow_up == "세무사 확인 필요":
        lines.append("Bitte steuerlich prüfen.")
    if follow_up == "증빙 확보 불가":
        lines.append("Der Beleg konnte nicht beschafft werden.")
    if due:
        when = date.fromisoformat(due).strftime("%d.%m.%Y")
        lines.append(f"Der Beleg wird voraussichtlich bis zum {when} nachgereicht.")
    return " ".join(lines)


def german_input(value):
    if re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", value):
        raise ValueError("독일어 전달란에는 독일어로 적어 주세요. 한국어는 내부 메모에 남길 수 있어요.")
    return value


def german_import_error(value):
    if "검산" in value or "원본 합계" in value:
        return "Summenabgleich prüfen · " + " / ".join(re.findall(r"-?\d+\.\d{2}", value))
    if "겹쳐" in value or "중복" in value:
        return "Mögliche doppelte Transaktionen prüfen"
    if "날짜" in value:
        return "Buchungsdatum prüfen"
    if "금액" in value or "통화" in value:
        return "Betrag oder Währung prüfen"
    return "Importzeile anhand des Originalauszugs prüfen"


GERMAN_TO_KOREAN = {
    "Abgeglichen": "금액 대조 완료", "Beleg fehlt": "증빙 없음",
    "Auszug als Nachweis · steuerlich prüfen": "명세서 근거 · 세무사 확인",
    "Dauerbeleg – Vertrag liegt vor": "계약서 근거 확인",
    "Verknüpfte Datei fehlt": "연결 파일 없음", "Rechnungsbetrag prüfen": "인보이스 총액 확인",
    "Währung prüfen": "통화 확인", "Gegenkontoauszug fehlt": "이체 상대 명세서 필요",
    "Auszug zur Erstattung fehlt": "환불 상대 명세서 필요",
    "Vollständig erstattet · Belegprüfung offen": "전액 환불 · 증빙 확인 필요",
    "Übersetzung der Notiz ausstehend.": "메모 번역 대기",
    "Enthalten": "포함", "Bereits übermittelt": "이미 전달함", "Zuordnung aufgehoben": "연결 해제됨",
}
GERMAN_PREFIXES = (("Interner Transfer zu", "내부이체"), ("Differenz", "차액"),
                   ("Teilerstattung · Restbetrag", "일부 환불 · 잔액"), ("Bereits übermittelt: ", "이미 전달: "))


def german_with_korean(text):
    """Show a German export value with its Korean meaning, e.g. 'Beleg fehlt (증빙 없음)'."""
    korean = GERMAN_TO_KOREAN.get(text)
    if korean is None:
        for german, meaning in GERMAN_PREFIXES:
            if text.startswith(german):
                korean = meaning + text[len(german):]
                break
    return f"{text} ({korean})" if korean else text


def delivery_text(value):
    try:
        return german_input(value)
    except ValueError:
        return "Übersetzung der Notiz ausstehend."


def first_page_preview(path, width, height):
    with open_evidence(path) as document:
        if not len(document):
            raise ValueError("빈 문서")
        page = document[0]
        scale = min(width / page.rect.width, height / page.rect.height)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")


def correction_relation(correction, original, relations):
    cursor = original.key
    visited = {correction.key}
    while cursor:
        if cursor in visited:
            raise ValueError("정정 문서가 자기 자신 또는 순환 관계로 연결돼요.")
        visited.add(cursor)
        cursor = relations.get(cursor, {}).get("original")
    return {
        "original": original.key, "original_number": original.number, "original_name": original.path.name,
        "correction_number": correction.number, "correction_name": correction.path.name,
    }


def corrections_for(state, links):
    links = set(links)
    return {key: relation for key, relation in state.get("corrections", {}).items()
            if key in links or relation.get("original") in links}


def delivery_changes(state, keys):
    snapshots = {
        key: {
            "links": sorted(state["records"][key].get("links", [])),
            "record": {field: state["records"][key].get(field) for field in (
                "advisor_note", "due", "follow_up", "allocations", "difference_reason", "transfer", "refund_group", "refund_of"
            )},
            "documents": {
                link: [state.get("document_notes", {}).get(link, ""), state.get("hospitality", {}).get(link, {})]
                for link in state["records"][key].get("links", [])
            },
        }
        for key in keys
    }
    for key, snapshot in snapshots.items():
        statement_evidence = state["records"][key].get("statement_evidence")
        if statement_evidence:
            snapshot["record"]["statement_evidence"] = statement_evidence
        attachments = state["records"][key].get("attachments", [])
        if attachments:
            snapshot["attachments"] = attachments
        relations = corrections_for(state, snapshot["links"])
        if relations:
            snapshot["corrections"] = relations
    return snapshots


def search_records(records, documents, query):
    tokens = query.casefold().replace(",", ".").split()
    if not tokens:
        return []
    invoices = {doc.key: doc for doc in documents}
    linked = set()
    rows = []
    for record in records.values():
        number, month = record.get("number", ""), record.get("period", "")
        subject = record.get("merchant", "")
        amount = " ".join(filter(None, (record.get("amount", ""), record.get("currency", ""))))
        keys = record.get("links", [])
        linked.update(keys)
        docs = [invoices[key] for key in keys if key in invoices]
        for doc in docs or [None]:
            path = doc.path if doc else None
            item = f"{number} {month} {subject} {amount} {record.get('day', '')}"
            if doc:
                item += f" {doc.path.name} {doc.seller} {doc.amount} {doc.number}"
            if all(token in item.casefold().replace(",", ".") for token in tokens):
                rows.append((month, number, subject or (doc.seller if doc else ""), amount, path))
    for key, doc in invoices.items():
        if key in linked:
            continue
        item = f"{doc.path.name} {doc.seller} {doc.amount} {doc.day} {doc.currency} {doc.number}".casefold().replace(",", ".")
        if all(token in item for token in tokens):
            amount = f"{doc.amount} {doc.currency}" if doc.amount is not None else ""
            rows.append(("", "", doc.seller, amount, doc.path))
    return sorted(rows, key=lambda row: (row[0], row[1], row[2], str(row[4])), reverse=True)


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
            temporary = Path(file.name)
            json.dump(state, file, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_backup(path):
    backup = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(backup, dict) or backup.get("format") != "InvoiceRenamer" or backup.get("version") != 1:
        raise ValueError("InvoiceRenamer 작업 백업 형식이 아니에요.")
    state = backup["state"]
    if not isinstance(state, dict) or not isinstance(state.get("records"), dict) or not isinstance(state.get("aliases"), dict):
        raise ValueError("백업에 필요한 거래 기록이 없어요.")
    for record in state["records"].values():
        if not isinstance(record, dict) or (
            record.get("period") and not re.fullmatch(MONTH_FOLDER, record["period"])
        ) or ("links" in record and not isinstance(record["links"], list)):
            raise ValueError("백업 거래 형식이 올바르지 않아요.")
    for key in ("exports", "periods", "session", "document_notes", "hospitality"):
        if key in state and not isinstance(state[key], dict):
            raise ValueError("백업 설정 형식이 올바르지 않아요.")
    return state


def statement_period(path, transactions):
    """Return dates printed by the issuer, or the observed transaction range."""
    if path.suffix.lower() == ".pdf":
        try:
            with pymupdf.open(path) as document:
                first = document[0].get_text(sort=True) if len(document) else ""
            if "americanexpress.de" in first.lower():
                heading = r"Abrechnungszeitraum\s+vom"
            elif "Deutsche Kreditbank AG" in first:
                heading = r"Ihre Abrechnung vom"
            elif "Deutsche Bank AG" in first:
                heading = r"Kontoauszug vom"
            else:
                heading = None
            if heading:
                date_token = r"\d{2}\.\d{2}\.(?:\d{4}|\d{2})"
                match = re.search(rf"{heading}\s+({date_token})\s+bis\s+({date_token})\b", first, re.I)
                if match:
                    start, end = (normalized_date(value if len(value) == 10 else value[:-2] + "20" + value[-2:])
                                  for value in match.groups())
                    if start and end and start <= end:
                        return start, end, True
        except (OSError, RuntimeError, ValueError):
            pass
    dates = [item.day.isoformat() for item in transactions]
    return (min(dates), max(dates), False) if dates else ("", "", False)


def coverage_warnings(periods):
    by_account = defaultdict(list)
    for item in periods:
        if item["printed"] and item["start"] and item["end"]:
            by_account[item["account"]].append((date.fromisoformat(item["start"]), date.fromisoformat(item["end"])))
    rows = []
    for account, ranges in sorted(by_account.items()):
        ordered = sorted(ranges)
        _, covered = ordered[0]
        for start, end in ordered[1:]:
            gap = (start - covered).days
            if gap > 1:
                rows.append((account, "누락 가능", covered + timedelta(days=1), start - timedelta(days=1)))
            elif gap <= 0:
                rows.append((account, "기간 겹침", start, min(covered, end)))
            covered = max(covered, end)
    return rows


NO_EVIDENCE_REASONS = {
    "": "", "은행 수수료": "Bankgebühr", "카드 연회비": "Kartenjahresgebühr",
    "임대료 · 계약서 기제출": "Miete – Mietvertrag liegt dem Steuerbüro vor (Dauerbeleg)",
    "할부금 · 계약서 기제출": "Ratenzahlung – Vertrag liegt dem Steuerbüro vor (Dauerbeleg)",
    "대출 상환 · 계약서 기제출": "Darlehensrate – Darlehensvertrag liegt dem Steuerbüro vor (Dauerbeleg)",
    "리스료 · 계약서 기제출": "Leasingrate – Leasingvertrag liegt dem Steuerbüro vor (Dauerbeleg)",
    "보험료 · 보험증권 기제출": "Versicherungsbeitrag – Police liegt dem Steuerbüro vor (Dauerbeleg)",
    "명세서만 확보": "Nur Konto- oder Kartenabrechnung vorhanden", "기타 · 세무사 확인": "Sonstiges – steuerlich prüfen",
}
UNLINKED_REASONS = ("", "미결제", "다른 결제수단", "다음 달 확인")

DAUERBELEG_REASONS = {reason for reason in NO_EVIDENCE_REASONS if "기제출" in reason}


_REASON_CACHE = []


def reason_list(settings=None):
    """Reasons for ‘증빙 없이 처리’: built-in ones (German text editable, can be hidden) and the user's own.

    [{"ko", "de", "contract", "hidden", "builtin"}]. Kept in settings.json → ‘no_evidence_reasons’.
    """
    if settings is None:
        if _REASON_CACHE:
            return [dict(entry) for entry in _REASON_CACHE[0]]
        try:
            from invoice_renamer import load_settings
            settings = load_settings()
        except Exception:
            settings = {}
        _REASON_CACHE[:] = [reason_list(settings)]
        return [dict(entry) for entry in _REASON_CACHE[0]]
    saved = settings.get("no_evidence_reasons") or {}
    overrides = saved.get("overrides") or {}
    reasons = []
    for korean, german in NO_EVIDENCE_REASONS.items():
        if not korean:
            continue
        change = overrides.get(korean) or {}
        reasons.append({"ko": korean, "de": change.get("de") or german, "contract": bool(change.get("contract", korean in DAUERBELEG_REASONS)),
                        "hidden": bool(change.get("hidden")), "builtin": True})
    for entry in saved.get("custom") or []:
        if entry.get("ko") and entry["ko"] not in NO_EVIDENCE_REASONS:
            reasons.append({"ko": entry["ko"], "de": entry.get("de", ""), "contract": bool(entry.get("contract")),
                            "hidden": bool(entry.get("hidden")), "builtin": False})
    return reasons


def save_reason_list(reasons):
    from invoice_renamer import save_settings
    overrides, custom = {}, []
    for entry in reasons:
        if entry["builtin"]:
            default_contract = entry["ko"] in DAUERBELEG_REASONS
            change = {}
            if entry["de"] != NO_EVIDENCE_REASONS[entry["ko"]]:
                change["de"] = entry["de"]
            if entry["contract"] != default_contract:
                change["contract"] = entry["contract"]
            if entry["hidden"]:
                change["hidden"] = True
            if change:
                overrides[entry["ko"]] = change
        else:
            custom.append({key: entry[key] for key in ("ko", "de", "contract", "hidden")})
    save_settings({"no_evidence_reasons": {"overrides": overrides, "custom": custom}})
    _REASON_CACHE.clear()


def reason_german(korean, reasons=None):
    """German text of a no-evidence reason for the advisor (unknown reason: empty)."""
    for entry in reasons if reasons is not None else reason_list():
        if entry["ko"] == korean:
            return entry["de"]
    return NO_EVIDENCE_REASONS.get(korean, "")


def is_contract_reason(korean, reasons=None):
    """True for ‘contract already with the advisor’ reasons (rent, instalments …): counted as done."""
    if not korean:
        return False
    for entry in reasons if reasons is not None else reason_list():
        if entry["ko"] == korean:
            return entry["contract"]
    return korean in DAUERBELEG_REASONS
CONTRACT_STATUS = "계약서 근거 확인"
EARLIER_REFUND = "전월 결제 환불 확인"
PARTIAL_REFUND_DONE = "일부 환불 · 증빙 연결됨"
DONE_STATES = ("금액 대조 완료", "내부이체 확인", "전액 환불 확인", CONTRACT_STATUS, EARLIER_REFUND, PARTIAL_REFUND_DONE)
RECURRING_AUTO = ("아니요", "예 · 같은 거래처·같은 금액이면 자동 적용")


def whole_page(path, index, rect):
    """PNG of a full statement page (readable width) with the transaction lines boxed."""
    with pymupdf.open(path) as document:
        page = document[index]
        page.draw_rect(pymupdf.Rect(rect) + (-2, -2, 2, 2), color=(0.9, 0.38, 0.05), width=1.8, overlay=True)
        scale = 780 / max(1, page.rect.width)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")


def page_height(path, index):
    with pymupdf.open(path) as document:
        return document[index].rect.height


STATEMENT_LINE_START = re.compile(r"^\s*\d{2}\.\d{2}\.?(?:\d{2,4})?\s")


def statement_excerpt(path, item, cache=None):
    """Where a transaction stands in its statement: (page index or None, rect or None, text lines).

    PDF: the line with the transaction's day.month and amount plus the lines that belong to it
    (Verwendungszweck, reference …) until the next transaction line. CSV: column name: value of the row.
    """
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        if cache is not None and str(path) in cache:
            pages = cache[str(path)]
        else:
            with pymupdf.open(path) as document:
                pages = [[(rect, " ".join(words)) for rect, words in statement_lines(page)] for page in document]
            if cache is not None:
                cache[str(path)] = pages
        amount = german_amount(item.amount)
        day = f"{item.day.day:02d}.{item.day.month:02d}"
        found = None
        for wanted in (lambda text: text.lstrip().startswith(day) and amount in text, lambda text: amount in text):
            found = next(((index, position) for index, lines in enumerate(pages)
                          for position, (_, text) in enumerate(lines) if wanted(text)), None)
            if found:
                break
        if not found:
            return None, None, []
        index, position = found
        lines = pages[index]
        rect = pymupdf.Rect(lines[position][0])
        texts = [lines[position][1]]
        for following_rect, text in lines[position + 1:position + 8]:
            if STATEMENT_LINE_START.match(text) or following_rect.y0 - rect.y1 > 18:
                break
            rect |= following_rect
            texts.append(text)
        return index, tuple(rect), texts
    header, rows = read_csv(path)
    for row in rows:
        days = {normalized_date(cell.strip()) for cell in row if re.fullmatch(DATE, cell.strip())}
        money = {cell.strip().lstrip("+-").replace(".", "").replace(",", ".") if "," in cell else cell.strip().lstrip("+-")
                 for cell in row}
        if item.day.isoformat() in days and format(abs(item.amount), ".2f") in money:
            return None, None, [f"{name}: {value}" for name, value in zip(header, row) if value.strip()]
    return None, None, []


REVIEW_STATES = {"ok": "검수 완료", "recheck": "다시 확인"}
REVIEW_GERMAN = {"ok": "Geprüft", "recheck": "Erneut prüfen", "changed": "Nach Prüfung geändert"}


def review_signature(record):
    """What a final check approved: a later change of links or reasons makes the check stale."""
    return json.dumps([sorted(record.get("links", [])), record.get("statement_evidence", ""), record.get("transfer", ""),
                       sorted(record.get("refund_group", [])), record.get("allocations", {}), record.get("refund_of", "")],
                      ensure_ascii=False, sort_keys=True)


def review_state(state, key):
    """'ok', 'recheck', 'changed' (checked, then changed) or '' (not checked)."""
    entry = state.get("final_review", {}).get(key)
    if not entry:
        return ""
    if entry.get("signature") != review_signature(state["records"].get(key, {})):
        return "changed"
    return entry.get("status", "")


def evidence_image(path, width):
    """PNG of the first page of an evidence file, scaled to a width (PDF or photo)."""
    from invoice_renamer import open_evidence
    with open_evidence(path) as document:
        page = document[0]
        scale = max(0.3, min(3.0, width / max(1, page.rect.width)))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png"), len(document)


def split_merchant(merchant):
    """‘SEPA Lastschrifteinzug von | AMAZON PAYMENTS EUROPE’ → (‘SEPA Lastschrifteinzug von’, ‘AMAZON PAYMENTS EUROPE’)."""
    parts = [part.strip() for part in merchant.split("|")]
    if len(parts) > 1 and parts[0] and any(parts[1:]):
        return parts[0], " | ".join(part for part in parts[1:] if part)
    return "", merchant


def payee_key(merchant):
    """Who was paid, without the numbers that change every month (month, instalment no., mandate reference).

    ‘Dauerauftrag Lager GmbH Miete 08/2026’ and ‘… Miete 09/2026’ give the same key. With ‘a | payee | text’ the middle part is used.
    """
    parts = [part.strip() for part in merchant.split("|") if part.strip()]
    text = parts[1] if len(parts) > 1 else merchant
    return "".join(char for char in text.casefold() if char.isalpha())[:60]


def apply_recurring(state, transactions):
    """Mark transactions that fit a saved recurring rule (rent, instalments …) as ‘evidence on file’. Returns the count."""
    rules = state.get("recurring_rules", [])
    count = 0
    for item in transactions:
        record = state["records"].get(item.key, {})
        if record.get("links") or record.get("transfer") or record.get("refund_group") or record.get("refund_of") \
                or record.get("statement_evidence"):
            continue
        for rule in rules:
            if rule["payee"] == payee_key(item.merchant) and rule["amount"] == str(item.amount) and rule["currency"] == item.currency:
                record = state["records"].setdefault(item.key, record)
                record["statement_evidence"] = rule["reason"]
                record["recurring_rule"] = True
                count += 1
                break
    return count


REVIEW_KINDS = ("증빙 없는 거래", "거래 없는 증빙", "번역 대기", "세무사 확인", "명세서 공백")


def review_months(state, invoices):
    rows = defaultdict(list)
    globally_linked = set()
    for record in state["records"].values():
        month, number = record.get("period", "미지정"), record.get("number", "")
        issues = rows[month]
        links = record.get("links", [])
        globally_linked.update(links)
        merchant = record.get("merchant", "")
        if not (links or record.get("transfer") or record.get("refund_group") or record.get("refund_of") or record.get("statement_evidence")):
            issues.append((REVIEW_KINDS[0], number, merchant))
        if (record.get("statement_evidence") and not is_contract_reason(record["statement_evidence"])) \
                or record.get("follow_up") in ("세무사 확인 필요", "증빙 확보 불가"):
            issues.append((REVIEW_KINDS[3], number, merchant))
        texts = [record.get("advisor_note", ""), *(state.get("document_notes", {}).get(key, "") for key in links)]
        if any(delivery_text(value) != value for value in texts):
            issues.append((REVIEW_KINDS[2], number, merchant))
    for doc in invoices:
        if doc.key not in globally_linked:
            month = doc.day.strftime("%Y-%m") if doc.day else "미지정"
            rows[month].append((REVIEW_KINDS[1], doc.path.name, state.get("unlinked_invoice_flags", {}).get(doc.key, "")))
    for account, kind, start, end in coverage_warnings(state.get("statement_periods", {}).values()):
        if kind != "누락 가능":
            continue
        cursor = start
        while cursor <= end:
            following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
            rows[cursor.strftime("%Y-%m")].append((REVIEW_KINDS[4], account, f"{cursor} ~ {min(end, following - timedelta(days=1))}"))
            cursor = following
    return rows


def affected_locked_month(old, new):
    locks = {month for month, record in old.get("periods", {}).items() if record.get("locked")}
    if not locks:
        return None
    old_records, new_records = old["records"], new["records"]
    for key in old_records.keys() | new_records.keys():
        before, after = old_records.get(key, {}), new_records.get(key, {})
        months = {before.get("period"), after.get("period")} & locks
        if not months:
            continue
        # Merchant text is bookkeeping metadata restored from the original statement.
        if {k: v for k, v in before.items() if k not in ("merchant", "day", "amount", "currency", "overlap_base")} != {k: v for k, v in after.items() if k not in ("merchant", "day", "amount", "currency", "overlap_base")}:
            return sorted(months)[0]
        if corrections_for(old, before.get("links", [])) != corrections_for(new, before.get("links", [])):
            return sorted(months)[0]
        for link in before.get("links", []):
            if any(old.get(field, {}).get(link) != new.get(field, {}).get(link)
                   for field in ("document_notes", "hospitality")):
                return sorted(months)[0]
    return None


PROFILE_FIELDS = ("columns", "currency", "date_order", "account", "flip_sign")


def statement_layout(path, header):
    marker = "csv"
    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            text = doc[0].get_text().casefold() if len(doc) else ""
        marker = next((candidate for candidate in ("americanexpress.de", "deutsche kreditbank ag", "deutsche bank ag")
                       if candidate in text), "pdf")
    payload = json.dumps([marker, [" ".join(str(name).casefold().split()) for name in header]], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def german_request(item, merchant):
    merchant = " ".join(merchant.split())
    amount = format(abs(item.amount), ",.2f").translate(str.maketrans(",.", ".,"))
    subject = f"Bitte um Rechnung zur Zahlung vom {item.day:%d.%m.%Y}"
    body = (
        "Guten Tag,\n\n"
        f"könnten Sie uns bitte die Rechnung zu folgender Zahlung zusenden?\n\n"
        f"Anbieter: {merchant}\nZahlungsdatum: {item.day:%d.%m.%Y}\nBetrag: {amount} {item.currency}\n\n"
        "Vielen Dank im Voraus.\n\nMit freundlichen Grüßen"
    )
    return subject, body


class MonthlyClose:
    def __init__(self, parent, folder, practice_folder=None, container=None, on_change=None):
        self.embedded = container is not None
        self.on_change = on_change
        if self.embedded:
            self.root = parent.winfo_toplevel()
        else:
            self.root = tk.Toplevel(parent)
            self.root.title(("[연습 모드] " if practice_folder else "") + f"카드 명세서 월 마감 · v{VERSION}")
            self.root.geometry("1160x720")
            container = self.root
        self.folder = folder
        self.pdfs_loaded = False
        self.practice_folder = practice_folder
        self.transactions = {}
        self.statement_sources = {}
        self.transaction_sources = {}
        self.accounts = {}
        self.session_specs = {}
        self.last_moves = []
        self.invoices = []
        self.by_key = {}
        self.duplicates = set()
        self.errors = []
        self.read_issues = []
        self.state_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "InvoiceRenamer" / "monthly-close.json"
        self.state = {"records": {}, "aliases": {}}
        try:
            if self.state_path.exists():
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if not isinstance(self.state["records"], dict) or not isinstance(self.state["aliases"], dict):
                    raise ValueError("저장 형식 오류")
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("마감 기록 열기 실패", str(error), parent=self.root)
            if self.embedded:
                raise RuntimeError(f"마감 기록 열기 실패: {error}") from error
            self.root.destroy()
            return

        from ui_theme import configure_tags, more_menu

        self.step = 3
        self.month_filter = tk.StringVar(value="전체")
        self.unresolved = tk.BooleanVar(value=False)
        self.view = tk.StringVar(value="확인 필요")  # matched transactions drop out; see them under 대조 완료
        self.unresolved.trace_add("write", lambda *_: self.view.set("확인 필요" if self.unresolved.get() else "전체"))
        self.head = ttk.Frame(container, padding=(18, 14, 18, 4))
        self.head.pack(fill="x")
        self.title_var = tk.StringVar()
        self.hint_var = tk.StringVar()
        ttk.Label(self.head, textvariable=self.title_var, style="Title.TLabel", font=FONT_TITLE).pack(anchor="w")
        ttk.Label(self.head, textvariable=self.hint_var, style="Muted.TLabel").pack(anchor="w")
        self.bar_area = ttk.Frame(container, padding=(18, 6, 18, 2))
        self.bar_area.pack(fill="x")
        steps = {
            2: [("명세서 추가", self.open_csv, "Primary.TButton"), ("여러 개 추가", self.open_batch, None),
                ("이어서 작업", self.resume, None), ("지난 달", self.open_past_month, None),
                ("번호 명세서", self.open_numbered_statements, None)],
            3: [("증빙 폴더", self.select_folder, None), ("일괄 매칭", self.batch_candidates, None),
                ("완료 폴더로 이동", self.archive_matched, "Primary.TButton")],
            4: [("최종 검수", self.final_review, "Primary.TButton"), ("인쇄 묶음", self.open_print_bundle, None), ("누락 점검표", self.monthly_review, None), ("미연결 증빙", self.show_unlinked_invoices, None),
                ("후속 할 일", self.followup_list, None), ("기간 점검", self.show_statement_periods, None),
                ("전체 검색", self.search_all_months, None)],
            5: [("세무사 ZIP", self.export_zip, "Primary.TButton"),
                ("추가 ZIP", lambda: self.export_zip(supplemental=True), None),
                ("전달 이력", self.export_history, None), ("마감 확정·해제", self.manage_period, None),
                ("CSV 저장", self.export_csv, None)],
        }
        if practice_folder:
            steps[2].insert(0, ("예시 명세서 불러오기", lambda: self.load_practice_statement(practice_folder), "Success.TButton"))
        self.step_bars = {}
        for number, items in steps.items():
            frame = ttk.Frame(self.bar_area)
            for label, command, style in items:
                ttk.Button(frame, text=label, command=command, style=style or "TButton").pack(side="left", padx=(0, 6))
            if number == 2:
                more_menu(frame, "더보기 ▾", [("명세서 양식 관리", self.manage_formats),
                                             ("명세서 기간 점검", self.show_statement_periods)]).pack(side="left")
            if number == 3:
                self.unmatched_button = ttk.Button(frame, text="연결 안 된 증빙", command=self.manage_unmatched)
                self.unmatched_button.pack(side="left", padx=(0, 6))
                more_menu(frame, "더보기 ▾", [("명세서 근거로 처리 (수수료 등)", self.mark_statement_evidence),
                                             ("이동 되돌리기", self.undo_archive)]).pack(side="left")
            self.step_bars[number] = frame
        chips = ttk.Frame(container, padding=(18, 4, 18, 4))
        chips.pack(fill="x")
        self.chip_buttons = {}
        for label in ("확인 필요", "전체", "증빙 없음", "대조 완료"):
            chip = ttk.Radiobutton(chips, text=label, value=label, variable=self.view, command=self.refresh,
                                   style="Chip.TRadiobutton")
            chip.pack(side="left", padx=(0, 6))
            self.chip_buttons[label] = chip
        self.status = tk.StringVar(value="은행·카드 명세서와 증빙 폴더를 선택하세요.")
        ttk.Label(chips, textvariable=self.status, style="Muted.TLabel").pack(side="right")

        self.batch_bar = tk.Frame(container, background="#e8f1fd", highlightthickness=1, highlightbackground="#b6cdee",
                                  highlightcolor="#b6cdee", padx=12, pady=2)
        self.batch_text = tk.StringVar()
        tk.Label(self.batch_bar, textvariable=self.batch_text, background="#e8f1fd", foreground=COLORS["ink"],
                 font=FONT_BOLD).pack(side="left")
        self.batch_button = ttk.Button(self.batch_bar, text="한 번에 연결하기", style="Primary.TButton",
                                       command=lambda: self.batch_candidates())
        self.batch_button.pack(side="right")
        body = ttk.Panedwindow(container, orient="horizontal")
        body.pack(fill="both", expand=True, padx=18, pady=(2, 6))
        self.body = body

        def keep_sash(_event=None):
            width = body.winfo_width()
            if width > 400 and len(body.panes()) > 1:
                self.sash_ratio = min(0.85, max(0.15, body.sashpos(0) / width))
        body.bind("<ButtonRelease-1>", keep_sash, add="+")
        upper = tk.Frame(body, background=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"],
                         highlightcolor=COLORS["line"])
        lower = tk.Frame(body, background=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"],
                         highlightcolor=COLORS["line"])
        self.upper, self.lower = upper, lower
        body.add(upper, weight=6)
        body.add(lower, weight=5)

        def card_header(parent, title, variable):
            head = tk.Frame(parent, background=COLORS["panel"], padx=12, pady=8)
            head.pack(fill="x")
            tk.Label(head, text=title, font=FONT_BOLD, background=COLORS["panel"]).pack(side="left")
            tk.Label(head, textvariable=variable, font=FONT_SMALL, background=COLORS["panel"],
                     foreground=COLORS["muted"]).pack(side="left", padx=10)
            tk.Frame(parent, height=1, background=COLORS["line"]).pack(fill="x")
            return head

        self.cards_caption = tk.StringVar()
        card_header(upper, "은행·카드 거래", self.cards_caption)
        self.left_footer = tk.Frame(upper, background="#fafbfc", padx=10, pady=8)
        self.left_footer.pack(side="bottom", fill="x")
        more_menu(self.left_footer, "증빙 없이 처리 ▾", [
            ("월세·할부·대출·수수료 (계약서·명세서 근거)", self.mark_statement_evidence),
            ("자동 적용 규칙 보기·삭제", self.manage_recurring),
            ("사유 관리 (한국어·독일어·추가)", self.manage_reasons),
            ("카드대금·내부이체 후보", self.transfer_candidates), ("결제·환불 연결", self.link_refunds),
        ]).pack(side="left", padx=(0, 6))
        for label, command in (("메모·기록", self.edit_note), ("인보이스 요청 초안", self.draft_invoice_request),
                               ("메일 검색", self.search_gmail)):
            ttk.Button(self.left_footer, text=label, command=command, style="Small.TButton").pack(side="left", padx=(0, 4))
        self.cards = self.table(upper, ("번호", "거래일", "가맹점·거래처", "금액", "증빙 상태", "메모", "구분"),
                                (72, 92, 220, 100, 120, 160, 120))
        self.cards.column("가맹점·거래처", minwidth=140)
        self.cards.master.pack_configure(padx=1, pady=(0, 1))
        configure_tags(self.cards)
        self.cards.bind("<<TreeviewSelect>>", lambda event: self.show_invoices())
        self.cards.bind("<Return>", lambda event: self.link_best() if self.best_doc is not None else None)
        self.detail_strip = tk.Frame(upper, background="#f6f8fa", padx=10, pady=6)
        detail_buttons = tk.Frame(self.detail_strip, background="#f6f8fa")
        detail_buttons.pack(side="right", fill="y", padx=(8, 0))
        ttk.Button(detail_buttons, text="명세서에서 보기", command=self.show_in_statement, style="Small.TButton").pack(fill="x")
        self.detail_text = tk.Text(self.detail_strip, height=4, wrap="word", font=FONT_SMALL, relief="flat",
                                   background="#f6f8fa", highlightthickness=0)
        self.detail_text.pack(side="left", fill="both", expand=True)
        make_copyable(self.detail_text)
        self.excerpt_cache = {}

        self.pdfs_caption = tk.StringVar(value="거래를 고르세요")
        self.pdfs_header = card_header(lower, "추천 증빙", self.pdfs_caption)
        right_footer = tk.Frame(lower, background="#fafbfc", padx=10, pady=8)
        right_footer.pack(side="bottom", fill="x")
        ttk.Button(right_footer, text="선택 연결", command=self.link, style="Success.TButton").pack(side="left", padx=(0, 6))
        for label, command in (("+ 증빙 파일 추가", self.add_evidence_for), ("해제", self.unlink), ("나란히 보기", self.compare_documents),
                               ("열기", self.open_pdf)):
            ttk.Button(right_footer, text=label, command=command, style="Small.TButton").pack(side="left", padx=(0, 4))
        more_menu(right_footer, "더보기", [
            ("증빙 값 고치기", self.edit_document_values), ("증빙 → 제외 폴더", self.exclude_document), ("-", None),
            ("배분·차액", self.edit_allocation), ("정정·원본 연결", self.edit_correction), ("접대비 정보", self.edit_hospitality),
            ("보조 자료 첨부", self.edit_attachments), ("증빙 전달 메모 (독일어)", self.edit_document_note),
        ]).pack(side="left")
        self.all_pdfs = tk.BooleanVar()
        ttk.Checkbutton(self.pdfs_header, text="전체 증빙에서 고르기", variable=self.all_pdfs, command=self.show_invoices,
                        style="Panel.TCheckbutton").pack(side="right", padx=(0, 10))
        self.show_far = tk.BooleanVar()
        self.far_text = tk.StringVar(value="다른 달 후보")
        self.far_check = ttk.Checkbutton(self.pdfs_header, textvariable=self.far_text, variable=self.show_far,
                                         command=self.show_invoices, style="Panel.TCheckbutton")
        self.best_card = tk.Frame(lower, background="#f3fbf6", highlightthickness=0)
        stripe = tk.Frame(self.best_card, background=COLORS["green"], width=4)
        stripe.pack(side="left", fill="y")
        best_body = tk.Frame(self.best_card, background="#f3fbf6", padx=12, pady=10)
        best_body.pack(side="left", fill="both", expand=True)
        self.best_title = tk.StringVar()
        self.best_file = tk.StringVar()
        self.best_reason = tk.StringVar()
        tk.Label(best_body, textvariable=self.best_title, font=FONT_BOLD, background="#f3fbf6", anchor="w").pack(fill="x")
        tk.Label(best_body, textvariable=self.best_file, font=("Consolas", 9), background="#f3fbf6",
                 foreground=COLORS["muted"], anchor="w", justify="left", wraplength=420).pack(fill="x")
        tk.Label(best_body, textvariable=self.best_reason, font=FONT_SMALL, background="#f3fbf6",
                 foreground=COLORS["green"], anchor="w").pack(fill="x", pady=(2, 0))
        ttk.Button(self.best_card, text="연결", style="Success.TButton", command=self.link_best).pack(side="right", padx=12)
        self.none_card = tk.Frame(lower, background="#fff8eb", highlightthickness=0)
        tk.Frame(self.none_card, background=COLORS["amber"], width=4).pack(side="left", fill="y")
        none_body = tk.Frame(self.none_card, background="#fff8eb", padx=12, pady=8)
        none_body.pack(side="left", fill="both", expand=True)
        tk.Label(none_body, text="금액이 같은 증빙이 없어요. 이렇게 찾아보세요:", font=FONT_BOLD, background="#fff8eb",
                 anchor="w").pack(fill="x")
        none_buttons = tk.Frame(none_body, background="#fff8eb")
        none_buttons.pack(fill="x", pady=(6, 0))
        for label, command in (("메일 검색", self.search_gmail), ("+ 증빙 파일 추가", self.add_evidence_for),
                               ("전체 증빙에서 고르기", lambda: (self.all_pdfs.set(True), self.show_invoices()))):
            ttk.Button(none_buttons, text=label, command=command, style="Small.TButton").pack(side="left", padx=(0, 6))
        self.refund_button = ttk.Button(none_buttons, text="환불의 원래 결제 찾기", style="Small.TButton",
                                        command=lambda: self.link_refunds())
        self.best_doc = None
        self.pdfs = self.table(lower, ("증빙", "발행일", "총액", "번호", "추천·상태"), (210, 90, 90, 95, 150))
        self.pdfs.master.pack_configure(padx=1, pady=(0, 1))
        self.pdfs.configure(displaycolumns=("증빙", "발행일", "총액", "추천·상태"))
        configure_tags(self.pdfs)
        self.pdfs.bind("<Double-1>", lambda event: self.open_pdf())
        self.pdfs.bind("<Button-3>", self.evidence_menu, add="+")

        self.next_bar = tk.Frame(container, background=COLORS["panel"], highlightthickness=1, highlightbackground="#b8c2cc",
                                 highlightcolor="#b8c2cc", padx=14, pady=8)
        self.next_text = tk.StringVar()
        self.next_name = tk.StringVar()
        ttk.Button(self.next_bar, text="다음 단계: 누락 점검 →", style="Primary.TButton",
                   command=lambda: self.navigate and self.navigate(4)).pack(side="right")
        texts = tk.Frame(self.next_bar, background=COLORS["panel"])
        texts.pack(side="left", fill="x", expand=True)
        tk.Label(texts, textvariable=self.next_text, font=FONT_BOLD, background=COLORS["panel"], anchor="w").pack(fill="x")
        tk.Label(texts, textvariable=self.next_name, font=("Consolas", 9), background="#f6f8fa",
                 foreground=COLORS["ink"], padx=6, pady=2, anchor="w").pack(anchor="w", pady=(3, 0))
        self.navigate = None
        self.show_step(3)
        if folder:
            self.load_pdfs(folder)

    STEP_TEXT = {
        2: ("2. 명세서 불러오기", "Amex·DKB·Deutsche Bank PDF 또는 은행·카드 CSV를 불러와요. 같은 파일을 다시 열면 거래번호가 유지돼요."),
        3: ("3. 증빙 매칭", "거래를 고르면 금액이 같은 증빙이 오른쪽에 추천 순서로 나와요. 초록 카드가 가장 유력한 후보예요."),
        4: ("4. 누락 점검", "증빙 없는 거래, 거래 없는 증빙, 번역 대기, 명세서 기간 공백을 확인해요."),
        5: ("5. 세무사 제출", "독일어 ZIP·Excel 대조표를 만들고 전달 상태를 기록해요. 내부 메모는 들어가지 않아요."),
    }

    def notice(self, message, kind="info", title="안내", action=None):
        """Show a guidance message as a pop-up inside the window and keep it in the status line."""
        self.status.set(message.replace("\n", " "))
        show_notice(self.root, message, title=title, kind=kind, action=action)

    def announce_import(self, statements, transactions, problems=0):
        """Tell the user the statements are in and offer the next step."""
        moved = [path for path in self.statement_sources.values() if self.folder and STATEMENT_FOLDER in path.parts]
        where = f"\n원본은 증빙 폴더 ‘{STATEMENT_FOLDER}/월’ 폴더로 옮겼어요." if moved else ""
        check = f"\n읽지 못한 행 {problems}개는 4단계 누락 점검에서 확인하세요." if problems else ""
        self.notice(f"명세서 {statements}개를 불러왔어요 · 거래 {transactions}건.{where}{check}\n"
                    "다음: 3. 증빙 매칭에서 거래마다 인보이스를 연결하세요.", title="명세서 불러오기 완료",
                    action=("증빙 매칭으로 이동 →", lambda: self.navigate(3)) if self.navigate else None)

    def show_step(self, number):
        if number in (2, 3) and getattr(self, "step", None) != number:
            self.view.set("전체" if number == 2 else "확인 필요")  # step 2 lists what was read, step 3 what is left
            self.refresh()
        self.step = number
        for frame in self.step_bars.values():
            frame.pack_forget()
        self.step_bars[number].pack(side="left")
        title, hint = self.STEP_TEXT[number]
        self.title_var.set(title)
        self.hint_var.set(hint)
        panes = self.body.panes()
        if number == 3 and str(self.lower) not in map(str, panes):
            self.body.add(self.lower, weight=5)
            self.sash_placed = False  # put the divider back where the user left it
        elif number != 3 and str(self.lower) in map(str, panes):
            self.body.forget(self.lower)
        self.show_card_columns()
        if number == 3:
            if not getattr(self, "sash_placed", False):
                def place_sash():
                    width = self.body.winfo_width()
                    if width > 400 and len(self.body.panes()) > 1:
                        self.body.sashpos(0, int(width * getattr(self, "sash_ratio", 0.5)))
                        self.sash_placed = True
                self.body.after(50, place_sash)
            self.left_footer.pack(side="bottom", fill="x", before=self.cards.master)
            self.detail_strip.pack(side="bottom", fill="x", before=self.cards.master)
            self.next_bar.pack(fill="x", padx=18, pady=(0, 10))
            self.update_batch_bar()
            if not self.cards.selection() and self.cards.get_children():
                first = self.cards.get_children()[0]
                self.cards.selection_set(first)  # start with the first open transaction
                self.cards.focus(first)
        else:
            self.left_footer.pack_forget()
            self.detail_strip.pack_forget()
            self.next_bar.pack_forget()
            self.batch_bar.pack_forget()

    def summary(self):
        counts = Counter()
        months = set()
        for key in self.transactions:
            record = self.record(key)
            months.add(record.get("period", ""))
            if self.month_filter.get() not in ("전체", record.get("period")):
                continue
            counts[transaction_tag(self.status_for(key), record.get("follow_up", ""))] += 1
        return counts, sorted(filter(None, months), reverse=True)

    def buttons(self, parent, buttons):
        for label, command in buttons:
            ttk.Button(parent, text=label, command=command).pack(side="left", padx=(0, 5))

    def table(self, parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        table = ttk.Treeview(frame, columns=columns, show="headings")
        for name, width in zip(columns, widths):
            table.heading(name, text=name)
            table.column(name, width=width, minwidth=40)
        bar = ttk.Scrollbar(frame, command=table.yview)
        across = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=bar.set, xscrollcommand=across.set)
        bar.pack(side="right", fill="y")
        across.pack(side="bottom", fill="x")
        table.pack(fill="both", expand=True)
        table_tools(table, "|".join(columns))
        return table

    def load_practice_statement(self, folder):
        path = folder / "Karte.csv"
        try:
            key, _, rows, layout = self.read_import(path)
            if key in self.statement_sources:
                self.notice("예시 명세서를 이미 불러왔어요.")
                return
            spec = {"path": str(path), "columns": [0, 1, 2, 3], "currency": "EUR",
                    "date_order": "DD/MM/YYYY", "account": "연습 카드", "period": "", "flip_sign": False, "layout": layout}
            transactions, errors = self.parse_spec(rows, spec)
            if self.add_statement(key, spec, transactions, errors):
                self.announce_import(1, len(transactions), len(errors))
        except (OSError, ValueError, RuntimeError, csv.Error) as error:
            record_error("statement", error)
            messagebox.showerror("예시 명세서 열기 실패", str(error), parent=self.root)

    def open_batch(self, names=None):
        """names: files picked elsewhere (step 1 right-click ‘명세서로 불러오기’); otherwise a file dialog."""
        if self.practice_folder and not names:
            self.load_practice_statement(self.practice_folder)
            return
        names = names or filedialog.askopenfilenames(parent=self.root, filetypes=[("CSV / TSV / PDF", "*.csv *.tsv *.pdf")])
        if not names:
            return
        window = InlineDialog(self.root)
        window.title("명세서 여러 개 가져오기")
        window.geometry("1040x560")
        window.transient(self.root)
        window.grab_set()
        ttk.Label(window, text="계좌·카드 별칭과 마감월을 확인하세요. 저장된 설정이 하나인 파일은 미리 계산해요.", padding=8).pack(anchor="w")
        bar = ttk.Frame(window, padding=8)
        bar.pack(fill="x")
        table = self.table(window, ("명세서", "계좌·카드", "거래", "검산 / 필요한 확인"), (365, 140, 85, 415))
        prepared = {}
        by_path = {}

        def inspect(key, path):
            try:
                content = self.read_import(path)
                source_key, header, rows, layout = content
                if source_key in self.statement_sources:
                    return ("", "", "이미 가져옴")
                profiles = self.state.get("import_profiles", {}).get(layout, {})
                known = self.state.get("source_accounts", {}).get(source_key, "")
                profile = profiles.get(known) if known else (next(iter(profiles.values())) if len(profiles) == 1 else None)
                if not profile:
                    return ("", "", "계좌·열 설정 필요")
                spec = {**profile, "path": str(path), "layout": layout, "period": ""}
                transactions, errors, check, discrepancy = self.preview_import(path, rows, spec)
                if errors or discrepancy or not transactions:
                    return (profile["account"], len(transactions), f"설정·오류 확인 · {check}")
                prepared[key] = (source_key, spec, transactions, errors)
                return (profile["account"], len(transactions), check or "확인 가능")
            except (OSError, ValueError, csv.Error, RuntimeError) as error:
                return ("", "", str(error))

        with Busy(window, "명세서를 확인하고 있어요") as busy:
            for i, name in enumerate(names):
                key, path = str(i), Path(name)
                busy.step(i + 1, len(names), path.name)
                by_path[key] = path
                table.insert("", "end", iid=key, values=(path.name, *inspect(key, path)))

        def edit_selected(_event=None):
            selection = table.selection()
            if len(selection) != 1:
                self.notice("설정을 확인할 명세서 한 개를 선택하세요.")
                return
            key = selection[0]
            path = by_path[key]

            def configure(source_key, spec, transactions, errors, check, discrepancy):
                prepared[key] = (source_key, spec, transactions, errors)
                suffix = f" · 마감월 {spec['period']}" if spec["period"] else ""
                if errors:
                    suffix += f" · 확인 {len(errors)}건"
                table.item(key, values=(path.name, spec["account"], len(transactions), (check or "설정 확인됨") + suffix))
            self.open_csv(path, on_review=configure, owner=window,
                          initial_spec=prepared[key][1] if key in prepared else None)

        def move(step):
            selection = table.selection()
            if not selection:
                self.notice("옮길 명세서를 선택하세요.")
                return
            rows = list(table.get_children())
            picked = [rows.index(key) for key in selection]
            for index in (sorted(picked) if step < 0 else sorted(picked, reverse=True)):
                target = index + step
                if 0 <= target < len(rows) and rows[target] not in selection:
                    rows[index], rows[target] = rows[target], rows[index]
            for position, key in enumerate(rows):
                table.move(key, "", position)
            table.see(selection[0])

        def sort_by_account():
            accounts = [table.set(key, "계좌·카드") for key in table.get_children()]
            order = ordered_accounts(accounts)
            rank = {name: index for index, name in enumerate(order)}
            rows = sorted(table.get_children(), key=lambda key: (rank.get(table.set(key, "계좌·카드"), len(rank)), int(key)))
            for position, key in enumerate(rows):
                table.move(key, "", position)

        def apply():
            selection = set(table.selection())
            chosen = [key for key in table.get_children() if key in selection and key in prepared]  # screen order = number order
            if not chosen:
                messagebox.showinfo("명세서 가져오기", "검토가 끝난 행을 선택하세요.", parent=window)
                return
            pending = [key for key in selection if key not in prepared]
            prompt = f"준비된 명세서 {len(chosen)}개를 가져올까요?"
            if pending:
                prompt += f"\n설정이 필요한 {len(pending)}개는 가져오지 않아요."
            if not ask_yes_no("명세서 가져오기", prompt, parent=window):
                return
            done = []
            for key in chosen:
                source_key, spec, transactions, errors = prepared[key]
                try:
                    unchanged = digest(by_path[key]) == source_key
                except OSError:
                    unchanged = False
                if not unchanged:
                    table.item(key, values=(by_path[key].name, spec["account"], "", "파일이 변경됨 · 설정 다시 확인"))
                    prepared.pop(key, None)
                    continue
                if source_key in self.statement_sources:
                    table.item(key, values=(by_path[key].name, spec["account"], "", "이미 가져옴"))
                    prepared.pop(key, None)
                    continue
                spec = self.resolve_overlaps(source_key, spec, transactions, window)
                if spec is None:
                    break
                if self.add_statement(source_key, spec, transactions, list(errors)):
                    table.item(key, values=(by_path[key].name, spec["account"], len(transactions), "가져오기 완료"))
                    prepared.pop(key, None)
                    done.append((len(transactions), len(errors)))
            if done:
                self.announce_import(len(done), sum(count for count, _ in done), sum(problem for _, problem in done))

        self.buttons(bar, [("선택 파일 설정", edit_selected), ("선택 가져오기", apply),
                           ("준비된 파일 모두 선택", lambda: table.selection_set(tuple(prepared)))])
        order_bar = ttk.Frame(window, padding=(8, 0, 8, 6))
        order_bar.pack(fill="x", after=bar)
        self.buttons(order_bar, [("▲ 위로", lambda: move(-1)), ("▼ 아래로", lambda: move(1)),
                           ("계좌 순서로 정렬", sort_by_account),
                           ("계좌 순서 정하기", lambda: self.edit_account_order(
                               [table.set(key, "계좌·카드") for key in table.get_children()], sort_by_account, window))])
        ttk.Label(window, text="번호는 위에 있는 명세서부터 매겨져요. ▲▼로 순서를 바꾸거나 ‘계좌 순서로 정렬’을 누르세요.",
                  foreground=COLORS["muted"], padding=(8, 0, 8, 6)).pack(anchor="w", after=order_bar)
        sort_by_account()
        table.bind("<Double-1>", edit_selected)

    def edit_account_order(self, extra=(), on_save=None, owner=None):
        """Panel: the order of bank accounts and cards used to number transactions (saved in settings.json)."""
        order = ordered_accounts([*self.known_accounts(), *extra])
        dialog = InlineDialog(self.root)
        dialog.title("계좌 순서 정하기")
        dialog.center(520, 420)
        dialog.grab_set()
        ttk.Label(dialog, text="여러 명세서를 가져올 때 이 순서대로 번호를 매겨요. 새 계좌는 맨 아래에 붙어요.",
                  wraplength=480, padding=(14, 10, 14, 6)).pack(anchor="w")
        footer = ttk.Frame(dialog, padding=(14, 10))
        footer.pack(fill="x", side="bottom")  # packed before the list so it can never be pushed out of the panel
        box = tk.Listbox(dialog, activestyle="none", height=8, exportselection=False)
        box.pack(fill="both", expand=True, padx=14)
        for name in order:
            box.insert("end", name)

        def shift(step):
            picked = box.curselection()
            if not picked:
                return
            index = picked[0]
            target = index + step
            if 0 <= target < box.size():
                name = box.get(index)
                box.delete(index)
                box.insert(target, name)
                box.selection_set(target)

        def save():
            save_account_order(list(box.get(0, "end")))
            dialog.destroy()
            if on_save:
                on_save()

        self.buttons(footer, [("▲ 위로", lambda: shift(-1)), ("▼ 아래로", lambda: shift(1))])
        ttk.Button(footer, text="저장", style="Primary.TButton", command=save).pack(side="right")
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right", padx=6)

        def give_back(event):
            if event.widget is dialog and owner is not None and owner.winfo_exists():
                owner.after_idle(owner.grab_set)
        dialog.bind("<Destroy>", give_back)

    def known_accounts(self):
        names = list(self.state.get("source_accounts", {}).values())
        for profiles in self.state.get("import_profiles", {}).values():
            names.extend(profile.get("account", "") for profile in profiles.values())
        names.extend(self.accounts.values())
        return names

    def read_import(self, source):
        is_pdf = source.suffix.lower() == ".pdf"
        key = digest(source)
        header, rows = read_statement_pdf(source) if is_pdf else read_csv(source, self.imported_before(key))
        return key, header, rows, statement_layout(source, header)

    def imported_before(self, source_key):
        return source_key in self.state.get("source_resolutions", {}) or source_key in self.state.get("statement_paths", {})

    def preview_import(self, source, rows, spec):
        transactions, errors = self.parse_spec(rows, spec)
        check, discrepancy = statement_check(source, transactions) if source.suffix.lower() == ".pdf" else ("", False)
        if discrepancy:
            errors.append(("검산", check))
        return transactions, errors, check, discrepancy

    def draft_invoice_request(self):
        item = self.single_card()
        if item is None:
            return
        window = InlineDialog(self.root)
        window.title("독일어 인보이스 요청 초안")
        window.geometry("740x440")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="거래처 이름을 확인한 뒤 초안을 복사해 직접 보낼 수 있어요.").pack(anchor="w")
        merchant = ttk.Entry(frame, width=66)
        merchant.insert(0, item.merchant)
        merchant.pack(anchor="w", pady=6)
        content = tk.Text(frame, height=14, wrap="word")
        content.pack(fill="both", expand=True)

        def generate():
            subject, body = german_request(item, merchant.get())
            content.delete("1.0", "end")
            content.insert("1.0", f"Betreff: {subject}\n\n{body}")

        def copy():
            window.clipboard_clear()
            window.clipboard_append(content.get("1.0", "end-1c"))
            self.status.set("독일어 요청 초안을 복사했어요.")

        def mark():
            if not ask_yes_no("요청 완료 기록", "메일을 직접 보냈나요? 오늘 날짜로 요청 완료를 기록할까요?", parent=window):
                return
            state = deepcopy(self.state)
            record = state["records"][item.key]
            record["follow_up"] = "요청 중"
            record.setdefault("timeline", []).append({"date": date.today().isoformat(), "text": "인보이스 요청 메일 발송"})
            if self.save(state):
                window.destroy()

        bar = ttk.Frame(frame, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        self.buttons(bar, [("거래처 변경 반영", generate), ("초안 복사", copy), ("보낸 뒤 요청 완료 기록", mark)])
        generate()

    def open_csv(self, path=None, on_review=None, owner=None, initial_spec=None):
        if self.practice_folder:
            self.load_practice_statement(self.practice_folder)
            return
        owner = owner or self.root
        path = path or filedialog.askopenfilename(parent=owner, filetypes=[("CSV / TSV / PDF", "*.csv *.tsv *.pdf"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            source = Path(path)
            is_pdf = source.suffix.lower() == ".pdf"
            source_key, header, rows, layout = self.read_import(source)
        except (OSError, ValueError, csv.Error, RuntimeError) as error:
            record_error("statement", error)
            messagebox.showerror("명세서 열기 실패", str(error), parent=self.root)
            return
        dialog = InlineDialog(owner)
        dialog.title("은행·카드 명세서 열 지정")
        dialog.transient(owner)
        dialog.grab_set()
        if on_review:
            def close_review():
                dialog.destroy()
                owner.grab_set()
            dialog.protocol("WM_DELETE_WINDOW", close_review)
        form = ttk.Frame(dialog, padding=14)
        form.pack()
        columns = [f"{index + 1}. {name}" for index, name in enumerate(header)]
        choices = []
        labels = ("거래일 열", "가맹점 열", "금액 열", "통화 열 (선택)", "기본 통화", "연도가 뒤에 오는 날짜", "계좌·카드 별칭", "마감월", "지출이 음수인 명세서")
        hints = (
            r"transaction date|purchase date|kaufdatum|buchungstag|buchungsdatum|umsatzdatum|datum|거래일|이용일|사용일|date",
            r"merchant|händler|empfänger|auftraggeber|beschreibung|verwendungszweck|buchungstext|가맹점|사용처|description",
            r"amount|betrag|금액|\bumsatz\b",  # not ‘Umsatzdatum’
            r"currency|währung|통화",
        )
        for index, label in enumerate(labels):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=4, padx=(0, 10))
            if index < 4:
                values = columns if index != 3 else ["기본 통화 사용", *columns]
                entry = ttk.Combobox(form, values=values, state="readonly", width=39)
                taken = {choice.current() - (pos == 3) for pos, choice in enumerate(choices)}
                guess = next((i for i, name in enumerate(header)
                              if re.search(hints[index], name, re.I) and i not in taken), None)
                if guess is not None:
                    entry.current(guess + (index == 3))
                elif index == 3:
                    entry.current(0)
            elif index == 4:
                entry = ttk.Entry(form, width=42)
                entry.insert(0, "EUR")
            elif index == 5:
                entry = ttk.Combobox(form, values=["DD/MM/YYYY", "MM/DD/YYYY"], state="readonly", width=39)
                entry.current(0)
            elif index == 6:
                entry = ttk.Entry(form, width=42)
                entry.insert(0, self.state.get("source_accounts", {}).get(source_key, ""))
            elif index == 7:
                entry = ttk.Combobox(form, values=period_choices(), state="readonly", width=39)
                entry.current(0)
            else:
                entry = ttk.Combobox(form, values=["아니요", "예 (금액 부호 반전)"], state="readonly", width=39)
                entry.current(0)
            entry.grid(row=index, column=1)
            choices.append(entry)

        profiles = self.state.get("import_profiles", {}).get(layout, {})
        known = self.state.get("source_accounts", {}).get(source_key, "")

        def use_profile(profile):
            for index, column in enumerate(profile["columns"]):
                if index == 3:
                    choices[index].current(0 if column is None else column + 1)
                else:
                    choices[index].current(column)
            for index, value in ((4, profile["currency"]), (6, profile["account"])):
                choices[index].delete(0, "end")
                choices[index].insert(0, value)
            choices[5].set(profile["date_order"])
            choices[8].current(int(profile["flip_sign"]))
            if profile.get("period"):
                choices[7].set(profile["period"])

        if initial_spec:
            use_profile(initial_spec)
        elif known in profiles:
            use_profile(profiles[known])
        elif len(profiles) == 1:
            use_profile(next(iter(profiles.values())))
        if profiles:
            ttk.Label(form, text="저장된 설정").grid(row=9, column=0, sticky="w")
            picker = ttk.Combobox(form, values=list(profiles), state="readonly", width=39)
            picker.grid(row=9, column=1)
            picker.bind("<<ComboboxSelected>>", lambda _event: use_profile(profiles[picker.get()]))

        def load():
            primary = [choice.current() for choice in choices[:3]]
            currency_index = choices[3].current() - 1
            default = choices[4].get().strip().upper()
            problems = [f"‘{labels[index]}’이 비어 있어요. 목록에서 열을 고르세요." for index, value in enumerate(primary) if value == -1]
            chosen = [value for value in primary if value != -1]
            for value in sorted(set(chosen)):
                same = [labels[index] for index, item in enumerate(primary) if item == value]
                if len(same) > 1:
                    problems.append(f"{' · '.join(same)}에 같은 열({columns[value]})을 골랐어요. 서로 다른 열을 고르세요.")
            if currency_index >= 0 and currency_index in primary:
                problems.append(f"‘통화 열’이 다른 칸과 같은 열({columns[currency_index]})이에요. ‘기본 통화 사용’을 고르거나 다른 열을 고르세요.")
            if not re.fullmatch(r"[A-Z]{3}", default):
                problems.append("‘기본 통화’에 EUR처럼 세 글자를 적으세요.")
            if problems:
                messagebox.showerror("열 지정 확인", "\n".join(problems) + "\n\n파일의 열: " + ", ".join(columns[:12]), parent=dialog)
                return
            account = choices[6].get().strip()
            period = "" if choices[7].current() == 0 else choices[7].get().strip()
            if period and not re.fullmatch(MONTH_FOLDER, period):
                messagebox.showerror("마감월 확인", "YYYY-MM 형식으로 입력하세요.", parent=dialog)
                return
            if not account:
                messagebox.showerror("명세서 별칭", "같은 계좌나 카드는 매번 같은 별칭을 입력하세요.", parent=dialog)
                return
            if source_key in self.statement_sources:
                messagebox.showinfo("이미 불러온 명세서", source.name, parent=dialog)
                return
            spec = {"path": str(source), "columns": [*primary, currency_index if currency_index >= 0 else None],
                    "currency": default, "date_order": choices[5].get(), "account": account,
                    "period": period, "flip_sign": choices[8].current() == 1, "layout": layout}
            with Busy(dialog, "명세서를 읽고 있어요"):
                transactions, errors, check, discrepancy = self.preview_import(source, rows, spec)
            if is_pdf:
                preview = "\n".join(f"{item.day}  {item.merchant}  {item.amount} {item.currency}" for item in transactions[:8])
                if not ask_yes_no("PDF 추출 확인", f"{len(transactions)}건 추출 · 확인 {len(errors)}행\n{check}\n\n{preview}\n\n계속할까요?", parent=dialog):
                    return
            if on_review:
                on_review(source_key, spec, transactions, errors, check, discrepancy)
                dialog.destroy()
                owner.grab_set()
                return
            spec = self.resolve_overlaps(source_key, spec, transactions, dialog)
            if spec is None:
                return
            with Busy(self.root, f"거래 {len(transactions)}건을 저장하고 있어요"):
                added = self.add_statement(source_key, spec, transactions, errors)
            if not added:
                return
            dialog.destroy()
            if errors:
                preview = "\n".join(f"{line}행 · {error}" for line, error in errors[:10])
                messagebox.showwarning("읽지 못한 거래", f"{len(errors)}개 행을 확인하세요.\n\n{preview}", parent=self.root)
            if not on_review:
                self.announce_import(1, len(transactions), len(errors))

        ttk.Button(form, text="설정 확인" if on_review else "불러오기", command=load).grid(row=10, column=1, sticky="e", pady=(8, 0))

    def parse_spec(self, rows, spec):
        return parse_transactions(rows, spec["columns"], spec["currency"], spec["date_order"],
                                  spec["account"], spec["flip_sign"])

    def statement_home(self, spec, transactions):
        """Folder inside the evidence folder where an imported statement is kept, or None."""
        if self.practice_folder or not self.folder:
            return None
        month = spec.get("period") or ""
        if not re.fullmatch(MONTH_FOLDER, month):
            months = Counter(item.day.strftime("%Y-%m") for item in transactions)
            month = months.most_common(1)[0][0] if months else date.today().strftime("%Y-%m")
        return self.folder / STATEMENT_FOLDER / month

    def file_statement(self, spec, transactions):
        """Move an imported statement into 명세서/YYYY-MM of the evidence folder without changing its content.

        Returns (spec, moved_from). The file is left where it is when there is no evidence folder,
        it is already inside the statement folder, or the move fails."""
        home = self.statement_home(spec, transactions)
        source = Path(spec["path"])
        if home is None or not source.is_file() or source.is_symlink():
            return spec, None
        try:
            if source.resolve().is_relative_to((self.folder / STATEMENT_FOLDER).resolve()):
                return spec, None
            home.mkdir(parents=True, exist_ok=True)
            target, counter = home / source.name, 2
            while target.exists():
                target = home / f"{source.stem} ({counter}){source.suffix}"
                counter += 1
            if import_mode() == "copy":
                shutil.copy2(source, target)
                return {**spec, "path": str(target), "original_path": str(source), "copied": True}, None
            move_without_overwrite(source, target)
        except OSError as error:
            record_error("statement-move", error)
            self.status.set(f"명세서를 작업 폴더로 옮기지 못해 원래 위치에 두었어요 · {error}")
            return spec, None
        return {**spec, "path": str(target), "original_path": str(source)}, source

    def file_loaded_statements(self):
        """Move statements that were read before the evidence folder was chosen."""
        if not self.session_specs or not self.statement_home({}, []):
            return 0
        state, moved = deepcopy(self.state), []
        for source_key, spec in list(self.session_specs.items()):
            items = [item for key, item in self.transactions.items()
                     if source_key in self.state.get("transaction_sources", {}).get(key, {})]
            filed, original = self.file_statement(spec, items)
            if original is None:
                continue
            moved.append((source_key, Path(spec["path"]), Path(filed["path"])))
            self.session_specs[source_key] = filed
            self.statement_sources[source_key] = Path(filed["path"])
            state.setdefault("statement_paths", {})[source_key] = filed["path"]
            state.setdefault("source_specs", {})[source_key] = filed
        if not moved:
            return 0
        state["session"] = {"sources": dict(self.session_specs), "folder": str(self.folder)}
        if not self.save(state):
            for source_key, original, target in reversed(moved):
                try:
                    move_without_overwrite(target, original)
                except OSError as error:
                    record_error("statement-move", error)
            return 0
        self.refresh()
        show_notice(self.root, f"명세서 {len(moved)}개를 증빙 폴더 안 ‘{STATEMENT_FOLDER}/월’ 폴더로 옮겼어요.\n"
                    "내용은 바꾸지 않았고, 원래 위치도 기록해 두었어요.")
        return len(moved)

    def add_statement(self, source_key, spec, transactions, errors, remember=True):
        moved_from = None
        if remember:
            spec, moved_from = self.file_statement(spec, transactions)
        state = deepcopy(self.state)
        resolutions = spec.get("resolutions", state.get("source_resolutions", {}).get(source_key, {}))
        originals = {resolutions.get(item.key, item.key): item.key for item in transactions}
        transactions = [replace(item, key=resolutions.get(item.key, item.key)) for item in transactions]
        assign_numbers(state["records"], transactions, spec["period"])
        recurring = apply_recurring(state, transactions)
        for item in transactions:
            state["records"][item.key].update(merchant=item.merchant, day=item.day.isoformat(),
                                              amount=str(item.amount), currency=item.currency,
                                              overlap_base=originals[item.key])
            state.setdefault("transaction_sources", {}).setdefault(item.key, {})[source_key] = Path(spec["path"]).name
        account = spec["account"]
        source = Path(spec["path"])
        start, end, printed = statement_period(source, transactions)
        state.setdefault("statement_periods", {})[source_key] = {
            "account": account, "file": source.name, "start": start, "end": end, "printed": printed,
        }
        state.setdefault("source_accounts", {})[source_key] = account
        if remember and spec.get("layout"):
            profiles = state.setdefault("import_profiles", {}).setdefault(spec["layout"], {})
            label = profiles.get(account, {}).get("label")
            profiles[account] = {key: spec[key] for key in PROFILE_FIELDS}
            if label:
                profiles[account]["label"] = label
        state.setdefault("statement_paths", {})[source_key] = str(source)
        state.setdefault("source_resolutions", {})[source_key] = resolutions
        state.setdefault("source_specs", {})[source_key] = {**spec, "resolutions": resolutions}
        specs = {**self.session_specs, source_key: {**spec, "resolutions": resolutions}}
        if remember:
            state["session"] = {"sources": specs, "folder": str(self.folder) if self.folder else ""}
        if not self.save(state):
            if spec.get("copied"):
                Path(spec["path"]).unlink(missing_ok=True)
            if moved_from:
                try:
                    move_without_overwrite(source, moved_from)
                except OSError as error:
                    record_error("statement-move", error)
            return False
        self.session_specs = specs
        self.transactions.update((item.key, item) for item in transactions)
        self.accounts.update((item.key, account) for item in transactions)
        self.statement_sources[source_key] = source
        self.transaction_sources.update((item.key, source) for item in transactions)
        self.errors.extend((f"{source.name}:{line}", error) for line, error in errors)
        if moved_from and self.folder and moved_from.parent.resolve() == self.folder.resolve():
            self.load_pdfs(self.folder)  # the statement had been listed as evidence before the move
        self.refresh()
        if moved_from:
            self.status.set(f"명세서를 ‘{source.parent.relative_to(self.folder).as_posix()}’ 폴더로 옮겼어요 · {source.name}")
        if recurring:
            self.status.set(f"정기 결제(월세·할부 등) {recurring}건은 등록한 규칙대로 ‘증빙 없이 처리’했어요.")
        return True

    def resume(self):
        session = self.state.get("session", {})
        sources = session.get("sources", {})
        if not sources:
            self.notice("이어 열 수 있는 명세서가 없어요.")
            return
        if self.transactions and not ask_yes_no("이어서 작업", "현재 화면을 마지막 저장 작업으로 바꿀까요?", parent=self.root):
            return
        self.reload_sources(sources, session.get("folder"))

    def reload_sources(self, sources, folder=None):
        """Read saved statements again with their saved settings. Returns the number loaded."""
        prepared, problems = {}, []
        busy = Busy(self.root, "명세서를 다시 읽고 있어요")
        for position, (checksum, spec) in enumerate(sources.items(), 1):
            path = Path(spec["path"])
            busy.step(position, len(sources), path.name)
            try:
                if digest(path) != checksum:
                    raise ValueError("명세서 내용이 변경됐어요.")
                pdf = path.suffix.lower() == ".pdf"
                _, rows = read_statement_pdf(path) if pdf else read_csv(path, self.imported_before(checksum))
                transactions, errors = self.parse_spec(rows, spec)
                if pdf:
                    check, discrepancy = statement_check(path, transactions)
                    if discrepancy:
                        errors.append(("검산", check))
                prepared[checksum] = (spec, transactions, errors)
            except (OSError, ValueError, RuntimeError, csv.Error) as error:
                problems.append(f"{path.name}: {error}")
        busy.close()
        if prepared:
            self.transactions.clear()
            self.accounts.clear()
            self.session_specs.clear()
            self.statement_sources.clear()
            self.transaction_sources.clear()
            self.errors.clear()
            self.unresolved.set(True)
            for checksum, (spec, transactions, errors) in prepared.items():
                self.add_statement(checksum, spec, transactions, errors, remember=False)
            if folder:
                self.load_pdfs(Path(folder))
        if problems:
            messagebox.showwarning("다시 열지 못한 명세서", "\n".join(problems), parent=self.root)
        return len(prepared)

    def saved_statements(self):
        """Every statement imported before: {checksum: (months, account, path, spec or None)}."""
        specs = {**self.state.get("session", {}).get("sources", {}), **self.state.get("source_specs", {})}
        result = {}
        for checksum, path in self.state.get("statement_paths", {}).items():
            period = self.state.get("statement_periods", {}).get(checksum, {})
            months = set()
            for key, sources in self.state.get("transaction_sources", {}).items():
                if checksum in sources and key in self.state["records"]:
                    months.add(self.state["records"][key].get("period", ""))
            if not months and period.get("start"):
                months.add(str(period["start"])[:7])
            result[checksum] = (sorted(month for month in months if month), period.get("account", ""), Path(path),
                                specs.get(checksum))
        return result

    BUILT_IN_FORMATS = (
        ("American Express (PDF)", "내장 판독기", "americanexpress.de · Abrechnungszeitraum"),
        ("DKB Kreditkarte (PDF)", "내장 판독기", "Deutsche Kreditbank AG · Ihre Abrechnung vom"),
        ("Deutsche Bank Konto (PDF)", "내장 판독기", "Deutsche Bank AG · Alter Saldo per"),
        ("기타 PDF", "표 자동 인식", "결과를 꼭 확인하세요. 정확하지 않으면 CSV를 쓰거나 샘플을 보내 주세요."),
    )

    def format_rows(self):
        """Saved import settings grouped by card/account name: [(layout, account, profile, files)]."""
        files = {}
        for checksum, info in self.state.get("statement_periods", {}).items():
            files.setdefault(info.get("account", ""), []).append(info.get("file", ""))
        rows = []
        for layout, profiles in self.state.get("import_profiles", {}).items():
            for account, profile in profiles.items():
                rows.append((layout, account, profile, files.get(account, [])))
        return sorted(rows, key=lambda row: (row[2].get("label") or row[1]).casefold())

    def manage_formats(self):
        dialog = InlineDialog(self.root)
        dialog.title("명세서 양식 관리")
        dialog.geometry("980x600")
        dialog.grab_set()
        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(side="bottom", fill="x", padx=14, pady=(6, 12))
        ttk.Label(dialog, text="명세서를 처음 불러올 때 고른 열 설정이 카드·계좌 이름별로 저장돼요. 같은 양식의 파일을 다시 불러오면 자동으로 적용돼요.",
                  wraplength=920, padding=(14, 10, 14, 4)).pack(anchor="w")
        tk.Label(dialog, text="저장된 양식", font=FONT_BOLD, background=COLORS["bg"], anchor="w").pack(fill="x", padx=14, pady=(6, 2))
        saved = self.table(dialog, ("표시 이름", "카드·계좌 (거래 구분용)", "통화", "날짜 순서", "부호 반전", "불러온 파일"),
                           (170, 170, 60, 100, 80, 360))
        tk.Label(dialog, text="프로그램에 들어 있는 PDF 양식", font=FONT_BOLD, background=COLORS["bg"], anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        built_in = self.table(dialog, ("양식", "방식", "알아보는 기준"), (220, 120, 600))
        built_in.configure(height=4)
        built_in.master.pack_configure(expand=False)
        for row in self.BUILT_IN_FORMATS:
            built_in.insert("", "end", values=row)
        rows = {}

        def fill():
            saved.delete(*saved.get_children())
            rows.clear()
            for index, (layout, account, profile, used) in enumerate(self.format_rows()):
                rows[str(index)] = (layout, account)
                saved.insert("", "end", iid=str(index), values=(
                    profile.get("label") or account, account, profile.get("currency", ""), profile.get("date_order", ""),
                    "예" if profile.get("flip_sign") else "", ", ".join(dict.fromkeys(used))[:120] or "-"))
            if not rows:
                saved.insert("", "end", values=("아직 저장된 양식이 없어요", "", "", "", "", ""))

        def chosen():
            selection = saved.selection()
            if not selection or selection[0] not in rows:
                messagebox.showinfo("명세서 양식", "목록에서 양식을 하나 선택하세요.", parent=dialog)
                return None
            return rows[selection[0]]

        def rename():
            target = chosen()
            if not target:
                return
            layout, account = target
            current = self.state["import_profiles"][layout][account].get("label") or account
            label = simpledialog.askstring("표시 이름", "목록에 보일 이름 (예: DKB 비자카드 개인)\n거래 구분용 이름은 바뀌지 않아요.",
                                           initialvalue=current, parent=dialog)
            if label is None:
                return
            state = deepcopy(self.state)
            state["import_profiles"][layout][account]["label"] = label.strip()
            if self.save(state):
                fill()

        def forget():
            target = chosen()
            if not target:
                return
            layout, account = target
            if not ask_yes_no("양식 삭제", f"‘{account}’ 양식 설정을 지울까요?\n이미 불러온 거래·연결·메모는 지워지지 않아요. 다음에 같은 파일을 불러오면 열을 다시 고르게 돼요.", parent=dialog):
                return
            state = deepcopy(self.state)
            del state["import_profiles"][layout][account]
            if not state["import_profiles"][layout]:
                del state["import_profiles"][layout]
            if self.save(state):
                fill()

        ttk.Button(footer, text="표시 이름 바꾸기", command=rename).pack(side="left")
        ttk.Button(footer, text="양식 삭제", command=forget).pack(side="left", padx=(6, 0))
        tk.Label(footer, text="새 PDF 양식이 필요하면 개인정보를 가린 샘플 1장을 보내 주세요.", background=COLORS["bg"],
                 foreground=COLORS["muted"]).pack(side="right")
        fill()

    def open_past_month(self):
        saved = self.saved_statements()
        if not saved:
            self.notice("아직 불러온 적 있는 명세서가 없어요.")
            return
        months = sorted({month for months, *_ in saved.values() for month in months}, reverse=True)
        dialog = InlineDialog(self.root)
        dialog.title("지난 달 열기")
        dialog.geometry("900x560")
        dialog.grab_set()
        ttk.Label(dialog, text="월을 고르면 그달 명세서를 다시 불러와요. 연결·메모·번호·제출 이력은 그대로 이어져요. 확정한 달은 ‘마감 확정·해제’에서 다시 열어야 수정할 수 있어요.",
                  wraplength=840, padding=(14, 10, 14, 4)).pack(anchor="w")
        top = tk.Frame(dialog, background=COLORS["bg"])
        top.pack(fill="x", padx=14, pady=(4, 8))
        tk.Label(top, text="월", font=FONT_BOLD, background=COLORS["bg"]).pack(side="left")
        month = ttk.Combobox(top, values=months, state="readonly", width=10)
        month.pack(side="left", padx=8)
        if months:
            month.current(0)
        table = self.table(dialog, ("선택", "명세서 파일", "카드·계좌", "포함 월", "상태"), (50, 300, 150, 150, 200))
        table.tag_configure("off", foreground=COLORS["muted"])
        picked = {}
        periods = self.state.get("periods", {})

        def state_of(path, spec):
            if spec is None:
                return "설정 없음 · 다시 추가 필요"
            if not path.is_file():
                return "파일 없음 · 위치 확인"
            return "열 수 있음"

        def fill(_event=None):
            table.delete(*table.get_children())
            picked.clear()
            chosen = month.get()
            for checksum, (covered, account, path, spec) in saved.items():
                if chosen not in covered:
                    continue
                ready = state_of(path, spec) == "열 수 있음"
                picked[checksum] = ready
                table.insert("", "end", iid=checksum, tags=() if ready else ("off",), values=(
                    "☑" if ready else "–", path.name, account, ", ".join(covered), state_of(path, spec)))
            locked = periods.get(chosen, {}).get("locked")
            info.set(f"{chosen} · {'마감 확정됨 (보기 전용)' if locked else '수정 가능'}")

        def toggle(event):
            key = table.identify_row(event.y)
            if key and state_of(saved[key][2], saved[key][3]) == "열 수 있음":
                picked[key] = not picked[key]
                table.set(key, "선택", "☑" if picked[key] else "☐")

        def go():
            chosen = {key: saved[key][3] for key, value in picked.items() if value}
            if not chosen:
                messagebox.showerror("지난 달 열기", "열 수 있는 명세서를 한 개 이상 선택하세요.", parent=dialog)
                return
            if self.transactions and not ask_yes_no("지난 달 열기", "지금 화면의 거래 대신 선택한 달의 명세서를 불러올까요?\n지금 작업은 ‘이어서 작업’으로 다시 열 수 있어요.", parent=dialog):
                return
            target = month.get()
            dialog.destroy()
            folder = self.folder or (Path(self.state.get("session", {}).get("folder")) if self.state.get("session", {}).get("folder") else None)
            if self.reload_sources(chosen, folder if folder and Path(folder).is_dir() else None):
                self.month_filter.set(target)
                self.refresh()
                self.status.set(f"{target} 명세서를 열었어요.")

        info = tk.StringVar()
        month.bind("<<ComboboxSelected>>", fill)
        table.bind("<Button-1>", toggle)
        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(footer, textvariable=info, background=COLORS["bg"], foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(footer, text="선택한 명세서 열기", style="Primary.TButton", command=go).pack(side="right")
        fill()

    def search_all_months(self):
        if not self.folder:
            self.select_folder()
            if not self.folder:
                return
        dialog = InlineDialog(self.root)
        dialog.title("전체 월 통합 검색")
        dialog.geometry("1100x520")
        toolbar = ttk.Frame(dialog, padding=8)
        toolbar.pack(fill="x")
        entry = ttk.Entry(toolbar, width=46)
        entry.pack(side="left")
        table = self.table(dialog, ("마감월", "거래번호", "거래처·증빙", "금액", "파일"), (100, 105, 245, 130, 490))
        hits = {}

        def search(_event=None):
            query = entry.get().strip()
            rows = search_records(self.state["records"], self.invoices, query)
            table.delete(*table.get_children())
            hits.clear()
            for index, (month, number, subject, amount, path) in enumerate(rows):
                ident = str(index)
                hits[ident] = path
                table.insert("", "end", iid=ident, values=(month, number, subject, amount,
                             str(path.relative_to(self.folder)) if path else ""))

        def open_found(_event=None):
            selection = table.selection()
            if selection and hits[selection[0]]:
                self.open_evidence_file(hits[selection[0]])

        self.buttons(toolbar, [("검색", search), ("증빙 열기", open_found)])
        entry.bind("<Return>", search)
        table.bind("<Double-1>", open_found)
        entry.focus_set()

    def resolve_overlaps(self, source_key, spec, transactions, parent):
        resolutions = dict(self.state.get("source_resolutions", {}).get(source_key, {}))
        old_records = self.state["records"]
        origins = self.state.get("transaction_sources", {})
        for item in transactions:
            if item.key in resolutions:
                continue
            previous = origins.get(item.key, {})
            if source_key in previous or (not previous and item.key not in self.transactions):
                continue
            previous_files = ", ".join(previous.values()) or "현재 열린 명세서"
            label = f"{item.day} · {item.merchant} · {item.amount} {item.currency}"
            answer = messagebox.askyesnocancel(
                "겹치는 거래 확인",
                f"기존 {old_records.get(item.key, {}).get('number', '')} · {previous_files}\n{label}\n\n"
                f"새 파일 · {Path(spec['path']).name}\n{label}\n\n"
                "예 = 같은 거래, 아니요 = 별도 결제, 취소 = 이번 가져오기 중단",
                parent=parent,
            )
            if answer is None:
                return None
            if answer:
                choices = {record["number"]: key for key, record in old_records.items()
                           if key == item.key or record.get("overlap_base") == item.key}
                if len(choices) > 1:
                    number = simpledialog.askstring("같은 거래 선택",
                        "연결할 기존 거래번호를 입력하세요.\n" + "\n".join(
                            f"{transaction_number} · {', '.join(origins.get(key, {}).values())}"
                            for transaction_number, key in choices.items()
                        ), parent=parent)
                    if not number or number.strip() not in choices:
                        if number:
                            messagebox.showerror("거래번호 확인", "목록에 있는 거래번호를 입력하세요.", parent=parent)
                        return None
                    resolutions[item.key] = choices[number.strip()]
                else:
                    resolutions[item.key] = item.key
            else:
                resolutions[item.key] = hashlib.sha256(f"separate:{source_key}:{item.key}".encode()).hexdigest()
        return {**spec, "resolutions": resolutions}

    def show_statement_periods(self):
        items = list(self.state.get("statement_periods", {}).values())
        if not items:
            self.notice("기간을 점검할 명세서를 불러오세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("계좌별 명세서 기간 점검")
        dialog.geometry("1050x510")
        ttk.Label(dialog, text="누락·겹침은 발행처가 기간을 표시한 명세서끼리 비교해요.", padding=8).pack(anchor="w")
        table = self.table(dialog, ("계좌·카드", "시작", "종료", "기간 근거", "명세서"), (140, 105, 105, 155, 515))
        for item in sorted(items, key=lambda row: (row["account"], row["start"], row["file"])):
            kind = "명세서 표기" if item["printed"] else "관측 거래일 범위"
            table.insert("", "end", values=(item["account"], item["start"], item["end"], kind, item["file"]))
        warnings = self.table(dialog, ("계좌·카드", "기간 점검", "시작", "종료"), (210, 250, 145, 145))
        for row in coverage_warnings(items):
            warnings.insert("", "end", values=row)
        if not warnings.get_children():
            ttk.Label(dialog, text="비교 가능한 기간 사이에 누락·겹침 표시가 없어요.", padding=8).pack(anchor="w")

    def show_unlinked_invoices(self):
        if not self.folder:
            self.select_folder()
            if not self.folder:
                return
        dialog = InlineDialog(self.root)
        dialog.title("거래에 연결되지 않은 증빙")
        dialog.geometry("1080x460")
        controls = ttk.Frame(dialog, padding=8)
        controls.pack(fill="x")
        status = ttk.Combobox(controls, values=UNLINKED_REASONS, state="readonly", width=16)
        status.current(0)
        status.pack(side="left", padx=5)
        table = self.table(dialog, ("발행일", "업체명", "총액", "분류", "증빙"), (100, 180, 120, 145, 500))
        linked = {key for record in self.state["records"].values() for key in record.get("links", [])}
        items = {key: doc for key, doc in self.by_key.items() if key not in linked}

        def fill():
            table.delete(*table.get_children())
            flags = self.state.get("unlinked_invoice_flags", {})
            for key, doc in sorted(items.items(), key=lambda pair: (pair[1].day or date.min, pair[1].path.name), reverse=True):
                amount = f"{signed_total(doc)} {doc.currency}" if doc.amount is not None else ""
                table.insert("", "end", iid=key, values=(doc.day or "", doc.seller, amount, flags.get(key, ""), doc.path.name))

        def save():
            if not table.selection():
                return
            state = deepcopy(self.state)
            flags = state.setdefault("unlinked_invoice_flags", {})
            for key in table.selection():
                if status.get():
                    flags[key] = status.get()
                else:
                    flags.pop(key, None)
            if self.save(state):
                fill()

        def open_file(_event=None):
            selected = table.selection()
            if len(selected) == 1:
                self.open_evidence_file(items[selected[0]].path)

        self.buttons(controls, [("선택 분류 저장", save), ("증빙 열기", open_file)])
        table.bind("<Double-1>", open_file)
        fill()

    def manage_reasons(self):
        """Edit the ‘증빙 없이 처리’ reasons: Korean label, German text for the advisor, counted as done or not."""
        dialog = InlineDialog(self.root)
        dialog.title("증빙 없이 처리 사유 관리 (한국어·독일어)")
        dialog.geometry("980x560")
        dialog.grab_set()
        ttk.Label(dialog, wraplength=930, padding=(14, 10, 14, 4), text=(
            "한국어 사유는 화면에서 고르는 이름, 독일어는 세무사 ZIP·Excel에 들어가는 문장이에요. "
            "‘완료로 보기’를 켠 사유(계약서를 이미 낸 월세·할부 등)는 ‘계약서 근거 확인’으로 완료 처리되고, "
            "끈 사유는 4단계에서 세무사 확인 대상으로 남아요. 기본 사유는 독일어·완료 여부만 고치거나 숨길 수 있어요.")).pack(anchor="w")
        footer = ttk.Frame(dialog, padding=(14, 8))
        footer.pack(side="bottom", fill="x")
        table = self.table(dialog, ("한국어 사유", "독일어 (세무사 전달)", "완료로 보기", "목록 표시"), (230, 470, 100, 90))
        reasons = reason_list()

        def fill():
            table.delete(*table.get_children())
            for index, entry in enumerate(reasons):
                table.insert("", "end", iid=str(index), values=(
                    entry["ko"] + ("" if entry["builtin"] else "  (추가)"), entry["de"],
                    "예 · 계약서 근거" if entry["contract"] else "아니요", "숨김" if entry["hidden"] else "보임"))
            resort(table)

        def store():
            try:
                save_reason_list(reasons)
            except OSError as error:
                show_notice(dialog, f"저장하지 못했어요: {error}", kind="error")
                return False
            fill()
            self.refresh()
            return True

        def chosen():
            selection = table.selection()
            if len(selection) != 1:
                show_notice(dialog, "사유 한 개를 고르세요.")
                return None
            return reasons[int(selection[0])]

        def form(entry=None):
            panel = InlineDialog(dialog)
            panel.title("사유 고치기" if entry else "사유 추가")
            panel.center(640, 330)
            panel.grab_set()
            body = ttk.Frame(panel, padding=16)
            body.pack(fill="both", expand=True)
            ttk.Label(body, text="한국어 사유").grid(row=0, column=0, sticky="w", pady=4)
            korean = ttk.Entry(body, width=56)
            korean.insert(0, entry["ko"] if entry else "")
            korean.grid(row=0, column=1, sticky="ew")
            if entry and entry["builtin"]:
                korean.configure(state="disabled")
            ttk.Label(body, text="독일어 (세무사 전달)").grid(row=1, column=0, sticky="w", pady=4)
            german = ttk.Entry(body, width=56)
            german.insert(0, entry["de"] if entry else "")
            german.grid(row=1, column=1, sticky="ew")
            contract = tk.BooleanVar(value=entry["contract"] if entry else False)
            ttk.Checkbutton(body, variable=contract, text="완료로 보기 (계약서·보험증권 등을 이미 세무사에게 냄)").grid(
                row=2, column=1, sticky="w", pady=(8, 0))
            ttk.Label(body, style="Muted.TLabel", wraplength=440, text=(
                "예: 한국어 ‘창고 월세 · 계약서 기제출’ → 독일어 ‘Lagermiete – Mietvertrag liegt dem Steuerbüro vor (Dauerbeleg)’"
            )).grid(row=3, column=1, sticky="w", pady=(8, 0))

            def save():
                name, text = korean.get().strip(), german.get().strip()
                if not name or not text:
                    show_notice(panel, "한국어 사유와 독일어 문장을 모두 적어 주세요.")
                    return
                if entry is None and any(other["ko"] == name for other in reasons):
                    show_notice(panel, "같은 이름의 사유가 이미 있어요.")
                    return
                if entry is None:
                    reasons.append({"ko": name, "de": text, "contract": contract.get(), "hidden": False, "builtin": False})
                else:
                    if not entry["builtin"] and name != entry["ko"]:
                        if self.reason_in_use(entry["ko"]):
                            show_notice(panel, "이미 거래에 쓴 사유라 이름은 바꿀 수 없어요. 독일어·완료 여부만 고치거나, 새 사유를 추가하세요.")
                            return
                        entry["ko"] = name
                    entry.update(de=text, contract=contract.get())
                if store():
                    panel.destroy()

            ttk.Button(body, text="저장", command=save).grid(row=4, column=1, sticky="e", pady=(14, 0))

        def edit():
            entry = chosen()
            if entry:
                form(entry)

        def toggle():
            entry = chosen()
            if entry:
                entry["hidden"] = not entry["hidden"]
                store()

        def remove():
            entry = chosen()
            if not entry:
                return
            if entry["builtin"]:
                show_notice(dialog, "기본 사유는 지울 수 없어요. ‘숨기기·보이기’로 목록에서 감출 수 있어요.")
                return
            if self.reason_in_use(entry["ko"]):
                entry["hidden"] = True
                store()
                show_notice(dialog, "이미 거래에 쓴 사유라 지우지 않고 숨겼어요. 독일어 문장은 계속 세무사 자료에 쓰여요.")
                return
            reasons.remove(entry)
            store()

        def restore():
            entry = chosen()
            if entry and entry["builtin"]:
                entry.update(de=NO_EVIDENCE_REASONS[entry["ko"]], contract=entry["ko"] in DAUERBELEG_REASONS, hidden=False)
                store()

        for label, command in (("+ 사유 추가", lambda: form()), ("고치기", edit), ("숨기기·보이기", toggle), ("지우기", remove),
                               ("기본값으로", restore)):
            ttk.Button(footer, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right")
        table.bind("<Double-1>", lambda event: edit())
        fill()

    def reason_in_use(self, korean):
        return any(record.get("statement_evidence") == korean for record in self.state["records"].values()) \
            or any(rule.get("reason") == korean for rule in self.state.get("recurring_rules", []))

    def manage_recurring(self):
        """Saved rules that mark rent, instalments etc. automatically; wrong ones can be deleted."""
        dialog = InlineDialog(self.root)
        dialog.title("자동 적용 규칙 (월세·할부·대출 등)")
        dialog.grab_set()
        ttk.Label(dialog, padding=(14, 10, 14, 4), wraplength=760, text=(
            "같은 거래처·같은 금액 거래가 명세서에 나오면 아래 사유로 ‘증빙 없이 처리’해요. "
            "금액이 바뀌면(월세 인상 등) 자동 적용되지 않으니, 새 금액 거래에서 다시 등록하세요. "
            "규칙을 지워도 이미 처리한 거래는 그대로예요.")).pack(anchor="w")
        table = self.table(dialog, ("거래처 예", "금액", "사유", "등록한 거래일"), (300, 110, 230, 110))
        footer = ttk.Frame(dialog, padding=(14, 8))
        footer.pack(fill="x")

        def fill():
            table.delete(*table.get_children())
            for index, rule in enumerate(self.state.get("recurring_rules", [])):
                table.insert("", "end", iid=str(index), values=(rule.get("example", rule["payee"]),
                                                                f"{rule['amount']} {rule['currency']}", rule["reason"], rule.get("since", "")))

        def delete():
            drop = {int(key) for key in table.selection()}
            if not drop:
                show_notice(dialog, "지울 규칙을 고르세요.")
                return
            state = deepcopy(self.state)
            state["recurring_rules"] = [rule for index, rule in enumerate(state.get("recurring_rules", [])) if index not in drop]
            if self.save(state):
                fill()

        ttk.Button(footer, text="선택 규칙 지우기", command=delete).pack(side="left")
        fill()

    def mark_statement_evidence(self):
        item = self.single_card()
        if not item:
            return
        record = self.record(item.key)
        if self.linked(item.key) or record.get("transfer") or record.get("refund_group"):
            self.notice("증빙 또는 별도 거래 연결이 없는 거래를 선택하세요.")
            return

        def commit(state, values):
            updated = state["records"][item.key]
            if values[0]:
                updated["statement_evidence"] = values[0]
            else:
                updated.pop("statement_evidence", None)
            rules = [rule for rule in state.get("recurring_rules", [])
                     if not (rule["payee"] == payee_key(item.merchant) and rule["amount"] == str(item.amount))]
            if values[0] and values[1] == RECURRING_AUTO[1]:
                rules.append({"payee": payee_key(item.merchant), "amount": str(item.amount), "currency": item.currency,
                              "reason": values[0], "example": item.merchant[:80], "since": item.day.isoformat()})
            state["recurring_rules"] = rules
            others = apply_recurring(state, list(self.transactions.values()))
            if others:
                self.status.set(f"같은 거래처·같은 금액 거래 {others}건에도 ‘{values[0]}’을 적용했어요.")

        known = any(rule["payee"] == payee_key(item.merchant) and rule["amount"] == str(item.amount)
                    for rule in self.state.get("recurring_rules", []))
        self.edit_fields("증빙 없이 처리 · 세무사 확인", self.card_label(item) + (
                             "\n월세·할부·대출처럼 계약서를 이미 세무사에게 냈다면 해당 사유를 고르세요. "
                             "‘다음부터 자동 적용’을 고르면 같은 거래처·같은 금액 거래는 다음 달부터 자동으로 처리돼요. "
                             "세무사에게 가는 독일어 문장과 사유 추가는 ‘증빙 없이 처리 ▾ → 사유 관리’에서 해요."),
                         [("증빙 없이 검토하는 사유", record.get("statement_evidence", ""),
                           [""] + [entry["ko"] for entry in reason_list() if not entry["hidden"]]),
                          ("다음부터 자동 적용", RECURRING_AUTO[1] if known else RECURRING_AUTO[0], list(RECURRING_AUTO))], commit)

    def select_folder(self):
        if self.practice_folder:
            self.load_pdfs(self.practice_folder / "Belege")
            return
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.folder)
        if folder:
            self.load_pdfs(Path(folder))
            if self.session_specs and self.folder == Path(folder) and not self.file_loaded_statements():
                state = deepcopy(self.state)
                state["session"] = {"sources": self.session_specs, "folder": str(self.folder)}
                self.save(state)

    def load_pdfs(self, folder):
        self.status.set("PDF를 읽고 있어요…")
        try:
            with Busy(self.root, "증빙을 읽고 있어요") as busy:
                self.invoices, self.read_issues = index_pdfs(folder, busy.step)
        except OSError as error:
            messagebox.showerror("PDF 폴더 열기 실패", str(error), parent=self.root)
            return
        self.folder = folder
        self.pdfs_loaded = True
        self.by_key = {}
        hashes = Counter(item.key for item in self.invoices)
        numbers = defaultdict(set)
        for invoice in self.invoices:
            self.by_key.setdefault(invoice.key, invoice)
            if invoice.seller and invoice.number:
                numbers[(merchant_key(invoice.seller), invoice.number.casefold())].add(invoice.key)
        self.duplicates = {key for key, count in hashes.items() if count > 1}
        for keys in numbers.values():
            if len(keys) > 1:
                self.duplicates.update(keys)
        self.refresh()

    def review_pairs(self, title, rows, commit, hint=""):
        """Checkbox list of (first, second, first label, second label[, reason, checked]) pairs to link at once."""
        if not rows:
            self.notice("한 번에 연결할 후보가 없어요. 거래를 하나씩 골라 오른쪽 추천 증빙을 확인하세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title(title)
        dialog.geometry("1040x520")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text=hint or "연결할 행에 체크하세요.", wraplength=980, padding=(14, 10, 14, 4)).pack(anchor="w")
        table = self.table(dialog, ("선택", "거래", "연결할 증빙", "판정"), (50, 340, 320, 300))
        checked = {str(index): bool(row[5]) if len(row) > 5 else False for index, row in enumerate(rows)}
        info = tk.StringVar()

        def fill():
            table.delete(*table.get_children())
            for index, row in enumerate(rows):
                key = str(index)
                table.insert("", "end", iid=key, values=("☑" if checked[key] else "☐", row[2], row[3], row[4] if len(row) > 4 else ""))
            info.set(f"체크 {sum(checked.values())}건 / 후보 {len(rows)}건")

        def toggle(event):
            key = table.identify_row(event.y)
            if key and table.identify_column(event.x) == "#1":
                checked[key] = not checked[key]
                fill()
                return "break"

        def set_all(value):
            for key in checked:
                checked[key] = value
            fill()

        def apply():
            selected = [rows[int(key)][:2] for key, value in checked.items() if value]
            if not selected:
                show_notice(dialog, "연결할 행에 체크하세요.")
                return
            state = deepcopy(self.state)
            for first, second in selected:
                commit(state["records"], first, second)
            if self.save(state):
                dialog.destroy()
                self.refresh()
                self.notice(f"{len(selected)}건을 한 번에 연결했어요.\n연결된 거래는 ‘확인 필요’ 목록에서 빠져요. "
                            "끝나면 ‘완료 폴더로 이동’을 누르세요.", title="일괄 연결 완료")

        def open_selected(_event=None):
            selection = table.selection()
            if selection:
                document = self.by_key.get(rows[int(selection[0])][1])
                if document:
                    self.open_evidence_file(document.path)

        table.bind("<Button-1>", toggle)
        table.bind("<Double-1>", open_selected)
        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(fill="x", padx=14, pady=10)
        ttk.Button(footer, text="전체 선택", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(footer, text="전체 해제", command=lambda: set_all(False)).pack(side="left", padx=(6, 0))
        ttk.Button(footer, text="증빙 열기", command=open_selected).pack(side="left", padx=(6, 0))
        tk.Label(footer, textvariable=info, background=COLORS["bg"], foreground=COLORS["muted"]).pack(side="left", padx=12)
        ttk.Button(footer, text="체크한 후보 연결", style="Primary.TButton", command=apply).pack(side="right")
        fill()

    def card_label(self, item):
        return f"{self.record(item.key).get('number', '')} · {item.day} · {item.merchant} · {item.amount} {item.currency}"

    def unlinked_transactions(self):
        return [item for item in self.transactions.values() if not self.linked(item.key)
                and not any(self.record(item.key).get(field) for field in ("transfer", "refund_group", "refund_of", "statement_evidence"))]

    def batch_pairs(self):
        """1:1 transaction–evidence pairs for bulk linking: [(transaction key, document key, sure)].

        sure: same amount, at most 7 days apart and the seller name or invoice number matches.
        Otherwise (almost): same amount, at most 7 days apart and no other open transaction or
        document with that amount competes for it."""
        used = {link for record in self.state["records"].values() for link in record.get("links", [])}
        open_items = self.unlinked_transactions()
        options, claims = {}, Counter()
        for item in open_items:
            found = [doc for doc in candidates(item, self.invoices, self.state["aliases"])
                     if doc.day and abs((doc.day - item.day).days) <= 7 and doc.key not in used and doc.key not in self.duplicates]
            unique = list({doc.key: doc for doc in found}.values())
            options[item.key] = unique
            claims.update(doc.key for doc in unique)
        pairs = []
        for item in open_items:
            found = options[item.key]
            if len(found) != 1 or claims[found[0].key] != 1:
                continue
            doc = found[0]
            reason = self.match_reason(item, doc)
            pairs.append((item.key, doc.key, "업체명" in reason or "번호" in reason or "아마존 판매자" in reason))
        return pairs

    def batch_candidates(self):
        pairs = self.batch_pairs()
        rows = [(first, second, self.card_label(self.transactions[first]), self.by_key[second].path.name,
                 ("확실 · " if sure else "거의 일치 · ") + self.match_reason(self.transactions[first], self.by_key[second]).lstrip("✓ "), sure)
                for first, second, sure in pairs]
        self.review_pairs("일괄 매칭", rows, lambda records, first, second: records[first].update(links=[second]),
                          hint="‘확실’은 미리 체크했어요. ‘거의 일치’는 업체명·번호가 확인되지 않은 후보라, 열어 보고 체크하세요.")

    def transfer_candidates(self):
        free = self.unlinked_transactions()
        marker = re.compile(r"amex|american express|dkb|kreditkart|lastschrift|zahlung|überweisung|umbuchung|eigenübertrag|transfer|카드대금|이체", re.I)
        pairs = [
            (a.key, b.key) for a in free if a.amount > 0 for b in free if b.amount == -a.amount
            and a.currency == b.currency and abs((a.day - b.day).days) <= 7
            and self.accounts.get(a.key) and self.accounts.get(b.key)
            and self.accounts[a.key] != self.accounts[b.key]
            and (marker.search(a.merchant) or marker.search(b.merchant))
        ]
        rows = [(first, second, self.card_label(self.transactions[first]), self.card_label(self.transactions[second]))
                for first, second in unique_pairs(pairs)]

        def link(records, first, second):
            records[first]["transfer"] = second
            records[second]["transfer"] = first

        self.review_pairs("카드대금·내부이체 후보", rows, link)

    def record(self, key):
        return self.state["records"].get(key, {})

    def matched_moves(self):
        periods = defaultdict(set)
        for record in self.state["records"].values():
            for link in record.get("links", []):
                periods[link].add(record.get("period", ""))
        eligible = {link for key in self.transactions if self.status_for(key) == "금액 대조 완료"
                    for link in self.linked(key)}
        moves = []
        skipped = 0
        for link in sorted(eligible):
            doc = self.by_key[link]
            if link in self.duplicates or len(periods[link]) != 1:
                skipped += 1
                continue
            month = next(iter(periods[link]))
            if not re.fullmatch(MONTH_FOLDER, month):
                skipped += 1
                continue
            parent = doc.path.parent
            if re.fullmatch(MATCHED_FOLDER, parent.name) and parent != self.folder:
                parent = parent.parent
            numbers = {self.record(key).get("number", "") for key in self.transactions if link in self.linked(key)}
            name = package_filename(numbers.pop(), doc) if len(numbers) == 1 else doc.path.name
            target = parent / f"매칭완료_{month[2:].replace('-', '')}" / name
            if doc.path != target:
                moves.append((doc, target))
        return moves, skipped

    def archive_matched(self):
        if self.folder:
            self.load_pdfs(self.folder)  # file names may have changed in step 1 since the folder was read
        moves, skipped = self.matched_moves()
        if not moves:
            self.notice(f"옮길 매칭완료 증빙이 없어요. 중복 또는 마감월 확인 필요 {skipped}개.")
            return
        preview = "\n".join(str(target.relative_to(self.folder)) for _, target in moves[:5])
        more = f"\n… 외 {len(moves) - 5}개" if len(moves) > 5 else ""
        if not ask_yes_no("매칭완료 증빙 이동", f"증빙 {len(moves)}개를 옮길까요?\n\n{preview}{more}", parent=self.root):
            return
        errors = []
        history = []
        busy = Busy(self.root, "매칭완료 폴더로 옮기고 있어요")
        for position, (doc, target) in enumerate(moves, 1):
            busy.step(position, len(moves), doc.path.name)
            try:
                if doc.path.is_symlink() or not target.resolve().is_relative_to(self.folder.resolve()):
                    raise OSError("선택한 증빙 폴더 밖으로 이동할 수 없어요.")
                if digest(doc.path) != doc.key:
                    raise OSError("증빙이 변경됐어요. 증빙 폴더를 다시 불러오세요.")
                remember_original_name(doc.path, doc.key)
                target.parent.mkdir(exist_ok=True)
                move_evidence(doc.path, target)
                history.append((doc.path, target, doc.key))
            except OSError as error:
                errors.append(f"{doc.path.name}: {error}")
        if history:
            self.last_moves = history
        busy.step(text="증빙 목록을 새로 읽고 있어요")
        self.load_pdfs(self.folder)
        busy.close()
        moved = len(moves) - len(errors)
        self.view.set("확인 필요")
        self.refresh()
        if errors:
            self.status.set(f"{moved}개 이동 · 확인 필요 {skipped}개 · 실패 {len(errors)}개")
            messagebox.showwarning("이동 결과", "\n".join(errors[:8]), parent=self.root)
            return
        remaining = len(self.cards.get_children())
        skipped_text = f"\n중복·마감월 확인이 필요해 옮기지 않은 증빙 {skipped}개가 있어요." if skipped else ""
        self.notice(f"증빙 {moved}개를 ‘매칭완료_월’ 폴더로 옮겼어요. 마크다운도 함께 옮겼어요.{skipped_text}\n"
                    f"목록에는 아직 확인이 필요한 거래 {remaining}건만 보여요. 끝난 거래는 위 ‘대조 완료’에서 볼 수 있어요.",
                    title="매칭완료 이동 완료",
                    action=("다음 단계: 누락 점검 →", lambda: self.navigate(4)) if self.navigate and not remaining else None)

    def undo_archive(self):
        if not self.last_moves:
            self.notice("되돌릴 증빙 이동이 없어요.")
            return
        if not ask_yes_no("이동 되돌리기", f"증빙 {len(self.last_moves)}개를 원래 폴더로 되돌릴까요?", parent=self.root):
            return
        restored, failed = undo_moves(self.last_moves)
        self.last_moves = [item[:3] for item in reversed(failed)]
        if self.folder:
            self.load_pdfs(self.folder)
        self.status.set(f"{len(restored)}개 복원 · 실패 {len(failed)}개")
        if failed:
            messagebox.showwarning("복원 실패", "\n".join(f"{item[1].name}: {item[3]}" for item in failed[:8]), parent=self.root)

    def linked(self, key):
        return self.record(key).get("links", [])

    def group(self, first):
        transactions, invoices = {first}, set()
        while True:
            updated = invoices.union(*(self.linked(key) for key in transactions))
            related = {key for key in self.transactions if updated.intersection(self.linked(key))}
            if updated == invoices and related <= transactions:
                return transactions, invoices
            invoices = updated
            transactions.update(related)

    def status_for(self, key):
        earlier = self.record(key).get("refund_of")
        if earlier:
            return EARLIER_REFUND if earlier in self.state["records"] else "환불 상대 명세서 필요"
        refunds = self.record(key).get("refund_group")
        if refunds:
            if any(other not in self.transactions for other in refunds):
                return "환불 상대 명세서 필요"
            remaining = sum(self.transactions[other].amount for other in refunds)
            if remaining:
                if any(self.linked(other) for other in refunds):
                    return PARTIAL_REFUND_DONE  # the invoice of the purchase covers what was kept
                return f"일부 환불 · 잔액 {remaining:.2f} {self.transactions[key].currency} · 증빙 필요"
            return "전액 환불 확인"
        peer = self.record(key).get("transfer")
        if peer:
            if peer not in self.transactions or self.record(peer).get("transfer") != key:
                return "이체 상대 명세서 필요"
            return "내부이체 확인"
        links = self.linked(key)
        if not links:
            evidence = self.record(key).get("statement_evidence")
            if is_contract_reason(evidence):
                return CONTRACT_STATUS
            return "명세서 근거 · 세무사 확인" if evidence else "증빙 미연결"
        transactions, invoices = self.group(key)
        if not invoices <= self.by_key.keys():
            return "연결 PDF 위치 확인"
        docs = [self.by_key[item] for item in invoices]
        cards = [self.transactions[item] for item in transactions]
        if any(doc.amount is None for doc in docs):
            return "연결됨 · PDF 총액 확인"
        currencies = {item.currency for item in [*cards, *docs]}
        if len(currencies) != 1:
            return "연결됨 · 통화 확인"
        difference = sum(item.amount for item in cards) - sum(signed_total(doc) for doc in docs)
        if difference:
            return f"연결됨 · 차액 {difference:.2f} {cards[0].currency}"
        return "금액 대조 완료"

    def refresh(self):
        selected = self.cards.selection()
        self.cards.delete(*self.cards.get_children())
        counts = Counter()
        view = self.view.get()
        month = self.month_filter.get()
        chip_counts = Counter()
        candidates = {}
        if getattr(self, "step", None) == 3 and self.invoices:
            candidates = {key: clear for key, _, clear in self.batch_pairs()}
        for key, item in self.transactions.items():
            state = self.status_for(key)
            counts[state] += 1
            record = self.record(key)
            if month not in ("전체", record.get("period")):
                continue
            follow = record.get("follow_up")
            tag = transaction_tag(state, follow or "")
            unresolved = follow in ("요청 중", "세무사 확인 필요", "증빙 확보 불가") or state not in DONE_STATES
            matches = {"전체": True, "확인 필요": unresolved, "증빙 없음": state == "증빙 미연결",
                       "대조 완료": tag in ("ok", "transfer")}
            chip_counts.update(name for name, hit in matches.items() if hit)
            if matches.get(view, True):
                shown = state
                if state == "증빙 미연결" and key in candidates:
                    shown = "후보 있음 · 확실" if candidates[key] else "후보 있음 · 거의 일치"
                kind, payee = split_merchant(item.merchant)
                self.cards.insert("", "end", iid=key, values=(
                    record.get("number", ""), item.day, payee, f"{item.amount} {item.currency}", shown, self.note_preview(key), kind,
                ), tags=(tag,))
        resort(self.cards)
        self.show_card_columns()
        kept = [key for key in selected if self.cards.exists(key)]
        if selected and not kept and self.cards.get_children():
            kept = [self.cards.get_children()[0]]  # the linked transaction left the list: go on with the next one
            self.cards.see(kept[0])
        self.cards.selection_set(kept)
        for name, chip in self.chip_buttons.items():
            chip.configure(text=f"{name} {chip_counts[name]}")
        self.cards_caption.set(f"{'전체 월' if month == '전체' else month} · {view} {len(self.cards.get_children())}건")
        self.update_batch_bar()
        self.show_invoices()
        self.status.set(
            f"거래 {len(self.transactions)}건 · 확인 완료 {counts['금액 대조 완료']}건 · "
            + (f"계약서 근거(월세·할부 등) {counts[CONTRACT_STATUS]}건 · " if counts[CONTRACT_STATUS] else "") +
            f"미연결 {counts['증빙 미연결']}건 · CSV 오류 {len(self.errors)}행 · "
            f"PDF {len(self.invoices)}개 · PDF 읽기 오류 {len(self.read_issues)}개"
        )
        if self.on_change:
            self.on_change()

    def advisor_phrases(self):
        """[(Korean button label, German text)] for the advisor memo, editable in 문구 관리."""
        saved = self.state.get("advisor_phrases")
        if saved is None:
            return list(DEFAULT_PHRASES)
        return [(item["label"], item["text"]) for item in saved if item.get("label") and item.get("text")]

    def manage_phrases(self, on_change=None):
        """Add, edit, reorder or delete the German phrases offered in the advisor memo."""
        phrases = self.advisor_phrases()
        dialog = InlineDialog(self.root)
        dialog.title("세무사 전달 문구 관리")
        dialog.geometry("900x560")
        dialog.grab_set()
        ttk.Label(dialog, text="메모·기록의 ‘세무사 전달 메모’에서 누르면 들어가는 문구예요. 버튼 이름은 한국어로, 문구는 독일어로 적어요.",
                  wraplength=860, padding=(14, 10, 14, 4)).pack(anchor="w")
        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(side="bottom", fill="x", padx=14, pady=10)
        form = tk.Frame(dialog, background=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"],
                        highlightcolor=COLORS["line"], padx=12, pady=10)
        form.pack(side="bottom", fill="x", padx=14, pady=(6, 0))
        table = self.table(dialog, ("버튼 이름 (한국어)", "넣을 문구 (독일어)"), (200, 640))
        tk.Label(form, text="버튼 이름", background=COLORS["panel"]).grid(row=0, column=0, sticky="w")
        label_entry = ttk.Entry(form, width=22)
        label_entry.grid(row=1, column=0, sticky="w", padx=(0, 10))
        tk.Label(form, text="독일어 문구", background=COLORS["panel"]).grid(row=0, column=1, sticky="w")
        text_entry = ttk.Entry(form, width=80)
        text_entry.grid(row=1, column=1, sticky="we")
        form.columnconfigure(1, weight=1)

        def fill(select=None):
            table.delete(*table.get_children())
            for index, (label, text) in enumerate(phrases):
                table.insert("", "end", iid=str(index), values=(label, text))
            if select is not None and table.exists(str(select)):
                table.selection_set(str(select))

        def chosen():
            selection = table.selection()
            return int(selection[0]) if selection else None

        def load(_event=None):
            index = chosen()
            if index is None:
                return
            label_entry.delete(0, "end")
            label_entry.insert(0, phrases[index][0])
            text_entry.delete(0, "end")
            text_entry.insert(0, phrases[index][1])

        def values():
            label, text = label_entry.get().strip(), text_entry.get().strip()
            if not label or not text:
                show_notice(dialog, "버튼 이름과 독일어 문구를 모두 적어 주세요.")
                return None
            try:
                german_input(text)
            except ValueError as error:
                show_notice(dialog, str(error), kind="warn")
                return None
            return label, text

        def add():
            pair = values()
            if pair:
                phrases.append(pair)
                fill(len(phrases) - 1)

        def change():
            index, pair = chosen(), values()
            if index is None:
                show_notice(dialog, "고칠 문구를 표에서 고르세요.")
            elif pair:
                phrases[index] = pair
                fill(index)

        def remove():
            index = chosen()
            if index is None:
                show_notice(dialog, "지울 문구를 표에서 고르세요.")
                return
            del phrases[index]
            fill()

        def move(step):
            index = chosen()
            if index is None or not 0 <= index + step < len(phrases):
                return
            phrases[index], phrases[index + step] = phrases[index + step], phrases[index]
            fill(index + step)

        def reset():
            phrases[:] = list(DEFAULT_PHRASES)
            fill()

        def save():
            state = deepcopy(self.state)
            state["advisor_phrases"] = [{"label": label, "text": text} for label, text in phrases]
            if self.save(state):
                dialog.destroy()
                if on_change:
                    on_change()
                self.status.set(f"세무사 전달 문구 {len(phrases)}개를 저장했어요.")

        table.bind("<<TreeviewSelect>>", load)
        buttons = tk.Frame(form, background=COLORS["panel"])
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for text, command in (("새 문구로 추가", add), ("선택 문구 고치기", change), ("선택 문구 지우기", remove),
                              ("▲ 위로", lambda: move(-1)), ("▼ 아래로", lambda: move(1)), ("기본 문구로 되돌리기", reset)):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(footer, text="취소", command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(footer, text="문구 저장", style="Primary.TButton", command=save).pack(side="right")
        fill()

    def update_batch_bar(self):
        """Step 3 banner that offers to link all clear 1:1 pairs at once."""
        if getattr(self, "step", None) != 3 or not self.invoices:
            self.batch_bar.pack_forget()
            return
        self.update_unmatched_button()
        pairs = self.batch_pairs()
        columns = self.cards["columns"]
        for key, _, clear in pairs:  # mark the candidates in the list, also when it was filled before the PDFs were read
            if self.cards.exists(key) and self.cards.set(key, columns[4]) == "증빙 미연결":
                self.cards.set(key, columns[4], "후보 있음 · 확실" if clear else "후보 있음 · 거의 일치")
        if not pairs:
            self.batch_bar.pack_forget()
            return
        sure = sum(1 for *_, clear in pairs if clear)
        almost = len(pairs) - sure
        detail = f"확실 {sure}건" + (f" · 거의 일치 {almost}건" if almost else "")
        self.batch_text.set(f"한 건씩 안 해도 돼요. 금액·날짜가 1:1로 맞는 후보 {len(pairs)}건 ({detail})")
        self.batch_button.configure(text=f"한 번에 연결하기 ({len(pairs)}건) →")
        if not self.batch_bar.winfo_ismapped():
            self.batch_bar.pack(fill="x", padx=18, pady=(0, 2), before=self.body)

    def match_reason(self, item, doc):
        """Short Korean reason why a document fits a transaction."""
        parts = ["금액"]
        if doc.seller and (merchant_key(doc.seller) == merchant_key(item.merchant)
                           or merchant_key(doc.seller) in self.state["aliases"].get(merchant_key(item.merchant), [])
                           or merchant_key(doc.seller) in merchant_key(item.merchant)):
            parts.append("업체명")
        elif amazon_marketplace(item.merchant, doc):
            parts.append("아마존 판매자")
        if matching_reference(item.merchant, doc):
            parts.append("번호")
        gap = abs((doc.day - item.day).days) if doc.day else None
        if gap is not None and gap <= 7:
            parts.append("날짜")
        text = "✓ " + " · ".join(parts) + " 일치"
        if gap is not None and gap > 7:
            text += f" · 날짜 {gap}일 차이"
        return text

    def update_match_panel(self, selected, linked, exact):
        """Best-candidate card on the right and the file-name preview at the bottom (step 3)."""
        self.best_doc = None
        self.best_card.pack_forget()
        self.none_card.pack_forget()
        if len(selected) != 1:
            self.pdfs_caption.set("거래 한 건을 고르세요" if not selected else f"거래 {len(selected)}건 선택")
            self.next_text.set("거래를 고르면 매칭완료 후 파일 이름을 미리 보여 줘요.")
            self.next_name.set("")
            return
        key = selected[0]
        item = self.transactions[key]
        number = self.record(key).get("number", "")
        self.pdfs_caption.set(f"{german_amount(item.amount)} {item.currency} · {item.day:%d.%m.%Y}")
        doc = None
        if linked:
            doc = next((self.by_key[link] for link in sorted(linked) if link in self.by_key), None)
            self.next_text.set("✅ 연결됨 · 매칭완료 폴더로 옮기면 이 이름이 돼요")
        elif exact:
            doc = exact[0]
            self.best_doc = doc
            self.best_title.set(f"추천 1순위 · {doc.seller or doc.path.stem} · {doc.day or '날짜 없음'}")
            self.best_file.set(doc.path.name)
            self.best_reason.set(self.match_reason(item, doc))
            self.best_card.pack(fill="x", before=self.pdfs.master)
            self.next_text.set("연결하고 매칭완료 폴더로 옮기면 이 이름이 돼요")
        else:
            self.next_text.set("금액이 같은 증빙이 없어요 · ‘전체 증빙에서 고르기’나 ‘증빙 없이 처리’를 쓰세요")
            if not self.all_pdfs.get() and not self.record(key).get("statement_evidence") and not self.record(key).get("transfer") \
                    and not self.record(key).get("refund_group"):
                self.none_card.pack(fill="x", before=self.pdfs.master)
                if item.amount < 0:
                    self.refund_button.pack(side="left", padx=(0, 6))
                else:
                    self.refund_button.pack_forget()
        self.next_name.set(package_filename(number, doc) if doc else "")

    def link_best(self):
        if self.best_doc is None:
            return
        for iid, doc in self.visible_pdfs.items():
            if doc.key == self.best_doc.key:
                self.pdfs.selection_set(iid)
                break
        self.link()

    def show_invoices(self):
        self.pdfs.delete(*self.pdfs.get_children())
        selected = self.cards.selection()
        linked = set().union(*(self.linked(key) for key in selected))
        exact, far = [], []
        seen = set(linked)
        for key in selected:
            item = self.transactions[key]
            for invoice in candidates(item, self.invoices, self.state["aliases"]):
                if invoice.key not in seen:
                    near = not invoice.day or abs((invoice.day - item.day).days) <= CANDIDATE_DAYS
                    (exact if near else far).append(invoice)
                    seen.add(invoice.key)
        if far:
            self.far_text.set(f"다른 달 {len(far)}")
            if not self.far_check.winfo_ismapped():
                self.far_check.pack(side="right", padx=(0, 10))
        elif self.far_check.winfo_ismapped():
            self.far_check.pack_forget()
        if far and (self.show_far.get() or not exact):  # nothing near: show the far ones instead of an empty list
            exact = exact + far
        else:
            seen -= {invoice.key for invoice in far}
        shown = [self.by_key[key] for key in sorted(linked) if key in self.by_key] + exact
        if self.all_pdfs.get():
            used_paths = {doc.path for doc in shown}
            shown.extend(doc for doc in self.invoices if doc.path not in used_paths)
        self.visible_pdfs = {}
        for index, doc in enumerate(shown):
            labels = []
            if doc.key in linked:
                labels.append("연결됨")
            elif doc.key in seen:
                labels.append("금액 일치 후보")
            if any(matching_reference(self.transactions[key].merchant, doc) for key in selected):
                labels.append("번호 일치")
            if doc.key in self.state.get("corrections", {}):
                labels.append("정정·원본 연결됨")
            if doc.key in self.duplicates:
                labels.append("중복 확인")
            if doc.credit:
                labels.append("전자 정정 문서")
            if doc.tax_fields and doc.tax_fields.get("special"):
                labels.append(doc.tax_fields["special"])
            if doc.tax_fields and doc.tax_fields.get("structured"):
                labels.append("E-Rechnung")
            self.visible_pdfs[str(index)] = doc
            best = exact[0].key if exact and not linked else None
            if doc.key == best:
                labels.insert(0, "추천 1순위")
            tag = "ok" if doc.key in linked or doc.key == best else "warn" if doc.key in seen else ""
            if doc.key in self.duplicates:
                tag = "bad"
            self.pdfs.insert("", "end", iid=str(index), tags=(tag,) if tag else (), values=(
                doc.path.name, doc.day or "", f"{signed_total(doc)} {doc.currency}" if doc.amount is not None else "",
                doc.number, " · ".join(labels),
            ))
        resort(self.pdfs)
        self.update_match_panel(selected, linked, exact)
        self.update_detail(selected)

    def statement_lines_for(self, item):
        """(source path, page index, rect, text lines) of a transaction in its statement file."""
        source = self.transaction_sources.get(item.key)
        if source is None or not Path(source).exists():
            return source, None, None, []
        try:
            page, rect, lines = statement_excerpt(source, item, self.excerpt_cache)
        except (OSError, ValueError, RuntimeError, csv.Error):
            return source, None, None, []
        return source, page, rect, lines

    def show_card_columns(self):
        """Step 3 hides the memo column and ‘구분’ (SEPA Lastschrift …, shown in the detail box below) so the payee
        has room; other steps show ‘구분’ when the statements have it."""
        kinds = getattr(self, "step", None) != 3 and any(split_merchant(item.merchant)[0] for item in self.transactions.values())
        columns = ("번호", "거래일") + (("구분",) if kinds else ()) + ("가맹점·거래처", "금액", "증빙 상태")
        if getattr(self, "step", None) != 3:
            columns += ("메모",)
        if tuple(self.cards["displaycolumns"]) != columns:
            self.cards.configure(displaycolumns=columns)

    def update_detail(self, selected=None):
        """Full statement text of the chosen transaction, below the list (copyable)."""
        selected = self.cards.selection() if selected is None else selected
        text = ""
        if len(selected) == 1 and selected[0] in self.transactions:
            item = self.transactions[selected[0]]
            source, page, _, lines = self.statement_lines_for(item)
            where = f"{Path(source).name}" + (f" · {page + 1}페이지" if page is not None else "") if source else "명세서 파일 없음"
            body = "\n".join(lines) if lines else item.merchant
            text = f"{self.record(item.key).get('number', '')} · {item.day} · {item.amount} {item.currency} · {where}\n{body}"
        elif len(selected) > 1:
            text = f"거래 {len(selected)}건을 골랐어요."
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", text)

    def show_in_statement(self, item=None):
        """Show the statement page part where the chosen transaction stands, with the lines boxed."""
        item = item or self.single_card()
        if item is None:
            return
        source, page, rect, lines = self.statement_lines_for(item)
        if source is None or not Path(source).exists():
            self.notice("이 거래의 명세서 파일을 찾지 못했어요. 2단계에서 명세서를 다시 불러오면 볼 수 있어요.")
            return
        window = InlineDialog(self.root)
        window.title(f"명세서에서 보기 · {self.record(item.key).get('number', '')}")
        window.geometry("900x720")
        window.grab_set()
        top = ttk.Frame(window, padding=(12, 10))
        top.pack(fill="x")
        ttk.Label(top, text=f"{Path(source).name}" + (f" · {page + 1}페이지" if page is not None else ""), font=FONT_BOLD).pack(side="left")
        ttk.Button(top, text="파일 열기", command=lambda: open_file(source)).pack(side="right")
        text = tk.Text(window, height=6, wrap="word", font=FONT_SMALL)
        text.insert("1.0", "\n".join(lines) if lines else "명세서에서 이 거래 줄을 찾지 못했어요. ‘파일 열기’로 확인하세요.")
        make_copyable(text)
        text.pack(side="bottom", fill="x", padx=12, pady=(4, 10))
        if Path(source).suffix.lower() != ".pdf" or page is None:
            return
        from pdf_view import PdfView
        view = PdfView(window)
        view.pack(fill="both", expand=True, padx=12)
        box = pymupdf.Rect(rect) if rect else None
        view.load(Path(source), page, {page: [box]} if box else {}, box)


    def save(self, state):
        locked = affected_locked_month(self.state, state)
        if locked:
            messagebox.showerror("마감 확정", f"{locked}은 확정된 달이에요. 변경하려면 ‘마감 확정·해제’에서 먼저 다시 여세요.", parent=self.root)
            return False
        fields = {"links": "증빙 연결", "transfer": "내부이체", "refund_group": "환불 연결", "refund_of": "전월 결제 환불",
                  "advisor_note": "전달 메모", "follow_up": "후속 상태", "due": "후속 예정일",
                  "allocations": "배분 금액", "difference_reason": "차액 사유", "statement_evidence": "명세서 근거", "attachments": "보조 자료"}
        for key, record in state["records"].items():
            old = self.state["records"].get(key, {})
            changed = [label for field, label in fields.items() if record.get(field) != old.get(field)]
            if changed:
                state.setdefault("audit", []).append({
                    "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "user": getpass.getuser(), "number": record.get("number", ""), "fields": ", ".join(changed),
                })
        try:
            write_state(self.state_path, state)
        except OSError as error:
            record_error("state", error)
            messagebox.showerror("저장 실패", str(error), parent=self.root)
            return False
        self.state = state
        self.refresh()
        return True

    def attachment_folder(self):
        return Path(self.state.get("attachment_root") or self.state_path.parent / "attachments")

    def edit_attachments(self):
        item = self.single_card()
        if not item:
            return
        dialog = InlineDialog(self.root)
        dialog.title("거래 보조 자료")
        dialog.geometry("800x420")
        dialog.transient(self.root)
        dialog.grab_set()
        bar = ttk.Frame(dialog, padding=8)
        bar.pack(fill="x")
        ttk.Label(dialog, text="추가한 자료는 독일어 제출 ZIP에 들어가요. 내부 자료는 여기 첨부하지 마세요.", padding=8).pack(anchor="w")
        table = self.table(dialog, ("파일명", "크기"), (585, 140))

        def entries():
            return self.record(item.key).get("attachments", [])

        def refresh():
            table.delete(*table.get_children())
            for index, doc in enumerate(entries()):
                path = attachment_path(self.attachment_folder(), doc)
                size = f"{path.stat().st_size // 1024} KB" if path.is_file() else "파일 위치 확인"
                table.insert("", "end", iid=str(index), values=(doc["name"], size))

        def add():
            names = filedialog.askopenfilenames(parent=dialog, filetypes=[
                ("보조 자료", "*.pdf *.xml *.png *.jpg *.jpeg *.eml *.txt *.csv")])
            if not names:
                return
            state = deepcopy(self.state)
            pending = state["records"][item.key].setdefault("attachments", [])
            try:
                for name in names:
                    entry = add_attachment(self.attachment_folder(), Path(name))
                    if entry not in pending:
                        pending.append(entry)
            except (OSError, ValueError) as error:
                messagebox.showerror("첨부 실패", str(error), parent=dialog)
                return
            if self.save(state):
                refresh()

        def remove():
            selected = {int(value) for value in table.selection()}
            if selected:
                state = deepcopy(self.state)
                state["records"][item.key]["attachments"] = [doc for index, doc in enumerate(entries()) if index not in selected]
                if self.save(state):
                    refresh()

        def open_file(_event=None):
            selected = table.selection()
            if len(selected) == 1:
                self.open_evidence_file(attachment_path(self.attachment_folder(), entries()[int(selected[0])]))

        self.buttons(bar, [("파일 첨부", add), ("선택 첨부 해제", remove), ("첨부 열기", open_file)])
        table.bind("<Double-1>", open_file)
        refresh()

    # Final check -------------------------------------------------------
    def final_checks(self, key, link_counts=None):
        """Automatic warnings a person should look at before the ZIP goes out (Korean, short)."""
        item, record = self.transactions[key], self.record(key)
        links = record.get("links", [])
        warnings = []
        if not links:
            if record.get("transfer") or record.get("refund_group") or record.get("refund_of"):
                return warnings
            evidence = record.get("statement_evidence")
            if not evidence:
                return ["증빙 없음"]
            if not is_contract_reason(evidence):
                warnings.append("증빙 없이 처리 · 세무사 확인")
            return warnings
        documents = [self.by_key.get(link) for link in links]
        if not all(documents):
            warnings.append("증빙 파일을 찾지 못함")
        documents = [doc for doc in documents if doc]
        if link_counts is None:
            link_counts = Counter(link for other in self.state["records"].values() for link in other.get("links", []))
        if any(link_counts[doc.key] > 1 for doc in documents) and not record.get("allocations"):
            warnings.append("같은 증빙이 다른 거래에도 연결")
        if any(doc.currency and doc.currency != item.currency for doc in documents):
            warnings.append("통화 다름")
        totals = [signed_total(doc) for doc in documents if doc.amount is not None and doc.currency == item.currency]
        if len(totals) == len(documents) and documents and not record.get("allocations") and not record.get("difference_reason"):
            gap = abs(sum(totals)) - abs(item.amount)
            if abs(gap) >= Decimal("0.01"):
                warnings.append(f"금액 차이 {gap:+.2f} {item.currency}")
        known = self.state["aliases"].get(merchant_key(item.merchant), [])
        merchant = merchant_key(item.merchant)
        for doc in documents:
            seller = merchant_key(doc.seller)
            words = [word for word in re.split(r"\W+", doc.seller.casefold()) if len(word) >= 4
                     and word not in ("gmbh", "kommanditgesellschaft", "europe", "germany", "deutschland", "services", "s.a.r.l")]
            if seller and not (seller in known or seller in merchant or merchant in seller
                               or any(merchant_key(word) in merchant for word in words) or matching_reference(item.merchant, doc)
                               or amazon_marketplace(item.merchant, doc)):
                warnings.append(f"업체명 다름 ({doc.seller[:24]})")
            if doc.day and abs((doc.day - item.day).days) > 7:
                warnings.append(f"날짜 {abs((doc.day - item.day).days)}일 차이")
        return warnings

    def final_review(self):
        """Go through the month's transactions one by one: statement text and invoice side by side, mark as checked."""
        month = self.month_filter.get()
        keys = [key for key in self.transactions if month in ("전체", self.record(key).get("period"))]
        if not keys:
            self.notice("검수할 거래가 없어요. 먼저 명세서를 불러오세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("최종 검수")
        dialog.center(1500, 980)
        dialog.grab_set()
        top = ttk.Frame(dialog, padding=(14, 10, 14, 4))
        top.pack(fill="x")
        progress = tk.StringVar()
        ttk.Label(top, textvariable=progress, font=FONT_BOLD).pack(side="left")
        order = tk.StringVar(value="확인할 것 먼저")
        ttk.Combobox(top, textvariable=order, state="readonly", width=16,
                     values=("확인할 것 먼저", "검수 안 한 것", "다시 확인", "전체")).pack(side="right")
        ttk.Label(top, text="보기").pack(side="right", padx=6)
        ttk.Label(dialog, style="Muted.TLabel", padding=(14, 0, 14, 6), wraplength=1400, text=(
            "왼쪽에서 거래를 고르면 명세서 원문과 인보이스가 나란히 보여요. 맞으면 Enter(검수 완료), 다시 봐야 하면 R(다시 확인). "
            "다음 거래로 자동으로 넘어가요. 프로그램이 찾은 주의할 점은 ‘자동 점검’ 칸과 오른쪽 위 빨간 글씨로 보여요.")).pack(anchor="w")
        layout = ttk.Panedwindow(dialog, orient="horizontal")
        layout.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        left = ttk.Frame(layout)
        right = tk.Frame(layout, background=COLORS["panel"])
        layout.add(left, weight=2)
        layout.add(right, weight=3)
        table = self.table(left, ("번호", "가맹점·거래처", "금액", "자동 점검", "검수", "거래일"), (66, 150, 92, 150, 76, 86))
        configure_tags(table)
        title = tk.StringVar()
        warning = tk.StringVar()
        tk.Label(right, textvariable=title, font=FONT_BOLD, background=COLORS["panel"], anchor="w").pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(right, textvariable=warning, font=FONT_BOLD, background=COLORS["panel"], foreground=COLORS["red"],
                 anchor="w", justify="left", wraplength=780).pack(fill="x", padx=12)
        statement = tk.Text(right, height=5, wrap="word", font=FONT_SMALL, relief="flat", background="#f6f8fa")
        statement.pack(fill="x", padx=12, pady=6)
        make_copyable(statement)
        chooser_row = tk.Frame(right, background=COLORS["panel"])
        chooser_row.pack(fill="x", padx=12)
        chooser = ttk.Combobox(chooser_row, state="readonly", width=70)
        chooser.pack(side="left")
        doc_caption = tk.StringVar()
        tk.Label(right, textvariable=doc_caption, font=FONT_SMALL, background=COLORS["panel"], foreground=COLORS["muted"],
                 anchor="w").pack(fill="x", padx=12, pady=(4, 0))
        extras = tk.Frame(right, background="#fafbfc", padx=10, pady=4)
        extras.pack(side="bottom", fill="x")
        tools = tk.Frame(right, background="#fafbfc", padx=10, pady=2)
        tools.pack(side="bottom", fill="x")
        buttons = tk.Frame(right, background="#fafbfc", padx=10, pady=8)
        buttons.pack(side="bottom", fill="x")
        note = ttk.Entry(extras, width=40)
        from pdf_view import PdfView
        viewer = PdfView(right, compact=True)
        viewer.pack(fill="both", expand=True, padx=12, pady=6)
        current = {"key": None, "docs": []}

        def label_for(key):
            checked = review_state(self.state, key)
            return {"ok": "✓ 완료", "recheck": "! 다시 확인", "changed": "변경됨 · 다시"}.get(checked, "")

        def fill(keep=None):
            link_counts = Counter(link for other in self.state["records"].values() for link in other.get("links", []))
            rows = []
            for key in keys:
                found = self.final_checks(key, link_counts)
                checked = review_state(self.state, key)
                rows.append((key, found, checked))
            view = order.get()
            if view == "검수 안 한 것":
                rows = [row for row in rows if row[2] not in ("ok",)]
            elif view == "다시 확인":
                rows = [row for row in rows if row[2] in ("recheck", "changed")]
            elif view == "확인할 것 먼저":
                rows.sort(key=lambda row: (row[2] == "ok", not row[1], self.record(row[0]).get("number", "")))
            table.delete(*table.get_children())
            for key, found, checked in rows:
                item = self.transactions[key]
                tag = "ok" if checked == "ok" else "bad" if checked in ("recheck", "changed") else "warn" if found else ""
                table.insert("", "end", iid=key, tags=(tag,) if tag else (), values=(
                    self.record(key).get("number", ""), split_merchant(item.merchant)[1],
                    f"{item.amount} {item.currency}", " · ".join(found) or "문제 없음", label_for(key), item.day))
            resort(table)
            done = sum(1 for key in keys if review_state(self.state, key) == "ok")
            again = sum(1 for key in keys if review_state(self.state, key) in ("recheck", "changed"))
            flagged = sum(1 for key, found, checked in rows if found and checked != "ok")
            progress.set(f"{'전체 월' if month == '전체' else month} · 검수 완료 {done}/{len(keys)}건 · 다시 확인 {again}건 · "
                         f"자동 점검에서 볼 것 {flagged}건")
            target = keep if keep and table.exists(keep) else (table.get_children() or [None])[0]
            if target:
                table.selection_set(target)
                table.focus(target)
                table.see(target)

        def show(_event=None):
            selection = table.selection()
            if len(selection) != 1:
                return
            key = selection[0]
            item, record = self.transactions[key], self.record(key)
            current["key"] = key
            title.set(f"{record.get('number', '')} · {item.day} · {item.amount} {item.currency} · {split_merchant(item.merchant)[1]}")
            found = self.final_checks(key)
            warning.set(("주의: " + " · ".join(found)) if found else "")
            source, page, _, lines = self.statement_lines_for(item)
            statement.configure(state="normal")
            statement.delete("1.0", "end")
            where = Path(source).name + (f" · {page + 1}페이지" if page is not None else "") if source else "명세서 파일 없음"
            statement.insert("1.0", f"[명세서 · {where}]\n" + ("\n".join(lines) if lines else item.merchant))
            entry = self.state.get("final_review", {}).get(key, {})
            note.delete(0, "end")
            note.insert(0, entry.get("note", ""))
            docs = [self.by_key[link] for link in record.get("links", []) if link in self.by_key]
            current["docs"] = docs
            if docs:
                chooser.configure(values=[f"{index}. {doc.path.name}" for index, doc in enumerate(docs, 1)])
                chooser.current(0)
            else:
                evidence = record.get("statement_evidence")
                chooser.configure(values=[])
                chooser.set(f"증빙 없이 처리: {evidence} → {reason_german(evidence)}" if evidence else
                            "내부이체·환불 연결" if record.get("transfer") or record.get("refund_group") or record.get("refund_of") else "연결된 증빙 없음")
            draw()

        def draw(_event=None):
            docs = current["docs"]
            if not docs:
                doc_caption.set("")
                viewer.message("연결된 증빙이 없어요.")
                return
            doc = docs[max(0, chooser.current())]
            doc_caption.set(f"발행일 {doc.day or '없음'} · {doc.seller or '업체명 없음'} · "
                            f"{signed_total(doc) if doc.amount is not None else '총액 없음'} {doc.currency} · 번호 {doc.number or '없음'}")
            if doc.path.suffix.lower() == ".xml":
                viewer.message("XRechnung/ZUGFeRD XML이에요. ‘인보이스 열기’로 원본을 보세요.")
                return
            viewer.load(doc.path, 0)

        def mark(status):
            key = current["key"]
            if not key:
                return "break"
            state = deepcopy(self.state)
            reviews = state.setdefault("final_review", {})
            if status:
                reviews[key] = {"status": status, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "user": getpass.getuser(), "note": note.get().strip(),
                                "signature": review_signature(state["records"].get(key, {}))}
            else:
                reviews.pop(key, None)
            if not self.save(state):
                return "break"
            rows = list(table.get_children())
            following = rows[rows.index(key) + 1] if key in rows and rows.index(key) + 1 < len(rows) else key
            if order.get() != "전체" and status:
                fill(following)  # the checked row may leave the list; go on with the next one
            else:
                table.set(key, "검수", label_for(key))
                table.item(key, tags=("ok",) if status == "ok" else ("bad",) if status else ())
                fill(following if status else key)
            return "break"

        def key_mark(event, status):
            if isinstance(event.widget, (ttk.Entry, tk.Entry, tk.Text)) and status == "recheck":
                return None
            return mark(status)

        ttk.Button(buttons, text="✓ 검수 완료 (Enter)", style="Success.TButton", command=lambda: mark("ok")).pack(side="left")
        ttk.Button(buttons, text="다시 확인 (R)", command=lambda: mark("recheck")).pack(side="left", padx=6)
        ttk.Button(buttons, text="검수 취소", command=lambda: mark(None)).pack(side="left")
        ttk.Button(tools, text="인보이스 열기", style="Small.TButton",
                   command=lambda: current["docs"] and open_file(current["docs"][max(0, chooser.current())].path)).pack(side="left")
        ttk.Button(tools, text="명세서에서 보기", style="Small.TButton",
                   command=lambda: current["key"] and self.show_in_statement(self.transactions[current["key"]])).pack(side="left", padx=(6, 0))
        ttk.Label(extras, text="검수 메모 (내부용)", background="#fafbfc").pack(side="left", padx=(0, 4))
        note.pack(side="left", fill="x", expand=True)
        table.bind("<<TreeviewSelect>>", show)
        chooser.bind("<<ComboboxSelected>>", draw)
        order.trace_add("write", lambda *_: fill(current["key"]))
        dialog.bind("<Return>", lambda event: key_mark(event, "ok"))
        dialog.bind("<r>", lambda event: key_mark(event, "recheck"))
        dialog.bind("<R>", lambda event: key_mark(event, "recheck"))
        fill()
        table.focus_set()
        dialog.after(120, lambda: layout.winfo_width() > 400 and layout.sashpos(0, int(layout.winfo_width() * 0.42)))

    def monthly_review(self):
        rows = review_months(self.state, self.invoices)
        if not rows:
            self.notice("먼저 증빙이나 명세서를 불러오세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("월별 누락 점검표")
        dialog.geometry("1100x480")
        controls = ttk.Frame(dialog, padding=8)
        controls.pack(fill="x")
        summary = self.table(dialog, ("월", "증빙 없는 거래", "거래 없는 증빙", "번역 대기", "세무사 확인", "명세서 공백"),
                             (125, 175, 175, 150, 175, 175))
        detail = self.table(dialog, ("구분", "거래번호·파일", "확인할 내용"), (180, 270, 530))
        for month, items in sorted(rows.items(), reverse=True):
            counts = Counter(kind for kind, _, _ in items)
            summary.insert("", "end", iid=month, values=(month, *(counts[kind] for kind in REVIEW_KINDS)))

        def show(_event=None):
            detail.delete(*detail.get_children())
            picked = summary.selection()
            if picked:
                for issue in rows[picked[0]]:
                    detail.insert("", "end", values=issue)

        summary.bind("<<TreeviewSelect>>", show)
        summary.selection_set(summary.get_children()[0])
        self.buttons(controls, [("미연결 증빙 열기", self.show_unlinked_invoices),
                                ("명세서 기간 점검", self.show_statement_periods), ("증빙 후속 목록", self.followup_list)])
        show()

    def export_workspace_backup(self):
        target = filedialog.asksaveasfilename(parent=self.root, defaultextension=".zip",
            initialfile=f"InvoiceRenamer_Workspace_{date.today()}.zip", filetypes=[("전체 작업 백업", "*.zip")])
        if not target:
            return
        try:
            with tempfile.TemporaryDirectory(dir=Path(target).parent, prefix="invoice-backup-") as directory:
                staging = Path(directory) / "backup.zip"
                count, missing = create_backup(staging, self.state, self.folder, self.attachment_folder(), load_invoice_edits())
                move_without_overwrite(staging, Path(target))
        except (OSError, ValueError, RuntimeError, BadZipFile) as error:
            messagebox.showerror("전체 백업 실패", str(error), parent=self.root)
            return
        self.status.set(f"전체 백업 완료 · 파일 {count}개 · 빠진 항목 {len(missing)}개 · {target}")
        if missing:
            messagebox.showwarning("백업에서 빠진 항목", "\n".join(missing[:12]), parent=self.root)

    def restore_workspace_backup(self):
        if self.practice_folder:
            self.notice("연습 모드는 예시 자료만 사용해요.")
            return
        source = filedialog.askopenfilename(parent=self.root, filetypes=[("전체 작업 백업", "*.zip")])
        if not source:
            return
        parent = filedialog.askdirectory(parent=self.root, title="복원 폴더를 만들 위치")
        if not parent or not ask_yes_no("전체 작업 복원",
            "선택한 위치에 새 폴더를 만들고 작업 기록을 불러올까요? 현재 기록은 별도 백업으로 남겨요.", parent=self.root):
            return
        try:
            state, edits, destination, missing = restore_workspace(Path(source), Path(parent))
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            previous = load_invoice_edits()
            with tempfile.NamedTemporaryFile(prefix="before-workspace-restore-", suffix=".json", dir=self.state_path.parent,
                                             mode="w", encoding="utf-8", delete=False) as file:
                json.dump({"format": "InvoiceRenamer", "version": 1, "state": self.state, "edits": previous}, file, ensure_ascii=False, indent=2)
            write_state(invoice_edits_path(), {**previous, **edits})
            try:
                write_state(self.state_path, state)
            except OSError:
                write_state(invoice_edits_path(), previous)
                raise
        except (OSError, ValueError, KeyError, TypeError, BadZipFile, RuntimeError) as error:
            messagebox.showerror("전체 작업 복원 실패", str(error), parent=self.root)
            return
        self.state = state
        self.transactions.clear()
        self.accounts.clear()
        self.statement_sources.clear()
        self.transaction_sources.clear()
        self.session_specs.clear()
        self.errors.clear()
        self.last_moves.clear()
        self.load_pdfs(destination / "Belege")
        if state.get("session", {}).get("sources"):
            self.resume()
        self.status.set(f"전체 복원 완료 · {destination}")
        if missing:
            messagebox.showwarning("원래 백업에 없던 파일", "\n".join(missing[:12]), parent=self.root)

    def export_backup(self):
        target = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json",
                                            initialfile=f"InvoiceRenamer_Backup_{date.today()}.json",
                                            filetypes=[("작업 백업", "*.json")])
        if not target:
            return
        try:
            with Path(target).open("x", encoding="utf-8") as file:
                json.dump({"format": "InvoiceRenamer", "version": 1, "state": self.state}, file, ensure_ascii=False, indent=2)
        except OSError as error:
            messagebox.showerror("백업 저장 실패", str(error), parent=self.root)
            return
        self.status.set(f"작업 기록 백업 완료 · {target}")

    def restore_backup(self):
        if self.practice_folder:
            self.notice("연습 모드는 예시 자료만 사용해요.")
            return
        selected = filedialog.askopenfilename(parent=self.root, filetypes=[("작업 백업", "*.json")])
        if not selected:
            return
        try:
            state = read_backup(Path(selected))
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("복원할 수 없어요", str(error), parent=self.root)
            return
        count = len(state["records"])
        if not ask_yes_no("작업 기록 복원",
            f"백업에 있는 거래 기록 {count}건으로 현재 기록을 바꿀까요?\n현재 기록도 복원 전 백업으로 남겨요.", parent=self.root):
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="before-restore-", suffix=".json", dir=self.state_path.parent,
                                             mode="w", encoding="utf-8", delete=False) as file:
                json.dump({"format": "InvoiceRenamer", "version": 1, "state": self.state}, file, ensure_ascii=False, indent=2)
                backup = Path(file.name)
            write_state(self.state_path, state)
        except OSError as error:
            messagebox.showerror("작업 기록 복원 실패", str(error), parent=self.root)
            return
        self.state = state
        self.transactions.clear()
        self.accounts.clear()
        self.statement_sources.clear()
        self.transaction_sources.clear()
        self.session_specs.clear()
        self.errors.clear()
        self.last_moves.clear()
        self.refresh()
        self.status.set(f"복원 완료 · ‘이어서 작업’으로 열 수 있어요. · 복원 전 사본 {backup}")

    def manage_period(self):
        months = sorted({record.get("period", "") for record in self.state["records"].values()} - {""}, reverse=True)
        if not months:
            self.notice("먼저 명세서를 불러오세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("월 마감 확정·재열기")
        dialog.transient(self.root)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        month = ttk.Combobox(form, values=months, state="readonly", width=18)
        month.current(0)
        month.pack(anchor="w")
        status = tk.StringVar()
        ttk.Label(form, textvariable=status, padding=(0, 8)).pack(anchor="w")

        def show(_event=None):
            info = self.state.get("periods", {}).get(month.get(), {})
            status.set("마감 확정" if info.get("locked") else "작업 가능")

        def toggle(locked):
            chosen = month.get()
            current = self.state.get("periods", {}).get(chosen, {})
            if bool(current.get("locked")) == locked:
                return
            if locked:
                if not ask_yes_no("마감 확정", f"{chosen} 거래와 연결 증빙을 잠글까요?", parent=dialog):
                    return
                reason = ""
            else:
                reason = simpledialog.askstring("마감 재열기", "추가 증빙 등 다시 여는 사유를 적어 주세요.", parent=dialog)
                if not reason or not reason.strip():
                    return
                reason = reason.strip()
            state = deepcopy(self.state)
            state.setdefault("periods", {})[chosen] = {"locked": locked, "reason": reason}
            state.setdefault("audit", []).append({
                "at": datetime.now().astimezone().isoformat(timespec="seconds"), "user": getpass.getuser(),
                "number": chosen, "fields": "마감 확정" if locked else f"마감 재열기 · {reason}",
            })
            if self.save(state):
                show()

        buttons = ttk.Frame(form)
        buttons.pack(anchor="w")
        self.buttons(buttons, [("마감 확정", lambda: toggle(True)), ("사유를 적고 재열기", lambda: toggle(False))])
        month.bind("<<ComboboxSelected>>", show)
        show()

    def change_links(self, remove):
        cards = self.cards.selection()
        docs = [self.visible_pdfs[key] for key in self.pdfs.selection()]
        if not cards or (not remove and not docs):
            self.notice("거래와 연결할 PDF를 선택하세요. 후보가 없다면 ‘전체 증빙 표시’를 켜세요.")
            return
        if not remove and any(self.record(key).get("transfer") for key in cards):
            self.notice("증빙을 연결하려면 먼저 이체 연결을 해제하세요.")
            return
        keys = {doc.key for doc in docs}
        if remove:
            if any(self.record(key).get("transfer") or self.record(key).get("refund_group") or self.record(key).get("refund_of") for key in cards):
                prompt = "선택한 거래의 이체 또는 환불 연결을 해제할까요?"
            else:
                prompt = "선택한 거래에서 선택한 PDF 연결을 해제할까요?" if keys else "선택한 거래의 모든 PDF 연결을 해제할까요?"
            if not ask_yes_no("연결 해제", prompt, parent=self.root):
                return
        else:
            repeated = any(keys.intersection(record.get("links", []))
                           for key, record in self.state["records"].items() if key not in cards)
            if repeated or len(cards) > 1 or len(docs) > 1:
                prompt = f"거래 {len(cards)}건 각각에 PDF {len(keys)}개를 연결할까요?"
                if repeated:
                    prompt += "\n선택한 PDF 중 다른 거래에 연결된 파일이 있어요."
                if not ask_yes_no("여러 거래 또는 PDF 연결", prompt, parent=self.root):
                    return
        state = deepcopy(self.state)
        for key in cards:
            record = state["records"].setdefault(key, {})
            if remove:
                for related in record.pop("refund_group", []):
                    state["records"].get(related, {}).pop("refund_group", None)
                record.pop("refund_of", None)
                peer = record.pop("transfer", None)
                if peer:
                    state["records"].get(peer, {}).pop("transfer", None)
            previous = set(record.get("links", []))
            updated = previous - (keys or previous) if remove else previous | keys
            record["links"] = sorted(updated)
            if updated:
                record.pop("statement_evidence", None)
            if remove and "allocations" in record:
                record["allocations"] = {link: amount for link, amount in record["allocations"].items() if link in updated}
            if not remove and len(cards) == 1 and len(docs) == 1 and docs[0].seller:
                merchant = merchant_key(self.transactions[key].merchant)
                alias = merchant_key(docs[0].seller)
                aliases = state["aliases"].setdefault(merchant, [])
                if alias not in aliases:
                    aliases.append(alias)
        before = list(self.cards.get_children())
        if self.save(state) and not remove and len(cards) == 1:
            self.select_next_open(before, cards[0])

    def link(self):
        self.change_links(False)

    def select_next_open(self, before, current):
        """After linking one transaction, go to the next one in the list that still needs evidence."""
        rows = set(self.cards.get_children())
        start = before.index(current) + 1 if current in before else 0
        for key in before[start:] + before[:start]:
            if key in rows and key != current and key in self.transactions and not self.linked(key):
                self.cards.selection_set(key)
                self.cards.focus(key)
                self.cards.see(key)
                self.cards.focus_set()
                return

    def unlink(self):
        self.change_links(True)

    def single_card(self):
        selected = self.cards.selection()
        if len(selected) != 1:
            self.notice("카드 거래 한 건을 선택하세요.")
            return None
        return self.transactions[selected[0]]

    def note_preview(self, key):
        record = self.record(key)
        due = record.get("due", "")
        follow = record.get("follow_up", "")
        if follow == "요청 중" and due and due < date.today().isoformat():
            follow = "기한 지남"
        refunded = [other.get("number", "") for other in self.state["records"].values() if other.get("refund_of") == key]
        text = [follow, f"예정 {due}" if due else "", record.get("difference_reason", ""), record.get("statement_evidence", ""),
                f"환불됨: {', '.join(refunded)}" if refunded else "",
                f"환불 원거래: {self.state['records'].get(record['refund_of'], {}).get('number', '')}" if record.get("refund_of") else ""]
        text.append(record.get("advisor_note") or record.get("note", ""))
        return " · ".join(filter(None, text))

    def edit_note(self):
        item = self.single_card()
        if item is None:
            return
        record = self.record(item.key)
        dialog = InlineDialog(self.root)
        dialog.title("메모·기록")
        dialog.center(900, 640)  # floats in the middle; drag the title bar to see the list behind it
        dialog.grab_set()
        ttk.Label(dialog, text=self.card_label(item), style="Summary.TLabel", font=FONT_BOLD).pack(fill="x", padx=14, pady=(12, 8))
        columns = ttk.Frame(dialog)
        columns.pack(fill="both", expand=True, padx=14)
        columns.columnconfigure(0, weight=1, uniform="note")
        columns.columnconfigure(1, weight=1, uniform="note")

        def card(column, row, title, hint, weight=0):
            frame = tk.Frame(columns, background=COLORS["panel"], highlightthickness=1,
                             highlightbackground=COLORS["line"], highlightcolor=COLORS["line"], padx=12, pady=10)
            frame.grid(row=row, column=column, sticky="nsew", padx=(0, 8) if column == 0 else (8, 0), pady=(0, 10))
            columns.rowconfigure(row, weight=weight)
            tk.Label(frame, text=title, font=FONT_BOLD, background=COLORS["panel"], foreground=COLORS["ink"],
                     anchor="w").pack(fill="x")
            if hint:
                tk.Label(frame, text=hint, font=FONT_SMALL, background=COLORS["panel"], foreground=COLORS["muted"],
                         anchor="w", justify="left", wraplength=380).pack(fill="x", pady=(0, 6))
            return frame

        def text_box(parent, value, height):
            box = tk.Text(parent, height=height, wrap="word", width=40)
            box.insert("1.0", value)
            box.pack(fill="both", expand=True)
            return box

        mine = card(0, 0, "내 작업 메모", "프로그램 안에서만 보여요. 세무사 ZIP에는 들어가지 않아요.", 1)
        internal = text_box(mine, record.get("note", ""), 4)

        log = card(0, 1, "작업 기록", "언제 무엇을 했는지 쌓아 두는 기록이에요.", 1)
        timeline = record.get("timeline", [])
        history = tk.Listbox(log, height=4, activestyle="none", background=COLORS["panel"],
                             foreground=COLORS["ink"], selectbackground=COLORS["blue_light"])
        for entry in timeline:
            history.insert("end", f"{entry['date']}  {entry['text']}")
        if not timeline:
            history.insert("end", "아직 기록이 없어요.")
            history.itemconfigure(0, foreground=COLORS["muted"])
        history.pack(fill="both", expand=True)
        add_row = tk.Frame(log, background=COLORS["panel"])
        add_row.pack(fill="x", pady=(8, 0))
        when = ttk.Entry(add_row, width=11)
        when.insert(0, date.today().isoformat())
        when.pack(side="left")
        action = ttk.Entry(add_row)
        action.pack(side="left", fill="x", expand=True, padx=(6, 0))
        tk.Label(log, text="새 기록 · 예) 인보이스 요청 메일 보냄 (저장할 때 추가돼요)", font=FONT_SMALL,
                 background=COLORS["panel"], foreground=COLORS["muted"], anchor="w").pack(fill="x", pady=(4, 0))

        follow = card(1, 0, "후속 처리", "상태가 ‘세무사 확인 필요’나 ‘증빙 확보 불가’면 주황색으로 표시돼요.")
        follow_row = tk.Frame(follow, background=COLORS["panel"])
        follow_row.pack(fill="x")
        tk.Label(follow_row, text="상태", background=COLORS["panel"]).grid(row=0, column=0, sticky="w")
        follow_up = ttk.Combobox(follow_row, values=list(FOLLOW_UP), state="readonly", width=16)
        follow_up.set(record.get("follow_up", ""))
        follow_up.grid(row=1, column=0, sticky="w", padx=(0, 12))
        tk.Label(follow_row, text="예정일 (YYYY-MM-DD, 선택)", background=COLORS["panel"]).grid(row=0, column=1, sticky="w")
        due = ttk.Entry(follow_row, width=14)
        due.insert(0, record.get("due", ""))
        due.grid(row=1, column=1, sticky="w")

        advisor_card = card(1, 1, "세무사 전달 메모 · 독일어", "세무사용 ZIP에 들어가요. 아래 문구를 누르면 추가돼요.", 1)
        templates = tk.Frame(advisor_card, background=COLORS["panel"])
        templates.pack(fill="x", pady=(0, 6))
        external = None

        def build_chips():
            for child in templates.winfo_children():
                child.destroy()
            row = None
            for index, (label, wording) in enumerate(self.advisor_phrases()):
                if index % 3 == 0:  # three phrase buttons per line so long lists stay visible
                    row = tk.Frame(templates, background=COLORS["panel"])
                    row.pack(fill="x", pady=(0, 3))
                ttk.Button(row, text="+ " + label, style="Chip.TButton", command=lambda value=wording: external.insert(
                    "end", ("\n" if external.get("1.0", "end-1c") else "") + value)).pack(side="left", padx=(0, 4))
            ttk.Button(templates, text="✎ 문구 관리", style="Chip.TButton",
                       command=lambda: self.manage_phrases(build_chips)).pack(anchor="w")

        build_chips()
        external = text_box(advisor_card, record.get("advisor_note", ""), 4)
        ttk.Button(advisor_card, text="상태·예정일로 독일어 초안 만들기", command=lambda: self.preview_draft(
            dialog, item, action.get().strip() or internal.get("1.0", "end-1c").strip(),
            due.get().strip(), follow_up.get(), external,
        )).pack(anchor="w", pady=(6, 0))

        def save():
            planned, entered = due.get().strip(), when.get().strip()
            event = action.get().strip()
            if (planned and normalized_date(planned) != planned) or (event and (not entered or normalized_date(entered) != entered)):
                messagebox.showerror("날짜 확인", "날짜를 YYYY-MM-DD 형식으로 입력하세요.", parent=dialog)
                return
            try:
                advisor = german_input(external.get("1.0", "end-1c").strip())
            except ValueError as error:
                messagebox.showerror("독일어 메모 확인", str(error), parent=dialog)
                return
            state = deepcopy(self.state)
            record = state["records"].setdefault(item.key, {})
            record.update(note=internal.get("1.0", "end-1c").strip(),
                          advisor_note=advisor, due=planned, follow_up=follow_up.get())
            if event:
                record.setdefault("timeline", []).append({"date": entered, "text": event})
            if self.save(state):
                dialog.destroy()
                self.status.set(f"메모 저장 · {self.card_label(item)}")

        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(footer, text="저장", style="Primary.TButton", command=save).pack(side="right")
        ttk.Button(footer, text="취소", command=dialog.destroy).pack(side="right", padx=(0, 8))
        dialog.bind("<Control-s>", lambda event: save())
        internal.focus_set()

    def preview_draft(self, parent, item, source, due, follow_up, destination):
        try:
            draft = german_draft(source, due, follow_up)
        except ValueError as error:
            messagebox.showerror("독일어 초안", str(error), parent=parent)
            return
        if not draft:
            messagebox.showinfo("독일어 초안", "요청·수신·검토 상태를 선택하거나 요청 내용을 적어 주세요.", parent=parent)
            return
        window = InlineDialog(parent)
        window.title("독일어 초안 검토")
        window.transient(parent)
        window.grab_set()
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.card_label(item)).pack(anchor="w")
        ttk.Label(body, text=f"한국어 원문 · {source or follow_up}", wraplength=520, padding=(0, 8)).pack(anchor="w")
        ttk.Label(body, text="정해진 증빙 문구로 만든 초안이에요. 보낼 내용을 확인하세요.").pack(anchor="w")
        text = tk.Text(body, width=62, height=5, wrap="word")
        text.insert("1.0", draft)
        text.pack(fill="both", expand=True)

        def use():
            try:
                approved = german_input(text.get("1.0", "end-1c").strip())
            except ValueError as error:
                messagebox.showerror("독일어 초안", str(error), parent=window)
                return
            destination.insert("end", ("\n" if destination.get("1.0", "end-1c") else "") + approved)
            window.destroy()
            parent.grab_set()

        def close():
            window.destroy()
            parent.grab_set()

        ttk.Button(body, text="확인한 문장 사용", command=use).pack(anchor="e", pady=8)
        window.protocol("WM_DELETE_WINDOW", close)

    def edit_document_note(self):
        selected = self.pdfs.selection()
        if len(selected) != 1:
            self.notice("세무사 메모를 남길 증빙 한 개를 선택하세요.")
            return
        doc = self.visible_pdfs[selected[0]]
        dialog = InlineDialog(self.root)
        dialog.title("증빙 전달 메모")
        dialog.transient(self.root)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text=doc.path.name, wraplength=540).pack(anchor="w")
        ttk.Label(form, text="이 증빙을 연결하면 세무사용 ZIP의 증빙 목록에 포함돼요.").pack(anchor="w", pady=(4, 8))
        box = tk.Text(form, width=66, height=8, wrap="word")
        box.insert("1.0", self.state.get("document_notes", {}).get(doc.key, ""))
        box.pack(fill="both", expand=True)

        def save():
            state = deepcopy(self.state)
            try:
                state.setdefault("document_notes", {})[doc.key] = german_input(box.get("1.0", "end-1c").strip())
            except ValueError as error:
                messagebox.showerror("독일어 메모 확인", str(error), parent=dialog)
                return
            if self.save(state):
                dialog.destroy()

        ttk.Button(form, text="저장", command=save).pack(anchor="e", pady=(8, 0))
        box.focus_set()

    def edit_fields(self, title, context, fields, commit):
        dialog = InlineDialog(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text=context, wraplength=580).grid(row=0, columnspan=2, sticky="w", pady=(0, 8))
        entries = []
        for row, (label, value, options) in enumerate(fields, 1):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            if options:
                entry = ttk.Combobox(form, values=options, state="readonly", width=46)
                entry.set(value)
            else:
                entry = ttk.Entry(form, width=49)
                entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew")
            entries.append(entry)

        def apply():
            try:
                state = deepcopy(self.state)
                commit(state, [entry.get().strip() for entry in entries])
            except ValueError as error:
                messagebox.showerror(title, str(error), parent=dialog)
                return
            if self.save(state):
                dialog.destroy()

        ttk.Button(form, text="저장", command=apply).grid(row=len(fields) + 1, column=1, sticky="e", pady=(8, 0))

    def edit_allocation(self):
        item = self.single_card()
        if item is None:
            return
        record = self.record(item.key)
        docs = [self.by_key[key] for key in self.linked(item.key) if key in self.by_key]
        selected = self.pdfs.selection()
        doc = self.visible_pdfs[selected[0]] if len(selected) == 1 else docs[0] if len(docs) == 1 else None
        if doc not in docs:
            doc = None
        allocations = record.get("allocations", {})
        fields = [("차액 사유", record.get("difference_reason", ""), list(DIFFERENCE_REASONS))]
        if doc:
            fields.append((f"선택 증빙 배분 ({item.currency})", allocations.get(doc.key, ""), None))
        elif len(docs) > 1:
            self.status.set("증빙별로 배분하려면 아래에서 연결된 증빙 한 장을 선택하세요.")

        def commit(state, values):
            updated = state["records"][item.key]
            updated["difference_reason"] = values[0]
            if doc:
                amounts = updated.setdefault("allocations", {})
                if not values[1]:
                    amounts.pop(doc.key, None)
                    return
                amount, currency = card_amount(values[1], item.currency)
                if not amount.is_finite() or currency != item.currency or (amount != 0 and (amount < 0) != (item.amount < 0)):
                    raise ValueError("거래와 같은 통화 및 부호로 배분 금액을 입력하세요.")
                total = sum(Decimal(value) for key, value in amounts.items() if key in self.linked(item.key) and key != doc.key)
                if abs(total + amount) > abs(item.amount):
                    raise ValueError("배분 합계가 거래 금액을 초과해요.")
                amounts[doc.key] = str(amount)

        context = self.card_label(item) + (f"\n{doc.path.name}" if doc else "")
        self.edit_fields("배분·차액", context, fields, commit)

    def edit_hospitality(self):
        selected = self.pdfs.selection()
        if len(selected) != 1:
            self.notice("접대비 정보를 입력할 증빙 한 장을 선택하세요.")
            return
        doc = self.visible_pdfs[selected[0]]
        saved = self.state.get("hospitality", {}).get(doc.key, {})
        labels = {
            "day": "접대 일자 (YYYY-MM-DD)", "place": "장소 (독일어 표기)", "participants": "참석자 (이름 원문)",
            "purpose": "업무 목적 (독일어)", "amount": "금액 (통화 포함)",
        }
        defaults = {"day": str(doc.day or ""), "amount": f"{doc.amount} {doc.currency}" if doc.amount is not None else ""}
        fields = [(label, saved.get(key, defaults.get(key, "")), None) for key, label in labels.items()]

        def commit(state, values):
            entry = dict(zip(labels, values))
            if entry["day"] and normalized_date(entry["day"]) != entry["day"]:
                raise ValueError("접대 일자는 YYYY-MM-DD 형식으로 적어 주세요.")
            german_input(entry["purpose"])
            german_input(entry["place"])
            if entry["amount"]:
                card_amount(entry["amount"], doc.currency or "EUR")
            state.setdefault("hospitality", {})[doc.key] = entry

        self.edit_fields("접대비 정보", doc.path.name, fields, commit)

    def followup_list(self):
        today = date.today().isoformat()
        dialog = InlineDialog(self.root)
        dialog.title("증빙 후속 목록")
        dialog.geometry("890x370")
        ttk.Label(dialog, text="미수신·기한 경과·세무 검토 또는 추가 전달이 필요한 거래", padding=8).pack(anchor="w")
        table = self.table(dialog, ("거래번호", "마감월", "상태", "예정일", "거래처"), (100, 115, 240, 110, 290))
        rows = []
        for key, record in self.state["records"].items():
            month = record.get("period", "")
            status = record.get("follow_up", "")
            due = record.get("due", "")
            previous = self.submitted_snapshot(month) or {}
            current = delivery_changes(self.state, [key])[key]
            changed = key in previous and current != {name: value for name, value in previous[key].items() if name != "available"}
            if record.get("statement_evidence") and not changed:
                continue
            if not status and not due and not changed:
                continue
            if changed:
                status = f"{status} · 추가 전달" if status else "추가 전달"
            elif due and due < today and status == "요청 중":
                status = "기한 지남"
            rows.append((due or "9999", key, (record.get("number", ""), month, status, due, record.get("merchant", ""))))
        for _, key, values in sorted(rows):
            table.insert("", "end", iid=key, values=values)

        def open_record(_event):
            if table.selection() and self.cards.exists(table.selection()[0]):
                self.cards.selection_set(table.selection()[0])
                self.edit_note()

        table.bind("<Double-1>", open_record)

    def export_history(self):
        dialog = InlineDialog(self.root)
        dialog.title("마감본 이력")
        dialog.geometry("940x460")
        panel = ttk.Frame(dialog, padding=8)
        panel.pack(fill="x")
        status = ttk.Combobox(panel, values=DELIVERY_STATES, state="readonly", width=14)
        status.current(0)
        status.pack(side="left", padx=5)
        table = self.table(dialog, ("마감월", "버전", "전달 상태", "상태 변경 시각", "파일"), (100, 70, 115, 190, 400))
        entries = {}

        def fill():
            table.delete(*table.get_children())
            entries.clear()
            for month, versions in sorted(self.state.get("exports", {}).items(), reverse=True):
                for index, entry in reversed(list(enumerate(versions))):
                    key = f"{month}:{index}"
                    entries[key] = entry
                    table.insert("", "end", iid=key, values=(month, entry["version"],
                                 entry.get("delivery_status", DELIVERY_STATES[0]), entry.get("status_at", entry["at"]), entry["path"]))

        def set_status():
            choice = table.selection()
            if len(choice) != 1:
                messagebox.showinfo("전달 상태", "마감본 한 개를 선택하세요.", parent=dialog)
                return
            selected = entries[choice[0]]
            state = deepcopy(self.state)
            for versions in state.get("exports", {}).values():
                for entry in versions:
                    if entry["path"] == selected["path"] and entry["at"] == selected["at"]:
                        entry["delivery_status"] = status.get()
                        entry["status_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            if self.save(state):
                fill()

        ttk.Button(panel, text="전달 상태 저장", command=set_status).pack(side="left")
        fill()
        history = self.table(dialog, ("변경 시각", "사용자", "거래번호", "변경 항목"), (200, 130, 120, 455))
        for change in reversed(self.state.get("audit", [])):
            history.insert("", "end", values=(change["at"], change.get("user", ""), change["number"], change["fields"]))

    def link_refunds(self):
        """Purchase and refund: two or more loaded transactions are tied together; one refund alone opens a search
        over every month (an earlier month may already be closed and sent, it is not changed)."""
        keys = list(self.cards.selection())
        if len(keys) == 1:
            self.find_refunded_purchase(keys[0])
            return
        if len(keys) < 2:
            self.notice("환불 거래 한 건을 고르면 원래 결제를 모든 달에서 찾아요.\n같은 화면의 결제와 환불은 Ctrl로 함께 고르세요.")
            return
        try:
            remaining = refund_balance([self.transactions[key] for key in keys])
            if any(self.record(key).get("transfer") or self.record(key).get("refund_group") or self.record(key).get("refund_of")
                   for key in keys):
                raise ValueError("기존 이체·환불 연결이 있는 거래는 먼저 연결을 해제하세요.")
        except ValueError as error:
            messagebox.showerror("결제·환불 연결", str(error), parent=self.root)
            return
        currency = self.transactions[keys[0]].currency
        prompt = f"선택한 거래 {len(keys)}건을 연결할까요?\n환불 후 잔액: {remaining:.2f} {currency}"
        if remaining:
            prompt += "\n\n일부 환불이에요. 남은 금액의 인보이스를 결제 거래에 연결해야 완료돼요."
        if not ask_yes_no("결제·환불 연결", prompt, parent=self.root):
            return
        state = deepcopy(self.state)
        for key in keys:
            state["records"].setdefault(key, {})["refund_group"] = keys
        self.save(state)

    def refund_candidates(self, key, query=""):
        """Earlier payments a refund may belong to, from every month: [(score, record key, record)], best first.
        Same payee and an amount at least as large rank first; query narrows by text."""
        item = self.transactions[key]
        payee = payee_key(item.merchant)
        merchant = merchant_key(item.merchant)
        tokens = query.casefold().replace(",", ".").split()
        taken = {record.get("refund_of") for other, record in self.state["records"].items() if other != key}
        rows = []
        for other, record in self.state["records"].items():
            if other == key or other in taken or record.get("refund_group") or record.get("currency") != item.currency:
                continue
            try:
                amount = Decimal(record.get("amount", ""))
                day = date.fromisoformat(record.get("day", ""))
            except (InvalidOperation, ValueError):
                continue
            if amount * item.amount >= 0 or day > item.day or (item.day - day).days > 400:
                continue
            text = f"{record.get('number', '')} {record.get('merchant', '')} {amount} {day}".casefold().replace(",", ".")
            if tokens and not all(token in text for token in tokens):
                continue
            same_payee = payee_key(record.get("merchant", "")) == payee or merchant_key(record.get("merchant", "")) == merchant
            exact = abs(amount) == abs(item.amount)
            covers = abs(amount) >= abs(item.amount)
            if not tokens and not same_payee and not exact:
                continue
            rows.append(((not exact, not same_payee, not covers, (item.day - day).days), other, record))
        return sorted(rows, key=lambda row: row[0])

    def find_refunded_purchase(self, key):
        """Panel: pick the earlier payment a refund belongs to (any month)."""
        item = self.transactions[key]
        record = self.record(key)
        if self.linked(key) or record.get("transfer") or record.get("refund_group") or record.get("refund_of"):
            self.notice("이미 증빙·이체·환불 연결이 있는 거래예요. 먼저 연결을 해제하세요.")
            return
        dialog = InlineDialog(self.root)
        dialog.title("환불의 원래 결제 찾기")
        dialog.geometry("980x560")
        dialog.grab_set()
        ttk.Label(dialog, text=f"{record.get('number', '')} · {item.day:%d.%m.%Y} · {item.merchant[:60]} · {german_amount(item.amount)} {item.currency}\n"
                               "모든 달의 결제 중 같은 업체·같은 금액을 먼저 보여 줘요. 마감해서 이미 보낸 달은 바꾸지 않고, "
                               "이 환불 거래에 ‘몇 번 결제의 환불’로 기록해 이번 달 세무사 자료에 독일어로 적어요.",
                  wraplength=940, padding=(14, 10, 14, 6)).pack(anchor="w")
        bar = ttk.Frame(dialog, padding=(14, 0, 14, 6))
        bar.pack(fill="x")
        query = ttk.Entry(bar, width=40)
        query.pack(side="left")
        footer = ttk.Frame(dialog, padding=(14, 8))
        footer.pack(side="bottom", fill="x")
        table = self.table(dialog, ("마감월", "번호", "거래일", "거래처", "금액", "상태"), (90, 100, 100, 380, 110, 160))
        found = {}

        def fill(_event=None):
            table.delete(*table.get_children())
            found.clear()
            for index, (score, other, entry) in enumerate(self.refund_candidates(key, query.get())[:300]):
                found[str(index)] = other
                period = entry.get("period", "")
                sent = "이미 보냄" if self.state.get("exports", {}).get(period) else ""
                status = " · ".join(filter(None, ["금액 같음" if not score[0] else "", "같은 업체" if not score[1] else "", sent]))
                table.insert("", "end", iid=str(index), values=(period, entry.get("number", ""), entry.get("day", ""),
                                                                 entry.get("merchant", "")[:80], f"{entry.get('amount', '')} {entry.get('currency', '')}", status))

        def choose():
            selection = table.selection()
            if len(selection) != 1:
                show_notice(dialog, "원래 결제 한 건을 고르세요.")
                return
            other = found[selection[0]]
            state = deepcopy(self.state)
            same_month = state["records"][other].get("period") == record.get("period")
            if other in self.transactions and same_month:  # an earlier month keeps what was sent; only the refund gets the note
                try:
                    refund_balance([self.transactions[other], item])
                except ValueError as error:
                    show_notice(dialog, str(error))
                    return
                for member in (other, key):
                    state["records"][member]["refund_group"] = [other, key]
            else:
                state["records"].setdefault(key, {})["refund_of"] = other
            if self.save(state):
                dialog.destroy()

        ttk.Button(bar, text="찾기", command=fill).pack(side="left", padx=6)
        ttk.Label(bar, text="번호·업체·금액으로 찾을 수 있어요", foreground=COLORS["muted"]).pack(side="left")
        ttk.Button(footer, text="이 결제의 환불로 연결", style="Primary.TButton", command=choose).pack(side="right")
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right", padx=6)
        query.bind("<Return>", fill)
        table.bind("<Double-1>", lambda event: choose())
        fill()

    def compare_documents(self):
        item = self.single_card()
        if item is None:
            return
        entries = list(self.visible_pdfs.items())
        if not entries:
            self.notice("표시할 증빙이 없어요. ‘전체 증빙 표시’를 켜서 확인하세요.")
            return
        window = InlineDialog(self.root)
        window.title("거래와 증빙 나란히 보기")
        window.geometry("1110x720")
        window.transient(self.root)
        window.grab_set()
        layout = ttk.Panedwindow(window, orient="horizontal")
        layout.pack(fill="both", expand=True, padx=10, pady=10)
        left = ttk.Frame(layout, padding=8)
        right = ttk.Frame(layout, padding=8)
        layout.add(left, weight=1)
        layout.add(right, weight=2)
        ttk.Label(left, text="선택한 거래").pack(anchor="w")
        detail = tk.Text(left, height=12, width=31, wrap="word")
        detail.insert("1.0", f"거래번호: {self.record(item.key).get('number', '')}\n거래일: {item.day}\n금액: {item.amount} {item.currency}\n\n{item.merchant}")
        detail.configure(state="disabled")
        detail.pack(fill="both", expand=True, pady=8)
        descriptions = [f"{index + 1}. {doc.number or '번호 없음'} · {doc.path.name}" for index, (_, doc) in enumerate(entries)]
        selector = ttk.Combobox(right, values=descriptions, state="readonly", width=75)
        selector.pack(fill="x")
        selection = self.pdfs.selection()
        selector.current(next((i for i, (key, _) in enumerate(entries) if key in selection), 0))
        caption = ttk.Label(right, wraplength=650)
        caption.pack(anchor="w", pady=6)
        from pdf_view import PdfView
        viewer = PdfView(right)
        viewer.pack(fill="both", expand=True)

        def draw(_event=None):
            _, doc = entries[selector.current()]
            reference = matching_reference(item.merchant, doc)
            info = f"{doc.day or ''} · {doc.seller} · {doc.amount if doc.amount is not None else ''} {doc.currency}"
            if reference:
                info += f" · {reference} 일치"
            caption.configure(text=info)
            if doc.path.suffix.lower() == ".xml":
                meta = doc.tax_fields or {}
                values = [doc.path.name, f"인보이스 번호: {doc.number}", f"주문번호: {meta.get('order', '')}",
                          f"원본 참조: {meta.get('reference', '')}", f"공급가액: {meta.get('net', '')}", f"부가세: {meta.get('tax', '')}"]
                viewer.message("\n\n".join(values))
            else:
                viewer.load(doc.path, 0)

        def select_on_main():
            key, _ = entries[selector.current()]
            if self.pdfs.exists(key):
                self.pdfs.selection_set(key)
                self.pdfs.see(key)
            window.destroy()

        def locate():
            doc = entries[selector.current()][1]
            if doc.path.suffix.lower() == ".xml":
                messagebox.showinfo("원본 위치", "XML은 미리보기의 구조화된 값을 확인하세요.", parent=window)
                return
            values = {"발행일": str(doc.day or ""), "총액": f"{doc.amount} {doc.currency}" if doc.amount is not None else "",
                      "인보이스 번호": doc.number}
            show_source_locations(window, doc.path, values)

        actions = ttk.Frame(right, padding=(0, 8, 0, 0))
        actions.pack(fill="x")
        self.buttons(actions, [("이 증빙 선택", select_on_main), ("원본 위치 확인", locate),
                               ("원본 열기", lambda: self.open_evidence_file(entries[selector.current()][1].path))])
        selector.bind("<<ComboboxSelected>>", draw)
        draw()

    def edit_correction(self):
        selection = self.pdfs.selection()
        if len(selection) != 1:
            self.notice("정정 인보이스 한 장을 선택하세요.")
            return
        correction = self.visible_pdfs[selection[0]]
        meta = correction.tax_fields or {}
        reference = meta.get("reference", "")
        allowed = {doc.key: doc for doc in self.invoices if doc.key != correction.key}
        options = sorted(allowed.values(), key=lambda doc: (
            not (doc.number and re.search(rf"(?<![^\W_]){re.escape(doc.number)}(?![^\W_])", reference, re.I)),
            merchant_key(doc.seller) != merchant_key(correction.seller), doc.path.name.casefold()
        ))
        labels = {f"{i + 1}. {doc.number or '번호 없음'} · {doc.seller} · {doc.path.name}": doc for i, doc in enumerate(options)}
        existing = self.state.get("corrections", {}).get(correction.key, {})
        initial = next((label for label, doc in labels.items() if doc.key == existing.get("original")), "")

        def commit(state, values):
            relations = state.setdefault("corrections", {})
            if not values[0]:
                relations.pop(correction.key, None)
                return
            original = labels[values[0]]
            relations[correction.key] = correction_relation(correction, original, relations)

        self.edit_fields("정정 인보이스의 원본 선택", f"{correction.path.name}\n문서의 원본 참조: {reference or '표시 없음'}\n빈 항목을 저장하면 기존 연결을 해제해요.",
                         [("원본 인보이스", initial, ["", *labels])], commit)

    def add_evidence_for(self):
        """Pick invoice files for the selected transaction: bring them into the work folder and link them at once."""
        item = self.single_card()
        if item is None:
            return
        if self.practice_folder or not self.folder:
            self.notice("작업 폴더가 없어 파일을 추가할 수 없어요. 1단계에서 작업 폴더를 확인하세요.")
            return
        names = filedialog.askopenfilenames(parent=self.root, title=f"{self.card_label(item)} · 증빙 파일 고르기",
                                            filetypes=[("인보이스 (PDF·사진·XML)", "*.pdf *.jpg *.jpeg *.png *.xml"), ("모든 파일", "*.*")])
        if not names:
            return
        from invoice_renamer import import_evidence
        copy = import_mode() == "copy"
        with Busy(self.root, "증빙을 작업 폴더에 넣고 있어요") as busy:
            history, _, errors = import_evidence([Path(name) for name in names], self.folder, copy=copy, progress=busy.step)
            inside = [Path(name) for name in names if Path(name).resolve().is_relative_to(self.folder.resolve())]
            keys = []
            for path in [target for _, target, _ in history] + inside:
                try:
                    keys.append(digest(path))
                except OSError as error:
                    errors.append(f"{path.name}: {error}")
            self.load_pdfs(self.folder)
        keys = [key for key in keys if key in self.by_key]
        if not keys:
            self.notice("연결할 파일을 넣지 못했어요.\n" + "\n".join(errors[:5]), kind="warn")
            return
        state = deepcopy(self.state)
        record = state["records"].setdefault(item.key, {})
        record["links"] = sorted(set(record.get("links", [])) | set(keys))
        record.pop("statement_evidence", None)
        if self.save(state):
            failed = f"\n넣지 못한 파일 {len(errors)}개: " + " / ".join(errors[:3]) if errors else ""
            self.notice(f"{self.card_label(item)}에 증빙 {len(keys)}개를 연결했어요."
                        f"\n파일은 작업 폴더에 {'복사' if copy else '이동'}했어요.{failed}", title="증빙 추가 완료")

    def unmatched_documents(self, near_only=False):
        """Evidence no transaction uses. near_only: only dated within CANDIDATE_DAYS of the loaded statements (or undated)."""
        linked = {key for record in self.state["records"].values() for key in record.get("links", [])}
        documents = [doc for doc in self.invoices if doc.key not in linked]
        if near_only and self.transactions:
            first = min(item.day for item in self.transactions.values()) - timedelta(days=CANDIDATE_DAYS)
            last = max(item.day for item in self.transactions.values()) + timedelta(days=CANDIDATE_DAYS)
            documents = [doc for doc in documents if not doc.day or first <= doc.day <= last]
        return documents

    def chosen_document(self):
        selection = self.pdfs.selection()
        if len(selection) != 1 or selection[0] not in self.visible_pdfs:
            self.notice("오른쪽 증빙 목록에서 증빙 한 개를 고르세요.")
            return None
        return self.visible_pdfs[selection[0]]

    def edit_document_values(self, doc=None, after=None):
        """Fix date, seller, total and number of one evidence file (kept in invoice-edits.json, the file is not changed)."""
        from invoice_renamer import ValueForm, save_invoice_edit
        doc = doc or self.chosen_document()
        if doc is None:
            return
        form = InlineDialog(self.root)
        form.title("증빙 값 고치기")
        form.grab_set()
        body = ttk.Frame(form, padding=16)
        body.pack(fill="x")
        ttk.Label(body, text=doc.path.name, wraplength=520).grid(row=0, columnspan=4, sticky="w", pady=(0, 8))
        values = (doc.day.isoformat() if doc.day else "", doc.seller,
                  f"{doc.amount} {doc.currency}" if doc.amount is not None else "", doc.number)

        def save():
            day, seller, total, number = fields.raw()
            day = normalized_date(day) if re.fullmatch(DATE, day) else pick_value("발행일", day)
            if not day or not seller or not total:
                show_notice(form, "발행일(2026-07-02), 업체명, 총액(숫자, 예: 7,87)을 적어 주세요. 통화는 옆 칸에서 골라요.\n"
                                  "빨간 ‘누락’ 칸은 ‘고르기’로 원본에서 고를 수 있어요.")
                return
            try:
                save_invoice_edit(doc.path, (day, seller, total, number))
            except (OSError, ValueError) as error:
                show_notice(form, f"저장하지 못했어요: {error}", kind="error")
                return
            form.destroy()
            self.load_pdfs(self.folder)
            if after:
                after()

        fields = ValueForm(body, doc.path, values, on_save=save, number_label="인보이스 번호")
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        if doc.path.suffix.lower() != ".xml":
            ttk.Button(buttons, text="원본에서 보기·값 고르기", command=lambda: fields.pick(fields.first_missing())).pack(side="left")
        ttk.Button(buttons, text="저장", style="Primary.TButton", command=save).pack(side="right")

    def exclude_document(self, doc=None, parent=None):
        """Move an evidence file that is not an invoice (or not for this close) to the ‘제외’ folder. Nothing is deleted."""
        from invoice_renamer import EXCLUDED_FOLDER, free_name
        doc = doc or self.chosen_document()
        if doc is None:
            return False
        parent = parent or self.root
        used = [self.record(key).get("number", "") or key[:8] for key, record in self.state["records"].items()
                if doc.key in record.get("links", [])]
        if used:
            show_notice(parent, f"{doc.path.name}\n거래 {', '.join(used)}에 연결된 증빙이에요. 먼저 ‘해제’한 뒤 제외하세요.")
            return False
        if not ask_yes_no("제외 폴더로", f"{doc.path.name}\n인보이스가 아니거나 이번 마감과 상관없는 파일이면 ‘제외’ 폴더로 옮길까요?\n"
                                                   "지우지 않아요. 1단계 ‘정리 되돌리기’나 탐색기에서 다시 꺼낼 수 있어요.", parent=parent):
            return False
        try:
            target_folder = doc.path.parent / EXCLUDED_FOLDER
            target_folder.mkdir(exist_ok=True)
            move_evidence(doc.path, free_name(target_folder, doc.path.name))
        except OSError as error:
            show_notice(parent, f"옮기지 못했어요: {error}", kind="error")
            return False
        self.load_pdfs(self.folder)
        return True

    def evidence_menu(self, event):
        row = self.pdfs.identify_row(event.y)
        if not row or self.pdfs.identify_region(event.x, event.y) == "heading":
            return None
        self.pdfs.selection_set(row)
        menu = tk.Menu(self.pdfs, tearoff=False)
        for label, command in (("열기", self.open_pdf), ("선택 연결", self.link), ("해제", self.unlink), (None, None),
                               ("값 고치기 (발행일·업체·총액·번호)", self.edit_document_values),
                               ("제외 폴더로 (인보이스 아님·이번 달 아님)", self.exclude_document)):
            if label is None:
                menu.add_separator()
            else:
                menu.add_command(label=label, command=lambda c=command: c())
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def manage_unmatched(self):
        """Evidence that no transaction uses yet: look, fix the values, set aside, or link to a transaction."""
        dialog = InlineDialog(self.root)
        dialog.title("연결 안 된 증빙")
        dialog.geometry("980x600")
        dialog.grab_set()
        caption = tk.StringVar()
        ttk.Label(dialog, textvariable=caption, padding=(14, 10, 14, 4), wraplength=940).pack(anchor="w")
        footer = ttk.Frame(dialog, padding=(14, 8))
        footer.pack(side="bottom", fill="x")
        near_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(dialog, text=f"불러온 명세서 기간 앞뒤 {CANDIDATE_DAYS}일 안의 증빙만 (날짜 없는 증빙 포함)", variable=near_only,
                        command=lambda: fill()).pack(anchor="w", padx=14)
        table = self.table(dialog, ("파일", "발행일", "업체명", "총액", "번호", "금액 맞는 거래"), (300, 90, 180, 100, 120, 150))
        shown = {}

        def fill():
            table.delete(*table.get_children())
            shown.clear()
            open_cards = [item for item in self.unlinked_transactions()]
            documents = self.unmatched_documents(near_only=near_only.get())
            for index, doc in enumerate(sorted(documents, key=lambda doc: (doc.day or date.min, doc.path.name))):
                fits = [item for item in open_cards if doc.amount is not None and abs(item.amount) == abs(doc.amount)]
                shown[str(index)] = (doc, fits)
                table.insert("", "end", iid=str(index), values=(
                    doc.path.name, doc.day or "", doc.seller, f"{doc.amount} {doc.currency}" if doc.amount is not None else "",
                    doc.number, f"{len(fits)}건" if fits else ""))
            caption.set(f"아직 어떤 거래에도 연결되지 않은 증빙 {len(shown)}개예요. 다른 달 거래의 증빙이거나, 개인 지출·중복·인보이스가 아닌 "
                        "파일일 수 있어요. 골라서 열어 보고, 값을 고치거나, 거래에 연결하거나, ‘제외’ 폴더로 보내세요.")

        def chosen():
            selection = table.selection()
            if len(selection) != 1:
                show_notice(dialog, "증빙 한 개를 고르세요.")
                return None
            return shown[selection[0]]

        def open_selected():
            picked = chosen()
            if picked:
                self.open_evidence_file(picked[0].path)

        def edit_selected():
            picked = chosen()
            if picked:
                self.edit_document_values(picked[0], after=fill)

        def exclude_selected():
            picked = chosen()
            if picked and self.exclude_document(picked[0], dialog):
                fill()

        def link_selected():
            picked = chosen()
            if not picked:
                return
            doc, fits = picked
            cards = self.cards.selection()
            if len(cards) == 1 and cards[0] not in {item.key for item in fits}:
                fits = [self.transactions[cards[0]], *fits]
            if not fits:
                show_notice(dialog, "금액이 같은 미연결 거래가 없어요. 왼쪽 목록에서 거래를 하나 고른 뒤 다시 누르면 그 거래에 연결해요.")
                return
            labels = [f"{self.record(item.key).get('number', '')} · {item.day} · {item.amount} {item.currency} · {item.merchant[:40]}"
                      for item in fits]

            def commit(state, values):
                item = fits[labels.index(values[0])]
                record = state["records"].setdefault(item.key, {})
                record["links"] = sorted(set(record.get("links", [])) | {doc.key})
                record.pop("statement_evidence", None)
                dialog.after(50, after_link)

            def after_link():
                if dialog.winfo_exists():
                    fill()
                    dialog.grab_set()

            self.edit_fields("거래에 연결", f"{doc.path.name} · {doc.amount} {doc.currency}", [("연결할 거래", labels[0], labels)], commit)

        for label, command in (("열기", open_selected), ("값 고치기", edit_selected), ("거래에 연결", link_selected),
                               ("제외 폴더로", exclude_selected)):
            ttk.Button(footer, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right")
        table.bind("<Double-1>", lambda event: open_selected())
        dialog.bind("<Destroy>", lambda event: event.widget is dialog and self.update_unmatched_button())
        fill()

    def update_unmatched_button(self):
        button = getattr(self, "unmatched_button", None)
        if button is not None and button.winfo_exists():
            count = len(self.unmatched_documents(near_only=True)) if self.invoices else 0
            button.configure(text=f"연결 안 된 증빙 ({count})" if self.invoices else "연결 안 된 증빙")

    def open_pdf(self):
        selected = self.pdfs.selection()
        if len(selected) != 1:
            self.notice("열 PDF 한 개를 선택하세요.")
            return
        self.open_evidence_file(self.visible_pdfs[selected[0]].path)

    def open_numbered_statements(self):
        """Open copies of the imported PDF statements with the transaction numbers marked."""
        sources = [(key, path) for key, path in self.statement_sources.items() if path.suffix.lower() == ".pdf"]
        if not sources:
            self.notice("PDF로 불러온 명세서가 없어요. CSV 명세서는 거래번호가 대조표에 들어가요.")
            return
        folder = Path(tempfile.mkdtemp(prefix="invoice-numbered-"))
        summary = []
        busy = Busy(self.root, "명세서에 번호를 찍고 있어요")
        for position, (source_key, path) in enumerate(sources, 1):
            busy.step(position, len(sources), path.name)
            entries = [(self.record(key)["number"], item) for key, item in self.transactions.items()
                       if source_key in self.state.get("transaction_sources", {}).get(key, {})]
            try:
                data, marked = numbered_statement(path, entries)
                target = folder / f"{path.stem}_nummeriert.pdf"
                target.write_bytes(data)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("statement", error)
                messagebox.showerror("번호 표시 실패", f"{path.name}: {error}", parent=self.root)
                continue
            summary.append(f"{path.name}: {marked}/{len(entries)}건 표시")
            self.open_evidence_file(target)
        busy.close()
        if summary:
            self.status.set(" · ".join(summary) + " · 세무사 ZIP에도 같은 사본이 들어가요.")

    def number_order(self, keys):
        def rank(key):
            number = self.record(key).get("number", "")
            head, _, tail = number.partition("-")
            return (self.record(key).get("period", ""), head, int(tail) if tail.isdigit() else 0, number)
        return sorted(keys, key=rank)

    def print_bundle(self, keys=None, notes=True, progress=None):
        """One PDF for printing: each statement page followed by the evidence of the numbers marked on it.

        keys: transactions to include (default all). Statements follow the saved account order; evidence
        gets its number stamped at the bottom centre. Numbers without evidence get a short German note page
        when notes is True. Numbers the statement page could not be found for follow the statement's last page.
        Returns (pdf bytes, summary text)."""
        wanted = set(self.transactions if keys is None else keys)
        sources_of = self.state.get("transaction_sources", {})
        by_source = defaultdict(list)
        for key in wanted:
            source = next((checksum for checksum in sources_of.get(key, {}) if checksum in self.statement_sources), "")
            by_source[source].append(key)
        accounts = {checksum: next((self.accounts.get(key, "") for key in items), "") for checksum, items in by_source.items()}
        rank = {name: index for index, name in enumerate(ordered_accounts(accounts.values()))}
        first_number = {checksum: self.record(self.number_order(items)[0]).get("number", "") for checksum, items in by_source.items()}
        order = sorted(by_source, key=lambda checksum: (checksum == "", rank.get(accounts[checksum], len(rank)), first_number[checksum]))
        done_docs, counts = set(), Counter()
        reasons = reason_list()
        bundle = pymupdf.open()
        steps = sum(len(items) for items in by_source.values()) or 1
        position = 0

        def add_transaction(key):
            nonlocal position
            position += 1
            record = self.record(key)
            number = record.get("number", "")
            if progress:
                progress(position, steps, number)
            docs = [link for link in self.linked(key) if link in self.by_key]
            for link in docs:
                if link in done_docs:
                    continue
                done_docs.add(link)
                path = self.by_key[link].path
                numbers = [self.record(other).get("number", "") for other in self.number_order(
                    [other for other in self.transactions if link in self.linked(other)])]
                if path.suffix.lower() == ".xml":
                    info_page(bundle, [f"{' / '.join(numbers)} · {path.name}",
                                       "E-Rechnung (XML) ohne Bilddarstellung - Datei liegt im Ordner Belege."], "E-Rechnung")
                    counts["xml"] += 1
                    continue
                try:
                    with numbered_evidence(path, numbers) as copy:
                        bundle.insert_pdf(copy)
                    counts["evidence"] += 1
                except (OSError, RuntimeError, ValueError) as error:
                    record_error("print_bundle", error)
                    info_page(bundle, [f"{' / '.join(numbers)} · {path.name}", f"Datei konnte nicht gelesen werden: {error}"],
                              "Beleg nicht lesbar")
                    counts["failed"] += 1
            if not docs and notes:
                item = self.transactions[key]
                lines = [f"{number} · {item.day.strftime('%d.%m.%Y')} · {item.merchant[:70]}",
                         f"Betrag: {german_amount(item.amount)} {item.currency}",
                         f"Status: {self.german_status(key)}"]
                reason = reason_german(record.get("statement_evidence", ""), reasons)
                if reason:
                    lines.append(f"Begründung: {reason}")
                if record.get("refund_group"):
                    lines.append("Verrechnet mit: " + ", ".join(self.record(other).get("number", "")
                                                               for other in record["refund_group"] if other != key))
                if record.get("advisor_note"):
                    lines.append(f"Notiz: {delivery_text(record['advisor_note'])[:300]}")
                info_page(bundle, lines, f"{number} · ohne Beleg")
                counts["notes"] += 1

        for checksum in order:
            items = self.number_order(by_source[checksum])
            path = self.statement_sources.get(checksum)
            if path is not None and path.suffix.lower() == ".pdf":
                placed = {}
                entries = [(self.record(key)["number"], self.transactions[key]) for key in items]
                data, _ = numbered_statement(path, entries, placed)
                by_page = defaultdict(list)
                for key in items:
                    number = self.record(key)["number"]
                    if number in placed:
                        by_page[placed[number]].append(key)
                missing = [key for key in items if self.record(key)["number"] not in placed]
                with pymupdf.open("pdf", data) as statement:
                    for index in range(len(statement)):
                        bundle.insert_pdf(statement, from_page=index, to_page=index)
                        counts["statement_pages"] += 1
                        for key in by_page[index]:
                            add_transaction(key)
                if missing:
                    info_page(bundle, [path.name, "Diese Belegnummern wurden im Auszug nicht automatisch gefunden:",
                                       ", ".join(self.record(key)["number"] for key in missing)], "Ohne Position im Auszug")
                    for key in missing:
                        add_transaction(key)
                counts["unplaced"] += len(missing)
            else:
                if path is not None:
                    info_page(bundle, [path.name, "CSV-Auszug - Belege in Reihenfolge der Belegnummern."], "Kontoauszug (CSV)")
                for key in items:
                    add_transaction(key)
        if not len(bundle):
            bundle.close()
            raise ValueError("인쇄할 거래가 없어요.")
        data = bundle.tobytes(garbage=3, deflate=True)
        bundle.close()
        summary = (f"명세서 {counts['statement_pages']}쪽 · 증빙 {counts['evidence']}개 · 안내 {counts['notes']}쪽"
                   + (f" · 명세서에서 위치 못 찾음 {counts['unplaced']}건" if counts["unplaced"] else "")
                   + (f" · 읽기 실패 {counts['failed']}개" if counts["failed"] else ""))
        return data, summary

    def open_print_bundle(self):
        """Ask what to include, then open the print bundle in the default PDF program."""
        if not self.transactions:
            self.notice("먼저 명세서를 불러오세요.")
            return
        month = self.month_filter.get()
        keys = [key for key in self.transactions if month in ("전체", self.record(key).get("period"))]
        dialog = InlineDialog(self.root)
        dialog.title("인쇄 묶음")
        dialog.center(560, 300)
        dialog.grab_set()
        scope = f"{month} 거래 {len(keys)}건" if month != "전체" else f"모든 거래 {len(keys)}건"
        ttk.Label(dialog, text=f"{scope}을 한 PDF로 묶어요. 명세서 1쪽 → 그 쪽 번호의 인보이스들 → 명세서 2쪽 … 순서예요.\n"
                               "인보이스 아래 가운데에 거래번호가 찍혀요(원본 파일은 그대로).",
                  wraplength=520, padding=(14, 12, 14, 6)).pack(anchor="w")
        notes = tk.BooleanVar(value=True)
        ttk.Checkbutton(dialog, text="증빙 없는 번호에 안내 쪽 넣기 (사유 한 줄)", variable=notes).pack(anchor="w", padx=14, pady=4)

        def build():
            dialog.destroy()
            try:
                with Busy(self.root, "인쇄 묶음을 만들고 있어요") as busy:
                    data, summary = self.print_bundle(keys, notes.get(), busy.step)
                    folder = Path(tempfile.mkdtemp(prefix="invoice-print-"))
                    name = f"Druckmappe_{month if month != '전체' else 'alle'}.pdf"
                    target = folder / name
                    target.write_bytes(data)
            except (OSError, RuntimeError, ValueError) as error:
                record_error("print_bundle", error)
                messagebox.showerror("인쇄 묶음 실패", str(error), parent=self.root)
                return
            self.status.set(f"인쇄 묶음: {summary}")
            self.open_evidence_file(target)

        footer = ttk.Frame(dialog, padding=(14, 12))
        footer.pack(fill="x", side="bottom")
        ttk.Button(footer, text="만들어서 열기", style="Primary.TButton", command=build).pack(side="right")
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right", padx=6)

    def open_evidence_file(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            elif not path.exists() or not webbrowser.open(path.resolve().as_uri()):
                raise OSError("PDF를 열 수 없어요.")
        except OSError as error:
            messagebox.showerror("PDF 열기 실패", str(error), parent=self.root)

    def search_gmail(self):
        """Panel: search a registered mailbox (Gmail or 한메일) by amount and/or seller around the payment date."""
        item = self.single_card()
        if item is None:
            return
        from invoice_renamer import load_settings, save_settings
        settings = load_settings()
        boxes = mailboxes(settings)
        key = merchant_key(item.merchant)
        payee = split_merchant(item.merchant)[1].split("|")[0].strip()
        previous = self.state.get("senders", {}).get(key, payee)
        dialog = InlineDialog(self.root)
        dialog.title("메일 검색")
        dialog.center(600, 430)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=(16, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text=f"{self.record(item.key).get('number', '')} · {item.day:%d.%m.%Y} · {german_amount(item.amount)} {item.currency}",
                  font=FONT_BOLD).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        names = [f"{box.get('label') or MAILBOX_KINDS[box['kind']]}" for box in boxes]
        last = settings.get("mailbox_last", 0)
        ttk.Label(body, text="메일").grid(row=1, column=0, sticky="w")
        box_choice = ttk.Combobox(body, values=names, state="readonly", width=30)
        box_choice.current(last if isinstance(last, int) and 0 <= last < len(names) else 0)
        box_choice.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(body, text="찾을 것").grid(row=2, column=0, sticky="w")
        mode = tk.StringVar(value=settings.get("mail_mode", "amount"))
        modes = ttk.Frame(body)
        modes.grid(row=2, column=1, sticky="w", pady=3)
        for value, label in (("amount", f"금액 ({format(abs(item.amount), '.2f').replace('.', ',')})"), ("seller", "업체·발신자"), ("both", "둘 다")):
            ttk.Radiobutton(modes, text=label, value=value, variable=mode).pack(side="left", padx=(0, 10))
        ttk.Label(body, text="업체·발신자").grid(row=3, column=0, sticky="w")
        seller = ttk.Entry(body, width=40)
        seller.insert(0, previous)
        seller.grid(row=3, column=1, sticky="we", pady=3)
        ttk.Label(body, text="기간 (거래일 기준)").grid(row=4, column=0, sticky="w")
        days = ttk.Frame(body)
        days.grid(row=4, column=1, sticky="w", pady=3)
        before = tk.StringVar(value=str(settings.get("mail_days_before", 14)))
        after = tk.StringVar(value=str(settings.get("mail_days_after", 15)))
        ttk.Spinbox(days, from_=0, to=365, width=5, textvariable=before).pack(side="left")
        ttk.Label(days, text="일 전부터 ").pack(side="left")
        ttk.Spinbox(days, from_=0, to=365, width=5, textvariable=after).pack(side="left")
        ttk.Label(days, text="일 후까지").pack(side="left")
        attachment = tk.BooleanVar(value=bool(settings.get("mail_attachment", False)))
        ttk.Checkbutton(body, text="첨부 있는 메일만 (Gmail)", variable=attachment).grid(row=5, column=1, sticky="w", pady=3)
        ttk.Label(body, text="한메일은 한 단어만 찾아요. ‘둘 다’를 고르면 금액으로 찾아요.\n메일 목록은 ‘환경설정’의 ‘메일 등록’에서 바꿔요.",
                  foreground=COLORS["muted"]).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        def search():
            try:
                days_before, days_after = int(before.get()), int(after.get())
            except ValueError:
                show_notice(dialog, "기간은 숫자로 적으세요.")
                return
            box = boxes[box_choice.current()]
            chosen = mode.get()
            name = seller.get().strip()
            url = mail_search_url(box, item.amount if chosen in ("amount", "both") else None,
                                  name if chosen in ("seller", "both") else "", item.day, days_before, days_after, attachment.get())
            try:
                save_settings({"mailbox_last": box_choice.current(), "mail_mode": chosen, "mail_days_before": days_before,
                               "mail_days_after": days_after, "mail_attachment": attachment.get()})
            except OSError:
                pass
            if name and name != previous and re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", name):
                state = deepcopy(self.state)
                state.setdefault("senders", {})[key] = name
                self.save(state)
            dialog.destroy()
            webbrowser.open(url)

        footer = ttk.Frame(dialog, padding=(16, 10))
        footer.pack(fill="x", side="bottom")
        ttk.Button(footer, text="브라우저에서 찾기", style="Primary.TButton", command=search).pack(side="right")
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right", padx=6)
        ttk.Button(footer, text="메일 등록…", command=lambda: (dialog.destroy(), self.manage_mailboxes())).pack(side="left")
        dialog.bind("<Return>", lambda event: search())

    def manage_mailboxes(self):
        """Panel: the mailboxes the mail search can open (Gmail accounts by their number in the browser, 한메일)."""
        from invoice_renamer import load_settings, save_settings
        boxes = [dict(box) for box in mailboxes(load_settings())]
        dialog = InlineDialog(self.root)
        dialog.title("메일 등록")
        dialog.center(760, 440)
        dialog.grab_set()
        ttk.Label(dialog, text="메일 검색에서 고를 메일함이에요. Gmail 계정이 여러 개면 브라우저에 로그인한 순서 번호(첫 계정 0)를 적으세요.",
                  wraplength=580, padding=(14, 10, 14, 6)).pack(anchor="w")
        footer = ttk.Frame(dialog, padding=(14, 10))
        footer.pack(fill="x", side="bottom")  # footer and form first: the table takes only the space left over
        form = ttk.Frame(dialog, padding=(14, 6))
        form.pack(fill="x", side="bottom")
        table = self.table(dialog, ("이름", "종류", "Gmail 계정 번호"), (240, 120, 140))
        table.configure(height=6)

        def fill():
            table.delete(*table.get_children())
            for index, box in enumerate(boxes):
                table.insert("", "end", iid=str(index), values=(box.get("label", ""), MAILBOX_KINDS[box["kind"]],
                                                                box.get("index", 0) if box["kind"] == "gmail" else ""))

        ttk.Label(form, text="이름").pack(side="left")
        label = ttk.Entry(form, width=18)
        label.insert(0, "한메일")
        label.pack(side="left")
        kind = ttk.Combobox(form, values=list(MAILBOX_KINDS.values()), state="readonly", width=10)
        kind.current(1)
        kind.pack(side="left", padx=6)
        ttk.Label(form, text="Gmail 계정 번호").pack(side="left", padx=(6, 4))
        number = ttk.Spinbox(form, from_=0, to=9, width=4)
        number.set("0")
        number.pack(side="left")

        def entry_from_form():
            text = label.get().strip() or kind.get()
            chosen = next(code for code, name in MAILBOX_KINDS.items() if name == kind.get())
            entry = {"kind": chosen, "label": text}
            if chosen == "gmail":
                try:
                    entry["index"] = max(0, int(number.get()))
                except ValueError:
                    entry["index"] = 0
            return entry

        def add():
            boxes.append(entry_from_form())
            fill()

        def change():
            selection = table.selection()
            if len(selection) != 1:
                show_notice(dialog, "고칠 메일함을 표에서 하나 고르세요.")
                return
            boxes[int(selection[0])] = entry_from_form()
            fill()
            table.selection_set(selection[0])

        def picked(_event=None):
            selection = table.selection()
            if len(selection) != 1:
                return
            box = boxes[int(selection[0])]
            label.delete(0, "end")
            label.insert(0, box.get("label", ""))
            kind.set(MAILBOX_KINDS[box["kind"]])
            number.set(str(box.get("index", 0)))

        table.bind("<<TreeviewSelect>>", picked)

        def remove():
            for iid in sorted(table.selection(), key=int, reverse=True):
                del boxes[int(iid)]
            fill()

        def save():
            if not boxes:
                show_notice(dialog, "메일함을 한 개 이상 남기세요.")
                return
            save_settings({"mailboxes": boxes})
            dialog.destroy()

        ttk.Button(form, text="추가", command=add).pack(side="left", padx=6)
        ttk.Button(form, text="선택 고치기", command=change).pack(side="left")
        ttk.Button(form, text="선택 삭제", command=remove).pack(side="left", padx=6)
        ttk.Button(footer, text="저장", style="Primary.TButton", command=save).pack(side="right")
        ttk.Button(footer, text="닫기", command=dialog.destroy).pack(side="right", padx=6)
        fill()

    def export_csv(self):
        if not self.transactions and not self.errors:
            self.notice("먼저 은행·카드 명세서를 불러오세요.")
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv", initialfile="월마감_증빙.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["거래번호", "마감월", "거래일", "가맹점", "금액", "통화", "증빙 상태", "증빙 파일명", "세무사 전달 메모", "후속 예정일"])
                for key, item in self.transactions.items():
                    names = " | ".join(self.by_key[link].path.name if link in self.by_key else "(파일 위치 확인)"
                                       for link in self.linked(key))
                    record = self.record(key)
                    writer.writerow([record.get("number", ""), record.get("period", ""), item.day, export_value(item.merchant), item.amount, item.currency,
                                     self.status_for(key), export_value(names), export_value(delivery_text(record.get("advisor_note", ""))), record.get("due", "")])
                for line, error in self.errors:
                    writer.writerow(["", "", "", "", "", "", f"{line} 읽기 확인", "", export_value(error), ""])
        except OSError as error:
            messagebox.showerror("CSV 내보내기 실패", str(error), parent=self.root)
            return
        self.status.set(f"마감 결과 저장 완료 · {path}")

    def submitted_snapshot(self, month):
        snapshot = {}
        submitted = False
        for entry in sorted(self.state.get("exports", {}).get(month, []), key=lambda value: value["version"]):
            if entry.get("delivery_status", "") in DELIVERY_STATES[1:]:
                snapshot.update(entry.get("snapshot", {}))
                submitted = True
        return snapshot if submitted else None

    def export_zip(self, supplemental=False):
        if not self.statement_sources:
            self.notice("먼저 은행·카드 명세서를 불러오세요.")
            return
        current = delivery_changes(self.state, self.transactions)
        for key, snapshot in current.items():
            snapshot["available"] = [doc for doc in snapshot["links"] if doc in self.by_key]
        previous = {}
        keys = []
        for key, snapshot in current.items():
            month = self.record(key)["period"]
            if month not in previous:
                previous[month] = self.submitted_snapshot(month)
            baseline = previous[month]
            old = baseline.get(key, {}) if baseline is not None else {}
            # Snapshots created by earlier versions have no availability field.
            if old and "available" not in old:
                old = {**old, "available": old.get("links", [])}
                baseline[key] = old
            if not supplemental or (baseline is not None and old != snapshot):
                keys.append(key)
        if supplemental and not keys:
            self.notice("전달한 마감본을 이력에서 표시한 뒤, 새 증빙이나 변경된 거래가 있는지 확인하세요.")
            return
        self.choose_package(keys, lambda chosen: self.preview_package(chosen, supplemental, previous, current),
                            supplemental)

    def german_status(self, key):
        """German reconciliation text written to the advisor files for one transaction."""
        status, record = self.status_for(key), self.record(key)
        return {
            "금액 대조 완료": "Abgeglichen", "증빙 미연결": "Beleg fehlt",
            "명세서 근거 · 세무사 확인": "Auszug als Nachweis · steuerlich prüfen",
            CONTRACT_STATUS: "Dauerbeleg – Vertrag liegt vor",
            "연결 PDF 위치 확인": "Verknüpfte Datei fehlt",
            "연결됨 · PDF 총액 확인": "Rechnungsbetrag prüfen",
            "연결됨 · 통화 확인": "Währung prüfen",
            "내부이체 확인": f"Interner Transfer zu {self.record(record.get('transfer', '')).get('number', '')}",
            "이체 상대 명세서 필요": "Gegenkontoauszug fehlt",
            "환불 상대 명세서 필요": "Auszug zur Erstattung fehlt",
            "전액 환불 확인": "Vollständig erstattet · Belegprüfung offen",
            PARTIAL_REFUND_DONE: "Teilerstattung · Beleg zum Kauf liegt bei",
            EARLIER_REFUND: self.earlier_refund_german(key),
        }.get(status, status.replace("연결됨 · 차액", "Differenz").replace("일부 환불 · 잔액", "Teilerstattung · Restbetrag")
              .replace(" · 증빙 필요", " · Beleg fehlt"))

    def earlier_refund_german(self, key):
        """‘Erstattung zu 608-002 (Kauf 04.08.2026, 62,80 EUR) – Monat 08/2026 bereits übermittelt’."""
        original = self.state["records"].get(self.record(key).get("refund_of", ""), {})
        day = normalized_date(original.get("day", ""))
        when = f"{day[8:10]}.{day[5:7]}.{day[:4]}" if day else ""
        try:
            amount = german_amount(Decimal(original.get("amount", "")))
        except (InvalidOperation, ValueError):
            amount = original.get("amount", "")
        text = f"Erstattung zu {original.get('number', '')} (Kauf {when}, {amount} {original.get('currency', '')})"
        period = original.get("period", "")
        if self.state.get("exports", {}).get(period):
            text += f" – Monat {period[5:7]}/{period[:4]} bereits übermittelt"
        return text

    def package_sources(self, keys):
        """Statement files that hold at least one of the given transactions."""
        wanted = set(keys)
        links = self.state.get("transaction_sources", {})
        if any(not links.get(key) for key in wanted):
            return dict(self.statement_sources)  # older records without a source: keep every statement
        return {checksum: path for checksum, path in self.statement_sources.items()
                if any(checksum in links.get(key, {}) for key in wanted)}

    def choose_package(self, keys, proceed, supplemental=False):
        """Let the user pick months, statements and transactions before the ZIP is built."""
        dialog = InlineDialog(self.root)
        dialog.title("추가 제출 범위 선택 · Umfang der Nachreichung" if supplemental else "세무사 제출 범위 선택 · Umfang der Übermittlung")
        dialog.geometry("1000x640")
        dialog.grab_set()
        links = self.state.get("transaction_sources", {})
        months = sorted({self.record(key)["period"] for key in keys})
        names = {checksum: path.name for checksum, path in self.statement_sources.items()}
        chosen_month = self.month_filter.get()
        month_vars = {month: tk.BooleanVar(value=chosen_month in ("전체", month)) for month in months}
        source_vars = {checksum: tk.BooleanVar(value=True) for checksum in names}
        picked = {key: True for key in keys}
        ttk.Label(dialog, text="월과 명세서를 고른 뒤, 빼고 싶은 거래는 체크를 눌러 해제하세요. 선택한 거래에 연결된 증빙과 그 거래가 들어 있는 명세서만 ZIP에 들어가요.\n"
                       "Monate und Auszüge wählen, nicht gewünschte Buchungen abwählen. Nur zugehörige Belege und Auszüge kommen in die ZIP-Datei.",
                  wraplength=940, padding=(14, 10, 14, 4)).pack(anchor="w")
        filters = tk.Frame(dialog, background=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"],
                           highlightcolor=COLORS["line"], padx=12, pady=8)
        filters.pack(fill="x", padx=14, pady=(4, 8))
        tk.Label(filters, text="월 · Monat", font=FONT_BOLD, background=COLORS["panel"]).grid(row=0, column=0, sticky="nw", padx=(0, 10))
        month_row = tk.Frame(filters, background=COLORS["panel"])
        month_row.grid(row=0, column=1, sticky="w")
        tk.Label(filters, text="명세서 · Auszug", font=FONT_BOLD, background=COLORS["panel"]).grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=(6, 0))
        source_row = tk.Frame(filters, background=COLORS["panel"])
        source_row.grid(row=1, column=1, sticky="w", pady=(6, 0))
        table = self.table(dialog, ("선택", "번호 · Belegnr.", "월 · Monat", "거래일 · Datum", "가맹점 · Buchungstext", "금액 · Betrag", "상태 · Abgleich"),
                           (50, 125, 95, 120, 220, 110, 280))
        table.tag_configure("off", foreground=COLORS["muted"])
        summary = tk.StringVar()

        def visible(key):
            record = self.record(key)
            if not month_vars.get(record["period"], tk.BooleanVar(value=True)).get():
                return False
            sources = links.get(key, {})
            return not sources or any(source_vars[checksum].get() for checksum in sources if checksum in source_vars)

        def fill():
            table.delete(*table.get_children())
            ordered = sorted(keys, key=lambda key: (self.record(key)["period"], self.record(key).get("number", "")))
            for key in ordered:
                if not visible(key):
                    continue
                item, record = self.transactions[key], self.record(key)
                table.insert("", "end", iid=key, tags=() if picked[key] else ("off",), values=(
                    "☑" if picked[key] else "☐", record.get("number", ""), record["period"], item.day.isoformat(),
                    item.merchant, f"{item.amount:.2f} {item.currency}", f"{self.german_status(key)} ({self.status_for(key)})"))
            count = len(selected())
            statements = len(self.package_sources(selected()))
            summary.set(f"선택 {count}건 · 명세서 {statements}개   ({count} Buchungen · {statements} Auszüge)")

        def selected():
            return [key for key in keys if picked[key] and visible(key)]

        def toggle(event):
            key = table.identify_row(event.y)
            if key:
                picked[key] = not picked[key]
                fill()
                table.see(key)

        def set_all(value):
            for key in table.get_children():
                picked[key] = value
            fill()

        for month, var in month_vars.items():
            ttk.Checkbutton(month_row, text=month, variable=var, command=fill, style="Panel.TCheckbutton").pack(side="left", padx=(0, 10))
        for checksum, var in source_vars.items():
            ttk.Checkbutton(source_row, text=names[checksum], variable=var, command=fill, style="Panel.TCheckbutton").pack(side="left", padx=(0, 10))
        table.bind("<Button-1>", toggle)

        def go():
            chosen = selected()
            if not chosen:
                messagebox.showerror("제출 범위 · Umfang", "ZIP에 넣을 거래를 한 건 이상 선택하세요.\nBitte mindestens eine Buchung wählen.", parent=dialog)
                return
            dialog.destroy()
            proceed(chosen)

        footer = tk.Frame(dialog, background=COLORS["bg"])
        footer.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(footer, text="전체 선택 · Alle", command=lambda: set_all(True)).pack(side="left")
        ttk.Button(footer, text="전체 해제 · Keine", command=lambda: set_all(False)).pack(side="left", padx=(6, 0))
        tk.Label(footer, textvariable=summary, background=COLORS["bg"], foreground=COLORS["muted"]).pack(side="left", padx=12)
        ttk.Button(footer, text="미리보기 · Vorschau →", style="Primary.TButton", command=go).pack(side="right")
        fill()

    def preview_package(self, keys, supplemental, previous, current):
        months = sorted({self.record(key)["period"] for key in keys})
        versions = self.state.get("exports", {})
        revision = max((entry["version"] for month in months for entry in versions.get(month, [])), default=0) + 1
        span = "_bis_".join(dict.fromkeys([months[0], months[-1]])) if months else "ohne_Buchungen"
        prefix = "Nachreichung" if supplemental else "Monatsabschluss"
        filename = f"{prefix}_{span}_v{revision}.zip"
        with tempfile.TemporaryDirectory(prefix="invoice-preview-") as folder:
            preview = Path(folder) / filename
            try:
                with Busy(self.root, "세무사용 ZIP 미리보기를 만들고 있어요"):
                    self.write_package(preview, keys=keys, baselines=previous if supplemental else None)
                with ZipFile(preview) as archive:
                    def rows(name):
                        return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")), delimiter=";"))
                    summary, details, unresolved = (rows(name) for name in (
                        "01_Abgleich.csv", "02_Belegdetails.csv", "03_Offene_Posten.csv"
                    ))
                    notes = [(row["Belegnummer"], row["Notiz für Steuerberatung"]) for row in summary if row["Notiz für Steuerberatung"]]
                    notes.extend((row["Belegnummer"], row["Belegnotiz für Steuerberatung"]) for row in details if row["Belegnotiz für Steuerberatung"])
                    attachment_rows = rows("10_Anlagen.csv")
                    attachment_count = sum(name.startswith("Anlagen/") for name in archive.namelist())
                    members = archive.namelist()
                    csv_rows = {name: max(len(archive.read(name).decode("utf-8-sig").strip().splitlines()) - 1, 0)
                                for name in members if name.endswith(".csv")}
                    evidence_count = sum(name.startswith("Belege/") for name in archive.namelist())
            except (OSError, ValueError, RuntimeError) as error:
                record_error("zip", error)
                messagebox.showerror("제출 미리보기 실패", str(error), parent=self.root)
                return
            translation_count = sum(note == "Übersetzung der Notiz ausstehend." for _, note in notes)
            dialog = InlineDialog(self.root)
            dialog.title("추가 제출 미리보기 · Vorschau Nachreichung" if supplemental else "세무사용 ZIP 미리보기 · Vorschau für Steuerberatung")
            dialog.geometry("1000x600")
            dialog.transient(self.root)
            dialog.grab_set()
            ttk.Label(dialog, text=f"{filename}\n"
                      f"거래 {len(summary)}건 · 증빙 파일 {evidence_count}개 · 첨부 {attachment_count}개 · 미해결 {len(unresolved)}건 · 번역 대기 메모 {translation_count}개\n"
                      f"{len(summary)} Buchungen · {evidence_count} Belege · {attachment_count} Anlagen · {len(unresolved)} offene Posten · {translation_count} Notizen ohne Übersetzung",
                      padding=10).pack(anchor="w")
            footer = tk.Frame(dialog, background=COLORS["bg"])
            footer.pack(side="bottom", fill="x", padx=10, pady=8)
            tabs = ttk.Notebook(dialog)
            tabs.pack(fill="both", expand=True, padx=10)
            files_tab, content_tab = ttk.Frame(tabs), ttk.Frame(tabs)
            tabs.add(files_tab, text="  ZIP에 넣을 파일 · Dateien  ")
            tabs.add(content_tab, text="  내보낼 내용 · Inhalt  ")

            counts = Counter(package_part(name) for name in members if not name.endswith("/"))
            include = {}
            parts = []
            for part, label, meaning, required in PACKAGE_PARTS:
                if part.endswith(".csv"):
                    if part not in csv_rows:
                        continue
                    amount, empty = f"{csv_rows[part]}행", csv_rows[part] == 0
                else:
                    if not counts.get(part):
                        continue
                    amount, empty = f"{counts[part]}개", False
                include[part] = required or (not empty and part != "Druckmappe.pdf")  # print copy only when asked
                parts.append((part, label, meaning + (" (내용 없음)" if empty else ""), required, amount))
            files = self.table(files_tab, ("넣기", "파일·폴더", "한글 설명", "개수"), (60, 250, 560, 70))
            files.tag_configure("off", foreground=COLORS["muted"])
            chosen_text = tk.StringVar()

            def fill_files():
                files.delete(*files.get_children())
                for part, label, meaning, required, amount in parts:
                    mark = "필수" if required else "☑" if include[part] else "☐"
                    files.insert("", "end", iid=part, values=(mark, label, meaning, amount), tags=() if include[part] else ("off",))
                left_out = [label for part, label, *_ in parts if not include[part]]
                chosen_text.set(f"뺀 항목 {len(left_out)}개 · 내용 없는 표는 처음부터 빼 두었어요. 표 내용은 모두 Abgleich.xlsx 시트에도 들어 있어요.")

            def toggle_file(event):
                part = files.identify_row(event.y)
                if part and files.identify_column(event.x) == "#1":
                    if dict((p, r) for p, _, _, r, _ in parts)[part]:
                        show_notice(dialog, "필수 항목은 뺄 수 없어요. 엑셀 링크와 거래 대조에 필요해요.")
                    else:
                        include[part] = not include[part]
                        fill_files()
                    return "break"

            files.bind("<Button-1>", toggle_file)
            ttk.Label(files_tab, textvariable=chosen_text, style="Muted.TLabel", padding=(4, 6)).pack(anchor="w")
            fill_files()

            table = self.table(content_tab, ("거래번호 · Belegnr.", "구분 · Art", "내보낼 내용 · Inhalt"), (145, 230, 535))
            def save_zip():
                path = filedialog.asksaveasfilename(parent=dialog, defaultextension=".zip", initialfile=filename, filetypes=[("ZIP", "*.zip")])
                if not path:
                    return
                target = Path(path)
                omit = {part for part, value in include.items() if not value}
                created = False
                busy = Busy(self.root, "ZIP을 저장하고 있어요")
                try:
                    package = preview
                    if omit:
                        busy.step(text="고른 파일로 ZIP을 다시 만들고 있어요")
                        package = Path(folder) / ("selected_" + filename)
                        self.write_package(package, keys=keys, baselines=previous if supplemental else None, omit=omit)
                    busy.step(text=target.name)
                    with target.open("xb") as output:
                        created = True
                        with package.open("rb") as source:
                            shutil.copyfileobj(source, output)
                except (OSError, ValueError, RuntimeError) as error:
                    busy.close()
                    if created:
                        target.unlink(missing_ok=True)
                    record_error("zip", error)
                    messagebox.showerror("ZIP 저장 실패", str(error), parent=dialog)
                    return
                busy.close()
                state = deepcopy(self.state)
                for month in months:
                    state.setdefault("exports", {}).setdefault(month, []).append({
                        "version": revision, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "path": str(target), "delivery_status": DELIVERY_STATES[0], "kind": prefix,
                        "snapshot": {key: current[key] for key in keys if self.record(key)["period"] == month},
                        "omitted": sorted(omit),
                    })
                saved = self.save(state)
                dialog.destroy()
                if saved:
                    self.notice(f"세무사용 ZIP을 저장했어요.\n{path}\n다음: 세무사에게 보낸 뒤 ‘전달 이력’에서 ‘전달함’으로 표시하세요.",
                                title="ZIP 저장 완료")
                else:
                    self.status.set(f"ZIP 저장 완료 · 이력 저장 실패 · {path}")

            ttk.Button(footer, text="이 내용으로 ZIP 저장 · ZIP speichern", style="Primary.TButton", command=save_zip).pack(side="right")
            self.root.wait_window(dialog)

    def write_package(self, target, keys=None, baselines=None, omit=frozenset()):
        """omit: PACKAGE_PARTS ids left out of the ZIP (required parts are always written)."""
        omit = set(omit) - {part for part, *_rest, required in PACKAGE_PARTS if required}
        temporary = None
        reasons = reason_list()
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
            with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
                overview, details, unmatched = (io.StringIO(newline="") for _ in range(3))
                summary_writer, details_writer, unmatched_writer = (
                    csv.writer(stream, delimiter=";") for stream in (overview, details, unmatched)
                )
                columns = ["Belegnummer", "Abschlussmonat", "Buchungsdatum", "Konto/Karte", "Buchungstext",
                           "Betrag", "Währung", "Abgleich", "Belege im ZIP", "Notiz für Steuerberatung", "Frist zur Nachreichung", "Verknüpfte Erstattungen", "Nachreichungsstatus", "Begründung ohne Beleg"]
                summary_writer.writerow(columns)
                unmatched_writer.writerow(columns)
                details_writer.writerow(["Belegnummer", "Datei im ZIP", "Quelldatei", "SHA-256", "Rechnungsdatum",
                                         "Leistungsdatum", "Lieferant", "Empfänger", "Rechnungsnummer", "Netto",
                                         "USt", "Steuersätze (Netto / USt)", "Brutto", "Währung", "Steuernummer/USt-IdNr.",
                                         "Referenz", "Prüfhinweise", "Belegnotiz für Steuerberatung"])
                changes, hospitality, allocation = (io.StringIO(newline="") for _ in range(3))
                change_writer, hospitality_writer, allocation_writer = (
                    csv.writer(stream, delimiter=";") for stream in (changes, hospitality, allocation)
                )
                change_writer.writerow(["Belegnummer", "Abschlussmonat", "Änderung", "Belege im ZIP"])
                hospitality_writer.writerow(["Belegnummer", "Belegdatei", "Datum", "Ort", "Teilnehmer", "Geschäftlicher Anlass", "Betrag", "Prüfung"])
                allocation_writer.writerow(["Belegnummer", "Belegdatei", "Zugeordneter Betrag", "Währung der Buchung", "Differenzgrund", "Nicht zugeordneter Betrag"])
                attachment_list = io.StringIO(newline="")
                attachment_writer = csv.writer(attachment_list, delimiter=";")
                attachment_writer.writerow(["Belegnummer", "Quelldatei", "Datei im ZIP", "SHA-256", "Status"])
                corrections = io.StringIO(newline="")
                correction_writer = csv.writer(corrections, delimiter=";")
                correction_writer.writerow(["Belegnummer", "Korrekturbeleg", "Korrektur-Rechnungsnummer", "Originalbeleg", "Original-Rechnungsnummer"])
                source_list = io.StringIO(newline="")
                source_writer = csv.writer(source_list, delimiter=";")
                source_writer.writerow(["Datei im ZIP", "Quelldatei", "SHA-256"])
                sources = self.statement_sources if keys is None else self.package_sources(keys)
                for index, (checksum, source) in enumerate(sources.items() if baselines is None else [], 1):
                    arcname = f"Kontoauszuege/{index:02d}_{source.name}"
                    data = source.read_bytes()
                    if hashlib.sha256(data).hexdigest() != checksum:
                        raise ValueError(f"명세서가 변경됐어요. 다시 불러오세요. {source.name}")
                    if "Kontoauszuege" not in omit:
                        archive.writestr(arcname, data)
                        source_writer.writerow([arcname, export_value(source.name), checksum])
                    if source.suffix.lower() == ".pdf":
                        entries = [(self.record(key)["number"], item) for key, item in self.transactions.items()
                                   if checksum in self.state.get("transaction_sources", {}).get(key, {})]
                        if entries and "nummeriert" not in omit:
                            copy, marked = numbered_statement(source, entries)
                            numbered = f"Kontoauszuege/{index:02d}_{source.stem}_nummeriert.pdf"
                            archive.writestr(numbered, copy)
                            source_writer.writerow([numbered, f"Kopie mit Belegnummern ({marked}/{len(entries)})",
                                                    hashlib.sha256(copy).hexdigest()])
                for key in self.transactions if keys is None else keys:
                    item = self.transactions[key]
                    record = self.record(key)
                    transaction_id = record["number"]
                    paths = []
                    allocated = Decimal(0)
                    used_names = set()
                    old = (baselines.get(record["period"]) or {}).get(key, {}) if baselines is not None else {}
                    already_delivered = set(old.get("available", old.get("links", [])))
                    old_documents = old.get("documents", {})
                    attachments = record.get("attachments", [])
                    former = old.get("attachments", [])
                    for index, attachment in enumerate(attachments, 1):
                        path = attachment_path(self.attachment_folder(), attachment)
                        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", attachment["name"])
                        member = f"Anlagen/{record['period']}/[{transaction_id}]_{index:02d}_{name}"
                        if baselines is not None and attachment in former:
                            attachment_writer.writerow([transaction_id, export_value(attachment["name"]), "", attachment["sha256"], "Bereits übermittelt"])
                            continue
                        if not path.is_file():
                            raise ValueError(f"첨부 파일을 찾지 못했어요: {attachment['name']}")
                        content = path.read_bytes()
                        if hashlib.sha256(content).hexdigest() != attachment["sha256"]:
                            raise ValueError(f"첨부가 변경됐어요: {attachment['name']}")
                        if "Anlagen" not in omit:
                            archive.writestr(member, content)
                        attachment_writer.writerow([transaction_id, export_value(attachment["name"]), member, attachment["sha256"], "Enthalten"])
                    if baselines is not None:
                        for attachment in former:
                            if attachment not in attachments:
                                attachment_writer.writerow([transaction_id, export_value(attachment["name"]), "", attachment["sha256"], "Zuordnung aufgehoben"])
                    for link in self.linked(key):
                        doc = self.by_key.get(link)
                        if not doc:
                            continue
                        filename = package_filename(transaction_id, doc)
                        stem, suffix = Path(filename).stem, Path(filename).suffix
                        sequence = 2
                        while filename.casefold() in used_names:
                            filename = f"{stem}_{sequence}{suffix}"
                            sequence += 1
                        used_names.add(filename.casefold())
                        arcname = f"Belege/{record['period']}/{filename}"
                        data = doc.path.read_bytes()
                        if hashlib.sha256(data).hexdigest() != doc.key:
                            raise ValueError(f"증빙이 변경됐어요. 증빙 폴더를 다시 불러오세요. {doc.path.name}")
                        metadata = [self.state.get("document_notes", {}).get(link, ""), self.state.get("hospitality", {}).get(link, {})]
                        include_file = baselines is None or link not in already_delivered or metadata != old_documents.get(link)
                        if include_file:
                            archive.writestr(arcname, data)
                        else:
                            arcname = f"Bereits übermittelt: {doc.path.name}"
                        paths.append(arcname)
                        meta = doc.tax_fields or {}
                        fields = (
                            transaction_id, arcname, doc.path.name, doc.key, doc.day or "", meta.get("delivery", ""),
                            doc.seller, meta.get("buyer", ""), doc.number, meta.get("net", ""), meta.get("tax", ""),
                            meta.get("rates", ""), doc.amount if doc.amount is not None else "", doc.currency,
                            meta.get("tax_id", ""), meta.get("reference", ""), tax_review(doc),
                            delivery_text(self.state.get("document_notes", {}).get(doc.key, "")),
                        )
                        details_writer.writerow([export_value(field) if isinstance(field, str) else field for field in fields])
                        value = record.get("allocations", {}).get(link)
                        if value is not None:
                            allocated += Decimal(value)
                            allocation_writer.writerow([transaction_id, arcname, Decimal(value), item.currency,
                                                        DIFFERENCE_REASONS.get(record.get("difference_reason", ""), ""), ""])
                        info = self.state.get("hospitality", {}).get(link)
                        if info:
                            inputs = [info.get(field, "") for field in ("day", "place", "participants", "purpose", "amount")]
                            hospitality_writer.writerow([transaction_id, arcname, *map(export_value, inputs),
                                                         "" if all(inputs) else "Angaben ergänzen"])
                    status = self.status_for(key)
                    german_status = self.german_status(key)
                    row = [transaction_id, record["period"], item.day, export_value(self.accounts.get(key, "")),
                           export_value(item.merchant), item.amount, item.currency, german_status,
                           " | ".join(paths), export_value(delivery_text(record.get("advisor_note", ""))), record.get("due", ""),
                           " | ".join([self.record(other).get("number", "") for other in record.get("refund_group", []) if other != key]
                                      + ([self.state["records"].get(record["refund_of"], {}).get("number", "")] if record.get("refund_of") else [])),
                           FOLLOW_UP.get(record.get("follow_up", ""), ""),
                           reason_german(record.get("statement_evidence", ""), reasons)]
                    reason = DIFFERENCE_REASONS.get(record.get("difference_reason", ""), "")
                    if reason or record.get("allocations"):
                        allocation_writer.writerow([transaction_id, "", "", item.currency, reason,
                                                    item.amount - allocated if record.get("allocations") else ""])
                    versions = self.state.get("exports", {}).get(record["period"], [])
                    if baselines is not None:
                        change_writer.writerow([transaction_id, record["period"], "Geändert" if old else "Neu", " | ".join(paths)])
                    elif versions:
                        previous = versions[-1].get("snapshot", {})
                        before = previous.get(key, {})
                        current = delivery_changes(self.state, [key])[key]
                        if current != {field: value for field, value in before.items() if field != "available"}:
                            change_writer.writerow([transaction_id, record["period"], "Geändert" if key in previous else "Neu", " | ".join(paths)])
                    for corrected, relation in corrections_for(self.state, self.linked(key)).items():
                        correction = self.by_key.get(corrected)
                        original = self.by_key.get(relation["original"])
                        correction_writer.writerow([transaction_id,
                            export_value(correction.path.name if correction else relation["correction_name"]),
                            export_value(correction.number if correction else relation["correction_number"]),
                            export_value(original.path.name if original else relation["original_name"]),
                            export_value(original.number if original else relation["original_number"])])
                    summary_writer.writerow(row)
                    if record.get("follow_up") in ("요청 중", "세무사 확인 필요", "증빙 확보 불가") or status not in DONE_STATES:
                        unmatched_writer.writerow(row)
                errors = io.StringIO(newline="")
                error_writer = csv.writer(errors, delimiter=";")
                error_writer.writerow(["Quelle/Zeile", "Importhinweis"])
                for location, error in self.errors if baselines is None else []:
                    error_writer.writerow([export_value(location), german_import_error(error)])
                for name in self.read_issues if baselines is None else []:
                    error_writer.writerow([export_value(name), "Beleg konnte nicht vollständig gelesen werden"])
                review_log = io.StringIO(newline="")
                review_writer = csv.writer(review_log, delimiter=";")
                review_writer.writerow(["Belegnummer", "Buchungsdatum", "Betrag", "Währung", "Endkontrolle", "Geprüft am", "Geprüft von"])
                for key in (self.transactions if keys is None else keys):
                    checked = review_state(self.state, key)
                    if key not in self.transactions or not checked:
                        continue
                    item, entry = self.transactions[key], self.state["final_review"][key]
                    review_writer.writerow([self.record(key).get("number", ""), item.day.isoformat(), str(item.amount), item.currency,
                                            REVIEW_GERMAN[checked], entry.get("at", "")[:10], export_value(entry.get("user", ""))])
                tables = {name: localized_csv(stream.getvalue()) for name, stream in (
                    ("01_Abgleich.csv", overview), ("02_Belegdetails.csv", details),
                    ("03_Offene_Posten.csv", unmatched), ("04_Importfehler.csv", errors),
                    ("05_Kontoauszuege.csv", source_list), ("06_Nachreichungen.csv", changes),
                    ("07_Bewirtung.csv", hospitality), ("08_Betragszuordnung.csv", allocation),
                    ("09_Rechnungskorrekturen.csv", corrections), ("10_Anlagen.csv", attachment_list),
                    ("11_Endkontrolle.csv", review_log),
                )}
                for name, contents in tables.items():
                    if name not in omit:
                        archive.writestr(name, contents.encode("utf-8-sig"))
                if "Druckmappe.pdf" not in omit and baselines is None:
                    try:
                        bundle, _ = self.print_bundle(keys)
                        archive.writestr("Druckmappe.pdf", bundle)
                    except (OSError, RuntimeError, ValueError) as error:
                        record_error("print_bundle", error)
                archive.writestr("Abgleich.xlsx", advisor_workbook(tables, set(archive.namelist())))
                if "Hinweise.txt" not in omit:
                    archive.writestr("Hinweise.txt", (
                    ("Nachreichung: Enthält ausgewählte Änderungen und gegebenenfalls neue Belege. Frühere Auszüge gelten weiter.\n" if baselines is not None else "") +
                    "Die Ordner enthalten Kopien der importierten Auszüge und der zugeordneten Belege.\n"
                    "Die Belegnummer verweist auf die zugehörige Buchung. Derselbe Beleg kann mehreren Buchungen zugeordnet sein.\n"
                    "Ein Abgleich bestätigt nur die Zuordnung und den Betragsvergleich. Steuerhinweise und PDF-Extrakte bitte prüfen.\n"
                    "Bei PDF-Auszügen Anzahl, Vorzeichen und Vollständigkeit anhand der Originalauszüge prüfen.\n"
                    "Alle Tabellen sind zusätzlich als Blätter in Abgleich.xlsx enthalten.\n"
                    ))
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def package_filename(transaction_id, doc):
    original = original_stem(doc.path)
    if doc.amount is None or not doc.day or not doc.seller:
        reference = number_label(doc.number, original).replace("__번호_", "_Nr_")
        label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", original + reference).strip(" .")
        return f"[{transaction_id}]_{label[:170]}{doc.path.suffix.lower()}"
    amount = f"{doc.amount:.0f}" if doc.currency == "KRW" and doc.amount == doc.amount.to_integral_value() else f"{doc.amount:.2f}"
    label = evidence_stem(doc.day.isoformat(), doc.seller, f"{amount} {doc.currency or 'EUR'}", doc.number, original)
    return f"[{transaction_id}]_{label[:170]}{doc.path.suffix.lower()}"


def tax_review(doc):
    meta = doc.tax_fields or {}
    notes = [meta["special"]] if meta.get("special") else []
    if doc.credit:
        notes.append("Korrektur/Referenz prüfen")
    small_candidate = doc.currency == "EUR" and doc.amount is not None and 0 < doc.amount <= 250 and not notes
    if small_candidate:
        notes.append("Kleinbetrag: Anwendbarkeit von § 33 UStDV prüfen")
    for label, value in (
        ("Rechnungsdatum", doc.day), ("Lieferant", doc.seller),
        ("Netto", meta.get("net")), ("Umsatzsteuer/Steuerhinweis", meta.get("tax")),
        ("Rechnungsnummer", doc.number), ("Leistungsdatum", meta.get("delivery")),
        ("Empfänger", meta.get("buyer")), ("Steuernummer/USt-IdNr.", meta.get("tax_id")),
    ):
        if not value and not (small_candidate and label in ("Rechnungsnummer", "Leistungsdatum", "Empfänger", "Steuernummer/USt-IdNr.")):
            notes.append(f"{label} prüfen")
    return " | ".join(notes)
