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
echo   启动 whisper 字幕引擎（默认 small 模型，约 0.5 GB 显存）
echo   启动后第一次请求会加载模型（约 5 秒），之后每次零点几秒
echo   关闭本窗口即停止引擎（字幕功能会退回慢速命令行模式）
echo ============================================================
rem 默认用 small（快、显存小）；想换回大模型：启动字幕引擎.bat ggml-large-v3-turbo.bin
set M=ggml-small.bin
if not "%~1"=="" set M=%~1
if not exist "%W%\models\%M%" (
  echo [!] 没有 %W%\models\%M%，改用 ggml-large-v3-turbo.bin
  set M=ggml-large-v3-turbo.bin
)
echo 使用模型：%M%
"%W%\cuda\Release\whisper-server.exe" -m "%W%\models\%M%" --host 127.0.0.1 --port 8178
pause
