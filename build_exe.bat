@echo off
rem Builds dist\InvoiceAuto\ (InvoiceAuto.exe + _internal). Needs Python 3.12 from python.org.
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt pyinstaller==6.11.1 || goto :err
py -3 -m PyInstaller --noconfirm --onedir --windowed --splash assets\splash.png --name InvoiceAuto main.py || goto :err
copy /y "InvoiceAuto*.bat" dist\ >nul
echo.
echo Done: copy the folder dist\InvoiceAuto into the work folder (next to Base, db, ShippingInvoice),
echo together with the InvoiceAuto start .bat file.
pause
exit /b 0
:err
echo Build failed. See the messages above.
pause
exit /b 1
