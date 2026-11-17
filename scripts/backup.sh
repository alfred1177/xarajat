#!/bin/sh
# Резервная копия базы: ./scripts/backup.sh  (кладёт копию в backups/)
set -e
mkdir -p backups
cp data/zakupki.db "backups/zakupki_$(date +%Y%m%d_%H%M%S).db"
ls -1t backups | tail -n +31 | sed 's|^|backups/|' | xargs -r rm --
echo "OK"
