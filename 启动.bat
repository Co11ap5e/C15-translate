@echo off
chcp 65001 >nul
title 本地翻译 LocalTranslate
cd /d "%~dp0"

echo ============================================================
echo   本地翻译 LocalTranslate   （模型：Ollama + qwen2.5）
echo ============================================================
echo.

rem 1) 确保 Ollama 在跑
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
  echo [1/3] 启动 Ollama ...
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
  ) else (
    start "" ollama serve
  )
  timeout /t 6 /nobreak >nul
) else (
  echo [1/3] Ollama 已在运行 ✓
)

rem 2) 启动翻译服务
echo [2/3] 启动翻译服务 ...
start "LocalTranslate" /min python "%~dp0server.py"

rem 3) 等端口起来后打开浏览器
echo [3/3] 打开界面 ...
timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:18765

echo.
echo   界面地址: http://127.0.0.1:18765
echo   关闭本窗口不会停止服务；要停止请结束 python.exe（或关掉 LocalTranslate 窗口）
echo.
pause
