@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "C:\Program Files\Python313\pythonw.exe" "%~dp0app.py"
