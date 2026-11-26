"""Checks for the v1.1.1 fixes. Runs without a screen, using synthetic data only."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf


APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from invoice_renamer import (  # noqa: E402
    EditsFileError, invoice_fields, load_invoice_edits, proposed_name, read_invoice_details,
)
from monthly_close import decode_statement, german_import_error, index_pdfs  # noqa: E402


UBL = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>XR-2026-0042</cbc:ID>
 <cbc:IssueDate>2026-11-20</cbc:IssueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyLegalEntity>
  <cbc:RegistrationName>Muster Lieferant GmbH</cbc:RegistrationName>
 </cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
 <cac:LegalMonetaryTotal>
  <cbc:TaxExclusiveAmount currencyID="EUR">100.00</cbc:TaxExclusiveAmount>
  <cbc:TaxInclusiveAmount currencyID="EUR">119.00</cbc:TaxInclusiveAmount>
 </cac:LegalMonetaryTotal>
</Invoice>
"""

CII = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
 xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
 xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
 <rsm:ExchangedDocument><ram:ID>ZF-77</ram:ID><ram:TypeCode>380</ram:TypeCode>
  <ram:IssueDateTime><udt:DateTimeString format="102">20261118</udt:DateTimeString></ram:IssueDateTime>
 </rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>
  <ram:ApplicableHeaderTradeAgreement><ram:SellerTradeParty><ram:Name>Beispiel AG</ram:Name></ram:SellerTradeParty></ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeSettlement><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
   <ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:GrandTotalAmount>23.80</ram:GrandTotalAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation>
  </ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


def check(condition, description):
    if not condition:
        raise AssertionError(description)


def run():
    # 1. Total labels: new labels work, and older labels still win when both appear.
    check(invoice_fields(["Gesamtbetrag 58,39 EUR"])[2] == "58.39 EUR", "Gesamtbetrag")
    check(invoice_fields(["Zahlbetrag: 1.234,56 €"])[2] == "1234.56 EUR", "Zahlbetrag")
    check(invoice_fields(["Summe 50,00 EUR\nBruttobetrag 59,50 EUR"])[2] == "59.50 EUR", "Primary label priority")
    check(invoice_fields(["Summe 50,00 EUR\nGesamtbetrag 59,50 EUR"])[2] == "", "Ambiguous fallback stays unresolved")

    # 2. German import error keeps the amounts.
    message = german_import_error("검산 불일치 · 계산 잔액 100.00 / 원본 120.50 EUR")
    check("100.00" in message and "120.50" in message, "Import error amounts")

    # 3. Statement encodings.
    check(decode_statement("Datum;Beschreibung\n01.02.2026;Änderungsgebühr\n".encode("cp1252")).endswith("Änderungsgebühr\n"), "cp1252")
    check(decode_statement("Datum;Beschreibung\n01.02.2026;Änderung\n".encode("cp1252")).endswith("Änderung\n"), "cp1252 that is also valid cp949")
    check("가맹점" in decode_statement("거래일,가맹점,금액\n2026-11-01,카페,4500\n".encode("cp949")), "cp949")
    check("Straße" in decode_statement("﻿Straße".encode("utf-8")), "UTF-8 BOM")
    legacy = "Datum;Beschreibung\n01.02.2026;Änderung\n".encode("cp1252")
    check(decode_statement(legacy, legacy=True) == legacy.decode("cp949"), "Already imported files keep the old reading")

    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
        root = Path(scratch)

        # 4. XRechnung XML in the renaming screen.
        xml = root / "rechnung.xml"
        xml.write_text(UBL, encoding="utf-8")
        fields, note, number, _ = read_invoice_details(xml)
        check(fields == ("2026-11-20", "Muster Lieferant GmbH", "119.00 EUR") and number == "XR-2026-0042" and not note, "XRechnung fields")
        check(proposed_name(fields, xml.name, number).endswith(".xml"), "XML keeps its extension")

        # 5. ZUGFeRD PDF: embedded XML wins over the visible text.
        pdf = root / "zugferd.pdf"
        with pymupdf.open() as document:
            document.new_page().insert_text((60, 80), "Rechnung ZF-77  Rechnungsbetrag 99,99 EUR")
            document.embfile_add("factur-x.xml", CII.encode("utf-8"), filename="factur-x.xml")
            document.save(pdf)
        fields, _, number, _ = read_invoice_details(pdf)
        check(fields == ("2026-11-18", "Beispiel AG", "23.80 EUR") and number == "ZF-77", "ZUGFeRD embedded XML")
        documents, issues = index_pdfs(root)
        check(not issues and {doc.path.name for doc in documents} == {"rechnung.xml", "zugferd.pdf"}, "Monthly close still reads both")

        # 6. Extra header sellers without code changes.
        folder = root / "InvoiceRenamer"
        folder.mkdir()
        (folder / "seller-patterns.json").write_text(json.dumps({r"^\s*Kopfzeile Handel\b": "Kopfzeile Handel"}), encoding="utf-8")
        check(invoice_fields(["Kopfzeile Handel\nRechnungsbetrag 10,00 EUR"])[1] == "Kopfzeile Handel", "seller-patterns.json")
        check(invoice_fields(["greenist GmbH\nRechnungsbetrag 10,00 EUR"])[1] == "greenist GmbH", "Built-in header seller kept")

        # 7. A damaged edits file is reported as such.
        (folder / "invoice-edits.json").write_text("{broken", encoding="utf-8")
        try:
            load_invoice_edits()
        except EditsFileError:
            pass
        else:
            raise AssertionError("Damaged edits file")
    print("PASS: v1.1.1 total labels, import errors, encodings, XRechnung/ZUGFeRD, seller patterns, edits file")


if __name__ == "__main__":
    run()
