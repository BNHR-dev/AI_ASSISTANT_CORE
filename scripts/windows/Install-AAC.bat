@echo off
REM Install-AAC.bat — lanceur double-clic du bootstrap AAC.
REM
REM Usage :
REM   Install-AAC.bat                           installation complète
REM   Install-AAC.bat -CheckOnly                audit : vérifie sans installer
REM   Install-AAC.bat -SkipComfyUI              saute ComfyUI (phase la plus lourde)
REM   Install-AAC.bat -AACDataDir "E:\AAC_Data" dossier de données personnalisé

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" %*
