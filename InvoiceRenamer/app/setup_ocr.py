"""Install OCR language files for the Tesseract engine built into PyMuPDF."""

import shutil
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen


SOURCE = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0"
LANGUAGES = {"deu": "German", "eng": "English"}


def install_languages(folder, download=urlopen):
    folder.mkdir(parents=True, exist_ok=True)
    for code, label in LANGUAGES.items():
        target = folder / f"{code}.traineddata"
        if target.is_file():
            continue
        print(f"Downloading {label} OCR language data...", flush=True)
        temporary = None
        try:
            with download(f"{SOURCE}/{target.name}", timeout=20) as response:
                with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
                    temporary = Path(output.name)
                    shutil.copyfileobj(response, output)
            if temporary.stat().st_size < 100_000:
                raise OSError("The downloaded language file is incomplete.")
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        install_languages(Path(__file__).resolve().parent / "tessdata")
    except OSError as error:
        print(f"OCR setup failed: {error}", file=sys.stderr)
        sys.exit(1)
