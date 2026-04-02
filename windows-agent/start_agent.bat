@echo off
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
