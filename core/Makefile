# Makefile — entrée une-commande de la démo AAC (P5). Détails : docs/DOCKER.md.
#   make demo       lance la stack en CPU (marche partout)
#   make demo-gpu   lance la stack avec GPU NVIDIA (démo pleine, rapide)
.DEFAULT_GOAL := help

COMPOSE            := docker compose -f docker-compose.app.yml
COMPOSE_GPU        := docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml
COMPOSE_SECURE     := docker compose -f docker-compose.app.yml -f docker-compose.sandbox.yml
COMPOSE_GPU_SECURE := docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml -f docker-compose.sandbox.yml

.PHONY: help setup fetch-models pull-llms deps doctor demo demo-gpu demo-secure demo-gpu-secure down logs

help:
	@echo "AAC — démo Docker cross-platform (Linux / macOS / Windows+WSL2)"
	@echo
	@echo "  make demo         lance la stack en CPU (marche partout, lent pour l'image)"
	@echo "  make demo-gpu     lance la stack avec GPU NVIDIA (démo pleine)"
	@echo "  make demo-secure  comme demo + sandbox bwrap EXIGÉ (overlay durci, mode require)"
	@echo "  make demo-gpu-secure  demo-gpu + sandbox bwrap EXIGÉ"
	@echo "  make fetch-models télécharge les modèles image RealVisXL + 4x-UltraSharp (idempotent)"
	@echo "  make pull-llms    télécharge les modèles Ollama (qwen3 / coder / vl, idempotent)"
	@echo "  make deps         fetch-models + pull-llms (toutes les dépendances de modèles)"
	@echo "  make doctor       preflight : vérifie Docker / Ollama / modèles / Blender / SearXNG"
	@echo "  make down         arrête la stack"
	@echo "  make logs         suit les logs"
	@echo
	@echo "  Backend prêt sur http://127.0.0.1:8000 (console + API OpenAI-compatible)."

# Prérequis : config SearXNG + modèles. Idempotent (ne refait rien d'inutile).
setup:
	@[ -f searxng/settings.yml ] || cp searxng/settings.example.yml searxng/settings.yml
	@$(MAKE) fetch-models

fetch-models:
	@bash scripts/fetch-models.sh

# Modèles LLM Ollama (natif ou conteneur démo, auto-détecté). Idempotent.
pull-llms:
	@bash scripts/fetch-ollama-models.sh

# Toutes les dépendances de modèles en une fois (image + LLM).
deps: fetch-models pull-llms

# Preflight : détecte ce qui manque, n'installe rien.
doctor:
	@bash scripts/check-deps.sh

demo: setup
	$(COMPOSE) up --build

demo-gpu: setup
	$(COMPOSE_GPU) up --build

# Variantes durcies : bwrap exigé dans le conteneur (échec franc si indisponible).
demo-secure: setup
	$(COMPOSE_SECURE) up --build

demo-gpu-secure: setup
	$(COMPOSE_GPU_SECURE) up --build

down:
	@$(COMPOSE_GPU) down

logs:
	@$(COMPOSE) logs -f
