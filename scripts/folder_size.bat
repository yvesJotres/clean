@echo off
setlocal
title Folder Size Explorer

:: Find the script relative to this bat file
set "SCRIPT=%~dp0folder_size.py"

:: Check if python is installed
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python or add it to your PATH.
    pause
    exit /b 1
)

:: Run the graphical UI by default. Use: folder_size.bat --cli
:: to force the original terminal browser instead.
python "%SCRIPT%" --ui %*

endlocal