# 本地打包脚本：生成单文件 EXE（带管理员权限清单）
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin `
    --name GitHubAccelerator `
    --distpath "$here\dist" --workpath "$here\build" --specpath "$here\build" `
    "$here\github_accel.py"

Write-Host ""
Write-Host "打包完成：$here\dist\GitHubAccelerator.exe" -ForegroundColor Green
