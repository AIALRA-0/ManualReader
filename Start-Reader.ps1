param([int]$Port = 8939, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$readerRoot = $PSScriptRoot
$readerBundled = Join-Path $readerRoot 'runtime/python.exe'
$readerPython = if (Test-Path -LiteralPath $readerBundled) { $readerBundled } else { 'python' }
$readerArgs = @((Join-Path $readerRoot 'bootstrap_reader.py'), '--port', "$Port")
if ($NoBrowser) { $readerArgs += '--no-browser' }
$env:PYTHONIOENCODING = 'utf-8'
& $readerPython @readerArgs
if ($LASTEXITCODE -ne 0) { throw '阅读器启动失败，请查看 reader-server.stderr.log' }
