#!/usr/bin/env python3
"""PLUSLokaal Print-Agent voor Raspberry Pi.

Draait in de winkel, verbindt ZELF (uitgaand, HTTPS) met pluslokaal.com en print
opdrachten lokaal op de via USB aangesloten printers:
  - labelprinter  : rauwe TSPL/ZPL-bytes naar /dev/usb/lp*  (of een CUPS-raw-queue)
  - winkelprinter : PDF via CUPS (lp), met papierlade per formaat

Webinterface op poort 8080 (PLUS-huisstijl, beveiligd met admin-login die door
PLUSLokaal wordt gegenereerd en automatisch synct). Welkomstscherm bij eerste
gebruik: eerst update-check, dan winkel-sleutel plakken. Auto-update via
pluslokaal.com. Alleen Python-standaardbibliotheek.

Installatie (als root):   python3 pluslokaal_agent.py --install
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
from http import cookies as http_cookies
from urllib.parse import parse_qs, urlparse, quote as urlquote

AGENT_VERSION = '1.5.2'
CONFIG_DIR = '/etc/pluslokaal-agent'
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
SETUP_STATUS_FILE = os.path.join(CONFIG_DIR, 'setup-status.json')


def read_setup_status():
    """Voortgang van de eerste installatie (geschreven door het installatiescript). None = geen
    installatie bezig (klaar of niet van toepassing). Fail-safe: een status ouder dan 20 min wordt
    als 'klaar' behandeld, zodat de webinterface nooit op de voortgangspagina blijft hangen."""
    try:
        if time.time() - os.path.getmtime(SETUP_STATUS_FILE) > 1200:
            return None
        st = json.load(open(SETUP_STATUS_FILE))
        if st.get('done'):
            return None
        return st
    except Exception:
        return None
DEFAULTS = {
    'server': 'https://pluslokaal.com',
    'key': '',
    'label_device': '',
    'doc_queue': '',
    'tray_map': {},
    'poll_interval': 3,
    'web_port': 8080,
    'auto_update': True,
    'web_pass_sha256': '',        # gesynct vanaf PLUSLokaal (login: admin)
    'store_nummer': None,
    'store_naam': '',
    'default_copies': 1,          # standaard aantal kopieën voor documenten
}


def _ver_tuple(s):
    try:
        return tuple(int(x) for x in str(s or '0').split('.')[:3])
    except Exception:
        return (0,)


def primary_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '-'

state = {'last_ok': 0, 'online': False, 'jobs_done': 0, 'jobs_err': 0, 'last_error': '', 'log': []}
_log_lock = threading.Lock()
_sessions = {}                    # token -> vervaltijd
_login_fails = {'n': 0, 't': 0}


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
    os.chmod(CONFIG_FILE, 0o600)


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
        raise RuntimeError('geen labelprinter ingesteld op de Pi (webinterface)')
    if '/' in dev:
        with open(dev, 'wb') as fh:
            fh.write(payload)
        return
    p = subprocess.run(['lp', '-d', dev, '-o', 'raw', '-'], input=payload,
                       capture_output=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or b'lp faalde').decode(errors='replace')[:200])


def print_document(pdf: bytes, meta: dict):
    q = CFG.get('doc_queue')
    if not q:
        raise RuntimeError('geen winkelprinter (CUPS-queue) ingesteld op de Pi')
    copies = int(meta.get('copies') or CFG.get('default_copies') or 1)
    args = ['lp', '-d', q, '-n', str(max(1, copies))]
    media = (meta.get('media') or '').strip()
    if media:
        args += ['-o', f'media={media}']
    if (meta.get('orient') or '') == 'landscape':
        args += ['-o', 'landscape']
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


def set_hostname_for_store(nr):
    """Geef de Pi na koppeling een herkenbare hostnaam: PA-<winkelnummer>-PLUSLokaal (PA = Print-Agent)."""
    host = f'PA-{nr}-PLUSLokaal'
    try:
        cur = os.uname().nodename
        if cur == host:
            return
        subprocess.run(['hostnamectl', 'set-hostname', host], timeout=15, capture_output=True)
        # /etc/hosts bijwerken zodat sudo/hostname-lookups niet gaan klagen
        try:
            lines = open('/etc/hosts').read().splitlines()
            lines = [l for l in lines if not l.startswith('127.0.1.1')]
            lines.append(f'127.0.1.1 {host}')
            open('/etc/hosts', 'w').write('\n'.join(lines) + '\n')
        except Exception:
            pass
        log(f'hostnaam ingesteld: {host}')
    except Exception as e:
        log(f'hostnaam instellen mislukt: {e}')


def poll_once():
    info = {'hostname': os.uname().nodename,
            'printers': ([os.path.basename(CFG['label_device'])] if CFG.get('label_device') else [])
                        + ([CFG['doc_queue']] if CFG.get('doc_queue') else [])}
    res = api('/api/agent/poll', {'version': AGENT_VERSION, 'info': info})
    state['online'] = True
    state['last_ok'] = time.time()
    # Winkelnaam + weblogin-wachtwoord(hash) syncen vanaf PLUSLokaal
    changed = False
    st = res.get('store') or {}
    if st.get('nummer') and st.get('nummer') != CFG.get('store_nummer'):
        CFG['store_nummer'] = st['nummer']; CFG['store_naam'] = st.get('naam') or ''; changed = True
        set_hostname_for_store(st['nummer'])
    elif st.get('naam') and st.get('naam') != CFG.get('store_naam'):
        CFG['store_naam'] = st['naam']; changed = True
    wp = res.get('web_pass_sha256') or ''
    if wp and wp != CFG.get('web_pass_sha256'):
        CFG['web_pass_sha256'] = wp; changed = True
        log('weblogin-wachtwoord gesynct vanaf PLUSLokaal')
    # Vlag voor toegang op afstand vanuit PLUSLokaal (tunnel actief tot dit tijdstip)
    state['tunnel_until'] = float(res.get('web_tunnel_until') or 0)
    if changed:
        save_config(CFG)
    # Direct bijwerken zodra de server een nieuwere versie aanbiedt (niet pas na 6 uur).
    srv_ver = res.get('agent_version') or ''
    if (CFG.get('auto_update') and _ver_tuple(srv_ver) > _ver_tuple(AGENT_VERSION)
            and not state.get('updating')):
        state['updating'] = True
        log(f'nieuwe versie beschikbaar (v{srv_ver}) - bijwerken…')
        threading.Thread(target=check_update, daemon=True).start()
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


def poll_loop():
    while True:
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


# ─── Toegang op afstand (webinterface via PLUSLokaal) ─────────────────────────
# PLUSLokaal kan de webinterface van deze Pi op afstand tonen zonder open poorten:
# de server zet een verzoek klaar, deze agent haalt het op (uitgaand), voert het
# LOKAAL uit op 127.0.0.1 en stuurt het antwoord terug. Alleen actief als een
# beheerder in PLUSLokaal de interface opent (web_tunnel_until in de poll-respons).
def local_exec(req):
    import http.client
    port = int(CFG.get('web_port') or 8080)
    method = (req.get('method') or 'GET').upper()
    path = req.get('path') or '/'
    body = base64.b64decode(req.get('body_b64') or '')
    headers = {'X-PL-Tunnel': '1'}
    if req.get('ctype'):
        headers['Content-Type'] = req['ctype']
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    conn.request(method, path, body=(body or None), headers=headers)
    r = conn.getresponse()
    data = r.read()
    out = {'id': req['id'], 'status': r.status,
           'location': r.getheader('Location') or '',
           'ctype': r.getheader('Content-Type') or 'text/html; charset=utf-8',
           'body_b64': base64.b64encode(data).decode()}
    conn.close()
    return out


def tunnel_loop():
    while True:
        try:
            if not CFG.get('key') or time.time() > state.get('tunnel_until', 0):
                time.sleep(2); continue
            req = api('/api/agent/webpoll', {}, timeout=30)   # long-poll
            if not req or not req.get('id'):
                continue
            try:
                resp = local_exec(req)
            except Exception as e:
                resp = {'id': req['id'], 'status': 502, 'location': '',
                        'ctype': 'text/plain; charset=utf-8',
                        'body_b64': base64.b64encode(f'agent-tunnel fout: {e}'.encode()).decode()}
            try:
                api('/api/agent/webresult', resp)
            except Exception:
                pass
        except Exception:
            time.sleep(1)


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
            log(f'update: {AGENT_VERSION} -> {latest}; herstarten…')
            threading.Timer(1.0, lambda: os._exit(42)).start()
            return f'Bijgewerkt naar v{latest} - de agent herstart nu…'
        return f'Je hebt al de nieuwste versie (v{AGENT_VERSION}).'
    except Exception as e:
        return f'Update-controle mislukt: {e}'


def update_loop():
    while True:
        time.sleep(6 * 3600)
        log('update-controle: ' + str(check_update()))


# ─── Webinterface (PLUS-huisstijl) ────────────────────────────────────────────
# Echt PLUS-logo (wit wordmark) 1:1 zoals pluslokaal.com, ingebed zodat de
# webinterface ook zonder internet het juiste logo toont.
LOGO_URI = 'data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4KPHN2ZyBpZD0iTGFhZ18xIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZlcnNpb249IjEuMSIgdmlld0JveD0iMCAwIDYwOS4wOSAxMTMuNSI+CiAgPCEtLSBHZW5lcmF0b3I6IEFkb2JlIElsbHVzdHJhdG9yIDMwLjUuMSwgU1ZHIEV4cG9ydCBQbHVnLUluIC4gU1ZHIFZlcnNpb246IDIuMS40IEJ1aWxkIDMpICAtLT4KICA8ZGVmcz4KICAgIDxzdHlsZT4KICAgICAgLnN0MCB7CiAgICAgICAgZmlsbDogI2ZmZjsKICAgICAgfQogICAgPC9zdHlsZT4KICA8L2RlZnM+CiAgPHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTYwMiwxNWMwLDcuMjQtNS44NywxMy4xMS0xMy4xLDEzLjExLS43OCwwLTEuNTMtLjA3LTIuMjgtLjE5LTUuNDQtLjk2LTExLjE5LTEuNS0xNy4xNC0xLjUtNi42LDAtMTguMDguMTYtMTguMDgsNy4yMywwLDE0LjMsNTcuNjkuNzksNTcuNjksNDMuMDcsMCwyOS40LTI5LjA4LDM2Ljc5LTUzLjQ1LDM2Ljc5LTEyLjI5LDAtMjIuOTUtLjktMzQuMjUtMi45Mi02LjI0LTEuMDEtMTEuMDMtNi40Mi0xMS4wMy0xMi45NXYtMTcuNzZjMTEuMzMsNC43MiwyNS4xNiw3LjIzLDM3LjczLDcuMjMsOS45MSwwLDE4Ljg3LTIuMDQsMTguODctNy41NSwwLTE0Ljc4LTU3LjY5LTEuNDEtNTcuNjktNDQuMDIsMC0zMC4xOCwzMS40NC0zNS41Miw1Ni40NC0zNS41MiwxMS43OSwwLDI0Ljg0LDEuNDEsMzYuMzEsMy43N3YxMS4yM2gtLjAyWiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik0xNDguMjIsMTExLjRWMTQuNzdjMC03LjI0LDUuODctMTMuMTEsMTMuMTEtMTMuMTFoNTkuMzZjMTcuMTMsMCw0MC4yNSw4LjE3LDQwLjI1LDM3Ljczcy0xOC4zOSw0MC40LTQwLjQsNDAuNGgtMzAuMTh2MTguNDljMCw3LjI0LTUuODcsMTMuMS0xMy4xLDEzLjFoLTI5LjAzWk0xOTAuMzUsMjcuMTR2MjcuMmg4LjE3YzEwLjA2LDAsMjAuMjgtMS44OSwyMC4yOC0xNC4xNXMtMTAuMjItMTMuMDUtMjAuNDQtMTMuMDVoLTguMDJaIi8+CiAgPHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTQ4OC4yMyw2Ni4xMmMwLDM1LjUzLTIxLjg1LDQ3LjE3LTU1LjAyLDQ3LjE3LTMwLjY2LDAtNTQuNzEtMTIuNTgtNTQuNzEtNDQuOTdWMS42N2gyOS4wM2M3LjI0LDAsMTMuMTEsNS44NywxMy4xMSwxMy4xMXY1MS45OGMwLDEwLjM4LDIuOTksMTguMjQsMTMuMDUsMTguMjQsMTEsMCwxMy4zNi03LjM5LDEzLjM2LTE4LjA4VjEuNjdzMjguMDYsMCwyOC4wNiwwYzcuMjUsMCwxMy4xMSw1Ljg3LDEzLjExLDEzLjExbC4wMiw1MS4zNVoiLz4KICA8cGF0aCBmaWxsPSIjZmZmZmZmIiBkPSJNMjc5LjQ3LDExMS40VjEuNjdzMjkuMDIsMCwyOS4wMiwwYzcuMjQsMCwxMy4xMSw1Ljg3LDEzLjExLDEzLjExdjY0LjI0aDQyLjE1djE5LjI4YzAsNy4yNC01Ljg2LDEzLjEtMTMuMSwxMy4xaC03MS4xOFoiLz4KICA8cGF0aCBmaWxsPSIjZmZmZmZmIiBkPSJNNTMuMDMsMjYuNTV2MTcuNjhjMCw0Ljg4LTMuOTYsOC44NC04Ljg0LDguODRoLTE3LjY4QzExLjg3LDUzLjA2LDAsNDEuMTksMCwyNi41NVMxMS44Ny4wMywyNi41Mi4wM3MyNi41MiwxMS44NywyNi41MiwyNi41MiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik01My4wMyw4Ni45NXYtMTcuNjhjMC00Ljg4LTMuOTYtOC44My04Ljg0LTguODNoLTE3LjY4Yy0xNC42NCwwLTI2LjUyLDExLjg3LTI2LjUyLDI2LjUxczExLjg3LDI2LjUyLDI2LjUyLDI2LjUyLDI2LjUyLTExLjg3LDI2LjUyLTI2LjUyIi8+CiAgPHBhdGggZmlsbD0iI2ZmZmZmZiIgZD0iTTYwLjM2LDg2Ljk2di0xNy42OGMwLTQuODgsMy45Ni04Ljg0LDguODQtOC44NGgxNy42OGMxNC42NCwwLDI2LjUxLDExLjg3LDI2LjUxLDI2LjUxcy0xMS44NywyNi41Mi0yNi41MSwyNi41Mi0yNi41Mi0xMS44Ny0yNi41Mi0yNi41MiIvPgogIDxwYXRoIGZpbGw9IiNmZmZmZmYiIGQ9Ik02MC40MSwyNi41NXYxNy42OGMwLDQuODgsMy45Niw4Ljg0LDguODQsOC44NGgxNy42OGMxNC42NSwwLDI2LjUyLTExLjg3LDI2LjUyLTI2LjUxUzEwMS41Ny4wMyw4Ni45Mi4wM3MtMjYuNTEsMTEuODctMjYuNTEsMjYuNTIiLz4KPC9zdmc+'

CSS = """
:root{--green:#80bd1d;--green-d:#115013;--red:#dd350d;--bg:#f4f5f3;--text:#231f20;
--radius:18px 18px 18px 4px}
*{box-sizing:border-box}body{font-family:'Segoe UI',system-ui,sans-serif;margin:0;background:var(--bg);color:var(--text)}
header{background:var(--green);box-shadow:2px 1px 6px 0 rgba(51,51,51,.2);color:#fff;padding:0 20px;min-height:56px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px;text-decoration:none;flex-shrink:0}
.brand img{height:30px;width:auto;display:block}
.brand__sub{color:#fff;font-weight:700;font-size:.82rem;letter-spacing:.2px;opacity:.95;padding-left:12px;margin-left:2px;border-left:1px solid rgba(255,255,255,.35)}
.sub{font-size:.8rem;opacity:.9}
header .sp{flex:1}
header a{color:#fff;font-size:.8rem;text-decoration:none;font-weight:600}
header a:hover{text-decoration:underline}
main{max-width:780px;margin:22px auto;padding:0 14px}
.card{background:#fff;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 2px 12px rgba(0,0,0,.07)}
h2{margin:0 0 12px;font-size:.82rem;color:var(--green-d);text-transform:uppercase;letter-spacing:.05em}
label{display:block;font-size:.78rem;font-weight:700;color:#666;margin:12px 0 4px}
input,select{width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:var(--radius);font-size:.95rem;outline:none}
input:focus,select:focus{border-color:var(--green-d)}
button{background:var(--green);color:#fff;border:0;border-radius:var(--radius);padding:11px 20px;font-weight:800;cursor:pointer;margin-top:14px;font-size:.95rem}
button:hover{filter:brightness(1.05)}
button.ghost{background:#fff;color:var(--green-d);border:1.5px solid var(--green)}
button.plain{background:#fff;color:#666;border:1px solid #ccc}
.badge{display:inline-block;border-radius:12px;padding:3px 12px;color:#fff;font-size:.8rem;font-weight:700}
.ok{background:var(--green)}.err{background:var(--red)}
pre{background:#1d221c;color:#cde6a8;padding:12px;border-radius:10px;font-size:.72rem;max-height:280px;overflow:auto}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>*{flex:1;min-width:200px}
small{color:#777}
.msg{background:#eef6e1;color:var(--green-d);border-radius:10px;padding:10px 14px;font-weight:600;margin-bottom:14px}
.steps{counter-reset:s;list-style:none;padding:0;margin:0 0 6px}
.steps li{counter-increment:s;padding:8px 0 8px 40px;position:relative;line-height:1.5}
.steps li::before{content:counter(s);position:absolute;left:0;top:6px;width:26px;height:26px;border-radius:50%;
background:var(--green);color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center;font-size:.85rem}
.sysgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px 18px}
.sysgrid > div{font-size:.9rem}
.sysk{display:block;font-size:.7rem;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.04em;margin-bottom:2px}
.spinner{width:46px;height:46px;border-radius:50%;border:5px solid #e6ebdd;border-top-color:var(--green);margin:6px auto 8px;animation:spin 1s linear infinite}
.plspin{width:15px;height:15px;flex:0 0 15px;border-radius:50%;border:2px solid #e0cf86;border-top-color:#7a5b00;display:inline-block;animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.bar{height:12px;border-radius:20px;background:#e9eee0;overflow:hidden;margin-top:14px}
.bar__fill{height:100%;background:linear-gradient(90deg,var(--green),#a6d94b);border-radius:20px;transition:width .5s ease}
.chk{list-style:none;padding:0;margin:0}
.chk li{padding:9px 0 9px 32px;position:relative;color:#999;border-bottom:1px solid #f0f1ec}
.chk li:last-child{border-bottom:0}
.chk li::before{content:'';position:absolute;left:2px;top:11px;width:16px;height:16px;border-radius:50%;border:2px solid #d5dac8}
.chk li.busy{color:var(--text);font-weight:700}
.chk li.busy::before{border-color:var(--green);border-top-color:transparent;animation:spin .9s linear infinite}
.chk li.done{color:var(--green-d);font-weight:600}
.chk li.done::before{border-color:var(--green);background:var(--green)}
.chk li.done::after{content:'';position:absolute;left:7px;top:13px;width:4px;height:8px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
"""

HEAD = ("<!doctype html><html lang=nl><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>PLUSLokaal Print-Agent</title><style>" + CSS + "</style></head><body>"
        "<div id=plsetup style=\"display:none;position:sticky;top:0;z-index:999;background:#fff7e0;"
        "border-bottom:1px solid #ecd98a;color:#7a5b00;padding:9px 16px;font-size:.85rem;font-weight:700;"
        "align-items:center;gap:9px\"><span class=plspin></span><span id=plsetuptxt></span></div>"
        "<script>(function(){"
        "function u(d){var b=document.getElementById('plsetup');if(!b)return;"
        "if(d&&d.done===false){b.style.display='flex';"
        "document.getElementById('plsetuptxt').textContent='Nog even bezig op de achtergrond: '"
        "+(d.step||'installeren')+' ('+(d.pct||0)+'%)';}else{b.style.display='none';}}"
        "function p(){fetch('/setup-status').then(function(r){return r.json();}).then(u).catch(function(){});}"
        "setInterval(p,2500);p();})();</script>")


def esc(s):
    return str(s if s is not None else '').replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;')


def header_html():
    naam = CFG.get('store_naam') or ''
    nr = CFG.get('store_nummer')
    winkel = f'PLUS {esc(naam)} ({nr})' if nr else 'nog niet gekoppeld'
    out = (f"<header><span class=brand><img src='{LOGO_URI}' alt='PLUS'>"
           f"<span class=brand__sub>Lokaal</span></span>"
           f"<span class=sub>Print-Agent v{AGENT_VERSION} · {winkel}</span><span class=sp></span>")
    if is_coupled():
        out += "<a href='/logout'>Uitloggen</a>"
    return out + "</header>"


def is_coupled():
    """Gekoppeld zodra er een sleutel is (winkel bekend). Het weblogin-wachtwoord
    synct daarna vanzelf vanaf PLUSLokaal."""
    return bool(CFG.get('key'))


def login_required_now():
    """Login pas afdwingen zodra het wachtwoord vanaf PLUSLokaal is binnengekomen,
    zodat je vlak na het koppelen niet buitengesloten raakt."""
    return bool(CFG.get('web_pass_sha256'))


def check_pass(pw):
    return bool(CFG.get('web_pass_sha256')) and \
        hashlib.sha256((pw or '').encode()).hexdigest() == CFG['web_pass_sha256']


def new_session():
    tok = base64.urlsafe_b64encode(os.urandom(24)).decode()
    _sessions[tok] = time.time() + 12 * 3600
    return tok


def valid_session(handler):
    c = http_cookies.SimpleCookie(handler.headers.get('Cookie') or '')
    tok = c['plagent'].value if 'plagent' in c else ''
    exp = _sessions.get(tok)
    return bool(exp and exp > time.time())


def is_tunnel(handler):
    """Verzoek dat via de PLUSLokaal-tunnel binnenkomt: uitsluitend vanaf localhost
    (de tunnel-loop verbindt naar 127.0.0.1) met de tunnel-header. Zo kan niemand op
    het winkelnetwerk de login omzeilen door zelf die header mee te sturen."""
    return bool(handler.headers.get('X-PL-Tunnel')) and \
        handler.client_address[0] in ('127.0.0.1', '::1')


def authed(handler):
    return valid_session(handler) or is_tunnel(handler)


LOGIN_PAGE = """{header}
<main><div class=card style="max-width:420px;margin:40px auto;">
<h2>Inloggen</h2>{msg}
<p><small>Log in met de gegevens uit PLUSLokaal (Beheer &rarr; Filialen &rarr; Print-agent).</small></p>
<form method=post action=/login>
  <label>Gebruikersnaam</label><input name=user value="admin" readonly>
  <label>Wachtwoord</label><input name=pw type=password autofocus>
  <button>Inloggen</button>
</form></div></main></body></html>"""

SETUP_PAGE = """{header}
<main>
<div class="msg" style="background:#eaf5da;color:#3d6b0f;display:flex;align-items:center;gap:8px">
  <b>&#10003;</b> Deze Pi is bereikbaar op <b>http://{ip}/</b> - vul hieronder de winkel-sleutel in om te koppelen.</div>
<div class=card><h2>Welkom - deze Pi instellen</h2>{msg}
<ol class=steps>
  <li><b>Controleer eerst op een nieuwere versie</b> van de agent:<br>
    <form method=post action=/update style="display:inline"><button class=ghost>&#8635; Zoek naar updates</button></form></li>
  <li><b>Plak de agent-sleutel</b> van deze winkel (PLUSLokaal &rarr; Beheer &rarr; Filialen &rarr; winkel &rarr; Print-agent):
    <form method=post action=/setup>
      <label>Agent-sleutel</label><input name=key value="{key}" placeholder="plak hier de sleutel" autofocus>
      <label>Server <small>(normaal laten staan)</small></label><input name=server value="{server}">
      <button>Koppelen</button>
    </form></li>
</ol>
<p><small>Na het koppelen verschijnt de winkelnaam bovenin, wordt de login automatisch gesynct
en kun je de printers instellen.</small></p></div>
</main></body></html>"""

MAIN_PAGE = """{header}
<main>
{msg}
<div class=card><h2>Systeem</h2>
  <div class=sysgrid>
    <div><span class=sysk>Winkel</span>{winkel}</div>
    <div><span class=sysk>Verbinding</span><span class="badge {online_cls}">{online_txt}</span></div>
    <div><span class=sysk>Hostnaam</span>{hostname}</div>
    <div><span class=sysk>IP-adres</span>{ip}</div>
    <div><span class=sysk>Agent-versie</span>v{ver}</div>
    <div><span class=sysk>Jobs</span>{done} geprint · {err} fout</div>
  </div>
  {lasterr_html}</div>
<div class=card><h2>Printers &amp; instellingen</h2>
<form method=post action=/save>
  <div class=row>
    <div><label>Labelprinter (USB)</label><select name=label_device><option value="">- uit -</option>{label_opts}</select></div>
    <div><label>Winkelprinter (CUPS-queue)</label><select name=doc_queue><option value="">- uit -</option>{queue_opts}</select></div>
  </div>
  <small>{usb_count} USB-printer(s) gevonden · <a href="/" >vernieuwen</a> · nieuwe USB-printer eerst toevoegen via <a href="http://{host}:631" target=_blank>CUPS (:631)</a></small>
  <label>Papierlade per lade-code <small>(bv. tray-2=Tray2, tray-3=Tray3)</small></label>
  <input name=tray_map value="{tray_map}" placeholder="tray-2=Tray2, tray-3=Tray3">
  <div class=row>
    <div><label>Standaard aantal kopieën</label><input name=default_copies value="{copies}"></div>
    <div><label>Poll-interval (sec)</label><input name=poll_interval value="{poll}"></div>
    <div><label>Auto-update</label><select name=auto_update><option value=1 {au1}>aan</option><option value=0 {au0}>uit</option></select></div>
  </div>
  <details style="margin-top:12px"><summary style="cursor:pointer;color:var(--green-d);font-weight:700">Geavanceerd (server &amp; sleutel)</summary>
    <div class=row>
      <div><label>Server</label><input name=server value="{server}"></div>
      <div><label>Agent-sleutel</label><input name=key value="{key}"></div>
    </div></details>
  <button>Opslaan</button>
</form></div>
<div class=card><h2>Testen &amp; onderhoud</h2>
  <form method=post action=/test_label style="display:inline"><button class=ghost>Testlabel</button></form>
  <form method=post action=/test_doc style="display:inline"><button class=ghost>Testpagina (A4)</button></form>
  <form method=post action=/update style="display:inline"><button class=ghost>&#8635; Zoek naar updates</button></form>
  <form method=post action=/restart style="display:inline"><button class=plain>Agent herstarten</button></form></div>
<div class=card><h2>Log</h2><pre>{log}</pre></div>
</main></body></html>"""


INSTALL_PAGE = """{header}
<main>
<div class=card style="text-align:center">
  <div class="spinner"></div>
  <h2 style="font-size:1.15rem;color:var(--green-d);text-transform:none;letter-spacing:0;margin:6px 0 6px">Een ogenblik geduld</h2>
  <p style="margin:0 0 4px;font-weight:700">Bezig met installeren…</p>
  <p><small id=stepnow>{step}</small></p>
  <div class=bar><div class=bar__fill id=barfill style="width:{pct}%"></div></div>
  <div id=barpct style="font-size:.8rem;color:#777;margin-top:4px">{pct}%</div>
</div>
<div class=card>
  <h2>Wat er gebeurt</h2>
  <ul class=chk id=chk>
    <li data-p=5>Systeem voorbereiden</li>
    <li data-p=20>Print-agent installeren</li>
    <li data-p=50>Printersoftware (CUPS) installeren</li>
    <li data-p=75>Beheer-software installeren</li>
    <li data-p=100>Afronden</li>
  </ul>
  <p><small>Deze pagina ververst automatisch. Zodra alles klaar is kun je hier de winkel koppelen.</small></p>
</div>
</main>
<script>
function upd(d){{
  if(d.done){{ location.reload(); return; }}
  var p=d.pct||0;
  document.getElementById('barfill').style.width=p+'%';
  document.getElementById('barpct').textContent=p+'%';
  if(d.step) document.getElementById('stepnow').textContent=d.step;
  document.querySelectorAll('#chk li').forEach(function(li){{
    var t=+li.dataset.p;
    li.className = p>=t ? 'done' : (p>=(t-30)&&p< t ? 'busy' : '');
  }});
}}
function poll(){{ fetch('/setup-status').then(r=>r.json()).then(upd).catch(function(){{}}); }}
setInterval(poll,1500); poll();
</script>
</body></html>"""


class Web(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _html(self, body, code=200, set_cookie=None):
        data = (HEAD + body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        if set_cookie:
            self.send_header('Set-Cookie', set_cookie)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, loc='/', msg='', set_cookie=None):
        self.send_response(303)
        self.send_header('Location', loc + (('?msg=' + urlquote(msg)) if msg else ''))
        if set_cookie:
            self.send_header('Set-Cookie', set_cookie)
        self.end_headers()

    def _msg(self):
        q = parse_qs(urlparse(self.path).query)
        m = (q.get('msg') or [''])[0]
        return f"<div class=msg>{esc(m)}</div>" if m else ''

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/logout':
            c = http_cookies.SimpleCookie(self.headers.get('Cookie') or '')
            if 'plagent' in c:
                _sessions.pop(c['plagent'].value, None)
            return self._redirect('/', set_cookie='plagent=; Max-Age=0; Path=/')
        if path == '/setup-status':
            # Voortgang van de achtergrond-installatie (voor de statusbalk bovenaan elke pagina).
            return self._json(read_setup_status() or {'done': True})
        if not is_coupled():
            return self._html(SETUP_PAGE.format(header=header_html(), msg=self._msg(), ip=esc(primary_ip()),
                                                key=esc(CFG.get('key', '')), server=esc(CFG.get('server', ''))))
        if login_required_now() and not authed(self):
            return self._html(LOGIN_PAGE.format(header=header_html(), msg=self._msg()))
        usb_devs = usb_label_devices()
        label_opts = ''.join(f'<option value="{esc(d)}" {"selected" if CFG.get("label_device")==d else ""}>{esc(d)}</option>'
                             for d in usb_devs + cups_queues())
        queue_opts = ''.join(f'<option value="{esc(d)}" {"selected" if CFG.get("doc_queue")==d else ""}>{esc(d)}</option>'
                             for d in cups_queues())
        tray = ', '.join(f'{k}={v}' for k, v in (CFG.get('tray_map') or {}).items())
        with _log_lock:
            logtxt = esc('\n'.join(state['log'][-120:]))
        host = (self.headers.get('Host') or 'pi').split(':')[0]
        naam = CFG.get('store_naam') or ''
        nr = CFG.get('store_nummer')
        winkel = f'PLUS {esc(naam)} ({nr})' if nr else 'nog niet gekoppeld'
        lasterr = state['last_error']
        self._html(MAIN_PAGE.format(
            header=header_html(), msg=self._msg(),
            server=esc(CFG.get('server', '')), key=esc(CFG.get('key', '')),
            online_cls='ok' if state['online'] else 'err',
            online_txt='verbonden' if state['online'] else 'geen verbinding',
            done=state['jobs_done'], err=state['jobs_err'],
            lasterr_html=(f"<p><small>Laatste fout: {esc(lasterr)}</small></p>" if lasterr else ''),
            winkel=winkel, hostname=esc(os.uname().nodename), ip=esc(primary_ip()), ver=AGENT_VERSION,
            usb_count=len(usb_devs), copies=CFG.get('default_copies', 1),
            label_opts=label_opts, queue_opts=queue_opts, tray_map=esc(tray),
            poll=CFG.get('poll_interval', 3),
            au1='selected' if CFG.get('auto_update') else '',
            au0='' if CFG.get('auto_update') else 'selected',
            host=esc(host), log=logtxt))

    def do_POST(self):
        global CFG
        ln = int(self.headers.get('Content-Length') or 0)
        form = parse_qs(self.rfile.read(ln).decode())
        g = lambda k, d='': (form.get(k) or [d])[0].strip()
        path = urlparse(self.path).path

        if path == '/login':
            # simpele brute-force-rem: na 5 missers 30s wachten
            if _login_fails['n'] >= 5 and time.time() - _login_fails['t'] < 30:
                return self._redirect('/', 'Te veel pogingen - wacht 30 seconden.')
            if check_pass(g('pw')):
                _login_fails['n'] = 0
                tok = new_session()
                return self._redirect('/', set_cookie=f'plagent={tok}; Path=/; HttpOnly; SameSite=Lax')
            _login_fails['n'] += 1; _login_fails['t'] = time.time()
            return self._redirect('/', 'Onjuist wachtwoord.')

        if path == '/setup' and not is_coupled():
            CFG['key'] = g('key')
            CFG['server'] = g('server', DEFAULTS['server']) or DEFAULTS['server']
            save_config(CFG)
            log('sleutel ingesteld via welkomstscherm')
            try:
                poll_once()   # meteen koppelen → winkelnaam + weblogin binnenhalen
            except Exception as e:
                return self._redirect('/', f'Koppelen mislukt: {e}')
            # Meteen ingelogd doorsturen naar de volledige instelpagina (printers e.d.)
            tok = new_session()
            return self._redirect('/', f"Gekoppeld aan {CFG.get('store_naam') or 'de winkel'}! "
                                       'Stel hieronder de printers in.',
                                  set_cookie=f'plagent={tok}; Path=/; HttpOnly; SameSite=Lax')

        if path == '/update' and (not is_coupled() or authed(self) or not login_required_now()):
            return self._redirect('/', str(check_update(force=True)))

        # vanaf hier: alleen ingelogd (of via de PLUSLokaal-tunnel, of vlak na koppelen)
        if not (is_coupled() and (authed(self) or not login_required_now())):
            return self._redirect('/', 'Log eerst in.')

        if path == '/save':
            CFG['server'] = g('server', DEFAULTS['server']) or DEFAULTS['server']
            CFG['key'] = g('key') or CFG.get('key')
            CFG['label_device'] = g('label_device')
            CFG['doc_queue'] = g('doc_queue')
            try:
                CFG['poll_interval'] = max(2, int(g('poll_interval', '3')))
            except ValueError:
                CFG['poll_interval'] = 3
            try:
                CFG['default_copies'] = max(1, int(g('default_copies', '1')))
            except ValueError:
                CFG['default_copies'] = 1
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
            return self._redirect('/', 'Opgeslagen.')
        if path == '/test_label':
            try:
                tspl = b'SIZE 45 mm,40 mm\r\nGAP 3 mm,0\r\nCLS\r\nTEXT 30,40,"3",0,1,1,"PLUS TEST"\r\nBARCODE 30,90,"EAN13",60,1,0,2,2,"871040014582"\r\nPRINT 1\r\n'
                print_label(tspl)
                return self._redirect('/', 'Testlabel verstuurd.')
            except Exception as e:
                return self._redirect('/', f'Testlabel mislukt: {e}')
        if path == '/test_doc':
            try:
                pdf = base64.b64decode(_TEST_PDF_B64)
                print_document(pdf, {'media': 'iso_a4_210x297mm', 'copies': 1, 'job_name': 'pluslokaal-test'})
                return self._redirect('/', 'Testpagina verstuurd.')
            except Exception as e:
                return self._redirect('/', f'Testpagina mislukt: {e}')
        if path == '/restart':
            threading.Timer(0.5, lambda: os._exit(0)).start()
            return self._redirect('/', 'Agent herstart…')
        self._redirect('/')

    def log_message(self, *a):
        pass


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


SYSTEMD_UNIT = """[Unit]
Description=PLUSLokaal Print-Agent
After=network-online.target cups.service

[Service]
ExecStart=/usr/bin/python3 /opt/pluslokaal-agent/pluslokaal_agent.py
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
    open('/etc/systemd/system/pluslokaal-agent.service', 'w').write(SYSTEMD_UNIT)
    subprocess.run(['systemctl', 'daemon-reload'])
    subprocess.run(['systemctl', 'enable', '--now', 'pluslokaal-agent'])
    print('Geinstalleerd. Open http://<pi-adres>:8080 om in te stellen.')


def main():
    if '--install' in sys.argv:
        install(); return
    if '--check-update' in sys.argv:
        print(check_update(force=True)); return
    log(f'PLUSLokaal Print-Agent v{AGENT_VERSION} gestart')
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=update_loop, daemon=True).start()
    threading.Thread(target=tunnel_loop, daemon=True).start()
    # Webinterface op poort 80 (gewoon het IP intypen - handig, ook via RMM) én 8080 (terugval).
    port = int(CFG.get('web_port') or 8080)
    try:
        srv80 = ThreadingHTTPServer(('0.0.0.0', 80), Web)
        threading.Thread(target=srv80.serve_forever, daemon=True).start()
        log('webinterface op poort 80 (http://<pi-adres>/)')
    except OSError as e:
        log(f'poort 80 niet beschikbaar ({e}) - alleen {port}')
    srv = ThreadingHTTPServer(('0.0.0.0', port), Web)
    log(f'webinterface op poort {port}')
    srv.serve_forever()


if __name__ == '__main__':
    main()
