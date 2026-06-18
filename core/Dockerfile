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
