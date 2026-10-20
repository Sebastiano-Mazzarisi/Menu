@echo off
rem Rifa' SUBITO i PDF di ogni locale, il PDF completo (menu.pdf) e il video e li pubblica
rem sul sito dei PDF (lo stesso che Menu.py fa da solo ogni 30 minuti se qualcosa cambia).
cd /d "%~dp0"
title Menu - rifaccio PDF e video
python Menu.py --completo
echo.
pause
