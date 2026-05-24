#!/bin/bash
# PVC Backup Verification Script
# Checks integrity of the latest backup for each PVC type
#
# Usage: bash verify-backup.sh

set -euo pipefail

BACKUP_DIR="/home/eugene/backups/pvcs"
ZSTD="/usr/bin/zstd"
TAR="/usr/bin/tar"

PVC_NAMES=("minecraft" "grafana" "prometheus" "alertmanager" "homepage")

errors=0

echo "=== Backup Verification ==="
echo "Directory: $BACKUP_DIR"
echo ""

if [ ! -d "$BACKUP_DIR" ]; then
    echo "ERROR: Backup directory does not exist: $BACKUP_DIR"
    exit 1
fi

for name in "${PVC_NAMES[@]}"; do
    # Bug 8 fix: use find instead of ls to avoid glob expansion errors under set -e
    latest_file=$(find "$BACKUP_DIR" -maxdepth 1 -name "${name}-*.tar.zst" -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2- || true)

    if [ -z "$latest_file" ]; then
        echo "[$name] SKIP: No backups found"
        continue
    fi

    filename=$(basename "$latest_file")
    file_size=$(du -h "$latest_file" | cut -f1)
    file_date=$(stat -c '%y' "$latest_file" | cut -d'.' -f1)

    # Test zstd archive integrity
    if ! $ZSTD -t "$latest_file" 2>/dev/null; then
        echo "[$name] CORRUPT (zstd): $filename ($file_size, $file_date)"
        errors=$((errors + 1))
        continue
    fi

    # Test tar archive can be listed (catches truncated streams)
    file_count=$($ZSTD -d -c "$latest_file" 2>/dev/null | $TAR -t 2>/dev/null | wc -l)
    if [ "$file_count" -lt 1 ]; then
        echo "[$name] CORRUPT (tar empty): $filename ($file_size, $file_date)"
        errors=$((errors + 1))
        continue
    fi

    echo "[$name] OK: $filename ($file_size, $file_date, $file_count files)"
done

echo ""
total_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
# Bug 8 fix: use find instead of ls for counting
total_count=$(find "$BACKUP_DIR" -maxdepth 1 -name "*.tar.zst" -print 2>/dev/null | wc -l)
echo "Summary: $total_count backup(s), total size: $total_size"

if [ "$errors" -gt 0 ]; then
    echo "WARNING: $errors corrupt backup(s) detected!"
    exit 1
fi

echo "All backups verified successfully."
exit 0
