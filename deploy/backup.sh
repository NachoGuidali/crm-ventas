#!/bin/sh
# Backup diario de la base y los archivos. Cron sugerido (crontab -e):
#   15 4 * * * /opt/crm-ventas/deploy/backup.sh >> /var/log/crm-backup.log 2>&1
set -eu
cd "$(dirname "$0")/.."
DESTINO=${DESTINO:-/opt/backups/crm-ventas}
DIAS=${DIAS:-14}
FECHA=$(date +%Y%m%d-%H%M)
mkdir -p "$DESTINO"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DESTINO/db-$FECHA.dump"
docker compose exec -T web tar czf - -C /app media > "$DESTINO/media-$FECHA.tgz"
find "$DESTINO" -type f -mtime +"$DIAS" -delete
echo "$(date) backup OK: $DESTINO/db-$FECHA.dump"
