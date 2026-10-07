#requires -Version 5.1
<#
  ZCode Hub packaging build.

  ASCII-only on purpose: Windows PowerShell 5.1 reads a .ps1 without a BOM as the
  system ANSI codepage, so any non-ASCII literal in this file would turn into
  mojibake. Chinese text lives in usage.zh.txt / ZCodeHub.iss (the latter gets a
  UTF-8 BOM written here before ISCC sees it) and the staged guide file name is
  built from code points below.

  Steps: ensure PyInstaller -> freeze exe -> assemble stage/ -> (optional) ISCC.
  Idempotent; run it again after any source change.

  .\build.ps1                    full build (freeze + stage + installer)
  .\build.ps1 -SkipInstaller     stage only (no Inno Setup needed)
  .\build.ps1 -Zip               also emit a portable green zip of stage/
  .\build.ps1 -WithExistingData  ship repo/data into the package (credentials!)
  .\build.ps1 -Clean             wipe previous build artifacts first
#>
[CmdletBinding()]
param(
    [switch]$SkipFreeze,
    [switch]$SkipInstaller,
    [switch]$Zip,
    [switch]$WithExistingData,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

$PkgDir   = $PSScriptRoot
$Repo     = Split-Path -Parent $PkgDir
$VenvPy   = Join-Path $Repo '.venv\Scripts\python.exe'
$DistDir  = Join-Path $PkgDir 'dist'
$WorkDir  = Join-Path $PkgDir 'work'
$StageDir = Join-Path $PkgDir 'stage'
$OutDir   = Join-Path $PkgDir 'output'
$ExeName  = 'ZCodeHub.exe'
$TrayExeName = 'ZCodeHubTray.exe'
# Admin key written into the staged .env (login: password only, no username).
$DefaultAdminKey = 'zcode'
# Guide file name built from code points so this file stays pure ASCII (see header).
$GuideName = (-join [char[]]@(0x4F7F, 0x7528, 0x8BF4, 0x660E)) + '.txt'

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Read-Utf8($path) {
    if (-not (Test-Path $path)) { throw "missing file: $path" }
    [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
}

function Write-Utf8($path, $text, [switch]$Bom) {
    # .env must stay BOM-less: python-dotenv would fold the BOM into the first key.
    $enc = New-Object System.Text.UTF8Encoding([bool]$Bom)
    [System.IO.File]::WriteAllText($path, $text, $enc)
}

function Sync-Tree($src, $dst, [string[]]$Extra = @()) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    # /MIR keeps stage in sync without re-copying 58 MB of node_modules every run.
    # robocopy: 0-7 success, >=8 failure.
    & robocopy $src $dst /MIR /NFL /NDL /NJH /NJS /NP @Extra | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $src -> $dst" }
    $global:LASTEXITCODE = 0
}

# Native CLIs (uv / PyInstaller / ISCC) log progress on stderr, which PS 5.1
# escalates into a terminating error under ErrorActionPreference=Stop. Run them
# with Continue and judge the exit code instead.
function Run-Native([scriptblock]$Block, [string]$Label) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit code $LASTEXITCODE)" }
}

if (-not (Test-Path $VenvPy)) {
    throw "venv python not found: $VenvPy`nCreate it first: uv sync (or python -m venv .venv && pip install -r requirements.txt)"
}

$Version = (Read-Utf8 (Join-Path $Repo 'frontend\version')).Trim()
if ($Version -notmatch '^\d+(\.\d+){1,3}$') { throw "bad version in frontend/version: '$Version'" }
$VersionNum = if ($Version.Split('.').Count -ge 4) { $Version } else { "$Version.0" }

if ($Clean) {
    Write-Step 'cleaning previous artifacts'
    foreach ($d in @($DistDir, $WorkDir, $StageDir, $OutDir)) {
        Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# -- 1. Build deps in the venv (it has no pip; use uv) ------------------------
if (-not $SkipFreeze) {
    Write-Step 'checking build deps in venv'
    # PS 5.1 turns a native command's stderr into a terminating error when
    # ErrorActionPreference=Stop; the probe below deliberately writes stderr.
    $ErrorActionPreference = 'Continue'
    & $VenvPy -m PyInstaller --version 2>$null | Out-Null
    $HasPyi = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    if (-not $HasPyi) {
        Write-Host 'installing pyinstaller + pystray + pillow into .venv via uv'
        Run-Native { & uv pip install pyinstaller pystray pillow --python $VenvPy } 'uv pip install'
    }
    $PyiVer = (& $VenvPy -m PyInstaller --version 2>$null).Trim()
    Write-Host "PyInstaller $PyiVer"

    Write-Step 'generating ZCodeHub.ico'
    Run-Native { & $VenvPy (Join-Path $PkgDir 'make_icon.py') } 'make_icon'

    Write-Step "freezing $ExeName + $TrayExeName (onedir)"
    $spec = Join-Path $PkgDir 'ZCodeHub.spec'
    Run-Native {
        # --specpath is rejected when a .spec is given; the spec's own directory is
        # used, and SPECPATH inside it already resolves the repo root.
        & $VenvPy -m PyInstaller --noconfirm --clean `
            --distpath $DistDir --workpath $WorkDir $spec
    } 'PyInstaller'
}

if (-not (Test-Path (Join-Path $DistDir "ZCodeHub\$ExeName"))) {
    throw "frozen exe not found under $DistDir - run without -SkipFreeze"
}
if (-not (Test-Path (Join-Path $DistDir "ZCodeHub\$TrayExeName"))) {
    throw "frozen tray exe not found under $DistDir"
}

# -- 2. Stage: the exact layout that ends up on disk after install ------------
Write-Step 'assembling stage/'
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
# /XD protects the sibling subtrees below from /MIR's purge (and keeps the
# node_modules copy incremental instead of 58 MB per run).
Sync-Tree (Join-Path $DistDir 'ZCodeHub') $StageDir @('/XD', 'data', 'frontend', 'captcha_node')
Sync-Tree (Join-Path $Repo 'frontend') (Join-Path $StageDir 'frontend')
Sync-Tree (Join-Path $Repo 'captcha_node') (Join-Path $StageDir 'captcha_node')

$StageData = Join-Path $StageDir 'data'
if ($WithExistingData) {
    Write-Host 'WARNING: shipping repo/data -> package contains live account credentials. Do not distribute.' -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $StageData | Out-Null
    Sync-Tree (Join-Path $Repo 'data') $StageData
} else {
    # A local test run leaves stage/data/accounts.db behind (it holds whatever the
    # gateways were configured with). The package must ship an EMPTY data/ so the
    # first start builds a fresh store.py database -- never our own credentials.
    if (Test-Path -LiteralPath $StageData) {
        Remove-Item -LiteralPath $StageData -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $StageData | Out-Null
    $Leftovers = @(Get-ChildItem -LiteralPath $StageData -Force)
    if ($Leftovers.Count -gt 0) {
        throw ('stage/data is not empty after purge: ' + ($Leftovers.Name -join ', ') +
               ' -- refusing to build a package that may carry credentials')
    }
}

# .env: derived from .env.example, loopback only, packaged default admin key stamped in.
$EnvText = Read-Utf8 (Join-Path $Repo '.env.example')
$EnvText = $EnvText -replace '(?m)^ZCODE_HOST=.*$', 'ZCODE_HOST=127.0.0.1'
$EnvText = $EnvText -replace '(?m)^ZCODE_ADMIN_KEY=.*$', ('ZCODE_ADMIN_KEY=' + $DefaultAdminKey)
$EnvText = "# packaged build v$Version, generated by packaging/build.ps1`n" +
           "# Loopback only by default -- opening ZCODE_HOST to the LAN shares this admin key.`n" +
           $EnvText
Write-Utf8 (Join-Path $StageDir '.env') $EnvText

# Usage guide with the version stamped in.
$Guide = (Read-Utf8 (Join-Path $PkgDir 'usage.zh.txt')).Replace('__APP_VERSION__', $Version)
Write-Utf8 (Join-Path $StageDir $GuideName) $Guide -Bom

$StageSize = (Get-ChildItem $StageDir -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("stage ready: {0}  ~{1:N1} MB" -f $StageDir, $StageSize)

# -- 3. Installer --------------------------------------------------------------
if ($Zip) {
    Write-Step 'zipping portable build'
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $zipPath = Join-Path $OutDir "ZCodeHub-$Version-portable.zip"
    Compress-Archive -Path (Join-Path $StageDir '*') -DestinationPath $zipPath -Force
    Write-Host "zip: $zipPath"
}

if ($SkipInstaller) {
    Write-Step 'installer skipped (-SkipInstaller)'
    return
}

Write-Step 'locating Inno Setup (ISCC.exe)'
$Iscc = @(
    (Join-Path ${env:ProgramFiles} 'Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $Iscc) {
    Write-Host 'Inno Setup not found. Install it, then re-run:' -ForegroundColor Yellow
    Write-Host '    winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements'
    throw 'ISCC.exe missing'
}
Write-Host "ISCC: $Iscc"

# ISCC needs the script as UTF-8 with BOM to render the Chinese wizard strings.
$IssSrc = Join-Path $PkgDir 'ZCodeHub.iss'
$IssBom = Join-Path $PkgDir 'ZCodeHub.bom.iss'
Write-Utf8 $IssBom (Read-Utf8 $IssSrc) -Bom

Run-Native { & $Iscc "/DMyAppVersion=$Version" "/DMyAppVersionNum=$VersionNum" $IssBom } 'ISCC'

$Setup = Join-Path $OutDir "ZCodeHub-Setup-$Version.exe"
if (-not (Test-Path $Setup)) { throw "expected installer not produced: $Setup" }
Write-Step 'done'
Write-Host ("installer: {0}  ~{1:N1} MB" -f $Setup, ((Get-Item $Setup).Length / 1MB))
