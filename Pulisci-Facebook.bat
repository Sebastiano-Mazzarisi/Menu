@echo off
rem Da usare UNA volta: elenca i post della Pagina Facebook con le foto dei menu dei locali o
rem con il video e, dopo la conferma (s + Invio), li cancella con le loro foto/video.
rem I post di presentazione, la copertina e l'immagine del profilo restano.
cd /d "%~dp0"
title Menu - pulizia della Pagina Facebook
python Menu.py --pulisci-facebook
echo.
pause
