# RUNBOOK POST-VM — AI_ASSISTANT_CORE

## Objectif
Donner la procédure normale d’exploitation du runtime post-VM sans mélanger host Windows et VM Ubuntu.

## Lecture simple
### Dans la VM Hyper-V `AICORE-VM`
- backend AI_ASSISTANT_CORE
- `aicore-backend.service`
- SearXNG
- `aicore-searxng.service`

### Sur le host Windows
- Ollama
- ComfyUI
- `portproxy` Ollama
- règles firewall bornées à la VM
- éventuellement OpenWebUI comme UI opérateur

## Invariants canoniques actuels
- backend VM : `127.0.0.1:8000`
- SearXNG VM : `127.0.0.1:8081`
- Ollama host vu depuis la VM : `192.168.77.1:12001`
- ComfyUI host vu depuis la VM : `192.168.77.1:8188`
- `portproxy` Ollama attendu : `192.168.77.1:12001 -> 127.0.0.1:12000`

## Checks normaux dans la VM
```bash
sudo systemctl status aicore-backend --no-pager
sudo systemctl status aicore-searxng --no-pager
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/health/runtime
```

## Relance dans la VM
```bash
sudo systemctl restart aicore-backend
sudo systemctl restart aicore-searxng
```

## Logs dans la VM
```bash
journalctl -u aicore-backend -n 50 --no-pager
journalctl -u aicore-searxng -n 50 --no-pager
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
sudo systemctl status aicore-searxng --no-pager
docker ps --filter name=searxng
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/health/runtime
```

## Ce qui est canonique
- backend sous `systemd` dans la VM
- SearXNG sous `systemd` + Docker dans la VM
- VM comme runtime principal
- host comme fournisseur de capacité pour Ollama et ComfyUI
- bridge Ollama réel via `12001` depuis la VM

## Ce qui est encore transitoire
- `portproxy` Ollama dans sa forme actuelle
- statut final d’OpenWebUI côté host
- normalisation future du bridge Ollama sans changer la topologie host + VM

## À ne plus documenter comme vrai état
- le chemin direct VM → `192.168.77.1:12000`
- un runtime principal entièrement host-side
- OpenWebUI comme composant nécessaire du runtime principal canonique
