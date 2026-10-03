@echo off
chcp 65001 >nul
title 文档翻译（全本地）
cd /d "%~dp0"
if "%~1"=="" (
  echo ============================================================
  echo   把文档拖到本文件上即可翻译：
  echo     字幕  .srt / .vtt / .ass   （保留时间轴，出双语）
  echo     文本  .txt / .md           （逐行翻译）
  echo ============================================================
  echo.
  pause
  exit /b
)
netstat -ano | findstr ":18765" | findstr LISTENING >nul
if errorlevel 1 (
  echo [1/2] 启动翻译服务 ...
  start "LocalTranslate" /min python "%~dp0server.py"
  timeout /t 4 /nobreak >nul
) else ( echo [1/2] 翻译服务已在运行 )

echo [2/2] 翻译 %~nx1 ...
python "%~dp0cli.py" -f "%~1" --template subtitle --model best --out "%~1.zh"
echo.
echo 完成：%~nx1.zh
pause
