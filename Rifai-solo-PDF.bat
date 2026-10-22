@echo off
rem Rifa' SUBITO soltanto i PDF (di ogni locale e menu.pdf con tutti i locali) e li pubblica sul
rem sito dei PDF. Il video e il post di Facebook NON vengono toccati (per quelli: Rifai-PDF.bat).
cd /d "%~dp0"
title Menu - rifaccio solo i PDF
python Menu.py --solo-pdf
echo.
pause
