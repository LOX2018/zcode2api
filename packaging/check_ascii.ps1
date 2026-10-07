$ErrorActionPreference = 'Stop'
$raw = [System.IO.File]::ReadAllLines("$PSScriptRoot\build.ps1", [System.Text.Encoding]::UTF8)
$ps = Get-Content "$PSScriptRoot\build.ps1"
Write-Host ("raw lines : {0}" -f $raw.Count)
Write-Host ("ps51 lines: {0}" -f $ps.Count)
if ($raw.Count -ne $ps.Count) {
    Write-Host 'MISMATCH: PowerShell 5.1 is folding lines (non-ASCII comment swallowing a newline)' -ForegroundColor Red
    for ($i = 0; $i -lt [Math]::Min($raw.Count, $ps.Count); $i++) {
        if ($raw[$i] -ne $ps[$i]) {
            Write-Host ("first diff at ps line {0}: {1}" -f ($i + 1), $ps[$i])
            break
        }
    }
    exit 1
}
Write-Host 'OK: build.ps1 is read line-for-line as written (pure ASCII)'
