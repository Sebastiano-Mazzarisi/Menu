@echo off
rem PROVA del post "La tua rosticceria": crea e apre l'immagine e stampa il testo con i link
rem diretti di ogni rosticceria. NON pubblica niente su Facebook.
cd /d "%~dp0"
python Menu.py --post-rosticcerie-prova
echo.
pause
