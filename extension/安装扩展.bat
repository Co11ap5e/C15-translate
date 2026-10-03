@echo off
chcp 65001 >nul
echo ============================================================
echo   本地翻译扩展 · 安装引导
echo ============================================================
echo.
echo   1. 稍后打开的页面里，右上角打开「开发者模式」
echo   2. 点「加载已解压的扩展程序」
echo   3. 选择这个目录：
echo      %~dp0
echo.
echo   （目录路径已复制到剪贴板，可直接粘贴）
echo.
echo %~dp0| clip
pause
start chrome.exe "chrome://extensions/" 2>nul || start msedge "edge://extensions/" 2>nul || start "" "chrome://extensions/"
