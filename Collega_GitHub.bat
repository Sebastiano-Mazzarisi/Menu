@echo off
rem Collega questa cartella al repository GitHub "Menu" (da eseguire una sola volta)
cd /d "%~dp0"
if exist .git goto invia
git init -b main
git remote add origin https://github.com/Sebastiano-Mazzarisi/Menu.git
git fetch origin
git rev-parse --verify origin/main >nul 2>&1 && git reset origin/main
:invia
git add -A
git commit -m "Collegamento dal PC"
git push -u origin main
pause
