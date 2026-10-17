@echo off
rem Pubblica SUBITO su Facebook il riassunto di oggi (se oggi non e' gia' stato pubblicato):
rem prima ricontrolla TUTTE le rosticcerie e aggiorna il sito, poi crea immagine e testo e li pubblica.
cd /d "%~dp0"
python Menu.py --riassunto
echo.
pause
