import re
from datetime import date
from xml.etree import ElementTree

import pymupdf


def structured_invoice(data):
    root = ElementTree.fromstring(data)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    kind = root.tag
    if kind not in ("Invoice", "CreditNote", "CrossIndustryInvoice"):
        return {}
    if kind == "CrossIndustryInvoice":
        trade = "SupplyChainTradeTransaction/"
        agreement = trade + "ApplicableHeaderTradeAgreement/"
        settlement = trade + "ApplicableHeaderTradeSettlement/"
        sums = settlement + "SpecifiedTradeSettlementHeaderMonetarySummation/"
        paths = {
            "number": "ExchangedDocument/ID", "day": "ExchangedDocument/IssueDateTime/DateTimeString",
            "seller": agreement + "SellerTradeParty/Name", "buyer": agreement + "BuyerTradeParty/Name",
            "tax_id": agreement + "SellerTradeParty/SpecifiedTaxRegistration/ID",
            "delivery": trade + "ApplicableHeaderTradeDelivery/ActualDeliverySupplyChainEvent/OccurrenceDateTime/DateTimeString",
            "currency": settlement + "InvoiceCurrencyCode", "amount": sums + "GrandTotalAmount",
            "net": sums + "TaxBasisTotalAmount", "tax": sums + "TaxTotalAmount",
            "reference": settlement + "InvoiceReferencedDocument/IssuerAssignedID",
            "order": agreement + "BuyerOrderReferencedDocument/IssuerAssignedID",
        }
        tax_path = settlement + "ApplicableTradeTax"
        rate, base, tax = "RateApplicablePercent", "BasisAmount", "CalculatedAmount"
        credit = root.findtext("ExchangedDocument/TypeCode") == "381"
    else:
        seller = "AccountingSupplierParty/Party/"
        paths = {
            "number": "ID", "day": "IssueDate", "seller": seller + "PartyLegalEntity/RegistrationName",
            "buyer": "AccountingCustomerParty/Party/PartyLegalEntity/RegistrationName",
            "tax_id": seller + "PartyTaxScheme/CompanyID", "delivery": "Delivery/ActualDeliveryDate",
            "currency": "DocumentCurrencyCode", "amount": "LegalMonetaryTotal/TaxInclusiveAmount",
            "net": "LegalMonetaryTotal/TaxExclusiveAmount", "tax": "TaxTotal/TaxAmount",
            "reference": "BillingReference/InvoiceDocumentReference/ID",
            "order": "OrderReference/ID",
        }
        tax_path = "TaxTotal/TaxSubtotal"
        rate, base, tax = "TaxCategory/Percent", "TaxableAmount", "TaxAmount"
        credit = kind == "CreditNote" or root.findtext("InvoiceTypeCode") == "381"
    out = {key: (root.findtext(path) or "").strip() for key, path in paths.items()}
    if not out["seller"] and kind != "CrossIndustryInvoice":
        out["seller"] = (root.findtext(seller + "PartyName/Name") or "").strip()
    for key in ("day", "delivery"):
        value = out[key]
        if re.fullmatch(r"\d{8}", value):
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        try:
            out[key] = date.fromisoformat(value).isoformat()
        except ValueError:
            out[key] = ""
    out["rates"] = " | ".join(
        f'{node.findtext(rate, "?")}%: {node.findtext(base, "?")} / {node.findtext(tax, "?")}'
        for node in root.findall(tax_path)
    )
    out["credit"] = credit
    out["structured"] = True
    categories = {node.findtext("CategoryCode") or node.findtext("TaxCategory/ID") for node in root.findall(tax_path)}
    out["special"] = "Reverse Charge prüfen" if "AE" in categories else ""
    return out


def read_structured(path):
    if path.suffix.lower() == ".xml":
        return structured_invoice(path.read_bytes())
    with pymupdf.open(path) as document:
        for name in document.embfile_names():
            info = document.embfile_info(name)
            if any(str(item).lower().endswith(".xml") for item in (name, info.get("filename"), info.get("ufilename"))):
                try:
                    result = structured_invoice(document.embfile_get(name))
                except ElementTree.ParseError:
                    continue
                if result:
                    return result
    return {}


# v1.14: tax checks before the ZIP goes out (§ 14 UStG items, reverse charge, foreign sellers)
REVERSE_CHARGE = (r"(?i)reverse[- ]charge|steuerschuldnerschaft des leistungsempfängers|§\s*13b\s*UStG|"
                  r"art(?:icle|\.)?\s*196|autoliquidation|inversione contabile|omvendt betalingspligt|"
                  r"verlegd(?:e)? btw|inversión del sujeto pasivo|odwrotne obciążenie")
INTRA_EU = r"(?i)innergemeinschaftliche[rn]? (?:lieferung|leistung|erwerb)|intra-community (?:supply|acquisition)|§\s*4\s*Nr\.?\s*1\s*b"
VAT_COUNTRIES = "AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK|XI|GB|CH|NO"
VAT_LABEL = r"(?:USt[ .-]*Id[ .-]*Nr\.?|USt[ .-]*IdNr|UID(?:-Nr\.?)?|VAT(?: Reg(?:istration)?\.?)?(?: No\.?| Number| ID| Nr\.?)?|TVA|P\.?\s?IVA|IVA|BTW|NIF|N° TVA|MwSt[ .-]*Nr\.?)"


def vat_ids(text):
    """VAT IDs printed in the document (‘DE123456789’, ‘FR 12 345678901’), labelled ones and bare German ones."""
    found = []
    for match in re.finditer(rf"(?i){VAT_LABEL}[^\n]{{0,25}}?\b({VAT_COUNTRIES})[ ]?((?:[0-9A-Z][ ]?){{8,12}})\b", text):
        found.append(match[1].upper() + re.sub(r"\s", "", match[2]).upper())
    found.extend(re.findall(r"\b(DE\d{9})\b", text))
    return list(dict.fromkeys(vat for vat in found if re.search(r"\d{6}", vat)))


def tax_notes(meta, amount, currency, credit=False):
    """[(Korean, German)] tax remarks for one invoice. Facts only; the advisor decides."""
    meta = meta or {}
    notes = []
    special = meta.get("special")
    if special == "Reverse Charge":
        notes.append(("역과세(Reverse Charge) 표시 · 세액은 우리 쪽에서 신고",
                      "Reverse Charge – Steuerschuld des Leistungsempfängers (§ 13b UStG) prüfen"))
    elif special == "Innergemeinschaftlich":
        notes.append(("EU 역내 거래(세금 면제) 표시", "Innergemeinschaftlicher Erwerb – Erwerbsbesteuerung prüfen"))
    if meta.get("foreign"):
        notes.append((f"해외 판매자 세금번호({meta['foreign']})",
                      f"Ausländischer Lieferant (USt-IdNr. {meta['foreign']}) – umsatzsteuerliche Behandlung prüfen"))
    if currency and currency != "EUR":
        notes.append((f"외화 인보이스({currency})", f"Fremdwährung {currency}"))
    small = currency == "EUR" and amount is not None and 0 < amount <= 250
    if not special and not meta.get("tax") and not credit:
        notes.append(("세액(MwSt) 표시 못 찾음 · 매입세액 공제 확인", "Keine Umsatzsteuer erkannt – Vorsteuerabzug prüfen"))
    if not small and not meta.get("tax_id") and not meta.get("vat_ids"):
        notes.append(("판매자 세금번호(Steuernummer/USt-IdNr.) 못 찾음", "Steuernummer/USt-IdNr. des Lieferanten nicht erkannt"))
    return notes


def pdf_tax_fields(text):
    fields = {}
    patterns = {
        "net": r"(?:Gesamtpreis Netto|Gesamt[- ]?Netto|Nettobetrag|Subtotal)",
        "tax": r"(?:MwSt\.?(?:\s*\d{1,2}(?:[.,]\d+)?\s*%)?|Umsatzsteuer|VAT)",
        "tax_id": r"(?:USt[ .-]*Id[ .-]*Nr\.?|Steuernummer|VAT ID)",
        "delivery": r"(?:Lieferdatum|Leistungsdatum|Delivery Date)",
        "reference": r"(?:Bezug(?:srechnung)?|Originalrechnung|Original Invoice)",
    }
    for key, label in patterns.items():
        values = re.findall(rf"(?im)^[ \t]*{label}[ \t]*:?[ \t]+([^\n]+)", text)
        clean = {value.strip() for value in values}
        if len(clean) == 1:
            fields[key] = clean.pop()
    orders = set(re.findall(
        r"(?im)\b(?:Bestell(?:nummer|[- ]?Nr\.?)|Auftragsnummer|Order (?:Number|No\.?|#))"
        r"[ \t]*[:#]?[ \t]*(?:\n[ \t]*)?([\w.-]*\d[\w./-]*)\b", text
    ))
    if len(orders) == 1:
        fields["order"] = orders.pop()
    fields["special"] = "Reverse Charge" if re.search(REVERSE_CHARGE, text) else ""
    if not fields["special"] and re.search(INTRA_EU, text):
        fields["special"] = "Innergemeinschaftlich"
    ids = vat_ids(text)
    if ids:
        fields["vat_ids"] = ids
        foreign = sorted({vat[:2] for vat in ids if not vat.startswith("DE")})
        if foreign:
            fields["foreign"] = ", ".join(foreign)
    # Amazon marketplace invoice: paid to Amazon, issued by the seller (‘Verkauft von …’ / Amazon order number).
    if re.search(r"(?i)\bamazon\b", text) and (re.search(r"(?i)verkauft von|sold by|\b\d{3}-\d{7}-\d{7}\b", text)):
        fields["amazon"] = True
    return fields
