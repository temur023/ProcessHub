FROM python:3.13-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp SAL_USE_VCLPLUGIN=svp
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress python3-uno fonts-liberation fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir fastapi==0.141.1 uvicorn==0.52.4 \
    && useradd --uid 10002 --no-create-home preview
COPY service.py convert.py ./
USER preview
EXPOSE 8000
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "4"]
