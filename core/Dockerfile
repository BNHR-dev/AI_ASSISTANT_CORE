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

# --- P1b : Blender 5.1.1 (rendu) + bubblewrap (sandbox, prêt pour C1c) ---
# Le pipeline génère du bpy ciblant l'API Blender 5.1.x -> on épingle 5.1.1.
# bubblewrap est installé mais l'enveloppe applicative (C1c) n'est pas sur cette
# branche : en démo, le conteneur fait l'isolation (voir docs/DOCKER.md).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils bubblewrap \
        libx11-6 libxext6 libxcb1 libxi6 libxxf86vm1 libxfixes3 libxrender1 \
        libxkbcommon0 libsm6 libgl1 libegl1 libgomp1; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL -o /tmp/blender.tar.xz \
        "https://download.blender.org/release/Blender5.1/blender-5.1.1-linux-x64.tar.xz"; \
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

EXPOSE 8000

# Healthcheck conteneur sur /health (pas de curl dans l'image slim -> urllib).
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
