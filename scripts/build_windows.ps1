# build_windows.ps1 - builds the AKAISDS Windows executable
# Can be run from anywhere. Automatically handles venv setup if not configured.
# If PowerShell blocks running scripts, run once as your own user:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$SrcDir  = Join-Path $RootDir "src"
$VenvDir = Join-Path $RootDir ".venv"

$FreshVenv = $false
if (-not (Test-Path $VenvDir)) {
    Write-Host "No venv found - creating new venv..."
    python -m venv $VenvDir
    $FreshVenv = $true
}

. (Join-Path $VenvDir "Scripts\Activate.ps1")

if ($FreshVenv) {
    Write-Host "Fresh venv - installing requirements..."
    pip install -r (Join-Path $RootDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip install failed - fix the error above before continuing."
    }
}

Set-Location $SrcDir

if (-not (Test-Path "pysidedeploy.spec")) {
    Write-Host "No spec file found - generating from defaults"

    pyside6-deploy --init main.py

    (Get-Content pysidedeploy.spec) -replace '^title = .*', 'title = AKAISDS' | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^icon = .*', 'icon = ../assets/icon/icon_512x512.png' | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^mode = .*', 'mode = standalone' | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^extra_args = .*', 'extra_args = --quiet --noinclude-qt-translations --windows-console-mode=disable --windows-product-name=AKAISDS --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template' | Set-Content pysidedeploy.spec

    Write-Host "Spec generated and configured."
}

Write-Host "Building AKAISDS..."
pyside6-deploy -c pysidedeploy.spec

Write-Host ""
Write-Host "Done - check the output above for exactly where the build landed."
