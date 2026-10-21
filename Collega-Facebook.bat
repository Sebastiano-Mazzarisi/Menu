@echo off
rem Collegamento ufficiale con la Pagina Facebook "Menu rosticcerie".
rem Chiede UN solo dato: il codice EAA... (meglio quello "che non scadra' mai" del Debugger dei token,
rem dopo "Estendi token d'accesso"). Il codice della Pagina finisce in dati\facebook.json (resta solo sul PC).
rem Subito dopo aggiunge, se mancano, i commenti con i link ai post "I menu in pdf" e "I menu in video".
cd /d "%~dp0"
python Menu.py --facebook-collega
if errorlevel 1 goto fine
echo.
python Menu.py --post-link
:fine
echo.
pause
