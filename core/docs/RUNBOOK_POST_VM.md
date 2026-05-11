# RUNBOOK POST-VM — AI_ASSISTANT_CORE

## Objectif
Donner la procédure normale d’exploitation du runtime post-VM sans mélanger host Windows et VM Ubuntu.

## Lecture simple
### Dans la VM Hyper-V `AICORE-VM`
- backend AI_ASSISTANT_CORE
- `aicore-backend.service`
- SearXNG
- Docker `searxng` (`restart: unless-stopped`)

### Sur le host Windows
- Ollama
- ComfyUI
- `portproxy` Ollama
- règles firewall bornées à la VM
- OpenWebUI (optionnel) comme UI opérateur côté host, **hors runtime canonique**

## Invariants runtime (référence canonique)

Ce bloc est la **source unique** pour les ports, binds et URL du runtime réel.
Les autres documents (`docs/README.md` s'il existe, `docs/ARCHITECTURE.md`, racine `README.md`) s'y réfèrent et ne les redéfinissent pas.

| Composant              | Localisation | Bind / URL                      | Rôle                                      |
|------------------------|--------------|---------------------------------|-------------------------------------------|
| backend AI_ASSISTANT_CORE | VM        | `192.168.77.10:8000` (override-bind.conf) | API FastAPI canonique + `/v1/*`  |
| SearXNG                | VM           | `127.0.0.1:8081`                | recherche web utilisée par le pipeline    |
| Ollama                 | Host Windows | `192.168.77.1:12001` (vu VM)    | LLM local                                 |
| ComfyUI                | Host Windows | `192.168.77.1:8188` (vu VM)     | génération visuelle                       |
| `portproxy` Ollama     | Host Windows | `192.168.77.1:12001 -> 127.0.0.1:12000` | pont VM → Ollama                  |

### Statut des dépendances
- Le `portproxy` Ollama est une **dépendance runtime canonique à court terme** mais **transitoire dans sa forme**. Ce n'est pas un invariant final de topologie.
- Le chemin direct VM → `192.168.77.1:12000` **n'est pas** un chemin runtime validé et ne doit pas être documenté comme tel.

## Checks normaux dans la VM
```bash
sudo systemctl status aicore-backend --no-pager
docker ps --filter name=searxng
curl -s http://192.168.77.10:8000/health
curl -s http://192.168.77.10:8000/health/runtime
```

## Relance dans la VM
```bash
sudo systemctl restart aicore-backend
docker restart searxng
```

## Logs dans la VM
```bash
journalctl -u aicore-backend -n 50 --no-pager
docker logs --tail 50 searxng
```

## Checks de connectivité depuis la VM
```bash
curl -v --max-time 5 http://192.168.77.1:12001/api/tags
curl -v --max-time 5 http://192.168.77.1:8188/system_stats
curl -s "http://127.0.0.1:8081/search?q=test&format=json" | head -c 300 ; echo
```

## Checks normaux sur le host Windows
PowerShell admin :
```powershell
docker ps
netsh interface portproxy show v4tov4
Invoke-WebRequest http://192.168.77.1:12001/api/tags
Get-NetFirewallRule | Where-Object DisplayName -like "AICORE *" | Format-Table DisplayName, Enabled, Profile, Direction, Action
```

## Test post-reboot VM
Après reboot de la VM :
```bash
sudo systemctl status aicore-backend --no-pager
docker ps --filter name=searxng
docker ps --filter name=searxng
curl -s http://192.168.77.10:8000/health
curl -s http://192.168.77.10:8000/health/runtime
```

## ComfyUI host runtime — procédure validée F.3b

ComfyUI tourne sur le **host Windows**, pas dans la VM. Le backend VM le contacte via l'IP bridge Hyper-V.

### Configuration actuelle

| Variable | Valeur |
|---|---|
| `COMFYUI_URL` | `http://192.168.77.1:8188` (défini dans `.env`) |
| `COMFYUI_AUTO_START` | `false` — relance manuelle uniquement |
| `COMFYUI_BAT_PATH` | vide — ne pas activer l'auto-start sans le renseigner |

### Lancement manuel

```powershell
# Sur le host Windows — depuis un terminal standard (pas admin requis)
& "E:\ComfyUI_windows_portable\run_nvidia_gpu.bat"
```

ComfyUI écoute sur `192.168.77.1:8188` uniquement (pas `localhost:8188`) — comportement attendu, configuré par `--listen 192.168.77.1`.

### Garde RAM avant lancement

Vérifier que la RAM libre est suffisante avant de lancer une génération SDXL :

```powershell
(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB
# Viser 8 GB libres minimum ; ne pas lancer si < 6 GB
```

### Healthcheck host

```powershell
# Vérifier que ComfyUI est up (réponse JSON avec version et GPU)
Invoke-WebRequest http://192.168.77.1:8188/system_stats -UseBasicParsing
```

### Healthcheck backend (depuis host)

```powershell
# Vérifier que le backend VM voit ComfyUI comme ready
Invoke-RestMethod -Uri "http://192.168.77.10:8000/health/runtime" -Method GET
# Attendu : comfyui.ready = true, reason = "http 200"
```

### Smoke image_generation minimal

```powershell
$body = [System.Text.Encoding]::UTF8.GetBytes('{"message":"Genere une image simple, style studio, une variante.","has_image":false}')
Invoke-RestMethod -Uri "http://192.168.77.10:8000/execute" -Method POST -ContentType "application/json; charset=utf-8" -Body $body -TimeoutSec 300
# Attendu : execution_strategy = visual_pipeline, comfyui_status = success, artifact_filename présent
```

### Points fragiles connus

- ComfyUI crash probable OOM si RAM libre < 6 GB au moment de la génération.
- `localhost:8188` ne répond pas si ComfyUI est bind sur `192.168.77.1` uniquement.
- `COMFYUI_BAT_PATH` vide = auto-start non disponible ; relance manuelle requise.
- Ne pas modifier `.env` sans plan explicite.

---

## Ce qui est canonique
- backend sous `systemd` dans la VM
- SearXNG sous `systemd` + Docker dans la VM
- VM comme runtime principal
- host comme fournisseur de capacité pour Ollama et ComfyUI
- bridge Ollama réel via `12001` depuis la VM

## Ce qui est encore transitoire
- `portproxy` Ollama dans sa forme actuelle
- normalisation future du bridge Ollama sans changer la topologie host + VM

## Décision OpenWebUI

OpenWebUI, **si utilisée**, est une UI opérateur optionnelle côté host.
Elle **n'est pas** une composante du runtime principal canonique : le backend AI_ASSISTANT_CORE et sa façade `/v1/*` OpenAI-compatible sont complets sans elle. OpenWebUI consomme cette façade en HTTP comme n'importe quel client.

## Section Blender

### Vérifier l'installation Blender dans la VM
```bash
blender --version
```

### Vérifier le backend
```bash
curl -s http://192.168.77.10:8000/health
curl -s http://192.168.77.10:8000/health/runtime
# /health/runtime peut rester partial si ComfyUI est indisponible — non bloquant pour Blender
```

### Tester le pipeline Blender (PowerShell host)
```powershell
$payload = @{
  message = "crée une scène Blender avec un cube métallique"
  has_image = $false
} | ConvertTo-Json -Compress

$r = Invoke-RestMethod `
  -Uri "http://192.168.77.10:8000/execute" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body $payload `
  -TimeoutSec 300

$r | Select-Object task_type, execution_strategy, selected_tool, blender_status, artifact_type, artifact_path, blender_render_path, blender_error
```

Résultat attendu :
- `execution_strategy` = `blender_pipeline`
- `blender_status` = `success`
- `artifact_type` = `blend`
- `artifact_path` = `outputs/blender/<uuid>/scene.blend`
- `blender_render_path` = `outputs/blender/<uuid>/preview.png`

### Vérifier les fichiers produits (dans la VM)
```bash
ls outputs/blender/<uuid>/
# scene.py       ← script bpy généré
# scene.blend    ← artefact canonique
# preview.png    ← rendu best-effort, subprocess séparé
```

### Récupérer le preview.png sur le Bureau Windows
```powershell
scp "bnhr@192.168.77.10:/home/bnhr/aicore/projects/core/<CHEMIN_PREVIEW>" "$env:USERPROFILE\Desktop\preview.png"
```

## À ne plus documenter comme vrai état
- le chemin direct VM → `192.168.77.1:12000`
- un runtime principal entièrement host-side
- OpenWebUI comme composant nécessaire du runtime principal canonique
