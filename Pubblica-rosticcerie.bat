@echo off
rem Pubblica SUBITO su Facebook il post "La tua rosticceria" (link diretto al menu di ogni
rem rosticceria), toglie quello vecchio e rimette in ordine i primi post. Di norma va da solo.
cd /d "%~dp0"
python Menu.py --post-rosticcerie
echo.
pause
