#!/bin/bash
# Resiliente: espera disco, espera red, retry interno, nunca se rinde.
# Solo sale con exit 0 cuando rclone reporta sync completo.

set -uo pipefail

SOURCE="/media/pedro/DiazSantaM"
DEST='gdrive-bisvidita:Respaldo DiazSantaM 2026-04-14'
LOG="/home/pedro/respaldo-fotos-familia.log"
NTFY_TOPIC="lamanana-staging-pedro-x7k2"
DEVICE="/dev/disk/by-label/DiazSantaM"
SUDO_PASS="jac6992"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

wait_for_disk() {
  local tries=0
  while true; do
    if mountpoint -q "$SOURCE" && [ -d "$SOURCE" ] && [ "$(ls -A "$SOURCE" 2>/dev/null | head -1)" ]; then
      log "disco montado OK en $SOURCE"
      return 0
    fi
    if [ -e "$DEVICE" ]; then
      log "disco presente pero no montado, montando..."
      mkdir -p "$SOURCE" 2>/dev/null || echo "$SUDO_PASS" | sudo -S mkdir -p "$SOURCE"
      echo "$SUDO_PASS" | sudo -S mount -t exfat "$DEVICE" "$SOURCE" 2>&1 | tee -a "$LOG"
      sleep 3
    else
      log "dispositivo $DEVICE no aparece, esperando 30s..."
      sleep 30
    fi
    tries=$((tries+1))
    if [ $tries -gt 60 ]; then
      log "ERROR: 30 minutos sin disco, rindiendome (systemd va a reintentar)"
      return 1
    fi
  done
}

wait_for_network() {
  local tries=0
  while true; do
    if ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && \
       ping -c 1 -W 3 www.googleapis.com >/dev/null 2>&1; then
      log "red OK"
      return 0
    fi
    log "sin red, esperando 10s..."
    sleep 10
    tries=$((tries+1))
    if [ $tries -gt 60 ]; then
      log "ERROR: 10 minutos sin red, rindiendome"
      return 1
    fi
  done
}

run_rclone() {
  rclone copy "$SOURCE" "$DEST" \
    --exclude '$RECYCLE.BIN/**' \
    --exclude '.fseventsd/**' \
    --exclude '.Spotlight-V100/**' \
    --exclude 'System Volume Information/**' \
    --exclude '.Trashes/**' \
    --exclude 'Thumbs.db' \
    --exclude '._*' \
    --exclude '.DS_Store' \
    --drive-chunk-size 64M \
    --drive-stop-on-upload-limit \
    --transfers 4 \
    --checkers 32 \
    --log-file "$LOG" \
    --log-level INFO \
    --stats 5m \
    --stats-one-line \
    --retries 10 \
    --retries-sleep 30s \
    --low-level-retries 20 \
    --bwlimit "08:00,400K 23:30,off"
  return $?
}

# --- Main loop interno (hasta 5 retries antes de devolverle la falla a systemd) ---
MAX_INTERNAL_RETRIES=5
retry=0
while [ $retry -lt $MAX_INTERNAL_RETRIES ]; do
  log "=== intento $((retry+1))/$MAX_INTERNAL_RETRIES ==="
  wait_for_disk || exit 1
  wait_for_network || exit 1

  if run_rclone; then
    log "rclone termino OK"
    curl -s -H "Title: Respaldo familia LISTO" \
         -d "Subida completa del disco DiazSantaM a Google Drive" \
         "https://ntfy.sh/$NTFY_TOPIC" || true
    exit 0
  fi

  rc=$?
  log "rclone salio con codigo $rc, esperando 60s antes de reintentar..."
  sleep 60
  retry=$((retry+1))
done

log "agotados los reintentos internos, systemd va a relanzar el servicio"
exit 1
