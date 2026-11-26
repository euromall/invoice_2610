@echo off
chcp 65001 >nul
rem Builds dist\InvoiceRenamer.exe on Windows. Needs Python 3.10+ with Tcl/Tk from python.org.
cd /d "%~dp0.."
py -3 -m pip install -r requirements.txt pyinstaller==6.11.1 || goto :err
py -3 tools\smoke_test.py || goto :err
py -3 tools\regression_test.py || goto :err
py -3 app\setup_ocr.py || goto :err
py -3 -m PyInstaller --noconfirm --onefile --windowed --name InvoiceRenamer --paths app ^
  --hidden-import monthly_close --hidden-import tax_documents --hidden-import advisor_workbook ^
  --hidden-import workspace_backup --hidden-import release_info --hidden-import ui_shell --hidden-import ui_theme ^
  --splash tools\splash.png ^
  --add-data "app\VERSION;." --add-data "app\사용법.txt;." --add-data "app\사용법.html;." --add-data "app\Beispiele;Beispiele" ^
  --add-data "app\tessdata;tessdata" app\invoice_renamer.py || goto :err
echo.
echo Done: dist\InvoiceRenamer.exe
pause
exit /b 0
:err
echo Build failed. See the messages above.
pause
exit /b 1
