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
from decimal import Decimal
from pathlib import Path
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
from xml.etree.ElementTree import ParseError

import pymupdf
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.parse import quote

from invoice_renamer import DATE, EVIDENCE_EXTENSIONS, MONEY, MONTH_FOLDER, digest, filename_fields, invoice_edits_path, load_invoice_edits, move_without_overwrite, open_evidence, number_label, normalized_date, normalized_money, original_stem, read_invoice_details, read_pdf_pages, show_source_locations, undo_moves
from tax_documents import pdf_tax_fields, read_structured
from advisor_workbook import advisor_workbook, localized_csv
from release_info import VERSION, record_error
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


def index_pdfs(folder):
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
    for path in sorted(paths):
        if not path.is_file() or path.suffix.lower() not in (EVIDENCE_EXTENSIONS | {".xml"}):
            continue
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


def signed_total(invoice):
    return -abs(invoice.amount) if invoice.credit and invoice.amount is not None else invoice.amount


def matching_reference(description, invoice):
    for label, value in (("인보이스 번호", invoice.number), ("주문번호", (invoice.tax_fields or {}).get("order", ""))):
        if len(value) >= 4 and any(char.isdigit() for char in value):
            if re.search(rf"(?<![^\W_]){re.escape(value)}(?![^\W_])", description, re.IGNORECASE):
                return label
    return ""


def candidates(transaction, invoices, aliases):
    merchant = merchant_key(transaction.merchant)
    known = aliases.get(merchant, [])
    ranked = []
    for invoice in invoices:
        if invoice.currency != transaction.currency or signed_total(invoice) != transaction.amount:
            continue
        distance = abs((invoice.day - transaction.day).days) if invoice.day else 9999
        seller = merchant_key(invoice.seller)
        named = bool(seller and (seller in known or seller in merchant or merchant in seller))
        ranked.append((not matching_reference(transaction.merchant, invoice), not named,
                       distance, invoice.path.name.casefold(), invoice))
    return [row[-1] for row in sorted(ranked, key=lambda row: row[:-1])]


def gmail_url(merchant, day):
    safe = merchant.replace('"', " ").replace("\n", " ").replace("\r", " ")
    term = f"from:{safe}" if re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", safe) else f'"{safe}"'
    search = f'has:attachment filename:pdf after:{day - timedelta(days=14)} before:{day + timedelta(days=15)} {term}'
    return f"https://mail.google.com/mail/u/0/#search/{quote(search, safe='')}"


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
                "advisor_note", "due", "follow_up", "allocations", "difference_reason", "transfer", "refund_group"
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
    "명세서만 확보": "Nur Konto- oder Kartenabrechnung vorhanden", "기타 · 세무사 확인": "Sonstiges – steuerlich prüfen",
}
UNLINKED_REASONS = ("", "미결제", "다른 결제수단", "다음 달 확인")

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
        if not (links or record.get("transfer") or record.get("refund_group") or record.get("statement_evidence")):
            issues.append((REVIEW_KINDS[0], number, merchant))
        if record.get("statement_evidence") or record.get("follow_up") in ("세무사 확인 필요", "증빙 확보 불가"):
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
    def __init__(self, parent, folder, practice_folder=None):
        self.root = tk.Toplevel(parent)
        self.root.title(("[연습 모드] " if practice_folder else "") + f"카드 명세서 월 마감 · v{VERSION}")
        self.root.geometry("1160x720")
        self.folder = folder
        self.practice_folder = practice_folder
        self.transactions = {}
        self.statement_sources = {}
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
            self.root.destroy()
            return

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        self.buttons(top, [
            ("은행·카드 명세서 추가", self.open_csv), ("여러 명세서 추가", self.open_batch), ("증빙 폴더 선택", self.select_folder),
            ("CSV 내보내기", self.export_csv), ("세무사용 ZIP", self.export_zip), ("추가 제출 ZIP", lambda: self.export_zip(supplemental=True)),
            ("매칭완료 폴더로 이동", self.archive_matched),
        ])
        actions = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        actions.pack(fill="x")
        self.buttons(actions, [
            ("이어서 작업", self.resume), ("일괄 매칭 후보", self.batch_candidates),
            ("카드대금·내부이체 후보", self.transfer_candidates), ("결제·환불 연결", self.link_refunds), ("이동 되돌리기", self.undo_archive), ("후속 목록", self.followup_list), ("마감본 이력", self.export_history),
        ])
        workflow = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        workflow.pack(fill="x")
        self.buttons(workflow, [
            ("작업 백업", self.export_backup), ("작업 복원", self.restore_backup),
            ("월 마감 확정·재열기", self.manage_period), ("전체 월 검색", self.search_all_months),
            ("미연결 증빙", self.show_unlinked_invoices), ("명세서 기간 점검", self.show_statement_periods),
            ("명세서 근거 표시", self.mark_statement_evidence), ("월별 누락 점검표", self.monthly_review),
            ("전체 작업 백업", self.export_workspace_backup), ("전체 작업 복원", self.restore_workspace_backup),
        ])
        if practice_folder:
            ttk.Button(workflow, text="예시 명세서 불러오기", command=lambda: self.load_practice_statement(practice_folder)).pack(side="left")
        self.unresolved = tk.BooleanVar()
        ttk.Checkbutton(top, text="확인 필요한 거래만", variable=self.unresolved, command=self.refresh).pack(side="left", padx=8)
        self.status = tk.StringVar(value="은행·카드 명세서와 증빙 폴더를 선택하세요.")
        ttk.Label(self.root, textvariable=self.status, padding=8, wraplength=1100).pack(side="bottom", fill="x")

        body = ttk.Panedwindow(self.root, orient="vertical")
        body.pack(fill="both", expand=True, padx=8, pady=4)
        upper, lower = ttk.Frame(body), ttk.Frame(body)
        body.add(upper, weight=3)
        body.add(lower, weight=2)
        self.cards = self.table(upper, ("번호", "거래일", "가맹점·거래처", "금액", "증빙 상태", "메모"), (85, 105, 240, 120, 230, 200))
        self.cards.bind("<<TreeviewSelect>>", lambda event: self.show_invoices())
        controls = ttk.Frame(lower, padding=(0, 5))
        controls.pack(fill="x")
        self.buttons(controls, [
            ("선택 연결", self.link), ("연결 해제", self.unlink), ("증빙 열기", self.open_pdf),
            ("나란히 보기", self.compare_documents), ("정정·원본 연결", self.edit_correction),
            ("거래 메모·기록", self.edit_note), ("증빙 전달 메모", self.edit_document_note), ("보조 자료 첨부", self.edit_attachments), ("배분·차액", self.edit_allocation), ("접대비", self.edit_hospitality), ("Gmail 검색", self.search_gmail), ("인보이스 요청 초안", self.draft_invoice_request),
        ])
        self.all_pdfs = tk.BooleanVar()
        ttk.Checkbutton(controls, text="전체 증빙 표시", variable=self.all_pdfs, command=self.show_invoices).pack(side="left", padx=8)
        self.pdfs = self.table(lower, ("증빙", "발행일", "총액", "인보이스 번호", "상태"), (420, 100, 110, 130, 200))
        self.pdfs.bind("<Double-1>", lambda event: self.open_pdf())
        if folder:
            self.load_pdfs(folder)

    def buttons(self, parent, buttons):
        for label, command in buttons:
            ttk.Button(parent, text=label, command=command).pack(side="left", padx=(0, 5))

    def table(self, parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        table = ttk.Treeview(frame, columns=columns, show="headings")
        for name, width in zip(columns, widths):
            table.heading(name, text=name)
            table.column(name, width=width, minwidth=65)
        bar = ttk.Scrollbar(frame, command=table.yview)
        table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        return table

    def load_practice_statement(self, folder):
        path = folder / "Karte.csv"
        try:
            key, _, rows, layout = self.read_import(path)
            if key in self.statement_sources:
                self.status.set("예시 명세서를 이미 불러왔어요.")
                return
            spec = {"path": str(path), "columns": [0, 1, 2, 3], "currency": "EUR",
                    "date_order": "DD/MM/YYYY", "account": "연습 카드", "period": "", "flip_sign": False, "layout": layout}
            transactions, errors = self.parse_spec(rows, spec)
            self.add_statement(key, spec, transactions, errors)
        except (OSError, ValueError, RuntimeError, csv.Error) as error:
            record_error("statement", error)
            messagebox.showerror("예시 명세서 열기 실패", str(error), parent=self.root)

    def open_batch(self):
        if self.practice_folder:
            self.load_practice_statement(self.practice_folder)
            return
        names = filedialog.askopenfilenames(parent=self.root, filetypes=[("CSV / TSV / PDF", "*.csv *.tsv *.pdf")])
        if not names:
            return
        window = tk.Toplevel(self.root)
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

        for i, name in enumerate(names):
            key, path = str(i), Path(name)
            by_path[key] = path
            table.insert("", "end", iid=key, values=(path.name, *inspect(key, path)))

        def edit_selected(_event=None):
            selection = table.selection()
            if len(selection) != 1:
                self.status.set("설정을 확인할 명세서 한 개를 선택하세요.")
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

        def apply():
            chosen = [key for key in table.selection() if key in prepared]
            if not chosen:
                messagebox.showinfo("명세서 가져오기", "검토가 끝난 행을 선택하세요.", parent=window)
                return
            pending = [key for key in table.selection() if key not in prepared]
            prompt = f"준비된 명세서 {len(chosen)}개를 가져올까요?"
            if pending:
                prompt += f"\n설정이 필요한 {len(pending)}개는 가져오지 않아요."
            if not messagebox.askyesno("명세서 가져오기", prompt, parent=window):
                return
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

        self.buttons(bar, [("선택 파일 설정", edit_selected), ("선택 가져오기", apply),
                           ("준비된 파일 모두 선택", lambda: table.selection_set(tuple(prepared)))])
        table.bind("<Double-1>", edit_selected)

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
        window = tk.Toplevel(self.root)
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
            if not messagebox.askyesno("요청 완료 기록", "메일을 직접 보냈나요? 오늘 날짜로 요청 완료를 기록할까요?", parent=window):
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
        dialog = tk.Toplevel(owner)
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
        labels = ("거래일 열", "가맹점 열", "금액 열", "통화 열 (선택)", "기본 통화", "연도가 뒤에 오는 날짜", "계좌·카드 별칭", "마감월 YYYY-MM (선택)", "지출이 음수인 명세서")
        hints = (
            r"transaction date|purchase date|kaufdatum|buchungstag|buchungsdatum|umsatzdatum|datum|거래일|이용일|사용일|date",
            r"merchant|händler|empfänger|auftraggeber|beschreibung|verwendungszweck|buchungstext|가맹점|사용처|description",
            r"amount|betrag|금액|umsatz",
            r"currency|währung|통화",
        )
        for index, label in enumerate(labels):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=4, padx=(0, 10))
            if index < 4:
                values = columns if index != 3 else ["기본 통화 사용", *columns]
                entry = ttk.Combobox(form, values=values, state="readonly", width=39)
                guess = next((i for i, name in enumerate(header) if re.search(hints[index], name, re.I)), None)
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
            elif index in (6, 7):
                entry = ttk.Entry(form, width=42)
                if index == 6:
                    entry.insert(0, self.state.get("source_accounts", {}).get(source_key, ""))
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
                choices[7].delete(0, "end")
                choices[7].insert(0, profile["period"])

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
            if -1 in primary or len(set(primary)) != 3 or currency_index in primary or not re.fullmatch(r"[A-Z]{3}", default):
                messagebox.showerror("열 지정 확인", "거래일·가맹점·금액에 서로 다른 열을 고르고 기본 통화를 입력하세요.", parent=dialog)
                return
            account = choices[6].get().strip()
            period = choices[7].get().strip()
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
            transactions, errors, check, discrepancy = self.preview_import(source, rows, spec)
            if is_pdf:
                preview = "\n".join(f"{item.day}  {item.merchant}  {item.amount} {item.currency}" for item in transactions[:8])
                if not messagebox.askyesno("PDF 추출 확인", f"{len(transactions)}건 추출 · 확인 {len(errors)}행\n{check}\n\n{preview}\n\n계속할까요?", parent=dialog):
                    return
            if on_review:
                on_review(source_key, spec, transactions, errors, check, discrepancy)
                dialog.destroy()
                owner.grab_set()
                return
            spec = self.resolve_overlaps(source_key, spec, transactions, dialog)
            if spec is None:
                return
            if not self.add_statement(source_key, spec, transactions, errors):
                return
            dialog.destroy()
            if errors:
                preview = "\n".join(f"{line}행 · {error}" for line, error in errors[:10])
                messagebox.showwarning("읽지 못한 거래", f"{len(errors)}개 행을 확인하세요.\n\n{preview}", parent=self.root)

        ttk.Button(form, text="설정 확인" if on_review else "불러오기", command=load).grid(row=10, column=1, sticky="e", pady=(8, 0))

    def parse_spec(self, rows, spec):
        return parse_transactions(rows, spec["columns"], spec["currency"], spec["date_order"],
                                  spec["account"], spec["flip_sign"])

    def add_statement(self, source_key, spec, transactions, errors, remember=True):
        state = deepcopy(self.state)
        resolutions = spec.get("resolutions", state.get("source_resolutions", {}).get(source_key, {}))
        originals = {resolutions.get(item.key, item.key): item.key for item in transactions}
        transactions = [replace(item, key=resolutions.get(item.key, item.key)) for item in transactions]
        assign_numbers(state["records"], transactions, spec["period"])
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
            state.setdefault("import_profiles", {}).setdefault(spec["layout"], {})[account] = {
                key: spec[key] for key in PROFILE_FIELDS
            }
        state.setdefault("statement_paths", {})[source_key] = str(source)
        state.setdefault("source_resolutions", {})[source_key] = resolutions
        specs = {**self.session_specs, source_key: {**spec, "resolutions": resolutions}}
        if remember:
            state["session"] = {"sources": specs, "folder": str(self.folder) if self.folder else ""}
        if not self.save(state):
            return False
        self.session_specs = specs
        self.transactions.update((item.key, item) for item in transactions)
        self.accounts.update((item.key, account) for item in transactions)
        self.statement_sources[source_key] = source
        self.errors.extend((f"{source.name}:{line}", error) for line, error in errors)
        self.refresh()
        return True

    def resume(self):
        session = self.state.get("session", {})
        sources = session.get("sources", {})
        if not sources:
            self.status.set("이어 열 수 있는 명세서가 없어요.")
            return
        if self.transactions and not messagebox.askyesno("이어서 작업", "현재 화면을 마지막 저장 작업으로 바꿀까요?", parent=self.root):
            return
        prepared, problems = {}, []
        for checksum, spec in sources.items():
            path = Path(spec["path"])
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
        if prepared:
            self.transactions.clear()
            self.accounts.clear()
            self.session_specs.clear()
            self.statement_sources.clear()
            self.errors.clear()
            self.unresolved.set(True)
            for checksum, (spec, transactions, errors) in prepared.items():
                self.add_statement(checksum, spec, transactions, errors, remember=False)
            if session.get("folder"):
                self.load_pdfs(Path(session["folder"]))
        if problems:
            messagebox.showwarning("다시 열지 못한 명세서", "\\n".join(problems), parent=self.root)

    def search_all_months(self):
        if not self.folder:
            self.select_folder()
            if not self.folder:
                return
        dialog = tk.Toplevel(self.root)
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
            self.status.set("기간을 점검할 명세서를 불러오세요.")
            return
        dialog = tk.Toplevel(self.root)
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
        dialog = tk.Toplevel(self.root)
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

    def mark_statement_evidence(self):
        item = self.single_card()
        if not item:
            return
        record = self.record(item.key)
        if self.linked(item.key) or record.get("transfer") or record.get("refund_group"):
            self.status.set("증빙 또는 별도 거래 연결이 없는 거래를 선택하세요.")
            return

        def commit(state, values):
            updated = state["records"][item.key]
            if values[0]:
                updated["statement_evidence"] = values[0]
            else:
                updated.pop("statement_evidence", None)

        self.edit_fields("명세서 근거 · 세무사 확인", self.card_label(item),
                         [("증빙 없이 검토하는 사유", record.get("statement_evidence", ""), list(NO_EVIDENCE_REASONS))], commit)

    def select_folder(self):
        if self.practice_folder:
            self.load_pdfs(self.practice_folder / "Belege")
            return
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.folder)
        if folder:
            self.load_pdfs(Path(folder))
            if self.session_specs and self.folder == Path(folder):
                state = deepcopy(self.state)
                state["session"] = {"sources": self.session_specs, "folder": str(self.folder)}
                self.save(state)

    def load_pdfs(self, folder):
        self.status.set("PDF를 읽고 있어요…")
        self.root.update_idletasks()
        try:
            self.invoices, self.read_issues = index_pdfs(folder)
        except OSError as error:
            messagebox.showerror("PDF 폴더 열기 실패", str(error), parent=self.root)
            return
        self.folder = folder
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

    def review_pairs(self, title, rows, commit):
        if not rows:
            self.status.set("확인할 후보가 없어요.")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("1040x470")
        dialog.transient(self.root)
        dialog.grab_set()
        tools = ttk.Frame(dialog, padding=8)
        tools.pack(fill="x")
        table = self.table(dialog, ("거래", "연결 후보"), (430, 580))
        for index, (_, _, first, second) in enumerate(rows):
            table.insert("", "end", iid=str(index), values=(first, second))

        def apply():
            selected = [rows[int(key)][:2] for key in table.selection()]
            if not selected:
                messagebox.showinfo(title, "연결할 행을 선택하세요. Ctrl이나 Shift로 여러 행을 선택할 수 있어요.", parent=dialog)
                return
            if messagebox.askyesno(title, f"선택한 후보 {len(selected)}건을 연결할까요?", parent=dialog):
                state = deepcopy(self.state)
                for first, second in selected:
                    commit(state["records"], first, second)
                if self.save(state):
                    dialog.destroy()

        self.buttons(tools, [("전체 선택", lambda: table.selection_set(table.get_children())), ("선택한 후보 연결", apply)])
        ttk.Label(tools, text="Ctrl 또는 Shift로 여러 행을 선택할 수 있어요.").pack(side="left", padx=8)

    def card_label(self, item):
        return f"{self.record(item.key).get('number', '')} · {item.day} · {item.merchant} · {item.amount} {item.currency}"

    def unlinked_transactions(self):
        return [item for item in self.transactions.values() if not self.linked(item.key) and not self.record(item.key).get("transfer") and not self.record(item.key).get("refund_group") and not self.record(item.key).get("statement_evidence")]

    def batch_candidates(self):
        used = {link for record in self.state["records"].values() for link in record.get("links", [])}
        pairs = []
        for item in self.unlinked_transactions():
            merchant = merchant_key(item.merchant)
            known = self.state["aliases"].get(merchant, [])
            options = [
                doc for doc in candidates(item, self.invoices, self.state["aliases"])
                if doc.day and abs((doc.day - item.day).days) <= 7 and doc.key not in used
                and doc.key not in self.duplicates and doc.seller
                and (merchant_key(doc.seller) in known or merchant_key(doc.seller) == merchant)
            ]
            if len(options) == 1:
                pairs.append((item.key, options[0].key))
        rows = [(first, second, self.card_label(self.transactions[first]), self.by_key[second].path.name)
                for first, second in unique_pairs(pairs)]
        self.review_pairs("일괄 매칭 후보", rows, lambda records, first, second: records[first].update(links=[second]))

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
            target = parent / f"매칭완료_{month[2:].replace('-', '')}" / doc.path.name
            if doc.path != target:
                moves.append((doc, target))
        return moves, skipped

    def archive_matched(self):
        moves, skipped = self.matched_moves()
        if not moves:
            self.status.set(f"옮길 매칭완료 증빙이 없어요. 중복 또는 마감월 확인 필요 {skipped}개.")
            return
        preview = "\n".join(str(target.relative_to(self.folder)) for _, target in moves[:5])
        more = f"\n… 외 {len(moves) - 5}개" if len(moves) > 5 else ""
        if not messagebox.askyesno("매칭완료 증빙 이동", f"증빙 {len(moves)}개를 옮길까요?\n\n{preview}{more}", parent=self.root):
            return
        errors = []
        history = []
        for doc, target in moves:
            try:
                if doc.path.is_symlink() or not target.resolve().is_relative_to(self.folder.resolve()):
                    raise OSError("선택한 증빙 폴더 밖으로 이동할 수 없어요.")
                if digest(doc.path) != doc.key:
                    raise OSError("증빙이 변경됐어요. 증빙 폴더를 다시 불러오세요.")
                target.parent.mkdir(exist_ok=True)
                move_without_overwrite(doc.path, target)
                history.append((doc.path, target, doc.key))
            except OSError as error:
                errors.append(f"{doc.path.name}: {error}")
        if history:
            self.last_moves = history
        self.load_pdfs(self.folder)
        self.status.set(f"{len(moves) - len(errors)}개 이동 · 확인 필요 {skipped}개 · 실패 {len(errors)}개")
        if errors:
            messagebox.showwarning("이동 결과", "\n".join(errors[:8]), parent=self.root)

    def undo_archive(self):
        if not self.last_moves:
            self.status.set("되돌릴 증빙 이동이 없어요.")
            return
        if not messagebox.askyesno("이동 되돌리기", f"증빙 {len(self.last_moves)}개를 원래 폴더로 되돌릴까요?", parent=self.root):
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
        refunds = self.record(key).get("refund_group")
        if refunds:
            if any(other not in self.transactions for other in refunds):
                return "환불 상대 명세서 필요"
            remaining = sum(self.transactions[other].amount for other in refunds)
            if remaining:
                return f"일부 환불 · 잔액 {remaining:.2f} {self.transactions[key].currency}"
            return "전액 환불 확인"
        peer = self.record(key).get("transfer")
        if peer:
            if peer not in self.transactions or self.record(peer).get("transfer") != key:
                return "이체 상대 명세서 필요"
            return "내부이체 확인"
        links = self.linked(key)
        if not links:
            return "명세서 근거 · 세무사 확인" if self.record(key).get("statement_evidence") else "증빙 미연결"
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
        for key, item in self.transactions.items():
            state = self.status_for(key)
            counts[state] += 1
            follow = self.record(key).get("follow_up")
            if not self.unresolved.get() or follow in ("요청 중", "세무사 확인 필요", "증빙 확보 불가") or state not in ("금액 대조 완료", "내부이체 확인", "전액 환불 확인"):
                self.cards.insert("", "end", iid=key, values=(
                    self.record(key).get("number", ""), item.day, item.merchant, f"{item.amount} {item.currency}", state, self.note_preview(key),
                ))
        self.cards.selection_set([key for key in selected if self.cards.exists(key)])
        self.show_invoices()
        self.status.set(
            f"거래 {len(self.transactions)}건 · 확인 완료 {counts['금액 대조 완료']}건 · "
            f"미연결 {counts['증빙 미연결']}건 · CSV 오류 {len(self.errors)}행 · "
            f"PDF {len(self.invoices)}개 · PDF 읽기 오류 {len(self.read_issues)}개"
            + (f"   |   {self.folder}" if self.folder else "")
        )

    def show_invoices(self):
        self.pdfs.delete(*self.pdfs.get_children())
        selected = self.cards.selection()
        linked = set().union(*(self.linked(key) for key in selected))
        exact = []
        seen = set(linked)
        for key in selected:
            for invoice in candidates(self.transactions[key], self.invoices, self.state["aliases"]):
                if invoice.key not in seen:
                    exact.append(invoice)
                    seen.add(invoice.key)
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
            self.pdfs.insert("", "end", iid=str(index), values=(
                doc.path.name, doc.day or "", f"{signed_total(doc)} {doc.currency}" if doc.amount is not None else "",
                doc.number, " · ".join(labels),
            ))

    def save(self, state):
        locked = affected_locked_month(self.state, state)
        if locked:
            messagebox.showerror("마감 확정", f"{locked}은 확정된 달이에요. 변경하려면 ‘월 마감 확정·재열기’에서 먼저 다시 여세요.", parent=self.root)
            return False
        fields = {"links": "증빙 연결", "transfer": "내부이체", "refund_group": "환불 연결",
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
        dialog = tk.Toplevel(self.root)
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

    def monthly_review(self):
        rows = review_months(self.state, self.invoices)
        if not rows:
            self.status.set("먼저 증빙이나 명세서를 불러오세요.")
            return
        dialog = tk.Toplevel(self.root)
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
            self.status.set("연습 모드는 예시 자료만 사용해요.")
            return
        source = filedialog.askopenfilename(parent=self.root, filetypes=[("전체 작업 백업", "*.zip")])
        if not source:
            return
        parent = filedialog.askdirectory(parent=self.root, title="복원 폴더를 만들 위치")
        if not parent or not messagebox.askyesno("전체 작업 복원",
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
            self.status.set("연습 모드는 예시 자료만 사용해요.")
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
        if not messagebox.askyesno("작업 기록 복원",
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
        self.session_specs.clear()
        self.errors.clear()
        self.last_moves.clear()
        self.refresh()
        self.status.set(f"복원 완료 · ‘이어서 작업’으로 열 수 있어요. · 복원 전 사본 {backup}")

    def manage_period(self):
        months = sorted({record.get("period", "") for record in self.state["records"].values()} - {""}, reverse=True)
        if not months:
            self.status.set("먼저 명세서를 불러오세요.")
            return
        dialog = tk.Toplevel(self.root)
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
                if not messagebox.askyesno("마감 확정", f"{chosen} 거래와 연결 증빙을 잠글까요?", parent=dialog):
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
            self.status.set("거래와 연결할 PDF를 선택하세요. 후보가 없다면 ‘전체 증빙 표시’를 켜세요.")
            return
        if not remove and any(self.record(key).get("transfer") or self.record(key).get("refund_group") for key in cards):
            self.status.set("증빙을 연결하려면 먼저 이체 또는 환불 연결을 해제하세요.")
            return
        keys = {doc.key for doc in docs}
        if remove:
            if any(self.record(key).get("transfer") or self.record(key).get("refund_group") for key in cards):
                prompt = "선택한 거래의 이체 또는 환불 연결을 해제할까요?"
            else:
                prompt = "선택한 거래에서 선택한 PDF 연결을 해제할까요?" if keys else "선택한 거래의 모든 PDF 연결을 해제할까요?"
            if not messagebox.askyesno("연결 해제", prompt, parent=self.root):
                return
        else:
            repeated = any(keys.intersection(record.get("links", []))
                           for key, record in self.state["records"].items() if key not in cards)
            if repeated or len(cards) > 1 or len(docs) > 1:
                prompt = f"거래 {len(cards)}건 각각에 PDF {len(keys)}개를 연결할까요?"
                if repeated:
                    prompt += "\n선택한 PDF 중 다른 거래에 연결된 파일이 있어요."
                if not messagebox.askyesno("여러 거래 또는 PDF 연결", prompt, parent=self.root):
                    return
        state = deepcopy(self.state)
        for key in cards:
            record = state["records"].setdefault(key, {})
            if remove:
                for related in record.pop("refund_group", []):
                    state["records"].get(related, {}).pop("refund_group", None)
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
        self.save(state)

    def link(self):
        self.change_links(False)

    def unlink(self):
        self.change_links(True)

    def single_card(self):
        selected = self.cards.selection()
        if len(selected) != 1:
            self.status.set("카드 거래 한 건을 선택하세요.")
            return None
        return self.transactions[selected[0]]

    def note_preview(self, key):
        record = self.record(key)
        due = record.get("due", "")
        follow = record.get("follow_up", "")
        if follow == "요청 중" and due and due < date.today().isoformat():
            follow = "기한 지남"
        text = [follow, f"예정 {due}" if due else "", record.get("difference_reason", ""), record.get("statement_evidence", "")]
        text.append(record.get("advisor_note") or record.get("note", ""))
        return " · ".join(filter(None, text))

    def edit_note(self):
        item = self.single_card()
        if item is None:
            return
        record = self.record(item.key)
        dialog = tk.Toplevel(self.root)
        dialog.title("거래 메모·기록")
        dialog.transient(self.root)
        dialog.grab_set()
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)

        def text_box(label, value, height):
            ttk.Label(form, text=label).pack(anchor="w", pady=(6, 2))
            box = tk.Text(form, width=66, height=height, wrap="word")
            box.insert("1.0", value)
            box.pack(fill="x")
            return box

        ttk.Label(form, text=self.card_label(item)).pack(anchor="w")
        internal = text_box("내 작업 메모 · 프로그램 안에서만 표시", record.get("note", ""), 3)
        history = "\n".join(f"{entry['date']} · {entry['text']}" for entry in record.get("timeline", []))
        previous = text_box("작업 기록", history, 5)
        previous.configure(state="disabled")
        action = text_box("새 작업 기록 · 예) 인보이스 요청", "", 2)
        when = ttk.Entry(form, width=15)
        ttk.Label(form, text="작업 기록 날짜").pack(anchor="w", pady=(6, 2))
        when.insert(0, date.today().isoformat())
        when.pack(anchor="w")
        external = text_box("세무사 전달 메모 (독일어) · ZIP에 포함", record.get("advisor_note", ""), 3)
        templates = ttk.Frame(form)
        templates.pack(anchor="w")
        for title, wording in GERMAN_TEMPLATES.items():
            ttk.Button(templates, text=title, command=lambda value=wording: external.insert("end", ("\n" if external.get("1.0", "end-1c") else "") + value)).pack(side="left", padx=2)
        follow_up = ttk.Combobox(form, values=list(FOLLOW_UP), state="readonly")
        ttk.Label(form, text="후속 처리 상태").pack(anchor="w", pady=(6, 2))
        follow_up.set(record.get("follow_up", ""))
        follow_up.pack(anchor="w")
        ttk.Label(form, text="증빙 후속 예정일 (YYYY-MM-DD, 선택)").pack(anchor="w", pady=(6, 2))
        due = ttk.Entry(form, width=15)
        due.insert(0, record.get("due", ""))
        due.pack(anchor="w")

        ttk.Button(form, text="독일어 초안 미리보기", command=lambda: self.preview_draft(
            dialog, item, action.get("1.0", "end-1c").strip() or internal.get("1.0", "end-1c").strip(),
            due.get().strip(), follow_up.get(), external,
        )).pack(anchor="w", pady=(6, 0))

        def save():
            planned, entered = due.get().strip(), when.get().strip()
            event = action.get("1.0", "end-1c").strip()
            if (planned and normalized_date(planned) != planned) or (event and (not entered or normalized_date(entered) != entered)):
                messagebox.showerror("날짜 확인", "날짜를 YYYY-MM-DD 형식으로 입력하세요.", parent=dialog)
                return
            state = deepcopy(self.state)
            record = state["records"].setdefault(item.key, {})
            record.update(note=internal.get("1.0", "end-1c").strip(),
                          advisor_note=advisor, due=planned, follow_up=follow_up.get())
            if event:
                record.setdefault("timeline", []).append({"date": entered, "text": event})
            if self.save(state):
                dialog.destroy()

        ttk.Button(form, text="저장", command=save).pack(anchor="e", pady=(8, 0))

    def preview_draft(self, parent, item, source, due, follow_up, destination):
        try:
            draft = german_draft(source, due, follow_up)
        except ValueError as error:
            messagebox.showerror("독일어 초안", str(error), parent=parent)
            return
        if not draft:
            messagebox.showinfo("독일어 초안", "요청·수신·검토 상태를 선택하거나 요청 내용을 적어 주세요.", parent=parent)
            return
        window = tk.Toplevel(parent)
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
            destination.insert("end", ("\\n" if destination.get("1.0", "end-1c") else "") + approved)
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
            self.status.set("세무사 메모를 남길 증빙 한 개를 선택하세요.")
            return
        doc = self.visible_pdfs[selected[0]]
        dialog = tk.Toplevel(self.root)
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
        dialog = tk.Toplevel(self.root)
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
            self.status.set("접대비 정보를 입력할 증빙 한 장을 선택하세요.")
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
        dialog = tk.Toplevel(self.root)
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
        dialog = tk.Toplevel(self.root)
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
        keys = list(self.cards.selection())
        if len(keys) < 2:
            self.status.set("결제 한 건과 환불 한 건 이상을 Ctrl로 함께 선택하세요.")
            return
        try:
            remaining = refund_balance([self.transactions[key] for key in keys])
            if any(self.linked(key) or self.record(key).get("transfer") or self.record(key).get("refund_group") for key in keys):
                raise ValueError("기존 연결이 있는 거래는 먼저 연결을 해제하세요.")
        except ValueError as error:
            messagebox.showerror("결제·환불 연결", str(error), parent=self.root)
            return
        currency = self.transactions[keys[0]].currency
        if not messagebox.askyesno("결제·환불 연결", f"선택한 거래 {len(keys)}건을 연결할까요?\n환불 후 잔액: {remaining:.2f} {currency}", parent=self.root):
            return
        state = deepcopy(self.state)
        for key in keys:
            state["records"].setdefault(key, {})["refund_group"] = keys
        self.save(state)

    def compare_documents(self):
        item = self.single_card()
        if item is None:
            return
        entries = list(self.visible_pdfs.items())
        if not entries:
            self.status.set("표시할 증빙이 없어요. ‘전체 증빙 표시’를 켜서 확인하세요.")
            return
        window = tk.Toplevel(self.root)
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
        canvas = tk.Canvas(right, background="#eeeeee", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        def draw(_event=None):
            _, doc = entries[selector.current()]
            reference = matching_reference(item.merchant, doc)
            info = f"{doc.day or ''} · {doc.seller} · {doc.amount if doc.amount is not None else ''} {doc.currency}"
            if reference:
                info += f" · {reference} 일치"
            caption.configure(text=info)
            canvas.delete("all")
            canvas.image = None
            try:
                if doc.path.suffix.lower() == ".xml":
                    meta = doc.tax_fields or {}
                    values = [doc.path.name, f"인보이스 번호: {doc.number}", f"주문번호: {meta.get('order', '')}",
                              f"원본 참조: {meta.get('reference', '')}", f"공급가액: {meta.get('net', '')}", f"부가세: {meta.get('tax', '')}"]
                    canvas.create_text(12, 12, text="\n\n".join(values), anchor="nw", width=max(120, canvas.winfo_width() - 24))
                else:
                    width = max(180, canvas.winfo_width() - 16)
                    height = max(180, canvas.winfo_height() - 16)
                    data = first_page_preview(doc.path, width, height)
                    canvas.image = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
                    canvas.create_image(canvas.winfo_width() / 2, 8, image=canvas.image, anchor="n")
            except (OSError, ValueError, RuntimeError, tk.TclError) as error:
                canvas.create_text(12, 12, text=f"미리보기를 열 수 없어요.\n{error}", anchor="nw")

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
        canvas.bind("<Configure>", draw)

    def edit_correction(self):
        selection = self.pdfs.selection()
        if len(selection) != 1:
            self.status.set("정정 인보이스 한 장을 선택하세요.")
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

    def open_pdf(self):
        selected = self.pdfs.selection()
        if len(selected) != 1:
            self.status.set("열 PDF 한 개를 선택하세요.")
            return
        self.open_evidence_file(self.visible_pdfs[selected[0]].path)

    def open_evidence_file(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            elif not path.exists() or not webbrowser.open(path.resolve().as_uri()):
                raise OSError("PDF를 열 수 없어요.")
        except OSError as error:
            messagebox.showerror("PDF 열기 실패", str(error), parent=self.root)

    def search_gmail(self):
        item = self.single_card()
        if item is None:
            return
        key = merchant_key(item.merchant)
        previous = self.state.get("senders", {}).get(key, item.merchant)
        merchant = simpledialog.askstring("Gmail 검색", "업체명이나 발신자 이메일 주소", initialvalue=previous, parent=self.root)
        if merchant and merchant.strip():
            merchant = merchant.strip()
            if merchant != previous and re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", merchant):
                state = deepcopy(self.state)
                state.setdefault("senders", {})[key] = merchant
                self.save(state)
            webbrowser.open(gmail_url(merchant, item.day))

    def export_csv(self):
        if not self.transactions and not self.errors:
            self.status.set("먼저 은행·카드 명세서를 불러오세요.")
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
            self.status.set("먼저 은행·카드 명세서를 불러오세요.")
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
            self.status.set("전달한 마감본을 이력에서 표시한 뒤, 새 증빙이나 변경된 거래가 있는지 확인하세요.")
            return
        months = sorted({self.record(key)["period"] for key in keys})
        versions = self.state.get("exports", {})
        revision = max((entry["version"] for month in months for entry in versions.get(month, [])), default=0) + 1
        span = "_bis_".join(dict.fromkeys([months[0], months[-1]])) if months else "ohne_Buchungen"
        prefix = "Nachreichung" if supplemental else "Monatsabschluss"
        filename = f"{prefix}_{span}_v{revision}.zip"
        with tempfile.TemporaryDirectory(prefix="invoice-preview-") as folder:
            preview = Path(folder) / filename
            try:
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
                    evidence_count = sum(name.startswith("Belege/") for name in archive.namelist())
            except (OSError, ValueError, RuntimeError) as error:
                record_error("zip", error)
                messagebox.showerror("제출 미리보기 실패", str(error), parent=self.root)
                return
            translation_count = sum(note == "Übersetzung der Notiz ausstehend." for _, note in notes)
            dialog = tk.Toplevel(self.root)
            dialog.title("추가 제출 미리보기" if supplemental else "세무사용 ZIP 미리보기")
            dialog.geometry("950x540")
            dialog.transient(self.root)
            dialog.grab_set()
            ttk.Label(dialog, text=f"거래 {len(summary)}건 · 증빙 파일 {evidence_count}개 · 첨부 {attachment_count}개 · 미해결 {len(unresolved)}건 · 번역 대기 메모 {translation_count}개", padding=10).pack(anchor="w")
            table = self.table(dialog, ("거래번호", "구분", "내보낼 내용"), (100, 145, 660))
            for number, note in notes:
                table.insert("", "end", values=(number, "독일어 전달 메모", note.replace("\n", " / ")))
            for row in unresolved:
                table.insert("", "end", values=(row["Belegnummer"], "미해결 거래", row["Abgleich"]))
            for row in details:
                path = row["Datei im ZIP"]
                kind = "이전 전달 증빙" if path.startswith("Bereits übermittelt: ") else "증빙 파일"
                table.insert("", "end", values=(row["Belegnummer"], kind, path))

            for row in attachment_rows:
                table.insert("", "end", values=(row["Belegnummer"], "보조 자료", f'{row["Quelldatei"]} · {row["Status"]}'))

            def save_zip():
                path = filedialog.asksaveasfilename(parent=dialog, defaultextension=".zip", initialfile=filename, filetypes=[("ZIP", "*.zip")])
                if not path:
                    return
                target = Path(path)
                created = False
                try:
                    with target.open("xb") as output:
                        created = True
                        with preview.open("rb") as source:
                            shutil.copyfileobj(source, output)
                except OSError as error:
                    if created:
                        target.unlink(missing_ok=True)
                    record_error("zip", error)
                    messagebox.showerror("ZIP 저장 실패", str(error), parent=dialog)
                    return
                state = deepcopy(self.state)
                for month in months:
                    state.setdefault("exports", {}).setdefault(month, []).append({
                        "version": revision, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "path": str(target), "delivery_status": DELIVERY_STATES[0], "kind": prefix,
                        "snapshot": {key: current[key] for key in keys if self.record(key)["period"] == month},
                    })
                if self.save(state):
                    self.status.set(f"세무용 ZIP 저장 완료 · {path}")
                else:
                    self.status.set(f"ZIP 저장 완료 · 이력 저장 실패 · {path}")
                dialog.destroy()

            ttk.Button(dialog, text="이 내용으로 ZIP 저장", command=save_zip).pack(anchor="e", padx=10, pady=8)
            self.root.wait_window(dialog)

    def write_package(self, target, keys=None, baselines=None):
        temporary = None
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
                for index, (checksum, source) in enumerate(self.statement_sources.items() if baselines is None else [], 1):
                    arcname = f"Kontoauszuege/{index:02d}_{source.name}"
                    data = source.read_bytes()
                    if hashlib.sha256(data).hexdigest() != checksum:
                        raise ValueError(f"명세서가 변경됐어요. 다시 불러오세요. {source.name}")
                    archive.writestr(arcname, data)
                    source_writer.writerow([arcname, export_value(source.name), checksum])
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
                    german_status = {
                        "금액 대조 완료": "Abgeglichen", "증빙 미연결": "Beleg fehlt",
                        "명세서 근거 · 세무사 확인": "Auszug als Nachweis · steuerlich prüfen",
                        "연결 PDF 위치 확인": "Verknüpfte Datei fehlt",
                        "연결됨 · PDF 총액 확인": "Rechnungsbetrag prüfen",
                        "연결됨 · 통화 확인": "Währung prüfen",
                        "내부이체 확인": f"Interner Transfer zu {self.record(record.get('transfer', '')).get('number', '')}",
                        "이체 상대 명세서 필요": "Gegenkontoauszug fehlt",
                        "환불 상대 명세서 필요": "Auszug zur Erstattung fehlt",
                        "전액 환불 확인": "Vollständig erstattet · Belegprüfung offen",
                    }.get(status, status.replace("연결됨 · 차액", "Differenz").replace("일부 환불 · 잔액", "Teilerstattung · Restbetrag"))
                    row = [transaction_id, record["period"], item.day, export_value(self.accounts.get(key, "")),
                           export_value(item.merchant), item.amount, item.currency, german_status,
                           " | ".join(paths), export_value(delivery_text(record.get("advisor_note", ""))), record.get("due", ""),
                           " | ".join(self.record(other).get("number", "") for other in record.get("refund_group", []) if other != key),
                           FOLLOW_UP.get(record.get("follow_up", ""), ""),
                           NO_EVIDENCE_REASONS.get(record.get("statement_evidence", ""), "")]
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
                    if record.get("follow_up") in ("요청 중", "세무사 확인 필요", "증빙 확보 불가") or status not in ("금액 대조 완료", "내부이체 확인", "전액 환불 확인"):
                        unmatched_writer.writerow(row)
                errors = io.StringIO(newline="")
                error_writer = csv.writer(errors, delimiter=";")
                error_writer.writerow(["Quelle/Zeile", "Importhinweis"])
                for location, error in self.errors if baselines is None else []:
                    error_writer.writerow([export_value(location), german_import_error(error)])
                for name in self.read_issues if baselines is None else []:
                    error_writer.writerow([export_value(name), "Beleg konnte nicht vollständig gelesen werden"])
                tables = {name: localized_csv(stream.getvalue()) for name, stream in (
                    ("01_Abgleich.csv", overview), ("02_Belegdetails.csv", details),
                    ("03_Offene_Posten.csv", unmatched), ("04_Importfehler.csv", errors),
                    ("05_Kontoauszuege.csv", source_list), ("06_Nachreichungen.csv", changes),
                    ("07_Bewirtung.csv", hospitality), ("08_Betragszuordnung.csv", allocation),
                    ("09_Rechnungskorrekturen.csv", corrections), ("10_Anlagen.csv", attachment_list),
                )}
                for name, contents in tables.items():
                    archive.writestr(name, contents.encode("utf-8-sig"))
                archive.writestr("Abgleich.xlsx", advisor_workbook(tables, set(archive.namelist())))
                archive.writestr("Hinweise.txt", (
                    ("Nachreichung: Enthält ausgewählte Änderungen und gegebenenfalls neue Belege. Frühere Auszüge gelten weiter.\n" if baselines is not None else "") +
                    "Die Ordner enthalten Kopien der importierten Auszüge und der zugeordneten Belege.\n"
                    "Die Belegnummer verweist auf die zugehörige Buchung. Derselbe Beleg kann mehreren Buchungen zugeordnet sein.\n"
                    "Ein Abgleich bestätigt nur die Zuordnung und den Betragsvergleich. Steuerhinweise und PDF-Extrakte bitte prüfen.\n"
                    "Bei PDF-Auszügen Anzahl, Vorzeichen und Vollständigkeit anhand der Originalauszüge prüfen.\n"
                ))
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def package_filename(transaction_id, doc):
    original = original_stem(doc.path)
    reference = number_label(doc.number, original).replace("__번호_", "_Nr_")
    if doc.amount is None or not doc.day or not doc.seller:
        label = original + reference
    else:
        currency = f" {doc.currency}" if doc.currency and doc.currency != "EUR" else ""
        label = f"{doc.amount}{currency}_{doc.day:%y-%m-%d}_{doc.seller}{reference}_{original}"
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", label).strip(" .")
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
