# Creates the project's own Python environment (.venv) and installs requirements.txt into it only.
# Every step and its result is written to logs\setup_venv_log.txt
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File tools\setup_venv.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$log = Join-Path $root "logs\setup_venv_log.txt"
"=== setup_venv $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Set-Content -Encoding UTF8 $log
function Log($t) { Write-Host $t; $t | Add-Content -Encoding UTF8 $log }
function Run($label, $exe, [string[]]$argList) {
    Log ""
    Log "--- $label"
    Log ("> " + $exe + " " + ($argList -join " "))
    $output = & $exe @argList 2>&1 | ForEach-Object { "$_" }
    $code = $LASTEXITCODE
    $output | ForEach-Object { Log "  $_" }
    Log ("  exit code: $code  -> " + $(if ($code -eq 0) {"PASS"} else {"FAIL"}))
    return ($code -eq 0)
}

Log "Project: $root"

# 1) Find Python 3.11 (prefer the py launcher, fall back to python on PATH)
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $p = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null)
    if ($LASTEXITCODE -eq 0 -and $p) { $py = $p.Trim() }
}
if (-not $py -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $py = (Get-Command python).Source
}
if (-not $py) { Log "FAIL: Python not found"; exit 1 }
Log "Python executable: $py"

# 2) Test Python, venv and ensurepip modules
if (-not (Run "Test: Python version" $py @("--version"))) { exit 1 }
if (-not (Run "Test: venv + ensurepip modules" $py @("-c", "import venv, ensurepip; print('venv and ensurepip available')"))) {
    Log "venv/ensurepip missing: re-run the Python installer -> Modify -> tick 'pip'. (Not done automatically.)"
    exit 1
}

# 3) Create .venv (only if it doesn't exist; never deletes anything)
$venvPy = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Log ""
    Log "--- .venv already exists, reusing it"
} else {
    if (Test-Path ".venv") { Log "FAIL: a broken .venv folder exists. Rename it manually (e.g. .venv_old) and run again."; exit 1 }
    if (-not (Run "Create .venv" $py @("-m", "venv", ".venv"))) { exit 1 }
}
if (-not (Test-Path $venvPy)) { Log "FAIL: $venvPy not found after creation"; exit 1 }
Run "Test: venv Python" $venvPy @("-c", "import sys; print(sys.prefix)") | Out-Null

# 4) Install requirements into the venv only
if (Test-Path "requirements.txt") {
    Run "Upgrade pip (inside .venv)" $venvPy @("-m", "pip", "install", "--upgrade", "pip") | Out-Null
    if (-not (Run "Install requirements.txt" $venvPy @("-m", "pip", "install", "-r", "requirements.txt"))) { exit 1 }
} else {
    Log "requirements.txt not found, skipping install"
}

# 5) Import test
Run "Test: imports" $venvPy @("-c", "import arabic_reshaper, bidi, fontTools, PIL; from bidi.algorithm import get_display; print('imports OK'); t='\u0635\u064a\u0627\u062f'; r=arabic_reshaper.reshape(t); print('reshape OK' if r!=t and len(r)==len(t) else 'reshape FAIL')") | Out-Null

# 6) Record installed versions
& $venvPy -m pip freeze 2>&1 | Set-Content -Encoding UTF8 (Join-Path $root "logs\installed_packages.txt")
Log ""
Log "Done. Activate with:  .\.venv\Scripts\Activate.ps1"
