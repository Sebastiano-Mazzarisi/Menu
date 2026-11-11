@echo off
rem Crea la tabella "Chi pubblica il menu" (Nome, Chiusura, %% pubblicazione, Orario medio) e la apre, SENZA pubblicare.
cd /d "%~dp0"
python Menu.py --frequenza-prova
echo.
pause
