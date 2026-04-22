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

:: GPU 利用促進（CPU fallback 回避）
::   CUDA_VISIBLE_DEVICES : GPU 0 を明示（複数GPU環境や Docker 残骸対策）
::   OLLAMA_FLASH_ATTENTION : attention VRAM 削減
::   OLLAMA_KV_CACHE_TYPE   : KV cache を q8_0 量子化し VRAM 削減（長コンテキスト向け）
::   OLLAMA_MAX_LOADED_MODELS : 同時ロードを1つに制限（VRAM超過で CPU fallback するのを回避）
set CUDA_VISIBLE_DEVICES=0
set OLLAMA_FLASH_ATTENTION=1
set OLLAMA_KV_CACHE_TYPE=q8_0
set OLLAMA_MAX_LOADED_MODELS=1

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
:: デスクトップ/トレイアプリも停止（Ollama 自動起動が :11434 を握ると start_agent の env が効かない）
taskkill /IM "ollama app.exe" /F >NUL 2>&1
taskkill /IM ollama.exe /F >NUL 2>&1
timeout /t 2 /nobreak >NUL
echo Starting Ollama (OLLAMA_HOST=%OLLAMA_HOST%)...
start "" "C:\Users\iniwa\AppData\Local\Programs\Ollama\ollama.exe" serve
timeout /t 5 /nobreak >NUL
echo Ollama started.

:: GPU 診断ログ（CPU fallback 等の切り分け用）
set "GPU_LOG=%~dp0logs\gpu_status.log"
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo. >> "%GPU_LOG%"
echo ===== %DATE% %TIME% ===== >> "%GPU_LOG%"
nvidia-smi >> "%GPU_LOG%" 2>&1
echo --- ollama ps --- >> "%GPU_LOG%"
"C:\Users\iniwa\AppData\Local\Programs\Ollama\ollama.exe" ps >> "%GPU_LOG%" 2>&1

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
