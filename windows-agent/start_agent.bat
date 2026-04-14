@echo off
setlocal enabledelayedexpansion
:: 管理者権限チェック・昇格
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Ollama を全インターフェースでリスンさせる（Pi等の外部からアクセス可能にする）
set OLLAMA_HOST=0.0.0.0

:: NAS認証（windows-agent/config/.env から読み込み）
set "ENV_FILE=%~dp0config\.env"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if not "%%A"=="" set "%%A=%%B"
    )
    if defined NAS_HOST if defined NAS_SHARE if defined NAS_USER if defined NAS_PASS (
        net use "\\%NAS_HOST%\%NAS_SHARE%" /user:%NAS_USER% %NAS_PASS% >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            echo NAS connected: \\%NAS_HOST%\%NAS_SHARE%
        ) else (
            echo WARNING: NAS connection failed: \\%NAS_HOST%\%NAS_SHARE%
        )
    )
) else (
    echo NOTE: %ENV_FILE% not found, skipping NAS mount
)

:loop
cd /d "%~dp0.."
echo.
echo ============================================
echo  Pulling latest code...
echo ============================================
git pull
echo Updating submodules...
git submodule update --init --recursive

echo Checking Ollama...
taskkill /IM ollama.exe /F >NUL 2>&1
timeout /t 2 /nobreak >NUL
echo Starting Ollama (OLLAMA_HOST=%OLLAMA_HOST%)...
start "" "C:\Users\iniwa\AppData\Local\Programs\Ollama\ollama.exe" serve
timeout /t 5 /nobreak >NUL
echo Ollama started.

cd windows-agent
echo.
echo Installing Python requirements...
python -m pip install --disable-pip-version-check -r requirements.txt
echo.
echo Starting Windows Agent...
python agent.py

echo.
echo Agent stopped. Restarting in 3 seconds... (Ctrl+C to abort)
timeout /t 3
goto loop
