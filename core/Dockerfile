# AAC backend — image cross-platform (P1a).
#
# App pur-Python (routeur + planner + executor + console FastAPI). Les dépendances
# sont pur-Python (voir requirements.txt) -> build simple, pas de toolchain native.
#
# La couche Blender + bwrap (rendu) est ajoutée en P1b (voir core/docs/DOCKER.md).
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# --- P1b : Blender 5.1.1 (rendu) + bubblewrap (sandbox C1c) ---
# Le pipeline génère du bpy ciblant l'API Blender 5.1.x -> on épingle 5.1.1.
# Le wrapper sandbox C1c est présent et câblé (app/clients/blender_sandbox.py) :
# avec AAC_BLENDER_SANDBOX=auto (ci-dessous), bwrap confine l'exécution Blender s'il
# est utilisable dans le conteneur, sinon dégradation gracieuse. Durcissement du
# bwrap imbriqué (capacités conteneur) -> voir docs/DOCKER.md.
# SHA256 officiel (https://download.blender.org/release/Blender5.1/blender-5.1.1.sha256) :
# l'archive est vérifiée avant extraction — un miroir compromis ou un download
# tronqué font échouer le build au lieu d'entrer dans l'image.
ENV BLENDER_TARBALL_SHA256=6f9fff89fef154ef7974d1a1c4b916ab4bc1f5618bcb48d5befee1bd0a7c7f2a

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils bubblewrap \
        libx11-6 libxext6 libxcb1 libxi6 libxxf86vm1 libxfixes3 libxrender1 \
        libxkbcommon0 libsm6 libgl1 libegl1 libgomp1; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL -o /tmp/blender.tar.xz \
        "https://download.blender.org/release/Blender5.1/blender-5.1.1-linux-x64.tar.xz"; \
    echo "${BLENDER_TARBALL_SHA256}  /tmp/blender.tar.xz" | sha256sum -c -; \
    mkdir -p /opt/blender; \
    tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1; \
    rm -f /tmp/blender.tar.xz; \
    ln -s /opt/blender/blender /usr/local/bin/blender; \
    blender --version

ENV BLENDER_EXE=/usr/local/bin/blender \
    BLENDER_OUTPUT_DIR=/outputs/blender \
    AAC_BLENDER_SANDBOX=auto

WORKDIR /app

# 1) Dépendances d'abord (cache de couche stable tant que requirements.txt ne change pas).
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

# 2) Code applicatif. Le contexte de build est core/ (voir .dockerignore).
#    L'app importe des modules racine (openai_compat, console) et le paquet `app`
#    depuis le répertoire de travail -> on lance uvicorn depuis /app.
COPY . /app

# 3) Utilisateur non-root. UID/GID 1000 = utilisateur par défaut des hôtes Linux,
#    donc les fichiers écrits sur le bind mount ./outputs naissent déjà au bon
#    propriétaire (run.sh reprend possession des restes root de ComfyUI au démarrage).
#    /app reste à root en lecture seule : le code n'a aucune raison de s'auto-modifier.
RUN groupadd --gid 1000 aac \
    && useradd --uid 1000 --gid aac --create-home --shell /usr/sbin/nologin aac \
    && mkdir -p /outputs/blender /outputs/comfyui \
    && chown -R aac:aac /outputs

ENV HOME=/home/aac
USER aac

EXPOSE 8000

# Healthcheck conteneur sur /health (pas de curl dans l'image slim -> urllib).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
