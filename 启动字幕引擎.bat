@echo off
chcp 65001 >nul
title whisper 字幕引擎（常驻）
set W=D:\DSH\tools\whisper
if not exist "%W%\cuda\Release\whisper-server.exe" (
  echo [x] 找不到 whisper-server.exe（应在 %W%\cuda\Release\）
  pause & exit /b
)
netstat -ano | findstr ":8178" | findstr LISTENING >nul
if not errorlevel 1 ( echo 引擎已在运行 & timeout /t 2 >nul & exit /b )

echo ============================================================
echo   启动 whisper 字幕引擎（模型常驻显存，约 1.5 GB）
echo   启动后第一次请求会加载模型（约 20 秒），之后每次几秒
echo   关闭本窗口即停止引擎（字幕功能会退回慢速命令行模式）
echo ============================================================
"%W%\cuda\Release\whisper-server.exe" -m "%W%\models\ggml-large-v3-turbo.bin" --host 127.0.0.1 --port 8178
pause
