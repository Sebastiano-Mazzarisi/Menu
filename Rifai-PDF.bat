@echo off
rem Rifa' SUBITO i PDF di ogni locale e il PDF con tutti i locali (menu.pdf) e li pubblica
rem sul sito dei PDF (lo stesso che Menu.py fa da solo ogni 30 minuti se qualcosa cambia).
cd /d "%~dp0"
title Menu - rifaccio i PDF
python Menu.py --completo
echo.
pause
