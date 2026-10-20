@echo off
rem DesktopLayoutKeeper - normal launch (prefers the packaged exe)
rem ASCII-only on purpose: any console code page reads this file correctly.
cd /d "%~dp0"

set "DLK_EXE="
for %%F in ("%~dp0*.exe") do set "DLK_EXE=%%~fF"

if defined DLK_EXE (
    start "" "%DLK_EXE%"
    exit /b 0
)

where pythonw >nul 2>nul && (
    start "" pythonw "src\desktop_layout_keeper.py"
    exit /b 0
)
where pyw >nul 2>nul && (
    start "" pyw "src\desktop_layout_keeper.py"
    exit /b 0
)

echo.
echo No exe next to this script, and no pythonw.exe on PATH.
echo Run "python build.py" once to produce the exe, or install Python 3.8+.
echo.
pause
