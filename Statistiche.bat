@echo off
rem Mostra follower e visualizzazioni della Pagina Facebook "Menu rosticcerie" (letti con il
rem collegamento ufficiale). Gli stessi numeri compaiono nel Monitor e si aggiornano a ogni giro automatico.
cd /d "%~dp0"
title Menu - statistiche della Pagina Facebook
python Menu.py --statistiche
echo.
pause
