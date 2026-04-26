<#
.SYNOPSIS
    AI_ASSISTANT_CORE — Synchronisation host -> VM (push contrôlé).

.DESCRIPTION
    Pousse un périmètre canonique de fichiers depuis le host Windows
    vers la VM Hyper-V AICORE-VM, avec :
      - whitelist stricte (scripts/deploy_vm/whitelist.txt)
      - denylist de garde (.env, secrets, settings, data, outputs, etc.)
      - manifest SHA-256 généré côté host, vérifié côté VM
      - snapshot tar.gz pré-push obligatoire dans la VM
      - aucun restart automatique (backend ou SearXNG)
      - dry-run par défaut

    Voir RUNBOOK_POST_VM.md, section "Synchro host -> VM".

.PARAMETER VmTarget
    Cible SSH (user@host). Défaut : bnhr@192.168.77.10.

.PARAMETER VmRoot
    Racine du repo côté VM. Défaut : /home/bnhr/aicore/projects/core.

.PARAMETER RepoRoot
    Racine du repo côté host. Vide = auto-détection depuis $PSScriptRoot.

.PARAMETER Apply
    Si présent, exécute réellement le push. Sinon dry-run.

.EXAMPLE
    .\scripts\deploy_vm\deploy_to_vm.ps1
    # Dry-run avec valeurs par défaut.

.EXAMPLE
    .\scripts\deploy_vm\deploy_to_vm.ps1 -Apply
    # Push réel avec valeurs par défaut.
#>

[CmdletBinding()]
param(
    [string]$VmTarget = "bnhr@192.168.77.10",
    [string]$VmRoot   = "/home/bnhr/aicore/projects/core",
    [string]$RepoRoot = "",
    [switch]$Apply
)

# Comportement strict : on veut un échec immédiat sur erreur non gérée.
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ----------------------------------------------------------
# Constantes dérivées (relatives, jamais codées en dur en chemin absolu)
# ----------------------------------------------------------

# Chemin relatif du script lui-même : scripts/deploy_vm/
# Remontée de deux niveaux pour atteindre la racine repo si auto-détection.
$ScriptDir = $PSScriptRoot

# Marqueurs canoniques attendus à la racine du repo host.
# Tous doivent être présents pour valider la racine.
$RepoMarkers = @(
    "openai_compat.py",
    "app",
    "docker-compose.yml",
    "scripts/deploy_vm/whitelist.txt"
)

# Fichiers de référence côté repo (résolus après détection RepoRoot).
$WhitelistRelPath      = "scripts/deploy_vm/whitelist.txt"
$VerifyScriptRelPath   = "scripts/deploy_vm/verify_manifest.sh"

# Sortie / logs côté host.
$LogDirRelPath         = "archive_notes/sync_log"

# Cibles côté VM.
$VmSnapshotsDir        = "/home/bnhr/aicore/snapshots"

# Timestamp de run (utilisé pour snapshot VM, log host, nom manifest).
$RunStamp = (Get-Date -Format "yyyyMMdd-HHmmss")

# ----------------------------------------------------------
# Résolution RepoRoot
# ----------------------------------------------------------

function Resolve-RepoRoot {
    param(
        [string]$Explicit,
        [string]$ScriptDir,
        [string[]]$Markers
    )

    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        $candidate = (Resolve-Path -LiteralPath $Explicit -ErrorAction Stop).Path
        Write-Verbose "RepoRoot fourni explicitement : $candidate"
    }
    else {
        if ([string]::IsNullOrWhiteSpace($ScriptDir)) {
            throw "Impossible de déterminer `$PSScriptRoot. Lancez le script depuis un fichier .ps1, pas en copier-coller dans la console."
        }
        # scripts/deploy_vm/ -> remontée de 2 niveaux
        $candidate = (Resolve-Path -LiteralPath (Join-Path $ScriptDir "..\..") -ErrorAction Stop).Path
        Write-Verbose "RepoRoot auto-détecté : $candidate"
    }

    # Vérification stricte des marqueurs.
    $missing = @()
    foreach ($m in $Markers) {
        $full = Join-Path $candidate $m
        if (-not (Test-Path -LiteralPath $full)) {
            $missing += $m
        }
    }

    if ($missing.Count -gt 0) {
        $msg  = "Racine repo invalide : $candidate"
        $msg += "`nMarqueurs manquants :"
        foreach ($m in $missing) { $msg += "`n  - $m" }
        throw $msg
    }

    return $candidate
}

# Bandeau d'info immédiat (utile en dry-run pour relire les valeurs effectives).
Write-Host "=========================================================="
Write-Host "AI_ASSISTANT_CORE — deploy_to_vm.ps1"
Write-Host "=========================================================="
Write-Host "Mode        : $(if ($Apply) { 'APPLY (push réel)' } else { 'DRY-RUN' })"
Write-Host "VmTarget    : $VmTarget"
Write-Host "VmRoot      : $VmRoot"
Write-Host "RunStamp    : $RunStamp"

try {
    $ResolvedRepoRoot = Resolve-RepoRoot -Explicit $RepoRoot -ScriptDir $ScriptDir -Markers $RepoMarkers
}
catch {
    Write-Host ""
    Write-Host "ERREUR de résolution RepoRoot :" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 2
}

Write-Host "RepoRoot    : $ResolvedRepoRoot"
Write-Host "----------------------------------------------------------"

# ----------------------------------------------------------
# Chemins effectifs (résolus après RepoRoot validé)
# ----------------------------------------------------------

$WhitelistPath    = Join-Path $ResolvedRepoRoot $WhitelistRelPath
$VerifyScriptPath = Join-Path $ResolvedRepoRoot $VerifyScriptRelPath
$LogDir           = Join-Path $ResolvedRepoRoot $LogDirRelPath
$LogPath          = Join-Path $LogDir "$RunStamp.log"

# Fin de la section 1.
# Les sections suivantes (pré-flight, résolution whitelist, snapshot, push, verdict)
# s'enchaînent à partir d'ici.

# Section 2 — Pré-flight checks
# ----------------------------------------------------------
# Aucun side effect côté VM / repo host.
# Peut ajouter la host key SSH dans known_hosts côté host
# si StrictHostKeyChecking=accept-new rencontre la VM pour la première fois.
# Toute défaillance => exit 2.
# ==========================================================

function Test-Command {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    return [bool]$cmd
}

function Fail-Preflight {
    param([string]$Reason)
    Write-Host ""
    Write-Host "ERREUR pré-flight :" -ForegroundColor Red
    Write-Host $Reason -ForegroundColor Red
    exit 2
}

Write-Host "Pré-flight checks..."

# ----------------------------------------------------------
# 2.1 — Outils host requis
# ----------------------------------------------------------

if (-not (Test-Command "ssh.exe")) {
    Fail-Preflight "ssh.exe introuvable dans le PATH. Installer OpenSSH Client (Windows: Settings -> Optional features)."
}
if (-not (Test-Command "scp.exe")) {
    Fail-Preflight "scp.exe introuvable dans le PATH. Installer OpenSSH Client (Windows: Settings -> Optional features)."
}
Write-Host "  [OK] ssh.exe et scp.exe disponibles"

# ----------------------------------------------------------
# 2.2 — Whitelist présente et lisible
# ----------------------------------------------------------

if (-not (Test-Path -LiteralPath $WhitelistPath)) {
    Fail-Preflight "whitelist.txt introuvable : $WhitelistPath"
}

# Lecture immédiate pour vérifier qu'au moins une entrée non-commentée existe.
# Volontaire : un fichier whitelist vide ou 100% commenté est suspect, on refuse.
try {
    $whitelistRawLines = Get-Content -LiteralPath $WhitelistPath -ErrorAction Stop
}
catch {
    Fail-Preflight "Impossible de lire whitelist.txt : $($_.Exception.Message)"
}

$nonEmptyEntries = $whitelistRawLines |
    Where-Object { $_ -notmatch '^\s*#' -and $_ -notmatch '^\s*$' }

if ($nonEmptyEntries.Count -eq 0) {
    Fail-Preflight "whitelist.txt ne contient aucune entrée active (que des commentaires ou des lignes vides)."
}
Write-Host "  [OK] whitelist.txt lue : $($nonEmptyEntries.Count) entrée(s) active(s)"

# Stockage pour la section 3 (résolution).
$Script:WhitelistEntries = $nonEmptyEntries

# ----------------------------------------------------------
# 2.2bis — Script de vérification VM présent côté host
# ----------------------------------------------------------
# verify_manifest.sh sera poussé côté VM lors du push (section 4).
# Il doit donc exister dans le repo host avant tout -Apply.
# Vérifié aussi en dry-run pour signaler l'incohérence le plus tôt possible.

if (-not (Test-Path -LiteralPath $VerifyScriptPath)) {
    Fail-Preflight "verify_manifest.sh introuvable : $VerifyScriptPath"
}
Write-Host "  [OK] verify_manifest.sh présent côté host"

# ----------------------------------------------------------
# 2.3 — Connectivité SSH + sha256sum côté VM
# ----------------------------------------------------------

# Un seul appel SSH combiné. Options strictes :
#   -o BatchMode=yes              => refuse toute prompt (mot de passe, fingerprint inconnu)
#   -o ConnectTimeout=10          => timeout court
#   -o StrictHostKeyChecking=accept-new
#                                 => accepte un nouveau host key UNE fois (premier run),
#                                    refuse si l'empreinte change ensuite.
#
# IMPORTANT — side effect HOST :
#   accept-new peut écrire la host key VM dans %USERPROFILE%\.ssh\known_hosts
#   au tout premier contact SSH depuis ce compte Windows.
#   Aucun side effect côté VM. Aucune écriture côté repo host.
#   Si ce comportement est indésirable, remplacer par StrictHostKeyChecking=yes
#   (refus systématique tant que la host key n'est pas déjà connue).
#
# La commande distante imprime deux marqueurs distincts qu'on parse ensuite,
# pour distinguer "SSH OK mais sha256sum manquant" de "SSH KO".

$remoteProbe = @"
echo SSH_OK
if command -v sha256sum >/dev/null 2>&1; then echo SHA256_OK; else echo SHA256_MISSING; fi
if command -v tar >/dev/null 2>&1; then echo TAR_OK; else echo TAR_MISSING; fi
"@

# On collapse en une seule ligne shell-safe (séparateur ;).
$remoteProbeOneLine = ($remoteProbe -split "`r?`n" | Where-Object { $_ -ne "" }) -join "; "

$sshArgs = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
    $VmTarget,
    $remoteProbeOneLine
)

$probeOutput = ""
$probeExit = 0
try {
    # & avec splatting : pas d'invocation cmd.exe, pas de quoting hasardeux.
    $probeOutput = & ssh.exe @sshArgs 2>&1
    $probeExit = $LASTEXITCODE
}
catch {
    Fail-Preflight "Échec d'invocation SSH : $($_.Exception.Message)"
}

if ($probeExit -ne 0) {
    $msg  = "SSH non fonctionnel vers $VmTarget (exit=$probeExit)."
    $msg += "`nSortie brute :"
    $msg += "`n$probeOutput"
    $msg += "`n"
    $msg += "`nVérifier :"
    $msg += "`n  - VM joignable (ping $($VmTarget.Split('@')[1]))"
    $msg += "`n  - clé SSH publique installée côté VM (~/.ssh/authorized_keys)"
    $msg += "`n  - aucun mot de passe requis (BatchMode=yes interdit toute prompt)"
    Fail-Preflight $msg
}

# Parsing des marqueurs.
$probeText = ($probeOutput | Out-String)

if ($probeText -notmatch "SSH_OK") {
    Fail-Preflight "SSH a répondu mais le marqueur SSH_OK est absent. Sortie : $probeText"
}

if ($probeText -match "SHA256_MISSING") {
    Fail-Preflight "sha256sum absent côté VM. Installer coreutils : sudo apt install coreutils"
}

if ($probeText -notmatch "SHA256_OK") {
    Fail-Preflight "Réponse VM inattendue (ni SHA256_OK ni SHA256_MISSING). Sortie : $probeText"
}

Write-Host "  [OK] SSH $VmTarget joignable, sha256sum présent côté VM"
Write-Host "Pré-flight OK."
Write-Host "----------------------------------------------------------"

# Fin de la section 2.

# ==========================================================
# Section 3 — Résolution whitelist + denylist + manifest host
# ----------------------------------------------------------
# Aucun side effect VM. Pas de SSH. Pas d'écriture dans le repo.
# ==========================================================

# -- 3.1 Deux tiers de denylist (indépendants de whitelist.txt) --------------
#
# Tier 1 — Sécurité : un match provoque exit 2 immédiat, même depuis un glob.
# Tier 2 — Artefacts : un match exclut le fichier sans bloquer le script ;
#           un résumé est affiché (count + exemples), aucun fichier n'entre
#           dans le manifest.

$SecurityDenyPatterns = @(
    '(^|/)\.git(/|$)',      # répertoire .git
    '(^|/)\.env($|\.)',     # .env, .env.*, .env.local, etc.
    'secret',               # tout chemin contenant "secret"
    'token',                # tout chemin contenant "token"
    '\.(pem|key|p12|pfx)$' # certificats et clés privées
)

$ArtifactSkipPatterns = @(
    '__pycache__',          # caches Python compilés
    '\.pyc$',               # bytecode Python
    '\.pytest_cache',       # cache pytest
    '\.mypy_cache',         # cache mypy
    '\.ruff_cache',         # cache ruff
    '(^|/)\.venv(/|$)',     # virtualenv caché
    '(^|/)venv(/|$)',       # virtualenv standard
    'node_modules',         # dépendances JS
    'agentic_desk',         # bureau agentique — jamais en VM
    'review_loop',          # boucle de review — jamais en VM
    '\.(zip|tar|gz)$'       # archives
)

function Test-SecurityDenied {
    param([string]$RelPath)
    foreach ($pat in $SecurityDenyPatterns) {
        if ($RelPath -match $pat) { return $true }
    }
    return $false
}

function Test-ArtifactSkip {
    param([string]$RelPath)
    foreach ($pat in $ArtifactSkipPatterns) {
        if ($RelPath -match $pat) { return $true }
    }
    return $false
}

# -- 3.2 Résolution des entrées whitelist ------------------------------------

function Resolve-WhitelistFiles {
    param([string]$Entry, [string]$Root)

    $Entry = $Entry.Trim()

    if ($Entry -match '^(.*)/\*\*$') {
        # Glob récursif : chemin/** -> tous les fichiers sous ce répertoire
        $dirSuffix = $Matches[1] -replace '/', '\'
        $dirPath = Join-Path $Root $dirSuffix
        if (-not (Test-Path -LiteralPath $dirPath -PathType Container)) {
            Write-Verbose "  [SKIP] Répertoire introuvable pour '$Entry' : $dirPath"
            return @()
        }
        return @(Get-ChildItem -LiteralPath $dirPath -Recurse -File |
                 Select-Object -ExpandProperty FullName)
    }
    else {
        # Fichier unique (chemin littéral, pas de glob)
        $fileSuffix = $Entry -replace '/', '\'
        $fullPath = Join-Path $Root $fileSuffix
        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            return @($fullPath)
        }
        Write-Verbose "  [SKIP] Fichier introuvable pour '$Entry' : $fullPath"
        return @()
    }
}

Write-Host "Résolution whitelist..."

$ResolvedFiles   = [System.Collections.Generic.List[string]]::new()
$SecurityBlocked = [System.Collections.Generic.List[string]]::new()
$ArtifactSkipped = [System.Collections.Generic.List[string]]::new()

foreach ($entry in $Script:WhitelistEntries) {
    $candidates = Resolve-WhitelistFiles -Entry $entry -Root $ResolvedRepoRoot
    foreach ($absPath in $candidates) {
        $relPath = $absPath.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        if (Test-SecurityDenied -RelPath $relPath) {
            $SecurityBlocked.Add($relPath) | Out-Null
        }
        elseif (Test-ArtifactSkip -RelPath $relPath) {
            $ArtifactSkipped.Add($relPath) | Out-Null
        }
        elseif (-not $ResolvedFiles.Contains($absPath)) {
            $ResolvedFiles.Add($absPath) | Out-Null
        }
    }
}

# Tier 1 — Sécurité : blocage immédiat
if ($SecurityBlocked.Count -gt 0) {
    Write-Host ""
    Write-Host "ERREUR SECURITE — Fichier(s) bloqués par la denylist sécurité :" -ForegroundColor Red
    foreach ($d in $SecurityBlocked) { Write-Host "  - $d" -ForegroundColor Red }
    Write-Host "Corrigez whitelist.txt avant de relancer." -ForegroundColor Red
    exit 2
}

# Tier 2 — Artefacts : résumé non bloquant
if ($ArtifactSkipped.Count -gt 0) {
    Write-Host "  Artefacts exclus (non copiés, hors manifest) : $($ArtifactSkipped.Count) fichier(s)"
    $examples = $ArtifactSkipped | Select-Object -First 5
    foreach ($ex in $examples) { Write-Host "    ex: $ex" }
    if ($ArtifactSkipped.Count -gt 5) {
        Write-Host "    ... et $($ArtifactSkipped.Count - 5) autre(s)"
    }
    Write-Host "  [OK] Aucun artefact exclu n'entre dans le manifest."
}

if ($ResolvedFiles.Count -eq 0) {
    Write-Host "ERREUR — Aucun fichier résolu depuis whitelist.txt. Vérifiez les chemins." -ForegroundColor Red
    exit 2
}

Write-Host "  [OK] $($ResolvedFiles.Count) fichier(s) à synchroniser :"
foreach ($f in $ResolvedFiles) {
    $rel = $f.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
    Write-Host "        $rel"
}
Write-Host "----------------------------------------------------------"

# -- 3.3 Génération du manifest SHA-256 (côté host) --------------------------

Write-Host "Génération du manifest SHA-256..."

$ManifestLines = [System.Collections.Generic.List[string]]::new()
foreach ($absPath in $ResolvedFiles) {
    $relPath = $absPath.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
    $hash = (Get-FileHash -LiteralPath $absPath -Algorithm SHA256).Hash.ToLower()
    $ManifestLines.Add("$hash  $relPath") | Out-Null
}

$ManifestHostPath = [System.IO.Path]::GetTempFileName()
try {
    $ManifestLines | Set-Content -LiteralPath $ManifestHostPath -Encoding UTF8
    Write-Host "  [OK] Manifest : $($ManifestLines.Count) entrée(s)"
}
catch {
    Write-Host "ERREUR génération manifest : $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}

# Chemin du manifest côté VM (temp hors VmRoot pour ne pas polluer le repo VM).
$ManifestVmPath = "/tmp/aicore_manifest_$RunStamp.txt"

Write-Host "----------------------------------------------------------"

# Fin de la section 3.

# ==========================================================
# Section 4 — Dry-run display / Apply (snapshot VM + scp + vérification)
# ----------------------------------------------------------
# Dry-run : affiche uniquement ce qui SERAIT fait, aucun side effect.
# Apply   : snapshot VM, scp fichiers + manifest + verify script, vérification.
# ==========================================================

# Arguments SSH/SCP de base (réutilisés dans toute la section 4).
$SshBase = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=30",
    "-o", "StrictHostKeyChecking=accept-new"
)
$ScpBase = @(
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=30",
    "-o", "StrictHostKeyChecking=accept-new"
)

function Invoke-SshCmd {
    param([string]$Command, [switch]$ThrowOnError)
    $out = & ssh.exe @SshBase $VmTarget $Command 2>&1
    $exitCode = $LASTEXITCODE
    if ($ThrowOnError -and $exitCode -ne 0) {
        throw "SSH a échoué (exit=$exitCode) pour : $Command`nSortie : $($out | Out-String)"
    }
    return [pscustomobject]@{ Output = $out; ExitCode = $exitCode }
}

function Invoke-ScpCmd {
    param([string]$Source, [string]$Destination, [switch]$ThrowOnError)
    $out = & scp.exe @ScpBase $Source $Destination 2>&1
    $exitCode = $LASTEXITCODE
    if ($ThrowOnError -and $exitCode -ne 0) {
        throw "SCP a échoué (exit=$exitCode) : $Source -> $Destination`nSortie : $($out | Out-String)"
    }
    return [pscustomobject]@{ Output = $out; ExitCode = $exitCode }
}

$VerifyScriptVmPath = "$VmRoot/scripts/deploy_vm/verify_manifest.sh"

if (-not $Apply) {
    # ----------------------------------------------------------
    # MODE DRY-RUN — Affichage uniquement, aucune écriture VM
    # ----------------------------------------------------------

    Write-Host "DRY-RUN — Ce qui serait exécuté avec -Apply :"
    Write-Host ""
    Write-Host "  [VM] Snapshot : tar -czf $VmSnapshotsDir/$RunStamp.tar.gz -C $VmRoot ."

    $DirsPreview = [System.Collections.Generic.HashSet[string]]::new()
    $DirsPreview.Add("$VmRoot/scripts/deploy_vm") | Out-Null
    foreach ($f in $ResolvedFiles) {
        $rel = $f.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        $parts = $rel -split '/'
        if ($parts.Count -gt 1) {
            $dir = ($parts[0..($parts.Count - 2)]) -join '/'
            $DirsPreview.Add("$VmRoot/$dir") | Out-Null
        }
    }
    Write-Host "  [VM] mkdir -p : $($DirsPreview.Count) répertoire(s)"
    Write-Host "  [SCP] $($ResolvedFiles.Count) fichier(s) → $VmTarget`:$VmRoot"
    Write-Host "  [SCP] verify_manifest.sh → $VmTarget`:$VerifyScriptVmPath"
    Write-Host "  [SCP] manifest → $VmTarget`:$ManifestVmPath"
    Write-Host "  [VM] bash $VerifyScriptVmPath $ManifestVmPath $VmRoot"
    Write-Host ""
    Write-Host "Relancez avec -Apply pour exécuter."
    Write-Host "=========================================================="

    Remove-Item -LiteralPath $ManifestHostPath -ErrorAction SilentlyContinue
    exit 0
}

# ----------------------------------------------------------
# MODE APPLY — Push réel
# ----------------------------------------------------------

Write-Host "APPLY — Début du push vers $VmTarget..."
Write-Host ""

$ApplyError = $null

try {
    # -- 4.1 Snapshot pré-push côté VM ---------------------------------------
    Write-Host "  [4.1] Snapshot VM : $VmSnapshotsDir/$RunStamp.tar.gz"
    $snapshotCmd = "mkdir -p $VmSnapshotsDir && tar -czf $VmSnapshotsDir/$RunStamp.tar.gz -C $VmRoot . 2>&1 && echo SNAPSHOT_OK"
    $snapshotResult = Invoke-SshCmd -Command $snapshotCmd
    if (($snapshotResult.Output | Out-String) -notmatch "SNAPSHOT_OK") {
        throw "Snapshot VM échoué. Sortie : $($snapshotResult.Output | Out-String)"
    }
    Write-Host "  [OK] Snapshot créé"

    # -- 4.2 Création des répertoires cibles côté VM -------------------------
    Write-Host "  [4.2] Création des répertoires VM..."

    $DirsToCreate = [System.Collections.Generic.HashSet[string]]::new()
    $DirsToCreate.Add("$VmRoot/scripts/deploy_vm") | Out-Null
    foreach ($f in $ResolvedFiles) {
        $rel = $f.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        $parts = $rel -split '/'
        if ($parts.Count -gt 1) {
            $dir = ($parts[0..($parts.Count - 2)]) -join '/'
            $DirsToCreate.Add("$VmRoot/$dir") | Out-Null
        }
    }
    $mkdirTargets = ($DirsToCreate | ForEach-Object { "'$_'" }) -join ' '
    Invoke-SshCmd -Command "mkdir -p $mkdirTargets" -ThrowOnError | Out-Null
    Write-Host "  [OK] $($DirsToCreate.Count) répertoire(s) prêt(s)"

    # -- 4.3 SCP des fichiers whitelistés ------------------------------------
    Write-Host "  [4.3] Copie des $($ResolvedFiles.Count) fichier(s)..."
    foreach ($absPath in $ResolvedFiles) {
        $rel = $absPath.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        Invoke-ScpCmd -Source $absPath -Destination "$VmTarget`:$VmRoot/$rel" -ThrowOnError | Out-Null
        Write-Host "    [SCP] $rel"
    }
    Write-Host "  [OK] Fichiers copiés"

    # -- 4.4 SCP de verify_manifest.sh (cas spécial hors whitelist) ----------
    Write-Host "  [4.4] Copie de verify_manifest.sh..."
    Invoke-ScpCmd -Source $VerifyScriptPath -Destination "$VmTarget`:$VerifyScriptVmPath" -ThrowOnError | Out-Null
    Invoke-SshCmd -Command "chmod +x $VerifyScriptVmPath" -ThrowOnError | Out-Null
    Write-Host "  [OK] verify_manifest.sh poussé et rendu exécutable"

    # -- 4.5 SCP du manifest -------------------------------------------------
    Write-Host "  [4.5] Copie du manifest SHA-256..."
    Invoke-ScpCmd -Source $ManifestHostPath -Destination "$VmTarget`:$ManifestVmPath" -ThrowOnError | Out-Null
    Write-Host "  [OK] Manifest poussé"

    # -- 4.6 Vérification manifest côté VM -----------------------------------
    Write-Host "  [4.6] Vérification manifest côté VM..."
    $verifyResult = Invoke-SshCmd -Command "bash $VerifyScriptVmPath $ManifestVmPath $VmRoot 2>&1"
    $verifyText = ($verifyResult.Output | Out-String).Trim()

    Write-Host "  Sortie verify_manifest.sh :"
    foreach ($line in ($verifyText -split "`n")) { Write-Host "    $line" }

    if ($verifyResult.ExitCode -ne 0) {
        throw "verify_manifest.sh a retourné exit=$($verifyResult.ExitCode). Voir sortie ci-dessus."
    }
    Write-Host "  [OK] MATCH"

    # -- 4.7 Nettoyage manifest temp côté VM ---------------------------------
    Invoke-SshCmd -Command "rm -f $ManifestVmPath" | Out-Null
}
catch {
    $ApplyError = $_.Exception.Message
}
finally {
    Remove-Item -LiteralPath $ManifestHostPath -ErrorAction SilentlyContinue
}

Write-Host "----------------------------------------------------------"

# Fin de la section 4.

# ==========================================================
# Section 5 — Verdict final + log host
# ==========================================================

Write-Host ""
Write-Host "=========================================================="
Write-Host "VERDICT"
Write-Host "=========================================================="

$PushSuccess = $null -eq $ApplyError

if ($PushSuccess) {
    Write-Host "  SUCCES — Push VM terminé." -ForegroundColor Green
    Write-Host "  $($ResolvedFiles.Count) fichier(s) synchronisés vers $VmTarget`:$VmRoot"
    Write-Host "  Snapshot : $VmSnapshotsDir/$RunStamp.tar.gz"
    Write-Host ""
    Write-Host "  Prochaines étapes manuelles si nécessaire :"
    Write-Host "    ssh $VmTarget sudo systemctl restart aicore-backend"
    Write-Host "    ssh $VmTarget curl -s http://127.0.0.1:8000/health"
}
else {
    Write-Host "  ECHEC" -ForegroundColor Red
    Write-Host "  $ApplyError" -ForegroundColor Red
    Write-Host "  Snapshot disponible si la section 4.1 a réussi."
    Write-Host "  Aucun service redémarré automatiquement."
}

# Log host
try {
    if (-not (Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
    $LogContent = @(
        "RunStamp : $RunStamp",
        "VmTarget : $VmTarget",
        "VmRoot   : $VmRoot",
        "RepoRoot : $ResolvedRepoRoot",
        "Mode     : $(if ($Apply) { 'APPLY' } else { 'DRY-RUN' })",
        "Files    : $($ResolvedFiles.Count)",
        "Success  : $PushSuccess",
        "Error    : $(if ($ApplyError) { $ApplyError } else { 'none' })",
        "",
        "--- Fichiers synchronisés ---"
    )
    foreach ($f in $ResolvedFiles) {
        $LogContent += $f.Substring($ResolvedRepoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
    }
    $LogContent += ""
    $LogContent += "--- Manifest SHA-256 ---"
    $LogContent += $ManifestLines

    $LogContent | Set-Content -LiteralPath $LogPath -Encoding UTF8
    Write-Host "  Log : $LogPath"
}
catch {
    Write-Host "  Avertissement : log non écrit — $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "=========================================================="

if (-not $PushSuccess) { exit 1 }
exit 0

# Fin de la section 5.