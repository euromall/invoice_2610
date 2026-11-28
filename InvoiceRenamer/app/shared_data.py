"""v1.15 work records on several computers (Dropbox, OneDrive, a network drive …).

The records of a company (matching, notes, review, settings, learned layouts) can live in the work folder,
in ‘_월마감 기록’, instead of %LOCALAPPDATA%. Every computer that uses the same synced work folder then sees the
same records. At start LOCALAPPDATA is pointed at that folder, so every module keeps its usual paths.

What stays on each computer: the app password (Windows credential manager), the stored PDF text (text-cache,
it is rebuilt where missing), OCR data and the list of companies. A small ‘in use’ file warns when a second
computer opens the same records, because Dropbox would otherwise keep two diverging copies."""

import getpass
import json
import os
import platform
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

FOLDER_NAME = "_월마감 기록"
POINTER = "shared-data.json"           # in the computer's own data folder: {"root": "<work>\\_월마감 기록"}
LOCAL_ENV = "INVOICE_RENAMER_LOCAL"    # the computer's own LOCALAPPDATA (company folder) while shared records are used
LEARNED_ENV = "INVOICE_RENAMER_LEARNED"  # where learned layouts and seller patterns are kept while sharing
LOCK = "in-use.json"
LOCK_FRESH = 15 * 60                   # seconds: a lock older than this is left over from a crash or a lost sync
KEEP_LOCAL = {"text-cache", "tessdata", "profiles", "logs", "profiles.json", POINTER, LOCK}
STATUS = {"state": "local", "root": "", "note": ""}  # what activate() found, for the window to show


def records_root(work):
    """The folder used as LOCALAPPDATA when the records live in the work folder."""
    return Path(work) / FOLDER_NAME


def data_folder(root):
    return Path(root) / "InvoiceRenamer"


def has_records(work):
    return (data_folder(records_root(work)) / "monthly-close.json").is_file() or \
        (data_folder(records_root(work)) / "settings.json").is_file()


def active():
    return bool(os.environ.get(LOCAL_ENV))


def local_root():
    """The computer's own LOCALAPPDATA (of the active company), also while shared records are used."""
    return Path(os.environ.get(LOCAL_ENV) or os.environ.get("LOCALAPPDATA", Path.home()))


def pointer_path(root=None):
    return data_folder(root or local_root()) / POINTER


def read_pointer(root=None):
    try:
        data = json.loads(pointer_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return data.get("root", "") if isinstance(data, dict) else ""


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def activate(root):
    """Called at start with the company's own LOCALAPPDATA already set to root. When this computer uses shared
    records and their folder is there, LOCALAPPDATA is pointed at it. Returns STATUS."""
    STATUS.update(state="local", root="", note="")
    shared = read_pointer(root)
    if not shared:
        return STATUS
    if not data_folder(shared).is_dir():
        STATUS.update(state="missing", root=shared,
                      note=f"공유 작업 기록 폴더를 찾지 못했어요: {shared}\n이 컴퓨터의 기록으로 열었어요. "
                           "Dropbox가 켜져 있고 동기화가 끝났는지 확인하세요.")
        return STATUS
    os.environ[LOCAL_ENV] = str(root)
    os.environ["LOCALAPPDATA"] = str(shared)
    os.environ[LEARNED_ENV] = str(shared)
    try:
        rebase(shared, Path(shared).parent)
    except OSError as error:
        STATUS["note"] = f"다른 컴퓨터의 폴더 경로를 이 컴퓨터 경로로 바꾸지 못했어요: {error}"
    STATUS.update(state="shared", root=str(shared))
    return STATUS


def deactivate_for_practice():
    for name in (LOCAL_ENV, LEARNED_ENV):
        os.environ.pop(name, None)


# Paths written on another computer ---------------------------------------------
def changed_prefix(old, new):
    """(old prefix, new prefix) of two paths of the same synced folder on two computers, by their common end:
    C:\\Users\\A\\Dropbox\\월마감 and D:\\Dropbox\\월마감 → (C:\\Users\\A, D:). None when nothing changed."""
    old_parts, new_parts = list(Path(old).parts), list(Path(new).parts)
    if [part.casefold() for part in old_parts] == [part.casefold() for part in new_parts]:
        return None
    same = 0
    while (same < min(len(old_parts), len(new_parts)) - 1
           and old_parts[-1 - same].casefold() == new_parts[-1 - same].casefold()):
        same += 1
    old_prefix = str(Path(*old_parts[:len(old_parts) - same])) if same else str(Path(old))
    new_prefix = str(Path(*new_parts[:len(new_parts) - same])) if same else str(Path(new))
    return old_prefix, new_prefix


def rebase(root, work):
    """The records were last saved on a computer where the work folder had another path (other user name,
    other drive): change the stored paths to this computer's. Only the top-level JSON files are changed."""
    folder = data_folder(root)
    settings_file = folder / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    old = settings.get("work_folder") if isinstance(settings, dict) else ""
    change = changed_prefix(old, work) if old else None
    if change:
        old_prefix, new_prefix = (prefix.rstrip("\\/") for prefix in change)
        replacements = []
        for separator in ("\\", "/"):
            replacements.append((old_prefix + separator, new_prefix + os.sep))
            replacements.append((json.dumps(old_prefix + separator)[1:-1], json.dumps(new_prefix + os.sep)[1:-1]))
        for path in folder.glob("*.json"):
            if path.name in (LOCK, POINTER):
                continue
            text = path.read_text(encoding="utf-8")
            changed = text
            for before, after in replacements:
                changed = changed.replace(before, after)
            if changed != text:
                json.loads(changed)  # never write a broken file
                path.write_text(changed, encoding="utf-8")
    if not isinstance(settings, dict):
        settings = {}
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else settings
    except (OSError, ValueError):
        pass
    settings["work_folder"] = str(work)
    write_json(settings_file, settings)


# Moving the records ------------------------------------------------------------
def _copy_records(source, target):
    """Copy a data folder without the parts each computer keeps for itself. Returns the number of files."""
    count = 0
    for item in Path(source).iterdir():
        if item.name in KEEP_LOCAL or item.suffix == ".tmp":
            continue
        destination = Path(target) / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
            count += sum(1 for path in item.rglob("*") if path.is_file())
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            count += 1
    return count


def start_sharing(work, learned_from):
    """Copy this computer's records into the work folder and use them from the next start on.
    learned_from: the folder with learned layouts and seller patterns (shared by all companies)."""
    root = records_root(work)
    if has_records(work):
        raise ValueError("이 작업 폴더에는 이미 공유 작업 기록이 있어요. ‘이 폴더의 공유 기록 쓰기’를 고르세요.")
    target = data_folder(root)
    target.mkdir(parents=True, exist_ok=True)
    count = _copy_records(data_folder(local_root()), target)
    for name in ("learned-formats.json", "seller-patterns.json"):
        source = Path(learned_from) / name
        if source.is_file() and not (target / name).exists():
            shutil.copy2(source, target / name)
    write_json(pointer_path(), {"root": str(root), "since": datetime.now().isoformat(timespec="seconds")})
    return count


def join(work):
    """Use the shared records already in the work folder (made on another computer) from the next start on."""
    if not has_records(work):
        raise ValueError("이 작업 폴더에 공유 작업 기록이 없어요.")
    write_json(pointer_path(), {"root": str(records_root(work)), "since": datetime.now().isoformat(timespec="seconds")})


def stop_sharing():
    """This computer keeps a copy of the shared records as its own and stops using the shared folder.
    The shared folder stays for the other computers."""
    shared = read_pointer()
    local = data_folder(local_root())
    if shared and data_folder(shared).is_dir():
        if local.is_dir():
            keep = local / "backups" / f"before-stop-sharing-{datetime.now():%Y%m%d-%H%M%S}"
            keep.mkdir(parents=True, exist_ok=True)
            for item in local.glob("*.json"):
                if item.name not in (POINTER, "profiles.json"):
                    shutil.copy2(item, keep / item.name)
        _copy_records(data_folder(shared), local)
    try:
        pointer_path().unlink()
    except OSError:
        pass


# Two computers at once --------------------------------------------------------
def this_computer():
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        user = ""
    return f"{platform.node() or '이 컴퓨터'}" + (f" ({user})" if user else "")


def lock_path():
    return data_folder(os.environ.get("LOCALAPPDATA", "")) / LOCK


def other_user(now=None):
    """{"computer", "since", "beat"} of another computer that uses the records now, or None."""
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("computer") == this_computer():
        return None
    if (now or time.time()) - float(data.get("beat", 0) or 0) > LOCK_FRESH:
        return None
    return data


def hold_lock():
    """Write or refresh this computer's ‘in use’ mark (call again every few minutes)."""
    try:
        previous = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    since = previous.get("since") if isinstance(previous, dict) and previous.get("computer") == this_computer() else None
    write_json(lock_path(), {"computer": this_computer(), "since": since or datetime.now().strftime("%Y-%m-%d %H:%M"),
                             "beat": time.time()})


def release_lock():
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("computer") == this_computer():
            lock_path().unlink()
    except (OSError, ValueError):
        pass


CONFLICT = re.compile(r"conflicted copy|충돌|Konflikt|in Konflikt stehende", re.IGNORECASE)


def conflicts():
    """Files Dropbox or OneDrive made because two computers changed the same record file."""
    folder = data_folder(os.environ.get("LOCALAPPDATA", ""))
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.glob("*.json") if CONFLICT.search(path.name))
