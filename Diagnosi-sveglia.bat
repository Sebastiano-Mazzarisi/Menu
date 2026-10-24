@echo off
rem Diagnosi-sveglia.bat - perche' il PC non si risveglia per l'attivita' "Menu"?
rem Scrive tutto in dati\diagnosi-sveglia.txt (si puo' rilanciare quando si vuole).
cd /d "%~dp0"
chcp 65001 >nul
set OUT=dati\diagnosi-sveglia.txt
(
echo ===== Data: %date% %time%
echo.
echo ===== Stati di sospensione disponibili (powercfg /a)
powercfg /a
echo.
echo ===== Timer di riattivazione attivi adesso (powercfg /waketimers)
powercfg /waketimers
echo.
echo ===== Ultima causa di risveglio (powercfg /lastwake)
powercfg /lastwake
echo.
echo ===== Combinazione risparmio energia attiva
powercfg /getactivescheme
echo.
echo ===== Consenti timer di riattivazione [0=Disattiva 1=Attiva 2=Solo importanti]
powercfg /q SCHEME_CURRENT 238c9fa8-0aad-41ed-83f4-97be242c8f20 bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d
echo.
echo ===== Attivita' "Menu"
schtasks /Query /TN Menu /V /FO LIST
echo.
echo ===== Ultimi eventi di sospensione/risveglio (registro di sistema)
powershell -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Power','Microsoft-Windows-Power-Troubleshooter'; StartTime=(Get-Date).AddDays(-2)} -ErrorAction SilentlyContinue | Select-Object -First 30 TimeCreated, Id, ProviderName, @{n='Messaggio';e={($_.Message -split [Environment]::NewLine)[0..6] -join ' | '}} | Format-List"
echo.
echo ===== Alimentazione (corrente o batteria)
powershell -NoProfile -Command "$b = Get-CimInstance Win32_Battery; if ($b) { 'Batteria presente, stato: ' + $b.BatteryStatus + ' (2 = collegato alla corrente)' } else { 'Nessuna batteria: PC fisso' }"
) > "%OUT%" 2>&1
echo Fatto: %OUT%
