#requires -Version 5.1
# Run-InstallerTests.ps1 -- self-contained tests for the Windows installer (run.ps1 /
# run.bat / Fetch-ComfyUIModels.ps1). No Pester dependency: a tiny assert harness that
# exits non-zero if any test fails, so it works on Windows PowerShell 5.1 and PS7 / CI.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File Run-InstallerTests.ps1
#
# Docker and real downloads are MOCKED (fake docker on PATH, tiny local manifest) so the
# suite is fast and hermetic.
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$RunPs1   = Join-Path $RepoRoot "run.ps1"
$FetchPs1 = Join-Path $RepoRoot "scripts\windows\Fetch-ComfyUIModels.ps1"
$Manifest = Join-Path $RepoRoot "scripts\models.manifest"

$script:Pass = 0; $script:Fail = 0
function Test-Case([string]$Name, [scriptblock]$Body) {
  try { & $Body; $script:Pass++; Write-Host "[PASS] $Name" -ForegroundColor Green }
  catch { $script:Fail++; Write-Host "[FAIL] $Name" -ForegroundColor Red; Write-Host "       $($_.Exception.Message)" -ForegroundColor DarkRed }
}
function Assert-True($cond, $msg) { if (-not $cond) { throw "assertion failed: $msg" } }
function Assert-Eq($actual, $expected, $msg) { if ($actual -ne $expected) { throw "$msg (got '$actual', expected '$expected')" } }
function Assert-Match($text, $pattern, $msg) { if ($text -notmatch $pattern) { throw "$msg (pattern '$pattern' not in output)" } }
function Assert-NoMatch($text, $pattern, $msg) { if ($text -match $pattern) { throw "$msg (forbidden pattern '$pattern' present)" } }

function Get-ParseErrors([string]$Path) {
  $t = $null; $e = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$t, [ref]$e)
  return $e
}
function Get-NonAsciiCount([string]$Path) {
  $bytes = [System.IO.File]::ReadAllBytes($Path)
  return (($bytes | Where-Object { $_ -gt 127 }) | Measure-Object).Count
}
function Invoke-Ps1Child {
  param([string]$Path, [string[]]$PsArgs = @(), [string]$Cwd = $null)
  $prev = Get-Location
  if ($Cwd) { Set-Location -LiteralPath $Cwd }
  try {
    $output = (& powershell -NoProfile -ExecutionPolicy Bypass -File $Path @PsArgs 2>&1 | Out-String)
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = $output }
  } finally { Set-Location -LiteralPath $prev }
}
function New-Temp([string]$suffix) {
  $p = Join-Path ([System.IO.Path]::GetTempPath()) ("aac-test-" + [System.Guid]::NewGuid().ToString("N").Substring(0,8) + $suffix)
  return $p
}
# PATH with every Docker entry stripped (keeps powershell.exe and the rest intact) ->
# `Get-Command docker` then returns null without breaking the child PowerShell launch.
function Get-DockerlessPath {
  return ((($env:PATH -split ';') | Where-Object { $_ -and ($_ -notmatch '[Dd]ocker') }) -join ';')
}

Write-Host "== AAC installer tests ==" -ForegroundColor Cyan

# 1) Full parse of run.ps1 (the headline acceptance criterion) ----------------------------
Test-Case "run.ps1 parses with zero errors (PS 5.1 tokenizer)" {
  $e = Get-ParseErrors $RunPs1
  Assert-Eq $e.Count 0 "run.ps1 must parse cleanly"
}
Test-Case "Fetch-ComfyUIModels.ps1 parses with zero errors" {
  $e = Get-ParseErrors $FetchPs1
  Assert-Eq $e.Count 0 "Fetch-ComfyUIModels.ps1 must parse cleanly"
}

# 2) ASCII-only (encoding-independent parsing) --------------------------------------------
Test-Case "run.ps1 is ASCII-only" { Assert-Eq (Get-NonAsciiCount $RunPs1) 0 "run.ps1 must be pure ASCII" }
Test-Case "Fetch-ComfyUIModels.ps1 is ASCII-only" { Assert-Eq (Get-NonAsciiCount $FetchPs1) 0 "fetch script must be pure ASCII" }

# 3) Paths containing spaces --------------------------------------------------------------
Test-Case "run.ps1 --help works from a directory whose path contains spaces" {
  $dir = New-Temp " with spaces"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  $copy = Join-Path $dir "run.ps1"
  Copy-Item $RunPs1 $copy
  $r = Invoke-Ps1Child -Path $copy -PsArgs @("--help") -Cwd $dir
  Assert-Eq $r.Code 0 "help must exit 0 from a spaced path"
  Assert-Match $r.Output "run.bat" "help text must be printed"
  Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
}

# 4) Resolution from a different working directory (absolute $PSScriptRoot) ----------------
Test-Case "run.ps1 --help resolves correctly when invoked from another cwd" {
  $other = New-Temp "-cwd"
  New-Item -ItemType Directory -Force -Path $other | Out-Null
  $r = Invoke-Ps1Child -Path $RunPs1 -PsArgs @("--help") -Cwd $other
  Assert-Eq $r.Code 0 "help must exit 0 from an unrelated cwd"
  Remove-Item -Recurse -Force $other -ErrorAction SilentlyContinue
}

# 5) Exit-code propagation: unknown option -> 2 -------------------------------------------
Test-Case "unknown option exits with code 2" {
  $r = Invoke-Ps1Child -Path $RunPs1 -PsArgs @("--bogus")
  Assert-Eq $r.Code 2 "unknown option must exit 2"
  Assert-Match $r.Output "option inconnue" "must explain the bad option"
}

# 6) Docker CLI absent -> clean exit 1 ----------------------------------------------------
Test-Case "Docker CLI absent -> clean exit 1 with a precise message" {
  $savedPath = $env:PATH
  try {
    # Strip docker from PATH (keeps powershell.exe) -> Get-Command docker returns null.
    $env:PATH = Get-DockerlessPath
    $r = Invoke-Ps1Child -Path $RunPs1 -PsArgs @("--no-open")
    Assert-Eq $r.Code 1 "missing docker CLI must exit 1"
    Assert-Match $r.Output "Docker CLI absente" "must report the missing CLI"
  } finally { $env:PATH = $savedPath }
}

# 7) Docker installed but daemon stopped -> clean exit 1, no NativeCommandError ------------
Test-Case "Docker daemon stopped -> exit 1, no NativeCommandError leak" {
  $fakeDir = New-Temp "-fakedocker"
  New-Item -ItemType Directory -Force -Path $fakeDir | Out-Null
  $fake = Join-Path $fakeDir "docker.bat"
  @(
    '@echo off',
    'if "%~1"=="compose" if "%~2"=="version" exit /b 0',
    'if "%~1"=="info" (',
    '  echo error during connect: open //./pipe/docker_engine: The system cannot find the file specified. 1>&2',
    '  exit /b 1',
    ')',
    'echo fake-docker %*',
    'exit /b 0'
  ) | Set-Content -LiteralPath $fake -Encoding Ascii
  $savedPath = $env:PATH
  try {
    # Fake docker FIRST, real docker dir excluded; --no-docker-start avoids launching Desktop.
    $env:PATH = "$fakeDir;" + (Get-DockerlessPath)
    $r = Invoke-Ps1Child -Path $RunPs1 -PsArgs @("--no-docker-start","--no-open")
    Assert-Eq $r.Code 1 "stopped daemon must exit 1"
    Assert-Match $r.Output "daemon Docker ne repond pas" "must report the stopped daemon"
    Assert-NoMatch $r.Output "NativeCommandError" "native stderr must not leak as NativeCommandError"
  } finally {
    $env:PATH = $savedPath
    Remove-Item -Recurse -Force $fakeDir -ErrorAction SilentlyContinue
  }
}

# 8) Ollama model names come from the manifest (dot-source, no execution) ------------------
Test-Case "Get-OllamaModelNames reads the manifest source of truth" {
  $env:AAC_RUN_PS1_NOEXEC = "1"
  $RepoRoot2 = $RepoRoot
  $RepoRoot = $RepoRoot2
  . $RunPs1
  $env:AAC_RUN_PS1_NOEXEC = $null
  $names = Get-OllamaModelNames
  Assert-True ($names -contains "qwen3:8b") "qwen3:8b expected"
  Assert-True ($names -contains "qwen2.5-coder:7b") "coder expected"
  Assert-True ($names -contains "qwen2.5vl:3b") "vlm expected"
  Assert-Eq $names.Count 3 "exactly 3 ollama models"
}

# 8b) Invoke-Child returns ONLY the integer exit code (not the child's stdout) -----------
#     Regression: a succeeding child that prints output must not corrupt the exit code.
Test-Case "Invoke-Child returns the integer exit code, never the child stdout" {
  $env:AAC_RUN_PS1_NOEXEC = "1"
  $RepoRoot3 = $RepoRoot; $RepoRoot = $RepoRoot3
  . $RunPs1
  $env:AAC_RUN_PS1_NOEXEC = $null
  $child = New-Temp "-child.ps1"
  @('Write-Host "noise line one"','Write-Host "noise line two"','exit 0') | Set-Content -LiteralPath $child -Encoding Ascii
  $code = Invoke-Child $child @()
  Assert-True ($code -is [int]) "Invoke-Child must return an [int]"
  Assert-Eq $code 0 "succeeding child must yield code 0"
  @('Write-Host "boom"','exit 7') | Set-Content -LiteralPath $child -Encoding Ascii
  $code2 = Invoke-Child $child @()
  Assert-Eq $code2 7 "failing child must propagate its exit code"
  Remove-Item $child -Force -ErrorAction SilentlyContinue
}

# 9) Fetch idempotence: a present file of the right size is skipped (no download) ----------
Test-Case "Fetch-ComfyUIModels is idempotent (skips present file, exits 0, twice)" {
  $work = New-Temp "-fetch-idem"
  New-Item -ItemType Directory -Force -Path (Join-Path $work "checkpoints") | Out-Null
  $dest = Join-Path $work "checkpoints\tiny.bin"
  [System.IO.File]::WriteAllBytes($dest, (New-Object byte[] 11))   # exactly 11 bytes
  $tm = Join-Path $work "test.manifest"
  "comfyui|checkpoints|tiny.bin|11|http://invalid.invalid/never" | Set-Content -LiteralPath $tm -Encoding Ascii
  $r1 = Invoke-Ps1Child -Path $FetchPs1 -PsArgs @("-ModelsDir",$work,"-ManifestPath",$tm)
  Assert-Eq $r1.Code 0 "first run must exit 0"
  Assert-Match $r1.Output "deja present" "must skip the present file"
  $r2 = Invoke-Ps1Child -Path $FetchPs1 -PsArgs @("-ModelsDir",$work,"-ManifestPath",$tm)
  Assert-Eq $r2.Code 0 "second run must also exit 0 (idempotent)"
  Assert-Match $r2.Output "deja present" "second run must still skip"
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

# 10) Fetch fail-fast: a mandatory download that fails -> exit 1 ---------------------------
Test-Case "Fetch-ComfyUIModels fails fast (exit 1) when a download fails" {
  $work = New-Temp "-fetch-fail"
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $tm = Join-Path $work "test.manifest"
  "comfyui|checkpoints|missing.bin|999|http://invalid.invalid/nope" | Set-Content -LiteralPath $tm -Encoding Ascii
  $r = Invoke-Ps1Child -Path $FetchPs1 -PsArgs @("-ModelsDir",$work,"-ManifestPath",$tm)
  Assert-Eq $r.Code 1 "failed mandatory download must exit 1"
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

# 11) -List does not download and reports presence ----------------------------------------
Test-Case "Fetch-ComfyUIModels -List reports declared models without downloading" {
  $work = New-Temp "-fetch-list"
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $r = Invoke-Ps1Child -Path $FetchPs1 -PsArgs @("-ModelsDir",$work,"-List")
  Assert-Eq $r.Code 0 "-List must exit 0"
  Assert-Match $r.Output "RealVisXL_V5.0_fp16.safetensors" "must list the checkpoint"
  Assert-Match $r.Output "4x-UltraSharp.pth" "must list the upscaler"
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("== Resultat : {0} pass, {1} fail ==" -f $script:Pass, $script:Fail) -ForegroundColor Cyan
if ($script:Fail -gt 0) { exit 1 }
exit 0
