@echo off
rem Crea il riassunto di oggi (immagine e testo in dati\riassunti) e apre l'immagine,
rem SENZA pubblicarlo su Facebook: serve per vedere come verra' il post delle 11:30.
cd /d "%~dp0"
python Menu.py --riassunto-prova
echo.
echo Testo del post: dati\riassunti\ (file .txt di oggi)
pause
