@echo off
rem Run from source in the program folder (with Base, db, ShippingInvoice)
cd /d "%~dp0"
py -3 -m pip install -q -r requirements.txt
py -3 main.py "%CD%"
