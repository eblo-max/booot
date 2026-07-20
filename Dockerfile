FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

CMD ["sh", "-c", "alembic upgrade head && python -m src.main_bot"]
