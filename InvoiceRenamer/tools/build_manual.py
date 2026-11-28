"""Build app/사용법.html from tools/manual/manual.html with the screenshots embedded."""

import base64
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "manual"


def build():
    html = (SOURCE / "manual.html").read_text(encoding="utf-8")
    html = html.replace("@VERSION@", (ROOT / "app" / "VERSION").read_text(encoding="utf-8").strip())

    def image(match):
        data = base64.b64encode((SOURCE / f"{match[1]}.jpg").read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{data}"

    html = re.sub(r"@IMG:(\w+)@", image, html)
    target = ROOT / "app" / "사용법.html"
    target.write_text(html, encoding="utf-8")
    return target


if __name__ == "__main__":
    print(build())
