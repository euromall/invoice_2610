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
    fields["special"] = "Reverse Charge" if re.search(r"reverse charge|steuerschuldnerschaft des leistungsempfängers", text, re.I) else ""
    # Amazon marketplace invoice: paid to Amazon, issued by the seller (‘Verkauft von …’ / Amazon order number).
    if re.search(r"(?i)\bamazon\b", text) and (re.search(r"(?i)verkauft von|sold by|\b\d{3}-\d{7}-\d{7}\b", text)):
        fields["amazon"] = True
    return fields
