FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 app
COPY --chown=app:app app ./app
COPY --chown=app:app main.py ./
USER app
EXPOSE 9001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9001/healthz', timeout=3)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9001", "--workers", "2"]
