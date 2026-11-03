@echo off
rem Crea l'immagine e il testo del post del METEO di oggi (dati veri) e li apre, SENZA pubblicare.
cd /d "%~dp0"
python Menu.py --meteo-prova
echo.
pause
