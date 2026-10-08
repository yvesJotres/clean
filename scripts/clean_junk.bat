@echo off
setlocal EnableDelayedExpansion
title Disk Cleanup Tool

:: ============================================================
::  Check for Admin rights (needed for some operations)
:: ============================================================
net session >nul 2>&1
if %errorlevel%==0 (
    set "ADMIN=1"
) else (
    set "ADMIN=0"
)

cls
echo ============================================================
echo   Disk Cleanup Tool
echo ============================================================
echo.
echo   The following will be cleaned:
echo.
echo   [LOCAL APP DATA]
echo   - pip cache           (~10.5 GB)
echo   - Temp folder         (~1.3 GB)
echo   - CrashDumps          (~317 MB)
echo   - lm-studio-updater   (~250 MB)
echo   - curseforge-updater  (~100 MB)
echo   - SquirrelTemp        (~42 KB)
echo   - npm cache           (~15 MB)
echo.
echo   [WINDOWS SYSTEM]
echo   - Windows Temp        (varies)
echo   - Thumbnail cache     (varies)
echo   - Windows Error Reporting (varies)
echo   - DNS cache (flush)
if "%ADMIN%"=="1" (
echo   - Windows Prefetch    (varies)  [Admin: YES]
echo   - Delivery Optimization cache   [Admin: YES]
) else (
echo   - Windows Prefetch    [Skipped - run as Admin to include]
echo   - Delivery Optimization cache   [Skipped - run as Admin]
)
echo.
echo   [RECYCLE BIN]
echo   - All drives
echo.
echo ============================================================
echo   WARNING: This cannot be undone.
echo ============================================================
echo.
set /p CONFIRM="  Type YES to proceed: "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo   Cancelled. No files were deleted.
    pause
    exit /b 0
)

echo.
echo ============================================================
echo   Cleaning...
echo ============================================================
echo.

:: ---- pip cache ----
echo   [1/10] pip cache...
pip cache purge >nul 2>&1
echo         Done.

:: ---- npm cache ----
echo   [2/10] npm cache...
npm cache clean --force >nul 2>&1
echo         Done.

:: ---- %LOCALAPPDATA%\Temp ----
echo   [3/10] Local Temp folder...
if exist "%LOCALAPPDATA%\Temp" (
    del /f /s /q "%LOCALAPPDATA%\Temp\*" >nul 2>&1
    for /d %%d in ("%LOCALAPPDATA%\Temp\*") do rd /s /q "%%d" >nul 2>&1
)
echo         Done.

:: ---- Windows Temp ----
echo   [4/10] Windows Temp folder...
if exist "%TEMP%" (
    del /f /s /q "%TEMP%\*" >nul 2>&1
    for /d %%d in ("%TEMP%\*") do rd /s /q "%%d" >nul 2>&1
)
echo         Done.

:: ---- CrashDumps ----
echo   [5/10] CrashDumps...
if exist "%LOCALAPPDATA%\CrashDumps" (
    del /f /s /q "%LOCALAPPDATA%\CrashDumps\*" >nul 2>&1
)
echo         Done.

:: ---- lm-studio-updater ----
echo   [6/10] lm-studio-updater...
if exist "%LOCALAPPDATA%\lm-studio-updater" (
    rd /s /q "%LOCALAPPDATA%\lm-studio-updater" >nul 2>&1
)
echo         Done.

:: ---- curseforge-updater ----
echo   [7/10] curseforge-updater...
if exist "%LOCALAPPDATA%\curseforge-updater" (
    rd /s /q "%LOCALAPPDATA%\curseforge-updater" >nul 2>&1
)
echo         Done.

:: ---- SquirrelTemp ----
echo   [8/10] SquirrelTemp...
if exist "%LOCALAPPDATA%\SquirrelTemp" (
    rd /s /q "%LOCALAPPDATA%\SquirrelTemp" >nul 2>&1
)
echo         Done.

:: ---- Thumbnail cache ----
echo   [9/10] Thumbnail cache...
taskkill /f /im explorer.exe >nul 2>&1
del /f /s /q "%LOCALAPPDATA%\Microsoft\Windows\Explorer\thumbcache_*.db" >nul 2>&1
start explorer.exe
echo         Done.

:: ---- Windows Error Reporting ----
echo   [10/10] Windows Error Reporting...
if exist "%LOCALAPPDATA%\Microsoft\Windows\WER" (
    del /f /s /q "%LOCALAPPDATA%\Microsoft\Windows\WER\*" >nul 2>&1
    for /d %%d in ("%LOCALAPPDATA%\Microsoft\Windows\WER\*") do rd /s /q "%%d" >nul 2>&1
)
if exist "%ProgramData%\Microsoft\Windows\WER" (
    del /f /s /q "%ProgramData%\Microsoft\Windows\WER\*" >nul 2>&1
    for /d %%d in ("%ProgramData%\Microsoft\Windows\WER\*") do rd /s /q "%%d" >nul 2>&1
)
echo         Done.

:: ---- Admin-only: Prefetch + Delivery Optimization ----
if "%ADMIN%"=="1" (
    echo   [+] Windows Prefetch...
    del /f /s /q "%SystemRoot%\Prefetch\*" >nul 2>&1
    echo         Done.

    echo   [+] Delivery Optimization cache...
    rd /s /q "%SystemDrive%\Windows\SoftwareDistribution\DeliveryOptimization" >nul 2>&1
    echo         Done.
)

:: ---- DNS cache ----
echo   [+] Flushing DNS cache...
ipconfig /flushdns >nul 2>&1
echo         Done.

:: ---- Recycle Bin ----
echo   [+] Emptying Recycle Bin...
PowerShell -NoProfile -Command "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
echo         Done.

echo.
echo ============================================================
echo   Cleanup complete!
if "%ADMIN%"=="0" (
    echo   Tip: Run as Administrator to also clean Prefetch
    echo   and Delivery Optimization cache.
)
echo ============================================================
echo.
pause
endlocal
