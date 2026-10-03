@echo off
chcp 65001 >nul
title 图片/漫画翻译（全本地）
cd /d "%~dp0"
if "%~1"=="" (
  echo ============================================================
  echo   把【图片】或【图片文件夹】拖到本文件上
  echo     输出：同目录的  ^<名字^>_zh.png（中文回贴图）
  echo                      ^<名字^>_zh.txt（日中对照文本）
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
) else ( echo [1/2] 翻译服务正常 )

echo [2/2] 识别 + 翻译（首次要加载 OCR 模型，约 20 秒）...
echo.
python "%~dp0image-translate.py" "%~1" --mode both --mt fast

echo.
echo 完成。输出：_zh.png（回贴图）/ _zh.txt（对照）
pause
