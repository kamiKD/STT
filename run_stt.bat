@echo off
rem Launch Speech To Text without a console window.
setlocal
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m stt
) else (
    start "" python -m stt
)
endlocal
