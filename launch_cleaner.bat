@echo off
title System Cleaner
cd /d "%~dp0"
start "" "C:\Users\Yves Caesario\AppData\Local\Programs\Python\Python312\python.exe" webui/server.py
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8000/