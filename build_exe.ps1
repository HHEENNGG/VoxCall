<#
.SYNOPSIS
    把 VoxCall 打包成独立 exe（PyInstaller onedir，不需要目标机器装 Python）。

.EXAMPLE
    .\build_exe.ps1
    .\build_exe.ps1 -Zip
#>
[CmdletBinding()]
param(
    [switch]$Zip,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# 选解释器：优先项目内便携运行时
if (-not $Python) {
    if (Test-Path "runtime\python313\python\python.exe") {
        $Python = Join-Path $PSScriptRoot "runtime\python313\python\python.exe"
    } elseif (Test-Path ".venv\Scripts\python.exe") {
        $Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    } else {
        throw "找不到 Python 运行时，请先运行 install.ps1 -Portable"
    }
}

Write-Host "使用: $Python" -ForegroundColor Cyan

Write-Host "检查 PyInstaller …" -ForegroundColor Cyan
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "未安装，正在安装 pyinstaller …" -ForegroundColor Yellow
    & $Python -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib
    if ($LASTEXITCODE -ne 0) { throw "安装 PyInstaller 失败" }
}

$build = Join-Path $PSScriptRoot "_build"
$dist = Join-Path $PSScriptRoot "_dist"
$tmp = Join-Path $PSScriptRoot "_buildtmp"
New-Item -ItemType Directory -Force $tmp | Out-Null
$env:TMP = $tmp
$env:TEMP = $tmp
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "开始打包（约 1–2 分钟）…" -ForegroundColor Cyan
& $Python -m PyInstaller --noconfirm --clean --windowed --name VoxCall `
    --distpath $dist --workpath $build --specpath $build --paths $PSScriptRoot `
    --icon (Join-Path $PSScriptRoot "assets\icon.ico") `
    --add-data "$(Join-Path $PSScriptRoot 'assets\icon.ico');assets" `
    --add-data "$(Join-Path $PSScriptRoot 'assets\vbcable\VBCABLE_Driver_Pack45.zip');assets\vbcable" `
    --collect-data sounddevice `
    --exclude-module tkinter --exclude-module unittest --exclude-module pydoc `
    --exclude-module lib2to3 --exclude-module hf_xet `
    --exclude-module PIL --exclude-module soundfile `
    (Join-Path $PSScriptRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

# 去掉用不到的组件（只裁尺寸，不影响 QWidget 界面与音频管线）
# 注意：QtNetwork 不能删——单实例用 QLocalServer/QLocalSocket
$internal = Join-Path $dist "VoxCall\_internal"
foreach ($junk in @(
        "PySide6\opengl32sw.dll",          # 软件 OpenGL 回退，QWidget 用不到（19.7 MB）
        "PySide6\plugins\sqldrivers",      # 不用数据库
        "hf_xet",                          # HuggingFace 传输加速，模型已内置
        "PIL",                             # 只在转图标时用到，运行时不需要
        "_soundfile_data"                  # 只有 load_audio 用，模型已内置
    )) {
    $target = Join-Path $internal $junk
    if (Test-Path $target) { Remove-Item $target -Recurse -Force; Write-Host "  - 移除 $junk" -ForegroundColor Gray }
}

# 把模型、配置、文档放到 exe 同级目录（程序按 exe 位置查找这些文件）
$out = Join-Path $dist "VoxCall"
Copy-Item (Join-Path $PSScriptRoot "models") $out -Recurse -Force
# README 不放软件目录（只在源码/仓库里），软件目录只留运行必需 + 许可
foreach ($f in @("config.toml", "LICENSE", "NOTICE.md")) {
    Copy-Item (Join-Path $PSScriptRoot $f) $out -Force
}

$size = [math]::Round(((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 0)
Write-Host ""
Write-Host "打包完成：$out（约 $size MB）" -ForegroundColor Green
Write-Host "  双击 VoxCall.exe 即可运行（无控制台）。" -ForegroundColor Gray

if ($Zip) {
    $zipPath = Join-Path $PSScriptRoot "VoxCall-0.02-beta-Win64.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Write-Host "压缩中（280 MB 左右，需要几分钟）…" -ForegroundColor Cyan
    Compress-Archive -Path $out -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Host "已生成：$zipPath" -ForegroundColor Green
}
