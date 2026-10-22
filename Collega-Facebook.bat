@echo off
rem Collegamento ufficiale con la Pagina Facebook "Menu rosticcerie".
rem Chiede UN solo dato: il codice EAA... (meglio quello "che non scadra' mai" del Debugger dei token,
rem dopo "Estendi token d'accesso"). Il codice della Pagina finisce in dati\facebook.json (resta solo sul PC).
cd /d "%~dp0"
python Menu.py --facebook-collega
echo.
pause
