FROM python:3.12-slim

# Не root: контейнер работает от непривилегированного пользователя
RUN useradd --create-home --uid 1000 studyapp
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY static/ static/

# data/ и config.json монтируются томами (см. docker-compose.yml)
RUN mkdir -p /app/data && chown -R studyapp:studyapp /app
USER studyapp

EXPOSE 8742
CMD ["waitress-serve", "--listen=0.0.0.0:8742", "--threads=8", "server:app"]
