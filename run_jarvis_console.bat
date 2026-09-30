@echo off
rem J.A.R.V.I.S. in console mode with voice (ASCII only)
set "JARVIS_DIR=%~dp0"
cd /d "%JARVIS_DIR%"
chcp 65001 >nul
"%JARVIS_DIR%.venv\Scripts\python.exe" "%JARVIS_DIR%main.py" --cli --voice %*
pause
