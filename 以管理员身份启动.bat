@echo off
rem DesktopLayoutKeeper - launch elevated (UAC prompt)
rem ASCII-only on purpose: any console code page reads this file correctly.
cd /d "%~dp0"

set "DLK_EXE="
for %%F in ("%~dp0*.exe") do set "DLK_EXE=%%~fF"

if defined DLK_EXE goto :USE_EXE
goto :USE_SRC

:USE_EXE
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "Start-Process -FilePath $env:DLK_EXE -Verb RunAs -WorkingDirectory (Get-Location).Path"
if errorlevel 1 goto :CANCELLED
exit /b 0

:USE_SRC
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$exe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $exe) { $exe = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source }; if (-not $exe) { Write-Host 'pythonw.exe not found.'; exit 1 }; Start-Process -FilePath $exe -ArgumentList 'src\desktop_layout_keeper.py' -Verb RunAs -WorkingDirectory (Get-Location).Path"
if errorlevel 1 goto :CANCELLED
exit /b 0

:CANCELLED
echo.
echo Elevation cancelled or failed. You can also right-click the target and pick "Run as administrator".
echo.
pause
exit /b 1
