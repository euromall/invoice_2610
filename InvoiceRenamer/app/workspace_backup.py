import json
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from invoice_renamer import EVIDENCE_EXTENSIONS, STATEMENT_FOLDER, digest, folder_base, folder_settings, matched_pattern


ATTACHMENT_EXTENSIONS = {".pdf", ".xml", ".png", ".jpg", ".jpeg", ".eml", ".txt", ".csv"}
MANIFEST = "InvoiceRenamer-Backup.json"


def attachment_path(folder, item):
    name = item["name"]
    suffix = Path(name).suffix.lower()
    key = item["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", key) or suffix not in ATTACHMENT_EXTENSIONS:
        raise ValueError("첨부 파일 정보가 올바르지 않아요.")
    return folder / (key + suffix)


def add_attachment(folder, source):
    item = {"name": source.name, "sha256": digest(source)}
    target = attachment_path(folder, item)
    folder.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as output:
            temporary = Path(output.name)
            try:
                with source.open("rb") as data:
                    shutil.copyfileobj(data, output)
            except OSError:
                output.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            if digest(temporary) != item["sha256"]:
                raise ValueError("첨부 파일을 복사하는 동안 원본이 변경됐어요.")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if digest(target) != item["sha256"]:
        raise ValueError("저장된 첨부 파일이 변경됐어요.")
    return item


def create_backup(target, state, evidence_folder, attachments, edits):
    sources = dict(state.get("statement_paths", {}))
    session = state.get("session", {})
    for key, spec in session.get("sources", {}).items():
        sources.setdefault(key, spec["path"])
    files, missing = {}, []
    mappings = {"statements": {}, "exports": {}}

    def include(path, member, checksum=None):
        if not path.is_file() or (checksum is not None and digest(path) != checksum):
            missing.append(member)
            return False
        files[member] = path
        return True

    if evidence_folder and evidence_folder.is_dir():
        root = evidence_folder.resolve()
        choice = folder_settings()
        statement_names = {STATEMENT_FOLDER, choice["statements"]["name"]}
        for path in evidence_folder.rglob("*"):
            if path.suffix.lower() in EVIDENCE_EXTENSIONS | {".md", ".xml"} and path.is_file() and not path.is_symlink():
                if path.resolve().is_relative_to(root) and path.relative_to(evidence_folder).parts[0] not in statement_names:
                    include(path, f"Belege/{path.relative_to(evidence_folder).as_posix()}")
        outside = folder_base("matched", evidence_folder, choice)  # v1.14: 매칭완료 folders kept in another place
        if choice["matched"]["location"] and outside.is_dir() and not outside.resolve().is_relative_to(root):
            for folder in outside.iterdir():
                if folder.is_dir() and re.fullmatch(matched_pattern(choice), folder.name):
                    for path in folder.rglob("*"):
                        if path.suffix.lower() in EVIDENCE_EXTENSIONS | {".md", ".xml"} and path.is_file() and not path.is_symlink():
                            include(path, f"Belege/{path.relative_to(outside).as_posix()}")
    else:
        missing.append("Belege/ (인보이스 폴더를 열지 않음)")
    for index, (key, path) in enumerate(sources.items(), 1):
        source = Path(path)
        member = f"Kontoauszuege/{index:04d}_{source.name}"
        if include(source, member, key):
            mappings["statements"][key] = member
    known_sources = {key: name for entries in state.get("transaction_sources", {}).values() for key, name in entries.items()}
    for key in known_sources.keys() - sources.keys():
        missing.append(f"Kontoauszuege/{known_sources[key]} (저장 경로 없음)")
    for index, path in enumerate(dict.fromkeys(entry["path"] for versions in state.get("exports", {}).values() for entry in versions), 1):
        source = Path(path)
        member = f"Abschluesse/{index:04d}_{source.name}"
        if include(source, member):
            mappings["exports"][path] = member
    for record in state["records"].values():
        for item in record.get("attachments", []):
            source = attachment_path(attachments, item)
            member = f"Anlagen/{source.name}"
            if member not in files and member not in missing:
                include(source, member, item["sha256"])

    with ZipFile(target, "x", ZIP_DEFLATED) as archive:
        for member, path in files.items():
            archive.write(path, member)
        archive.writestr(MANIFEST, json.dumps({
            "format": "InvoiceRenamerWorkspace", "version": 1, "state": state, "edits": edits,
            "files": list(files), "mappings": mappings, "missing": missing,
        }, ensure_ascii=False, indent=2).encode("utf-8"))
    return len(files), missing


def safe_member(name):
    parts = PurePosixPath(name).parts
    if not parts or any(
        part in (".", "..") or part.endswith((" ", ".")) or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
        or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I)
        for part in parts
    ) or "/".join(parts) != name or parts[0] not in ("Belege", "Kontoauszuege", "Abschluesse", "Anlagen"):
        raise ValueError("백업에 안전하지 않은 파일 경로가 있어요.")
    return parts


def restore_workspace(source, parent):
    with ZipFile(source) as archive:
        data = json.loads(archive.read(MANIFEST))
        if data["format"] != "InvoiceRenamerWorkspace" or data["version"] != 1:
            raise ValueError("전체 작업 백업 형식이 아니에요.")
        state, edits = data["state"], data["edits"]
        if not isinstance(state.get("records"), dict) or not isinstance(state.get("aliases"), dict) or not isinstance(edits, dict):
            raise ValueError("백업 기록 형식이 올바르지 않아요.")
        names = data["files"]
        if (len({name.casefold() for name in names}) != len(names) or len(archive.namelist()) != len(names) + 1
                or set(archive.namelist()) != {MANIFEST, *names}):
            raise ValueError("백업 목록이 파일과 일치하지 않아요.")
        for name in names:
            safe_member(name)
        statements, exports = data["mappings"]["statements"], data["mappings"]["exports"]
        if any(name not in names or not name.startswith("Kontoauszuege/") for name in statements.values()) or any(
            name not in names or not name.startswith("Abschluesse/") for name in exports.values()
        ):
            raise ValueError("백업 경로 연결이 올바르지 않아요.")
        destination = Path(tempfile.mkdtemp(prefix="InvoiceRenamer-", dir=parent))
        try:
            for name in names:
                path = destination.joinpath(*safe_member(name))
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as data_file, path.open("xb") as output:
                    shutil.copyfileobj(data_file, output)
            evidence = destination / "Belege"
            evidence.mkdir(exist_ok=True)
            attachments = destination / "Anlagen"
            attachments.mkdir(exist_ok=True)
            state["statement_paths"] = {key: str(destination / value) for key, value in statements.items()}
            session = state.get("session", {})
            session["sources"] = {
                key: {**spec, "path": state["statement_paths"][key]}
                for key, spec in session.get("sources", {}).items() if key in statements
            }
            session["folder"] = str(evidence)
            state["session"] = session
            state["attachment_root"] = str(attachments)
            for versions in state.get("exports", {}).values():
                for entry in versions:
                    name = exports.get(entry["path"])
                    entry["path"] = str(destination / name) if name else str(destination / "Abschluesse" / Path(entry["path"]).name)
        except Exception:
            shutil.rmtree(destination)
            raise
    return state, edits, destination, data.get("missing", [])
