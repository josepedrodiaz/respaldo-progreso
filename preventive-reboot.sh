#!/bin/bash
# Reboot preventivo diario para limpiar firmware del dongle WiFi (r8712u),
# memoria leaks, USB hub state, y cualquier corruption acumulada por
# operacion 24/7 del backup.
#
# Solo reinicia si el ultimo reboot fue hace +20h, asi nunca entra en loop
# con el watchdog (que tiene cooldown de 1h pero respetamos un margen mas
# grande para que un reboot del watchdog y este preventivo no se pisen).

set -u

REBOOT_STAMP="/var/lib/wifi-watchdog/last-reboot"
MIN_AGE_SECONDS=$((20 * 3600))  # 20 horas

log() {
  echo "[preventive-reboot $(date '+%H:%M:%S')] $*"
}

now=$(date +%s)
if [ -f "$REBOOT_STAMP" ]; then
  last=$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)
  age=$((now - last))
  if [ $age -lt $MIN_AGE_SECONDS ]; then
    log "SKIP: ultimo reboot hace ${age}s (umbral ${MIN_AGE_SECONDS}s)"
    exit 0
  fi
  log "OK: ultimo reboot hace ${age}s, procediendo"
else
  log "OK: nunca rebooteamos, procediendo"
fi

log "registrando timestamp y rebooteando"
echo "$now" | sudo -n /usr/bin/tee "$REBOOT_STAMP" > /dev/null
sync
sleep 2
sudo -n /bin/systemctl reboot
