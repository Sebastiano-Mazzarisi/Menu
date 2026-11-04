@echo off
rem Elimina le attivita' pianificate dei menu e delle notizie
schtasks /Delete /TN "Menu" /F
schtasks /Delete /TN "Menu Notizie" /F
pause
