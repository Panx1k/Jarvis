@echo off
rem J.A.R.V.I.S. launcher (ASCII only: cmd.exe reads .bat files in the OEM code page)
set "JARVIS_DIR=%~dp0"
if not exist "%JARVIS_DIR%.venv\Scripts\pythonw.exe" (
    echo Python environment not found. Run install.bat first.
    pause
    exit /b 1
)
cd /d "%JARVIS_DIR%"
start "" "%JARVIS_DIR%.venv\Scripts\pythonw.exe" "%JARVIS_DIR%main.py" %*
