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
        exit 1
    }
}

Set-Location $SrcDir

# derive version from the nearest git tag - falls back to a dev
# placeholder if no tags exist yet
$RawTag = git -C $RootDir describe --tags --abbrev=0 2>$null
if (-not $RawTag) {
    $Version = "0.0.0-dev"
} else {
    $Version = $RawTag -replace '^v', ''
}
Write-Host "Building version: $Version"
"APP_VERSION = `"$Version`"" | Set-Content (Join-Path $SrcDir "ui\_version.py")

# Windows --product-version needs EXACTLY 4 numeric parts, no suffix
# allowed (eg "0.0.0-dev" is invalid) - strip any non-numeric suffix,
# then pad or trim to exactly 4 parts
$NumericVersion = $Version -replace '[^0-9.].*$', ''
$Parts = $NumericVersion.Split('.')
while ($Parts.Count -lt 4) {
    $Parts += "0"
}
$ProductVersion = ($Parts[0..3] -join '.')
Write-Host "Windows product version: $ProductVersion"

if (-not (Test-Path "pysidedeploy.spec")) {
    Write-Host "No spec file found - generating one with our known-good settings..."

    pyside6-deploy --init main.py

    (Get-Content pysidedeploy.spec) -replace '^title = .*', 'title = AKAISDS' | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^icon = .*', 'icon = ../assets/icon/icon_512x512.png' | Set-Content pysidedeploy.spec
    (Get-Content pysidedeploy.spec) -replace '^mode = .*', 'mode = onefile' | Set-Content pysidedeploy.spec

    Write-Host "Spec generated and configured."
}

# extra_args is regenerated on EVERY run (not just first-time setup),
# specifically so the version stays current even on an existing spec
(Get-Content pysidedeploy.spec) -replace '^extra_args = .*', "extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --windows-console-mode=disable --windows-product-name=AKAISDS --product-version=$ProductVersion --include-module=mido.backends.rtmidi --include-data-dir=ui/icons=ui/icons --include-data-files=ui/style.qss.template=ui/style.qss.template" | Set-Content pysidedeploy.spec

Write-Host "Building AKAISDS..."
pyside6-deploy -c pysidedeploy.spec

Write-Host ""
Write-Host "Done - check the output above for exactly where the build landed."
