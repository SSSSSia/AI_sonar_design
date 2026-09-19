@echo off
rem Sonar-CAD v2 full-stack start - PowerShell/cmd entry.
rem NOTE: typing "bash" in PowerShell hits WSL bash (broken on this machine),
rem       so we explicitly use Git Bash. Keep this file ASCII-only (cmd reads ANSI).
setlocal
set "GBASH=C:\Program Files\Git\bin\bash.exe"
if not exist "%GBASH%" (
  echo [start-all] Git Bash not found: %GBASH%
  echo [start-all] Run from a Git Bash window instead: bash scripts/start-all.sh
  exit /b 1
)
"%GBASH%" "%~dp0start-all.sh"
exit /b %errorlevel%
