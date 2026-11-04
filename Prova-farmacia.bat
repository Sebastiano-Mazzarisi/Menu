@echo off
rem Crea e apre la schermata della farmacia del prossimo fine settimana, senza pubblicare.
cd /d "%~dp0"
python Menu.py --farmacia-prova
pause
