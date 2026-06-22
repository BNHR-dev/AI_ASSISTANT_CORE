<#
.SYNOPSIS
  AAC - bootstrap Windows plug & play.

.DESCRIPTION
  Detecte les dependances deja presentes sur le systeme.
  Ce qui est absent est telecharge dans AAC_Data\ (dossier frere du repo).
  Blender est optionnel. SearXNG tourne via Docker avec redemarrage automatique.

  /!\ MODE AVANCE, NON SANDBOXE : l'install native Windows N'A PAS le sandbox
  bubblewrap (Linux uniquement) -> le code Blender genere tourne SANS confinement OS.
  Chemin RECOMMANDE et securise = Docker Desktop + WSL2 (run.bat / run.ps1), ou bwrap
  s'execute a l'interieur du conteneur Linux.

.PARAMETER CheckOnly
  Audit uniquement - affiche l'etat, n'installe rien.

.PARAMETER SkipComfyUI
  Passe ComfyUI et les modeles image (phase la plus lourde).

.PARAMETER AACDataDir
  Chemin du dossier de donnees IA. Defaut : <parent_du_repo>\AAC_Data
  Exemple : -AACDataDir "E:\AAC_Data"
#>
[CmdletBinding()]
param(
    [switch] $CheckOnly,
    [switch] $SkipComfyUI,
    [string] $AACDataDir = ''
)
$ErrorActionPreference = 'Stop'

# -- Chemins -------------------------------------------------------------------
# scripts\windows\ -> remonter de 2 niveaux pour la racine du repo (single-repo).
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$CoreDir  = Join-Path $RepoRoot 'core'
if (-not $AACDataDir) {
    $AACDataDir = Join-Path (Split-Path $RepoRoot -Parent) 'AAC_Data'
}

# -- Affichage -----------------------------------------------------------------
$script:HasError = $false
function Write-Det  ($m) { Write-Host "  [DET]  $m" -ForegroundColor DarkCyan }
function Write-Ok   ($m) { Write-Host "  [OK]   $m" -ForegroundColor Green    }
function Write-Dl   ($m) { Write-Host "  [DL]   $m" -ForegroundColor Cyan     }
function Write-Skip ($m) { Write-Host "  [~]    $m" -ForegroundColor Yellow   }
function Write-Fail ($m) { Write-Host "  [!]    $m" -ForegroundColor Red; $script:HasError = $true }
function Write-Head ($m) { Write-Host "`n-- $m" -ForegroundColor White        }
function Write-Info ($m) { Write-Host "         $m" -ForegroundColor DarkGray  }

# -- Telechargement ------------------------------------------------------------
function Get-File([string]$Url, [string]$Dest, [string]$Label) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null
    $tmp = "$Dest.part"
    Write-Dl "$Label"
    Write-Info "->  $Dest"
    $old = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
    try   { Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing }
    catch { $ProgressPreference = $old; throw }
    $ProgressPreference = $old
    Move-Item -Force $tmp $Dest
}

function Update-EnvPath {
    $env:Path = (@(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')
    ) | Where-Object { $_ }) -join ';'
}

# -- Detection Python (3.13 > 3.12 > 3.11) ------------------------------------
function Find-Python {
    foreach ($v in @('3.13', '3.12', '3.11')) {
        try {
            $args = @("-$v", '-c', 'import sys; print(sys.executable)')
            $p = (& py @args 2>$null) | Select-Object -First 1
            if ($p -and (Test-Path ($p = $p.Trim()))) { return $p }
        } catch {}
    }
    $cands = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        'C:\Program Files\Python313\python.exe',
        'C:\Program Files\Python312\python.exe',
        'C:\Program Files\Python311\python.exe'
    )
    return ($cands | Where-Object { Test-Path $_ } | Select-Object -First 1)
}

# -- Detection Ollama ----------------------------------------------------------
function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        (Join-Path $AACDataDir 'ollama\ollama.exe')
    )) { if (Test-Path $c) { return $c } }
    return $null
}

# -- Detection Blender (optionnel) ---------------------------------------------
function Find-Blender {
    $cmd = Get-Command blender -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $reg = Get-ItemProperty `
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'Blender' } | Select-Object -First 1
    if ($reg -and $reg.InstallLocation) {
        $c = Join-Path $reg.InstallLocation 'blender.exe'
        if (Test-Path $c) { return $c }
    }
    $c = Join-Path $AACDataDir 'blender\blender.exe'
    if (Test-Path $c) { return $c }
    return $null
}

# -- Detection ComfyUI ---------------------------------------------------------
function Find-ComfyUI {
    foreach ($root in @(
        (Join-Path $RepoRoot  'ComfyUI\ComfyUI_windows_portable'),
        (Join-Path $AACDataDir 'ComfyUI\ComfyUI_windows_portable')
    )) {
        if (Test-Path (Join-Path $root 'run_nvidia_gpu.bat')) { return $root }
    }
    return $null
}

# -- Detection Docker ----------------------------------------------------------
function Test-Docker {
    try { docker info 2>$null | Out-Null; return $true } catch { return $false }
}

# ==============================================================================
# HEADER
# ==============================================================================
Write-Host ''
Write-Host '  +-===============================================-+' -ForegroundColor White
Write-Host '  |    AAC - Installation Windows (plug & play)   |' -ForegroundColor White
Write-Host '  +-===============================================-+' -ForegroundColor White
Write-Host "  Repo    : $RepoRoot"   -ForegroundColor DarkGray
Write-Host "  Donnees : $AACDataDir" -ForegroundColor DarkGray
if ($CheckOnly) {
    Write-Host '  Mode    : AUDIT - rien ne sera installe' -ForegroundColor Yellow
}
Write-Host ''

# ==============================================================================
# 1. DeTECTION
# ==============================================================================
Write-Head '1/7  Detection des composants'

$pythonExe  = Find-Python
$ollamaExe  = Find-Ollama
$blenderExe = Find-Blender
$comfyRoot  = Find-ComfyUI
$dockerUp   = Test-Docker
$gitCmd     = Get-Command git -ErrorAction SilentlyContinue

# Dossier modeles LLM (variable env > ~/.ollama/models > sera dans AAC_Data)
$ollamaModelsDir = $env:OLLAMA_MODELS
if (-not $ollamaModelsDir -or -not (Test-Path $ollamaModelsDir)) {
    $def = "$env:USERPROFILE\.ollama\models"
    if (Test-Path $def) { $ollamaModelsDir = $def } else { $ollamaModelsDir = $null }
}

if ($pythonExe)      { Write-Det "Python       : $pythonExe" }
else                 { Write-Fail 'Python 3.11+ : introuvable (requis)' }

if ($ollamaExe)      { Write-Det "Ollama       : $ollamaExe" }
else                 { Write-Skip 'Ollama       : absent - sera telecharge dans AAC_Data' }

if ($ollamaModelsDir){ Write-Det "LLM models   : $ollamaModelsDir" }
else                 { Write-Info "LLM models   : sera dans $AACDataDir\ollama\models" }

if ($dockerUp)       { Write-Det 'Docker       : daemon actif' }
else                 { Write-Skip 'Docker       : non disponible (SearXNG sera desactive)' }

if ($comfyRoot)      { Write-Det "ComfyUI      : $comfyRoot" }
else                 { Write-Skip 'ComfyUI      : absent - sera telecharge dans AAC_Data' }

if ($blenderExe)     { Write-Det "Blender      : $blenderExe" }
else                 { Write-Skip 'Blender      : absent (optionnel - pipeline 3D desactive)' }

if ($gitCmd)         { Write-Det "Git          : $($gitCmd.Source)" }
else                 { Write-Skip 'Git          : absent (optionnel)' }

if ($CheckOnly) {
    Write-Host ''
    Write-Host '  Fin du mode audit.' -ForegroundColor Yellow
    Read-Host -Prompt '  Appuyez sur Entree pour fermer'
    exit 0
}

if (-not $pythonExe) {
    Write-Host ''
    Write-Fail 'Python est requis. Telecharger : https://python.org/downloads'
    Read-Host -Prompt '  Appuyez sur Entree pour fermer'
    exit 1
}

# ==============================================================================
# 2. OLLAMA + MODeLES LLM
# ==============================================================================
Write-Head '2/7  Ollama (moteur LLM)'

if (-not $ollamaExe) {
    $installer = Join-Path $AACDataDir 'setup\OllamaSetup.exe'
    try {
        Get-File 'https://ollama.com/download/OllamaSetup.exe' $installer 'Ollama installer'
        Write-Host '  Installation Ollama...' -ForegroundColor Gray
        Start-Process $installer -ArgumentList '/S' -Wait
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
        Update-EnvPath
        $ollamaExe = Find-Ollama
        if ($ollamaExe) { Write-Ok "Ollama installe  ->  $ollamaExe" }
        else             { Write-Fail 'Ollama : installation echouee' }
    } catch {
        Write-Fail "Ollama : $($_.Exception.Message)"
    }
} else {
    Write-Ok "Ollama  ->  $ollamaExe"
}

$LlmModels = @('qwen3:8b', 'qwen2.5-coder:7b', 'qwen2.5vl:3b')

if ($ollamaExe) {
    # Demarrer le serveur si pas deja actif
    $reachable = $false
    try {
        Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 3 | Out-Null
        $reachable = $true
    } catch {}

    if (-not $reachable) {
        Write-Host '  Demarrage du serveur Ollama...' -ForegroundColor Gray
        Start-Process $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
        for ($i = 0; $i -lt 20 -and -not $reachable; $i++) {
            Start-Sleep 1
            try {
                Invoke-WebRequest 'http://127.0.0.1:11434/api/tags' -UseBasicParsing -TimeoutSec 2 | Out-Null
                $reachable = $true
            } catch {}
        }
    }

    if (-not $reachable) {
        Write-Fail 'Serveur Ollama injoignable apres 20s'
    } else {
        Write-Ok 'Serveur Ollama actif  ->  http://127.0.0.1:11434'

        # Configurer OLLAMA_MODELS si pas encore fait
        if (-not $ollamaModelsDir) {
            $ollamaModelsDir = Join-Path $AACDataDir 'ollama\models'
            New-Item -ItemType Directory -Force -Path $ollamaModelsDir | Out-Null
            [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $ollamaModelsDir, 'User')
            $env:OLLAMA_MODELS = $ollamaModelsDir
            Write-Ok "OLLAMA_MODELS  ->  $ollamaModelsDir"
        }

        $present = (& $ollamaExe list 2>$null) | Out-String
        foreach ($m in $LlmModels) {
            $short = $m.Split(':')[0]
            if ($present -match [regex]::Escape($short)) {
                Write-Ok "modele $m deja present"
            } else {
                Write-Host "  Pull $m (peut prendre plusieurs minutes)..." -ForegroundColor Gray
                & $ollamaExe pull $m
                if ($LASTEXITCODE -eq 0) { Write-Ok "modele $m telecharge" }
                else                      { Write-Fail "pull $m echoue" }
            }
        }
    }
}

# ==============================================================================
# 3. SEARXNG (Docker - restart automatique)
# ==============================================================================
Write-Head '3/7  SearXNG (recherche web)'

if (-not $dockerUp) {
    Write-Skip 'Docker non disponible - SearXNG desactive'
    Write-Info 'Installer Docker Desktop pour activer la recherche web.'
    Write-Info 'https://www.docker.com/products/docker-desktop'
} else {
    # Image SearXNG pinnee (alignee sur docker-compose Linux/Windows historique).
    $searxImage = 'searxng/searxng:2026.5.10-df1f24fb7'

    # -- Config SearXNG : le backend interroge /search?format=json. L'image par
    #    defaut n'autorise QUE 'html' (et active le limiter) -> 403 sur json.
    #    On ecrit donc une config qui active json + desactive le limiter, A
    #    L'IDENTIQUE de la variante Linux (searxng/settings.example.yml).
    $searxCfgDir  = Join-Path $AACDataDir 'searxng'
    $searxCfgFile = Join-Path $searxCfgDir 'settings.yml'
    New-Item -ItemType Directory -Force -Path $searxCfgDir | Out-Null
    if (-not (Test-Path $searxCfgFile)) {
        $secret = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
        $searxLines = @(
            '# settings.yml - genere par bootstrap.ps1 (parite Linux : json + limiter off).',
            'use_default_settings: true',
            '',
            'general:',
            '  debug: false',
            '  instance_name: "Local AI Search"',
            '',
            'server:',
            "  secret_key: `"$secret`"",
            '  limiter: false',
            '  image_proxy: true',
            '',
            'search:',
            '  safe_search: 0',
            '  autocomplete: "duckduckgo"',
            '  formats:',
            '    - html',
            '    - json',
            '',
            'redis:',
            '  url: false'
        )
        [System.IO.File]::WriteAllLines(
            $searxCfgFile, $searxLines,
            (New-Object System.Text.UTF8Encoding($false))
        )
        Write-Ok "config SearXNG ecrite  ->  $searxCfgFile"
    } else {
        Write-Ok "config SearXNG presente  ->  $searxCfgFile"
    }

    # -- Le container doit monter cette config. Un container existant SANS le
    #    volume de config laisserait json en 403 -> on teste et on recree si besoin.
    function Test-SearxJson {
        try {
            $r = Invoke-WebRequest 'http://127.0.0.1:8081/search?q=test&format=json' `
                 -UseBasicParsing -TimeoutSec 5
            return ($r.StatusCode -eq 200)
        } catch { return $false }
    }

    $ErrorActionPreference = 'Continue'
    $running = docker ps --filter 'name=searxng' --format '{{.Names}}' 2>$null |
               Select-String 'searxng' -Quiet

    $needRecreate = $true
    if ($running -and (Test-SearxJson)) {
        $needRecreate = $false
        Write-Ok 'SearXNG tourne deja (json OK)  ->  http://127.0.0.1:8081'
    }

    if ($needRecreate) {
        Write-Host '  (Re)creation du container SearXNG avec config json...' -ForegroundColor Gray
        docker stop searxng 2>&1 | Out-Null
        docker rm   searxng 2>&1 | Out-Null
        docker run -d --name searxng --restart unless-stopped `
            -p 127.0.0.1:8081:8080 `
            -v "${searxCfgDir}:/etc/searxng" `
            $searxImage 2>&1 | Out-Null

        $ok = $false
        for ($i = 0; $i -lt 20 -and -not $ok; $i++) { Start-Sleep 1; $ok = Test-SearxJson }
        if ($ok) {
            Write-Ok 'SearXNG demarre (json OK)  ->  http://127.0.0.1:8081'
            Write-Info 'Se relance automatiquement a chaque demarrage de Docker.'
        } else {
            Write-Skip 'SearXNG : json injoignable apres 20s (recherche web desactivee)'
        }
    }
    $ErrorActionPreference = 'Stop'
}

# ==============================================================================
# 4. COMFYUI + MODeLES IMAGE
# ==============================================================================
$ImageModels = @(
    @{
        Sub  = 'checkpoints'
        Name = 'RealVisXL_V5.0_fp16.safetensors'
        Url  = 'https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors'
        Size = 6938065488
    },
    @{
        Sub  = 'upscale_models'
        Name = '4x-UltraSharp.pth'
        Url  = 'https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth'
        Size = 66961958
    }
)

if ($SkipComfyUI) {
    Write-Head '4/7  ComfyUI - saute (-SkipComfyUI)'
} else {
    Write-Head '4/7  ComfyUI (generation image)'

    if (-not $comfyRoot) {
        # 7-Zip requis pour extraire le .7z
        $szExe = $null
        if (Test-Path 'C:\Program Files\7-Zip\7z.exe') {
            $szExe = 'C:\Program Files\7-Zip\7z.exe'
        } else {
            $szCmd = Get-Command 7z -ErrorAction SilentlyContinue
            if ($szCmd) { $szExe = $szCmd.Source }
        }

        if (-not $szExe) {
            Write-Fail 'ComfyUI : 7-Zip requis mais introuvable'
            Write-Info 'winget install 7zip.7zip  puis relancer bootstrap.ps1'
        } else {
            try {
                Write-Host '  Recuperation de la derniere version ComfyUI...' -ForegroundColor Gray
                $rel   = Invoke-RestMethod `
                    'https://api.github.com/repos/comfyanonymous/ComfyUI/releases/latest' `
                    -Headers @{'User-Agent' = 'AAC-bootstrap'}
                $asset = $rel.assets |
                    Where-Object { $_.name -match 'windows_portable_nvidia.*\.7z$' } |
                    Select-Object -First 1
                if (-not $asset) { throw "Asset ComfyUI introuvable dans $($rel.tag_name)" }

                $destDir = Join-Path $AACDataDir 'ComfyUI'
                $archive = Join-Path $destDir $asset.name
                $sizeMb  = [math]::Round($asset.size / 1MB)
                Get-File $asset.browser_download_url $archive "ComfyUI portable (~${sizeMb} Mo)"

                Write-Host '  Extraction...' -ForegroundColor Gray
                & $szExe x $archive "-o$destDir" -y | Out-Null
                Remove-Item $archive -Force -ErrorAction SilentlyContinue

                $comfyRoot = Find-ComfyUI
                if ($comfyRoot) { Write-Ok "ComfyUI  ->  $comfyRoot" }
                else             { Write-Fail 'ComfyUI : structure inattendue apres extraction' }
            } catch {
                Write-Fail "ComfyUI : $($_.Exception.Message)"
            }
        }
    } else {
        Write-Ok "ComfyUI deja present  ->  $comfyRoot"
    }

    # Modeles image
    if ($comfyRoot) {
        $modelsDir = Join-Path $comfyRoot 'ComfyUI\models'
        foreach ($im in $ImageModels) {
            $dest = Join-Path $modelsDir "$($im.Sub)\$($im.Name)"
            if ((Test-Path $dest) -and (Get-Item $dest).Length -eq $im.Size) {
                Write-Ok "modele $($im.Name)"
            } else {
                $sizeMb = [math]::Round($im.Size / 1MB)
                try {
                    Get-File $im.Url $dest "$($im.Name) (~${sizeMb} Mo)"
                    Write-Ok "$($im.Name) telecharge"
                } catch {
                    Write-Fail "$($im.Name) : $($_.Exception.Message)"
                }
            }
        }
    }
}

# ==============================================================================
# 5. PYTHON VENV + REQUIREMENTS
# ==============================================================================
Write-Head '5/7  Backend Python (venv)'

$venvDir = Join-Path $CoreDir '.venv'
$venvPy  = Join-Path $venvDir 'Scripts\python.exe'

if (Test-Path $venvPy) {
    Write-Ok "venv deja present  ->  $venvDir"
} else {
    Write-Host "  Creation du venv avec $pythonExe..." -ForegroundColor Gray
    & $pythonExe -m venv $venvDir
    Write-Host '  Installation des dependances...' -ForegroundColor Gray
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -r (Join-Path $CoreDir 'requirements.txt') --quiet
    if (Test-Path $venvPy) { Write-Ok "venv pret  ->  $venvDir" }
    else                   { Write-Fail 'venv : creation echouee' }
}

# ==============================================================================
# 6. eCRITURE .env
# ==============================================================================
Write-Head '6/7  Configuration (.env)'

$comfyModelsDir  = if ($comfyRoot) { Join-Path $comfyRoot 'ComfyUI\models' } else { '' }
$comfyOutputDir  = if ($comfyRoot) { Join-Path $comfyRoot 'ComfyUI\output' } else { '' }
$envPath        = Join-Path $CoreDir '.env'

$envLines = [System.Collections.Generic.List[string]]@(
    '# Genere par bootstrap.ps1 - ne pas editer manuellement.',
    '# Relancer bootstrap.ps1 pour regenerer apres un changement de config.',
    '',
    '# -- Services ----------------------------------------',
    'OLLAMA_BASE_URL=http://127.0.0.1:11434',
    'OLLAMA_URL=http://127.0.0.1:11434/api/generate',
    'OLLAMA_TAGS_URL=http://127.0.0.1:11434/api/tags',
    'SEARXNG_SEARCH_URL=http://127.0.0.1:8081/search',
    'COMFYUI_URL=http://127.0.0.1:8700',
    '',
    '# -- ComfyUI ------------------------------------------',
    'COMFYUI_AUTO_START=false',
    'COMFYUI_CHECKPOINT_NAME=RealVisXL_V5.0_fp16.safetensors',
    'COMFYUI_REFINER_CHECKPOINT_NAME=RealVisXL_V5.0_fp16.safetensors',
    'COMFYUI_UPSCALE_MODEL_NAME=4x-UltraSharp.pth'
)

if ($ollamaModelsDir) { $envLines.Add("OLLAMA_MODELS_DIR=$ollamaModelsDir") }
if ($ollamaExe)       { $envLines.Add("OLLAMA_EXE=$ollamaExe") }
if ($comfyModelsDir)  { $envLines.Add("COMFYUI_MODELS_DIR=$comfyModelsDir") }
if ($comfyOutputDir)  { $envLines.Add("COMFYUI_OUTPUT_DIR=$comfyOutputDir") }
if ($comfyRoot)       { $envLines.Add("COMFYUI_ROOT=$comfyRoot") }
if ($blenderExe)      { $envLines.Add("BLENDER_EXE=$blenderExe") }
if (Test-Path $venvPy){ $envLines.Add("AAC_VENV_PYTHON=$venvPy") }

[System.IO.File]::WriteAllLines(
    $envPath, $envLines,
    (New-Object System.Text.UTF8Encoding($false))
)
Write-Ok ".env ecrit  ->  $envPath"
foreach ($l in ($envLines | Where-Object { $_ -and -not $_.StartsWith('#') -and $_ -ne '' })) {
    Write-Info $l
}

# ==============================================================================
# 7. FICHIERS DE LANCEMENT
# ==============================================================================
Write-Head '7/7  Fichiers de lancement'

# Start-AAC.* sont dans scripts\windows\, rien a generer.
$startPs1 = Join-Path $RepoRoot 'scripts\windows\Start-AAC.ps1'
$startBat = Join-Path $RepoRoot 'scripts\windows\Start-AAC.bat'
if (Test-Path $startBat) { Write-Ok "Start-AAC.bat  ->  $startBat" }
if (Test-Path $startPs1) { Write-Ok "Start-AAC.ps1  ->  $startPs1" }

# ==============================================================================
# BILAN
# ==============================================================================
Write-Host ''
Write-Host '  -------------------------------------------------' -ForegroundColor DarkGray

if ($script:HasError) {
    Write-Host '  INSTALLATION INCOMPLeTE - voir les [!] ci-dessus.' -ForegroundColor Red
    Write-Host '  Corrige les erreurs puis relance Install-AAC.bat.' -ForegroundColor Red
} else {
    Write-Host '  INSTALLATION TERMINeE.' -ForegroundColor Green
    Write-Host ''
    Write-Host '  Pour lancer AAC :' -ForegroundColor White
    Write-Host "    double-clic sur  Start-AAC.bat" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Services actifs au prochain boot :' -ForegroundColor White
    Write-Host '    * Ollama    : tray Windows (demarrage automatique)' -ForegroundColor DarkGray
    Write-Host '    * SearXNG   : container Docker (restart unless-stopped)' -ForegroundColor DarkGray
    Write-Host '    * Backend   : demarre par Start-AAC.bat uniquement' -ForegroundColor DarkGray
}

Write-Host ''
Read-Host -Prompt '  Appuyez sur Entree pour fermer'
