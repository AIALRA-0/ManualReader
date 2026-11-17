param(
  [string]$Wheelhouse,
  [switch]$IncludeTests
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Py = Join-Path $Root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $Py)) {
  python -m venv (Join-Path $Root '.venv')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$Deps = @('--require-hashes', '-r', (Join-Path $Root 'requirements.lock'))
if ($IncludeTests) { $Deps += @('-r', (Join-Path $Root 'tests.lock')) }
if ($Wheelhouse) { $Deps += @('--no-index','--find-links',$Wheelhouse) }
& $Py -m pip install @Deps
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Py -m pip install --no-deps --no-build-isolation $Root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Py -m pip check
exit $LASTEXITCODE
