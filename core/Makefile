# Makefile — entrée une-commande de la démo AAC (P5). Détails : docs/DOCKER.md.
#   make demo       lance la stack en CPU (marche partout)
#   make demo-gpu   lance la stack avec GPU NVIDIA (démo pleine, rapide)
.DEFAULT_GOAL := help

COMPOSE     := docker compose -f docker-compose.app.yml
COMPOSE_GPU := docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml

.PHONY: help setup fetch-models demo demo-gpu down logs

help:
	@echo "AAC — démo Docker cross-platform (Linux / macOS / Windows+WSL2)"
	@echo
	@echo "  make demo         lance la stack en CPU (marche partout, lent pour l'image)"
	@echo "  make demo-gpu     lance la stack avec GPU NVIDIA (démo pleine)"
	@echo "  make fetch-models télécharge RealVisXL + 4x-UltraSharp (HuggingFace, idempotent)"
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

demo: setup
	$(COMPOSE) up --build

demo-gpu: setup
	$(COMPOSE_GPU) up --build

down:
	@$(COMPOSE_GPU) down

logs:
	@$(COMPOSE) logs -f
