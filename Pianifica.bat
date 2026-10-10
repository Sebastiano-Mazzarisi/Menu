@echo off
rem Crea (o aggiorna) l'attivita' pianificata "Menu" con gli orari di locali.json:
rem   impostazioni > automatico: "dalle", "alle", "ogni_minuti" (es. 08:00, 20:00, 30).
rem Lo stesso fa il pulsante "Pianifica" del Monitor; e se gli orari in locali.json cambiano,
rem il primo giro automatico aggiorna l'attivita' da solo.
cd /d "%~dp0"
python Menu.py --pianifica
echo Registro delle esecuzioni: dati\automatico.log
pause
