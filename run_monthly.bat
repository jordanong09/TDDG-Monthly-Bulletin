@echo off
REM ============================================================================
REM TDDG Monthly Bulletin - one-click monthly runner.
REM
REM Double-click this file to run the full pipeline up to (not including)
REM promotion. Promotion is the manual human-review step - see
REM MONTHLY_CHECKLIST.md.
REM
REM Pass-through args (rare):
REM    run_monthly.bat --skip-collect
REM    run_monthly.bat --month 2026-04
REM ============================================================================

setlocal
cd /d "%~dp0"

echo TDDG Monthly Bulletin pipeline
echo ===============================

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo ERROR: .venv not found at .venv\Scripts\activate.bat
    echo Run setup.ps1 first to create the virtual environment.
    echo.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

python "scripts\run_monthly.py" %*
set ERR=%errorlevel%

echo.
if %ERR% NEQ 0 (
    echo [Pipeline failed with code %ERR%.]
    echo Look in the logs\ folder for details on which stage failed.
) else (
    echo [Pipeline completed successfully.]
    echo Open the draft in a browser, then follow MONTHLY_CHECKLIST.md.
)

echo.
pause
exit /b %ERR%
