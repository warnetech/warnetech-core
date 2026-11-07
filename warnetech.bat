@echo off
REM warnetech.bat — Windows wrapper for warnetech-core CLI
REM Usage: warnetech ask "your prompt"
REM        warnetech run "your goal"
REM        warnetech status

setlocal enabledelayedexpansion

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0

REM Load .env if it exists
if exist "%SCRIPT_DIR%.env" (
    for /f "delims==" %%A in ('type "%SCRIPT_DIR%.env"') do (
        if not "%%A"=="" (
            set "%%A"
        )
    )
)

REM Forward all arguments to the Python CLI
python -m warnetech_core.cli %*

endlocal
