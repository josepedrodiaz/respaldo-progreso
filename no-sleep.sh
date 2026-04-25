#!/bin/bash
# Setup unico: deshabilita suspend del sistema, autosuspend USB del WiFi,
# y power_save del WiFi. Idempotente, lo podes correr cuantas veces quieras.

set -u
SUDO_PASS="jac6992"
sudo() { echo "$SUDO_PASS" | command sudo -S "$@"; }

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# 1. Deshabilitar suspend a nivel systemd (es un server, no debe dormirse)
log "deshabilitando targets de suspend/sleep/hibernate..."
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>&1 | tail -2

# 2. Login manager de Linux Mint (cinnamon-screensaver / xfce4-power-manager)
#    Power management settings: nada de blank/suspend en server
if command -v gsettings >/dev/null 2>&1; then
  log "gsettings: idle-delay = 0 (no apaga pantalla)"
  gsettings set org.cinnamon.desktop.session idle-delay 0 2>/dev/null || true
  gsettings set org.cinnamon.settings-daemon.plugins.power sleep-display-ac 0 2>/dev/null || true
  gsettings set org.cinnamon.settings-daemon.plugins.power sleep-inactive-ac-timeout 0 2>/dev/null || true
fi

# 3. WiFi: desactivar power_save persistente via NetworkManager
log "NetworkManager wifi.powersave = 2 (desactivado)"
sudo bash -c 'cat > /etc/NetworkManager/conf.d/99-no-powersave.conf <<EOF
[connection]
wifi.powersave = 2
EOF'
sudo systemctl restart NetworkManager
sleep 5

# 4. WiFi: power_save off ahora mismo
IFACE=$(ls /sys/class/net/ | grep -E '^wlx|^wlan|^wlp' | head -1)
if [ -n "$IFACE" ]; then
  log "iw dev $IFACE set power_save off"
  sudo iw dev "$IFACE" set power_save off 2>&1 | tail -1
fi

# 5. USB autosuspend: deshabilitar para el dongle WiFi
log "USB autosuspend: desactivando para dispositivos WiFi..."
for d in /sys/bus/usb/devices/*/; do
  if [ -e "$d/product" ]; then
    prod=$(cat "$d/product" 2>/dev/null)
    # Heuristica: dispositivos con "WiFi", "Wireless", "WLAN", "802.11" en product
    if echo "$prod" | grep -qiE 'wifi|wireless|wlan|802\.11|RTL|MT76'; then
      log "  $(basename $d): $prod -> autosuspend off"
      sudo bash -c "echo on > $d/power/control" 2>/dev/null || true
      sudo bash -c "echo -1 > $d/power/autosuspend_delay_ms" 2>/dev/null || true
    fi
  fi
done

# 6. udev rule para que persista entre reboots
log "udev rule persistente para USB WiFi..."
sudo bash -c 'cat > /etc/udev/rules.d/50-wifi-no-suspend.rules <<EOF
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="*", ATTR{product}=="*Wireless*", TEST=="power/control", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="*", ATTR{product}=="*WiFi*", TEST=="power/control", ATTR{power/control}="on"
ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="*", ATTR{product}=="*WLAN*", TEST=="power/control", ATTR{power/control}="on"
EOF'
sudo udevadm control --reload-rules

log "LISTO. Verificacion:"
log "  systemd suspend: $(systemctl is-enabled sleep.target 2>&1)"
if [ -n "$IFACE" ]; then
  log "  WiFi power_save: $(iw dev "$IFACE" get power_save 2>/dev/null | head -1)"
fi
