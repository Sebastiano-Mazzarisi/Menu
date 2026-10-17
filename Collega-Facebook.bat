@echo off
rem Collegamento ufficiale con la Pagina Facebook "Menu rosticcerie" (si fa una volta sola).
rem Prima segui i passi di Facebook\Collegamento-Facebook.txt: qui vengono chiesti
rem ID app, chiave segreta e codice provvisorio; il codice della Pagina finisce in dati\facebook.json
rem (resta solo sul PC, non va su GitHub).
cd /d "%~dp0"
python Menu.py --facebook-collega
echo.
pause
