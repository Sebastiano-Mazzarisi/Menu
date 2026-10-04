@echo off
rem Crea (o aggiorna) l'attivita' pianificata "Menu":
rem ogni 15 minuti a orari fissi (07:00, 07:15, 07:30...) esegue "Menu.py --automatico" senza aprire finestre.
rem La fascia oraria (es. 08:00-14:00) si decide in locali.json > impostazioni > automatico.
cd /d "%~dp0"
set "PYW="
for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if not defined PYW (
  echo pythonw.exe non trovato: Python non e' nel PATH.
  pause
  exit /b 1
)
schtasks /Create /F /TN "Menu" /SC MINUTE /MO 15 /ST 07:00 /TR "\"%PYW%\" \"%~dp0Menu.py\" --automatico"
if errorlevel 1 (
  echo Creazione NON riuscita.
) else (
  echo.
  echo Fatto: ogni 15 minuti Menu.py controlla i menu e pubblica il sito.
  echo Registro delle esecuzioni: dati\automatico.log
)
pause
