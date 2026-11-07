@echo off
rem Convenience wrapper: runs the experimental backup CLI with the project interpreter.
rem Usage: backup.cmd store-passphrase | show-recovery-code | run --vault DIR --local DIR [--remote]
setlocal
set "ROOT=%~dp0..\.."
set "PY=%ROOT%\.jarvis-python-3.14.10\python.exe"
set "CLI=%ROOT%\backend\experimental\backup_cli.py"
if not exist "%PY%" (
  echo Python not found: %PY%
  exit /b 1
)
if not exist "%CLI%" (
  echo CLI not found: %CLI%
  exit /b 1
)
"%PY%" "%CLI%" %*
exit /b %ERRORLEVEL%
