"""Display version information and export a report without user data."""

from collections import deque
from datetime import datetime, timezone
import json
import platform
from pathlib import Path
import sys


VERSION = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="ascii").strip()
STAGES = {
    "pdf_read": "인보이스를 읽는 중 오류",
    "file_move": "파일을 정리하는 중 오류",
    "markdown": "마크다운을 저장하는 중 오류",
    "statement": "명세서를 가져오는 중 오류",
    "state": "작업 기록을 저장하는 중 오류",
    "zip": "세무사용 ZIP을 만드는 중 오류",
    "gui": "화면 동작 중 예상하지 못한 오류",
}
ERROR_TYPES = {
    "OSError", "PermissionError", "FileNotFoundError", "FileExistsError", "RuntimeError", "ValueError",
    "TypeError", "KeyError", "TclError", "FileDataError", "EmptyFileError", "BadZipFile", "Error",
    "IndexError", "AttributeError", "NameError",
}
_EVENTS = deque(maxlen=50)


def record_error(stage, error):
    label = STAGES.get(stage, STAGES["gui"])
    kind = type(error).__name__
    code = getattr(error, "errno", None)
    event = {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": label,
        "error_type": kind if kind in ERROR_TYPES else "OtherError",
    }
    if isinstance(code, int):
        event["errno"] = code
    _EVENTS.append(event)


def export_diagnostics(path):
    report = {
        "version": VERSION,
        "platform": platform.system(),
        "python": ".".join(map(str, sys.version_info[:3])),
        "events_current_session": list(_EVENTS),
    }
    with path.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
