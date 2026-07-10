param(
    [switch]$UseLocalWheel
)

$ErrorActionPreference = 'Stop'
$BuildDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Parley - pyapp build environment setup" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# Pinned pyapp release: recorded hash keeps the exe build reproducible and the
# supply chain auditable. To bump: download the new source.tar.gz, record its
# SHA-256 here in the same commit.
$PyAppVersion = '0.29.0'
$PyAppUrl = "https://github.com/ofek/pyapp/releases/download/v$PyAppVersion/source.tar.gz"
$PyAppSha256 = '533004baf6d1d46ef15abad02e98881ec92291c024182a36b57931c8d66df5f'

$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $cargo) {
    Write-Host "[!] Rust toolchain not found (cargo not on PATH)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    Install Rust via rustup (one-time):" -ForegroundColor White
    Write-Host "      Invoke-WebRequest -Uri https://win.rustup.rs/x86_64 -OutFile rustup-init.exe" -ForegroundColor Gray
    Write-Host "      .\rustup-init.exe -y" -ForegroundColor Gray
    Write-Host "      Remove-Item rustup-init.exe" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    Then RE-OPEN your terminal and re-run this script." -ForegroundColor White
    Write-Host ""
    $proceed = Read-Host "Try the install commands above for you? [y/N]"
    if ($proceed -eq 'y' -or $proceed -eq 'Y') {
        Invoke-WebRequest -Uri 'https://win.rustup.rs/x86_64' -OutFile "$env:TEMP\rustup-init.exe"
        & "$env:TEMP\rustup-init.exe" -y
        Remove-Item "$env:TEMP\rustup-init.exe" -ErrorAction SilentlyContinue
        Write-Host ""
        Write-Host "Rust installed. Re-open your terminal and re-run this script." -ForegroundColor Green
        exit 0
    } else {
        exit 1
    }
} else {
    Write-Host "[OK] cargo: $($cargo.Source)" -ForegroundColor Green
}

$PyAppDir = Join-Path $BuildDir 'pyapp-source'
$PyAppArchive = Join-Path $BuildDir 'pyapp-source.tar.gz'

if (Test-Path (Join-Path $PyAppDir 'Cargo.toml')) {
    Write-Host "[OK] pyapp source already present at $PyAppDir" -ForegroundColor Green
} else {
    Write-Host "[~] Downloading pyapp v$PyAppVersion source..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $PyAppUrl -OutFile $PyAppArchive

    $ActualHash = (Get-FileHash -Algorithm SHA256 $PyAppArchive).Hash.ToLower()
    if ($ActualHash -ne $PyAppSha256) {
        Remove-Item $PyAppArchive -ErrorAction SilentlyContinue
        Write-Host "[!] SHA-256 mismatch for pyapp source (expected $PyAppSha256, got $ActualHash)." -ForegroundColor Red
        Write-Host "    The upstream artifact changed since it was pinned - re-verify before updating the pin." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] pyapp archive verified (sha256 $($PyAppSha256.Substring(0,12))...)" -ForegroundColor Green

    if (Test-Path $PyAppDir) { Remove-Item -Recurse -Force $PyAppDir }
    New-Item -ItemType Directory -Path $PyAppDir | Out-Null

    Write-Host "[~] Extracting..." -ForegroundColor Yellow
    tar -xzf $PyAppArchive -C $PyAppDir --strip-components=1

    Remove-Item $PyAppArchive -ErrorAction SilentlyContinue

    if (-not (Test-Path (Join-Path $PyAppDir 'Cargo.toml'))) {
        Write-Host "[!] Extraction failed - no Cargo.toml in $PyAppDir" -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] pyapp source extracted to $PyAppDir" -ForegroundColor Green
}

$ConfigFile = Join-Path $BuildDir 'build-config.json'
if (-not (Test-Path $ConfigFile)) {
    $config = @{
        pyapp_source_path = '.\pyapp-source'
        dist_path = 'dist'
    } | ConvertTo-Json
    Set-Content -Path $ConfigFile -Value $config -Encoding UTF8
    Write-Host "[OK] Created $ConfigFile" -ForegroundColor Green
} else {
    Write-Host "[OK] $ConfigFile already exists" -ForegroundColor Green
}

if ($UseLocalWheel) {
    Write-Host ""
    Write-Host "[~] Building local wheel..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    if (Test-Path 'dist') { Remove-Item -Recurse -Force 'dist' }
    python -m build --wheel 2>&1 | Select-Object -Last 3
    Pop-Location

    $Wheel = Get-ChildItem (Join-Path $ProjectRoot 'dist\*.whl') | Select-Object -First 1
    if ($Wheel) {
        Write-Host "[OK] Wheel: $($Wheel.FullName)" -ForegroundColor Green
        Write-Host "    To build pyapp against this local wheel:" -ForegroundColor White
        Write-Host "      `$env:PYAPP_PROJECT_PATH = '$($Wheel.FullName)'" -ForegroundColor Gray
        Write-Host "      .\pyapp-build\build-pyapp.ps1" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "Setup complete. Now run:" -ForegroundColor Cyan
Write-Host "  .\pyapp-build\build-pyapp.ps1" -ForegroundColor White
Write-Host ""
Write-Host "Note: by default pyapp downloads 'parley' from PyPI at first launch." -ForegroundColor DarkYellow
Write-Host "If you haven't published yet, re-run with -UseLocalWheel to build against the local wheel." -ForegroundColor DarkYellow
