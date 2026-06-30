# Dockerfile PADRÃO (raiz) = build da API. Contexto = repo. É o que o EasyPanel
# procura por default (-f code/Dockerfile). Worker usa esta MESMA imagem, só troca o
# Command — por isso NÃO há HEALTHCHECK aqui (o worker não sobe HTTP).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/app ./app
COPY api/alembic ./alembic
COPY api/alembic.ini .
COPY api/scripts ./scripts
COPY api/docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh
EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
