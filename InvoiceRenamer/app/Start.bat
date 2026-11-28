@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
if defined INVOICE_SETUP_PYTHON goto managedpython
py -3 -m venv .venv
if not errorlevel 1 goto dependencies
python -m venv .venv
if errorlevel 1 goto failed
goto dependencies
:managedpython
"%INVOICE_SETUP_PYTHON%" -m venv .venv
if errorlevel 1 goto failed
:dependencies
".venv\Scripts\python.exe" -c "import pymupdf, tkinter, openpyxl" >nul 2>&1
if not errorlevel 1 goto launch
".venv\Scripts\python.exe" -m pip install PyMuPDF==1.26.6 openpyxl==3.1.5
if errorlevel 1 goto failed
:launch
".venv\Scripts\python.exe" setup_ocr.py
if errorlevel 1 echo [OCR] 언어 파일을 받지 못했습니다. 인터넷에 연결한 뒤 다시 실행하세요.
".venv\Scripts\python.exe" invoice_renamer.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo [실행 오류] 압축을 모두 풀었는지, Python 3.10 이상과 Tcl/Tk가 설치되어 있는지 확인하세요.
echo 첫 실행은 인터넷이 필요합니다. 위쪽 오류 내용도 확인해 주세요.
pause
exit /b 1
