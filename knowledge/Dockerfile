FROM python:3.13-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
# Stable's antiword 0.37-17 has known heap writes. Only the fixed converter is
# allowed from forky; all other packages retain the base distribution policy.
RUN printf 'deb http://deb.debian.org/debian forky main\n' > /etc/apt/sources.list.d/knowledge-converter.list \
    && printf 'Package: *\nPin: release n=forky\nPin-Priority: -1\n\nPackage: antiword\nPin: version 0.37-18\nPin-Priority: 1001\n' > /etc/apt/preferences.d/knowledge-converter \
    && apt-get update \
    && apt-get install -y --no-install-recommends antiword=0.37-18 \
    && rm -f /etc/apt/sources.list.d/knowledge-converter.list /etc/apt/preferences.d/knowledge-converter \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt && useradd --uid 10001 --create-home knowledge
COPY knowledge_service ./knowledge_service
USER knowledge
EXPOSE 8000
CMD ["uvicorn", "knowledge_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
