@echo off
REM Install-AAC.bat — lanceur double-clic du bootstrap AAC (Windows natif).
REM Lance bootstrap.ps1 avec la bonne ExecutionPolicy. Le .ps1 s'auto-elève en admin.
REM
REM Usage : double-clic, ou en ligne de commande :
REM   Install-AAC.bat                                  installation complete (dialog de choix du dossier)
REM   Install-AAC.bat -DataRoot "E:\AI"                installation complete dans E:\AI\
REM   Install-AAC.bat -CheckOnly                       mode "doctor" : verifie sans rien installer
REM   Install-AAC.bat -SkipComfyUI                     saute ComfyUI (phase la plus lourde)
REM   Install-AAC.bat -DataRoot "E:\AI" -SkipComfyUI   installe dans E:\AI\ sans ComfyUI

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" %*
