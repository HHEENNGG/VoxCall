<#
.SYNOPSIS
    VoxCall 安装脚本。

.DESCRIPTION
    两种装法：
      默认（虚拟环境）  用系统里的 Python 3.11–3.13 建 .venv
      -Portable         下载便携版 CPython 3.13 到 runtime\python313，不装系统、不改 PATH、
                        不写注册表，删掉 runtime 目录即可卸载

.EXAMPLE
    .\install.ps1 -Portable
    .\install.ps1 -InstallPython      # 没有 3.11–3.13 时用 winget 装一个
#>
[CmdletBinding()]
param(
    [switch]$InstallPython,
    [switch]$Portable,
    [string]$RuntimeDir = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$CANDIDATES = @("3.13", "3.12", "3.11")
# python-build-standalone 的 install_only 构建：免安装、带 pip，解压即用
$PORTABLE_URLS = @(
    "https://github.com/astral-sh/python-build-standalone/releases/download/20250818/cpython-3.13.7+20250818-x86_64-pc-windows-msvc-install_only.tar.gz",
    "https://github.com/astral-sh/python-build-standalone/releases/download/20250712/cpython-3.13.5+20250712-x86_64-pc-windows-msvc-install_only.tar.gz",
    "https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.13.0+20241016-x86_64-pc-windows-msvc-install_only.tar.gz"
)

function Find-SuitablePython {
    foreach ($version in $CANDIDATES) {
        try {
            $output = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
        } catch {
            $output = $null
        }
        if ($LASTEXITCODE -eq 0 -and $output) {
            return @{ Version = $version; Exe = "$output".Trim() }
        }
    }
    return $null
}

function Get-PortablePython {
    param([string]$Destination)
    New-Item -ItemType Directory -Force $Destination | Out-Null
    $tarball = Join-Path $Destination "python313.tar.gz"
    foreach ($url in $PORTABLE_URLS) {
        try {
            Write-Host "下载便携版 Python：$($url.Split('/')[-1])" -ForegroundColor Cyan
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                & curl.exe -L --fail --retry 3 --retry-delay 2 -o $tarball $url
                if ($LASTEXITCODE -ne 0) { throw "curl 退出码 $LASTEXITCODE" }
            } else {
                Invoke-WebRequest -Uri $url -OutFile $tarball -UseBasicParsing
            }
            & tar.exe -xzf $tarball -C $Destination
            if ($LASTEXITCODE -ne 0) { throw "tar 解压失败" }
            Remove-Item $tarball -Force -ErrorAction SilentlyContinue
            $exe = Join-Path $Destination "python\python.exe"
            if (Test-Path $exe) { return $exe }
            throw "解压后没找到 python\python.exe"
        } catch {
            Write-Warning "该地址失败：$($_.Exception.Message)"
        }
    }
    return $null
}

function Install-Requirements {
    param([string]$Exe, [string]$Label)
    Write-Host "使用 $Label: $Exe" -ForegroundColor Green
    & $Exe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "升级 pip 失败" }
    Write-Host "安装依赖（首次约 200 MB，需要几分钟）…" -ForegroundColor Cyan
    & $Exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "依赖安装失败。常见原因：" -ForegroundColor Red
        Write-Host "  - 网络问题：可加国内镜像  -i https://pypi.tuna.tsinghua.edu.cn/simple" -ForegroundColor Gray
        Write-Host "  - 模型下载失败：先设置  `$env:HF_ENDPOINT='https://hf-mirror.com'  再启动" -ForegroundColor Gray
        exit 1
    }
    Write-Host ""
    Write-Host "运行环境自检：" -ForegroundColor Cyan
    & $Exe main.py --doctor
}

Write-Host "VoxCall 安装程序" -ForegroundColor Cyan

if ($Portable) {
    $dir = if ($RuntimeDir) { $RuntimeDir } else { Join-Path $PSScriptRoot "runtime\python313" }
    $exe = Join-Path $dir "python\python.exe"
    if (-not (Test-Path $exe)) {
        $exe = Get-PortablePython -Destination $dir
    } else {
        Write-Host "已存在便携运行时，复用：$exe" -ForegroundColor Gray
    }
    if (-not $exe) {
        Write-Host "便携版 Python 下载失败。检查网络，或改用默认安装方式（需要系统 Python 3.11–3.13）。" -ForegroundColor Red
        exit 1
    }
    Install-Requirements -Exe $exe -Label "便携版 Python 3.13"
    Write-Host ""
    Write-Host "安装结束（便携模式：没有改动系统环境）。启动：" -ForegroundColor Green
    Write-Host "  .\run.bat" -ForegroundColor Gray
    Write-Host "卸载：删掉 runtime 目录即可。" -ForegroundColor Gray
    exit 0
}

$python = Find-SuitablePython
if ($null -eq $python -and $InstallPython) {
    Write-Host "未找到 3.11-3.13，尝试用 winget 安装 Python 3.13 …" -ForegroundColor Yellow
    winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
    $python = Find-SuitablePython
}

if ($null -eq $python) {
    Write-Host ""
    Write-Host "找不到系统里的 Python 3.11-3.13。" -ForegroundColor Red
    Write-Host "原因：VoxLock 依赖的 onnxruntime 目前只发布到 Python 3.13。" -ForegroundColor Gray
    Write-Host "三种做法（任选其一）：" -ForegroundColor Gray
    Write-Host "  1) 便携安装，不碰系统：  .\install.ps1 -Portable" -ForegroundColor Gray
    Write-Host "  2) 用 winget 装 3.13：    .\install.ps1 -InstallPython" -ForegroundColor Gray
    Write-Host "  3) 手动装 Python 3.13（勾选 Add to PATH）后重跑本脚本" -ForegroundColor Gray
    exit 1
}

if (Test-Path ".venv") {
    Write-Host "已存在 .venv，复用。" -ForegroundColor Gray
} else {
    & $python.Exe -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
}

$venv = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Install-Requirements -Exe $venv -Label "虚拟环境 Python $($python.Version)"

Write-Host ""
Write-Host "安装结束。启动方式：" -ForegroundColor Green
Write-Host "  .\run.bat         （或 .\.venv\Scripts\python.exe main.py）" -ForegroundColor Gray
Write-Host "首次启动会下载约 75 MB 模型；在此之前界面会显示「等待目标语音」。" -ForegroundColor Gray
