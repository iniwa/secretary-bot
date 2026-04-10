@echo off
:: 管理者権限チェック・昇格
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Ollama を全インターフェースでリスンさせる（Pi等の外部からアクセス可能にする）
set OLLAMA_HOST=0.0.0.0

echo Checking Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo Starting Ollama (OLLAMA_HOST=%OLLAMA_HOST%)...
    start "" "C:\Users\iniwa\AppData\Local\Programs\Ollama\ollama.exe" serve
    timeout /t 5 /nobreak >NUL
    echo Ollama started.
) else (
    echo Ollama already running.
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

cd windows-agent
echo.
echo Starting Windows Agent...
python agent.py

echo.
echo Agent stopped. Restarting in 3 seconds... (Ctrl+C to abort)
timeout /t 3
goto loop
