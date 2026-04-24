#!/usr/bin/env python3
import http.server, socketserver, subprocess, os, time, re, json, threading

LOG = "/home/pedro/respaldo-fotos-familia.log"
DEST = "gdrive-bisvidita:Respaldo DiazSantaM 2026-04-14"
TOTAL_GIB = 888.0
CACHE = {"ts": 0, "data": {}}

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
        if "reseteando" in ln.lower():
            last_reset = ln
        if "fallo" in ln.lower() or "recuperada" in ln.lower():
            last_event = ln
    return last_reset, last_event

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
    except Exception:
        d["objects"] = CACHE["data"].get("objects", 0)
        d["bytes"] = CACHE["data"].get("bytes", 0)
        d["drive_reachable"] = False
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
  <h2>Eventos</h2>
  <div class="row"><span class="k">Último del script</span></div>
  <code id="script">—</code>
  <div class="row"><span class="k">Último error</span></div>
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
    const gib=d.bytes/(1024**3);
    const pct=Math.min(gib/TOTAL*100,100);
    set('pct',pct.toFixed(2)+'%',true);
    set('sub',gib.toFixed(2)+' GiB de '+TOTAL.toFixed(0)+' GiB',true);
    document.getElementById('fill').style.width=pct+'%';
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
