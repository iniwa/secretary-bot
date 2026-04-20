<#
.SYNOPSIS
  Sub PC 側の sshd + 公開鍵登録をまとめて行う冪等スクリプト。
  Pi 経由の Cloudflare Tunnel 構成が前提（Sub PC には cloudflared を入れない）。

.DESCRIPTION
  以下を順に実行する:
    1. sshd を Automatic で起動（未起動なら開始）
    2. Windows ファイアウォールルール OpenSSH-Server-In-TCP を確認/作成（LAN 限定）
    3. Remote PC の公開鍵を administrators_authorized_keys に追記（重複排除）
    4. administrators_authorized_keys の ACL を Administrators:F / SYSTEM:F にセット
    5. sshd_config で PubkeyAuthentication yes / PasswordAuthentication no を保証
    6. sshd を再起動
    7. 最終状態サマリを表示

  冪等性: 同じ公開鍵を重複追加しない、既存ルール/設定は破壊しない。

.NOTES
  実行には管理者権限が必要。
  実行: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_subpc_ssh.ps1

.LINK
  docs/guides/remote_pc_subpc_vscode_access.md
#>

[CmdletBinding()]
param(
  [string]$PublicKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHHt2glWm3ySSj+1tx6GU1rGbjOyqEnwUmDIcjogIsyb iniwa_remote_access'
)

$ErrorActionPreference = 'Stop'

function Assert-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($id)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error '管理者権限で実行してください。PowerShell を「管理者として実行」で起動し直してください。'
    exit 1
  }
}

function Write-Step {
  param([string]$Msg)
  Write-Host "==> $Msg" -ForegroundColor Cyan
}

function Write-Ok {
  param([string]$Msg)
  Write-Host "    [OK] $Msg" -ForegroundColor Green
}

function Write-Skip {
  param([string]$Msg)
  Write-Host "    [skip] $Msg" -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
Assert-Admin

# 1. sshd
Write-Step '1. sshd の起動・自動起動化'
$svc = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $svc) {
  Write-Error 'OpenSSH Server (sshd サービス) が存在しません。Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 でインストールしてください。'
  exit 1
}
if ($svc.StartType -ne 'Automatic') {
  Set-Service sshd -StartupType Automatic
  Write-Ok 'StartupType = Automatic に設定'
} else {
  Write-Skip 'StartupType は既に Automatic'
}
if ($svc.Status -ne 'Running') {
  Start-Service sshd
  Write-Ok 'sshd 起動'
} else {
  Write-Skip 'sshd は既に Running'
}

# 2. Firewall (LAN のみ許可)
Write-Step '2. ファイアウォールルール (LAN のみ) の確認/作成'
$ruleName = 'OpenSSH-Server-In-TCP'
$rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if (-not $rule) {
  New-NetFirewallRule -Name $ruleName -DisplayName 'OpenSSH Server (sshd)' `
    -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
    -LocalPort 22 -RemoteAddress LocalSubnet | Out-Null
  Write-Ok 'ルール作成（LocalSubnet 限定）'
} else {
  # 既存ルールが全開なら LAN 限定に絞る
  $addrFilter = $rule | Get-NetFirewallAddressFilter
  if ($addrFilter.RemoteAddress -ne 'LocalSubnet') {
    Set-NetFirewallRule -Name $ruleName -RemoteAddress LocalSubnet
    Write-Ok 'ルール RemoteAddress を LocalSubnet に更新'
  } else {
    Write-Skip 'ルール既存 (LocalSubnet 限定済み)'
  }
  if (-not $rule.Enabled) {
    Set-NetFirewallRule -Name $ruleName -Enabled True
    Write-Ok 'ルール有効化'
  }
}

# 3. 公開鍵登録 (administrators_authorized_keys)
Write-Step '3. administrators_authorized_keys への公開鍵追記'
$adminKeysDir = Join-Path $env:ProgramData 'ssh'
$adminKeys = Join-Path $adminKeysDir 'administrators_authorized_keys'
if (-not (Test-Path $adminKeysDir)) {
  New-Item -ItemType Directory -Force -Path $adminKeysDir | Out-Null
}

$normalizedKey = $PublicKey.Trim()
if (-not $normalizedKey) {
  Write-Error '公開鍵が空です。-PublicKey で指定してください。'
  exit 1
}

$existing = @()
if (Test-Path $adminKeys) {
  $existing = Get-Content -Path $adminKeys -ErrorAction SilentlyContinue | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}
if ($existing -contains $normalizedKey) {
  Write-Skip '同一公開鍵が既に登録済み'
} else {
  # ファイルを UTF-8 (BOMなし) で追記（sshd は BOM を嫌うことがあるため）
  $newContent = @()
  if ($existing) { $newContent += $existing }
  $newContent += $normalizedKey
  [System.IO.File]::WriteAllLines($adminKeys, $newContent, (New-Object System.Text.UTF8Encoding($false)))
  Write-Ok '公開鍵を追記'
}

# 4. ACL 設定
Write-Step '4. administrators_authorized_keys の ACL 設定'
icacls $adminKeys /inheritance:r | Out-Null
icacls $adminKeys /grant 'Administrators:F' | Out-Null
icacls $adminKeys /grant 'SYSTEM:F' | Out-Null
Write-Ok 'ACL: Administrators:F / SYSTEM:F (継承無効)'

# 5. sshd_config
Write-Step '5. sshd_config の PubkeyAuthentication / PasswordAuthentication 確認'
$sshdConfig = Join-Path $adminKeysDir 'sshd_config'
if (-not (Test-Path $sshdConfig)) {
  Write-Error "$sshdConfig が見つかりません。sshd が一度も起動していない可能性があります。"
  exit 1
}

function Set-SshdConfigOption {
  param(
    [string]$Path,
    [string]$Key,
    [string]$Value
  )
  $lines = Get-Content -Path $Path
  $pattern = "^[#\s]*${Key}\s+.*$"
  $newLine = "${Key} ${Value}"
  $found = $false
  $changed = $false
  $out = foreach ($l in $lines) {
    if ($l -match $pattern) {
      $found = $true
      if ($l -ne $newLine) {
        $changed = $true
        $newLine
      } else {
        $l
      }
    } else {
      $l
    }
  }
  if (-not $found) {
    $out += $newLine
    $changed = $true
  }
  if ($changed) {
    [System.IO.File]::WriteAllLines($Path, $out, (New-Object System.Text.UTF8Encoding($false)))
  }
  return $changed
}

$c1 = Set-SshdConfigOption -Path $sshdConfig -Key 'PubkeyAuthentication' -Value 'yes'
$c2 = Set-SshdConfigOption -Path $sshdConfig -Key 'PasswordAuthentication' -Value 'no'
if ($c1 -or $c2) {
  Write-Ok 'sshd_config を更新'
} else {
  Write-Skip 'sshd_config は既に目的値'
}

# 6. sshd 再起動
Write-Step '6. sshd 再起動'
Restart-Service sshd
Write-Ok 'sshd 再起動'

# 7. サマリ
Write-Step '7. 最終状態サマリ'
Get-Service sshd | Format-List Name,Status,StartType
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' |
  Format-List DisplayName,Enabled,Direction,Action,Profile
$addr = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' | Get-NetFirewallAddressFilter
Write-Host "RemoteAddress: $($addr.RemoteAddress)"
Write-Host "登録済み公開鍵:"
Get-Content $adminKeys | ForEach-Object { Write-Host "  $_" }

Write-Host ''
Write-Host 'Sub PC 側の準備は完了です。' -ForegroundColor Green
Write-Host '次のステップ: Pi の Cloudflare Tunnel に subpcssh.iniwach.com -> tcp://192.168.1.211:22 の ingress を追加してください。'
Write-Host '詳細: docs/guides/remote_pc_subpc_vscode_access.md'
