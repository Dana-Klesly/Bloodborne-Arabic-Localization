# Read-only environment check. Changes nothing on the system.
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File tools\check_environment.ps1
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$out  = Join-Path $root "logs\environment_report.txt"
$lines = New-Object System.Collections.Generic.List[string]
function Add($t) { $lines.Add($t); Write-Host $t }
function Ver($cmd, $arg) {
    $c = Get-Command $cmd
    if ($c) { $v = (& $cmd $arg 2>&1 | Select-Object -First 1); return "OK  $v  ($($c.Source))" }
    return "NOT FOUND"
}

Add "=== Bloodborne-Arabic-Localization environment report ==="
Add ("Date: " + (Get-Date -Format "yyyy-MM-dd HH:mm"))
$os = Get-CimInstance Win32_OperatingSystem
Add ("Windows: " + $os.Caption + "  build " + $os.BuildNumber)
Add ""
Add ("Python : " + (Ver "python" "--version"))
Add ("py     : " + (Ver "py" "--version"))
Add ("Git    : " + (Ver "git" "--version"))
Add ("VSCode : " + (Ver "code" "--version"))
Add ("7-Zip  : " + $(if (Test-Path "C:\Program Files\7-Zip\7z.exe") {"OK  C:\Program Files\7-Zip\7z.exe"} else {(Ver "7z" "")}))
Add ("Ollama : " + (Ver "ollama" "--version"))
Add ""
$smi = Get-Command nvidia-smi
if ($smi) {
    Add "GPU (nvidia-smi):"
    & nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader | ForEach-Object { Add ("  " + $_) }
} else {
    Add "GPU: nvidia-smi not found"
    Get-CimInstance Win32_VideoController | ForEach-Object { Add ("  " + $_.Name + "  driver " + $_.DriverVersion) }
}
Add ""
Add "Disk space:"
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Used -ne $null } | ForEach-Object {
    Add ("  {0}:  free {1:N1} GB of {2:N1} GB" -f $_.Name, ($_.Free/1GB), (($_.Used+$_.Free)/1GB))
}
$lines | Set-Content -Encoding UTF8 $out
Write-Host ""
Write-Host "Saved to $out"
