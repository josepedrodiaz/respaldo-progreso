#!/bin/bash
# Watchdog de WiFi: si no hay internet por 2 minutos, bounces el dongle.
# Loguea a journal (se ve con: journalctl -u wifi-watchdog -f)

set -u

FAIL_THRESHOLD=4        # 4 fallos consecutivos x 30s = 2 minutos sin red
CHECK_INTERVAL=30
TARGET="8.8.8.8"
IFACE_GLOB="wlx*"

fails=0

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

get_iface() {
  ls /sys/class/net/ 2>/dev/null | grep -E '^wlx|^wlan|^wlp' | head -1
}

while true; do
  if ping -c 1 -W 5 "$TARGET" >/dev/null 2>&1; then
    if [ $fails -gt 0 ]; then
      log "red recuperada despues de $fails fallos"
    fi
    fails=0
  else
    fails=$((fails+1))
    log "fallo de red $fails/$FAIL_THRESHOLD"
    if [ $fails -ge $FAIL_THRESHOLD ]; then
      iface=$(get_iface)
      if [ -n "$iface" ]; then
        log "reseteando WiFi $iface..."
        nmcli device disconnect "$iface" 2>&1 | head -1
        sleep 5
        nmcli device connect "$iface" 2>&1 | head -1
        sleep 20
      else
        log "ERROR: no encontre interfaz WiFi"
      fi
      fails=0
    fi
  fi
  sleep $CHECK_INTERVAL
done
