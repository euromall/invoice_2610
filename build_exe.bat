@echo off
rem Builds dist\InvoiceAuto.exe. Needs Python 3.12 from python.org.
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller==6.11.1 || goto :err
py -3 -m PyInstaller --noconfirm --onefile --windowed --name InvoiceAuto main.py || goto :err
echo.
echo Done: copy dist\InvoiceAuto.exe into the program folder (Base, db, ShippingInvoice).
pause
exit /b 0
:err
echo Build failed. See the messages above.
pause
exit /b 1
