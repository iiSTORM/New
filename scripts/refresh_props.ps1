<#
.SYNOPSIS
    Keeps props.json fresh on a timer, for a provider that permits it.

.NOTES
    THIS CANNOT WORK WITH THE PRIZEPICKS ADAPTER. The provider refuses this
    script from every network, home connections included -- measured on a
    Windows machine whose own browser fetched the payload fine minutes
    later. Every run will end in that 403. It is kept because nothing in it
    is provider-specific: point PROVIDERS at a source with a real
    server-side API and it starts working unchanged.

.DESCRIPTION
    The provider refuses datacenter IPs, which rules out CI and a Codespace,
    and it sends no CORS headers, so the page cannot fetch it either. What is
    left is a machine on an ordinary connection running this on a timer. See
    "Automatic refresh" in the README for the Task Scheduler registration.

    Safe to leave running every half hour:
      - stages props.json by path, so an editor left open in the same clone
        cannot have its files swept into an unattended commit;
      - refuses to run off the configured branch;
      - rebases onto the remote first, since the data scrape pushes to the
        same branch twice a day and would otherwise reject the push;
      - exits quietly when the lines have not moved;
      - a held lock makes the next tick skip rather than race;
      - a failed fetch leaves the existing props.json alone, because
        scrape_props.py refuses to write an empty file over good lines.

.EXAMPLE
    .\scripts\refresh_props.ps1 -DryRun
    Fetches and reports without committing or pushing. Run this first.

.EXAMPLE
    .\scripts\refresh_props.ps1
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$Branch = "main",
    [string]$Python = "python"
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$lock = Join-Path $repo ".props-refresh.lock"

function Write-Log($message) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Output "$stamp  $message"
}

# Fails when the directory exists, which is the whole point: it is the
# atomic test-and-set. A lock older than an hour is from a run that died,
# not one still going -- the fetch times out in 25 seconds.
try {
    New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null
} catch {
    $existing = Get-Item $lock -ErrorAction SilentlyContinue
    if ($existing -and $existing.CreationTimeUtc -lt (Get-Date).ToUniversalTime().AddHours(-1)) {
        Write-Log "clearing a stale lock from a run that did not finish"
        Remove-Item $lock -Recurse -Force -ErrorAction SilentlyContinue
        try { New-Item -ItemType Directory -Path $lock -ErrorAction Stop | Out-Null }
        catch { Write-Log "could not take the lock, giving up"; exit 0 }
    } else {
        Write-Log "another refresh is still running, skipping this tick"
        exit 0
    }
}

function Invoke-Refresh {
    $current = (& git rev-parse --abbrev-ref HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) { Write-Log "not a git repository: $repo"; return 1 }
    if ($current -ne $Branch) {
        Write-Log "on '$current', not '$Branch' -- refusing to push prop lines from the wrong branch"
        return 1
    }

    & git pull --rebase --autostash --quiet origin $Branch
    if ($LASTEXITCODE -ne 0) {
        Write-Log "could not rebase onto origin/$Branch -- leaving the tree alone"
        return 1
    }

    $fetchArgs = @("scripts/scrape_props.py", "--out", "props.json")
    if ($DryRun) { $fetchArgs += "--dry-run" }
    & $Python @fetchArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Log "fetch failed -- props.json left as it was"
        return 1
    }

    if ($DryRun) { Write-Log "-DryRun: nothing committed"; return 0 }

    # By path only: whatever else is in the tree is not this job's business.
    & git add props.json
    & git diff --quiet --cached -- props.json
    if ($LASTEXITCODE -eq 0) {
        Write-Log "lines unchanged since the last run"
        return 0
    }

    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    & git -c user.name="props-refresh" -c user.email="actions@users.noreply.github.com" `
        commit --quiet --only props.json -m "Update prop lines $stamp"
    if ($LASTEXITCODE -ne 0) { Write-Log "commit failed"; return 1 }

    foreach ($attempt in 1..3) {
        & git push --quiet origin $Branch
        if ($LASTEXITCODE -eq 0) { Write-Log "pushed fresh lines"; return 0 }
        Write-Log "push rejected ($attempt/3) -- rebasing onto the newest remote and retrying"
        & git pull --rebase --autostash --quiet origin $Branch
        Start-Sleep -Seconds ($attempt * 3)
    }

    Write-Log "could not push after three attempts; the commit is local and the next run will carry it"
    return 1
}

# The lock is released in finally, and the script exits AFTER it, rather
# than exiting from inside the try. Whether PowerShell runs a finally on
# exit is version-dependent trivia, and a lock left behind would make every
# later tick skip until the hour-old rule cleared it.
$code = 1
try { $code = Invoke-Refresh }
finally { Remove-Item $lock -Recurse -Force -ErrorAction SilentlyContinue }
exit $code
