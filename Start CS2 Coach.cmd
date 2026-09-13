@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-local.ps1" -OpenBrowser
if errorlevel 1 (
    echo.
    echo CS2 Coach could not start. Read the error above.
    pause
    exit /b 1
)
exit /b 0
