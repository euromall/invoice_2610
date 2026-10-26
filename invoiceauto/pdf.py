"""Saves finished workbooks as PDF, with Microsoft Excel when it is installed, otherwise LibreOffice."""
import os
import shutil
import subprocess
import sys
from typing import List, Tuple


def _excel_export(pairs: List[Tuple[str, str]]):
    import win32com.client  # pywin32, only on Windows
    excel = win32com.client.DispatchEx('Excel.Application')
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        for xlsx, pdf in pairs:
            wb = excel.Workbooks.Open(os.path.abspath(xlsx), ReadOnly=True)
            try:
                wb.ExportAsFixedFormat(0, os.path.abspath(pdf))   # 0 = xlTypePDF
            finally:
                wb.Close(SaveChanges=False)
    finally:
        excel.Quit()


def _soffice():
    for name in ('soffice', 'libreoffice'):
        p = shutil.which(name)
        if p:
            return p
    for p in (r'C:\Program Files\LibreOffice\program\soffice.exe',
              r'C:\Program Files (x86)\LibreOffice\program\soffice.exe'):
        if os.path.exists(p):
            return p
    return None


def _libreoffice_export(pairs: List[Tuple[str, str]]):
    exe = _soffice()
    if not exe:
        raise RuntimeError('Excel 또는 LibreOffice가 없어 PDF를 만들 수 없습니다.')
    for xlsx, pdf in pairs:
        outdir = os.path.dirname(os.path.abspath(pdf))
        subprocess.run([exe, '--headless', '--convert-to', 'pdf', '--outdir', outdir, os.path.abspath(xlsx)],
                       check=True, capture_output=True, timeout=120)
        produced = os.path.join(outdir, os.path.splitext(os.path.basename(xlsx))[0] + '.pdf')
        if os.path.abspath(produced) != os.path.abspath(pdf):
            os.replace(produced, pdf)


def export(pairs: List[Tuple[str, str]]) -> str:
    """pairs: (xlsx path, pdf path). Returns the tool that was used."""
    if not pairs:
        return ''
    if sys.platform == 'win32':
        try:
            _excel_export(pairs)
            return 'Excel'
        except Exception:
            pass
    _libreoffice_export(pairs)
    return 'LibreOffice'
