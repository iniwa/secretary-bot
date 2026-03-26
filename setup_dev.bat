@echo off
title Secretary Bot - Dev Setup

cd /d "%~dp0"

echo ========================================
echo   Secretary Bot - Development Setup
echo ========================================
echo.

python --version 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+
    pause
    exit /b 1
)

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt

if not exist ".env" (
    echo.
    echo Creating .env from .env.example ...
    copy .env.example .env
    echo [NOTE] Edit .env and set your API keys / tokens.
)

if not exist "config.yaml" (
    echo Creating config.yaml from config.yaml.example ...
    copy config.yaml.example config.yaml
    echo [NOTE] Edit config.yaml if needed.
)

if not exist "data" mkdir data

echo.
echo ========================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Edit .env (set API keys / tokens)
echo   2. Edit config.yaml if needed
echo   3. Double-click start_bot.bat to run
echo ========================================
echo.
pause
