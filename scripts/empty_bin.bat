@echo off
echo Emptying Recycle Bin...
PowerShell -NoProfile -Command "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"
echo Done.
pause
