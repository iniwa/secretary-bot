# Ollama自動起動 実装計画

## 背景

Windows PC起動時にOllamaを自動起動させたい。現在は手動で起動する必要がある。
Ollamaが稼働していないと、secretary-botはGeminiフォールバック（または省エネモード）になり、人格品質が低下する。

## 方式

**2段構え**で確実に起動させる:

### 1. Windowsタスクスケジューラ（メイン）

PC起動時にOllamaを自動起動。Windows Agentとは独立して動作する。

```
タスク名: OllamaAutoStart
トリガー: ログオン時
操作: プログラムの開始
  プログラム: C:\Users\iniwa\AppData\Local\Programs\ollama\ollama.exe
  引数: serve
条件: なし（常に実行）
設定: 既に実行中の場合は新しいインスタンスを開始しない
```

**登録コマンド** (管理者PowerShell):
```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\Users\iniwa\AppData\Local\Programs\ollama\ollama.exe" `
    -Argument "serve"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "iniwa"
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask `
    -TaskName "OllamaAutoStart" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Ollama自動起動"
```

> **注意**: `ollama.exe` のパスは実際のインストール先を確認すること。
> `where ollama` または `Get-Command ollama` で確認可能。

### 2. start_agent.bat（フォールバック）

Windows Agent起動時にOllamaが動いていなければ起動する。
タスクスケジューラが何らかの理由で失敗した場合の保険。

**変更後の `start_agent.bat`**:

```bat
@echo off
cd /d "%~dp0.."
echo Pulling latest code...
git pull

echo Checking Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if %ERRORLEVEL% NEQ 0 (
    echo Starting Ollama...
    start "" "C:\Users\iniwa\AppData\Local\Programs\ollama\ollama.exe" serve
    timeout /t 5 /nobreak >NUL
    echo Ollama started.
) else (
    echo Ollama already running.
)

echo Starting Windows Agent...
cd windows-agent
python agent.py
pause
```

**変更点**:
- `tasklist` でOllamaプロセスの存在を確認
- 未起動なら `start ""` でバックグラウンド起動
- 5秒待機してOllamaの起動完了を待つ

## 実装手順

1. Ollamaのインストールパスを確認（`where ollama`）
2. タスクスケジューラに登録（PowerShellコマンド実行）
3. `start_agent.bat` を更新
4. PC再起動して動作確認

## 検証方法

1. PC再起動 → `tasklist | find "ollama"` でプロセス確認
2. Ollamaを手動停止 → `start_agent.bat` 実行 → 自動起動確認
3. secretary-botのstatusユニットでOllama接続確認
