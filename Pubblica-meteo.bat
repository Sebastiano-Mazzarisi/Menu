@echo off
rem Pubblica SUBITO su Facebook il post del METEO di oggi (toglie dalla Pagina quello precedente).
cd /d "%~dp0"
python Menu.py --meteo
echo.
pause
