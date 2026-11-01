# Crawl ranked-doubles replays for each rank band, spread evenly over monthly windows so the
# corpus covers the whole date range used by the time split (not just the newest games).
# Needs BALLCHASING_API_KEY at user level; never prints it. Resumable: rerun to top up.
param(
    [int]$PerMonth = 200,
    [string]$Start = "2025-07-01",
    [int]$Months = 16,
    [int]$Workers = 4,
    [string]$Tier = "gc"
)
$env:BALLCHASING_API_KEY = [Environment]::GetEnvironmentVariable("BALLCHASING_API_KEY", "User")
if (-not $env:BALLCHASING_API_KEY) { throw "BALLCHASING_API_KEY is not set at user level" }
$env:PYTHONUTF8 = "1"
$bands = @(
    @{ name = "2v2_diamond";  min = "diamond-1";        max = "diamond-3" },
    @{ name = "2v2_champion"; min = "champion-1";       max = "champion-3" },
    @{ name = "2v2_gc";       min = "grand-champion-1"; max = "grand-champion-3" }
)
$first = [datetime]::ParseExact($Start, "yyyy-MM-dd", $null)
for ($i = 0; $i -lt $Months; $i++) {
    $after = $first.AddMonths($i).ToString("yyyy-MM-ddT00:00:00Z")
    $before = $first.AddMonths($i + 1).ToString("yyyy-MM-ddT00:00:00Z")
    foreach ($b in $bands) {
        Write-Host "== $($b.name) $after .. $before"
        & .\.venv\Scripts\python -B -m rlreplay crawl --out data/raw --name $b.name `
            --min-rank $b.min --max-rank $b.max --after $after --before $before `
            --sort-dir desc --tier $Tier --workers $Workers --max-replays $PerMonth
    }
}
