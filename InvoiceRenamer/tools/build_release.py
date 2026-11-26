"""Generate the two Windows distribution files from this source bundle."""

import argparse
import base64
import hashlib
import io
from pathlib import Path
import textwrap
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
APP_FILES = (
    "Start.bat", "invoice_renamer.py", "monthly_close.py", "ui_shell.py", "ui_theme.py", "tax_documents.py", "workspace_backup.py",
    "advisor_workbook.py", "setup_ocr.py", "release_info.py", "VERSION", "Restore-PreviousVersion.cmd", "사용법.txt",
    "Beispiele/Belege/Rechnung_Rechnungstest-001.pdf", "Beispiele/Karte.csv",
)
RELEASE_NAME = "invoice-renamer-monthly-close-windows.zip"
INSTALLER_NAME = "InvoiceRenamer-Setup.cmd"


def build_release(destination):
    app = ROOT / "app"
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for member in APP_FILES:
            data = (app / member).read_bytes()
            if member.endswith(".py"):
                compile(data, member, "exec")
            info = ZipInfo(member, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    payload = buffer.getvalue()
    with ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip():
            raise ValueError("The Windows ZIP did not pass its integrity check.")
    template = (ROOT / "tools" / "windows-installer.cmd.in").read_text(encoding="utf-8")
    markers = {"@ZIP_PAYLOAD@": "\n".join(textwrap.wrap(base64.b64encode(payload).decode("ascii"), 76)),
               "@ZIP_SHA256@": hashlib.sha256(payload).hexdigest().upper()}
    for token, data in markers.items():
        if template.count(token) != 1:
            raise ValueError(f"Expected one installer template marker: {token}")
        template = template.replace(token, data)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / RELEASE_NAME).write_bytes(payload)
    (destination / INSTALLER_NAME).write_bytes(template.replace("\n", "\r\n").encode("utf-8"))
    return destination / RELEASE_NAME, destination / INSTALLER_NAME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for path in build_release(args.output):
        print(path)


if __name__ == "__main__":
    main()
