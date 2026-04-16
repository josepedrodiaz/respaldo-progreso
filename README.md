# Respaldo Progreso

Página web mínima para ver en tiempo real el progreso de un backup `rclone` a Google Drive. Servidor en Python puro (sin dependencias), systemd unit incluido.

Se creó para monitorear el respaldo de un disco familiar de ~888 GiB a Drive, corriendo como servicio systemd en un server de staging casero.

## Qué muestra

- Porcentaje y GiB subidos
- Barra de progreso
- Estado del servicio `respaldo-fotos-familia`
- Cantidad de archivos en el destino
- Última línea de stats de rclone
- Último archivo copiado
- Punto verde parpadeando si el servicio está activo

Frontend hace polling a `/api` cada 10s vía `fetch`, no recarga la página. Backend refresca `rclone size` contra Drive cada 20s en un thread aparte.

## Archivos

- `progreso-server.py` — servidor HTTP (Python stdlib, puerto 8081)
- `respaldo-progreso.service` — unit systemd

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
