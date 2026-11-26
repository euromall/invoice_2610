"""Exercise core flows using synthetic samples and disposable user data."""

import csv
from decimal import Decimal
import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import load_workbook


APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from invoice_renamer import digest, export_markdown, source_locations
from monthly_close import MonthlyClose, assign_numbers, candidates, index_pdfs, parse_transactions, read_csv
from workspace_backup import add_attachment, create_backup, restore_workspace


def check(condition, description):
    if not condition:
        raise AssertionError(description)


def run():
    with TemporaryDirectory() as scratch, patch.dict("os.environ", {"LOCALAPPDATA": scratch}):
        root = Path(scratch)
        source = APP / "Beispiele" / "Karte.csv"
        folder = APP / "Beispiele" / "Belege"
        _, rows = read_csv(source)
        transactions, errors = parse_transactions(rows, (0, 1, 2, 3), "EUR", "DD/MM/YYYY", "Demo")
        check(not errors and len(transactions) == 1, "Sample CSV import")
        transaction = transactions[0]
        check(transaction.amount == Decimal("58.39"), "German decimal comma")
        documents, issues = index_pdfs(folder)
        check(not issues and len(documents) == 1, "Sample PDF import")
        document = documents[0]
        check(candidates(transaction, documents, {}) == [document], "Matching by amount and reference")
        check(source_locations(document.path, "58.39 EUR", "총액"), "Source amount location")

        copied = root / document.path.name
        copied.write_bytes(document.path.read_bytes())
        markdown, incomplete = export_markdown(copied, ("2026-11-25", "Muster GmbH", "58.39 EUR"), document.number)
        check("58,39" in markdown.read_text(encoding="utf-8") and not incomplete, "Markdown export")

        supporting = root / "confirmation.txt"
        supporting.write_text("Synthetic supporting material", encoding="utf-8")
        app = MonthlyClose.__new__(MonthlyClose)
        app.state_path = root / "state.json"
        app.state = {"records": {}, "aliases": {}}
        app.transactions = {transaction.key: transaction}
        app.accounts = {transaction.key: "Demo"}
        app.by_key = {document.key: document}
        app.statement_sources = {digest(source): source}
        app.errors, app.read_issues = [], []
        assign_numbers(app.state["records"], transactions)
        record = app.record(transaction.key)
        record.update(links=[document.key], note="INTERNAL_ONLY", advisor_note="Bitte prüfen.",
                      attachments=[add_attachment(app.attachment_folder(), supporting)])
        check(app.status_for(transaction.key) == "금액 대조 완료", "Reconciliation status")

        package = root / "Monatsabschluss.zip"
        app.write_package(package)
        with ZipFile(package) as result:
            check(result.testzip() is None, "Advisor ZIP integrity")
            overview = result.read("01_Abgleich.csv").decode("utf-8-sig")
            row = next(csv.DictReader(io.StringIO(overview), delimiter=";"))
            check(row["Belegnummer"] == "611-001" and row["Betrag"] == "58,39", "Stable ID and localized CSV")
            check("INTERNAL_ONLY" not in overview and "Bitte prüfen." in overview, "Memo separation")
            check(any(path.startswith("Anlagen/") for path in result.namelist()), "Supporting attachment")
            book = load_workbook(io.BytesIO(result.read("Abgleich.xlsx")))
            check(book["Abgleich"]["F2"].data_type == "n" and book["Abgleich"]["I2"].hyperlink, "Excel amounts and file links")

        import pymupdf
        statement = root / "Auszug.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 80), "25.11. 25.11.   MUSTER GMBH RE-TEST-001   - 58,39", fontname="cour", fontsize=9)
            pdf.save(statement)
        app.statement_sources[digest(statement)] = statement
        app.state["transaction_sources"] = {transaction.key: {digest(statement): statement.name}}
        numbered_package = root / "Monatsabschluss-pdf.zip"
        app.write_package(numbered_package)
        with ZipFile(numbered_package) as result:
            names = result.namelist()
            check(any(name.endswith("_Auszug.pdf") for name in names), "Original PDF statement in ZIP")
            check(any(name.endswith("_Auszug_nummeriert.pdf") for name in names), "Numbered PDF statement in ZIP")
            load_workbook(io.BytesIO(result.read("Abgleich.xlsx")))
        del app.statement_sources[digest(statement)]
        del app.state["transaction_sources"]

        app.state["session"] = {
            "folder": str(folder), "sources": {digest(source): {"path": str(source)}},
        }
        backup = root / "workspace.zip"
        count, missing = create_backup(backup, app.state, folder, app.attachment_folder(), {})
        check(count == 3 and not missing, "Portable backup")
        state, _, destination, missing = restore_workspace(backup, root)
        check(not missing and state["records"][transaction.key]["note"] == "INTERNAL_ONLY", "Private state restored")
        check(Path(state["statement_paths"][digest(source)]).is_relative_to(destination), "Restored paths relinked")
    print("PASS: sample CSV/PDF, matching, source location, Markdown, German ZIP/Excel, attachment and workspace restore")


if __name__ == "__main__":
    run()
