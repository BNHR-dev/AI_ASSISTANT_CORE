@echo off
REM run.bat — AAC en une commande (Windows). Delegue a run.ps1 (logique reelle).
REM Double-cliquable ET utilisable depuis cmd.exe. Prerequis : Docker Desktop + WSL2.
REM
REM   run.bat            demarre, ouvre la Console
REM   run.bat --down     arrete la stack
REM   run.bat --logs     suit les logs
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
