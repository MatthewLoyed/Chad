# Verify health of Chad's infrastructure dependencies (issue #363).
# Mirrors the checks behind GET /api/services/health (src/remote/endpoints.py) so the
# CLI and the future Settings -> Services GUI panel never drift.
#
# Usage:
#   .\scripts\setup\verify_chad_services.ps1
#   .\scripts\setup\verify_chad_services.ps1 -BrainUrl http://localhost:8788   # dev Brain

param(
    [string]$BrainUrl = "http://localhost:8767",
    [string]$LmStudioUrl = "http://localhost:1234/v1",
    [string]$SearxngUrl = "http://127.0.0.1:8080",
    [string]$KrokiUrl = "https://kroki.io",
    [string]$PresentonUrl = "http://127.0.0.1:5000"
)

function Test-Url([string]$Url, [int]$TimeoutSec = 3) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return $resp.StatusCode -lt 500
    } catch {
        # Some services (e.g. LM Studio /v1/models with no key) still answer with a
        # non-200 that reaches us — treat any HTTP response as "up", only network
        # failures (no listener, DNS, timeout) as down.
        if ($_.Exception.Response) { return $true }
        return $false
    }
}

$results = @()

$results += [pscustomobject]@{ Service = "Brain";     Tier = "Core";     Healthy = (Test-Url "$BrainUrl/health") }
$results += [pscustomobject]@{ Service = "LM Studio"; Tier = "Core";     Healthy = (Test-Url "$LmStudioUrl/models") }
$results += [pscustomobject]@{ Service = "SearXNG";   Tier = "Enhanced"; Healthy = (Test-Url $SearxngUrl) }
$results += [pscustomobject]@{ Service = "Kroki";     Tier = "Enhanced"; Healthy = (Test-Url $KrokiUrl) }
$results += [pscustomobject]@{ Service = "Presenton"; Tier = "Optional"; Healthy = (Test-Url $PresentonUrl) }

$typstBin = (Get-Command typst -ErrorAction SilentlyContinue)
$results += [pscustomobject]@{ Service = "Typst CLI"; Tier = "Enhanced"; Healthy = [bool]$typstBin }

$npxBin = (Get-Command npx -ErrorAction SilentlyContinue)
$results += [pscustomobject]@{ Service = "Marp CLI";  Tier = "Enhanced"; Healthy = [bool]$npxBin }

Write-Host ""
foreach ($r in $results) {
    $status = if ($r.Healthy) { "OK" } else { "DOWN" }
    $color = if ($r.Healthy) { "Green" } else { "Yellow" }
    Write-Host ("[{0,-4}] {1,-10} ({2})" -f $status, $r.Service, $r.Tier) -ForegroundColor $color
}
Write-Host ""

$coreDown = $results | Where-Object { $_.Tier -eq "Core" -and -not $_.Healthy }
if ($coreDown) {
    Write-Warning "Core service(s) down: $($coreDown.Service -join ', '). Chad will be severely degraded."
    exit 1
}

$enhancedDown = $results | Where-Object { $_.Tier -eq "Enhanced" -and -not $_.Healthy }
if ($enhancedDown) {
    Write-Host "Enhanced service(s) down (fallbacks apply): $($enhancedDown.Service -join ', ')" -ForegroundColor Yellow
}

exit 0
