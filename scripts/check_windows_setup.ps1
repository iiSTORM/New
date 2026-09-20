<#
.SYNOPSIS
    Reports whether this machine can run the scheduled prop refresh.

.DESCRIPTION
    Four things have to be true, and each fails differently when it is not.
    Run this once before scheduling anything -- an unattended job that
    prompts for a password does not fail loudly, it just silently never
    pushes again.
#>
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$problems = @()

function Test-Step($label, [scriptblock]$check) {
    try {
        $result = & $check
        if ($result) { Write-Host "  OK    $label -- $result" -ForegroundColor Green; return $true }
    } catch {}
    Write-Host "  FAIL  $label" -ForegroundColor Red
    return $false
}

Write-Host "`nChecking this machine can refresh prop lines:`n"

if (-not (Test-Step "git is installed" { (& git --version 2>$null) })) {
    $problems += "Install Git for Windows: https://git-scm.com/download/win"
}

$py = $null
foreach ($candidate in @("python", "py")) {
    $v = & $candidate --version 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
}
if ($py) { Write-Host "  OK    python is installed -- $(& $py --version 2>&1) (use -Python $py)" -ForegroundColor Green }
else {
    Write-Host "  FAIL  python is installed" -ForegroundColor Red
    $problems += "Install Python: https://www.python.org/downloads/windows/ (tick 'Add to PATH')"
}

if ($py) {
    & $py -c "import requests" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "  OK    the requests package is present" -ForegroundColor Green }
    else {
        Write-Host "  FAIL  the requests package is present" -ForegroundColor Red
        $problems += "Run: $py -m pip install requests   (the live fetch needs it; parsing a saved file does not)"
    }
}

# A push that prompts is the failure that hides: interactively it works, and
# under Task Scheduler there is no console to type into, so it hangs or dies.
$helper = & git config --get credential.helper 2>$null
$remote = & git remote get-url origin 2>$null
if ($remote -match "^git@" -or $remote -match "^ssh://") {
    Write-Host "  OK    pushes over SSH, which will not prompt if your key is loaded" -ForegroundColor Green
} elseif ($helper) {
    Write-Host "  OK    a credential helper is configured ($helper)" -ForegroundColor Green
} else {
    Write-Host "  FAIL  git push will not prompt for a password" -ForegroundColor Red
    $problems += "No credential helper and an HTTPS remote: Task Scheduler has no console to type a password into. Run 'git push' by hand once and let Windows store the credential, or switch origin to SSH."
}

Write-Host ""
if ($problems.Count -eq 0) {
    Write-Host "Ready. Next: .\scripts\refresh_props.ps1 -DryRun" -ForegroundColor Green
} else {
    Write-Host "Fix these first:" -ForegroundColor Yellow
    $problems | ForEach-Object { Write-Host "  - $_" }
}
