#requires -Version 5.1
# Fetch-ComfyUIModels.ps1 -- download the ComfyUI image models for the AAC demo.
#
# Windows-native: NO bash dependency (the old run.ps1 passed a Windows path to bash, which
# mangled the backslashes). Model names/URLs/sizes come from scripts/models.manifest, the
# single source of truth shared with the Linux scripts. Idempotent: a file already present
# with the expected size is skipped. ASCII-only output (PS 5.1 / PS7 safe).
[CmdletBinding()]
param(
  [string]$ModelsDir = "",
  [string]$ManifestPath = "",
  [switch]$Force,
  [switch]$List
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $ModelsDir) {
  if ($env:COMFYUI_MODELS_DIR) { $ModelsDir = $env:COMFYUI_MODELS_DIR }
  else { $ModelsDir = (Join-Path $RepoRoot "docker\models") }
}
if (-not $ManifestPath) { $ManifestPath = (Join-Path $RepoRoot "scripts\models.manifest") }
if (-not (Test-Path $ManifestPath)) {
  Write-Host "ERREUR: manifest introuvable : $ManifestPath" -ForegroundColor Red
  exit 1
}

function Get-ComfyRows([string]$Manifest) {
  $rows = @()
  foreach ($line in (Get-Content -LiteralPath $Manifest)) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith("#")) { continue }
    $f = $t.Split("|")
    if ($f.Count -ge 5 -and $f[0].Trim() -eq "comfyui") {
      $rows += [pscustomobject]@{
        Sub  = $f[1].Trim()
        Name = $f[2].Trim()
        Size = [int64]$f[3].Trim()
        Url  = $f[4].Trim()
      }
    }
  }
  return ,$rows
}

$rows = Get-ComfyRows $ManifestPath
if (-not $rows -or $rows.Count -eq 0) {
  Write-Host "ERREUR: aucune ligne 'comfyui' dans le manifest : $ManifestPath" -ForegroundColor Red
  exit 1
}

if ($List) {
  Write-Host "== Modeles image declares ($ManifestPath) ==" -ForegroundColor Cyan
  foreach ($r in $rows) {
    $dest = Join-Path (Join-Path $ModelsDir $r.Sub) $r.Name
    $present = (Test-Path $dest) -and ((Get-Item $dest).Length -eq $r.Size)
    $tag = if ($present) { "present" } else { "absent" }
    Write-Host ("   {0}/{1}  size={2}  [{3}]" -f $r.Sub, $r.Name, $r.Size, $tag)
  }
  exit 0
}

Write-Host "== Telechargement des modeles image -> $ModelsDir ==" -ForegroundColor Cyan
$rc = 0
foreach ($r in $rows) {
  $destDir = Join-Path $ModelsDir $r.Sub
  $dest    = Join-Path $destDir $r.Name
  New-Item -ItemType Directory -Force -Path $destDir | Out-Null

  if ((-not $Force) -and (Test-Path $dest) -and ((Get-Item $dest).Length -eq $r.Size)) {
    Write-Host "[OK] deja present : $($r.Sub)/$($r.Name)" -ForegroundColor Green
    continue
  }

  $mb  = [math]::Round($r.Size / 1MB)
  Write-Host "[DL] $($r.Sub)/$($r.Name)  (~${mb} Mo)" -ForegroundColor Cyan
  $tmp = "$dest.part"
  $old = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
  try {
    Invoke-WebRequest -Uri $r.Url -OutFile $tmp -UseBasicParsing
  } catch {
    $ProgressPreference = $old
    Write-Host "[!] ECHEC $($r.Name) : $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    $rc = 1; continue
  }
  $ProgressPreference = $old

  $got = (Get-Item $tmp).Length
  if ($got -ne $r.Size) {
    Write-Host "[!] ECHEC $($r.Name) : $got octets (attendu $($r.Size))" -ForegroundColor Red
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    $rc = 1; continue
  }
  Move-Item -Force $tmp $dest
  Write-Host "[OK] $($r.Sub)/$($r.Name)" -ForegroundColor Green
}

if ($rc -ne 0) {
  Write-Host "== Echec : au moins un modele image n'a pas pu etre telecharge. ==" -ForegroundColor Red
  exit $rc
}
Write-Host "== OK. Modeles image prets dans $ModelsDir ==" -ForegroundColor Green
exit 0
