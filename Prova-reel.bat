@echo off
rem Crea il REEL di oggi (video verticale 9:16 con i menu) e lo apre, SENZA pubblicare.
cd /d "%~dp0"
python Menu.py --reel-prova
echo.
pause
