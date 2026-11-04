@echo off
cd /d "%~dp0"
python Notizie.py --apri
if errorlevel 1 pause
