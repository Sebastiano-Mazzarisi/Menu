@echo off
cd /d "%~dp0"
python Menu.py --pubblica
if errorlevel 1 pause
