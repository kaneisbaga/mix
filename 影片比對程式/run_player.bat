@echo off
chcp 65001 >nul
title Dual Video Player
cd /d "%~dp0"
python dual_video_player.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo 執行發生錯誤，請按任意鍵離開...
    pause >nul
)
