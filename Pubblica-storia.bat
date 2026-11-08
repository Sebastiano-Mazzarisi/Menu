@echo off
rem Pubblica SUBITO come STORIA della Pagina Facebook il video dei menu di oggi (di norma esce da sola alle 11:00).
cd /d "%~dp0"
python Menu.py --storia
echo.
pause
