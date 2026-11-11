@echo off
rem Pubblica SUBITO su Facebook la tabella "Chi pubblica il menu" (secondo post) e rimette in ordine i primi post.
cd /d "%~dp0"
python Menu.py --frequenza
echo.
pause
