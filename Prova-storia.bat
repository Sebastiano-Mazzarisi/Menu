@echo off
rem Crea il video della STORIA di oggi (verticale 9:16, max 58 secondi) e lo apre, SENZA pubblicare.
cd /d "%~dp0"
python Menu.py --storia-prova
echo.
pause
