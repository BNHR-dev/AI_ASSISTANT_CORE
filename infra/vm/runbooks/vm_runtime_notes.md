# VM runtime notes — AI_ASSISTANT_CORE

## Runtime canonique dans la VM

### Backend
- service : `aicore-backend.service`
- bind : `127.0.0.1:8000`

### SearXNG
- service : `aicore-searxng.service`
- bind : `127.0.0.1:8081`

## Vérifications utiles dans la VM

```bash
sudo systemctl status aicore-backend --no-pager
sudo systemctl status aicore-searxng --no-pager
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/health/runtime