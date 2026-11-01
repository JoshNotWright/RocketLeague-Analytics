# Second crawl stage: for each band, pull up to -PerPlayer games for the -Players players seen
# most in the window crawl's corpus, into data/raw/2v2_<band>_players. Waits for any running
# `rlreplay crawl` process to finish first so the two stages never share the API quota.
param(
    [int]$Players = 120,
    [int]$PerPlayer = 40,
    [int]$Workers = 4,
    [string]$Tier = "gc",
    [string]$After = "2025-07-01T00:00:00Z"
)
$env:BALLCHASING_API_KEY = [Environment]::GetEnvironmentVariable("BALLCHASING_API_KEY", "User")
if (-not $env:BALLCHASING_API_KEY) { throw "BALLCHASING_API_KEY is not set at user level" }
$env:PYTHONUTF8 = "1"

function Test-CrawlRunning {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like "*rlreplay crawl *" }
    return ($procs | Measure-Object).Count -gt 0
}
while (Test-CrawlRunning) {
    Write-Host "$(Get-Date -Format HH:mm) window crawl still running; waiting"
    Start-Sleep -Seconds 120
}

$bands = @(
    @{ name = "diamond";  min = "diamond-1";        max = "diamond-3";        lo = 52; hi = 63 },
    @{ name = "champion"; min = "champion-1";       max = "champion-3";       lo = 64; hi = 75 },
    @{ name = "gc";       min = "grand-champion-1"; max = "grand-champion-3"; lo = 76; hi = 88 }
)
foreach ($b in $bands) {
    Write-Host "== players stage: $($b.name)"
    & .\.venv\Scripts\python -B -m rlreplay crawl-players --band-dir "data/raw/2v2_$($b.name)" `
        --out "data/raw/2v2_$($b.name)_players" --min-rank $b.min --max-rank $b.max `
        --min-points $b.lo --max-points $b.hi --after $After `
        --players $Players --per-player $PerPlayer --tier $Tier --workers $Workers
}
