# install-autostart.ps1 — 把常驻合成服务注册成开机自启（当前用户，无需管理员）
# 用法：powershell -ExecutionPolicy Bypass -File client\install-autostart.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$cfgPath = Join-Path $root 'config.json'
if (-not (Test-Path $cfgPath)) { throw "找不到 $cfgPath，请先复制 config.example.json 为 config.json 并填写" }

$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$pkg = $cfg.gpt_sovits_dir
$py = Join-Path $pkg $cfg.python_exec
if (-not (Test-Path $py)) { throw "找不到 Python：$py" }

$serverPy = Join-Path $root 'server\server.py'
$startup = [Environment]::GetFolderPath('Startup')
$vbs = Join-Path $startup 'local-voice-tts.vbs'

$content = @"
' 由 install-autostart.ps1 生成：登录后隐藏窗口拉起本地语音合成服务
Dim sh, D
Set sh = CreateObject("WScript.Shell")
D = Chr(34)
sh.CurrentDirectory = "$pkg"
sh.Run D & "$py" & D & " $D$serverPy$D --config $D$cfgPath$D", 0, False
"@

Set-Content -Path $vbs -Value $content -Encoding ASCII
Write-Host "已写入启动项: $vbs"
Write-Host "下次登录会自动启动；现在想立刻启动可以双击该 vbs，或直接运行 server.py。"
