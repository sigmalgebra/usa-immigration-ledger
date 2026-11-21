# Fetches new State Dept files and pushes them to the data/state branch, where the "Refresh data" workflow builds,
# tests and merges them. It runs on a maintainer's machine because State's file store refuses GitHub's runners.
# It works in its own clone, reset to origin/main on every run, so it never touches your checkout and always runs
# the current code. Register it with scripts/install-fetch-task.ps1.
$ErrorActionPreference = "Continue"
$repo = "https://github.com/sigmalgebra/usa-immigration-ledger.git"
$clone = Join-Path $env:LOCALAPPDATA "usa-immigration-ledger-fetch"
Start-Transcript -Path "$clone.log" -Append | Out-Null

function Run([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed with exit code $LASTEXITCODE" }
}

try {
    if (-not (Test-Path $clone)) { git clone --quiet $repo $clone; Run "git clone" }
    Set-Location $clone
    git fetch --quiet origin; Run "git fetch"
    git checkout --quiet -B main origin/main; Run "git checkout"
    git reset --quiet --hard origin/main; Run "git reset"
    git clean -fdq; Run "git clean"
    if (-not (Test-Path .venv)) { python -m venv .venv; Run "python -m venv" }
    .\.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r pipeline/requirements.txt
    Run "pip install"
    $env:PYTHONPATH = "pipeline"
    .\.venv\Scripts\python.exe -m usdata fetch-state; Run "usdata fetch-state"

    if (-not (git status --porcelain archive)) { Write-Output "nothing new"; return }
    git checkout --quiet -B data/state; Run "git checkout data/state"
    git add archive; Run "git add"
    git commit --quiet -m "State Dept files $(Get-Date -Format yyyy-MM-dd)"; Run "git commit"
    git ls-remote --exit-code --heads origin data/state | Out-Null
    if ($LASTEXITCODE -eq 0) {
        git fetch --quiet origin data/state; Run "git fetch data/state"
        git diff --quiet FETCH_HEAD HEAD -- archive
        if ($LASTEXITCODE -eq 0) { Write-Output "already waiting on data/state"; return }
    }
    git push --quiet --force origin data/state; Run "git push"
    Write-Output "pushed data/state"
}
catch {
    Write-Output "FAILED: $_"
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
