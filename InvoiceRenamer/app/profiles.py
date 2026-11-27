"""v1.14 company profiles: each business (Euromall GmbH, …) keeps its own work folder, statements, records and settings.

The default company uses %LOCALAPPDATA%\\InvoiceRenamer as before (nothing changes for existing data). Another company
uses %LOCALAPPDATA%\\InvoiceRenamer\\profiles\\<id>\\InvoiceRenamer: at start LOCALAPPDATA is pointed there, so every
module keeps its usual paths. Learned invoice layouts and the stored PDF text stay shared (shared_folder)."""

import json
import os
import re
from pathlib import Path

BASE_ENV = "INVOICE_RENAMER_BASE"  # the real LOCALAPPDATA while another company is active
DEFAULT_ID = ""


def base_folder():
    return Path(os.environ.get(BASE_ENV) or os.environ.get("LOCALAPPDATA", Path.home())) / "InvoiceRenamer"


def shared_folder():
    """Data all companies share (learned layouts, stored PDF text): always the default company's folder."""
    return base_folder()


def profiles_path():
    return base_folder() / "profiles.json"


def load_profiles():
    """{"active": id, "profiles": [{"id", "name"}]}; the default company has id "" and is always first."""
    try:
        data = json.loads(profiles_path().read_text(encoding="utf-8")) if profiles_path().is_file() else {}
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    profiles = [entry for entry in data.get("profiles", []) if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and isinstance(entry.get("name"), str)]
    if not any(entry["id"] == DEFAULT_ID for entry in profiles):
        profiles.insert(0, {"id": DEFAULT_ID, "name": "기본 회사"})
    active = data.get("active") if any(entry["id"] == data.get("active") for entry in profiles) else DEFAULT_ID
    return {"active": active, "profiles": profiles}


def save_profiles(data):
    path = profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def profile_root(profile_id):
    """The folder used as LOCALAPPDATA for a company."""
    return base_folder().parent if profile_id == DEFAULT_ID else base_folder() / "profiles" / profile_id


def activate_at_start():
    """Point LOCALAPPDATA at the active company's folder (call once before the window opens). Returns its name."""
    data = load_profiles()
    if BASE_ENV not in os.environ:
        os.environ[BASE_ENV] = os.environ.get("LOCALAPPDATA", str(Path.home()))
    root = profile_root(data["active"])
    root.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(root)
    return active_name(data)


def active_name(data=None):
    data = data or load_profiles()
    return next(entry["name"] for entry in data["profiles"] if entry["id"] == data["active"])


def several():
    return len(load_profiles()["profiles"]) > 1


def add_profile(name, work_folder):
    """A new company with its own work folder. Returns its id."""
    name = " ".join(name.split())[:60]
    if not name:
        raise ValueError("회사 이름을 적어 주세요.")
    data = load_profiles()
    if any(entry["name"].casefold() == name.casefold() for entry in data["profiles"]):
        raise ValueError("같은 이름의 회사가 이미 있어요.")
    used = {entry["id"] for entry in data["profiles"]}
    number = 1
    while f"c{number}" in used:
        number += 1
    profile_id = f"c{number}"
    folder = profile_root(profile_id) / "InvoiceRenamer"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "settings.json").write_text(json.dumps({"work_folder": str(work_folder)}, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    data["profiles"].append({"id": profile_id, "name": name})
    save_profiles(data)
    return profile_id


def rename_profile(profile_id, name):
    name = " ".join(name.split())[:60]
    data = load_profiles()
    if not name or any(entry["name"].casefold() == name.casefold() and entry["id"] != profile_id for entry in data["profiles"]):
        raise ValueError("비어 있거나 이미 있는 이름이에요.")
    for entry in data["profiles"]:
        if entry["id"] == profile_id:
            entry["name"] = name
    save_profiles(data)


def set_active(profile_id):
    data = load_profiles()
    if not any(entry["id"] == profile_id for entry in data["profiles"]):
        raise ValueError("없는 회사예요.")
    data["active"] = profile_id
    save_profiles(data)


def default_work_folder_for(name):
    documents = Path.home() / "Documents"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name).strip(" .") or "회사"
    return (documents if documents.is_dir() else Path.home()) / f"월마감_{safe}"
