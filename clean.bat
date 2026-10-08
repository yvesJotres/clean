@echo off
REM Clean CLI - Quick junk cleanup for C: drive
REM Usage: clean [--delete|--move|--list|--clean --force|--help]

set "PYTHON=C:\Users\Yves Caesario\AppData\Local\Programs\Python\Python312\python.exe"
set "SCRIPT=D:\Projects\Clean\folder_size.py"

if "%~1"=="" (
    "%PYTHON%" "%SCRIPT%" --analyze-c
    goto :eof
)

if "%~1"=="--help" (
    echo clean - Quick C: drive junk cleanup
    echo.
    echo Usage:
    echo   clean              ^|  Show bloat analysis (cache/move/decide)
    echo   clean --cache      ^|  Show only safe-to-delete cache items
    echo   clean --move       ^|  Show only safe-to-move items
    echo   clean --decide     ^|  Show only review-needed items
    echo   clean --json       ^|  Output JSON (for scripts/agents)
    echo   clean --cache --json  ^|  JSON for cache items only
    echo   clean --clean --force  ^|  Actually delete cache folders (requires Admin for some)
    echo   clean --help       ^|  Show this help
    goto :eof
)

if "%~1"=="--delete" (
    "%PYTHON%" "%SCRIPT%" --analyze-c --category delete
    goto :eof
)

if "%~1"=="--cache" (
    "%PYTHON%" "%SCRIPT%" --analyze-c --category cache %*
    goto :eof
)

if "%~1"=="--move" (
    "%PYTHON%" "%SCRIPT%" --analyze-c --category move %*
    goto :eof
)

if "%~1"=="--decide" (
    "%PYTHON%" "%SCRIPT%" --analyze-c --category decide %*
    goto :eof
)

if "%~1"=="--json" (
    "%PYTHON%" "%SCRIPT%" --analyze-c --json %*
    goto :eof
)

if "%~1"=="--clean" (
    "%PYTHON%" "%SCRIPT%" --category cache --clean %*
    goto :eof
)

echo Unknown option: %1
echo Run "clean --help" for usage.
