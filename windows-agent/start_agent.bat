@echo off
:: 管理者権限チェック・昇格
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0.."
echo Pulling latest code...
git pull

echo Checking Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo Starting Ollama...
    start "" "C:\Users\iniwa\AppData\Local\Programs\Ollama\ollama.exe" serve
    timeout /t 5 /nobreak >NUL
    echo Ollama started.
) else (
    echo Ollama already running.
)

echo Starting Windows Agent...
cd windows-agent
python agent.py
pause
