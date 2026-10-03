@echo off
chcp 65001 >nul
title 视频/音频 → 双语字幕（全本地）
cd /d "%~dp0"
if "%~1"=="" (
  echo ============================================================
  echo   把视频或音频文件【拖到本文件上】即可生成双语字幕
  echo ============================================================
  echo.
  echo   输出：与源文件同目录的  ^<文件名^>.双语.srt
  echo   依赖：ffmpeg + whisper.cpp + 本地翻译服务
  echo.
  pause
  exit /b
)

echo ============================================================
echo   输入：%~nx1
echo ============================================================
echo [1/3] 检查本地服务 ...

netstat -ano | findstr ":18765" | findstr LISTENING >nul
if errorlevel 1 (
  echo       启动翻译服务 ...
  start "LocalTranslate" /min python "%~dp0server.py"
  timeout /t 4 /nobreak >nul
) else ( echo       翻译服务正常 )

netstat -ano | findstr ":8178" | findstr LISTENING >nul
if errorlevel 1 (
  echo       启动字幕引擎（首次请求会加载模型，约 20 秒）...
  start "whisper-engine" /min "%~dp0启动字幕引擎.bat"
  timeout /t 3 /nobreak >nul
) else ( echo       字幕引擎正常 )

echo [2/3] 语音识别 + 翻译 ...
echo.
python "%~dp0video-to-subtitle.py" "%~1" --lang ja --model large-v3-turbo --mt fast --keep-ja

echo.
echo [3/3] 完成。字幕在同目录：%~n1.双语.srt
pause
