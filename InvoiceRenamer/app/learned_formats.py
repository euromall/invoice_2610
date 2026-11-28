"""Per-seller invoice layouts learned from one manual correction.

When the user corrects an invoice in step 1, the words standing in front of the issue date, the total
and the invoice number are remembered together with a marker text (the seller name as printed).
Later invoices that contain the marker are read with those words, so each new seller needs to be
corrected only once. Stored in %LOCALAPPDATA%\\InvoiceRenamer\\learned-formats.json.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile

GERMAN_MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
                 "Oktober", "November", "Dezember")
ENGLISH_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
                  "October", "November", "December")
MONTH_NAMES = {name.casefold(): index for names in (GERMAN_MONTHS, ENGLISH_MONTHS)
               for index, name in enumerate(names, 1)}
MONTH_NAMES.update({name[:3].casefold(): index for names in (GERMAN_MONTHS, ENGLISH_MONTHS)
                    for index, name in enumerate(names, 1)})
MONTH_NAMES["mär"] = MONTH_NAMES["maerz"] = 3
MONTH_WORD = r"[A-Za-zÄÖÜäöü]{3,9}\.?"
DATE_TOKEN = (rf"\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[./]\d{{1,2}}[./]\d{{2,4}}|"
              rf"\d{{1,2}}\.?\s+{MONTH_WORD}\s+\d{{4}}|{MONTH_WORD}\s+\d{{1,2}},?\s+\d{{4}}")
AMOUNT_TOKEN = r"-?\d{1,3}(?:[.,'  ]\d{3})*[.,]\d{2}|-?\d+[.,]\d{2}"
CURRENCIES = {"€": "EUR", "EUR": "EUR", "$": "USD", "USD": "USD", "£": "GBP", "GBP": "GBP", "CHF": "CHF"}


def storage_path():
    # v1.14: shared by every company profile (INVOICE_RENAMER_BASE is the real LOCALAPPDATA while another company is active)
    # v1.15: in the shared records folder while the records are used on several computers
    base = (os.environ.get("INVOICE_RENAMER_LEARNED") or os.environ.get("INVOICE_RENAMER_BASE")
            or os.environ.get("LOCALAPPDATA", Path.home()))
    return Path(base) / "InvoiceRenamer" / "learned-formats.json"


def load_formats():
    path = storage_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("marker")]


def save_formats(formats):
    path = storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as output:
            temporary = Path(output.name)
            json.dump(formats, output, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_date(token):
    token = " ".join(token.replace(",", " ").split())
    numeric = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", token)
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", token):
            return date(*map(int, token.split("-"))).isoformat()
        if numeric:
            day, month, year = map(int, numeric.groups())
            return date(year + 2000 if year < 100 else year, month, day).isoformat()
        named = re.fullmatch(r"(\d{1,2})\.?\s+(\S+?)\.?\s+(\d{4})", token)
        if named and named[2].casefold() in MONTH_NAMES:
            return date(int(named[3]), MONTH_NAMES[named[2].casefold()], int(named[1])).isoformat()
        named = re.fullmatch(r"(\S+?)\.?\s+(\d{1,2})\s+(\d{4})", token)
        if named and named[1].casefold() in MONTH_NAMES:
            return date(int(named[3]), MONTH_NAMES[named[1].casefold()], int(named[2])).isoformat()
    except ValueError:
        return ""
    return ""


def parse_amount(token):
    raw = re.sub(r"[\s' ]", "", token)
    cents = re.search(r"[.,](\d{2})$", raw)
    if not cents:
        return None
    whole = re.sub(r"[^\d-]", "", raw[:cents.start()])
    try:
        return Decimal(f"{whole}.{cents[1]}")
    except InvalidOperation:
        return None


def date_spellings(value):
    day = date.fromisoformat(value)
    return {day.strftime("%d.%m.%Y"), f"{day.day}.{day.month}.{day.year}", day.strftime("%d/%m/%Y"),
            day.isoformat(), day.strftime("%d.%m.%y"),
            f"{day.day}. {GERMAN_MONTHS[day.month - 1]} {day.year}", f"{day.day} {GERMAN_MONTHS[day.month - 1]} {day.year}",
            f"{day.strftime('%d')}. {GERMAN_MONTHS[day.month - 1]} {day.year}",
            f"{ENGLISH_MONTHS[day.month - 1]} {day.day}, {day.year}", f"{day.day} {ENGLISH_MONTHS[day.month - 1]} {day.year}"}


def amount_spellings(amount):
    plain = f"{amount:.2f}"
    grouped = f"{amount:,.2f}"
    return {plain.replace(".", ","), plain, grouped, grouped.replace(",", "_").replace(".", ",").replace("_", ".")}


def label_before(text, start):
    """Words in front of a value: same line if there are any, else the previous non-empty line."""
    line_start = text.rfind("\n", 0, start) + 1
    label = text[line_start:start]
    if not re.search(r"[^\W\d_]", label):
        previous = text[:line_start].rstrip("\n").splitlines()
        label = next((line for line in reversed(previous) if line.strip()), "")
    label = re.sub(r"[\s:：#€$£-]+$", "", label)
    words = re.findall(r"[^\W\d_][\w./()-]*", label)
    return " ".join(words[-3:])


FIELD_WORDS = {
    "date": ("datum", "date", "rechnungsdatum", "belegdatum", "ausgestellt", "invoice date", "issued", "vom"),
    "total": ("summe", "gesamt", "total", "betrag", "brutto", "zahlbetrag", "endbetrag", "zu zahlen", "rechnungsbetrag",
              "amount", "zahlung"),
    "number": ("nr", "nummer", "no", "number", "rechnung", "invoice", "beleg", "id"),
}
BAD_WORDS = ("iban", "bic", "sparkasse", "bank", "ust", "steuer", "telefon", "fax", "kunden", "amtsgericht", "hrb",
             "geschäftsführer", "seite", "page")


def label_score(label, kind):
    """Higher is better: a label that names the field beats one from the footer (bank, tax office, ...)."""
    words = label.casefold()
    score = 2 * sum(1 for word in FIELD_WORDS.get(kind, ()) if word in words)
    if "|" in label or any(word in words for word in BAD_WORDS):
        score -= 3
    if kind == "number" and "kunden" in words:
        score -= 3
    return score


def find_label(text, spellings, last=False, kind="", check=None):
    """The words in front of the value. With kind/check: every place the value appears is tried, labels that name
    the field are preferred, and a label is only taken when reading the text with it gives the value back."""
    hits = []
    for spelling in spellings:
        for match in re.finditer(rf"(?<![\d.,]){re.escape(spelling)}(?![\d])", text):
            label = label_before(text, match.start())
            if label:
                hits.append((match.start(), label))
    if not hits:
        return ""
    hits.sort()
    if not kind:
        return hits[-1][1] if last else hits[0][1]
    ordered = sorted(hits, key=lambda hit: (-label_score(hit[1], kind), -hit[0] if last else hit[0]))
    for _, label in ordered:
        if label_score(label, kind) < 0:
            continue
        if check is None or check(label):
            return label
    return ""


def pick_marker(text, seller):
    folded = text.casefold()
    if seller and seller.casefold() in folded:
        return seller
    for word in sorted(re.findall(r"[^\W\d_]{4,}", seller or ""), key=len, reverse=True):
        if word.casefold() in folded and word.casefold() not in ("gmbh", "rechnung", "invoice"):
            return word
    return ""


def learn(text, filename, fields, number, anchors=None, save=True):
    """Remember the layout of one corrected invoice. Returns the stored entry or None (and why).

    anchors: {"date"|"total"|"number": label} — words next to the box the user dragged on the page; tried first.
    save=False only works out the entry (to show it for confirmation); store it with remember(entry)."""
    issued, seller, total = fields
    marker = pick_marker(text, seller)
    if not marker:
        return None, "업체명이 PDF 안에 그대로 적혀 있지 않아 양식을 배우지 못했어요. PDF에 보이는 업체명으로 적어 주세요."
    anchors = anchors or {}
    labels = {}
    amount_text, _, currency = total.partition(" ")
    amount = parse_amount(amount_text) if amount_text else None

    def gives_date(label):
        return parse_date(value_after(text, label, DATE_TOKEN)) == issued

    def gives_total(label):
        token = value_after(text, label, AMOUNT_TOKEN)
        found = parse_amount(token) if token else None
        return found is not None and abs(found) == abs(amount)

    def gives_number(label):
        return value_after(text, label, r"[\w/-]*\d[\w/-]*") == number

    checks = {"date": gives_date, "total": gives_total, "number": gives_number}
    wanted = {"date": issued and date_spellings(issued), "total": amount is not None and amount_spellings(abs(amount)),
              "number": number and {number}}
    for kind, spellings in wanted.items():
        if not spellings:
            continue
        anchor = anchors.get(kind, "")
        if anchor and checks[kind](anchor):
            labels[kind] = anchor
            continue
        labels[kind] = find_label(text, spellings, last=kind == "total", kind=kind, check=checks[kind])
    labels = {key: value for key, value in labels.items() if value}
    if not labels:
        return None, "PDF 안에서 입력한 값 옆의 항목 이름을 확실히 찾지 못해 양식을 배우지 않았어요."
    entry = {"marker": marker, "seller": seller, "currency": currency or "EUR", "labels": labels,
             "learned_from": filename, "at": date.today().isoformat()}
    if save:
        remember(entry)
    return entry, ""


def remember(entry):
    formats = [item for item in load_formats() if item["marker"].casefold() != entry["marker"].casefold()]
    formats.append(entry)
    save_formats(formats)


def label_pattern(label):
    return r"[^\w\n]+".join(re.escape(word) for word in label.split())


def value_after(text, label, token):
    match = re.search(rf"(?i){label_pattern(label)}[^\S\n]*[:：#]?[^\S\n]*(?:[^\n]*?)(?:\n[^\S\n]*)?({token})", text)
    return match[1] if match else ""


def apply(text, fields, number):
    """Fill fields from a learned layout whose marker is in the text. Returns (fields, number, seller name used)."""
    folded = text.casefold()
    for entry in load_formats():
        if entry["marker"].casefold() not in folded:
            continue
        issued, seller, total = fields
        labels = entry.get("labels", {})
        if labels.get("date"):
            issued = parse_date(value_after(text, labels["date"], DATE_TOKEN)) or issued
        if labels.get("total"):
            token = value_after(text, labels["total"], AMOUNT_TOKEN)
            amount = parse_amount(token) if token else None
            if amount is not None:
                currency = entry.get("currency") or "EUR"
                total = f"{amount:.0f} {currency}" if currency == "KRW" else f"{amount:.2f} {currency}"
        if labels.get("number"):
            found = value_after(text, labels["number"], r"[\w/-]*\d[\w/-]*")
            number = found or number
        return (issued, entry.get("seller") or seller, total), number, entry["marker"]
    return fields, number, ""
