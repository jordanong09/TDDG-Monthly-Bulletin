# ----------------------------------------------------------------------------
# TDDG Monthly Bulletin - Stage 0 bootstrap finisher (Windows / PowerShell)
# ----------------------------------------------------------------------------
# What this script does:
#   1. Removes any partial .venv / .git left over from sandbox attempts
#   2. Confirms Python 3.11+ is available
#   3. Creates a fresh .venv (Windows-native)
#   4. Installs dependencies from requirements.txt
#   5. Initialises git and makes the first commit
#
# How to run (one time):
#   1. Open PowerShell
#   2. cd "C:\Users\jorda\OneDrive\Documents\TDDG Monthly Bulletin"
#   3. If script execution is blocked, run once:
#        Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#   4. .\setup.ps1
# ----------------------------------------------------------------------------

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\Users\jorda\OneDrive\Documents\TDDG Monthly Bulletin"

Write-Host "==> TDDG Bulletin Agent - bootstrap" -ForegroundColor Cyan
Set-Location $ProjectRoot

# 1. Clean up sandbox leftovers
if (Test-Path ".venv") {
    Write-Host "Removing leftover .venv ..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force ".venv"
}
if (Test-Path ".git") {
    Write-Host "Removing leftover .git ..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force ".git"
}

# 2. Check Python version
Write-Host "==> Checking Python" -ForegroundColor Cyan
$pythonCmd = $null
foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "python")) {
    try {
        $verOutput = & cmd /c "$candidate --version 2>&1"
        if ($verOutput -match "Python 3\.(1[1-9]|[2-9]\d)") {
            $pythonCmd = $candidate
            Write-Host "  Using: $candidate ($verOutput)" -ForegroundColor Green
            break
        }
    } catch { }
}
if (-not $pythonCmd) {
    Write-Host "ERROR: Python 3.11+ not found. Install from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

# 3. Create venv
Write-Host "==> Creating virtual environment (.venv)" -ForegroundColor Cyan
& cmd /c "$pythonCmd -m venv .venv"

# 4. Install dependencies
Write-Host "==> Installing dependencies" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

# 5. Initialise git
Write-Host "==> Initialising git" -ForegroundColor Cyan
git init -b main
git config user.email "jordanong.09@gmail.com"
git config user.name "Jordan"
git add .gitignore README.md requirements.txt setup.ps1
git add config sources data bulletins scripts prompts website logs
git commit -m "chore: bootstrap project skeleton"

# 6. Verify
Write-Host ""
Write-Host "==> Verification" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" --version
Write-Host ""
Write-Host "Installed packages:" -ForegroundColor Cyan
& ".\.venv\Scripts\python.exe" -m pip list
Write-Host ""
Write-Host "Git log:" -ForegroundColor Cyan
git log --oneline
Write-Host ""
Write-Host "Bootstrap complete." -ForegroundColor Green
Write-Host "To activate the venv in future sessions, run: .\.venv\Scripts\Activate.ps1"
