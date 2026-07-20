#!/bin/sh
set -e

echo "[start] DATABASE_URL host: $(echo "$DATABASE_URL" | sed -E 's|.*@([^/:]+).*|\1|')"
echo "[start] PRIMARY_PROVIDER=${PRIMARY_PROVIDER:-<не задан>}"

echo "[start] применяю миграции…"
alembic upgrade head
echo "[start] миграции применены"

echo "[start] запускаю бота"
# exec — чтобы python стал PID 1 и корректно получал SIGTERM от Railway
exec python -m src.main_bot
