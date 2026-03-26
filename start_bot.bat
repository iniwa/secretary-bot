@echo off
title Secretary Bot - Local Dev

cd /d "%~dp0"

set BOT_BASE_DIR=%~dp0
if "%BOT_BASE_DIR:~-1%"=="\" set BOT_BASE_DIR=%BOT_BASE_DIR:~0,-1%

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

if exist ".env" (
    echo Loading .env ...
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
)

if not exist "data" mkdir data

if not exist "config.yaml" (
    if exist "config.yaml.example" (
        echo config.yaml not found, copying from example...
        copy config.yaml.example config.yaml
    )
)

echo.
echo ========================================
echo   Secretary Bot - Starting...
echo   Base: %BOT_BASE_DIR%
echo   WebGUI: http://localhost:%WEBGUI_PORT%
echo ========================================
echo.

python -m src.bot

echo.
echo Bot stopped. Press any key to close.
pause >nul
