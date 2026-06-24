@echo off
setlocal enableextensions
REM run.bat -- AAC in one command (Windows). Delegates to run.ps1 (real logic).
REM Double-clickable AND usable from cmd.exe. Prereq: Docker Desktop + WSL2.
REM
REM   run.bat                 start, open the Console
REM   run.bat --down          stop the stack
REM   run.bat --logs          follow the logs
REM   run.bat --no-models     DEGRADED: skip model (down)load
REM
REM On failure the window is KEPT OPEN when double-clicked, and the run.ps1 journal
REM under logs\ is always written. Set AAC_NO_PAUSE=1 to never pause (automation).

REM Detect double-click: Explorer launches "cmd /c ...run.bat" -> %cmdcmdline% has /c.
set "AAC_DOUBLECLICK=0"
echo %cmdcmdline% | find /i "/c" >nul 2>&1 && set "AAC_DOUBLECLICK=1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "AAC_RC=%ERRORLEVEL%"

if not "%AAC_RC%"=="0" (
  echo.
  echo [run.bat] echec ^(code %AAC_RC%^). Journal : "%~dp0logs"
  if "%AAC_DOUBLECLICK%"=="1" if not "%AAC_NO_PAUSE%"=="1" (
    echo.
    pause
  )
)

exit /b %AAC_RC%
