param([switch]$SkipSync, [switch]$SkipTests, [string]$OutputDirectory = 'dist\windows')
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Build this application on native Windows x64.' }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location -LiteralPath $projectRoot
try {
    if (-not $SkipSync) {
        # The wrapper uses a current uv without replacing the system installation.
        & uv tool run --from uv uv sync --locked --group dev
        if ($LASTEXITCODE -ne 0) { throw 'Dependency synchronization failed.' }
    }
    $pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run uv sync --locked --group dev first.' }
    & $pythonExe -c "import sys, struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8; assert sys.version_info[:2] == (3,12)"
    if ($LASTEXITCODE -ne 0) { throw 'The build requires Windows x64 Python 3.12.' }
    if (-not $SkipTests) {
        & $pythonExe -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed; no EXE built.' }
    }
    & $pythonExe scripts\prepare_notices.py
    if ($LASTEXITCODE -ne 0) { throw 'License notice generation failed.' }
    $distributionDirectory = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
        [System.IO.Path]::GetFullPath($OutputDirectory)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
    }
    & $pythonExe -m PyInstaller --noconfirm --clean --distpath $distributionDirectory --workpath build\pyinstaller packaging\ControlPointExporter.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    $binaryPath = Join-Path $distributionDirectory 'ControlPointExporter.exe'
    & $pythonExe scripts\build_report.py --exe $binaryPath
    if ($LASTEXITCODE -ne 0) { throw 'Build report generation failed.' }
    Write-Host "Built $binaryPath with checksum and build report."
} finally {
    Pop-Location
}
