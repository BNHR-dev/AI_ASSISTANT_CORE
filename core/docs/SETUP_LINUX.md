# SETUP_LINUX.md — Mise en place du dev sur Fedora

> Runbook de la migration vers un poste de dev **Fedora KDE** (dual-boot, Windows
> conservé). Ce document est **non destructif** : il décrit des étapes ; il n'en
> exécute aucune automatiquement. Les actions disque / boot / Secure Boot / VM
> restent **manuelles et validées au cas par cas**.

## Périmètre de cette préparation

Cette phase « Linux compatibility prep » ne touche **ni au disque, ni au boot,
ni à Ollama, ni à la VM**. Elle prépare seulement le repo pour qu'il tourne
aussi bien sous Linux que sous Windows.

Artefacts ajoutés :
- `scripts/setup-fedora.sh` — installe le toolchain dev sur Fedora (idempotent).
- `core/env.linux.example` — template d'env adapté à la topologie single-host.
- `core/docker-compose.linux.yml` — stack avec labels SELinux `:z`.
- Lanceurs ComfyUI rendus cross-platform (`cmd /c` sous Windows, `bash` sous Linux).

---

## Phase -1 — Inventaire & sauvegarde vérifiable (AVANT toute modif disque)

> À faire **côté Windows**, tant que tu y es encore. Rien ne se supprime ici.

### 1. État Git (confirmer que GitHub est à jour)
```powershell
cd E:\AI_ASSISTANT_CORE\core
git status --short
git rev-parse --short HEAD
git rev-parse --short origin/main
git stash list
```
- `HEAD` doit correspondre à `origin/main` (sinon committer + pousser après validation).

### 2. Inventaire des éléments NON versionnés (à sauvegarder à part)
- `core/.env`, `core/.env.vm_runtime_snapshot` (secrets/config)
- `local_artifacts/`, `outputs/blender/` (si à conserver)
- VM `AICORE-VM.vhdx` (brique runtime)
- Coffre Obsidian (cockpit)
- Liste des modèles Ollama : `ollama list` (à noter ; les blobs se re-`pull`ent)

### 3. Sauvegarde vérifiable (vers D: + idéalement un support externe)
- Copier les éléments ci-dessus, puis **vérifier qu'ils s'ouvrent** (pas juste « copiés »).
- VM (VHDX) — procédure spécifique :
  ```powershell
  Stop-VM -Name <nom>          # arrêt propre, jamais forcé
  Get-VM                       # State doit être Off (ni Running, ni Saved)
  # copier le .vhdx, puis :
  Mount-VHD -Path <copie> -ReadOnly ; Dismount-VHD -Path <copie>
  ```

### 4. Filet de sécurité Windows (à confirmer avant Phase 1)
- [ ] Windows boote actuellement
- [ ] BitLocker actif ou non (`manage-bde -status`) — si actif, **clé de récup sauvegardée**
- [ ] Fast Startup désactivé
- [ ] Capture d'écran de la Gestion des disques **avant** modif
- [ ] Support de récupération Windows disponible
- [ ] Espace + partition exacte à réduire confirmés

**Tant que cette checklist n'est pas verte : pas de partitionnement.**

---

## Installation sur Fedora (une fois l'OS installé)

### 1. Toolchain dev
```bash
cd scripts
chmod +x setup-fedora.sh
./setup-fedora.sh            # interactif ; --dry-run pour prévisualiser
```
Le script installe : RPM Fusion, git/zsh/outils CLI modernes, Docker CE,
`nvidia-container-toolkit`, et crée le venv Python. **Il n'installe pas** le
driver NVIDIA par défaut (voir section dédiée).

### 2. Environnement projet
```bash
cp core/env.linux.example core/.env   # puis ajuster les valeurs (secrets, ports)
```
> Ne jamais committer le `.env` réel.

### 3. Stack conteneurs
```bash
# configurer le runtime GPU une fois :
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

docker compose -f core/docker-compose.linux.yml up
```

---

## Authentification de l'API (C2)

L'API s'authentifie par **bearer token** : les requêtes portent une en-tête
`Authorization: Bearer <token>`. C'est ce qu'envoient déjà les clients
compatibles OpenAI (Open-WebUI). Le token n'est jamais logué ; la comparaison
est en temps constant.

### Variables d'environnement (dans `core/.env`)

| Variable | Rôle |
|---|---|
| `AAC_API_TOKEN` | Le token partagé. ≥ 16 caractères. Généré par ex. avec `python -c "import secrets;print(secrets.token_hex(24))"`. |
| `AAC_API_AUTH_MODE` | `off` \| `required`. Non posé ⇒ `presence` (défaut). |
| `AAC_CONSOLE_ENABLED` | Monte la console `/console/*` (UI navigateur, **non authentifiée**). Défaut activé. |

### Postures

- **`presence`** (défaut, non posé) — auth appliquée **si** `AAC_API_TOKEN` est
  posé ; sinon API ouverte + avertissement au démarrage. Confort dev loopback.
- **`off`** — auth jamais appliquée (même avec un token posé).
- **`required`** — auth appliquée **et** l'app **refuse de démarrer** si
  `AAC_API_TOKEN` est absent ou invalide (fail-closed).

### Routes

- **Ouvert** : `GET /health` (sonde de vie).
- **Protégé** (quand l'auth est appliquée) : `/route`, `/execute`,
  `/debug/canonical`, `/health/runtime`, et tout `/v1/*` → `401` +
  `WWW-Authenticate: Bearer` sans token valide.
- **Console** `/console/*` : non protégée par le token (UI navigateur). Montée
  seulement si `AAC_CONSOLE_ENABLED` est activé.

### Profil local vs profil exposé

- **Profil local** (défaut) : bind `127.0.0.1`, console activée, docs activés.
  `AAC_API_TOKEN` optionnel.
- **Profil exposé / public** (avant tout bind hors loopback) :
  ```bash
  AAC_API_AUTH_MODE=required
  AAC_API_TOKEN=<token fort>
  AAC_CONSOLE_ENABLED=0
  ```
  En mode `required`, `/docs`, `/redoc` et `/openapi.json` sont automatiquement
  désactivés.

> **Règle** : ne jamais binder l'API hors `127.0.0.1` sans
> `AAC_API_AUTH_MODE=required` + un token fort. La console n'étant pas
> authentifiée, ne pas l'exposer : la garder en loopback ou la désactiver
> (`AAC_CONSOLE_ENABLED=0`) et n'exposer que l'API (par ex. via un reverse
> proxy qui ne publie que `/execute` et `/v1/*`).

---

## NVIDIA / Secure Boot (section dédiée)

> ⚠️ Étape sensible : **rien d'irréversible n'est figé**. Procédure adaptée à
> l'état réel constaté.

1. Installer le driver via RPM Fusion (réversible) :
   `./scripts/setup-fedora.sh --with-nvidia-driver`
2. Vérifier Secure Boot : `mokutil --sb-state`
   - **enabled** → enrôlement **MOK** au reboot (écran bleu MOK Manager, mot de passe à saisir).
   - **disabled** → pas de MOK.
3. Vérifs post-install, dans l'ordre :
   ```bash
   mokutil --sb-state
   nvidia-smi                 # le GPU doit apparaître
   ```
   puis test GPU réel : inférence Ollama + rendu Blender (OptiX).

Tant que `nvidia-smi` ne répond pas, **ne pas enchaîner**.

---

## Vérification de bout en bout
- `nvtop` montre la RTX 3060 active.
- `docker compose -f core/docker-compose.linux.yml up` → Ollama répond ; backend `/health` OK.
- Un rendu Blender produit `scene.blend` + `preview.png`.
- Windows boote toujours normalement (dual-boot intact).
