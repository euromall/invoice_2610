"""새 버전 받기: the latest GitHub release of the folder build replaces the InvoiceAuto folder.

Only for the folder build (…\\<work folder>\\InvoiceAuto\\InvoiceAuto.exe). The swap is done by a small batch
file after the program has closed, because Windows does not let a running exe be replaced.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from ._version import VERSION

REPO = 'euromall/invoice_2610'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'InvoiceAuto.zip'


def current_version() -> str:
    return VERSION


def install_dir():
    """The InvoiceAuto folder of a folder build, else None (running from source or the one-file exe)."""
    if not getattr(sys, 'frozen', False):
        return None
    here = os.path.dirname(sys.executable)
    if os.path.isdir(os.path.join(here, '_internal')) and os.path.isdir(os.path.join(os.path.dirname(here), 'Base')):
        return here
    return None


def _num(v):
    try:
        return int(str(v).lstrip('v').split('.')[0])
    except ValueError:
        return 0


def latest_release(timeout=10):
    """(version, download_url, notes) of the latest release, or None."""
    req = urllib.request.Request(API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'InvoiceAuto'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    url = next((a['browser_download_url'] for a in data.get('assets', []) if a.get('name') == ASSET), None)
    if not url:
        return None
    return data.get('tag_name', ''), url, data.get('body') or ''


def is_newer(remote, local) -> bool:
    return _num(remote) > _num(local)


def download_and_stage(url, progress=None) -> str:
    """Downloads and extracts the release; returns the folder holding the new InvoiceAuto\\."""
    work = tempfile.mkdtemp(prefix='InvoiceAuto_update_')
    zpath = os.path.join(work, ASSET)
    req = urllib.request.Request(url, headers={'User-Agent': 'InvoiceAuto'})
    with urllib.request.urlopen(req, timeout=60) as r, open(zpath, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    out = os.path.join(work, 'new')
    with zipfile.ZipFile(zpath) as z:
        z.extractall(out)
    if not os.path.isfile(os.path.join(out, 'InvoiceAuto', 'InvoiceAuto.exe')):
        raise RuntimeError('받은 파일에 InvoiceAuto\\InvoiceAuto.exe 가 없습니다.')
    return out


def start_swap(staged, target):
    """Writes and starts the batch file that waits for this program to close, keeps the current folder as
    InvoiceAuto_prev, moves the new one in and starts it. The caller quits right after."""
    parent = os.path.dirname(target)
    prev = os.path.join(parent, 'InvoiceAuto_prev')
    new = os.path.join(staged, 'InvoiceAuto')
    bat = os.path.join(os.path.dirname(staged), 'swap.bat')
    pid = os.getpid()
    lines = [
        '@echo off',
        'chcp 65001 >nul',
        ':wait',
        f'tasklist /fi "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        f'if exist "{prev}" rmdir /s /q "{prev}"',
        f'move "{target}" "{prev}" >nul || goto fail',
        f'move "{new}" "{target}" >nul || (move "{prev}" "{target}" >nul & goto fail)',
        f'start "" "{os.path.join(target, "InvoiceAuto.exe")}"',
        'exit /b 0',
        ':fail',
        'echo 업데이트에 실패했습니다. 프로그램 폴더를 확인해 주세요.',
        'pause',
    ]
    with open(bat, 'w', encoding='utf-8') as f:
        f.write('\r\n'.join(lines) + '\r\n')
    subprocess.Popen(['cmd', '/c', bat], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
