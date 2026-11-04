# XML stack source and native Windows rebuild materials

This archive accompanies Control Point Exporter 1.0.2. The application's own source is MIT-licensed. GNU libiconv retains LGPL 2.1; the other bundled components retain their respective licenses. The app's About view includes their notices and this archive's release URL. No additional restrictions on modifying or debugging your rebuilt application are imposed.

The archive contains complete source trees, build files, and licenses for the lxml XML stack: `native-build/` is upstream `lxml/libxml2-win-binaries` at tag `2026.05.17`, with all four exact submodules expanded; `lxml/` is the hash-verified lxml 6.1.3 source distribution. `application-source/` contains the public application source, fixtures, lockfile, and packaging scripts. `SOURCE-MANIFEST.json` records immutable URLs, commits, versions, and archive hashes. `SOURCE-FILE-SHA256.json` records every included file except itself.

The original wheel hash is proven by `uv.lock`. Selecting the matching upstream Windows build recipe is an inference: it is the latest published recipe before the wheel's upload and matches libxml2 2.11.9, libxslt 1.1.45, and the exposed iconv 1.17 identifier. The Windows libiconv port calls its package 1.17.1. We do not claim the upstream wheel published a complete build attestation. The supplied source commits and hashes are exact and can be used to rebuild the stack locally.

## Rebuild with a modified libiconv

Use Windows x64, Visual Studio 2022 C++ Build Tools (including an x64 compiler and Windows SDK), Git for Windows, uv, and Python 3.12 x64. No Linux, WSL, or separate graphics card is used. Extract the archive into a new working folder, then run PowerShell there. Build tools may download Python packages during setup; end users running the EXE need no toolchain.

```powershell
Push-Location application-source
uv sync --locked --group dev
$appPython = (Resolve-Path .venv\Scripts\python.exe).Path
uv pip install --python $appPython setuptools wheel
Pop-Location
```

Edit the supplied `native-build\libiconv` source as desired. Then rebuild it, the other static libraries, and lxml from these local source trees:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File application-source\scripts\Rebuild-Lxml.ps1 -SourceRoot . -PythonExe $appPython
$rebuiltWheel = (Get-ChildItem lxml\dist\lxml-6.1.3-cp312-cp312-win_amd64.whl).FullName
uv pip install --python $appPython --force-reinstall --no-deps $rebuiltWheel
& $appPython -c "from lxml import etree; print(etree.LIBXML_VERSION, etree.LIBXSLT_VERSION, etree.ICONV_COMPILED_VERSION)"
```

The helper uses the included MSVC project, upstream iconv patch, native `nmake`/`cscript` builds, and lxml's documented static include/library environment variables. It links against those local libraries rather than downloading replacements. Its source is included for inspection or adjustment for a different Visual Studio/SDK setup. This produces a new wheel; a bit-for-bit match to the upstream wheel is not expected.

Package the application with your rebuilt dependency:

```powershell
Push-Location application-source
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Build.ps1 -SkipSync -OutputDirectory dist\custom
& $appPython scripts\smoke_exe.py --exe dist\custom\ControlPointExporter.exe
Pop-Location
```

**Keep `-SkipSync` for this custom build.** Synchronizing with `uv.lock` would reinstall the original upstream lxml wheel and replace your rebuilt library. The application tests and synthetic fixtures are included; the private EAD corpus is unnecessary for rebuilding or ordinary tests.

To collect this source archive again from the public project, run `python scripts\collect_third_party_sources.py`. Downloads are size- and SHA-256-verified against the included manifest. The resulting ZIP and checksum are placed in the version's release output directory.
