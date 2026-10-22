param(
    [string]$Url = "https://www.amazon.com"
)
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$userData = "$env:LOCALAPPDATA\ChadBrowser\UserData"

Write-Host "Opening ChadBrowser with profile: $userData" -ForegroundColor Cyan
Write-Host "URL: $Url" -ForegroundColor Cyan

Start-Process $chrome -ArgumentList "--user-data-dir=`"$userData`"", "--new-window", "`"$Url`""
