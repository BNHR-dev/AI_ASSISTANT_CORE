# core/scripts

Dev-side helpers that live next to the backend code. The *operational* scripts
(model fetchers, preflight, installers) moved to the repo root: see
[`../../scripts/linux/`](../../scripts/linux) and
[`../../scripts/windows/`](../../scripts/windows), driven by the root
[`Makefile`](../../Makefile) (`make deps`, `make doctor`) and the launchers
(`run.sh` / `run.bat`).

What is actually here:

- `init_local_config.py` — creates a local `core/.env` and `searxng/settings.yml` from the
  committed examples, generating secrets when missing.
- `router_corpus.jsonl` — the labeled corpus (prompt → task) used to train the semantic
  router fallback.
- `train_router_classifier.py` — **offline** training of the routing classifier (dev tool,
  never shipped in the image): encodes the corpus via the Ollama embedding model
  (`bge-m3`), trains a scikit-learn logistic regression with cross-validation, exports the
  weights as JSON — the runtime (`app/engine/router_embeddings.py`) only reads them back
  in pure Python.

Full dependency guide: [`../../docs/DEPENDENCIES.md`](../../docs/DEPENDENCIES.md).
