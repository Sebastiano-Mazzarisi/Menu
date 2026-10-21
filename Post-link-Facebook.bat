@echo off
rem Da usare UNA volta: pubblica sulla Pagina Facebook i due post con un'immagine 16:9:
rem   1) "I menu in pdf"   (PRIMO, fissato in cima)
rem   2) "I menu in video" (SECONDO)
rem Sotto ognuno un commento di due righe: il link e "Aggiornato <giorno> alle ore <ora>".
rem Poi Menu.py aggiorna da solo l'ora nei commenti e, dopo il post del giorno, ripubblica il
rem post del video perche' resti secondo. Se i post ci sono gia' non li rifa'.
cd /d "%~dp0"
title Menu - post con i link su Facebook
python Menu.py --post-link
echo.
pause
