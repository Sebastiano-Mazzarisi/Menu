@echo off
rem Sulla Pagina Facebook deve restare UN SOLO post: l'immagine del giorno con i link a PDF e video.
rem Elenca tutti gli altri post e, dopo la conferma (s + Invio), li cancella.
rem Non tocca i cambi di immagine di copertina e del profilo.
cd /d "%~dp0"
title Menu - pulizia della Pagina Facebook
python Menu.py --pulisci-facebook
echo.
pause
