#!/usr/bin/env python3
"""PLUSLokaal Print-Agent voor Raspberry Pi.

Draait in de winkel, verbindt ZELF (uitgaand, HTTPS) met pluslokaal.com en print
opdrachten lokaal op de via USB aangesloten printers:
  - labelprinter  : rauwe TSPL/ZPL-bytes naar /dev/usb/lp*  (of een CUPS-raw-queue)
  - winkelprinter : PDF via CUPS (lp), met papierlade per formaat

Webinterface op poort 8080 voor alle instellingen. Werkt zichzelf automatisch bij
(controleert dagelijks op pluslokaal.com). Alleen Python-standaardbibliotheek.

Installatie (als root):   python3 pluslokaal_agent.py --install
Handmatig draaien:        python3 pluslokaal_agent.py
"""
import base64
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote as urlquote

AGENT_VERSION = '1.0.0'
CONFIG_DIR = '/etc/pluslokaal-agent'
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
DEFAULTS = {
    'server': 'https://pluslokaal.com',
    'key': '',
    'label_device': '',          # bv. /dev/usb/lp0 (leeg = labelprinten uit)
    'doc_queue': '',             # CUPS-queuenaam van de winkelprinter (leeg = documentprinten uit)
    'tray_map': {},              # {'A3': 'tray-3', 'A4': 'tray-2', ...} → lp -o InputSlot=...
    'poll_interval': 3,
    'web_port': 8080,
    'auto_update': True,
}

state = {
    'last_poll': 0, 'last_ok': 0, 'online': False, 'jobs_done': 0, 'jobs_err': 0,
    'last_error': '', 'log': [],
}
_log_lock = threading.Lock()


def log(msg):
    line = time.strftime('%d-%m %H:%M:%S') + '  ' + str(msg)
    with _log_lock:
        state['log'].append(line)
        if len(state['log']) > 300:
            state['log'] = state['log'][-200:]
    print(line, flush=True)


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.load(open(CONFIG_FILE)))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + '.tmp'
    json.dump(cfg, open(tmp, 'w'), indent=2)
    os.replace(tmp, CONFIG_FILE)
    os.chmod(CONFIG_FILE, 0o600)          # sleutel niet wereld-leesbaar


CFG = load_config()


# ─── Printers ─────────────────────────────────────────────────────────────────
def usb_label_devices():
    return sorted(glob.glob('/dev/usb/lp*'))


def cups_queues():
    try:
        out = subprocess.run(['lpstat', '-p'], capture_output=True, text=True, timeout=10).stdout
        return re.findall(r'^printer (\S+)', out, re.M)
    except Exception:
        return []


def print_label(payload: bytes):
    dev = CFG.get('label_device')
    if not dev:
        raise RuntimeError('geen labelprinter ingesteld op de Pi (webinterface → Labelprinter)')
    if '/' in dev:                       # devicepad (bv. /dev/usb/lp0) → rauw schrijven
        with open(dev, 'wb') as fh:
            fh.write(payload)
        return
    # anders: CUPS-raw-queue (queuenaam)
    p = subprocess.run(['lp', '-d', dev, '-o', 'raw', '-'], input=payload,
                       capture_output=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b'lp faalde').decode(errors='replace')[:200])


def print_document(pdf: bytes, meta: dict):
    q = CFG.get('doc_queue')
    if not q:
        raise RuntimeError('geen winkelprinter (CUPS-queue) ingesteld op de Pi')
    args = ['lp', '-d', q, '-n', str(max(1, int(meta.get('copies') or 1)))]
    media = (meta.get('media') or '').strip()
    if media:
        args += ['-o', f'media={media}']
    if (meta.get('orient') or '') == 'landscape':
        args += ['-o', 'landscape']
    # lade: server stuurt bv. 'tray-3'; vertaal via de mapping in de webinterface (formaat→InputSlot)
    src = (meta.get('source') or '').strip()
    tray_map = CFG.get('tray_map') or {}
    slot = tray_map.get(meta.get('label') or '', '') or tray_map.get(src, '') or src
    if slot and slot != 'auto':
        args += ['-o', f'InputSlot={slot}']
    args += ['-t', (meta.get('job_name') or 'pluslokaal')[:60], '-']
    p = subprocess.run(args, input=pdf, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b'lp faalde').decode(errors='replace')[:200])


# ─── Server-communicatie ──────────────────────────────────────────────────────
def api(path, body=None, timeout=30):
    url = CFG['server'].rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method='POST' if data else 'GET',
                                 headers={'Content-Type': 'application/json',
                                          'X-Agent-Key': CFG.get('key', ''),
                                          'User-Agent': f'pluslokaal-agent/{AGENT_VERSION}'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def poll_once():
    info = {'hostname': os.uname().nodename,
            'printers': ([os.path.basename(CFG['label_device'])] if CFG.get('label_device') else [])
                        + ([CFG['doc_queue']] if CFG.get('doc_queue') else [])}
    res = api('/api/agent/poll', {'version': AGENT_VERSION, 'info': info})
    state['online'] = True
    state['last_ok'] = time.time()
    for job in res.get('jobs', []):
        jid = job['id']
        try:
            payload = base64.b64decode(job['payload_b64'])
            meta = job.get('meta') or {}
            log(f"job {jid}: {job['kind']} ({len(payload)}b) {meta.get('label','')}")
            if job['kind'] == 'label':
                print_label(payload)
            else:
                print_document(payload, meta)
            api('/api/agent/result', {'job_id': jid, 'ok': True})
            state['jobs_done'] += 1
            log(f'job {jid}: klaar')
        except Exception as e:
            state['jobs_err'] += 1
            state['last_error'] = str(e)[:300]
            log(f'job {jid}: FOUT {e}')
            try:
                api('/api/agent/result', {'job_id': jid, 'ok': False, 'error': str(e)[:300]})
            except Exception:
                pass
    return res


def poll_loop():
    while True:
        state['last_poll'] = time.time()
        try:
            if CFG.get('key'):
                poll_once()
            else:
                state['online'] = False
        except urllib.error.HTTPError as e:
            state['online'] = False
            state['last_error'] = f'server: HTTP {e.code}' + (' (sleutel onjuist?)' if e.code == 401 else '')
        except Exception as e:
            state['online'] = False
            state['last_error'] = str(e)[:200]
        time.sleep(max(2, int(CFG.get('poll_interval') or 3)))


# ─── Auto-update ──────────────────────────────────────────────────────────────
def check_update(force=False):
    if not (CFG.get('auto_update') or force):
        return 'auto-update staat uit'
    try:
        res = api('/api/agent/update')
        latest, sha = res.get('version', ''), res.get('sha256', '')
        if latest and latest != AGENT_VERSION:
            url = CFG['server'].rstrip('/') + '/api/agent/download'
            req = urllib.request.Request(url, headers={'User-Agent': 'pluslokaal-agent'})
            data = urllib.request.urlopen(req, timeout=60).read()
            if sha and hashlib.sha256(data).hexdigest() != sha:
                log('update: sha256 klopt niet - overgeslagen')
                return 'controle mislukt'
            target = os.path.abspath(__file__)
            tmp = target + '.new'
            open(tmp, 'wb').write(data)
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
            log(f'update: {AGENT_VERSION} → {latest}; herstarten…')
            threading.Timer(1.0, lambda: os._exit(42)).start()   # systemd Restart=always start ons opnieuw
            return f'bijgewerkt naar {latest}, herstart…'
        return f'al actueel (v{AGENT_VERSION})'
    except Exception as e:
        return f'update-controle mislukt: {e}'


def update_loop():
    while True:
        time.sleep(6 * 3600)
        log('update-controle: ' + str(check_update()))


# ─── Webinterface ─────────────────────────────────────────────────────────────
PAGE = """<!doctype html><html lang=nl><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>PLUSLokaal Print-Agent</title><style>
:root{{--green:#80bd1d;--dgreen:#115013;--bg:#f4f5f3}}
*{{box-sizing:border-box}}body{{font-family:system-ui,sans-serif;margin:0;background:var(--bg);color:#231f20}}
header{{background:var(--green);color:#fff;padding:14px 20px;font-weight:800;font-size:1.15rem}}
main{{max-width:760px;margin:20px auto;padding:0 14px}}
.card{{background:#fff;border-radius:12px;padding:18px;margin-bottom:16px;box-shadow:0 2px 10px rgba(0,0,0,.06)}}
h2{{margin:0 0 12px;font-size:1rem;color:var(--dgreen);text-transform:uppercase;letter-spacing:.04em}}
label{{display:block;font-size:.8rem;font-weight:700;color:#666;margin:10px 0 4px}}
input,select{{width:100%;padding:9px 10px;border:1px solid #ddd;border-radius:8px;font-size:.95rem}}
button{{background:var(--green);color:#fff;border:0;border-radius:20px;padding:10px 18px;font-weight:700;cursor:pointer;margin-top:12px}}
button.ghost{{background:#fff;color:var(--dgreen);border:1px solid #ccc}}
.badge{{display:inline-block;border-radius:12px;padding:3px 10px;color:#fff;font-size:.8rem;font-weight:700}}
.ok{{background:var(--green)}}.err{{background:#dd350d}}
pre{{background:#1d221c;color:#cde6a8;padding:12px;border-radius:8px;font-size:.72rem;max-height:280px;overflow:auto}}
.row{{display:flex;gap:10px;flex-wrap:wrap}}.row>*{{flex:1;min-width:180px}}
small{{color:#777}}</style></head><body>
<header>&#10010; PLUSLokaal · Print-Agent <span style="font-weight:400;font-size:.8rem">v{version}</span></header>
<main>
<div class=card><h2>Status</h2>
  <p>Server: <span class="badge {online_cls}">{online_txt}</span> &nbsp; {server}<br>
  <small>Jobs geprint: {done} · fouten: {err} · laatste fout: {lasterr}</small></p></div>
<div class=card><h2>Instellingen</h2>
<form method=post action=/save>
  <div class=row>
    <div><label>Server</label><input name=server value="{server}"></div>
    <div><label>Agent-sleutel (uit Beheer &rarr; Filialen)</label><input name=key value="{key}" placeholder="plak hier de sleutel"></div>
  </div>
  <div class=row>
    <div><label>Labelprinter (USB)</label><select name=label_device><option value="">- uit -</option>{label_opts}</select></div>
    <div><label>Winkelprinter (CUPS-queue)</label><select name=doc_queue><option value="">- uit -</option>{queue_opts}</select></div>
  </div>
  <label>Papierlade per lade-code <small>(server-code &rarr; CUPS InputSlot, bv. tray-3=Tray3; regel per mapping)</small></label>
  <input name=tray_map value="{tray_map}" placeholder="tray-2=Tray2, tray-3=Tray3">
  <div class=row>
    <div><label>Poll-interval (sec)</label><input name=poll_interval value="{poll}"></div>
    <div><label>Auto-update</label><select name=auto_update><option value=1 {au1}>aan</option><option value=0 {au0}>uit</option></select></div>
  </div>
  <button>Opslaan</button>
</form></div>
<div class=card><h2>Testen &amp; onderhoud</h2>
  <form method=post action=/test_label style="display:inline"><button class=ghost>Testlabel</button></form>
  <form method=post action=/test_doc style="display:inline"><button class=ghost>Testpagina (A4)</button></form>
  <form method=post action=/update style="display:inline"><button class=ghost>Nu bijwerken</button></form>
  <p><small>{msg}</small></p></div>
<div class=card><h2>Log</h2><pre>{log}</pre></div>
</main></body></html>"""


class Web(BaseHTTPRequestHandler):
    def _html(self, body, code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, msg=''):
        self.send_response(303)
        self.send_header('Location', '/?msg=' + urlquote(msg))
        self.end_headers()

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        esc = lambda s: str(s).replace('&', '&amp;').replace('<', '&lt;')
        label_opts = ''.join(f'<option value="{d}" {"selected" if CFG.get("label_device")==d else ""}>{d}</option>'
                             for d in usb_label_devices() + cups_queues())
        queue_opts = ''.join(f'<option value="{d}" {"selected" if CFG.get("doc_queue")==d else ""}>{d}</option>'
                             for d in cups_queues())
        tray = ', '.join(f'{k}={v}' for k, v in (CFG.get('tray_map') or {}).items())
        online = state['online']
        with _log_lock:
            logtxt = esc('\n'.join(state['log'][-120:]))
        self._html(PAGE.format(
            version=AGENT_VERSION, server=esc(CFG.get('server', '')), key=esc(CFG.get('key', '')),
            online_cls='ok' if online else 'err', online_txt='verbonden' if online else 'geen verbinding',
            done=state['jobs_done'], err=state['jobs_err'], lasterr=esc(state['last_error'] or '-'),
            label_opts=label_opts, queue_opts=queue_opts, tray_map=esc(tray),
            poll=CFG.get('poll_interval', 3),
            au1='selected' if CFG.get('auto_update') else '', au0='' if CFG.get('auto_update') else 'selected',
            msg=esc((q.get('msg') or [''])[0]), log=logtxt))

    def do_POST(self):
        global CFG
        ln = int(self.headers.get('Content-Length') or 0)
        form = parse_qs(self.rfile.read(ln).decode())
        g = lambda k, d='': (form.get(k) or [d])[0].strip()
        path = urlparse(self.path).path
        if path == '/save':
            CFG['server'] = g('server', DEFAULTS['server'])
            CFG['key'] = g('key')
            CFG['label_device'] = g('label_device')
            CFG['doc_queue'] = g('doc_queue')
            try:
                CFG['poll_interval'] = max(2, int(g('poll_interval', '3')))
            except ValueError:
                CFG['poll_interval'] = 3
            CFG['auto_update'] = g('auto_update', '1') == '1'
            tm = {}
            for part in re.split(r'[,\n]+', g('tray_map')):
                if '=' in part:
                    k, v = part.split('=', 1)
                    if k.strip():
                        tm[k.strip()] = v.strip()
            CFG['tray_map'] = tm
            save_config(CFG)
            log('instellingen opgeslagen')
            return self._redirect('Opgeslagen.')
        if path == '/test_label':
            try:
                # simpel TSPL-testlabel
                tspl = b'SIZE 45 mm,40 mm\r\nGAP 3 mm,0\r\nCLS\r\nTEXT 30,40,"3",0,1,1,"PLUS TEST"\r\nBARCODE 30,90,"EAN13",60,1,0,2,2,"871040014582"\r\nPRINT 1\r\n'
                print_label(tspl)
                return self._redirect('Testlabel verstuurd.')
            except Exception as e:
                return self._redirect(f'Testlabel mislukt: {e}')
        if path == '/test_doc':
            try:
                pdf = base64.b64decode(_TEST_PDF_B64)
                print_document(pdf, {'media': 'iso_a4_210x297mm', 'copies': 1, 'job_name': 'pluslokaal-test'})
                return self._redirect('Testpagina verstuurd.')
            except Exception as e:
                return self._redirect(f'Testpagina mislukt: {e}')
        if path == '/update':
            return self._redirect(str(check_update(force=True)))
        self._redirect('')

    def log_message(self, *a):
        pass


# Mini-PDF (1 pagina, "PLUS TEST") - vooraf gegenereerd, ~600 bytes
_TEST_PDF_B64 = (
    'JVBERi0xLjQKMSAwIG9iajw8L1R5cGUvQ2F0YWxvZy9QYWdlcyAyIDAgUj4+ZW5kb2JqCjIgMCBv'
    'Ymo8PC9UeXBlL1BhZ2VzL0tpZHNbMyAwIFJdL0NvdW50IDE+PmVuZG9iagozIDAgb2JqPDwvVHlw'
    'ZS9QYWdlL1BhcmVudCAyIDAgUi9NZWRpYUJveFswIDAgNTk1IDg0Ml0vUmVzb3VyY2VzPDwvRm9u'
    'dDw8L0YxIDQgMCBSPj4+Pi9Db250ZW50cyA1IDAgUj4+ZW5kb2JqCjQgMCBvYmo8PC9UeXBlL0Zv'
    'bnQvU3VidHlwZS9UeXBlMS9CYXNlRm9udC9IZWx2ZXRpY2EtQm9sZD4+ZW5kb2JqCjUgMCBvYmo8'
    'PC9MZW5ndGggNjM+PnN0cmVhbQpCVCAvRjEgMzYgVGYgMTAwIDcwMCBUZCAoUExVUyBURVNUIC0g'
    'cHJpbnQtYWdlbnQpIFRqIEVUCmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDYKMDAwMDAwMDAwMCA2'
    'NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTIgMDAwMDAgbiAKMDAwMDAwMDEw'
    'MSAwMDAwMCBuIAowMDAwMDAwMjExIDAwMDAwIG4gCjAwMDAwMDAyODAgMDAwMDAgbiAKdHJhaWxl'
    'cjw8L1NpemUgNi9Sb290IDEgMCBSPj4Kc3RhcnR4cmVmCjM5MgolJUVPRgo=')


SYSTEMD_UNIT = f"""[Unit]
Description=PLUSLokaal Print-Agent
After=network-online.target cups.service

[Service]
ExecStart=/usr/bin/python3 {os.path.abspath(__file__)}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install():
    if os.geteuid() != 0:
        print('Draai met sudo:  sudo python3 pluslokaal_agent.py --install')
        sys.exit(1)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        save_config(dict(DEFAULTS))
    dest = '/opt/pluslokaal-agent/pluslokaal_agent.py'
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.abspath(__file__) != dest:
        shutil.copy2(os.path.abspath(__file__), dest)
        os.chmod(dest, 0o755)
    unit = SYSTEMD_UNIT.replace(os.path.abspath(__file__), dest)
    open('/etc/systemd/system/pluslokaal-agent.service', 'w').write(unit)
    subprocess.run(['systemctl', 'daemon-reload'])
    subprocess.run(['systemctl', 'enable', '--now', 'pluslokaal-agent'])
    print('Geinstalleerd. Open http://<pi-adres>:8080 om in te stellen.')


def main():
    if '--install' in sys.argv:
        install(); return
    log(f'PLUSLokaal Print-Agent v{AGENT_VERSION} gestart')
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    port = int(CFG.get('web_port') or 8080)
    srv = ThreadingHTTPServer(('0.0.0.0', port), Web)
    log(f'webinterface op poort {port}')
    srv.serve_forever()


if __name__ == '__main__':
    main()
