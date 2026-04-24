# Respaldo Progreso

Sistema completo para respaldar un disco grande a Google Drive vía `rclone` de forma resiliente, con monitoreo web en tiempo real.

Se creó para subir ~888 GiB de fotos/videos familiares a Drive desde un server casero con WiFi USB que se caía.

## Componentes

1. **Script de backup auto-sanante** (`respaldo-fotos-familia.sh`): espera a que el disco esté montado, verifica red, y reintenta hasta 5 veces antes de devolverle la falla a systemd.
2. **Watchdog de WiFi** (`wifi-watchdog.sh`): si no hay internet por 2 minutos, bounces el dongle WiFi con `nmcli`.
3. **Página de estado** (`progreso-server.py`): dashboard en tiempo real vía AJAX.

## Qué muestra la página

**Salud del sistema:**
- Disco montado / presente sin montar / ausente
- Red (ping 8.8.8.8)
- Google Drive accesible
- Servicio de backup activo/inactivo
- Watchdog de WiFi activo/inactivo

**Progreso:**
- % y GiB subidos con barra animada
- Cantidad de archivos
- Última stats de rclone
- Último archivo copiado

**Eventos:**
- Último mensaje del script
- Último error
- Último reset automático del WiFi

**UI:**
- Polling a `/api` cada 10s con `fetch`. Solo corre cuando la pestaña está en foco (pausa en `blur`/`visibilitychange`).
- Barrido verde animado en cada tick (feedback visual).
- Flash verde en los campos que cambiaron.

## Archivos

- `progreso-server.py` — servidor HTTP de la página (Python stdlib, puerto 8081)
- `respaldo-progreso.service` — unit systemd de la página
- `respaldo-fotos-familia.sh` — script resiliente del backup
- `respaldo-fotos-familia.service` — unit systemd del backup (`Restart=always`, `RestartSec=30`)
- `wifi-watchdog.sh` — watchdog del dongle WiFi
- `wifi-watchdog.service` — unit systemd del watchdog

## Instalación

```bash
# Subir el script
scp progreso-server.py usuario@server:/home/usuario/progreso-server.py
chmod +x /home/usuario/progreso-server.py

# Instalar el servicio
sudo cp respaldo-progreso.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now respaldo-progreso
```

Accede en `http://IP_DEL_SERVER:8081`.

## Config

Dentro de `progreso-server.py`:

```python
LOG = "/home/pedro/respaldo-fotos-familia.log"
DEST = "gdrive-bisvidita:Respaldo DiazSantaM 2026-04-14"
TOTAL_GIB = 888.0
```

Cambia esos tres valores si lo vas a usar para otro backup.

## Endpoints

- `GET /` — HTML con el dashboard
- `GET /api` — JSON con `{objects, bytes, service, last_copied, last_stats}`
- `GET /health` — responde `ok`
