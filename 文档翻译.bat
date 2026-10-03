@echo off
chcp 65001 >nul
title ?????????
cd /d "%~dp0"
if "%~1"=="" (
  echo ============================================================
  echo   ??????????????
  echo     ??  .srt / .vtt / .ass   ???????????
  echo     ??  .txt / .md           ??????
  echo ============================================================
  echo.
  pause
  exit /b
)
netstat -ano | findstr ":18765" | findstr LISTENING >nul
if errorlevel 1 (
  echo [1/2] ?????? ...
  start "LocalTranslate" /min python "%~dp0server.py"
  timeout /t 4 /nobreak >nul
) else ( echo [1/2] ???????? ? )

echo [2/2] ?? %~nx1 ...
python "%~dp0cli.py" -f "%~1" --template subtitle --model best --out "%~1.zh"
echo.
echo ???%~nx1.zh
pause