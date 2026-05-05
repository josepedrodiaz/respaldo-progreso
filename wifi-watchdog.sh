#!/bin/bash
# Watchdog simplificado de red (2 niveles).
# LV1 (60s):  nmcli disconnect/connect — barato, a veces alcanza
# LV2 (180s): reboot via systemctl (con cooldown 1h, anti-loop)
#
# Health check real: curl HTTPS a Google API. Si falla 1 chequeo no es
# motivo de pánico; el contador de "down_since" decide la escalada.
# Esto detecta zombies de NetworkManager (NM "connected" pero rclone no llega)
# que un ping ICMP simple no detectaría.

set -u

CHECK_INTERVAL=30
HEALTH_URL="https://www.googleapis.com/discovery/v1/apis"
ROUTER="192.168.100.1"

# Umbrales (segundos sin red)
T_LV1=60      # 1 min: nmcli disconnect/connect
T_LV2=180     # 3 min: reboot directo (con cooldown)

# Si LV2 fue intentado pero seguimos vivos despues de 5 min → reset escalada
LV2_FAILED_RESET_AFTER=300

# Cooldown de reboot: no rebootear si el ultimo fue hace menos de 1h
REBOOT_COOLDOWN=3600
REBOOT_STAMP="/var/lib/wifi-watchdog/last-reboot"

down_since=0
last_action=""
last_action_time=0

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

run_with_timeout() {
  local secs=$1 label=$2; shift 2
  log "  $label: ejecutando (timeout ${secs}s)"
  timeout --kill-after=10 "${secs}s" "$@" 2>&1 | head -5 | while read l; do log "    $l"; done
  local rc=${PIPESTATUS[0]}
  if [ $rc -eq 124 ]; then
    log "  $label: TIMEOUT despues de ${secs}s, se mato el comando"
  elif [ $rc -ne 0 ]; then
    log "  $label: salio con codigo $rc"
  else
    log "  $label: OK"
  fi
  return $rc
}

get_iface() {
  ls /sys/class/net/ 2>/dev/null | grep -E '^wlx|^wlan|^wlp' | head -1
}

check_health() {
  curl -sS -o /dev/null -m 5 -w '' "$HEALTH_URL" 2>/dev/null
}

snapshot_diagnostico() {
  log "=== DIAGNOSTICO AL FALLAR ==="
  local iface=$(get_iface)
  log "iface: $iface"
  ping -c 1 -W 3 "$ROUTER" >/dev/null 2>&1 && \
    log "  ping router($ROUTER): OK" || \
    log "  ping router($ROUTER): FALLA"
  ping -c 1 -W 3 8.8.8.8 >/dev/null 2>&1 && \
    log "  ping 8.8.8.8: OK" || \
    log "  ping 8.8.8.8: FALLA"
  curl -sS -o /dev/null -m 5 -w 'http_code=%{http_code} time=%{time_total}s' \
    "$HEALTH_URL" 2>&1 | while read l; do log "  curl googleapis: $l"; done
  log "  IP: $(ip -o -4 addr show "$iface" 2>/dev/null | awk '{print $4}' | head -1)"
  log "  gateway: $(ip route | awk '/^default/ {print $3, $5; exit}')"
  log "  nmcli state: $(nmcli -t -f STATE,CONNECTION device | grep -E '^conectado:|^connected:' | head -1)"
  log "  nmcli wifi: $(nmcli -t -f IN-USE,SSID,SIGNAL,CHAN device wifi 2>/dev/null | grep '^\*' | head -1)"
  log "  ult kernel usb/wifi (10):"
  dmesg -T 2>/dev/null | grep -iE "usb 1-1|usb 2-1|wlxd|r8712u|wifi|wireless" | tail -10 | while read l; do log "    $l"; done
  log "=== FIN DIAGNOSTICO ==="
}

action_lv1() {
  local iface=$(get_iface)
  log "LV1 cycle nmcli iface=$iface"
  run_with_timeout 20 "nmcli disconnect" nmcli device disconnect "$iface"
  sleep 5
  run_with_timeout 20 "nmcli connect" nmcli device connect "$iface"
  sleep 15
}

can_reboot() {
  if [ ! -f "$REBOOT_STAMP" ]; then
    return 0
  fi
  local last=$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)
  local now=$(date +%s)
  local age=$((now - last))
  if [ $age -ge $REBOOT_COOLDOWN ]; then
    return 0
  fi
  log "REBOOT BLOQUEADO: ultimo reboot hace ${age}s (cooldown ${REBOOT_COOLDOWN}s)"
  return 1
}

action_lv2() {
  log "LV2 considerando reboot..."
  if can_reboot; then
    log "REBOOT ahora (3+ min sin red, cooldown OK)"
    echo $(date +%s) | sudo -n /usr/bin/tee "$REBOOT_STAMP" > /dev/null
    sync
    sleep 2
    log "ejecutando: sudo -n /bin/systemctl reboot"
    run_with_timeout 20 "reboot" sudo -n /bin/systemctl reboot
    log "ERROR: reboot NO se ejecuto"
  fi
}

log "watchdog arrancado (lv1=${T_LV1}s, lv2=${T_LV2}s, cooldown=${REBOOT_COOLDOWN}s, health=$HEALTH_URL)"

while true; do
  if check_health; then
    if [ $down_since -gt 0 ]; then
      now=$(date +%s)
      log "red OK despues de $((now-down_since))s caida"
    fi
    down_since=0
    last_action=""
    last_action_time=0
  else
    now=$(date +%s)
    if [ $down_since -eq 0 ]; then
      down_since=$now
      log "HEALTH falla, empezando contador"
      snapshot_diagnostico
    fi
    elapsed=$((now - down_since))

    # Reset si LV2 fue intentado pero el sistema sigue vivo (reboot fallo)
    if [ "$last_action" = "lv2" ] && [ $((now - last_action_time)) -ge $LV2_FAILED_RESET_AFTER ]; then
      log "LV2 fallo (sistema sigue vivo despues de ${LV2_FAILED_RESET_AFTER}s), reseteando escalada"
      last_action=""
      down_since=$now
      elapsed=0
    fi

    log "sin red hace ${elapsed}s (ult accion: ${last_action:-ninguna})"

    if [ $elapsed -ge $T_LV2 ] && [ "$last_action" != "lv2" ]; then
      action_lv2
      last_action="lv2"
      last_action_time=$now
    elif [ $elapsed -ge $T_LV1 ] && [ "$last_action" = "" ]; then
      action_lv1
      last_action="lv1"
      last_action_time=$now
    fi
  fi
  sleep $CHECK_INTERVAL
done
