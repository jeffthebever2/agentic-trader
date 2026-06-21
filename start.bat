@echo off
REM ===========================================================================
REM  Agentic Trader - Windows launcher
REM
REM  Just double-click this file, or run it in Command Prompt / PowerShell:
REM      start.bat            set up everything (if needed) and start the app
REM      start.bat doctor     check what is wrong, change nothing
REM      start.bat stop       stop the dashboard
REM      start.bat help       show all commands
REM
REM  The first run creates a .venv folder and installs dependencies, so it
REM  takes a few minutes. After that it starts in seconds.
REM ===========================================================================

setlocal
cd /d "%~dp0"
title Agentic Trader

REM --- Find a Python to bootstrap with; the launcher script does the rest. ---
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py -3"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)

if not defined PYEXE (
    echo.
    echo ===================================================================
    echo  [FAIL] Python was not found on this PC.
    echo ===================================================================
    echo.
    echo  Install Python 3.10 or newer from:
    echo      https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: on the first install screen, tick the box
    echo      "Add python.exe to PATH"
    echo.
    echo  Then double-click start.bat again.
    echo.
    pause
    exit /b 1
)

%PYEXE% "scripts\windows_launcher.py" %*
set "RC=%ERRORLEVEL%"

REM Keep the window open on failure so the error stays readable.
if not "%RC%"=="0" (
    echo.
    echo [FAIL] Something went wrong ^(exit code %RC%^). Read the messages above.
    pause
)

exit /b %RC%
