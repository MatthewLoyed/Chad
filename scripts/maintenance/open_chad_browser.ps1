$userDataDir = Join-Path $env:LOCALAPPDATA "ChadBrowser\UserData"
$chromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chromePath)) {
    $chromePath = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
}

if (-not (Test-Path $chromePath)) {
    Write-Error "Chrome executable not found."
    exit 1
}

Write-Host "Launching Chrome with ChadBrowser profile: $userDataDir"
Start-Process -FilePath $chromePath -ArgumentList "--user-data-dir=`"$userDataDir`"", "https://www.amazon.com"
