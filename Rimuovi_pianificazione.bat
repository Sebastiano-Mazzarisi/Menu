@echo off
rem Elimina l'attivita' pianificata "Menu" (i controlli automatici si fermano)
schtasks /Delete /TN "Menu" /F
pause
