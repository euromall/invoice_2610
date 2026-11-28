"""Checks for the v1.1.1 and v1.2 fixes. Runs without a screen, using synthetic data only."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pymupdf


APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from invoice_renamer import (  # noqa: E402
    App, EditsFileError, duplicate_groups, free_name, import_evidence, work_folder, split_pdf, move_evidence, write_markdown, filename_fields, invoice_likeness, invoice_fields, load_invoice_edits, original_stem, proposed_name,
    read_invoice_details, remember_original_name, reserve_name,
)
from monthly_close import MonthlyClose, package_part, Transaction, decode_statement, german_import_error, index_pdfs, numbered_statement, read_statement_pdf  # noqa: E402


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
    check(invoice_fields(["Summe 50,00 EUR\nGesamtbetrag 59,50 EUR"])[2] == "59.50 EUR", "v1.6: Gesamtbetrag beats weak Summe")
    check(invoice_fields(["Zahlbetrag 10,00 EUR\nGesamtbetrag 12,00 EUR"])[2] == "", "Ambiguous fallback stays unresolved")
    check(invoice_fields(["USt. Gesamt\n5,59 €\nZahlbetrag\n34,99 €"])[2] == "34.99 EUR", "v1.6: VAT ‘Gesamt’ line ignored")
    # v1.6 common seller and date rules (synthetic text shaped like real invoices).
    check(invoice_fields(["Verkauft von Muster Shop GmbH\nRechnungsdatum\n/Lieferdatum 02 Juli 2026"])[:2] == ("2026-07-02", "Muster Shop GmbH"),
          "Marketplace seller and month-name date")
    check(invoice_fields(["Muster Energie GmbH, Postfach 11 07 61, 64222 Darmstadt\nKunde GmbH\n   Dienstag, 30. Mai 2023"])[:2]
          == ("2023-05-30", "Muster Energie GmbH"), "Sender line and weekday date")
    check(invoice_fields(["Beispiel Apotheke - Hauptstraße 16 - 95152 Selbitz"])[1] == "Beispiel Apotheke", "Sender line without legal form")
    check(invoice_fields(["Gesamtbetrag 1.122,04 Euro"])[2] == "1122.04 EUR", "Euro spelled out")

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

    # 3b. v1.2: invoice-likeness and required-field check in step 1.
    check(invoice_likeness("Rechnung Nr. 5\nNetto 10,00 Brutto 11,90 MwSt 19%") >= 3, "Invoice words")
    check(invoice_likeness("Urlaubsfoto am Strand mit Freunden und Familie") == 0, "Not an invoice")
    from types import SimpleNamespace
    probe = SimpleNamespace(invoice_like={Path("x.pdf"): False})
    row = lambda fields, note="", number="R1", path=Path("a.pdf"): App.row_check(probe, path, fields, note, number)
    check(row(("2026-11-25", "Shop", "58.39 EUR")) == ("", "ok"), "Complete row")
    check(row(("2026-11-25", "", "58.39 EUR"))[1] == "bad", "Missing seller is red")
    check(row(("2026-11-25", "", "580.00 EUR"), number="")[0] == "필수 누락: 업체명, 인보이스 번호", "All missing fields listed")
    check(row(("2026-11-25", "Shop", "58.39 EUR"), number="")[1] == "warn", "Small receipt without number is amber")
    check(row(("2026-11-25", "Shop", "580.00 EUR"), number="")[1] == "bad", "Large invoice without number is red")
    check(row(("2026-11-25", "Shop", "58.39 EUR"), path=Path("x.pdf"))[1] == "muted", "Not-invoice suspicion is gray")

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

        # 6. File names: same layout as the advisor ZIP, original name kept across renames.
        fields = ("2026-11-25", "greenist GmbH", "58.39 EUR")
        source = root / "invoice.pdf"
        source.write_bytes(b"%PDF-1.4 synthetic")
        name = proposed_name(fields, source, "R1044176")
        check(name == "58.39_26-11-25_greenist GmbH_Nr_R1044176_invoice.pdf", f"Name before matching: {name}")
        remember_original_name(source)
        renamed = root / name
        source.rename(renamed)
        check(original_stem(renamed) == "invoice", "Original name from the record")
        check(filename_fields(renamed) == fields, f"Fields from the new name: {filename_fields(renamed)}")
        check(proposed_name(fields, renamed, "R1044176") == name, "Renaming again keeps the same name")
        matched = root / f"[611-001]_{name}"
        renamed.rename(matched)
        check(proposed_name(fields, matched, "R1044176") == matched.name, "Transaction prefix kept")
        check(filename_fields(matched) == fields, "Fields from the matched name")
        legacy = root / "2026-11-25_58.39 EUR_greenist GmbH__번호_R1044176__원본__Rechnung R1.pdf"
        legacy.write_bytes(b"%PDF-1.4 legacy")
        check(proposed_name(fields, legacy, "R1044176") == "58.39_26-11-25_greenist GmbH_Nr_R1044176_Rechnung R1.pdf", "Legacy name converted")
        check(proposed_name(("2026-11-25", "Shop", "12.50 USD"), root / "a.pdf", "") == "12.50 USD_26-11-25_Shop_a.pdf", "Foreign currency")
        check(reserve_name(root, "x.pdf", root / "y.pdf", {"x.pdf"}).name == "x (2).pdf", "Duplicate number")

        # 6b. v1.2.2: PDF statement copy with transaction numbers.
        statement = root / "db.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            lines = ["Deutsche Bank AG", "Alter Saldo per 31.10.2026        1.000,00",
                     "25.11. 25.11.   GREENIST GMBH BERLIN                 - 58,39", "2026   2026     Kartenzahlung",
                     "27.11. 27.11.   DKB Kreditkarte Ausgleich            - 1.204,55", "2026   2026     Ueberweisung"]
            for row, line in enumerate(lines):
                page.insert_text((40, 60 + 14 * row), line, fontname="cour", fontsize=9)
            document.save(statement)
        original = statement.read_bytes()
        _, rows = read_statement_pdf(statement)
        from datetime import date as day
        from decimal import Decimal
        entries = [(f"611-{index:03d}", Transaction(str(index), day.fromisoformat(row[0]), row[1],
                    Decimal(row[2].replace(".", "").replace(",", ".")), "EUR")) for index, row in enumerate(rows, 1)]
        data, marked = numbered_statement(statement, entries)
        with pymupdf.open(stream=data) as document:
            text = document[0].get_text()
        check(marked == 2 and "[611-001]" in text and "[611-002]" in text, "Numbered PDF statement")
        check(statement.read_bytes() == original, "Original statement unchanged")
        with pymupdf.open(stream=data) as document:
            fills = [drawing.get("fill") for drawing in document[0].get_drawings() if drawing.get("fill")]
        check(not any(fill[0] > 0.9 and fill[1] > 0.8 and fill[2] < 0.6 for fill in fills), f"v1.9: no yellow band behind numbers {fills}")

        # 6b. v1.9: recurring rule (rent by standing order) is applied to the next statement, not to other payees.
        from monthly_close import apply_recurring, payee_key
        rent = "Dauerauftrag | Lager Vermietung GmbH | Miete 09/2026"
        check(payee_key(rent) == payee_key("Dauerauftrag | Lager Vermietung GmbH | Miete 10/2026"), "v1.9: payee key ignores reference")
        check(payee_key("Lastschrift Darlehen Bank AG Rate 3/24 MREF 123") == payee_key("Lastschrift Darlehen Bank AG Rate 4/24 MREF 124")
              and payee_key("Miete Lager A") != payee_key("Miete Lager B"), "v1.9: payee key ignores changing numbers")
        rule_state = {"records": {"linked": {"links": ["x"]}},
                      "recurring_rules": [{"payee": payee_key(rent), "amount": "-850.00", "currency": "EUR",
                                           "reason": "임대료/할부금/대출 상환/리스료 · 계약서 기제출"}]}
        october = [Transaction("oct", day(2026, 10, 1), rent.replace("09", "10"), Decimal("-850.00"), "EUR"),
                   Transaction("other", day(2026, 10, 1), "Dauerauftrag | Anderer Vermieter | Miete", Decimal("-850.00"), "EUR"),
                   Transaction("odd", day(2026, 10, 1), rent, Decimal("-900.00"), "EUR"),
                   Transaction("linked", day(2026, 10, 1), rent, Decimal("-850.00"), "EUR")]
        count = apply_recurring(rule_state, october)
        check(count == 1 and rule_state["records"]["oct"]["statement_evidence"].startswith("임대료")
              and "other" not in rule_state["records"] and "odd" not in rule_state["records"]
              and "statement_evidence" not in rule_state["records"]["linked"], f"v1.9: recurring rule applied {rule_state}")
        # 6c. v1.10: statement text of a transaction, merchant split, table sort values, reason list.
        from monthly_close import is_contract_reason, reason_german, reason_list, save_reason_list, split_merchant, statement_excerpt
        greenist = next(item for _, item in entries if "GREENIST" in item.merchant)
        page, rect, excerpt = statement_excerpt(statement, greenist)
        check(page == 0 and rect and "GREENIST" in excerpt[0] and any("Kartenzahlung" in line for line in excerpt),
              f"v1.10: statement excerpt from PDF {excerpt}")
        csv_statement = root / "excerpt.csv"
        csv_statement.write_text("Buchungstag;Empfänger;Verwendungszweck;Betrag\n01.10.2026;Lager GmbH;Miete 10/2026 Halle 3;-850,00\n",
                                 encoding="utf-8")
        _, _, row_text = statement_excerpt(csv_statement, Transaction("c", day(2026, 10, 1), "Lager GmbH", Decimal("-850.00"), "EUR"))
        check("Verwendungszweck: Miete 10/2026 Halle 3" in row_text, f"v1.10: statement excerpt from CSV {row_text}")
        check(split_merchant("SEPA Lastschrifteinzug von | AMAZON PAYMENTS EUROPE S.C.A.") == ("SEPA Lastschrifteinzug von", "AMAZON PAYMENTS EUROPE S.C.A.")
              and split_merchant("AMAZON EU   BADEN") == ("", "AMAZON EU   BADEN"), "v1.10: merchant split")
        from ui_theme import help_key, sort_value
        check(sorted(["-5176.50 EUR", "40.51 EUR", "", "9.46 EUR", "800.00 EUR"], key=sort_value)
              == ["-5176.50 EUR", "9.46 EUR", "40.51 EUR", "800.00 EUR", ""], "v1.10: amounts sort by value")
        check(help_key("파일 가져오기 (복사)") == "파일 가져오기" and help_key("연결 안 된 인보이스 (234)") == "연결 안 된 인보이스", "v1.10: help keys")
        reasons = reason_list({})
        rent_reason = next(entry for entry in reasons if entry["ko"].startswith("임대료"))
        check(rent_reason["contract"] and not next(entry for entry in reasons if entry["ko"] == "은행 수수료")["contract"],
              "v1.10: built-in contract reasons")
        rent_reason["de"] = "Lagermiete – Vertrag liegt vor"
        reasons.append({"ko": "창고 관리비 · 계약서 기제출", "de": "Lagernebenkosten – Vertrag liegt vor", "contract": True,
                        "hidden": False, "builtin": False})
        save_reason_list(reasons)
        check(reason_german(rent_reason["ko"]) == "Lagermiete – Vertrag liegt vor" and is_contract_reason("창고 관리비 · 계약서 기제출")
              and reason_german("창고 관리비 · 계약서 기제출") == "Lagernebenkosten – Vertrag liegt vor", "v1.10: reasons saved and used")

        # 6d. v1.11: final check warnings and stale checks.
        from types import SimpleNamespace
        from monthly_close import Invoice, review_signature, review_state
        invoice = Invoice("doc", root / "x.pdf", day(2026, 11, 20), "Muster Energie GmbH", Decimal("84.20"), "EUR", "R1", False)
        other = Invoice("doc2", root / "y.pdf", day(2026, 10, 1), "Ganz Anderer Laden", Decimal("50.00"), "EUR", "R2", False)
        review_records = {"t1": {"links": ["doc"], "number": "611-001"}, "t2": {"links": ["doc2"], "number": "611-002"},
                          "t3": {"links": ["doc2"], "number": "611-003"}, "t4": {"number": "611-004"}}
        stub = SimpleNamespace(
            transactions={"t1": Transaction("t1", day(2026, 11, 18), "SEPA Lastschrifteinzug von | Muster Energie GmbH", Decimal("-84.20"), "EUR"),
                          "t2": Transaction("t2", day(2026, 11, 18), "Kartenzahlung Buerobedarf", Decimal("-55.00"), "EUR"),
                          "t3": Transaction("t3", day(2026, 11, 18), "Kartenzahlung Buerobedarf", Decimal("-50.00"), "EUR"),
                          "t4": Transaction("t4", day(2026, 11, 18), "Miete", Decimal("-800.00"), "EUR")},
            state={"records": review_records, "aliases": {}}, by_key={"doc": invoice, "doc2": other})
        stub.record = lambda key: review_records[key]
        found = {key: MonthlyClose.final_checks(stub, key) for key in stub.transactions}
        check(found["t1"] == [] and "인보이스 없음" in found["t4"] and any(w.startswith("금액 차이") for w in found["t2"])
              and any("업체명 다름" in w for w in found["t2"]) and any("날짜" in w for w in found["t2"])
              and "같은 인보이스가 다른 거래에도 연결" in found["t3"], f"v1.11: final check warnings {found}")
        review_records["t1"]["links"] = ["doc"]
        checked_state = {"records": review_records, "final_review": {"t1": {"status": "ok", "signature": review_signature(review_records["t1"])}}}
        before = review_state(checked_state, "t1")
        review_records["t1"]["links"] = ["doc", "doc2"]
        check(before == "ok" and review_state(checked_state, "t1") == "changed" and review_state(checked_state, "t2") == "",
              "v1.11: a check goes stale when the links change")
        docs = [SimpleNamespace(key="x"), SimpleNamespace(key="y")]
        check([doc.key for doc in MonthlyClose.unmatched_documents(SimpleNamespace(state=rule_state, invoices=docs))] == ["y"],
              "v1.9: unmatched evidence list")

        # 7. Extra header sellers without code changes.
        folder = root / "InvoiceRenamer"
        folder.mkdir(exist_ok=True)
        (folder / "seller-patterns.json").write_text(json.dumps({r"^\s*Kopfzeile Handel\b": "Kopfzeile Handel"}), encoding="utf-8")
        check(invoice_fields(["Kopfzeile Handel\nRechnungsbetrag 10,00 EUR"])[1] == "Kopfzeile Handel", "seller-patterns.json")
        check(invoice_fields(["greenist GmbH\nRechnungsbetrag 10,00 EUR"])[1] == "greenist GmbH", "Built-in header seller kept")

        # 8. A damaged edits file is reported as such.
        (folder / "invoice-edits.json").write_text("{broken", encoding="utf-8")
        try:
            load_invoice_edits()
        except EditsFileError:
            pass
        else:
            raise AssertionError("Damaged edits file")

        # 9. v1.4: an imported statement is moved unchanged into 명세서/YYYY-MM of the evidence folder.
        evidence = root / "Belege-Ordner"
        (evidence / "명세서" / "2026-11").mkdir(parents=True)
        (evidence / "명세서" / "2026-11" / "Auszug.pdf").write_bytes(b"older file with the same name")
        downloaded = root / "Downloads"
        downloaded.mkdir()
        incoming = downloaded / "Auszug.pdf"
        incoming.write_bytes(original)

        class Status:
            def set(self, _text):
                pass

        close = MonthlyClose.__new__(MonthlyClose)
        close.folder, close.practice_folder, close.status = evidence, None, Status()
        from invoice_renamer import save_settings
        copy_source = downloaded / "Copy.pdf"
        copy_source.write_bytes(original)
        copied_spec, copied_from = close.file_statement({"path": str(copy_source), "period": "2026-11"}, [])
        check(copied_from is None and copy_source.exists() and copied_spec.get("copied")
              and Path(copied_spec["path"]).read_bytes() == original, "v1.8: statement copied by default, original kept")
        save_settings({"import_mode": "move"})
        spec, moved_from = close.file_statement({"path": str(incoming), "period": ""}, [entry for _, entry in entries])
        target = Path(spec["path"])
        check(moved_from == incoming and not incoming.exists(), "Statement moved out of the download folder")
        check(target == evidence / "명세서" / "2026-11" / "Auszug (2).pdf", "Statement month folder without overwrite")
        check(target.read_bytes() == original and spec["original_path"] == str(incoming), "Moved statement unchanged")
        check(close.file_statement(spec, [])[1] is None, "Statement already filed stays put")
        close.practice_folder = root
        practice_file = downloaded / "Practice.pdf"
        practice_file.write_bytes(original)
        check(close.file_statement({"path": str(practice_file), "period": "2026-11"}, [])[1] is None, "Practice mode does not move")
        (folder / "invoice-edits.json").write_text("{}", encoding="utf-8")
        check(not any("명세서" in document.path.parts for document in index_pdfs(evidence)[0]), "Statement folder not indexed as evidence")
        save_settings({"import_mode": "copy"})

        # 10. v1.4.1: duplicate invoices by content or by seller/date/total/number.
        base = (("2026-11-25", "greenist GmbH", "58.39 EUR"), "R1044176")
        entries = [
            ("a", Path("in/scan.pdf"), *base, "h1"),
            ("b", Path("매칭완료_2611/[611-001]_x.pdf"), *base, "h2"),
            ("c", Path("in/copy.pdf"), ("2026-11-25", "Greenist GmbH", "58,39 EUR"), "R 1044176", "h3"),
            ("d", Path("in/other.pdf"), ("2026-11-25", "greenist GmbH", "58.39 EUR"), "R1044177", "h4"),
            ("e", Path("in/same-bytes.pdf"), ("", "", ""), "", "h4"),
            ("f", Path("in/no-number.pdf"), ("2026-11-25", "greenist GmbH", "58.39 EUR"), "", "h6"),
        ]
        groups = sorted(([entry[0] for entry in group] for group in duplicate_groups(entries)), key=len)
        check(groups == [["d", "e"], ["b", "c", "a"]], f"Duplicate groups, matched file kept first: {groups}")

        # 11. v1.5: Markdown summary follows its PDF; the program's own summary is refreshed, not duplicated.
        moving = root / "moving"
        (moving / "sub").mkdir(parents=True)
        pdf = moving / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        write_markdown(pdf, "# 인보이스\n\n- PDF 파일명: a.pdf\n")
        write_markdown(pdf, "# 인보이스\n\n- PDF 파일명: a.pdf\n- 업체명: X\n")
        check(sorted(path.name for path in moving.iterdir() if path.is_file()) == ["a.md", "a.pdf"], "Markdown refreshed in place")
        (moving / "other.md").write_text("my own notes", encoding="utf-8")
        target = moving / "sub" / "[611-001]_a.pdf"
        move_evidence(pdf, target)
        moved_md = target.with_suffix(".md")
        check(moved_md.exists() and not (moving / "a.md").exists(), "Markdown moved with the PDF")
        check("- PDF 파일명: \\[611-001\\]\\_a.pdf" in moved_md.read_text(encoding="utf-8") and "업체명: X" in moved_md.read_text(encoding="utf-8"),
              "Markdown file name line updated")
        check(write_markdown(moving / "other.pdf", "# 인보이스\n").name == "other_2.md", "User Markdown never overwritten")
        check(package_part("Kontoauszuege/01_x_nummeriert.pdf") == "nummeriert" and package_part("Belege/2026-11/a.pdf") == "Belege"
              and package_part("03_Offene_Posten.csv") == "03_Offene_Posten.csv", "ZIP part names")

        # 12. v1.6: a seller layout learned from one correction reads the next invoice of that seller.
        import learned_formats
        with patch.dict("os.environ", {"LOCALAPPDATA": str(root / "appdata")}):
            first = ("Amazon EU S.a.r.l.\nTax Invoice\nBestelldatum 02.11.2026\nRechnungsdatum / Lieferdatum\n05.11.2026\n"
                     "Rechnungsnummer DE6AB12CD3EF\nZwischensumme 10,00 €\nZahlbetrag 11,90 €\n")
            entry, reason = learned_formats.learn(first, "a.pdf", ("2026-11-05", "Amazon EU S.a.r.l.", "11.90 EUR"), "DE6AB12CD3EF")
            check(entry and not reason, f"Layout learned: {reason}")
            check(entry["labels"] == {"date": "Rechnungsdatum Lieferdatum", "total": "Zahlbetrag", "number": "Rechnungsnummer"},
                  f"Learned labels: {entry['labels']}")
            second = ("Amazon EU S.a.r.l.\nTax Invoice\nBestelldatum 20.10.2026\nRechnungsdatum / Lieferdatum\n24.10.2026\n"
                      "Rechnungsnummer DE9XY88ZZ1\nZwischensumme 100,00 €\nZahlbetrag 1.234,50 €\n")
            fields, number, marker = learned_formats.apply(second, ("", "", ""), "")
            check(fields == ("2026-10-24", "Amazon EU S.a.r.l.", "1234.50 EUR") and number == "DE9XY88ZZ1" and marker,
                  f"Learned layout applied: {fields} {number} {marker}")
            check(learned_formats.apply("Other Shop GmbH\nDatum 01.01.2026", ("", "", ""), "")[2] == "", "Other sellers untouched")
            check(learned_formats.learn("Shop\n", "b.pdf", ("2026-01-01", "Nowhere AG", ""), "")[0] is None, "Seller must be printed")
            check(learned_formats.parse_date("5. März 2026") == "2026-03-05" and learned_formats.parse_date("March 5, 2026") == "2026-03-05",
                  "Month-name dates")

        # 13. v1.7: picked files are moved into the fixed work folder without overwriting; undo data is kept.
        with patch.dict("os.environ", {"LOCALAPPDATA": str(root / "appdata2")}):
            check(work_folder().name == "월마감", "Default work folder")
            inbox, desk = root / "work13", root / "desk13"
            (desk / "sub").mkdir(parents=True)
            inbox.mkdir()
            (inbox / "a.pdf").write_bytes(b"%PDF-1.4 old")
            (desk / "a.pdf").write_bytes(b"%PDF-1.4 new")
            (desk / "sub" / "a.pdf").write_bytes(b"%PDF-1.4 newer")
            (desk / "note.txt").write_text("x")
            history, inside, errors = import_evidence([desk / "a.pdf", desk / "sub" / "a.pdf", desk / "note.txt", inbox / "a.pdf"], inbox)
            check(sorted(path.name for path in inbox.iterdir()) == ["a (2).pdf", "a (3).pdf", "a.pdf"] and inside == 1 and not errors,
                  f"Import without overwrite: {sorted(path.name for path in inbox.iterdir())} {inside} {errors}")
            check((inbox / "a.pdf").read_bytes() == b"%PDF-1.4 old" and (desk / "note.txt").exists() and len(history) == 2,
                  "Existing file kept, non-evidence left behind")
            check(free_name(inbox, "b.pdf").name == "b.pdf", "Free name")
            (desk / "c.pdf").write_bytes(b"%PDF-1.4 copy me")
            copied, _, errors = import_evidence([desk / "c.pdf"], inbox, copy=True)
            check((desk / "c.pdf").exists() and (inbox / "c.pdf").read_bytes() == b"%PDF-1.4 copy me" and len(copied) == 1 and not errors,
                  "v1.8: copy keeps the original")
            from invoice_renamer import import_mode, save_settings
            check(import_mode() == "copy", "Copy is the default import mode")
            save_settings({"import_mode": "move"})
            check(import_mode() == "move", "Import mode saved")

        # 14. v1.7.1: one PDF with two marketplace invoices is detected and split; the original goes to 제외.
        combined = root / "combo" / "order.pdf"
        combined.parent.mkdir()
        with pymupdf.open() as document:
            for text in ("Rechnung\nSeite 1 von 1\nVerkauft von Shop Eins GmbH\nRechnungsnummer DE1AAA1\nZahlbetrag 18,99 EUR",
                         "Rechnung\nSeite 1 von 2\nVerkauft von Shop Zwei GmbH\nRechnungsnummer DE2BBB2\nZahlbetrag 11,00 EUR",
                         "Rechnung\nRechnungsnummer DE2BBB2\nSeite 2 von 2"):
                document.new_page().insert_text((60, 80), text)
            document.save(combined)
        info = {}
        fields, note, number, _ = read_invoice_details(combined, info)
        check(info.get("parts") == [(0, 0), (1, 2)] and "2개" in note and not fields[1], f"Combined PDF detected: {info} {note}")
        parts = split_pdf(combined, info["parts"])
        check([read_invoice_details(path)[0][1:] for path in parts] == [("Shop Eins GmbH", "18.99 EUR"), ("Shop Zwei GmbH", "11.00 EUR")]
              and not combined.exists() and (combined.parent / "제외" / "나누기 원본" / "order__나누기전_원본.pdf").exists(), "Combined PDF split")

        # 15. v1.12: amount input, picking values, mail search, account order, Amazon sellers, print bundle, refunds.
        from datetime import date as day
        from decimal import Decimal
        from types import SimpleNamespace
        from invoice_renamer import money_text, pick_value
        from monthly_close import (EARLIER_REFUND, PARTIAL_REFUND_DONE, amazon_marketplace, mail_search_url, ordered_accounts,
                                   stamp_page, Invoice)
        check(money_text("7,87") == "7.87 EUR" and money_text("7.87", "USD") == "7.87 USD" and money_text("1.234,56") == "1234.56 EUR"
              and money_text("7,8") == "7.80 EUR" and money_text("12,00 CHF") == "12.00 CHF" and money_text("abc") == "", "v1.12: amount input")
        check(pick_value("발행일", "Rechnungsdatum 31.07.2026") == "2026-07-31" and pick_value("총액", "Summe 6,61 EUR 7,87 EUR") == "7.87 EUR"
              and pick_value("총액", "Betrag 7,87") == "7.87 EUR", "v1.12: picked value")
        check(invoice_fields(["DHL Paket GmbH\nRechnungsdatum: 31.07.2026\nRechnungsbetrag (inklusive Umsatzsteuer)    7,87 EUR\n"])[2] == "7.87 EUR",
              "v1.12: total label with a note in brackets")
        daum = mail_search_url({"kind": "daum"}, Decimal("-30.27"), "", day(2026, 8, 5))
        check(daum == "https://mail.daum.net/top/SEARCH?after=20260722&before=20260820&body=30%2C27&cc=30%2C27&file=30%2C27&folderId=ALL"
              "&from=30%2C27&subject=30%2C27&to=30%2C27", f"v1.12: Daum search {daum}")
        gmail = mail_search_url({"kind": "gmail", "index": 1}, Decimal("30.27"), "DHL", day(2026, 8, 5))
        check(gmail.startswith("https://mail.google.com/mail/u/1/#search/") and "30%2C27" in gmail and "%22DHL%22" in gmail, f"v1.12: Gmail {gmail}")
        check(ordered_accounts(["Amex", "Deutsche Bank", "DKB"], {"account_order": ["Deutsche Bank", "Amex"]}) == ["Deutsche Bank", "Amex", "DKB"],
              "v1.12: account order, new accounts last")
        seller_doc = Invoice("x", Path("a.pdf"), None, "Aktuell Vertriebs-GmbH", Decimal("62.80"), "EUR", "RE212140", False, {"amazon": True})
        other_doc = Invoice("y", Path("b.pdf"), None, "Aktuell Vertriebs-GmbH", Decimal("62.80"), "EUR", "RE212140", False, {})
        check(amazon_marketplace("SEPA Lastschrift | AMAZON EU S.A R.L.", seller_doc) and not amazon_marketplace("Shop GmbH", seller_doc)
              and not amazon_marketplace("AMAZON EU", other_doc), "v1.12: Amazon marketplace seller")
        rotated = pymupdf.open()
        rotated.new_page().set_rotation(90)
        stamp_page(rotated[0], "608-004")
        check("608-004" in rotated[0].get_text(), "v1.12: number stamped on a rotated page")
        rotated.close()

        bundle_root = root / "bundle"
        bundle_root.mkdir()
        statement2 = bundle_root / "db.pdf"
        with pymupdf.open() as document:
            for page_index in range(2):
                page = document.new_page()
                rows_text = ["Deutsche Bank AG", "Alter Saldo per 31.07.2026        1.000,00",
                             f"0{page_index + 1}.08. 0{page_index + 1}.08.   SHOP {page_index}A BERLIN                 - 10,00", "2026   2026     Kartenzahlung",
                             f"0{page_index + 1}.08. 0{page_index + 1}.08.   SHOP {page_index}B BERLIN                 - 20,00", "2026   2026     Kartenzahlung"]
                for row, line in enumerate(rows_text):
                    page.insert_text((40, 60 + 14 * row), line, fontname="cour", fontsize=9)
            document.save(statement2)
        documents = {}
        for index in range(2):
            path = bundle_root / f"inv{index}.pdf"
            with pymupdf.open() as document:
                document.new_page().insert_text((72, 100), f"Rechnung {index}")
                document.save(path)
            documents[f"d{index}"] = SimpleNamespace(path=path)
        _, rows = read_statement_pdf(statement2)
        mc = MonthlyClose.__new__(MonthlyClose)
        mc.transactions = {f"k{i}": Transaction(f"k{i}", day.fromisoformat(row[0]), row[1], Decimal(row[2].replace(",", ".")), "EUR")
                           for i, row in enumerate(rows)}
        mc.accounts = {key: "Deutsche Bank" for key in mc.transactions}
        mc.statement_sources = {"s1": statement2}
        mc.by_key = documents
        records = {key: {"number": f"608-{i + 1:03d}", "period": "2026-08"} for i, key in enumerate(mc.transactions)}
        records["k0"]["links"], records["k2"]["links"] = ["d0"], ["d1"]
        mc.state = {"records": records, "aliases": {}, "transaction_sources": {key: {"s1": "db.pdf"} for key in mc.transactions}}
        data, summary = mc.print_bundle()
        with pymupdf.open("pdf", data) as document:
            order = [" ".join(page.get_text().split())[:40] for page in document]
        check(len(order) == 6 and order[0].startswith("Deutsche Bank") and order[1].startswith("Rechnung 0")
              and "608-002" in order[2] and order[3].startswith("Deutsche Bank") and order[4].startswith("Rechnung 1")
              and "608-004" in order[5], f"v1.12: print bundle order {order} {summary}")
        check(statement2.read_bytes() and all(b"608-" not in documents[key].path.read_bytes() for key in documents), "v1.12: originals unchanged")
        # v1.14 print options
        def pages(**options):
            data, summary = mc.print_bundle(**options)
            with pymupdf.open("pdf", data) as document:
                return [" ".join(page.get_text().split())[:40] for page in document], summary
        only_statements, _ = pages(content="statements")
        check(len(only_statements) == 2 and all(text.startswith("Deutsche Bank") for text in only_statements), f"v1.14 print: statements only {only_statements}")
        only_invoices, _ = pages(content="invoices")
        check(len(only_invoices) == 4 and only_invoices[0].startswith("Rechnung 0") and "608-002" in only_invoices[1]
              and only_invoices[2].startswith("Rechnung 1"), f"v1.14 print: invoices in number order {only_invoices}")
        plain, _ = pages(content="invoices", stamp=False, notes=False)
        check(plain == ["Rechnung 0", "Rechnung 1"], f"v1.14 print: no stamp, no note pages {plain}")
        sheets, summary = pages(duplex=True)
        check(len(sheets) % 2 == 0 and len(sheets) == 12 and "빈 쪽 6쪽" in summary, f"v1.14 print: blank pages for two-sided printing {sheets} {summary}")
        check(mc.print_keys(first="608-002", last="608-003") == ["k1", "k2"] and mc.print_keys(status="인보이스 없음") == ["k1", "k3"]
              and mc.print_keys(month="2026-07") == [], "v1.14 print: range and state")

        # refunds: an earlier, already sent month is not changed; partial refunds need the purchase invoice
        mc.state["records"]["old"] = {"number": "607-010", "period": "2026-07", "day": "2026-07-20", "amount": "62.80",
                                      "currency": "EUR", "merchant": "SHOP 0A BERLIN | Kartenzahlung"}
        mc.state["exports"] = {"2026-07": [{"version": 1}]}
        mc.transactions["r"] = Transaction("r", day(2026, 8, 9), "SHOP 0A BERLIN | Gutschrift", Decimal("-62.80"), "EUR")
        mc.state["records"]["r"] = {"number": "608-005", "period": "2026-08"}
        check([row[1] for row in mc.refund_candidates("r")] == ["old"], "v1.12: refund candidate from an earlier month")
        mc.state["records"]["r"]["refund_of"] = "old"
        check(mc.status_for("r") == EARLIER_REFUND and "607-010" in mc.german_status("r") and "bereits übermittelt" in mc.german_status("r"),
              f"v1.12: earlier refund status {mc.german_status('r')}")
        mc.transactions["p"] = Transaction("p", day(2026, 8, 3), "SHOP X", Decimal("50.00"), "EUR")
        mc.transactions["q"] = Transaction("q", day(2026, 8, 6), "SHOP X", Decimal("-20.00"), "EUR")
        mc.state["records"]["p"] = {"number": "608-006", "period": "2026-08", "refund_group": ["p", "q"]}
        mc.state["records"]["q"] = {"number": "608-007", "period": "2026-08", "refund_group": ["p", "q"]}
        check("인보이스 필요" in mc.status_for("p") and "Beleg fehlt" in mc.german_status("p"), f"v1.12: partial refund needs evidence {mc.status_for('p')}")
        mc.state["records"]["p"]["links"] = ["d0"]
        check(mc.status_for("q") == PARTIAL_REFUND_DONE, "v1.12: partial refund with purchase invoice is done")
    print("PASS: v1.12 amount input, value picking, DHL total, mail search, account order, Amazon sellers, print bundle, refunds, v1.11 final check, v1.10 statement text, sort, reasons, v1.9 recurring rules, unmatched evidence, plain numbers, v1.8 copy import mode, v1.7.1 combined PDF split, v1.7 work folder import, v1.6 learned seller layouts, v1.5 Markdown follows PDF, ZIP parts, v1.4.1 duplicate invoices, v1.4 statement filing, v1.2.2 numbered PDF statement, v1.2 required fields and invoice check, v1.1.1 file names, total labels, import errors, encodings, XRechnung/ZUGFeRD, seller patterns, edits file")


def run_v113():
    """v1.13: learning picks the right labels, file-name numbers, field guess from labels, duplicate words."""
    import learned_formats
    from invoice_renamer import field_from_label, invoice_likeness, number_from_filename, value_problems
    from pdf_view import PdfView

    from monthly_close import FOLLOW_UP, update_terms
    saved = update_terms({"records": {"a": {"follow_up": "증빙 확보 불가"}, "b": {"follow_up": "요청 중"}, "c": "x"}})
    check(saved["records"]["a"]["follow_up"] == "인보이스 확보 불가" and saved["records"]["b"]["follow_up"] == "요청 중"
          and "인보이스 확보 불가" in FOLLOW_UP, f"v1.13: old ‘증빙’ follow-up renamed {saved}")

    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
        root = Path(scratch)
        # an advisor invoice with a bank footer: the old learner took ‘Umsatzsteuer | Sparkasse’ as the number label
        text = ("Ast Steuerberatung\nRechnung\nRechnungs-Nummer: 2026-0815\nRechnungsdatum: 03.11.2026\n"
                "Gebühr 100,00\nUmsatzsteuer 19,00\nGesamtbetrag 119,00 EUR\n"
                "Umsatzsteuer | Sparkasse 2026-0815 IBAN DE00 1234\nEUR Gebühr GESELLSCHAFT 119,00\n")
        entry, reason = learned_formats.learn(text, "ast.pdf", ("2026-11-03", "Ast Steuerberatung", "119.00 EUR"), "2026-0815",
                                              save=False)
        check(bool(entry) and entry["labels"].get("number", "").startswith("Rechnungs"),
              f"v1.13: number label from the invoice line {entry and entry['labels']}")
        check("Sparkasse" not in str(entry["labels"]) and "GESELLSCHAFT" not in str(entry["labels"]),
              f"v1.13: footer words not learned {entry['labels']}")
        check(not learned_formats.load_formats(), "v1.13: save=False stores nothing")
        learned_formats.remember(entry)
        check(len(learned_formats.load_formats()) == 1, "v1.13: remember stores the entry")
        # the anchor (words next to the dragged box) wins when it reads the value back
        entry2, _ = learned_formats.learn(text, "ast.pdf", ("2026-11-03", "Ast Steuerberatung", "119.00 EUR"), "2026-0815",
                                          anchors={"total": "Gesamtbetrag"}, save=False)
        check(entry2["labels"]["total"] == "Gesamtbetrag", f"v1.13: anchor label used {entry2['labels']}")
        entry3, _ = learned_formats.learn(text, "ast.pdf", ("2026-11-03", "Ast Steuerberatung", "119.00 EUR"), "2026-0815",
                                          anchors={"total": "Telefon"}, save=False)
        check(entry3["labels"]["total"] != "Telefon", "v1.13: an anchor that does not give the value back is ignored")

        # invoice number from the original file name, only when printed in the document
        check(number_from_filename(Path("RE_612088028.pdf"), "Rechnungsnummer 612088028") == "612088028", "v1.13: number from file name")
        check(number_from_filename(Path("RE_612088028.pdf"), "nothing here") == "", "v1.13: file-name number must be printed")
        check(number_from_filename(Path("2026-07-31_Shop.pdf"), "Datum 2026-07-31") == "", "v1.13: dates are not numbers")
        pdf = root / "dm_612088028.pdf"
        with pymupdf.open() as document:
            document.new_page().insert_text((60, 80), "dm-drogerie markt\nDatum 05.11.2026\nSumme 12,34 EUR\nBeleg 612088028")
            document.save(pdf)
        info = {}
        fields, _, number, _ = read_invoice_details(pdf, info)
        check(number == "612088028" and info["checks"].get("인보이스 번호") == "파일명에서 가져옴",
              f"v1.13: read number from file name {number} {info.get('checks')}")

        # which field a picked value belongs to, from the words next to it
        check(field_from_label("Rechnungs-Nummer", "612088028") == "인보이스 번호", "v1.13: label → number")
        check(field_from_label("Rechnungsdatum", "03.11.2026") == "발행일", "v1.13: label → date")
        check(field_from_label("Gesamtbetrag", "119,00 EUR") == "총액", "v1.13: label → total")
        check(field_from_label("Rechnungsdatum", "abc") == "", "v1.13: date label but no date picked")
        check(field_from_label("IBAN", "DE00") == "", "v1.13: footer label names no field")

        # values not printed in the PDF are flagged ‘확인 필요’
        problems = value_problems(pdf, {"발행일": "2026-11-05", "총액": "99.00 EUR", "인보이스 번호": "612088028", "업체명": "dm"})
        check(problems.get("총액") and "발행일" not in problems and "인보이스 번호" not in problems,
              f"v1.13: value check {problems}")

        # the same word printed twice on top of itself is picked once
        doubled = root / "doubled.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((60, 80), "Nr")
            page.insert_text((90, 80), "612088028")
            page.insert_text((90.4, 80.3), "612088028")
            document.save(doubled)
        with pymupdf.open(doubled) as document:
            picked = PdfView.words_in(document[0], pymupdf.Rect(0, 0, 400, 120))
            label = PdfView.label_near(document[0], next(pymupdf.Rect(w[:4]) for w in document[0].get_text("words")
                                                         if w[4] == "612088028"))
        check(picked == "Nr 612088028", f"v1.13: duplicate words dropped ‘{picked}’")
        check(label == "Nr", f"v1.13: label next to the value ‘{label}’")

        # not-invoice rule: full-width letters, order words, table totals, customs papers
        from invoice_renamer import customs_document, invoice_fields, set_flag
        chinese = ("Shenzhen Trading Co., Ltd.\nＩｎｖｏｉｃｅ\nOrder ID 302-1234567-1234567\nInvoice Date 2026-10-02\n"
                   "Item  Quantity  Unit Price  Total Price(EUR)\nUSB cable 3 11.99 35.97\n")
        check(invoice_likeness(chinese) >= 3, f"v1.13: Chinese seller invoice counts as invoice ({invoice_likeness(chinese)})")
        check(invoice_fields(["Total Price(EUR) 35.97", "Total Price(EUR) 35.97"])[2] == "35.97 EUR", "v1.13: total with currency in heading")
        check(customs_document("COMMERCIAL INVOICE\nDeclared value 17.98") == "commercial invoice", "v1.13: customs paper")
        check(customs_document("Rechnung\nSumme 10,00 EUR") == "", "v1.13: normal invoice is not customs")
        customs = root / "awb.pdf"
        with pymupdf.open() as document:
            document.new_page().insert_text((60, 80), "Air Waybill\nShipper Shenzhen Trading\nConsignee Euromall\n"
                                                      "Declared value 17.98 USD\nPieces 1 Weight 0.4 kg")
            document.save(customs)
        info = {}
        read_invoice_details(customs, info)
        check(info["invoice_like"] is False and info.get("kind") == "customs" and "Air Waybill".casefold() in info["reason"].casefold(),
              f"v1.13: air waybill classified {info}")
        set_flag([customs], "invoice")
        info = {}
        read_invoice_details(customs, info)
        check(info["invoice_like"] is True, "v1.13: ‘인보이스 맞음’ is remembered")
        set_flag([customs], "invoice", None)
        info = {}
        read_invoice_details(customs, info)
        check(info["invoice_like"] is False, "v1.13: mark removed")

        # exclusion by reason: moved (not deleted), logged, undoable; marks follow renamed files
        from invoice_renamer import exclude_files, follow_flags, load_exclusion_log, marks_for, undo_moves
        work = root / "work"
        work.mkdir()
        ad = work / "werbung.pdf"
        ad.write_bytes(pdf.read_bytes() + b"%ad")
        history, errors = exclude_files(work, [ad], "인보이스 아님")
        moved = work / "제외" / "인보이스 아님" / "werbung.pdf"
        check(not errors and moved.is_file() and not ad.exists(), f"v1.13: excluded by reason {errors}")
        log = load_exclusion_log()
        check(log[-1]["reason"] == "인보이스 아님" and log[-1]["from"] == str(ad), "v1.13: exclusion logged")
        restored, failed = undo_moves(history)
        check(ad.is_file() and not failed, "v1.13: exclusion undone")
        set_flag([ad], "hold", "2026-11-27")
        check(marks_for([ad]).get(ad, {}).get("hold") == "2026-11-27", "v1.13: hold mark found by name and content")
        renamed = work / "2026-11-05_dm.pdf"
        ad.rename(renamed)
        check(marks_for([renamed]) == {}, "v1.13: a renamed file is not found by its old name")
        follow_flags([(ad, renamed, history[0][2])])
        check(marks_for([renamed]).get(renamed, {}).get("hold"), "v1.13: marks follow the renamed file")

        # step 3: reasons to look again before linking
        from datetime import date as day
        from decimal import Decimal
        from monthly_close import Invoice, Transaction, link_concerns
        item = Transaction("t", day(2026, 8, 10), "BUEROBEDARF NORD KG", Decimal("129.90"), "EUR")
        good = Invoice("g", Path("2026-08-09_Buerobedarf.pdf"), day(2026, 8, 9), "Buerobedarf Nord KG", Decimal("129.90"), "EUR", "R-1", False)
        check(link_concerns(item, [good], [good], {}) == [], "v1.13: a clean link has no concerns")
        late = Invoice("l", Path("x (1).pdf"), day(2026, 7, 1), "Other Shop", Decimal("99.00"), "EUR", "R-2", False)
        concerns = link_concerns(item, [late], [good, late], {}, used_by={"l": "607-004"})
        text = " | ".join(concerns)
        check("금액" in text and "날짜 차이 40일" in text and "업체명" in text and "607-004" in text and "(1)" in text,
              f"v1.13: concerns listed {concerns}")
        twin = Invoice("w", Path("b.pdf"), day(2026, 8, 11), "Buerobedarf Nord KG", Decimal("129.90"), "EUR", "R-3", False)
        check(any("후보가 1개 더" in c for c in link_concerns(item, [good], [good, twin], {})), "v1.13: same amount twin noted")
        usd = Invoice("u", Path("u.pdf"), day(2026, 8, 10), "Buerobedarf Nord KG", Decimal("129.90"), "USD", "R-4", False)
        check(any("통화" in c for c in link_concerns(item, [usd], [usd], {})), "v1.13: currency differs")

        # folder import keeping the subfolders
        source = root / "drop"
        (source / "2026-08").mkdir(parents=True)
        (source / "top.pdf").write_bytes(pdf.read_bytes())
        (source / "2026-08" / "inner.pdf").write_bytes(pdf.read_bytes() + b"%x")
        target = root / "target"
        history, _, errors = import_evidence([source / "top.pdf", source / "2026-08" / "inner.pdf"], target, copy=True, base=source)
        check(not errors and (target / "top.pdf").is_file() and (target / "2026-08" / "inner.pdf").is_file(),
              f"v1.13: import keeps subfolders {errors}")
    print("PASS: v1.13 learning labels, file-name number, field guess, value check, duplicate words, not-invoice rule, customs papers,"
          " exclusion reasons, marks, link check, import subfolders")

def run_v1131():
    """v1.13.1: what was read is stored on disk, so unchanged (or only renamed/moved) files are not read or OCR'd again."""
    import invoice_renamer as ir
    from monthly_close import index_pdfs, clear_index_cache
    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
        ir._DIGEST_INDEX.update(loaded=False, data={}, dirty=False)
        root = Path(scratch) / "work"
        root.mkdir()
        pdf = root / "a.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((60, 80), "Druckerei Schmidt GmbH\nRechnungsnummer R-77\nRechnungsdatum: 03.11.2026\nGesamtbetrag 119,00 EUR")
            document.save(pdf)
        calls = []
        original = ir._read_pdf_pages

        def counted(path):
            calls.append(Path(path).name)
            return original(path)

        with patch.object(ir, "_read_pdf_pages", counted):
            first = ir.read_invoice_details(pdf)
            ir.flush_text_cache()
            ir._DIGEST_INDEX.update(loaded=False, data={}, dirty=False)  # like a new start
            second = ir.read_invoice_details(pdf)
            check(calls == ["a.pdf"] and first == second, f"v1.13.1: second start uses the stored text {calls}")
            moved = root / "2026-11"
            moved.mkdir()
            renamed = moved / "119.00_26-11-03_Druckerei_Nr_R-77_invoice.pdf"
            pdf.rename(renamed)
            check(ir.read_invoice_details(renamed) == first and calls == ["a.pdf"], f"v1.13.1: renamed file not read again {calls}")
            clear_index_cache()
            invoices, _ = index_pdfs(root)
            check(len(invoices) == 1 and calls == ["a.pdf"], f"v1.13.1: step 3 shares the stored text {calls}")
            ir.forget_text([renamed])
            ir.read_invoice_details(renamed)
            check(calls[-1] == renamed.name and len(calls) == 2, f"v1.13.1: ‘다시 읽기’ really reads again {calls}")
        # a page that could not be read completely (no OCR data) is not stored
        scan = root / "scan.pdf"
        with pymupdf.open() as document:
            document.new_page()
            document.save(scan)
        with patch.object(ir, "tessdata_folder", lambda: (None, "")):
            note = ir.read_invoice_details(scan)[1]
        stored = list((Path(scratch) / "InvoiceRenamer" / "text-cache").rglob(f"{ir.known_digest(scan)}.json"))
        check(note and not stored, f"v1.13.1: incomplete OCR is tried again next time {note} {stored}")
    # the ‘오늘 할 일’ panel can open while a message box has the focus
    import tkinter as tk
    from ui_theme import InlineDialog
    try:
        window = tk.Tk()
    except tk.TclError:
        print("SKIP: v1.13.1 panel focus (no display)")
    else:
        window.withdraw()
        with patch.object(tk.Tk, "focus_get", side_effect=KeyError("__tk__messagebox")):
            panel = InlineDialog(window)
        check(panel._previous_focus is None, "v1.13.1: panel opens while a message box has the focus")
        panel.destroy()
        window.destroy()
    # one odd file must not stop the reading: it is listed as unreadable, the error says where it happened
    import monthly_close
    import release_info
    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
        ir._DIGEST_INDEX.update(loaded=False, data={}, dirty=False)
        folder = Path(scratch)
        for name in ("good.pdf", "odd.pdf"):
            with pymupdf.open() as document:
                document.new_page().insert_text((60, 80), f"Rechnungsnummer R-1\nGesamtbetrag 10,00 EUR\n{name}")
                document.save(folder / name)
        real = monthly_close.read_invoice_details

        def odd(path, *args):
            if Path(path).name == "odd.pdf":
                {}["missing"]
            return real(path, *args)

        monthly_close.clear_index_cache()
        release_info._EVENTS.clear()
        with patch.object(monthly_close, "read_invoice_details", odd):
            invoices, issues = monthly_close.index_pdfs(folder)
        event = release_info._EVENTS[-1] if release_info._EVENTS else {}
        check(len(invoices) == 2 and issues == ["odd.pdf"] and event.get("error_type") == "KeyError"
              and "index_pdfs" in event.get("where", ""), f"v1.13.1: odd file does not stop reading {issues} {event}")
        check(ir.money_text("1-2,50") in ("", "-2.50 EUR", "12.50 EUR"), "v1.13.1: odd amount text")
    print("PASS: v1.13.1 stored text (new start, renamed, step 3, re-read, incomplete OCR), panel focus, odd file keeps reading")


def run_v114():
    """v1.14 features."""
    from tax_documents import pdf_tax_fields, tax_notes, vat_ids
    from monthly_close import exchange_rate, Invoice
    from decimal import Decimal
    from datetime import date as day
    # tax: reverse charge, foreign seller, IBAN is not a VAT ID, missing tax / tax id
    french = ("Société Exemple SAS\nN° TVA FR 12 345678901\nClient: Euromall GmbH USt-IdNr. DE123456789\n"
              "Autoliquidation - Reverse charge Article 196\nIBAN DE89370400440532013000\nTotal 100,00 EUR\n")
    meta = pdf_tax_fields(french)
    check(vat_ids(french) == ["FR12345678901", "DE123456789"] and meta.get("foreign") == "FR" and meta["special"] == "Reverse Charge",
          f"v1.14: VAT IDs and reverse charge {vat_ids(french)} {meta}")
    notes = [korean for korean, _ in tax_notes(meta, Decimal("100"), "EUR")]
    check(any("역과세" in n for n in notes) and any("해외" in n for n in notes) and not any(n.startswith("세액(MwSt)") for n in notes),
          f"v1.14: reverse charge needs no tax line {notes}")
    german = "Muster GmbH\nSteuernummer 12/345/67890\nMwSt 19% 19,00\nGesamtbetrag 119,00 EUR\n"
    check(tax_notes(pdf_tax_fields(german), Decimal("119"), "EUR") == [], "v1.14: complete German invoice has no remarks")
    bare = tax_notes(pdf_tax_fields("Laden\nGesamt 300,00 EUR\n"), Decimal("300"), "EUR")
    check(len(bare) == 2, f"v1.14: no tax and no tax id are named {bare}")
    small = tax_notes(pdf_tax_fields("Bäckerei\nMwSt 7% 0,30\nSumme 4,50 EUR\n"), Decimal("4.5"), "EUR")
    check(small == [], f"v1.14: small invoice needs no tax id {small}")
    intra = pdf_tax_fields("Lieferant NL\nBTW NL123456789B01\nInnergemeinschaftliche Lieferung\n")
    check(intra["special"] == "Innergemeinschaftlich" and intra.get("foreign") == "NL", f"v1.14: intra-EU {intra}")
    # FX: implied rate from booking and invoice amount
    class Item:  # the transaction side only needs amount and currency
        amount, currency = Decimal("-92.15"), "EUR"
    doc = Invoice("k", Path("x.pdf"), day(2026, 11, 3), "Shop", Decimal("100.00"), "USD", "1", False)
    check(exchange_rate(Item, doc) == Decimal("0.9215"), f"v1.14: exchange rate {exchange_rate(Item, doc)}")
    from monthly_close import recurring_gaps, review_months
    records = {}
    for index, (period, merchant) in enumerate([("2026-07", "TELEFONICA GERMANY"), ("2026-08", "Telefonica Germany"),
                                                ("2026-09", "TELEFONICA GERMANY"), ("2026-10", "Amazon"),
                                                ("2026-08", "Shop A"), ("2026-10", "Shop A"), ("2026-09", "Shop A")]):
        records[f"r{index}"] = {"period": period, "merchant": merchant, "number": str(index)}
    gaps = recurring_gaps({"records": records})
    check(list(gaps) == ["2026-10"] and [name for name, _ in gaps["2026-10"]] == ["TELEFONICA GERMANY"],
          f"v1.14: monthly merchant missing {dict(gaps)}")
    holes = recurring_gaps({"records": {"a": {"period": "2026-01", "merchant": "X"}, "b": {"period": "2026-02", "merchant": "X"},
                                        "c": {"period": "2026-03", "merchant": "X"}, "d": {"period": "2026-06", "merchant": "Y"}}})
    check(not holes, f"v1.14: no warning after months that were not loaded {dict(holes)}")
    review = review_months({"records": records}, [])
    check(any(kind == "매달 거래처 빠짐" for kind, _, _ in review["2026-10"]), "v1.14: monthly review lists the missing merchant")
    from monthly_close import advisor_notice, open_inquiries, NOTICE_KINDS, german_money, cover_letter_pdf
    asked = {"inquiries": [{"month": "2026-10", "question": "a", "status": "답변 필요"},
                           {"month": "2026-10", "question": "b", "status": "답변함"},
                           {"month": "2026-09", "question": "c"}, "broken"]}
    check([e["question"] for e in open_inquiries(asked, "2026-10")] == ["a"] and len(open_inquiries(asked)) == 2,
          "v1.14: open Rückfragen (no state counts as open, odd entries skipped)")
    subjects = [advisor_notice(kind, ["2026-10"], "M.zip", {"bookings": 3, "documents": 2, "open": 1})[0] for kind in NOTICE_KINDS]
    first = advisor_notice(NOTICE_KINDS[0], ["2026-10"], "M.zip", {"bookings": 3, "documents": 2, "open": 1}, "Firma", "Frau Muster")[1]
    check(subjects == ["Unterlagen Monatsabschluss 10/2026", "Nachreichung Unterlagen 10/2026", "Rückfrage zum Monatsabschluss 10/2026"]
          and first.startswith("Guten Tag Frau Muster,") and "3 Buchungen, 2 Belege, 1 offene Posten" in first and first.endswith("Firma"),
          f"v1.14: notification mail {subjects} {first}")
    check(german_money(Decimal("-1234.5"), "EUR") == "-1.234,50 EUR", "v1.14: German amount")
    import pymupdf
    letter = cover_letter_pdf({"months": ["2026-10"], "open": [(f"K-{n}", day(2026, 10, 1), Decimal("5"), "EUR", "Händler 상점 – x", "offen")
                                                               for n in range(80)]})
    with pymupdf.open(stream=letter) as pdf:
        check(pdf.page_count >= 2 and "Händler ?? - x" in pdf[0].get_text(), "v1.14: long Begleitschreiben continues on a new page, Latin-1 text")
    from advisor_workbook import cost_rows, cost_workbook
    from openpyxl import load_workbook
    import io
    costs = cost_rows({"a": {"period": "2026-09", "number": "609-001", "merchant": "TELEFONICA | Lastschrift", "amount": "39.99", "currency": "EUR"},
                       "b": {"period": "2026-10", "number": "610-001", "merchant": "TELEFONICA", "amount": "39.99", "currency": "EUR"},
                       "c": {"period": "2026-10", "number": "610-002", "merchant": "AMAZON", "amount": "-20.00", "currency": "EUR"},
                       "d": {"period": "2026-10", "number": "610-003", "merchant": "Kreditkarte", "amount": "500", "transfer": "x"},
                       "e": {"period": "2026-10", "number": "610-004", "merchant": "AMZN Mktp", "amount": "62.80", "currency": "EUR"}},
                      {"e": "AMAZON"}, {"a": "DKB"})
    check([row["vendor"] for row in costs] == ["TELEFONICA", "TELEFONICA", "AMAZON", "AMAZON"] and costs[0]["account"] == "DKB",
          f"v1.14: cost rows (transfer left out, invoice seller as vendor) {costs}")
    book = load_workbook(io.BytesIO(cost_workbook(costs)))
    vendors = [[cell.value for cell in row] for row in book["업체별"].iter_rows(min_row=2)]
    pivot = [[cell.value for cell in row] for row in book["업체×월"].iter_rows()]
    check(book.sheetnames == ["업체별", "월별", "업체×월", "거래"] and vendors[0][:3] == ["TELEFONICA", "EUR", 2]
          and float(vendors[1][4]) == -20.0 and float(vendors[1][5]) == 42.8 and pivot[0] == ["업체", "통화", "2026-09", "2026-10", "합계"],
          f"v1.14: cost workbook {vendors} {pivot}")
    print("PASS: v1.14 tax remarks (reverse charge, EU/foreign VAT ID, missing tax/tax id, small invoice), exchange rate, monthly merchant missing, Rückfragen, notification mail, Begleitschreiben, cost Excel")


def run_v114_folders():
    """v1.14 환경설정 → 폴더 이름·위치."""
    import invoice_renamer as ir
    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": str(Path(scratch) / "appdata")}):
        root = Path(scratch)
        work, outside = root / "work", root / "elsewhere"
        work.mkdir()
        outside.mkdir()
        default = ir.folder_settings()
        check(default["matched"] == {"name": "매칭완료", "location": ""} and ir.program_folder("excluded", work) == work / "제외",
              f"v1.14 folders: defaults {default}")
        check(not ir.valid_folder_name("a/b") and not ir.valid_folder_name("2026-10") and ir.valid_folder_name("Erledigt"),
              "v1.14 folders: name rule")

        def invoice(path, text):
            path.parent.mkdir(parents=True, exist_ok=True)
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((60, 80), text)
                pdf.save(path)

        invoice(work / "매칭완료_2610" / "a.pdf", "Muster GmbH Rechnung 1 Gesamt 10,00 EUR")
        invoice(work / "2026-09" / "매칭완료_2609" / "b.pdf", "Muster GmbH Rechnung 2 Gesamt 20,00 EUR")
        invoice(work / "제외" / "중복" / "c.pdf", "copy")
        invoice(work / "open.pdf", "Muster GmbH Rechnung 3 Gesamt 30,00 EUR")
        old = ir.folder_settings()
        new = {"matched": {"name": "Erledigt", "location": str(outside)}, "statements": {"name": "Auszuege", "location": ""},
               "excluded": {"name": "Ablage", "location": ""}}
        ir.save_settings({"program_folders": new})
        check(ir.folder_settings() == new, "v1.14 folders: saved")
        check(ir.is_matched_folder("매칭완료_2610") and ir.is_matched_folder("Erledigt_2610") and not ir.is_matched_folder("Erledigt"),
              "v1.14 folders: old and new matched names known")
        check({"Auszuege", "Ablage", "제외", "명세서"} <= ir.kept_apart_names(), "v1.14 folders: kept apart names")
        pairs = [pair for kind in new for pair in ir.relocation_plan(kind, work, old, new)]
        targets = sorted(target.relative_to(root).as_posix() for _, target in pairs)
        check(targets == ["elsewhere/Erledigt_2609/b.pdf", "elsewhere/Erledigt_2610/a.pdf", "work/Ablage/중복/c.pdf"],
              f"v1.14 folders: relocation plan {targets}")
        moved, errors = ir.relocate_files(pairs, keep=[work, outside])
        check(len(moved) == 3 and not errors and not (work / "매칭완료_2610").exists() and (work / "2026-09").is_dir() is False,
              f"v1.14 folders: moved, empty folders removed {errors}")
        found = sorted(doc.path.name for doc in index_pdfs(work)[0])
        check(found == ["a.pdf", "b.pdf", "open.pdf"], f"v1.14 folders: step 3 reads the matched folders in the other place {found}")
        check([path.name for path in ir.evidence_in(work)] == ["open.pdf"], "v1.14 folders: step 1 leaves Ablage out")
        history, errors = ir.exclude_files(work, [work / "open.pdf"], "기타")
        check(not errors and history[0][1].parent == work / "Ablage" / "기타", f"v1.14 folders: exclusion uses the new name {history}")
        ir.update_exclusion_log([(history[0][1], root / "x.pdf")])
        check(ir.load_exclusion_log()[-1]["to"] == str(root / "x.pdf"), "v1.14 folders: exclusion log follows a move")
        restored, failed = ir.undo_moves(history)
        check(restored and (work / "open.pdf").is_file(), "v1.14 folders: undo after exclusion")
        back = {kind: {"name": ir.PROGRAM_FOLDERS[kind][0], "location": ""} for kind in ir.PROGRAM_FOLDERS}
        pairs = [pair for kind in back for pair in ir.relocation_plan(kind, work, new, back)]
        moved, errors = ir.relocate_files(pairs, keep=[work, outside])
        check(outside.is_dir() and not any(outside.iterdir()), "v1.14 folders: chosen place itself is kept")
        check(not errors and (work / "매칭완료_2610" / "a.pdf").is_file() and (work / "제외" / "중복" / "c.pdf").is_file(),
              f"v1.14 folders: back to the work folder {[str(t) for _, t in pairs]}")
        # sales invoices (Ausgangsrechnung) from their own folder count as money coming in
        from monthly_close import Invoice, signed_total, candidates, Transaction, next_cash_number, cash_documents
        from datetime import date as day
        from decimal import Decimal
        sales_dir = root / "Ausgang"
        invoice(sales_dir / "2026-11" / "AR-100.pdf", "Euromall GmbH Rechnung AR-100 Gesamtbetrag 5.176,50 EUR")
        ir.save_settings({"sales_folder": str(sales_dir)})
        docs = {doc.path.name: doc for doc in index_pdfs(work)[0]}
        check(docs["AR-100.pdf"].tax_fields.get("sales") and not (docs["open.pdf"].tax_fields or {}).get("sales"),
              f"v1.14 sales: own invoices read and marked {list(docs)}")
        sale = Invoice("s", Path("AR.pdf"), day(2026, 11, 3), "Euromall", Decimal("5176.50"), "EUR", "AR-100", False, {"sales": True})
        paid = Transaction("t", day(2026, 11, 20), "LX PANTOS | Gutschrift", Decimal("-5176.50"), "EUR")
        check(signed_total(sale) == Decimal("-5176.50") and candidates(paid, [sale], {}) == [sale]
              and signed_total(Invoice("c", Path("G.pdf"), None, "", Decimal("10"), "EUR", "", True, {"sales": True})) == Decimal("10"),
              "v1.14 sales: incoming money matches my invoice, my credit note counts as money out")
        ir.save_settings({"sales_folder": ""})
        cash_state = {"cash": [{"id": "K2611-001", "doc": "abc"}, {"id": "K2611-007"}, {"id": "K2610-002"}, "odd"]}
        check(next_cash_number(cash_state, "2026-11") == "K2611-008" and next_cash_number({}, "2026-12") == "K2612-001"
              and cash_documents(cash_state) == {"abc"}, "v1.14 Kasse: numbers per month, receipts count as used")
        # phone photos
        phone = root / "Camera Uploads"
        (phone / "2026").mkdir(parents=True)
        for name in ("a.jpg", "b.png", "notes.txt"):
            (phone / "2026" / name).write_bytes(b"x" * 10)
        (phone / "old.jpg").write_bytes(b"y")
        import os, time
        old_time = time.time() - 100 * 86400
        os.utime(phone / "old.jpg", (old_time, old_time))
        recent = sorted(path.name for path, _ in ir.new_photos(phone, 30))
        everything = sorted(path.name for path, _ in ir.new_photos(phone, None))
        check(recent == ["a.jpg", "b.png"] and everything == ["a.jpg", "b.png", "old.jpg"], f"v1.14 photos: new photos {recent} {everything}")
        ir.remember_photos([phone / "2026" / "a.jpg"], "가져옴")
        ir.remember_photos([phone / "old.jpg"], "숨김")
        check([path.name for path, _ in ir.new_photos(phone, None)] == ["b.png"], "v1.14 photos: taken in or hidden photos are not offered again")
    print("PASS: v1.14 folder names and places (old names still known, move existing files, step 1/3 reading, exclusion, undo), phone photos, sales invoices, Kasse numbers")


def run_v114_mail():
    """v1.14 IMAP attachments and SMTP mail with fake servers; password in a fake credential store."""
    import mail_link
    from email.message import EmailMessage
    from datetime import date as day
    import keyring
    from keyring.backend import KeyringBackend

    class Memory(KeyringBackend):
        priority = 1
        store = {}

        def set_password(self, service, user, password):
            self.store[(service, user)] = password

        def get_password(self, service, user):
            return self.store.get((service, user))

        def delete_password(self, service, user):
            self.store.pop((service, user), None)

    previous = keyring.get_keyring()
    keyring.set_keyring(Memory())
    try:
        mail_link.save_password("me@example.com", "app-secret")
        check(mail_link.load_password("me@example.com") == "app-secret" and Memory.store, "v1.14 mail: password in the credential store")
        with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
            import invoice_renamer as ir
            ir.save_settings({"mail_account": {"user": "me@example.com", "imap_host": "imap.example.com", "imap_port": "993"}})
            account = mail_link.account_settings(ir.load_settings())
            check(account["imap_port"] == 993 and "app-secret" not in (Path(scratch) / "InvoiceRenamer" / "settings.json").read_text(encoding="utf-8"),
                  "v1.14 mail: settings keep no password")
        mails = []
        for index, files in enumerate(([("Rechnung_1.pdf", b"%PDF-1 a")], [("foto.jpg", b"jpg"), ("agb.txt", b"t")], [])):
            message = EmailMessage()
            message["From"], message["Subject"], message["Message-ID"] = "Shop <shop@example.com>", f"=?utf-8?q?Rechnung_Nr._{index}_=C3=BC?=", f"<m{index}@x>"
            message["Date"] = "Thu, 26 Nov 2026 10:00:00 +0100"
            message.set_content("Hallo")
            for name, data in files:
                message.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
            mails.append(message.as_bytes())

        class FakeImap:
            calls = []

            def select(self, folder, readonly=False):
                self.calls.append(("select", folder, readonly))
                return "OK", [b"3"]

            def uid(self, command, *args):
                self.calls.append((command, args))
                if command == "SEARCH":
                    return "OK", [b"1 2 3"]
                return "OK", [(b"1 (BODY[] {10}", mails[int(args[0]) - 1]), b")"]

            def logout(self):
                self.calls.append(("logout",))

        fake = FakeImap()
        found, skipped = mail_link.fetch_attachments(account, "app-secret", 30, connect=lambda *_: fake)
        names = sorted(item["filename"] for item in found)
        check(names == ["Rechnung_1.pdf", "foto.jpg"] and found[0]["day"] == "2026-11-26" and "ü" in found[0]["subject"]
              and ("select", "INBOX", True) in fake.calls and all("PEEK" in str(call) for call in fake.calls if call[0] == "FETCH")
              and fake.calls[-1] == ("logout",), f"v1.14 mail: attachments read without marking mails {names} {fake.calls}")
        again, _ = mail_link.fetch_attachments(account, "app-secret", 30, seen={item["key"] for item in found}, connect=lambda *_: fake)
        check(again == [], "v1.14 mail: attachments taken before are not offered again")

        class FakeSmtp:
            sent = []

            def login(self, user, password):
                self.sent.append(("login", user, password))

            def send_message(self, message):
                self.sent.append(message)

            def quit(self):
                pass

        server = FakeSmtp()
        account.update(smtp_host="smtp.example.com", sender="Euromall GmbH")
        mail_link.smtp_send(account, "app-secret", "kanzlei@example.com", "Unterlagen 10/2026", "Guten Tag", [("M.zip", b"PK")],
                            connect=lambda _account: server)
        message = server.sent[-1]
        check(server.sent[0] == ("login", "me@example.com", "app-secret") and message["To"] == "kanzlei@example.com"
              and "Euromall GmbH" in message["From"] and [part.get_filename() for part in message.iter_attachments()] == ["M.zip"],
              f"v1.14 mail: SMTP message {message['From']}")
        mail_link.delete_password("me@example.com")
        check(mail_link.load_password("me@example.com") == "", "v1.14 mail: password removed")
    finally:
        keyring.set_keyring(previous)
    print("PASS: v1.14 mail link (credential store, IMAP attachments unread and once, SMTP mail with ZIP)")


def run_v114_profiles():
    """v1.14 company profiles: own data folder per company, default company unchanged, shared learned layouts."""
    import os
    import profiles
    import learned_formats
    import invoice_renamer as ir
    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
        os.environ.pop(profiles.BASE_ENV, None)
        data = profiles.load_profiles()
        check(data == {"active": "", "profiles": [{"id": "", "name": "기본 회사"}]} and not profiles.several(), f"v1.14 profiles: default {data}")
        ir.save_settings({"work_folder": str(Path(scratch) / "A")})
        name = profiles.activate_at_start()
        check(name == "기본 회사" and os.environ["LOCALAPPDATA"] == scratch and ir.work_folder() == Path(scratch) / "A",
              "v1.14 profiles: the default company keeps the old place")
        new_id = profiles.add_profile("Euromall  GmbH", Path(scratch) / "B")
        check(profiles.several() and profiles.load_profiles()["profiles"][1] == {"id": new_id, "name": "Euromall GmbH"}, "v1.14 profiles: added")
        try:
            profiles.add_profile("euromall gmbh", Path(scratch) / "C")
            check(False, "v1.14 profiles: same name twice")
        except ValueError:
            pass
        profiles.set_active(new_id)
        os.environ["LOCALAPPDATA"] = os.environ[profiles.BASE_ENV]  # a new start
        os.environ.pop(profiles.BASE_ENV)
        check(profiles.activate_at_start() == "Euromall GmbH" and ir.work_folder() == Path(scratch) / "B"
              and ir.user_data_folder() == Path(scratch) / "InvoiceRenamer" / "profiles" / new_id / "InvoiceRenamer",
              f"v1.14 profiles: the other company has its own settings {ir.user_data_folder()}")
        check(learned_formats.storage_path() == Path(scratch) / "InvoiceRenamer" / "learned-formats.json"
              and ir.text_cache_folder() == Path(scratch) / "InvoiceRenamer" / "text-cache", "v1.14 profiles: learned layouts and text shared")
        profiles.rename_profile(new_id, "Euromall")
        check(profiles.active_name() == "Euromall", "v1.14 profiles: renamed")
        os.environ.pop(profiles.BASE_ENV, None)
    print("PASS: v1.14 company profiles (default unchanged, own folders, shared learning, rename)")


def run_v115_numbers():
    """v1.15: numbers on a PDF statement stand in one column right of the furthest amount (debit and credit alike)."""
    from datetime import date as day
    from decimal import Decimal
    with TemporaryDirectory() as scratch:
        statement = Path(scratch) / "db.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((40, 60), "17.08. 17.08. SEPA Lastschrift 1+1 Telecom", fontname="helv", fontsize=9)
            page.insert_text((380, 60), "- 9,99", fontname="helv", fontsize=9)
            page.insert_text((40, 90), "21.08. 21.08. AUSL.ZAHL. NAVER", fontname="helv", fontsize=9)
            page.insert_text((470, 90), "+ 1.334,00", fontname="helv", fontsize=9)
            document.save(statement)
        entries = [("608-074", Transaction("a", day(2026, 8, 17), "1+1", Decimal("-9.99"), "EUR")),
                   ("608-076", Transaction("b", day(2026, 8, 21), "NAVER", Decimal("1334.00"), "EUR"))]
        data, marked = numbered_statement(statement, entries)
        with pymupdf.open(stream=data) as document:
            spots = {word[4]: word[0] for word in document[0].get_text("words") if word[4].startswith("[608-")}
        check(marked == 2 and len(spots) == 2 and abs(spots["[608-074]"] - spots["[608-076]"]) < 0.5
              and spots["[608-074]"] > 500, f"v1.15 numbers: one column in the right margin {spots}")
    from ui_theme import transaction_look
    looks = [transaction_look("인보이스 미연결"), transaction_look("인보이스 미연결", "", True),
             transaction_look("인보이스 미연결", "", False), transaction_look("금액 대조 완료"),
             transaction_look("연결됨 · 차액 1.00 EUR"), transaction_look("인보이스 미연결", "인보이스 확보 불가")]
    check([tag for _, tag in looks] == ["open", "sure", "likely", "done", "bad", "muted"]
          and looks[3][0].startswith("✓") and looks[1][0].startswith("●"), f"v1.15 colours: {looks}")
    print("PASS: v1.15 statement numbers in one margin column, list colours")


if __name__ == "__main__":
    run()
    run_v113()
    run_v1131()
    run_v114()
    run_v114_folders()
    run_v114_mail()
    run_v114_profiles()
    run_v115_numbers()
