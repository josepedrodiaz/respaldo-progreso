#!/usr/bin/env python3
import http.server, socketserver, subprocess, os, time, re, json, threading

LOG = "/home/pedro/respaldo-fotos-familia.log"
DEST = "gdrive-bisvidita:Respaldo DiazSantaM 2026-04-14"
TOTAL_GIB = 888.0
CACHE = {"ts": 0, "data": {}}

def refresh():
    d = {}
    try:
        out = subprocess.check_output(
            ["rclone", "size", DEST, "--json"],
            timeout=120, stderr=subprocess.DEVNULL
        ).decode()
        j = json.loads(out)
        d["objects"] = j.get("count", 0)
        d["bytes"] = j.get("bytes", 0)
    except Exception:
        d["objects"] = CACHE["data"].get("objects", 0)
        d["bytes"] = CACHE["data"].get("bytes", 0)
    try:
        active = subprocess.check_output(
            ["systemctl", "is-active", "respaldo-fotos-familia"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except subprocess.CalledProcessError as e:
        active = e.output.decode().strip() if e.output else "unknown"
    d["service"] = active
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
        d["last_copied"] = last_copied
        d["last_stats"] = stats
    except Exception:
        d["last_copied"] = CACHE["data"].get("last_copied", "")
        d["last_stats"] = CACHE["data"].get("last_stats", "")
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
@keyframes blink{50%{opacity:.3}}
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
</style></head>
<body>
<h1><span id="dot" class="dot"></span>Respaldo familia → Google Drive</h1>
<div class="card">
  <div class="big" id="pct">—</div>
  <div class="sub" id="sub">cargando…</div>
  <div class="bar"><div class="fill" id="fill" style="width:0%"></div></div>
</div>
<div class="card">
  <div class="row"><span class="k">Servicio</span><span class="v" id="svc">—</span></div>
  <div class="row"><span class="k">Archivos</span><span class="v" id="obj">—</span></div>
  <div class="row"><span class="k">Última stats</span></div>
  <code id="stats">—</code>
  <div class="row"><span class="k">Último archivo</span></div>
  <code id="last">—</code>
</div>
<div class="foot">Actualizado <span id="ts">—</span></div>
<script>
const TOTAL=888.0;
let timer=null;
let prev={};
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
    const r=await fetch('/api',{cache:'no-store'});
    const d=await r.json();
    const gib=d.bytes/(1024**3);
    const pct=Math.min(gib/TOTAL*100,100);
    set('pct',pct.toFixed(2)+'%',true);
    set('sub',gib.toFixed(2)+' GiB de '+TOTAL.toFixed(0)+' GiB',true);
    document.getElementById('fill').style.width=pct+'%';
    const svc=document.getElementById('svc');
    svc.className='v '+(d.service==='active'?'active':'inactive');
    set('svc',d.service,true);
    document.getElementById('dot').className='dot '+(d.service==='active'?'on':'');
    set('obj',(d.objects||0).toLocaleString(),true);
    set('stats',d.last_stats||'—',true);
    set('last',d.last_copied||'—',true);
    document.getElementById('ts').textContent=new Date().toLocaleTimeString();
    sweepAll();
  }catch(e){
    document.getElementById('sub').textContent='Error al consultar';
  }
}
function start(){
  if(timer) return;
  tick();
  timer=setInterval(tick,10000);
}
function stop(){
  if(!timer) return;
  clearInterval(timer);
  timer=null;
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
