# Общий образ для сервисов userbot и panel — команда запуска задаётся в docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

# Слой зависимостей отдельно от остального кода — пересборка при правке кода не тянет pip заново.
COPY pyproject.toml ./
COPY matcher ./matcher
COPY store ./store
RUN pip install --no-cache-dir .

COPY . .

# Данные (SQLite, *.session) живут в volume ./data:/app/data — см. docker-compose.yml.
CMD ["python", "-m", "userbot.main"]
