#!/bin/bash
# Watchdog escalado de red.
# Niveles: nmcli -> NetworkManager -> reload driver -> reboot (con cooldown)
# Loguea TODO via journal asi se ve desde la pagina de estado.

set -u

CHECK_INTERVAL=30
TARGET="8.8.8.8"

# Umbrales (segundos sin red)
T_LV1=60      # 1 min: nmcli disconnect/connect
T_LV2=180     # 3 min: restart NetworkManager
T_LV3=300     # 5 min: rmmod/modprobe del driver
T_LV4=900     # 15 min: reboot (con cooldown)

# Si el reboot fue intentado pero el sistema sigue vivo despues de este tiempo,
# resetear last_action y volver a probar todos los niveles desde LV1.
LV4_FAILED_RESET_AFTER=300  # 5 min

# Cooldown de reboot: no rebootear si el ultimo fue hace menos de 1h
REBOOT_COOLDOWN=3600
REBOOT_STAMP="/var/lib/wifi-watchdog/last-reboot"

down_since=0
last_action=""
last_action_time=0

log() {
  echo "[$(date '+%H:%M:%S')] $*"
}

get_iface() {
  ls /sys/class/net/ 2>/dev/null | grep -E '^wlx|^wlan|^wlp' | head -1
}

get_driver() {
  local iface=$1
  basename $(readlink /sys/class/net/$iface/device/driver 2>/dev/null) 2>/dev/null
}

action_lv1() {
  local iface=$(get_iface)
  log "LV1 cycle nmcli iface=$iface"
  nmcli device disconnect "$iface" 2>&1 | head -3 | while read l; do log "  nmcli: $l"; done
  sleep 5
  nmcli device connect "$iface" 2>&1 | head -3 | while read l; do log "  nmcli: $l"; done
  sleep 15
}

action_lv2() {
  log "LV2 restart NetworkManager"
  sudo -n /bin/systemctl restart NetworkManager 2>&1 | head -3 | while read l; do log "  sudo: $l"; done
  sleep 30
}

action_lv3() {
  local iface=$(get_iface)
  local driver=$(get_driver $iface)
  log "LV3 reload driver $driver iface=$iface"
  if [ -n "$driver" ]; then
    sudo -n /sbin/modprobe -r "$driver" 2>&1 | head -3 | while read l; do log "  modprobe -r: $l"; done
    sleep 3
    sudo -n /sbin/modprobe "$driver" 2>&1 | head -3 | while read l; do log "  modprobe: $l"; done
    sleep 30
  else
    log "LV3 SKIP no driver detectado"
  fi
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

action_lv4() {
  log "LV4 considerando reboot..."
  if can_reboot; then
    log "REBOOT ahora (15+ min sin red, cooldown OK)"
    echo $(date +%s) | sudo -n /usr/bin/tee "$REBOOT_STAMP" > /dev/null
    sync
    sleep 2
    log "ejecutando: sudo -n /bin/systemctl reboot"
    sudo -n /bin/systemctl reboot 2>&1 | while read l; do log "  reboot: $l"; done
    # si llegamos aca el reboot fallo
    log "ERROR: reboot NO se ejecuto, sudo lo rechazo o systemctl fallo"
  fi
}

log "watchdog arrancado (lv1=${T_LV1}s, lv2=${T_LV2}s, lv3=${T_LV3}s, lv4=${T_LV4}s, cooldown=${REBOOT_COOLDOWN}s, lv4_reset_after=${LV4_FAILED_RESET_AFTER}s)"

while true; do
  if ping -c 1 -W 5 "$TARGET" >/dev/null 2>&1; then
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
      log "PING falla, empezando contador"
    fi
    elapsed=$((now - down_since))

    # Reset si LV4 fue intentado pero el sistema sigue vivo (reboot fallo)
    if [ "$last_action" = "lv4" ] && [ $((now - last_action_time)) -ge $LV4_FAILED_RESET_AFTER ]; then
      log "LV4 fallo (sistema sigue vivo despues de ${LV4_FAILED_RESET_AFTER}s), reseteando escalada"
      last_action=""
      down_since=$now
      elapsed=0
    fi

    log "sin red hace ${elapsed}s (ult accion: ${last_action:-ninguna})"

    if [ $elapsed -ge $T_LV4 ] && [ "$last_action" != "lv4" ]; then
      action_lv4
      last_action="lv4"
      last_action_time=$now
    elif [ $elapsed -ge $T_LV3 ] && [ "$last_action" != "lv3" ] && [ "$last_action" != "lv4" ]; then
      action_lv3
      last_action="lv3"
      last_action_time=$now
    elif [ $elapsed -ge $T_LV2 ] && [ "$last_action" != "lv2" ] && [ "$last_action" != "lv3" ] && [ "$last_action" != "lv4" ]; then
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
