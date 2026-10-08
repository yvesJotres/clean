@echo off
REM Storage Monitor Daemon Launcher
REM Usage: storage_monitor_daemon.bat [daemon|install|uninstall|config|test]

cd /d "%~dp0"

if "%1"=="" (
    echo Starting Storage Monitor Daemon...
    python storage_monitor.py daemon
    goto :eof
)

if "%1"=="install" (
    echo Installing as Windows Service (requires Admin)...
    python storage_monitor.py install-service
    goto :eof
)

if "%1"=="uninstall" (
    echo Uninstalling Windows Service...
    python storage_monitor.py uninstall-service
    goto :eof
)

if "%1"=="config" (
    python storage_monitor.py config
    goto :eof
)

if "%1"=="test" (
    python storage_monitor.py test
    goto :eof
)

echo Usage: storage_monitor_daemon.bat [daemon|install|uninstall|config|test]
echo   daemon        - Run in foreground (for testing)
echo   install       - Install as Windows auto-start service
echo   uninstall     - Remove Windows service
echo   config        - Show current configuration
echo   test          - Run one scan and show results