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
              and not combined.exists() and (combined.parent / "제외" / "order__나누기전_원본.pdf").exists(), "Combined PDF split")
    print("PASS: v1.8 copy import mode, v1.7.1 combined PDF split, v1.7 work folder import, v1.6 learned seller layouts, v1.5 Markdown follows PDF, ZIP parts, v1.4.1 duplicate invoices, v1.4 statement filing, v1.2.2 numbered PDF statement, v1.2 required fields and invoice check, v1.1.1 file names, total labels, import errors, encodings, XRechnung/ZUGFeRD, seller patterns, edits file")


if __name__ == "__main__":
    run()
