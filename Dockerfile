# Общий образ для сервисов userbot и panel — команда запуска задаётся в docker-compose.yml.
FROM python:3.11-slim

WORKDIR /app

# Внешние зависимости отдельным слоем — пересборка при правке кода не тянет pip заново.
# Версии — как в pyproject.toml [project.dependencies].
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "telethon>=1.36" \
    "aiogram>=3.13" \
    "aiosqlite>=0.20" \
    "aiohttp>=3.9" \
    "tzdata>=2024.1"

# Локальные пакеты (matcher, store, userbot, worker, panel) НЕ ставятся как
# дистрибутив (pip install .) — рабочая директория /app уже на sys.path,
# `python -m userbot.main`/`python -m panel.main` находит их через cwd без установки.
COPY . .

# Данные (SQLite, *.session) живут в volume ./data:/app/data — см. docker-compose.yml.
CMD ["python", "-m", "userbot.main"]
