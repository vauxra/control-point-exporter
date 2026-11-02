param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [Parameter(Mandatory = $true)][string]$PythonExe
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Use native Windows x64 with Visual Studio 2022 C++ Build Tools.' }
$sourceDirectory = (Resolve-Path -LiteralPath $SourceRoot).Path
$python = (Resolve-Path -LiteralPath $PythonExe).Path
$nativeRoot = Join-Path $sourceDirectory 'native-build'
$lxmlRoot = Join-Path $sourceDirectory 'lxml'
foreach ($required in @('libiconv', 'libxml2', 'libxslt', 'zlib')) {
    if (-not (Test-Path -LiteralPath (Join-Path $nativeRoot $required))) { throw "Missing complete source tree: $required" }
}
if (-not (Test-Path -LiteralPath (Join-Path $lxmlRoot 'setup.py'))) { throw 'Missing lxml source tree.' }
& $python -c "import sys, struct, setuptools; assert sys.platform == 'win32' and struct.calcsize('P') == 8; assert sys.version_info[:2] == (3,12)"
if ($LASTEXITCODE -ne 0) { throw 'Use Python 3.12 x64 with setuptools and wheel installed.' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Install Visual Studio 2022 C++ Build Tools and a Windows 10/11 SDK.' }
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'Visual Studio C++ x64 tools were not found.' }
$developerCommand = Join-Path $visualStudio 'Common7\Tools\VsDevCmd.bat'
# This cmd invocation only reads the compiler environment; file operations stay in PowerShell.
$compilerEnvironment = & $env:ComSpec /d /s /c "call `"$developerCommand`" -no_logo -arch=x64 -host_arch=x64 && set"
if ($LASTEXITCODE -ne 0) { throw 'Could not load the x64 compiler environment.' }
foreach ($line in $compilerEnvironment) {
    if ($line -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
$sdkVersion = $env:WindowsSDKVersion.TrimEnd('\')
$iconvRoot = Join-Path $nativeRoot 'libiconv'
$zlibRoot = Join-Path $nativeRoot 'zlib'
$xmlRoot = Join-Path $nativeRoot 'libxml2'
$xsltRoot = Join-Path $nativeRoot 'libxslt'
Push-Location -LiteralPath $iconvRoot
try {
    & git apply --check '..\libiconv.patch' 2>$null
    if ($LASTEXITCODE -eq 0) {
        & git apply '..\libiconv.patch'
        if ($LASTEXITCODE -ne 0) { throw 'Could not apply the included upstream iconv patch.' }
    } else {
        & git apply --reverse --check '..\libiconv.patch' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'The upstream iconv patch neither applies nor is already applied.' }
    }
    & msbuild 'MSVC17\libiconv_static\libiconv_static.vcxproj' /nologo /verbosity:minimal /p:Configuration=Release /p:Platform=x64 "/p:WindowsTargetPlatformVersion=$sdkVersion"
    if ($LASTEXITCODE -ne 0) { throw 'libiconv compilation failed.' }
} finally { Pop-Location }
$iconvLib = Join-Path $iconvRoot 'MSVC17\x64\lib'
Copy-Item -LiteralPath (Join-Path $iconvLib 'libiconv_a.lib') -Destination (Join-Path $iconvLib 'iconv_a.lib') -Force
$iconvInclude = Join-Path $iconvRoot 'source\include'
Push-Location -LiteralPath $zlibRoot
try {
    & nmake /nologo -f 'win32\Makefile.msc' zlib_a.lib
    if ($LASTEXITCODE -ne 0) { throw 'zlib compilation failed.' }
    Copy-Item -LiteralPath 'zlib_a.lib' -Destination 'zlib.lib' -Force
} finally { Pop-Location }
Push-Location -LiteralPath (Join-Path $xmlRoot 'win32')
try {
    & cscript //nologo configure.js "lib=$zlibRoot;$iconvLib" "include=$zlibRoot;$iconvInclude" vcmanifest=yes zlib=yes
    if ($LASTEXITCODE -ne 0) { throw 'libxml2 configuration failed.' }
    & nmake /nologo libxmla
    if ($LASTEXITCODE -ne 0) { throw 'libxml2 compilation failed.' }
} finally { Pop-Location }
$xmlLib = Join-Path $xmlRoot 'win32\bin.msvc'
$xmlInclude = Join-Path $xmlRoot 'include'
Push-Location -LiteralPath (Join-Path $xsltRoot 'win32')
try {
    & cscript //nologo configure.js "lib=$zlibRoot;$iconvLib;$xmlLib" "include=$zlibRoot;$iconvInclude;$xmlInclude" vcmanifest=yes zlib=yes
    if ($LASTEXITCODE -ne 0) { throw 'libxslt configuration failed.' }
    & nmake /nologo libxslta libexslta
    if ($LASTEXITCODE -ne 0) { throw 'libxslt compilation failed.' }
} finally { Pop-Location }
$xsltLib = Join-Path $xsltRoot 'win32\bin.msvc'
# Use only the locally rebuilt libraries. STATICBUILD/STATIC_DEPS would download originals.
Remove-Item Env:STATICBUILD -ErrorAction SilentlyContinue
Remove-Item Env:STATIC_DEPS -ErrorAction SilentlyContinue
$env:LXML_STATIC_INCLUDE_DIRS = "$xmlInclude;$xsltRoot;$iconvInclude;$zlibRoot"
$env:LXML_STATIC_LIBRARY_DIRS = "$xmlLib;$xsltLib;$iconvLib;$zlibRoot"
$env:LXML_STATIC_CFLAGS = '/DLIBXML_STATIC /DLIBXSLT_STATIC /DLIBEXSLT_STATIC'
Push-Location -LiteralPath $lxmlRoot
try {
    & $python setup.py bdist_wheel --static --without-cython
    if ($LASTEXITCODE -ne 0) { throw 'lxml compilation/linking failed.' }
    Write-Host "Rebuilt wheel is in $lxmlRoot\dist. Install it into the app environment, then build the EXE using -SkipSync."
} finally { Pop-Location }
