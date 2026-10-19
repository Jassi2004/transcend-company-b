FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=4300 DATABASE_PATH=/data/company-b.sqlite
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home app && mkdir /data && chown app:app /data
COPY --chown=app:app app.py ./
COPY --chown=app:app static ./static
USER app
EXPOSE 4300
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/api/health',timeout=4)"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-4300}"]
