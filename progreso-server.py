#!/usr/bin/env python3
import http.server, socketserver, subprocess, os, time, re, json, threading
from collections import deque

LOG = "/home/pedro/respaldo-fotos-familia.log"
DEST = "gdrive-bisvidita:Respaldo DiazSantaM 2026-04-14"
TOTAL_GIB = 888.0
TOTAL_BYTES = TOTAL_GIB * (1024**3)
CACHE = {"ts": 0, "data": {}}
# Historia: (epoch_seconds, bytes) — guardamos hasta 4h de samples (cada 20s = 720 samples)
HIST = deque(maxlen=720)

SOURCE = "/media/pedro/DiazSantaM"

def cmd(args, timeout=10):
    try:
        return subprocess.check_output(args, timeout=timeout, stderr=subprocess.DEVNULL).decode().strip()
    except subprocess.CalledProcessError as e:
        return (e.output.decode().strip() if e.output else "")
    except Exception:
        return ""

def svc_status(name):
    s = cmd(["systemctl", "is-active", name])
    return s if s else "unknown"

def disk_mounted():
    try:
        with open("/proc/mounts") as f:
            return SOURCE in f.read()
    except Exception:
        return False

def disk_device_present():
    return os.path.exists("/dev/disk/by-label/DiazSantaM")

def net_ok():
    r = subprocess.run(["ping", "-c", "1", "-W", "3", "8.8.8.8"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0

def wifi_watchdog_last():
    out = cmd(["journalctl", "-u", "wifi-watchdog", "-n", "40", "--no-pager", "-o", "short-iso"], timeout=10)
    last_reset = ""
    last_event = ""
    for ln in out.splitlines():
        if "reseteando" in ln.lower() or "lv1" in ln.lower() or "lv2" in ln.lower() or "lv3" in ln.lower() or "lv4" in ln.lower() or "REBOOT" in ln:
            last_reset = ln
        if "fallo" in ln.lower() or "recuperada" in ln.lower():
            last_event = ln
    return last_reset, last_event

def parse_journal_events(unit, source_label, max_n=80):
    """Lee journalctl con timestamp unix, devuelve [(epoch, source, severity, msg)]"""
    out = cmd(["journalctl", "-u", unit, "-n", str(max_n), "--no-pager", "-o", "short-unix"], timeout=15)
    events = []
    for ln in out.splitlines():
        # formato: "1745123456.789 host UNIT[pid]: mensaje"
        m = re.match(r"^(\d+)(?:\.\d+)?\s+\S+\s+([^:]+):\s*(.*)$", ln)
        if not m:
            continue
        epoch = int(m.group(1))
        msg = m.group(3).strip()
        # Limpieza: si el mensaje empieza con [HH:MM:SS], sacarlo (el epoch ya basta)
        msg = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s*", "", msg)
        sev = "info"
        ml = msg.lower()
        if "error" in ml or "failed" in ml or "fallo" in ml:
            sev = "error"
        if "reboot" in ml.lower() or "lv4" in ml or "lv3" in ml:
            sev = "warn"
        if "lv1" in ml or "lv2" in ml or "reseteando" in ml:
            sev = "warn"
        if "recuperada" in ml or "OK" in msg or "arrancado" in ml:
            sev = "ok"
        if "Started" in msg or "Stopped" in msg or "Stopping" in msg:
            sev = "info"
        events.append((epoch, source_label, sev, msg[:240]))
    return events

def parse_rclone_log_events(max_n=60):
    """Eventos relevantes del log de rclone: errores, retries, copied recientes."""
    events = []
    try:
        with open(LOG) as f:
            lines = f.readlines()
    except Exception:
        return events
    # solo ultimas N lineas para no leer el archivo entero (puede ser MB)
    for ln in lines[-2000:]:
        ln = ln.rstrip()
        # Lineas del script: "[YYYY-MM-DD HH:MM:SS] ..."
        m1 = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(.*)$", ln)
        if m1:
            try:
                t = int(time.mktime(time.strptime(m1.group(1), "%Y-%m-%d %H:%M:%S")))
                msg = m1.group(2)[:240]
                sev = "info"
                if "ERROR" in msg or "rindiendome" in msg.lower():
                    sev = "error"
                elif "rclone termino OK" in msg:
                    sev = "ok"
                elif "intento" in msg.lower() or "esperando" in msg.lower() or "retry" in msg.lower():
                    sev = "warn"
                events.append((t, "script", sev, msg))
            except Exception:
                pass
            continue
        # Lineas de rclone: "YYYY/MM/DD HH:MM:SS LEVEL : mensaje"
        m2 = re.match(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s*:\s*(.*)$", ln)
        if m2:
            try:
                t = int(time.mktime(time.strptime(m2.group(1), "%Y/%m/%d %H:%M:%S")))
                level = m2.group(2)
                msg = m2.group(3)[:240]
                # solo reportamos errores, retries, y arranques/finales (no cada Copied)
                interesting = (level in ("ERROR", "CRITICAL")) or \
                              "retry" in msg.lower() or "Failed" in msg or \
                              "Attempt" in msg or "all retries" in msg.lower()
                if not interesting:
                    continue
                sev = "error" if level in ("ERROR", "CRITICAL") or "Failed" in msg else "warn"
                events.append((t, "rclone", sev, msg))
            except Exception:
                pass
    return events[-max_n:]

def parse_nm_events(max_n=60):
    """Eventos relevantes de NetworkManager (state changes, dhcp, etc)."""
    out = cmd(["journalctl", "-u", "NetworkManager", "-n", "200", "--no-pager", "-o", "short-unix"], 15)
    events = []
    for ln in out.splitlines():
        m = re.match(r"^(\d+)(?:\.\d+)?\s+\S+\s+([^:]+):\s*(.*)$", ln)
        if not m:
            continue
        epoch = int(m.group(1))
        raw = m.group(3).strip()
        # filtrar solo lo relevante
        ml = raw.lower()
        relevant = any(x in ml for x in [
            "state change", "dhcp", "associated", "deauth", "disconnected",
            "wifi", "wlxd", "powersave", "no secrets", "secrets exist",
            "scan", "activation", "carrier"
        ])
        if not relevant:
            continue
        # remover prefijo de timestamp interno y nivel
        msg = re.sub(r"^<\w+>\s*\[\d+\.\d+\]\s*", "", raw)
        sev = "info"
        if "error" in ml or "warn" in ml or "fail" in ml or "deauth" in ml:
            sev = "warn"
        if "disconnected" in ml or "unmanaged" in ml or "no secrets" in ml:
            sev = "warn"
        if "associated" in ml or "activated" in ml or "got ip" in ml or "address" in ml:
            sev = "ok"
        events.append((epoch, "nm", sev, msg[:240]))
    return events[-max_n:]

def parse_wpa_events(max_n=40):
    out = cmd(["journalctl", "-u", "wpa_supplicant", "-n", "150", "--no-pager", "-o", "short-unix"], 15)
    events = []
    for ln in out.splitlines():
        m = re.match(r"^(\d+)(?:\.\d+)?\s+\S+\s+([^:]+):\s*(.*)$", ln)
        if not m:
            continue
        epoch = int(m.group(1))
        msg = m.group(3).strip()
        ml = msg.lower()
        relevant = any(x in ml for x in [
            "deauth", "disassoc", "connected", "disconnected",
            "auth failure", "associating", "associated", "ctrl-event",
            "trying to associate"
        ])
        if not relevant:
            continue
        sev = "info"
        if "deauth" in ml or "disassoc" in ml or "disconnected" in ml or "failure" in ml:
            sev = "warn"
        if "ctrl-event-connected" in ml or "associated with" in ml:
            sev = "ok"
        events.append((epoch, "wpa", sev, msg[:240]))
    return events[-max_n:]

def parse_kernel_events(max_n=40):
    out = cmd(["journalctl", "-k", "-n", "300", "--no-pager", "-o", "short-unix"], 15)
    events = []
    for ln in out.splitlines():
        m = re.match(r"^(\d+)(?:\.\d+)?\s+\S+\s+kernel:\s*(.*)$", ln)
        if not m:
            continue
        epoch = int(m.group(1))
        msg = m.group(2).strip()
        ml = msg.lower()
        # solo eventos USB del bus, wifi driver, suspend/resume
        relevant = any(x in ml for x in [
            "wlxd0df", "r8712u", "rtl81", "usb 1-1", "usb 2-1",
            "suspend", "resume", "disabled by hub", "emi"
        ])
        if not relevant:
            continue
        sev = "info"
        if "disabled" in ml or "disconnect" in ml or "error" in ml or "emi" in ml:
            sev = "warn"
        if "new high-speed" in ml or "new low-speed" in ml or "new full-speed" in ml:
            sev = "info"
        events.append((epoch, "kernel", sev, msg[:240]))
    return events[-max_n:]

def collect_events(max_n=80):
    all_e = []
    all_e += parse_journal_events("wifi-watchdog", "watchdog", 80)
    all_e += parse_journal_events("respaldo-fotos-familia", "backup", 60)
    all_e += parse_rclone_log_events(60)
    all_e += parse_nm_events(60)
    all_e += parse_wpa_events(40)
    all_e += parse_kernel_events(40)
    all_e.sort(key=lambda e: e[0], reverse=True)
    out = []
    for (t, src, sev, msg) in all_e[:max_n]:
        out.append({"ts": t, "src": src, "sev": sev, "msg": msg})
    return out

def refresh():
    d = {}
    # Drive size (lento, puede fallar si no hay red)
    try:
        out = subprocess.check_output(
            ["rclone", "size", DEST, "--json"],
            timeout=120, stderr=subprocess.DEVNULL
        ).decode()
        j = json.loads(out)
        d["objects"] = j.get("count", 0)
        d["bytes"] = j.get("bytes", 0)
        d["drive_reachable"] = True
        # Append a la historia
        HIST.append((time.time(), d["bytes"]))
    except Exception:
        d["objects"] = CACHE["data"].get("objects", 0)
        d["bytes"] = CACHE["data"].get("bytes", 0)
        d["drive_reachable"] = False
    # Calculo de velocidades promedio y ETA
    def avg_speed_window(seconds):
        now = time.time()
        relevant = [(t, b) for (t, b) in HIST if now - t <= seconds]
        if len(relevant) < 2:
            return None
        dt = relevant[-1][0] - relevant[0][0]
        db = relevant[-1][1] - relevant[0][1]
        if dt <= 0:
            return None
        return db / dt  # bytes/s
    speed_5m = avg_speed_window(300)
    speed_1h = avg_speed_window(3600)
    speed_total = avg_speed_window(86400 * 30)  # toda la historia
    d["speed_5m_bps"] = speed_5m
    d["speed_1h_bps"] = speed_1h
    d["speed_total_bps"] = speed_total
    # ETA: usa el promedio mas confiable (1h si hay, sino 5m)
    speed_eta = speed_1h if speed_1h and speed_1h > 0 else speed_5m
    if speed_eta and speed_eta > 0:
        remaining = max(0, TOTAL_BYTES - d["bytes"])
        d["eta_seconds"] = int(remaining / speed_eta)
    else:
        d["eta_seconds"] = None
    # Archivos por hora: contar lineas "Copied" en el log de la ultima hora
    try:
        cutoff = time.time() - 3600
        n = 0
        with open(LOG) as f:
            for ln in f:
                if "Copied" in ln:
                    m = re.match(r"^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", ln)
                    if m:
                        try:
                            t = time.mktime(time.strptime(m.group(1), "%Y/%m/%d %H:%M:%S"))
                            if t >= cutoff:
                                n += 1
                        except Exception:
                            pass
        d["files_per_hour"] = n
    except Exception:
        d["files_per_hour"] = CACHE["data"].get("files_per_hour", 0)
    # Estado de servicios
    d["service"] = svc_status("respaldo-fotos-familia")
    d["watchdog"] = svc_status("wifi-watchdog")
    # Estado de disco
    d["disk_mounted"] = disk_mounted()
    d["disk_present"] = disk_device_present()
    # Estado de red
    d["net_ok"] = net_ok()
    # Watchdog - ultimo evento
    last_reset, last_event = wifi_watchdog_last()
    d["wifi_last_reset"] = last_reset
    d["wifi_last_event"] = last_event
    # Timeline completo de eventos del sistema de proteccion
    try:
        d["events"] = collect_events(60)
    except Exception:
        d["events"] = CACHE["data"].get("events", [])
    # Log del respaldo - ultimo archivo, stats, ultimo mensaje relevante
    try:
        with open(LOG) as f:
            lines = f.readlines()
        last_copied = ""
        for ln in reversed(lines):
            if "Copied" in ln:
                last_copied = ln.strip()[-220:]
                break
        stats = ""
        for ln in reversed(lines):
            if re.search(r"\d+ B/s|\d+\.\d+ [KMG]iB/s", ln):
                stats = ln.strip()
                break
        last_script = ""
        for ln in reversed(lines):
            s = ln.strip()
            if s.startswith("[") and "]" in s:
                last_script = s
                break
        last_error = ""
        for ln in reversed(lines[-300:]):
            if re.search(r"\bERROR\b|CRITICAL|Failed", ln):
                last_error = ln.strip()[-220:]
                break
        d["last_copied"] = last_copied
        d["last_stats"] = stats
        d["last_script"] = last_script
        d["last_error"] = last_error
    except Exception:
        for k in ("last_copied", "last_stats", "last_script", "last_error"):
            d[k] = CACHE["data"].get(k, "")
    CACHE["data"] = d
    CACHE["ts"] = time.time()

def loop():
    while True:
        try:
            refresh()
        except Exception:
            pass
        time.sleep(20)

# Refresh inicial sincronico (rapido, sin rclone size) para que la pagina
# tenga datos utiles desde el primer request. El refresh en background
# va a llenar lo que falta (rclone size, journalctl).
def quick_initial():
    d = {}
    d["objects"] = 0
    d["bytes"] = 0
    d["drive_reachable"] = False
    d["service"] = svc_status("respaldo-fotos-familia")
    d["watchdog"] = svc_status("wifi-watchdog")
    d["disk_mounted"] = disk_mounted()
    d["disk_present"] = disk_device_present()
    d["net_ok"] = net_ok()
    d["wifi_last_reset"] = ""
    d["wifi_last_event"] = ""
    d["speed_5m_bps"] = None
    d["speed_1h_bps"] = None
    d["speed_total_bps"] = None
    d["eta_seconds"] = None
    d["files_per_hour"] = 0
    d["events"] = []
    for k in ("last_copied", "last_stats", "last_script", "last_error"):
        d[k] = ""
    CACHE["data"] = d
    CACHE["ts"] = time.time()

quick_initial()
threading.Thread(target=loop, daemon=True).start()

HTML = """<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><title>Respaldo familia</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:-apple-system,sans-serif;background:#111;color:#eee;margin:0;padding:20px;max-width:720px;margin:auto}
h1{font-size:22px;margin:0 0 20px}
.card{background:#1e1e1e;border-radius:10px;padding:18px;margin-bottom:14px}
.big{font-size:42px;font-weight:700;color:#8bd450;transition:color .3s}
.sub{color:#888;font-size:13px;margin-top:4px}
.bar{background:#333;border-radius:6px;height:22px;overflow:hidden;margin-top:12px}
.fill{background:linear-gradient(90deg,#4a9eff,#8bd450);height:100%;transition:width .8s}
.row{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #2a2a2a;font-size:14px;gap:10px}
.row:last-child{border:0}
.k{color:#888;flex-shrink:0}
.v{color:#eee;font-family:monospace;text-align:right;word-break:break-all}
.active{color:#8bd450}
.inactive{color:#e55}
code{background:#000;padding:6px 8px;border-radius:3px;font-size:11px;word-break:break-all;display:block;margin-top:6px}
.foot{color:#555;font-size:11px;text-align:center;margin-top:20px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#555}
.dot.on{background:#8bd450;animation:blink 1.5s infinite}
.dot.bad{background:#e55}
.dot.warn{background:#f0a030}
@keyframes blink{50%{opacity:.3}}
.pill{display:inline-flex;align-items:center;gap:6px}
.pill .dotmini{width:8px;height:8px;border-radius:50%;background:#555}
.pill.ok .dotmini{background:#8bd450}
.pill.bad .dotmini{background:#e55}
.pill.ok{color:#8bd450}
.pill.bad{color:#e55}
h2{font-size:14px;color:#888;margin:0 0 10px;text-transform:uppercase;letter-spacing:.5px}
.metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-top:8px}
.metric{background:#0d0d0d;padding:14px;border-radius:8px;border:1px solid #2a2a2a}
.metric-label{color:#888;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.metric-val{font-family:monospace;color:#8bd450;font-size:20px;font-weight:600}
.metric-val.big2{font-size:28px;line-height:1.1}
.metric-sub{color:#666;font-size:11px;margin-top:4px}
.timeline{max-height:480px;overflow-y:auto;margin:0 -6px;padding:0 6px}
.event{display:grid;grid-template-columns:60px 70px 1fr;gap:8px;align-items:start;padding:6px 0;border-bottom:1px solid #1a1a1a;font-size:12px}
.event:last-child{border:0}
.ev-time{color:#666;font-family:monospace;font-size:11px}
.ev-src{font-size:10px;text-transform:uppercase;letter-spacing:.5px;font-weight:600;padding:2px 6px;border-radius:4px;text-align:center;background:#222;color:#aaa}
.ev-src.watchdog{background:#1a3a5a;color:#82c4ff}
.ev-src.backup{background:#3a2a5a;color:#c9a4ff}
.ev-src.rclone{background:#2a4a2a;color:#a4dca4}
.ev-src.script{background:#5a3a1a;color:#ffc080}
.ev-src.nm{background:#5a1a4a;color:#ff80c0}
.ev-src.wpa{background:#1a4a5a;color:#80e0e0}
.ev-src.kernel{background:#3a3a3a;color:#cccccc}
.ev-msg{color:#ddd;font-family:monospace;word-break:break-word;line-height:1.4}
.ev-msg.error{color:#ff8585}
.ev-msg.warn{color:#f0c060}
.ev-msg.ok{color:#8bd450}
.ev-msg.info{color:#aaa}
.flash{animation:flash 1.2s ease-out}
@keyframes flash{
  0%{background:rgba(139,212,80,.35);box-shadow:0 0 0 3px rgba(139,212,80,.35);color:#fff}
  100%{background:transparent;box-shadow:0 0 0 0 transparent}
}
.card{position:relative;overflow:hidden}
.card::before{
  content:"";position:absolute;inset:0;
  background:linear-gradient(110deg,transparent 30%,rgba(139,212,80,.22) 50%,transparent 70%);
  transform:translateX(-100%);
  pointer-events:none;
  opacity:0
}
.card.sweep::before{animation:sweep 1.1s ease-out}
@keyframes sweep{
  0%{transform:translateX(-100%);opacity:1}
  100%{transform:translateX(100%);opacity:0}
}
body.offline{opacity:.45;filter:grayscale(.8)}
body.offline .card{border:1px solid #533}
#offline{display:none;background:#6b1a1a;color:#fff;padding:12px 16px;border-radius:8px;margin-bottom:14px;font-size:14px;text-align:center;border:1px solid #e55;opacity:1;filter:none}
body.offline #offline{display:block;animation:pulse 1.5s infinite;opacity:1;filter:none}
@keyframes pulse{50%{background:#8b2323}}
</style></head>
<body>
<div id="offline">⚠️ SIN CONEXIÓN al server · datos desactualizados <span id="stale">—</span></div>
<h1><span id="dot" class="dot"></span>Respaldo familia → Google Drive</h1>
<div class="card">
  <div class="big" id="pct">—</div>
  <div class="sub" id="sub">cargando…</div>
  <div class="bar"><div class="fill" id="fill" style="width:0%"></div></div>
</div>
<div class="card">
  <h2>Ritmo y ETA</h2>
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">ETA</div>
      <div class="metric-val big2" id="eta">—</div>
      <div class="metric-sub" id="etadate">—</div>
    </div>
    <div class="metric">
      <div class="metric-label">Velocidad última hora</div>
      <div class="metric-val" id="sp1h">—</div>
    </div>
    <div class="metric">
      <div class="metric-label">Velocidad últimos 5 min</div>
      <div class="metric-val" id="sp5m">—</div>
    </div>
    <div class="metric">
      <div class="metric-label">Archivos / hora</div>
      <div class="metric-val" id="fph">—</div>
    </div>
  </div>
</div>
<div class="card">
  <h2>Salud del sistema</h2>
  <div class="row"><span class="k">Disco (DiazSantaM)</span><span class="v" id="disk">—</span></div>
  <div class="row"><span class="k">Red (ping 8.8.8.8)</span><span class="v" id="net">—</span></div>
  <div class="row"><span class="k">Google Drive</span><span class="v" id="drive">—</span></div>
  <div class="row"><span class="k">Servicio backup</span><span class="v" id="svc">—</span></div>
  <div class="row"><span class="k">Watchdog WiFi</span><span class="v" id="wd">—</span></div>
</div>
<div class="card">
  <h2>Progreso</h2>
  <div class="row"><span class="k">Archivos subidos</span><span class="v" id="obj">—</span></div>
  <div class="row"><span class="k">Última stats rclone</span></div>
  <code id="stats">—</code>
  <div class="row"><span class="k">Último archivo copiado</span></div>
  <code id="last">—</code>
</div>
<div class="card">
  <h2>Sistema de protección — eventos</h2>
  <div class="sub" style="margin-bottom:10px">Watchdog WiFi, servicio backup, rclone — últimos 60 eventos</div>
  <div class="timeline" id="events"></div>
</div>
<div class="card">
  <h2>Resumen rápido</h2>
  <div class="row"><span class="k">Último del script</span></div>
  <code id="script">—</code>
  <div class="row"><span class="k">Último error rclone</span></div>
  <code id="err">—</code>
  <div class="row"><span class="k">Último reset WiFi</span></div>
  <code id="wifireset">—</code>
</div>
<div class="foot">Actualizado <span id="ts">—</span></div>
<script>
const TOTAL=888.0;
let timer=null;
let staleTimer=null;
let lastOk=Date.now();
function fmtAge(ms){
  const s=Math.floor(ms/1000);
  if(s<60) return 'hace '+s+'s';
  if(s<3600) return 'hace '+Math.floor(s/60)+'m '+(s%60)+'s';
  return 'hace '+Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
}
function markOffline(){
  document.body.classList.add('offline');
  document.getElementById('stale').textContent=fmtAge(Date.now()-lastOk);
}
function markOnline(){
  document.body.classList.remove('offline');
  lastOk=Date.now();
}
function set(id,val,highlight){
  const el=document.getElementById(id);
  const old=el.dataset.val;
  el.textContent=val;
  if(highlight && old!==undefined && old!==String(val)){
    el.classList.remove('flash');
    void el.offsetWidth;
    el.classList.add('flash');
  }
  el.dataset.val=val;
}
function sweepAll(){
  document.querySelectorAll('.card').forEach(c=>{
    c.classList.remove('sweep');
    void c.offsetWidth;
    c.classList.add('sweep');
  });
}
document.addEventListener('animationend',e=>{
  if(e.animationName==='sweep') e.target.classList.remove('sweep');
});
async function tick(){
  try{
    const ctrl=new AbortController();
    const tout=setTimeout(()=>ctrl.abort(),8000);
    const r=await fetch('/api',{cache:'no-store',signal:ctrl.signal});
    clearTimeout(tout);
    if(!r.ok) throw new Error('http '+r.status);
    const d=await r.json();
    markOnline();
    if(!d || d.service===undefined){
      // cache aun sin poblar
      document.getElementById('sub').textContent='Cargando datos…';
      return;
    }
    const gib=(d.bytes||0)/(1024**3);
    const pct=Math.min(gib/TOTAL*100,100);
    set('pct',pct.toFixed(2)+'%',true);
    set('sub',gib.toFixed(2)+' GiB de '+TOTAL.toFixed(0)+' GiB',true);
    document.getElementById('fill').style.width=pct+'%';
    // Velocidad y ETA
    function fmtSpeed(bps){
      if(bps==null||bps<=0) return '—';
      if(bps>=1024*1024) return (bps/(1024*1024)).toFixed(2)+' MiB/s';
      if(bps>=1024) return (bps/1024).toFixed(0)+' KiB/s';
      return bps.toFixed(0)+' B/s';
    }
    function fmtDuration(s){
      if(s==null||s<=0) return '—';
      const d=Math.floor(s/86400),h=Math.floor((s%86400)/3600),m=Math.floor((s%3600)/60);
      if(d>0) return d+'d '+h+'h';
      if(h>0) return h+'h '+m+'m';
      return m+'m';
    }
    set('sp5m',fmtSpeed(d.speed_5m_bps),true);
    set('sp1h',fmtSpeed(d.speed_1h_bps),true);
    set('eta',fmtDuration(d.eta_seconds),true);
    if(d.eta_seconds&&d.eta_seconds>0){
      const end=new Date(Date.now()+d.eta_seconds*1000);
      set('etadate','llega ~ '+end.toLocaleDateString('es',{weekday:'short',day:'numeric',month:'short'})+' '+end.toLocaleTimeString('es',{hour:'2-digit',minute:'2-digit'}),true);
    } else {
      set('etadate','—',false);
    }
    set('fph',(d.files_per_hour||0).toLocaleString(),true);
    // Salud
    function pill(id,ok,txt){
      const el=document.getElementById(id);
      el.className='v pill '+(ok?'ok':'bad');
      el.innerHTML='<span class="dotmini"></span>'+txt;
    }
    pill('disk',d.disk_mounted,d.disk_mounted?'montado':(d.disk_present?'presente sin montar':'ausente'));
    pill('net',d.net_ok,d.net_ok?'OK':'sin red');
    pill('drive',d.drive_reachable,d.drive_reachable?'OK':'sin acceso');
    pill('svc',d.service==='active',d.service);
    pill('wd',d.watchdog==='active',d.watchdog);
    // Dot del header: peor estado manda
    const allGood=d.service==='active'&&d.disk_mounted&&d.net_ok;
    const someBad=!d.disk_mounted||!d.net_ok||d.service==='failed';
    const dot=document.getElementById('dot');
    dot.className='dot '+(allGood?'on':(someBad?'bad':'warn'));
    set('obj',(d.objects||0).toLocaleString(),true);
    set('stats',d.last_stats||'—',true);
    set('last',d.last_copied||'—',true);
    set('script',d.last_script||'—',true);
    set('err',d.last_error||'sin errores recientes',true);
    set('wifireset',d.wifi_last_reset||'sin resets',true);
    // Timeline
    function timeAgo(ts){
      const s=Math.floor(Date.now()/1000-ts);
      if(s<60) return s+'s';
      if(s<3600) return Math.floor(s/60)+'m';
      if(s<86400) return Math.floor(s/3600)+'h';
      return Math.floor(s/86400)+'d';
    }
    function escapeHtml(s){
      return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    const evs=d.events||[];
    const evHtml=evs.map(e=>
      '<div class="event"><div class="ev-time">'+timeAgo(e.ts)+'</div>'+
      '<div class="ev-src '+e.src+'">'+e.src+'</div>'+
      '<div class="ev-msg '+e.sev+'">'+escapeHtml(e.msg)+'</div></div>'
    ).join('');
    const evContainer=document.getElementById('events');
    if(evContainer.dataset.sig!==String(evs.length)+(evs[0]?evs[0].ts:'')){
      evContainer.innerHTML=evHtml||'<div style="color:#666;padding:10px">sin eventos</div>';
      evContainer.dataset.sig=String(evs.length)+(evs[0]?evs[0].ts:'');
    }
    document.getElementById('ts').textContent=new Date().toLocaleTimeString();
    sweepAll();
  }catch(e){
    markOffline();
  }
}
function start(){
  if(timer) return;
  tick();
  timer=setInterval(tick,10000);
  staleTimer=setInterval(()=>{
    if(document.body.classList.contains('offline')){
      document.getElementById('stale').textContent=fmtAge(Date.now()-lastOk);
    }
  },1000);
}
function stop(){
  if(!timer) return;
  clearInterval(timer);timer=null;
  clearInterval(staleTimer);staleTimer=null;
}
document.addEventListener('visibilitychange',()=>{
  document.hidden?stop():start();
});
window.addEventListener('focus',start);
window.addEventListener('blur',stop);
if(!document.hidden) start();
</script>
</body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        if self.path == "/api":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(CACHE["data"]).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0", 8081), H) as s:
    s.serve_forever()
