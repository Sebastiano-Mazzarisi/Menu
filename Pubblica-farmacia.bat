@echo off
rem Pubblica subito la farmacia del prossimo fine settimana sulla Pagina Facebook.
cd /d "%~dp0"
python Menu.py --farmacia
pause
