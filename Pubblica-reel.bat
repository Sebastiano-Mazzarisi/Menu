@echo off
rem Pubblica SUBITO su Facebook il REEL di oggi (toglie dalla Pagina quello precedente).
cd /d "%~dp0"
python Menu.py --reel
echo.
pause
