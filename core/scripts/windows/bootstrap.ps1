<#
.SYNOPSIS
  AAC — bootstrap natif Windows (machine vierge). Installe TOUTES les dépendances et
  prépare le runtime, sans Docker.

.DESCRIPTION
  Sur un Windows 10/11 neuf, ce script installe et configure, de façon idempotente :
    - Git, Python 3.12, 7-Zip            (via winget — App Installer, présent nativement)
    - Ollama (natif) + les 3 modèles LLM (qwen3:8b, qwen2.5-coder:7b, qwen2.5vl:3b)
    - Blender (natif, pipeline 3D)
    - ComfyUI portable + modèles image   (RealVisXL V5.0 + 4x-UltraSharp)
    - le venv du backend (core/.venv + requirements) et un core/.env natif

  Chaque phase est idempotente (re-jouable) et résiliente : l'échec d'une phase
  optionnelle (ComfyUI) n'interrompt pas le cœur router/planner/executor. SearXNG
  (recherche web) n'a pas d'install native propre sous Windows — laissé optionnel.

.PARAMETER CheckOnly
  N'installe rien : se comporte en « doctor » et n'affiche que l'état (✓/✗/~).

.PARAMETER SkipComfyUI
  Saute l'installation de ComfyUI et des modèles image (phase la plus lourde).

.PARAMETER DataRoot
  Dossier racine pour toutes les données IA (modèles LLM, ComfyUI...).
  Si absent en mode installation, un sélecteur de dossier s'ouvre.
  Exemple : -DataRoot "E:\AI_ASSISTANT_CORE"

.PARAMETER ComfyUIDir
  Dossier d'installation de ComfyUI portable.
  Par défaut : <DataRoot>\ComfyUI, ou %USERPROFILE%\AAC\ComfyUI si DataRoot absent.

.EXAMPLE
  # Double-clic sur Install-AAC.bat, ou en terminal :
  powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1 -CheckOnly
#>
[CmdletBinding()]
param(
  [switch] $CheckOnly,
  [switch] $SkipComfyUI,
  [string] $DataRoot   = '',
  [string] $ComfyUIDir = ''
)

$ErrorActionPreference = 'Stop'

# --- Chemins du dépôt --------------------------------------------------------
# Ce script vit dans core\scripts\windows\ -> remonter de deux niveaux = core\.
$CoreDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RepoRoot = Split-Path -Parent $CoreDir

# Modèles LLM requis (source de vérité : core\app\engine\task_routing.py).
$LlmModels = @('qwen3:8b', 'qwen2.5-coder:7b', 'qwen2.5vl:3b')

# Modèles image (mêmes URLs/tailles que scripts/fetch-models.sh).
$ImageModels = @(
  @{ Sub = 'checkpoints';    Name = 'RealVisXL_V5.0_fp16.safetensors';
     Url = 'https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors';
     Size = 6938065488 },
  @{ Sub = 'upscale_models'; Name = '4x-UltraSharp.pth';
     Url = 'https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth';
     Size = 66961958 }
)

# --- Sortie ------------------------------------------------------------------
$script:Fail = $false
function Write-Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Miss($m) { Write-Host "  [X]    $m" -ForegroundColor Red;    $script:Fail = $true }
function Write-Warn($m) { Write-Host "  [~]    $m" -ForegroundColor Yellow }
function Write-Hint($m) { Write-Host "         -> $m" -ForegroundColor DarkGray }

# --- Helpers -----------------------------------------------------------------
function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Invoke-Elevate {
  # Relance le script en admin (winget machine-scope en a besoin), arguments conservés.
  $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
  if ($CheckOnly)   { $argList += '-CheckOnly' }
  if ($SkipComfyUI) { $argList += '-SkipComfyUI' }
  if ($DataRoot)    { $argList += @('-DataRoot',   "`"$DataRoot`"") }
  if ($ComfyUIDir)  { $argList += @('-ComfyUIDir', "`"$ComfyUIDir`"") }
  Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList
}

function Update-PathFromRegistry {
  # winget modifie le PATH machine/user : le rafraîchir dans la session courante.
  $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $u = [Environment]::GetEnvironmentVariable('Path', 'User')
  $env:Path = ($m, $u | Where-Object { $_ }) -join ';'
}

function Select-DataRoot {
  # Suggère le disque non-C avec le plus d'espace libre comme racine par défaut.
  $suggested = try {
    $drive = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -ne 'C' -and $_.Free -gt 0 } |
      Sort-Object Free -Descending | Select-Object -First 1
    if ($drive) { "$($drive.Name):\AAC" } else { Join-Path $env:USERPROFILE 'AAC' }
  } catch { Join-Path $env:USERPROFILE 'AAC' }

  # Tentative dialog GUI (Windows Forms).
  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description         = "Dossier racine pour les données IA (modèles LLM, ComfyUI — plusieurs Go)"
    $dlg.SelectedPath        = $suggested
    $dlg.ShowNewFolderButton = $true
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.SelectedPath }
  } catch { }

  # Fallback texte si GUI indisponible.
  Write-Host "`nDossier racine pour les données IA [$suggested] : " -NoNewline -ForegroundColor Cyan
  $ans = Read-Host
  if ($ans.Trim()) { return $ans.Trim() } else { return $suggested }
}

function Test-Winget {
  if (Get-Command winget -ErrorAction SilentlyContinue) { return $true }
  Write-Miss "winget introuvable (App Installer)"
  Write-Hint "installer 'App Installer' depuis le Microsoft Store, puis relancer"
  return $false
}

function Test-WingetInstalled($id) {
  winget list --id $id -e --accept-source-agreements 2>$null | Out-String |
    Select-String -SimpleMatch $id -Quiet
}

function Install-WingetPackage($id, $label) {
  if (Test-WingetInstalled $id) { Write-Ok "$label déjà installé"; return }
  if ($CheckOnly) { Write-Miss "$label absent"; Write-Hint "winget install --id $id"; return }
  Write-Host "  installation de $label ($id) ..." -ForegroundColor Gray
  winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Ok "$label installé" }
  else { Write-Miss "échec installation $label (winget code $LASTEXITCODE)" }
}

function Get-Model($url, $dest, $expected) {
  if ((Test-Path $dest) -and ((Get-Item $dest).Length -eq $expected)) {
    Write-Ok "déjà présent : $(Split-Path $dest -Leaf)"; return
  }
  if ($CheckOnly) { Write-Miss "$(Split-Path $dest -Leaf) absent"; return }
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  Write-Host "  téléchargement $(Split-Path $dest -Leaf) (~$([math]::Round($expected/1MB)) Mo) ..." -ForegroundColor Gray
  $tmp = "$dest.part"
  $old = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'  # x10 plus rapide
  try { Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing }
  finally { $ProgressPreference = $old }
  $got = (Get-Item $tmp).Length
  if ($got -ne $expected) { Remove-Item $tmp -Force; Write-Miss "$(Split-Path $dest -Leaf) : $got octets (attendu $expected)"; return }
  Move-Item -Force $tmp $dest
  Write-Ok (Split-Path $dest -Leaf)
}

# =============================================================================
# Résolution des chemins de données (AVANT élévation pour transmettre via UAC)
# =============================================================================
if (-not $CheckOnly -and -not $DataRoot) {
  $DataRoot = Select-DataRoot
  Write-Host "Dossier racine IA : $DataRoot" -ForegroundColor Cyan
}
if (-not $ComfyUIDir) {
  $ComfyUIDir = if ($DataRoot) { Join-Path $DataRoot 'ComfyUI' }
                else           { Join-Path $env:USERPROFILE 'AAC\ComfyUI' }
}

# =============================================================================
# Élévation
# =============================================================================
if (-not $CheckOnly -and -not (Test-Admin)) {
  Write-Host "Élévation administrateur requise pour les installations winget..." -ForegroundColor Yellow
  Invoke-Elevate
  return
}

Write-Host "AAC — bootstrap Windows natif" -ForegroundColor White
Write-Host "Dépôt : $RepoRoot" -ForegroundColor DarkGray
if ($CheckOnly) { Write-Host "(mode CheckOnly : aucune installation)" -ForegroundColor Yellow }

# =============================================================================
# Phase 1 — paquets natifs (winget)
# =============================================================================
Write-Step "Paquets natifs (winget)"
if (Test-Winget) {
  Install-WingetPackage 'Git.Git'                 'Git'
  Install-WingetPackage 'Python.Python.3.12'      'Python 3.12'
  Install-WingetPackage '7zip.7zip'               '7-Zip'
  Install-WingetPackage 'Ollama.Ollama'           'Ollama'
  Install-WingetPackage 'BlenderFoundation.Blender' 'Blender'
  Update-PathFromRegistry
}

# =============================================================================
# Phase 2 — Ollama : serveur + modèles LLM
# =============================================================================
Write-Step "Ollama (LLM)"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
  Write-Miss "binaire ollama introuvable après installation"
  Write-Hint "fermer/rouvrir le terminal (PATH), puis relancer"
} else {
  # Configurer OLLAMA_MODELS si DataRoot fourni et variable non déjà définie.
  if ($DataRoot) {
    $ollamaModelsDir = Join-Path $DataRoot 'ollama\models'
    if ($env:OLLAMA_MODELS) {
      Write-Ok "OLLAMA_MODELS → $env:OLLAMA_MODELS (conservé)"
    } elseif ($CheckOnly) {
      Write-Warn "OLLAMA_MODELS non défini (sera : $ollamaModelsDir)"
      Write-Hint "relancer sans -CheckOnly pour configurer"
    } else {
      New-Item -ItemType Directory -Force -Path $ollamaModelsDir | Out-Null
      [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $ollamaModelsDir, 'User')
      $env:OLLAMA_MODELS = $ollamaModelsDir
      Write-Ok "OLLAMA_MODELS → $ollamaModelsDir"
    }
  }

  # S'assurer que le serveur répond ; sinon le démarrer en arrière-plan.
  $reachable = $false
  try { Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 3 | Out-Null; $reachable = $true } catch { }
  if (-not $reachable -and -not $CheckOnly) {
    Write-Host "  démarrage du serveur ollama..." -ForegroundColor Gray
    Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and -not $reachable; $i++) {
      Start-Sleep -Seconds 1
      try { Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 3 | Out-Null; $reachable = $true } catch { }
    }
  }
  if (-not $reachable) {
    Write-Warn "serveur ollama injoignable sur 127.0.0.1:11434"
    Write-Hint "lancer Ollama (menu Démarrer) puis relancer"
  } else {
    Write-Ok "serveur ollama joignable"
    $present = (& ollama list 2>$null | Out-String)
    foreach ($m in $LlmModels) {
      if ($present -match [regex]::Escape($m)) { Write-Ok "modèle $m"; continue }
      if ($CheckOnly) { Write-Miss "modèle $m manquant"; continue }
      Write-Host "  pull $m ..." -ForegroundColor Gray
      & ollama pull $m
      if ($LASTEXITCODE -eq 0) { Write-Ok "modèle $m" } else { Write-Miss "échec pull $m" }
    }
  }
}

# =============================================================================
# Phase 3 — Blender (3D, hôte)
# =============================================================================
Write-Step "Blender (3D)"
Update-PathFromRegistry
$blender = Get-Command blender -ErrorAction SilentlyContinue
if (-not $blender) {
  # winget ne met pas toujours blender.exe au PATH : sonder les emplacements connus.
  $cand = Get-ChildItem 'C:\Program Files\Blender Foundation\*\blender.exe' -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
  if (-not $cand) {
    # Fallback registre — couvre les installs sur D:\, E:\, etc.
    $regKeys = @(
      'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
      'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $installDir = Get-ItemProperty $regKeys -ErrorAction SilentlyContinue |
      Where-Object { $_.DisplayName -match 'Blender' } |
      Select-Object -First 1 -ExpandProperty InstallLocation
    if ($installDir) {
      $cand = Get-Item (Join-Path $installDir 'blender.exe') -ErrorAction SilentlyContinue
    }
  }
  if ($cand) { $blender = $cand }
}
if ($blender) {
  $blenderExe = if ($blender.Path) { $blender.Path } else { $blender.FullName }
  Write-Ok "Blender : $blenderExe"
} else {
  $blenderExe = ''
  Write-Warn "Blender introuvable (pipeline 3D désactivé ; le cœur marche sans)"
}

# =============================================================================
# Phase 4 — ComfyUI portable + modèles image
# =============================================================================
$comfyModelsDir = ''
if ($SkipComfyUI) {
  Write-Step "ComfyUI (image) — SAUTÉ (-SkipComfyUI)"
} else {
  Write-Step "ComfyUI portable + modèles image"
  $comfyRoot = Join-Path $ComfyUIDir 'ComfyUI_windows_portable'
  $comfyModelsDir = Join-Path $comfyRoot 'ComfyUI\models'
  if (Test-Path (Join-Path $comfyRoot 'run_nvidia_gpu.bat')) {
    Write-Ok "ComfyUI portable déjà présent : $comfyRoot"
  } elseif ($CheckOnly) {
    Write-Miss "ComfyUI portable absent ($comfyRoot)"
  } else {
    try {
      $sevenZip = Get-Command 7z -ErrorAction SilentlyContinue
      if (-not $sevenZip) { $sevenZip = Get-Item 'C:\Program Files\7-Zip\7z.exe' -ErrorAction SilentlyContinue }
      if (-not $sevenZip) { throw "7-Zip introuvable (requis pour extraire le .7z)" }
      Write-Host "  recherche de la dernière release ComfyUI..." -ForegroundColor Gray
      $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest' -Headers @{ 'User-Agent' = 'AAC-bootstrap' }
      $asset = $rel.assets | Where-Object { $_.name -match 'windows_portable_nvidia.*\.7z$' } | Select-Object -First 1
      if (-not $asset) { throw "asset 'windows_portable_nvidia*.7z' introuvable dans $($rel.tag_name)" }
      New-Item -ItemType Directory -Force -Path $ComfyUIDir | Out-Null
      $archive = Join-Path $ComfyUIDir $asset.name
      Write-Host "  téléchargement $($asset.name) (~$([math]::Round($asset.size/1MB)) Mo) ..." -ForegroundColor Gray
      $old = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
      try { Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive -UseBasicParsing } finally { $ProgressPreference = $old }
      Write-Host "  extraction (7-Zip)..." -ForegroundColor Gray
      $szExe = if ($sevenZip.Path) { $sevenZip.Path } else { $sevenZip.FullName }
      & $szExe x $archive "-o$ComfyUIDir" -y | Out-Null
      Remove-Item $archive -Force -ErrorAction SilentlyContinue
      if (Test-Path (Join-Path $comfyRoot 'run_nvidia_gpu.bat')) { Write-Ok "ComfyUI portable installé : $comfyRoot" }
      else { Write-Miss "extraction ComfyUI : structure inattendue" }
    } catch {
      Write-Warn "ComfyUI non installé : $($_.Exception.Message)"
      Write-Hint "réessayer plus tard : bootstrap.ps1 (les autres deps restent OK)"
    }
  }
  # Modèles image -> dossier models de ComfyUI (sinon repli core\models).
  if (-not (Test-Path $comfyModelsDir)) { $comfyModelsDir = Join-Path $CoreDir 'models' }
  foreach ($im in $ImageModels) {
    $dest = Join-Path $comfyModelsDir (Join-Path $im.Sub $im.Name)
    Get-Model $im.Url $dest $im.Size
  }
}

# =============================================================================
# Phase 5 — backend Python (venv + requirements)
# =============================================================================
Write-Step "Backend (venv Python)"
Update-PathFromRegistry
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
$venv = Join-Path $CoreDir '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
if (-not $py) {
  Write-Miss "Python introuvable"; Write-Hint "rouvrir le terminal (PATH) puis relancer"
} elseif (Test-Path $venvPy) {
  Write-Ok "venv déjà présent : $venv"
} elseif ($CheckOnly) {
  Write-Miss "venv absent ($venv)"
} else {
  Write-Host "  création du venv + pip install -r requirements.txt ..." -ForegroundColor Gray
  & $py.Path -m venv $venv
  & $venvPy -m pip install --upgrade pip --quiet
  & $venvPy -m pip install -r (Join-Path $CoreDir 'requirements.txt') --quiet
  if (Test-Path $venvPy) { Write-Ok "venv prêt : $venv" } else { Write-Miss "échec création venv" }
}

# =============================================================================
# Phase 6 — core\.env natif
# =============================================================================
Write-Step "Configuration (core\.env)"
$envPath = Join-Path $CoreDir '.env'
if (Test-Path $envPath) {
  Write-Ok ".env déjà présent (laissé tel quel)"
} elseif ($CheckOnly) {
  Write-Miss ".env absent"
} else {
  $lines = @(
    '# core/.env — généré par scripts/windows/bootstrap.ps1 (runtime natif Windows).',
    'OLLAMA_BASE_URL=http://127.0.0.1:11434',
    'OLLAMA_URL=http://127.0.0.1:11434/api/generate',
    'OLLAMA_TAGS_URL=http://127.0.0.1:11434/api/tags',
    'SEARXNG_SEARCH_URL=http://127.0.0.1:8081/search',
    'COMFYUI_URL=http://127.0.0.1:8188',
    'COMFYUI_AUTO_START=false',
    'COMFYUI_CHECKPOINT_NAME=RealVisXL_V5.0_fp16.safetensors',
    'COMFYUI_REFINER_CHECKPOINT_NAME=RealVisXL_V5.0_fp16.safetensors',
    'COMFYUI_UPSCALE_MODEL_NAME=4x-UltraSharp.pth'
  )
  if ($comfyModelsDir) { $lines += "COMFYUI_MODELS_DIR=$comfyModelsDir" }
  if ($blenderExe)     { $lines += "BLENDER_EXE=$blenderExe" }
  # UTF-8 SANS BOM (un BOM corromprait la 1re clé lue par python-dotenv).
  [System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
  Write-Ok ".env écrit : $envPath"
}

# =============================================================================
# Phase 7 — SearXNG via Docker Desktop
# =============================================================================
Write-Step "SearXNG (recherche web) via Docker Desktop"

$dockerInstalled = Test-WingetInstalled 'Docker.DockerDesktop'
if (-not $dockerInstalled) {
  Install-WingetPackage 'Docker.DockerDesktop' 'Docker Desktop'
  if (-not $CheckOnly) {
    Write-Warn "Docker Desktop vient d'être installé — un redémarrage peut être nécessaire"
    Write-Hint "Redémarrez, relancez Install-AAC.bat, puis SearXNG sera configuré automatiquement"
  }
} else {
  # Vérifier que le daemon Docker répond
  $dockerReady = $false
  try { docker info 2>$null | Out-Null; $dockerReady = $true } catch { }

  if (-not $dockerReady -and -not $CheckOnly) {
    Write-Host "  démarrage de Docker Desktop..." -ForegroundColor Gray
    $dockerExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $dockerExe) { Start-Process $dockerExe }
    for ($i = 0; $i -lt 24 -and -not $dockerReady; $i++) {
      Start-Sleep -Seconds 5
      try { docker info 2>$null | Out-Null; $dockerReady = $true } catch { }
    }
  }

  if (-not $dockerReady) {
    Write-Warn "daemon Docker injoignable — lancer Docker Desktop puis relancer"
    Write-Hint "Une fois Docker prêt, relancer Install-AAC.bat ou bootstrap.ps1"
  } else {
    $searxRunning = docker ps  --filter 'name=searxng' --format '{{.Names}}' 2>$null | Select-String 'searxng' -Quiet
    $searxExists  = docker ps -a --filter 'name=searxng' --format '{{.Names}}' 2>$null | Select-String 'searxng' -Quiet

    if ($searxRunning) {
      Write-Ok "SearXNG en cours d'exécution (http://127.0.0.1:8081/search)"
    } elseif ($CheckOnly) {
      if ($searxExists) { Write-Warn "conteneur SearXNG présent mais arrêté" }
      else              { Write-Miss "conteneur SearXNG absent" }
      Write-Hint "docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8081:8080 searxng/searxng"
    } elseif ($searxExists) {
      docker start searxng 2>$null | Out-Null
      Write-Ok "conteneur SearXNG redémarré (http://127.0.0.1:8081/search)"
    } else {
      Write-Host "  pull + démarrage de searxng/searxng..." -ForegroundColor Gray
      docker run -d --name searxng --restart unless-stopped `
        -p 127.0.0.1:8081:8080 searxng/searxng 2>$null | Out-Null
      Start-Sleep -Seconds 3
      $ok = docker ps --filter 'name=searxng' --format '{{.Names}}' 2>$null | Select-String 'searxng' -Quiet
      if ($ok) { Write-Ok "SearXNG démarré (http://127.0.0.1:8081/search)" }
      else     { Write-Miss "échec démarrage conteneur SearXNG" }
    }
  }
}

# =============================================================================
# Bilan
# =============================================================================
Write-Host ""
if ($script:Fail) {
  Write-Host "== Bootstrap INCOMPLET : voir les [X] ci-dessus. ==" -ForegroundColor Red
} else {
  Write-Host "== Bootstrap OK. Démarrer le backend (depuis core\) :" -ForegroundColor Green
  Write-Host "   cd `"$CoreDir`"; .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000" -ForegroundColor Gray
}
if (-not $CheckOnly) { Write-Host "`nAppuyez sur Entrée pour fermer..."; [void][Console]::ReadLine() }
