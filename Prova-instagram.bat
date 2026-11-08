@echo off
rem Controlla che Instagram "Menu rosticcerie" sia collegato al programma (account e permessi), SENZA pubblicare.
cd /d "%~dp0"
python Menu.py --instagram-prova
echo.
pause
