@echo off
rem Rifa' SUBITO i PDF di ogni locale, il PDF con tutti i locali (menu.pdf) e il video con le
rem stesse pagine (video.mp4) e li pubblica sul sito dei PDF (Menu.py lo fa da solo ogni 30 minuti
rem se qualcosa cambia).
cd /d "%~dp0"
title Menu - rifaccio PDF e video
python Menu.py --completo
echo.
pause
