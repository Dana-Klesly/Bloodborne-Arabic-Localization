# Runs all project tests with the project venv and saves the output to logs\tests_log.txt
# The tests run through tools\run_tests_isolated.py, which also checks that the real logs\,
# translation\batches\, backups\ and database\ folders were not changed and that temp files were cleaned up.
# Works in Windows PowerShell 5.1 and PowerShell 7.
#
# Why the test run is wrapped below: Python's unittest writes its results to stderr.
# Windows PowerShell 5.1 turns every stderr line of a native program redirected with 2>&1
# into an error record; with ErrorActionPreference = "Stop" the first line aborted the
# script with NativeCommandError, even when all tests passed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "venv not found. Run tools\setup_venv.ps1 first." -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\tests_log.txt"

$env:PYTHONIOENCODING = "utf-8"
$prevConsoleEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8   # decode Python's UTF-8 output correctly
$lines = New-Object System.Collections.Generic.List[string]
$code = 1
$ErrorActionPreference = "Continue"                          # stderr lines must not abort the run
try {
    & $py (Join-Path $root "tools\run_tests_isolated.py") 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            # stderr line: take its text; PS 5.1 reports an empty line as "...RemoteException"
            $line = $_.Exception.Message
            if ($line -eq "System.Management.Automation.RemoteException") { $line = "" }
        }
        else { $line = "$_" }
        Write-Host $line
        $lines.Add($line)
    }
    $code = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = "Stop"
    [Console]::OutputEncoding = $prevConsoleEncoding
}
$lines | Out-File -FilePath $log -Encoding utf8

$summary = $lines | Where-Object { $_ -match '^Ran \d+ tests? in' } | Select-Object -Last 1
if ($code -eq 0) { Write-Host "`nALL TESTS PASSED ($summary) - log: logs\tests_log.txt" -ForegroundColor Green }
elseif ($code -eq 3) { Write-Host "`nREAL PROJECT FILES CHANGED DURING THE TESTS (exit code 3) - see logs\tests_log.txt" -ForegroundColor Red }
elseif ($code -eq 4) { Write-Host "`nTESTS LEFT TEMPORARY FILES BEHIND (exit code 4) - see logs\tests_log.txt" -ForegroundColor Red }
else { Write-Host "`nSOME TESTS FAILED (exit code $code) - see logs\tests_log.txt" -ForegroundColor Red }
exit $code
