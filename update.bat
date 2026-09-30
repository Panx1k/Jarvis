@echo off
set "JARVIS_DIR=%~dp0"
cd /d "%JARVIS_DIR%"
if not exist ".venv\Scripts\python.exe" (echo Run install.bat first. & pause & exit /b 1)
echo === Updating J.A.R.V.I.S. ===
".venv\Scripts\python.exe" -m jarvis.services.updater
echo.
pause
