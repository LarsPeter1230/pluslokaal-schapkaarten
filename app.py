from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text, func
from itsdangerous import URLSafeTimedSerializer
import os, json, io, secrets, time, smtplib, ssl, threading, re, subprocess, shutil
from datetime import datetime, timedelta

# Tijdzone van de app op Nederland zetten, zodat alle tijdstempels (logs, kaarten, e-mails) de
# Nederlandse tijd tonen. Zomer-/wintertijd (CEST/CET) gaat automatisch goed.
os.environ['TZ'] = 'Europe/Amsterdam'
try:
    time.tzset()
except Exception:
    pass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.utils import formataddr
from PIL import Image, ImageDraw, ImageFont
from werkzeug.middleware.proxy_fix import ProxyFix
import sharedstate   # proces-overstijgende state (print/W2P-jobs, login-rate-limiting) voor multi-worker

# 'idna'-codec vooraf laden in de HOOFD-thread. Werkzeug idna-encodeert de hostnaam bij het binden van de
# URL-map; de eerste `codecs.lookup('idna')` vanuit een gunicorn-worker-THREAD kan racen en faalt dan met
# "unknown encoding: idna" (een 500 op willekeurige pagina's, alleen via het echte domein - niet op
# 127.0.0.1, want een IP wordt niet idna-geëncodeerd). Eén keer opwarmen registreert 'm globaal.
import codecs as _codecs, encodings.idna  # noqa: F401
try:
    _codecs.lookup('idna')
except Exception:
    pass

app = Flask(__name__)
# Achter de Cloudflare-tunnel: laat Flask het echte schema (https) en client-IP uit de proxy-headers lezen,
# zodat o.a. de Secure-sessiecookie correct wordt gezet.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ─── SECURE SECRET KEY ────────────────────────────────────────────────────────
_KEY_FILE = os.path.join(os.path.dirname(__file__), '.secret_key')
try:
    _sk = open(_KEY_FILE).read().strip()
    if len(_sk) < 32 or _sk == 'pluslokaal-secret-2024':
        raise ValueError
    app.secret_key = _sk
except Exception:
    app.secret_key = secrets.token_hex(32)
    try:
        open(_KEY_FILE, 'w').write(app.secret_key)
    except Exception:
        pass

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pluslokaal.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['EXPORT_FOLDER'] = os.path.join(os.path.dirname(__file__), 'static', 'export')
os.makedirs(app.config['EXPORT_FOLDER'], exist_ok=True)
# Bovengrens op de request-body: voorkomt dat één upload (bv. een enorme screenshot) het geheugen/
# de schijf vult. Ruim genoeg voor legitieme posts (screenshots ~3,5 MB, avatars ~3 MB).
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Versie van de applicatie - getoond in de footer; klikbaar naar de changelog (/changelog).
APP_VERSION = '2.37.0'

# Ingelogd blijven tot wachtwoordwijziging: langlevende, permanente sessiecookie (overleeft het
# sluiten van het tabblad/de browser). De secret key staat vast in .secret_key, dus herstarts loggen
# niemand uit. Ongeldig maken gebeurt via de 'pwv'-marker (zie _pw_marker/get_current_user).
app.permanent_session_lifetime = timedelta(days=30)   # een maand ingelogd blijven
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
# Nette cookie-kenmerken zodat browsers (Edge/Chrome tracking-preventie) de cookie niet vroegtijdig
# opruimen: eerste-partij, alleen via HTTPS, niet leesbaar vanuit JS.
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Op STATISCHE bestanden geen sessie-cookie meesturen. Anders zet de sessie-refresh op elke asset een
# Set-Cookie → dan mag een gedeelde cache (Cloudflare) die assets niet cachen. Zonder cookie kan Cloudflare
# ze aan de rand cachen → ze hoeven de server/tunnel niet eens te raken (sneller + veel betrouwbaarder;
# lost 'foto's/CSS laden soms niet in één keer' op). De sessie zelf (30-dagen sliding window) blijft gelijk.
from flask.sessions import SecureCookieSessionInterface as _SCSI
class _NoCookieOnStatic(_SCSI):
    def should_set_cookie(self, app, session):
        try:
            if (request.path or '').startswith('/static/'):
                return False
        except Exception:
            pass
        return super().should_set_cookie(app, session)
app.session_interface = _NoCookieOnStatic()

db = SQLAlchemy(app)

# Met threaded=True kunnen meerdere requests tegelijk de SQLite-db raken. WAL + een ruime busy_timeout
# voorkomen "database is locked": lezers blokkeren schrijvers niet (WAL) en een schrijver wacht netjes
# tot 5s i.p.v. meteen te falen. Eenmalig per connectie gezet.
from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _SAEngine
@_sa_event.listens_for(_SAEngine, "connect")
def _sqlite_pragmas(dbapi_conn, _rec):
    try:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
    except Exception:
        pass

# ─── MODELLEN ─────────────────────────────────────────────────────────────────
class User(db.Model):
    id           = db.Column(db.Integer,     primary_key=True)
    username     = db.Column(db.String(100), unique=True, nullable=False)
    password     = db.Column(db.String(256), nullable=False)
    role         = db.Column(db.String(50),  nullable=False)
    filiaal      = db.Column(db.Integer,     nullable=False)
    filiaal_naam = db.Column(db.String(100), nullable=True)
    avatar       = db.Column(db.Text,        nullable=True)   # data-URL profielfoto
    email        = db.Column(db.String(200), nullable=True)
    # Labels-module: IP-toegangsbeleid + accountstatus
    access_policy        = db.Column(db.String(20),  default='anywhere')  # anywhere|ip_login|ip_print
    allowed_ips          = db.Column(db.Text,        nullable=True)       # eigen IP-lijst (overschrijft winkel)
    approved             = db.Column(db.Boolean,     default=True)
    must_change_password = db.Column(db.Boolean,     default=False)
    # Twee-factor-authenticatie (TOTP). Verplicht voor superadmins.
    mfa_secret           = db.Column(db.String(64),  nullable=True)
    mfa_enabled          = db.Column(db.Boolean,     default=False)
    show_tour            = db.Column(db.Boolean,     default=False)   # rondleiding tonen na login
    # Portaal: gekoppelde pluslokaal.nl-inloggegevens (wachtwoord versleuteld met Fernet, nooit getoond)
    portaal_user         = db.Column(db.String(200), nullable=True)
    portaal_pass_enc     = db.Column(db.Text,        nullable=True)
    portaal_status       = db.Column(db.String(20),  default='none')  # none|ok|fout
    portaal_checked      = db.Column(db.DateTime,    nullable=True)
    notify_w2p_fail      = db.Column(db.Boolean,     default=False)  # mail bij mislukte W2P-sync/download

class Filiaal(db.Model):
    id       = db.Column(db.Integer,     primary_key=True)
    nummer   = db.Column(db.Integer,     unique=True, nullable=False)   # winkelnummer (= store_number)
    naam     = db.Column(db.String(120), nullable=True)
    # Labels-module: winkelprinter-config + toegestane (publieke) IP's
    printer_name     = db.Column(db.String(120), nullable=True)
    printer_ip       = db.Column(db.String(64),  nullable=True)
    printer_port     = db.Column(db.Integer,     default=9100)
    printer_dpi      = db.Column(db.Integer,     default=300)
    printer_protocol = db.Column(db.String(16),  default='tspl')   # tspl|zpl|epl|text
    printer_label_w  = db.Column(db.Float,       default=45.0)
    printer_label_h  = db.Column(db.Float,       default=40.0)
    printer_offset_x = db.Column(db.Integer,     default=0)
    printer_offset_y = db.Column(db.Integer,     default=0)
    printer_rotation = db.Column(db.Integer,     default=0)
    allowed_ips      = db.Column(db.Text,        nullable=True)     # publieke IP's (app achter Cloudflare)
    login_hint       = db.Column(db.Text,        nullable=True)     # hint bij mislukte login vanaf dit winkel-IP
    # Winkelprinter voor schapkaarten/scankaarten (kantoorprinter, IPP - A3/A4)
    doc_printer_name  = db.Column(db.String(120), nullable=True)
    doc_printer_ip    = db.Column(db.String(64),  nullable=True)
    doc_printer_port  = db.Column(db.Integer,     default=631)
    doc_printer_trays = db.Column(db.Text,        nullable=True)    # JSON {formaat: 'tray-3', ...}
    print_only        = db.Column(db.Boolean,     default=False)    # download-knop verbergen (tenzij printer offline/fout)
    # Print-agent (Raspberry Pi in de winkel): verbindt ZELF naar buiten (geen firewall-gaten),
    # haalt printopdrachten op en stuurt ze naar de USB-printers. agent_key = geheime winkel-sleutel.
    agent_key         = db.Column(db.String(64),  nullable=True, index=True)
    agent_seen        = db.Column(db.DateTime,    nullable=True)    # laatste poll (online = < 2 min geleden)
    agent_version     = db.Column(db.String(20),  nullable=True)
    agent_info        = db.Column(db.Text,        nullable=True)    # JSON (hostname, printers, ip)
    agent_web_pass    = db.Column(db.String(40),  nullable=True)    # login (admin/…) voor de Pi-webinterface

class Setting(db.Model):
    key   = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, nullable=True)

class AgentJob(db.Model):
    """Printopdracht voor een winkel-print-agent (Raspberry Pi). De agent pollt en voert uit."""
    id         = db.Column(db.Integer, primary_key=True)
    filiaal    = db.Column(db.Integer, nullable=False, index=True)
    kind       = db.Column(db.String(16), nullable=False)          # label | document
    payload    = db.Column(db.Text, nullable=False)                # base64 (TSPL-bytes of PDF)
    meta_json  = db.Column(db.Text, nullable=True)                 # {media,source,orient,copies,job_name,label}
    status     = db.Column(db.String(16), default='pending', index=True)  # pending|fetched|done|error|cancelled
    error      = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    done_at    = db.Column(db.DateTime, nullable=True)

class AgentWebReq(db.Model):
    """Eén browserverzoek voor de webinterface van een print-agent, doorgegeven via de
    DB zodat het over gunicorn-workers heen werkt. De agent haalt 'pending' op, voert het
    lokaal uit en schrijft het antwoord terug ('answered')."""
    id          = db.Column(db.Integer, primary_key=True)
    filiaal     = db.Column(db.Integer, nullable=False, index=True)
    req_id      = db.Column(db.String(32), nullable=False, index=True)
    method      = db.Column(db.String(8), nullable=False)
    path        = db.Column(db.Text, nullable=False)
    ctype       = db.Column(db.String(120), nullable=True)
    body        = db.Column(db.Text, nullable=True)                # base64
    status      = db.Column(db.String(12), default='pending', index=True)  # pending|answered
    resp_status = db.Column(db.Integer, nullable=True)
    resp_ctype  = db.Column(db.String(120), nullable=True)
    resp_loc    = db.Column(db.Text, nullable=True)
    resp_body   = db.Column(db.Text, nullable=True)                # base64
    created_at  = db.Column(db.DateTime, default=datetime.now, index=True)

# ─── LABELS-MODULE (geïntegreerd uit PLUS Label Manager) ──────────────────────
class Product(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    filiaal    = db.Column(db.Integer, nullable=False)          # winkelnummer (scope)
    name       = db.Column(db.String(200), nullable=False)
    barcode    = db.Column(db.String(64),  nullable=True)
    barcode_type = db.Column(db.String(16), default='ean13')   # ean13|ean8|code128
    price      = db.Column(db.Float,       nullable=True)
    sku        = db.Column(db.String(64),  nullable=True)
    category   = db.Column(db.String(120), nullable=True)
    active     = db.Column(db.Boolean,     default=True)
    created_at = db.Column(db.DateTime,    default=datetime.now)
    updated_at = db.Column(db.DateTime,    default=datetime.now, onupdate=datetime.now)

class LabelFormat(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(120), nullable=False)
    width_mm  = db.Column(db.Float, nullable=False)
    height_mm = db.Column(db.Float, nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    filiaal   = db.Column(db.Integer, nullable=True)            # null = globaal
    created_at = db.Column(db.DateTime, default=datetime.now)

class LabelJob(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    filiaal    = db.Column(db.Integer, nullable=False)
    created_by = db.Column(db.String(100), nullable=True)       # username
    format_id  = db.Column(db.Integer, nullable=True)
    name       = db.Column(db.String(200), nullable=True)
    status     = db.Column(db.String(20), default='concept')
    items_json = db.Column(db.Text, default='[]')              # [{name,barcode,barcode_type,price,old_price,qty}]
    created_at = db.Column(db.DateTime, default=datetime.now)
    printed_at = db.Column(db.DateTime, nullable=True)
    price_unit = db.Column(db.String(30), default='stuk')
    extra_line1 = db.Column(db.String(120), nullable=True)
    extra_line2 = db.Column(db.String(120), nullable=True)
    show_date  = db.Column(db.Boolean, default=False)
    show_logo  = db.Column(db.Boolean, default=True)

class Design(db.Model):
    """Vrij ontwerp uit de Designer (Bèta): een canvas met vrije elementen (tekst/foto/barcode/vorm/
    icoon), voor een label of voor papier (A-formaat). `data_json` = het volledige canvas."""
    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(200), default='Naamloos ontwerp')
    kind       = db.Column(db.String(20),  default='paper')   # 'label' | 'paper'
    w_mm       = db.Column(db.Float, nullable=False)
    h_mm       = db.Column(db.Float, nullable=False)
    data_json  = db.Column(db.Text, default='{"bg":"#ffffff","elements":[]}')
    thumb      = db.Column(db.String(200), nullable=True)     # png in static/export
    username   = db.Column(db.String(100), nullable=True)
    filiaal    = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

class AuditLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    user_id    = db.Column(db.Integer, nullable=True)
    username   = db.Column(db.String(100), nullable=True)
    filiaal    = db.Column(db.Integer, nullable=True)
    action     = db.Column(db.String(80), nullable=False)
    detail     = db.Column(db.String(500), nullable=True)
    ip         = db.Column(db.String(64), nullable=True)

class Feedback(db.Model):
    """Een melding uit het feedback-widget: probleem, suggestie of idee.
    Werkt als een klein ticketsysteem (status, gelezen-markering, logboek)."""
    id           = db.Column(db.Integer, primary_key=True)
    created_at   = db.Column(db.DateTime, default=datetime.now)
    ftype        = db.Column(db.String(20),  default='probleem')   # probleem|suggestie|idee
    status       = db.Column(db.String(20),  default='nieuw')      # nieuw|in_behandeling|opgelost|afgewezen
    is_read      = db.Column(db.Boolean,     default=False)
    title        = db.Column(db.String(200), nullable=True)
    message      = db.Column(db.Text,        nullable=False)
    page_url     = db.Column(db.String(600), nullable=True)        # waar de melding vandaan kwam
    screenshot   = db.Column(db.Text,        nullable=True)        # data-URL (auto of geüpload)
    user_id      = db.Column(db.Integer,     nullable=True)
    username     = db.Column(db.String(100), nullable=True)
    user_email   = db.Column(db.String(200), nullable=True)
    user_role    = db.Column(db.String(50),  nullable=True)
    filiaal      = db.Column(db.Integer,     nullable=True)
    filiaal_naam = db.Column(db.String(120), nullable=True)
    ip           = db.Column(db.String(64),  nullable=True)
    user_agent   = db.Column(db.String(400), nullable=True)
    log_json     = db.Column(db.Text,        default='[]')         # [{at, who, text}] statuswijzigingen/notities

class FeedbackMessage(db.Model):
    """Een bericht in het gesprek onder een melding (heen-en-weer tussen melder en beheer)."""
    id           = db.Column(db.Integer, primary_key=True)
    feedback_id  = db.Column(db.Integer, index=True, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.now)
    author_id    = db.Column(db.Integer,     nullable=True)
    author_name  = db.Column(db.String(100), nullable=True)
    is_admin     = db.Column(db.Boolean,     default=False)        # bericht van een beheerder?
    body         = db.Column(db.Text,        nullable=False)
    read_by_user = db.Column(db.Boolean,     default=False)        # heeft de melder dit gezien?
    read_by_admin= db.Column(db.Boolean,     default=False)        # heeft een beheerder dit gezien?

class KbArticle(db.Model):
    """Kennisbank/wiki-artikel. Inhoud is Markdown; door superadmin te bewerken en uit te breiden."""
    id         = db.Column(db.Integer, primary_key=True)
    slug       = db.Column(db.String(140), unique=True, nullable=False)
    title      = db.Column(db.String(200), nullable=False)
    category   = db.Column(db.String(120), nullable=True)
    icon       = db.Column(db.String(40),  nullable=True)          # FontAwesome-klasse, bv. 'fa-tags'
    summary    = db.Column(db.String(400), nullable=True)
    body       = db.Column(db.Text,        nullable=True)          # Markdown
    sort_index = db.Column(db.Integer,     default=0)
    updated_at = db.Column(db.DateTime,    default=datetime.now, onupdate=datetime.now)
    updated_by = db.Column(db.String(100), nullable=True)

class W2PDocument(db.Model):
    """Gesynchroniseerde kant-en-klare schapkaart uit het oude W2P-systeem (metadata + thumbnail-cache)."""
    promotion_document_id = db.Column(db.Integer, primary_key=True)
    period_id    = db.Column(db.Integer, nullable=False)
    period_label = db.Column(db.String(120), nullable=True)
    category_id  = db.Column(db.Integer, default=7)
    group_id     = db.Column(db.Integer, nullable=True)
    group_label  = db.Column(db.String(120), nullable=True)
    formaat      = db.Column(db.String(80), nullable=True)
    naam         = db.Column(db.String(200), nullable=True)
    sort_index   = db.Column(db.Integer, default=0)  # positie in de tegel-volgorde van de groep-pagina
    synced_at    = db.Column(db.DateTime, default=datetime.now)
    # Gezet zodra pluslokaal.nl deze kaart niet meer heeft ('niet meer beschikbaar'). Dan proberen we
    # 'm niet meer te downloaden (geen eeuwige pogingen) en tonen we 'm als onbeschikbaar in de UI.
    unavailable_at = db.Column(db.DateTime, nullable=True)

class W2PCachedPdf(db.Model):
    """Vooraf (tijdens sync) besteld+gedownload verzameldocument per (categorie,periode,groep,formaat),
    zodat downloaden achteraf lokale pagina's kan knippen i.p.v. opnieuw bij W2P te bestellen."""
    id           = db.Column(db.Integer, primary_key=True)
    category_id  = db.Column(db.Integer, nullable=False)
    period_id    = db.Column(db.Integer, nullable=False)
    group_id     = db.Column(db.Integer, nullable=False)
    formaat      = db.Column(db.String(80), nullable=False)
    path         = db.Column(db.String(300), nullable=False)  # relatief pad onder static/w2p_pdfs/
    doc_ids      = db.Column(db.Text, nullable=False)         # JSON-lijst promotion_document_id, in paginavolgorde
    page_count   = db.Column(db.Integer, nullable=False)
    synced_at    = db.Column(db.DateTime, default=datetime.now)
    __table_args__ = (db.UniqueConstraint('category_id', 'period_id', 'group_id', 'formaat',
                                           name='uq_w2p_pdf_group_fmt'),)

class Role(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(40), unique=True, nullable=False)  # slug = User.role
    label        = db.Column(db.String(80), nullable=False)
    permissions  = db.Column(db.Text, default='[]')      # JSON-lijst van permissie-keys
    is_system    = db.Column(db.Boolean, default=False)  # basisrol (niet verwijderbaar/hernoembaar)
    store_scoped = db.Column(db.Boolean, default=True)   # gebruikers beperkt tot eigen winkel
    created_at   = db.Column(db.DateTime, default=datetime.now)

class Card(db.Model):
    id           = db.Column(db.Integer,     primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    price        = db.Column(db.String(100), nullable=False)
    image        = db.Column(db.String(200))
    formaat      = db.Column(db.String(50),  default='A3 liggend')
    kaart_data   = db.Column(db.Text,        default='{}')
    timestamp    = db.Column(db.DateTime,    default=datetime.now)
    username     = db.Column(db.String(100), nullable=False)
    filiaal      = db.Column(db.Integer,     nullable=False)
    filiaal_naam = db.Column(db.String(100), nullable=True)

# ─── FONTS (Montserrat variable - vrije Gotham-substituut) ────────────────────
_FONT_VAR = os.path.join(os.path.dirname(__file__), 'static', 'fonts', 'Montserrat-var.ttf')

# Gewichten afgeleid van het PLUS-ontwerp (PDF): Gotham-Book≈500, Bold≈700, Black≈900
W_BOOK, W_BOLD, W_BLACK, W_NARROW = 500, 700, 900, 400
_font_cache = {}

# Gotham drop-in: zet de gelicentieerde Gotham-fonts in static/fonts/gotham/ (bestandsnamen hieronder)
# en de renderer gebruikt ze automatisch → dan is de typografie ook 1:1 met de PLUS-winkelpakketten.
# Zolang die er niet zijn, valt hij terug op Montserrat (gratis Gotham-substituut). Per gewicht kunnen
# meerdere bestandsnamen; de eerste die bestaat wint.
_GOTHAM_DIR = os.path.join(os.path.dirname(__file__), 'static', 'fonts', 'gotham')
_GOTHAM_FILES = {
    900: ['Gotham-Black.otf', 'Gotham-Black.ttf', 'Gotham-Bold.otf'],
    700: ['Gotham-Bold.otf', 'Gotham-Bold.ttf', 'Gotham-Medium.otf'],
    500: ['Gotham-Book.otf', 'Gotham-Book.ttf', 'Gotham-Medium.otf'],
    400: ['GothamNarrow-Book.otf', 'GothamNarrow-Book.ttf', 'Gotham-Book.otf', 'Gotham-Book.ttf'],
}
# Vrije Gotham-substituut: GothicA1 (OFL, van Google Fonts) - visueel het dichtst bij Gotham van de
# gratis fonts (dubbelverdiepings-'a', zelfde proporties/gewicht). Wordt gebruikt zolang de
# gelicentieerde Gotham nog niet in static/fonts/gotham/ staat; anders wint Gotham.
_GOTHICA1_DIR = os.path.join(os.path.dirname(__file__), 'static', 'fonts', 'gothica1')
_GOTHICA1_FILES = {
    900: 'GothicA1-Black.ttf',
    700: 'GothicA1-Bold.ttf',
    500: 'GothicA1-Medium.ttf',
    400: 'GothicA1-Regular.ttf',
}
# Zet op True om GothicA1 (vrije Gotham-substituut) te gebruiken; op False → terug naar Montserrat.
# (De gelicentieerde Gotham in static/fonts/gotham/ wint altijd, ongeacht deze vlag.)
_GOTHICA1_ENABLED = False
def _gotham_path(weight):
    # 1) gelicentieerde Gotham (drop-in) → 2) GothicA1 (indien ingeschakeld) → anders None (Montserrat).
    for name in _GOTHAM_FILES.get(weight, []):
        p = os.path.join(_GOTHAM_DIR, name)
        if os.path.exists(p):
            return p
    if _GOTHICA1_ENABLED:
        ga = os.path.join(_GOTHICA1_DIR, _GOTHICA1_FILES.get(weight, ''))
        if _GOTHICA1_FILES.get(weight) and os.path.exists(ga):
            return ga
    return None

def F(weight, size):
    size = max(1, int(size))
    key = (weight, size)
    f = _font_cache.get(key)
    if f is None:
        gp = _gotham_path(weight)
        if gp:
            try:
                f = ImageFont.truetype(gp, size)
            except Exception:
                gp = None
        if not gp:
            try:
                f = ImageFont.truetype(_FONT_VAR, size)
                try:
                    f.set_variation_by_axes([weight])
                except Exception:
                    pass
            except Exception:
                f = ImageFont.load_default()
        _font_cache[key] = f
    return f

# PLUS-kleuren (exact uit de W2P-winkelpakketten)
RED   = (227,  0,  10)   # #E3000A - PLUS actierood
GREEN = (130, 187, 34)   # #82BB22 (Action Green)
BLACK = (35,  31,  32)   # #231F20
WHITE = (255, 255, 255)
GREY  = (108, 108, 108)
NEWTIP_GREEN = (124, 193, 65)   # tip-prijsblok (fel groen), gemeten uit de PLUS-referentie
NEWTIP_BAND  = (170, 203, 98)   # tip-band/linkerpaneel (lichter, met subtiele textuur)


def wrap(text, font, maxw, draw):
    if not text: return []
    words = str(text).split()
    lines, cur = [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if draw.textbbox((0, 0), t, font=font)[2] <= maxw:
            cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines or [str(text)]


def _tw(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1], b


def fit(draw, text, weight, max_w, max_h):
    """Grootste fontgrootte waarbij tekst binnen (max_w, max_h) past."""
    size = max(6, int(max_h / 0.72))
    for _ in range(60):
        f = F(weight, size)
        w, h, b = _tw(draw, text, f)
        if w <= max_w and h <= max_h:
            return f, w, h, b
        size = int(size * 0.93)
        if size < 6: break
    f = F(weight, 6)
    w, h, b = _tw(draw, text, f)
    return f, w, h, b


def _center(draw, cx, cy, text, font, fill):
    """Teken tekst gecentreerd op (cx, cy) op basis van de ink-bbox."""
    b = draw.textbbox((0, 0), text, font=font)
    x = cx - (b[0] + b[2]) / 2
    y = cy - (b[1] + b[3]) / 2
    draw.text((x, y), text, font=font, fill=fill)


# ─── NIX18-LOGO (echt logo uit static/img) ────────────────────────────────────
_nix_cache = {}
def nix_img(h):
    h = max(8, int(h))
    im = _nix_cache.get(h)
    if im is None:
        svg = os.path.join(os.path.dirname(__file__), 'static', 'img', 'nix18-logo.svg')
        try:
            import cairosvg
            png = cairosvg.svg2png(url=svg, output_height=h)
            im = Image.open(io.BytesIO(png)).convert('RGBA')
        except Exception:
            im = False
        _nix_cache[h] = im
    return im or None


# ─── SCANNER-ICOON (wit lijn-icoon uit static/img) ────────────────────────────
_scanner_cache = {}
def scanner_img(h):
    h = max(8, int(h))
    im = _scanner_cache.get(h)
    if im is None:
        svg = os.path.join(os.path.dirname(__file__), 'static', 'img', 'scanner-icon.svg')
        try:
            import cairosvg
            png = cairosvg.svg2png(url=svg, output_height=h)
            im = Image.open(io.BytesIO(png)).convert('RGBA')
        except Exception:
            im = False
        _scanner_cache[h] = im
    return im or None


# ─── BARCODE (EAN-8 / EAN-13) ─────────────────────────────────────────────────
GREEN_SCAN = (127, 193, 67)   # groen van de scan-tegels (iets frisser dan GREEN)

_EAN_GUARDS = {
    'ean13': {'start': (0, 3), 'center': (45, 50), 'end': (92, 95), 'n': 95},
    'ean8':  {'start': (0, 3), 'center': (31, 36), 'end': (64, 67), 'n': 67},
}

def _ean_kind(code):
    """Kies ean8 of ean13 op basis van de lengte van (ruwe) code."""
    digits = ''.join(c for c in str(code) if c.isdigit())
    if len(digits) >= 12:
        return 'ean13', digits[:13]
    return 'ean8', digits[:8]

def draw_barcode(draw, x0, y0, x1, y1, code, kind=None, number=True):
    """Teken een EAN-8/EAN-13 barcode binnen (x0,y0)-(x1,y1). Guard-bars steken door tot de cijfers.
    Geeft de volledige code (incl. checksum) terug, of None bij een ongeldige code."""
    import barcode as _bc
    if kind is None:
        kind, code = _ean_kind(code)
    digits = ''.join(c for c in str(code) if c.isdigit())
    need = 13 if kind == 'ean13' else 8
    if len(digits) < need - 1:
        return None
    try:
        cls = _bc.get_barcode_class(kind)
        obj = cls(digits[:need])
        binary = obj.build()[0]
        full = obj.get_fullcode()
    except Exception:
        return None
    g = _EAN_GUARDS[kind]; n = g['n']
    W = x1 - x0; H = y1 - y0
    qz = W * 0.05
    bx0 = x0 + qz; bw = W - 2 * qz
    mw = bw / n
    num_h = H * 0.22 if number else 0
    bar_bot = y1 - num_h
    guard_bot = y1 - num_h * 0.30
    def is_guard(i):
        return (g['start'][0] <= i < g['start'][1]) or (g['center'][0] <= i < g['center'][1]) or (g['end'][0] <= i < g['end'][1])
    for i, ch in enumerate(binary):
        if ch != '1':
            continue
        rx0 = bx0 + i * mw; rx1 = bx0 + (i + 1) * mw
        draw.rectangle([rx0, y0, rx1, guard_bot if is_guard(i) else bar_bot], fill=BLACK)
    if number and num_h > 0:
        f = F(W_BOOK, num_h * 0.82)
        def put(txt, cx):
            b = draw.textbbox((0, 0), txt, font=f); w = b[2] - b[0]
            draw.text((cx - w / 2 - b[0], y1 - num_h * 0.96 - b[1]), txt, font=f, fill=BLACK)
        if kind == 'ean8':
            lc = bx0 + (g['start'][1] + (g['center'][0] - g['start'][1]) / 2) * mw
            rc = bx0 + (g['center'][1] + (g['end'][0] - g['center'][1]) / 2) * mw
            put(full[:4], lc); put(full[4:], rc)
        else:
            b = draw.textbbox((0, 0), full[0], font=f)
            draw.text((x0, y1 - num_h * 0.96 - b[1]), full[0], font=f, fill=BLACK)
            lc = bx0 + (g['start'][1] + (g['center'][0] - g['start'][1]) / 2) * mw
            rc = bx0 + (g['center'][1] + (g['end'][0] - g['center'][1]) / 2) * mw
            put(full[1:7], lc); put(full[7:], rc)
    return full


def draw_scan_tile(canvas, draw, tx, ty, tw, th, item):
    """Groene 'Scan hier'-tegel: scanner-icoon + Scan hier + productnaam + formaat + witte barcodebox.
    item = {naam, formaat, code, type}. Coördinaten 1:1 met de PLUS-referentie (A3)."""
    naam = str(item.get('naam', '')).strip()
    fmt  = str(item.get('formaat', '')).strip()
    code = str(item.get('code', '')).strip()
    rad = th * 0.12
    draw.rounded_rectangle([tx, ty, tx + tw, ty + th], radius=rad, fill=GREEN_SCAN)
    # scanner-icoon linksboven
    ic = scanner_img(int(th * 0.46))
    if ic:
        canvas.paste(ic, (int(tx + tw * 0.06), int(ty + th * 0.10)), ic)
    # "Scan hier" wit, linksonder
    fsh = F(W_BOLD, th * 0.165)
    draw.text((tx + tw * 0.05, ty + th * 0.60), 'Scan hier', font=fsh, fill=WHITE)
    # rechterzone: naam + formaat (gecentreerd) boven de barcodebox
    rz0 = tx + tw * 0.44; rz1 = tx + tw * 0.985; rzc = (rz0 + rz1) / 2
    y = ty + th * 0.06
    if naam:
        fn = F(W_BOLD, th * 0.15)
        for line in wrap(naam, fn, (rz1 - rz0), draw):
            _center(draw, rzc, y + th * 0.075, line, fn, BLACK); y += th * 0.155
    if fmt:
        ff = F(W_BOOK, th * 0.10)
        _center(draw, rzc, y + th * 0.05, fmt, ff, BLACK); y += th * 0.12
    # witte barcodebox
    bx0 = rz0; bx1 = rz1; by0 = ty + th * 0.42; by1 = ty + th * 0.92
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=th * 0.04, fill=WHITE)
    pad = tw * 0.02
    draw_barcode(draw, bx0 + pad, by0 + th * 0.05, bx1 - pad, by1 - th * 0.04, code,
                 (item.get('type') or None))


def _draw_product_box(canvas, draw, x, y, w, h, item):
    """Wit barcodevak met productnaam + formaat erboven (op de groene scankaart)."""
    naam = str(item.get('naam', '')).strip()
    fmt  = str(item.get('formaat', '')).strip()
    code = str(item.get('code', '')).strip()
    cx = x + w / 2
    yy = y + h * 0.04
    if naam:
        fn = F(W_BOLD, h * 0.15)
        for line in wrap(naam, fn, w * 0.96, draw):
            _center(draw, cx, yy + h * 0.075, line, fn, BLACK); yy += h * 0.155
    if fmt:
        ff = F(W_BOOK, h * 0.11)
        _center(draw, cx, yy + h * 0.055, fmt, ff, BLACK); yy += h * 0.13
    by0 = max(yy + h * 0.02, y + h * 0.40); by1 = y + h * 0.97
    draw.rounded_rectangle([x + w * 0.02, by0, x + w * 0.98, by1], radius=h * 0.04, fill=WHITE)
    pad = w * 0.05
    draw_barcode(draw, x + pad, by0 + h * 0.05, x + w - pad, by1 - h * 0.04, code,
                 (item.get('type') or None))


SCAN_GREEN = GREEN_SCAN        # zelfde PLUS-scankaartgroen als de A3-barcodetegels (127,193,67)

def _scan_hier(canvas, draw, x0, y0, x1, y1, big=False):
    """Scanner-icoon + wit 'Scan hier' - in een cel (grid) of groot links (compact).
    De tekst wordt op de beschikbare breedte geschaald, zodat hij nooit buiten de kaart valt."""
    w = x1 - x0; h = y1 - y0
    ic = scanner_img(int(h * (0.34 if big else 0.42)))
    if ic:
        canvas.paste(ic, (int((x0 + x1) / 2 - ic.width / 2), int(y0 + h * (0.08 if big else 0.05))), ic)
    f, _, _, _ = fit(draw, 'Scan hier', W_BLACK, w * 0.90, h * (0.22 if big else 0.26))
    _center(draw, (x0 + x1) / 2, y1 - h * (0.16 if big else 0.14), 'Scan hier', f, WHITE)

def _fit_name(draw, text, maxw, maxh, start_px):
    """Grootste bold-fontgrootte waarbij `text` (max 3 regels) binnen (maxw, maxh) past."""
    size = max(6, int(start_px))
    while size >= 6:
        f = F(W_BOLD, size)
        lines = wrap(text, f, maxw, draw)[:3]
        widest = max((draw.textbbox((0, 0), l, font=f)[2] for l in lines), default=0)
        if widest <= maxw and len(lines) * size * 1.18 <= maxh:
            return f, size, lines
        size = int(size * 0.9)
    f = F(W_BOLD, 6)
    return f, 6, wrap(text, f, maxw, draw)[:3]

def _scan_product(canvas, draw, x0, y0, x1, y1, item, boxed=True):
    """Eén product: witte afgeronde box (of, boxed=False, tekst op groen) met naam + formaat + barcode.
    Naam schaalt mee met de vakbreedte; de barcode staat vast in de onderste helft."""
    naam = str(item.get('naam', '')).strip()
    fmt  = str(item.get('formaat', '')).strip()
    code = str(item.get('code', '')).strip()
    w = x1 - x0; h = y1 - y0; cx = (x0 + x1) / 2
    if boxed:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=h * 0.10, fill=WHITE)
        ix0, ix1 = x0 + w * 0.07, x1 - w * 0.07
    else:
        ix0, ix1 = x0 + w * 0.03, x1 - w * 0.03
    # naam (bold zwart, max 3 regels) in de bovenste zone
    top_h = h * 0.36
    nf, nsz, lines = _fit_name(draw, naam, ix1 - ix0, top_h, min(w * 0.14, h * 0.20))
    lh = nsz * 1.14
    y = y0 + h * 0.07
    for line in lines:
        _center(draw, cx, y + lh / 2, line, nf, BLACK); y += lh
    if fmt:
        fsz = max(6, int(nsz * 0.72))
        _center(draw, cx, y + fsz * 0.7, fmt, F(W_BOOK, fsz), BLACK)
    # barcode in de onderste helft (vaste plek)
    by0 = y0 + h * 0.50; by1 = y1 - h * 0.06
    if not boxed:
        draw.rectangle([ix0 + w * 0.05, by0, ix1 - w * 0.05, by1], fill=WHITE)
    if code:
        draw_barcode(draw, ix0 + w * 0.10, by0 + h * 0.02, ix1 - w * 0.10, by1 - h * 0.02,
                     code, (item.get('type') or None))

def draw_scankaart_cell(canvas, draw, ox, oy, W, H, products):
    """Eén PLUS-scankaart (groene kaart): 'Scan hier' + producten. Indeling past zich aan het aantal
    producten aan - 1:1 met de PLUS-referentie (SK Maxi):
      • 1 product : links groot 'Scan hier', rechts naam + barcode (op groen).
      • 2 producten: links groot 'Scan hier', rechts 2 witte productvakken onder elkaar.
      • ≥3 producten: 2-koloms raster; 'Scan hier' in de eerste cel, de rest witte vakken."""
    import math
    draw.rounded_rectangle([ox, oy, ox + W, oy + H], radius=min(W, H) * 0.035, fill=SCAN_GREEN)
    products = [p for p in (products or []) if str(p.get('code', '')).strip() or str(p.get('naam', '')).strip()]
    n = len(products)
    def box(fx0, fy0, fx1, fy1):
        return (ox + fx0 * W, oy + fy0 * H, ox + fx1 * W, oy + fy1 * H)

    if n <= 2:
        _scan_hier(canvas, draw, *box(0.05, 0.08, 0.47, 0.92), big=True)
        rx0, rx1 = 0.52, 0.95
        if n == 1:
            _scan_product(canvas, draw, *box(rx0, 0.14, rx1, 0.86), products[0], boxed=False)
        elif n == 2:
            gap = 0.05; bh = (0.86 - 0.12 - gap) / 2
            _scan_product(canvas, draw, *box(rx0, 0.12, rx1, 0.12 + bh), products[0], boxed=True)
            _scan_product(canvas, draw, *box(rx0, 0.12 + bh + gap, rx1, 0.86), products[1], boxed=True)
        return

    # ≥3 producten: 2-koloms raster
    rows = max(2, math.ceil((n + 1) / 2))
    MX0, MX1, MY0, MY1 = 0.054, 0.946, 0.085, 0.915
    gapx = 0.018
    cw = (MX1 - MX0 - gapx) / 2
    ph = (MY1 - MY0) / rows; boxh = ph * 0.90
    def cell(col, row):
        x0 = MX0 + col * (cw + gapx); y0 = MY0 + row * ph
        return box(x0, y0, x0 + cw, y0 + boxh)
    _scan_hier(canvas, draw, *cell(0, 0), big=False)
    order = [(r, c) for r in range(rows) for c in range(2) if not (r == 0 and c == 0)]
    for item, (r, c) in zip(products, order):
        _scan_product(canvas, draw, *cell(c, r), item, boxed=True)


def generate_scankaart(cells):
    """Render een SK Maxi-vel (A4-liggend) met **4 (afzonderlijke) PLUS-scankaarten** - net als de
    actie-SK Maxi: 4 kaarten op één vel, elk met een eigen productenlijst.
    `cells` = lijst van maximaal 4× {'products': [ {naam, formaat, code, type}, ... ]}.
    Verwerkt ook een platte productenlijst (→ 4 identieke kaarten) voor terugwaartse compatibiliteit."""
    cells = cells or []
    # platte lijst producten? -> 4 identieke kaarten
    if cells and isinstance(cells[0], dict) and 'products' not in cells[0]:
        cells = [{'products': cells}] * 4
    W, H = _px(297), _px(210)
    canvas = Image.new('RGB', (W, H), WHITE)
    draw = ImageDraw.Draw(canvas)
    cw, ch = int(_SK_CW * W), int(_SK_CH * H)
    for i, (fx, fy) in enumerate(_SK_CELLS):
        c = cells[i] if i < len(cells) else {}
        prods = c.get('products') if isinstance(c, dict) else None
        prods = [p for p in (prods or []) if str(p.get('code', '')).strip() or str(p.get('naam', '')).strip()]
        if not prods:
            continue   # lege kaart → dat deel van het vel blijft wit (niets tekenen)
        draw_scankaart_cell(canvas, draw, int(fx * W), int(fy * H), cw, ch, prods)
    stem = f"scan_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:18]}_{secrets.token_hex(4)}"
    folder = app.config['EXPORT_FOLDER']
    canvas.save(os.path.join(folder, stem + '.pdf'), 'PDF', resolution=300)
    canvas.convert('RGB').resize((int(W * 0.28), int(H * 0.28))).save(os.path.join(folder, stem + '.png'))
    return stem + '.png'


# ─── ACTIEBLOKKEN (herbruikbaar; werken op een expliciete box) ─────────────────
def _twotone(draw, x0, x1, y0, y1, top_txt, label_txt):
    """Rood vlak (grote witte tekst) + groene balk (wit label). Rood = 2/3, groen = 1/3."""
    bw = x1 - x0
    r_bot = y0 + (y1 - y0) * 0.667
    draw.rectangle([x0, y0, x1, r_bot], fill=RED)
    draw.rectangle([x0, r_bot, x1, y1], fill=GREEN)
    f, _, _, _ = fit(draw, top_txt, W_BLACK, bw * 0.86, (r_bot - y0) * 0.74)
    _center(draw, (x0 + x1) / 2, (y0 + r_bot) / 2, top_txt, f, WHITE)
    if label_txt:
        fl, _, _, _ = fit(draw, label_txt, W_BOLD, bw * 0.90, (y1 - r_bot) * 0.60)
        _center(draw, (x0 + x1) / 2, (r_bot + y1) / 2, label_txt, fl, WHITE)


def _prijs(draw, x0, x1, y0, y1, av, vp1, vp2, vlbl):
    """Groene labelbalk + rood blok met doorgestreepte vanprijs en grote prijs (superscript-centen)."""
    bw = x1 - x0
    cx = (x0 + x1) / 2
    top = y0
    if vlbl:
        lb = y0 + (y1 - y0) * 0.205
        draw.rectangle([x0, y0, x1, lb], fill=GREEN)
        fl, _, _, _ = fit(draw, vlbl, W_BOLD, bw * 0.68, (lb - y0) * 0.80)
        _center(draw, cx, (y0 + lb) / 2, vlbl, fl, WHITE)
        top = lb
    draw.rectangle([x0, top, x1, y1], fill=RED)
    rh = y1 - top
    lw = max(2, int(rh * 0.020))

    # Vanprijs (doorgestreept), gecentreerd bovenin het rode blok
    vptxt = ' - '.join(filter(None, [vp1, vp2]))
    price_top = top + rh * 0.075
    if vptxt:
        fv, vw, vh, vb = fit(draw, vptxt, W_BOLD, bw * 0.48, rh * 0.125)
        vx = cx - vw / 2
        draw.text((vx - vb[0], price_top), vptxt, font=fv, fill=WHITE)
        midy = price_top + (vb[1] + vb[3]) / 2
        draw.line([(vx, midy), (vx + vw, midy)], fill=WHITE, width=lw)
        price_top += vb[3] + rh * 0.02

    # Grote prijs: geheel "N." + superscript centen
    ps = av.replace('€', '').replace(',', '.').strip()
    parts = ps.split('.')
    geheel = (parts[0] or '0') + '.'
    cent = (parts[1] + '00')[:2] if len(parts) > 1 and parts[1] else '-'

    fg, gw, gh, gb = fit(draw, geheel, W_BLACK, bw * 0.62, (y1 - price_top) * 0.86)
    fc = F(W_BLACK, max(6, int(fg.size * 0.64)))
    cw, ch, cb = _tw(draw, cent, fc)

    # Korte prijzen (1 euro-cijfer) worden hoogte-gelimiteerd enorm → geheel+centen
    # zouden samen buiten het rode vlak lopen. Schaal terug zodat het totaal past.
    avail = bw * 0.86
    if gw + cw + rh * 0.02 > avail:
        sc = avail / (gw + cw + rh * 0.02)
        fg = F(W_BLACK, max(6, int(fg.size * sc)))
        gw, gh, gb = _tw(draw, geheel, fg)
        fc = F(W_BLACK, max(6, int(fg.size * 0.64)))
        cw, ch, cb = _tw(draw, cent, fc)

    total = gw + cw + int(rh * 0.02)
    start_x = cx - total / 2
    region_cy = (price_top + y1) / 2
    gy = region_cy - (gb[1] + gb[3]) / 2
    draw.text((start_x - gb[0], gy), geheel, font=fg, fill=WHITE)
    cx2 = start_x + gw + int(rh * 0.02)
    cy2 = gy + gb[1] - cb[1]              # centen top-aligned (superscript)
    draw.text((cx2 - cb[0], cy2), cent, font=fc, fill=WHITE)


def _action_spec(d):
    """Bepaal top/label voor two-tone, of prijs, uit de kaartdata."""
    t = str(d.get('actietype', '')).strip()
    av = str(d.get('av', '')).strip()
    av2 = str(d.get('av2', '')).strip()
    if t == 'prijs' and av:
        return ('prijs', av)
    if t == 'xpctkorting' and av:
        return ('two', (av if '%' in av else av + '%'), 'KORTING')
    if t == 'xeurokorting' and av:
        # PLUS-notatie: hele euro's als "5.-", met centen als "5.50"
        a = av.replace('€', '').replace(',', '.').strip()
        if '.' in a:
            w, c = a.split('.', 1)
            c = (c + '00')[:2]
            disp = f'{w}.-' if c in ('00', '') else f'{w}.{c}'
        else:
            disp = f'{a}.-'
        return ('two', disp, 'KORTING')
    if t == 'xplusygratis' and av:
        top = av if '+' in av else f'{av}+{av2 or av}'   # bv. '1+1'
        return ('two', top, 'GRATIS')
    if t == 'xisy' and av:
        return ('two', av, 'GRATIS')
    if t == 'xhalenybetalen' and av:
        return ('two', av + ' HALEN', (av2 or '?') + ' BETALEN')
    if t == 'halveprijs':
        return ('two', '2e', 'HALVE PRIJS')
    return (None,)


# ─── KAART TEKENEN (oriëntatie-bewust) ────────────────────────────────────────
_KM_LOGO_FILE = os.path.join(os.path.dirname(__file__), 'static', 'img', 'kies-en-mix.png')
_km_logo_cache = {}
def _kies_mix_logo(w_px):
    """Het originele PLUS 'Kies & Mix'-schild (transparant), geschaald op breedte. Gecachet per breedte."""
    w_px = max(8, int(w_px))
    if w_px in _km_logo_cache:
        return _km_logo_cache[w_px]
    img = None
    try:
        base = Image.open(_KM_LOGO_FILE).convert('RGBA')
        h_px = max(1, int(w_px * base.height / base.width))
        img = base.resize((w_px, h_px), Image.LANCZOS)
    except Exception:
        img = None
    _km_logo_cache[w_px] = img
    return img

def _draw_kies_mix(canvas, ox, oy, W, H):
    """Plak het 'Kies & Mix'-logo 1:1 rechtsboven op de kaart (zelfde plek als de PLUS-referentie)."""
    logo = _kies_mix_logo(int(0.165 * W))          # ~16,5% van de kaartbreedte, net als de PLUS-kaart
    if logo is None:
        return
    mx = int(0.030 * W)                            # marge vanaf de rechterrand
    my = int(0.045 * H)                            # marge vanaf de bovenrand
    x = int(ox + W - mx - logo.width)
    y = int(oy + my)
    canvas.paste(logo, (x, y), logo)

def draw_kaart(canvas, draw, data, ox, oy, W, H):
    layout = str(data.get('layout', 'nieuw')).strip()
    kt = str(data.get('kaarttype', 'actie')).strip()
    land = W >= H
    if kt == 'tip':
        if layout == 'oud':
            (_tip_landscape if land else _tip_portrait)(canvas, draw, data, ox, oy, W, H)
        else:
            _newtip(canvas, draw, data, ox, oy, W, H)
    else:
        if layout == 'oud':
            (_old_actie_landscape if land else _old_actie_portrait)(canvas, draw, data, ox, oy, W, H)
        else:
            (_draw_landscape if land else _draw_portrait)(canvas, draw, data, ox, oy, W, H)
    _draw_overlay(canvas, data, ox, oy, W, H)
    # Kies & Mix-schild rechtsboven (1:1 het PLUS-logo) wanneer de kaart 'Kies & Mix' is.
    if str(data.get('kem', 'nee')).strip() == 'ja':
        _draw_kies_mix(canvas, ox, oy, W, H)


_overlay_cache = {}
def _fetch_overlay(src, want_w=800):
    """Haal een productfoto (transparante PNG) op van het ctfassets/plus-CDN. SSRF-veilig + cache."""
    import urllib.request, urllib.parse
    host = urllib.parse.urlparse(src).netloc
    if not (host.endswith('ctfassets.net') or host.endswith('plus.nl')):
        return None
    w = max(150, min(1600, int(want_w)))
    url = src.split('?')[0] + f'?w={w}&fm=png'
    if url in _overlay_cache:
        return _overlay_cache[url]
    im = None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        data = urllib.request.urlopen(req, timeout=10).read()
        im = Image.open(io.BytesIO(data)).convert('RGBA')
    except Exception:
        im = None
    _overlay_cache[url] = im
    return im

def _paste_overlay(canvas, o, ox, oy, W, H):
    if not o or not o.get('src'):
        return
    try:
        w_px = max(1, int(float(o.get('w', 0.25)) * W))
        img = _fetch_overlay(o['src'], w_px)
        if img is None:
            return
        h_px = max(1, int(w_px * img.height / img.width))
        img2 = img.resize((w_px, h_px))
        x = int(ox + float(o.get('x', 0.1)) * W)
        y = int(oy + float(o.get('y', 0.1)) * H)
        canvas.paste(img2, (x, y), img2)
    except Exception:
        pass

def _draw_overlay(canvas, d, ox, oy, W, H):
    """Teken de op de kaart geplaatste productfoto('s) - één of meerdere - op exact de positie/grootte
    uit de editor. Ondersteunt zowel het oude enkele object als een lijst met meerdere foto's."""
    ov = d.get('overlay')
    if isinstance(ov, str):
        try:
            ov = json.loads(ov) if ov.strip() else None
        except Exception:
            ov = None
    if not ov:
        return
    for o in (ov if isinstance(ov, list) else [ov]):
        _paste_overlay(canvas, o, ox, oy, W, H)


def _outline_text(draw, x, y, text, font, fill, outline, ow):
    """Tekst met buitenlijn (sticker-effect) via PIL stroke - één efficiënte aanroep."""
    draw.text((x, y), text, font=font, fill=fill, stroke_width=max(1, int(ow)), stroke_fill=outline)


def _txt_block(canvas, draw, d, LX, y, colw, R, fill=None):
    """Teken merk/kop/sub/vbtekst/aanv + kies&mix-regels. R = referentiegrootte (px). Geeft nieuwe y terug."""
    fill = fill or BLACK
    def sz(p): return max(6, int(p * R))

    merk = str(d.get('merk', '')).strip()
    if merk:
        f = F(W_BOLD, sz(0.030))
        draw.text((LX, y), merk, font=f, fill=fill); y += sz(0.040)

    kop = str(d.get('koptekst', '')).strip()
    if kop:
        f = F(W_BLACK, sz(0.077))
        for line in wrap(kop, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=fill); y += sz(0.079)
        y += sz(0.012)

    sub = str(d.get('subtekst', '')).strip()
    if sub:
        f = F(W_BOOK, sz(0.056))
        for line in wrap(sub, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=fill); y += sz(0.059)

    # Kies & Mix-regels (elke regel = een product)
    if str(d.get('kem', 'nee')).strip() == 'ja':
        mix = str(d.get('mix', '')).strip()
        if mix:
            f = F(W_BOOK, sz(0.040))
            for line in [l for l in mix.splitlines() if l.strip()]:
                draw.text((LX, y), '• ' + line.strip(), font=f, fill=fill); y += sz(0.046)

    vbt = str(d.get('vbtekst', '')).strip()
    if vbt:
        y += sz(0.006)
        f = F(W_BOOK, sz(0.032))
        for line in wrap(vbt, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=fill); y += sz(0.036)

    aanv = str(d.get('aanv', '')).strip()
    if aanv:
        y += sz(0.006)
        f = F(W_BOOK, sz(0.028))
        for line in wrap(aanv, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=fill); y += sz(0.032)

    return y


def _extra_info(canvas, draw, d, LX, y, R):
    """Kiloprijs, land van herkomst en NIX18-logo onder de tekst. R = referentiegrootte."""
    def sz(p): return max(6, int(p * R))
    kilo = str(d.get('kilo', '')).strip()
    if kilo:
        y += sz(0.010)
        draw.text((LX, y), kilo, font=F(W_BOOK, sz(0.026)), fill=GREY); y += sz(0.030)
    land = str(d.get('land', '')).strip()
    if land:
        y += sz(0.004)
        draw.text((LX, y), f'Land van herkomst: {land}', font=F(W_BOLD, sz(0.026)), fill=BLACK)
        y += sz(0.032)
    if str(d.get('alcohol', 'nee')).strip() == 'ja':
        y += sz(0.010)
        f = F(W_BOOK, sz(0.022))
        draw.text((LX, y), '< 25 jaar? Laat je legitimatie zien!', font=f, fill=GREY); y += sz(0.026)
        draw.text((LX, y), '< 18 jaar verkopen wij geen alcohol', font=f, fill=GREY); y += sz(0.030)
        ni = nix_img(sz(0.075))
        if ni:
            canvas.paste(ni, (int(LX), int(y)), ni); y += sz(0.085)
    return y


def _disclaimers(draw, d, LX, W, X, Y, S, colw):
    t = str(d.get('actietype', '')).strip()
    if t == 'halveprijs':
        note = ('**Voor 2e halve prijs geldt: per combinatie kan de prijs verschillen. '
                'Je krijgt 25% korting op de totaalprijs.')
        fn = F(W_NARROW, S(0.0145))
        yy = Y(0.930)
        for line in wrap(note, fn, colw, draw):
            draw.text((LX, yy), line, font=fn, fill=BLACK); yy += S(0.017)
    mx = str(d.get('max', '')).strip()
    if mx:
        draw.text((LX, Y(0.972)),
                  f'Maximaal {mx} aanbiedingen per klant tenzij anders vermeld.',
                  font=F(W_NARROW, max(6, int(0.0092 * W))), fill=BLACK)


def _kaartcode(draw, d, X, Y, W, top=False):
    # Kaartcode (klein, GothamNarrow) in een hoek weg van het prijsblok: portret → rechtsONDER,
    # landschap (prijsblok rechtsonder) → rechtsBOVEN. Grootte schaalt met de breedte (~0.009·W).
    code = str(d.get('code', '')).strip()
    if code:
        f = F(W_NARROW, max(6, int(0.009 * W)))
        cw, _, _ = _tw(draw, code, f)
        draw.text((X(0.952) - cw, Y(0.022) if top else Y(0.980)), code, font=f, fill=BLACK)


def _draw_action(draw, d, x0, x1, y0_two, y1_two, y0_pr, y1_pr):
    spec = _action_spec(d)
    if spec[0] == 'prijs':
        verp = str(d.get('verpakking', '')).strip()
        inh = str(d.get('inhoud', '')).strip()
        vlbl = ' '.join(filter(None, [verp, inh])).strip()
        _prijs(draw, x0, x1, y0_pr, y1_pr, spec[1],
               str(d.get('vp1', '')).strip(), str(d.get('vp2', '')).strip(), vlbl)
    elif spec[0] == 'two':
        _twotone(draw, x0, x1, y0_two, y1_two, spec[1], spec[2])


def _parse_scans(d):
    """Haal de scan-lijst uit de kaartdata (JSON-string of lijst). Alleen items met een code."""
    scans = d.get('scans')
    if isinstance(scans, str):
        try:
            scans = json.loads(scans) if scans.strip() else []
        except Exception:
            scans = []
    return [s for s in (scans or []) if isinstance(s, dict) and str(s.get('code', '')).strip()]


def _draw_scans(canvas, draw, d, ox, oy, W, H, land, y_bottom_frac=None):
    """Teken de 'Scan hier'-barcodetegels als grid. 1:1 met de PLUS-referentie (A3 liggend).
    Liggend: onderin uitgelijnd. Staand: het prijsblok staat onderaan, dus dan geven de
    layouts een `y_bottom_frac` mee zodat de tegels net BOVEN het prijsblok eindigen."""
    scans = _parse_scans(d)
    if not scans:
        return
    if land:
        tw = 0.202 * W; th = 0.128 * H
        cols = 2; stepx = 0.252 * W
        x0 = ox + 0.030 * W
        y_bottom = oy + (y_bottom_frac if y_bottom_frac is not None else 0.935) * H
    else:
        tw = 0.42 * W; th = 0.085 * H
        cols = 2; stepx = 0.45 * W
        x0 = ox + 0.03 * W
        y_bottom = oy + (y_bottom_frac if y_bottom_frac is not None else 0.955) * H
    scans = scans[:8]
    gap = th * 0.14
    rows = (len(scans) + cols - 1) // cols
    top = y_bottom - rows * th - (rows - 1) * gap
    for i, item in enumerate(scans):
        col = i % cols; row = i // cols
        tx = x0 + col * stepx
        ty = top + row * (th + gap)
        draw_scan_tile(canvas, draw, tx, ty, tw, th, item)


def _draw_landscape(canvas, draw, d, ox, oy, W, H):
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    def S(p): return max(6, int(p * H))
    LX = X(0.030)
    COLW = 0.50 * W
    y = _txt_block(canvas, draw, d, LX, Y(0.028), COLW, H)
    _extra_info(canvas, draw, d, LX, y, H)
    _kaartcode(draw, d, X, Y, W, top=True)
    _disclaimers(draw, d, LX, W, X, Y, S, 0.52 * W)
    _draw_action(draw, d, X(0.561), X(0.970), Y(0.325), Y(0.904), Y(0.422), Y(0.904))
    _draw_scans(canvas, draw, d, ox, oy, W, H, True)


def _draw_portrait(canvas, draw, d, ox, oy, W, H):
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    def S(p): return max(6, int(p * H))
    LX = X(0.047)
    COLW = 0.90 * W
    R = W                                  # tekst schaalt op breedte in portret
    y = _txt_block(canvas, draw, d, LX, Y(0.020), COLW, R)
    _extra_info(canvas, draw, d, LX, y, R)
    _kaartcode(draw, d, X, Y, W, top=False)
    _disclaimers(draw, d, LX, W, X, Y, S, COLW)
    # actieblok: onderste helft; breedte/hoogte 1:1 met de PLUS-referentie (A4)
    ax0, ax1 = X(0.186), X(0.812)
    _draw_action(draw, d, ax0, ax1, Y(0.508), Y(0.951), Y(0.582), Y(0.951))
    # barcodes staand: net boven het prijsblok (dat onderaan staat) → geen overlap
    _draw_scans(canvas, draw, d, ox, oy, W, H, False, y_bottom_frac=0.492)


# ─── TIP-KAART (groene TIP-banner + zwarte prijs) ─────────────────────────────
def _tip_price(draw, d, px0, px1, py0, py1):
    """Zwart VERPAKKING-label + grote zwarte prijs met superscript-centen, in de box (px0,py0)-(px1,py1)."""
    av = str(d.get('av', '')).replace('€', '').replace(',', '.').strip()
    if not av:
        return
    parts = av.split('.')
    whole = (parts[0] or '0') + '.'
    cent = (parts[1] + '00')[:2] if len(parts) > 1 and parts[1] else '-'
    bw = px1 - px0
    bh = py1 - py0
    cx = (px0 + px1) / 2

    verp = str(d.get('verpakking', '')).strip()
    inh  = str(d.get('inhoud', '')).strip()
    vlbl = ' '.join(filter(None, [verp, inh])).upper()

    price_top = py0
    if vlbl:
        fl, lw, lh, lb = fit(draw, vlbl, W_BLACK, bw * 0.92, bh * 0.16)
        lx0 = cx - lw / 2 - lh * 0.5
        lx1 = cx + lw / 2 + lh * 0.5
        draw.rectangle([lx0, py0, lx1, py0 + lh * 1.7], fill=BLACK)
        _center(draw, cx, py0 + lh * 0.85, vlbl, fl, WHITE)
        price_top = py0 + lh * 1.7 + bh * 0.03

    fg, gw, gh, gb = fit(draw, whole, W_BLACK, bw * 0.66, (py1 - price_top) * 0.98)
    fc = F(W_BLACK, max(6, int(fg.size * 0.52)))
    cw, ch, cb = _tw(draw, cent, fc)
    total = gw + cw
    sx = cx - total / 2
    # teken geheel met de onderkant op py1
    gy = py1 - gb[3]
    draw.text((sx - gb[0], gy), whole, font=fg, fill=BLACK)
    # centen superscript (bovenaan uitgelijnd met geheel)
    cx2 = sx + gw
    cy2 = gy + gb[1] - cb[1]
    draw.text((cx2 - cb[0], cy2), cent, font=fc, fill=BLACK)


def _tip_nix(canvas, draw, d, X, Y, S, panel_right):
    if str(d.get('alcohol', 'nee')).strip() != 'ja':
        return
    ni = nix_img(S(0.055))
    if ni:
        canvas.paste(ni, (int(X(0.03)), int(Y(0.86))), ni)


def _tip_landscape(canvas, draw, d, ox, oy, W, H):
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    def S(p): return max(6, int(p * H))
    PR = 0.279                                   # breedte groene paneel
    draw.rectangle([ox, oy, X(PR), oy + H], fill=GREEN)
    draw.text((X(0.035), Y(0.028)), 'TIP', font=F(W_BOLD, S(0.19)), fill=WHITE)
    _tip_nix(canvas, draw, d, X, Y, S, PR)

    LX = X(0.305)
    colw = 0.66 * W
    y = Y(0.024)
    kop = str(d.get('koptekst', '')).strip()
    if kop:
        f = F(W_BLACK, S(0.088))
        for line in wrap(kop, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.092)
        y += S(0.008)
    sub = str(d.get('subtekst', '')).strip()
    if sub:
        f = F(W_BOOK, S(0.056))
        for line in wrap(sub, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.060)

    # prijs rechtsonder
    _tip_price(draw, d, X(0.44), X(0.965), Y(0.44), Y(0.92))
    # kilo + land onderaan
    kilo = str(d.get('kilo', '')).strip()
    if kilo:
        f = F(W_BOOK, S(0.028)); kw, _, _ = _tw(draw, kilo, f)
        draw.text((X(0.965) - kw, Y(0.93)), kilo, font=f, fill=BLACK)
    land = str(d.get('land', '')).strip()
    if land:
        draw.text((LX, Y(0.93)), f'Land van herkomst: {land}', font=F(W_BOLD, S(0.024)), fill=BLACK)


def _tip_portrait(canvas, draw, d, ox, oy, W, H):
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    def S(p): return max(6, int(p * H))
    BH = 0.17                                     # hoogte groene banner bovenaan
    draw.rectangle([ox, oy, ox + W, Y(BH)], fill=GREEN)
    # TIP gecentreerd in banner
    ft = F(W_BOLD, S(0.13))
    _center(draw, X(0.5), Y(BH / 2), 'TIP', ft, WHITE)

    LX = X(0.07)
    colw = 0.86 * W
    y = Y(BH + 0.03)
    kop = str(d.get('koptekst', '')).strip()
    if kop:
        f = F(W_BLACK, S(0.070))
        for line in wrap(kop, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.074)
        y += S(0.006)
    sub = str(d.get('subtekst', '')).strip()
    if sub:
        f = F(W_BOOK, S(0.044))
        for line in wrap(sub, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.048)
    # kilo/land/nix
    kilo = str(d.get('kilo', '')).strip()
    if kilo:
        y += S(0.010); draw.text((LX, y), kilo, font=F(W_BOOK, S(0.026)), fill=GREY); y += S(0.030)
    land = str(d.get('land', '')).strip()
    if land:
        draw.text((LX, y), f'Land van herkomst: {land}', font=F(W_BOLD, S(0.026)), fill=BLACK)
    if str(d.get('alcohol', 'nee')).strip() == 'ja':
        ni = nix_img(S(0.07))
        if ni: canvas.paste(ni, (int(LX), int(Y(0.86))), ni)
    # prijs gecentreerd onderin
    _tip_price(draw, d, X(0.12), X(0.88), Y(0.55), Y(0.90))


# ─── OUDE ACTIEKAART (rode 'sticker'-prijs) ───────────────────────────────────
def _old_label(draw, cx, cy, text, maxw, maxh):
    """Wit badge met rode tekst + zwarte schaduw (Verpakking/Inhoud-label)."""
    fl, tw, th, tb = fit(draw, text.strip(), W_BLACK, maxw, maxh)
    padx = th * 0.5; pady = th * 0.35
    x0 = cx - tw / 2 - padx; x1 = cx + tw / 2 + padx
    y0 = cy - th / 2 - pady; y1 = cy + th / 2 + pady
    sh = max(2, int(th * 0.16)); r = th * 0.35
    draw.rounded_rectangle([x0 + sh, y0 + sh, x1 + sh, y1 + sh], radius=r, fill=BLACK)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=WHITE)
    _center(draw, cx, cy, text.strip(), fl, RED)


def _old_twotone(draw, top_txt, label_txt, X, Y, S, W, H):
    """Oude actie-sticker met twee regels (bv. '1+1' boven, 'GRATIS' onder; of '25%' + 'KORTING'):
    rode tekst met witte buitenlijn + zwarte slagschaduw, rechts op de kaart. 1-op-1 met de PLUS-
    referentie (SK Maxi-cel: bovent. ~0.44·H hoog rond cx≈0.83, label licht overlappend eronder)."""
    # Vaste bounding box in de rechterhelft van de kaart; alles wordt binnen deze box gepast zodat
    # het NOOIT over de kaartrand/gutter loopt - ongeacht de tekstbreedte ('1+1' vs '2 HALEN').
    bx0, bx1 = X(0.505), X(0.985)
    bcx = (bx0 + bx1) / 2
    bw = bx1 - bx0
    ft, tw, th, tb = fit(draw, top_txt, W_BLACK, bw * 0.98, 0.30 * H)
    ow = max(4, int(ft.size * 0.095)); sh = max(3, int(ft.size * 0.06))
    tx = bcx - tw / 2
    ty = Y(0.30)
    draw.text((tx - tb[0] + sh, ty - tb[1] + sh), top_txt, font=ft, fill=BLACK)   # schaduw
    _outline_text(draw, tx - tb[0], ty - tb[1], top_txt, ft, RED, WHITE, ow)
    fl, lw, lh, lb = fit(draw, label_txt, W_BLACK, bw * 0.98, 0.145 * H)
    ow2 = max(4, int(fl.size * 0.095)); sh2 = max(3, int(fl.size * 0.06))
    lx = bcx - lw / 2
    ly = ty + th + lh * 0.02   # net onder de boventekst (geen overlap)
    draw.text((lx - lb[0] + sh2, ly - lb[1] + sh2), label_txt, font=fl, fill=BLACK)
    _outline_text(draw, lx - lb[0], ly - lb[1], label_txt, fl, RED, WHITE, ow2)


def _old_price(canvas, draw, d, X, Y, S, W, H):
    """Rode 'sticker'-prijs - 1-op-1 met de PLUS-referentie: groot geheel + grote superscript-centen,
    witte buitenlijn + zwarte slagschaduw. Geheel ink-links ~0.46, onderkant ~0.98; centen rechtsboven."""
    av = str(d.get('av', '')).replace('€', '').replace(',', '.').strip()
    if not av:
        return
    parts = av.split('.')
    whole = (parts[0] or '0') + '.'
    cent = (parts[1] + '00')[:2] if len(parts) > 1 and parts[1] else '-'
    # vaste groottes (fractie van kaarthoogte), exact als de referentie
    Sw = S(0.50); Sc = int(Sw * 0.585)
    fg = F(W_BLACK, Sw); gb = draw.textbbox((0, 0), whole, font=fg)
    fc = F(W_BLACK, Sc); cb = draw.textbbox((0, 0), cent, font=fc)
    gw = gb[2] - gb[0]; cw = cb[2] - cb[0]
    # bij brede (meercijferige) prijs alles evenredig verkleinen zodat het binnen de kaart past
    # De hele prijs (geheel + centen) moet passen tussen ink-links 0.46W en de rechterrand 0.98W -
    # anders (bv. in een smallere SK Maxi-cel) loopt 'ie over de kaartrand/gutter. Schaal totaal terug.
    avail = 0.52 * W
    total = gw + cw + int(Sw * 0.02)
    if total > avail:
        f = avail / total
        Sw = max(8, int(Sw * f)); Sc = max(6, int(Sc * f))
        fg = F(W_BLACK, Sw); gb = draw.textbbox((0, 0), whole, font=fg)
        fc = F(W_BLACK, Sc); cb = draw.textbbox((0, 0), cent, font=fc)
        gw = gb[2] - gb[0]; cw = cb[2] - cb[0]
    ow = max(3, int(Sw * 0.055))
    sh = max(3, int(Sw * 0.05))
    # geheel: ink-links op 0.46W, onderkant op 0.98H
    wx = X(0.46) - gb[0]
    wy = Y(0.98) - gb[3]
    # centen: grote superscript, direct rechts van het geheel, bovenaan iets lager dan de top
    cx = wx + gw - int(Sc * 0.06)
    cy = (wy + gb[1]) + int(Sw * 0.14) - cb[1]
    # zwarte slagschaduw
    draw.text((wx + sh, wy + sh), whole, font=fg, fill=BLACK)
    draw.text((cx + sh, cy + sh), cent, font=fc, fill=BLACK)
    # witte buitenlijn + rode vulling
    _outline_text(draw, wx, wy, whole, fg, RED, WHITE, ow)
    _outline_text(draw, cx, cy, cent, fc, RED, WHITE, ow)


def _old_actie_landscape(canvas, draw, d, ox, oy, W, H):
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    def S(p): return max(6, int(p * H))
    LX = X(0.03)
    colw = 0.44 * W
    y = Y(0.026)
    kop = str(d.get('koptekst', '')).strip() or str(d.get('merk', '')).strip()
    if kop:
        f = F(W_BLACK, S(0.077))
        for line in wrap(kop, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.079)
        y += S(0.010)
    sub = str(d.get('subtekst', '')).strip()
    if sub:
        f = F(W_BOOK, S(0.056))
        for line in wrap(sub, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.059)
    vbt = str(d.get('vbtekst', '')).strip() or str(d.get('aanv', '')).strip()
    if vbt:
        y += S(0.006); f = F(W_BOOK, S(0.032))
        for line in wrap(vbt, f, colw, draw):
            draw.text((LX, y), line, font=f, fill=BLACK); y += S(0.036)

    # Actie-mechanisme rechts: tweekleurige sticker (1+1 GRATIS / X% KORTING / …) of prijssticker.
    spec = _action_spec(d)
    if spec[0] == 'two':
        _old_twotone(draw, spec[1], spec[2], X, Y, S, W, H)
    else:
        # vanprijs (rode balk met witte doorgestreepte tekst)
        vptxt = ' - '.join(filter(None, [str(d.get('vp1', '')).strip(), str(d.get('vp2', '')).strip()]))
        if vptxt:
            fv = F(W_BLACK, S(0.069))
            vw, vh, vb = _tw(draw, vptxt, fv)
            bx0, by0 = X(0.044), Y(0.555)
            bx1, by1 = bx0 + vw + S(0.05), by0 + vh + S(0.03)
            sh = S(0.012)
            draw.rectangle([bx0 + sh, by0 + sh, bx1 + sh, by1 + sh], fill=BLACK)   # schaduw
            draw.rectangle([bx0, by0, bx1, by1], fill=RED)
            tx = bx0 + S(0.025); ty = by0 + S(0.015)
            draw.text((tx - vb[0], ty - vb[1]), vptxt, font=fv, fill=WHITE)
            midy = ty + vh / 2
            draw.line([(tx, midy), (tx + vw, midy)], fill=WHITE, width=max(2, S(0.008)))
        # grote rode prijs (sticker) rechts - eerst tekenen zodat het label eroverheen komt
        _old_price(canvas, draw, d, X, Y, S, W, H)
        # wit label (verpakking + inhoud) - overlapt de bovenkant van de prijs
        vlbl = ' '.join(filter(None, [str(d.get('verpakking', '')).strip(), str(d.get('inhoud', '')).strip()]))
        if vlbl:
            _old_label(draw, X(0.72), Y(0.40), vlbl, 0.44 * W, S(0.052))

    # onderregels
    kilo = str(d.get('kilo', '')).strip()
    if kilo:
        f = F(W_BOOK, S(0.028)); kw, _, _ = _tw(draw, kilo, f)
        draw.text((X(0.955) - kw, Y(0.90)), kilo, font=f, fill=BLACK)
    land = str(d.get('land', '')).strip()
    if land:
        draw.text((LX, Y(0.88)), f'Land van herkomst: {land}', font=F(W_BOLD, S(0.024)), fill=BLACK)
    mx = str(d.get('max', '')).strip()
    if mx:
        draw.text((LX, Y(0.915)), f'Maximaal {mx} aanbiedingen per klant tenzij anders vermeld.',
                  font=F(W_NARROW, S(0.022)), fill=BLACK)


def _old_actie_portrait(canvas, draw, d, ox, oy, W, H):
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    def S(p): return max(6, int(p * H))
    LX = X(0.06); colw = 0.88 * W; R = W
    y = _txt_block(canvas, draw, d, LX, Y(0.035), colw, R)
    # vanprijs + label + prijs onderin, gecentreerd
    vptxt = ' - '.join(filter(None, [str(d.get('vp1', '')).strip(), str(d.get('vp2', '')).strip()]))
    if vptxt:
        fv = F(W_BLACK, S(0.05)); vw, vh, vb = _tw(draw, vptxt, fv)
        cx = X(0.5); bx0 = cx - vw / 2 - S(0.02); bx1 = cx + vw / 2 + S(0.02)
        by0 = Y(0.55)
        draw.rectangle([bx0, by0, bx1, by0 + vh + S(0.02)], fill=RED)
        draw.text((cx - vw / 2 - vb[0], by0 + S(0.01) - vb[1]), vptxt, font=fv, fill=WHITE)
        draw.line([(cx - vw / 2, by0 + S(0.01) + vh / 2), (cx + vw / 2, by0 + S(0.01) + vh / 2)], fill=WHITE, width=max(2, S(0.006)))
    _old_price(canvas, draw, d, X, Y, S, W, H)
    vlbl = ' '.join(filter(None, [str(d.get('verpakking', '')).strip(), str(d.get('inhoud', '')).strip()]))
    if vlbl:
        _old_label(draw, X(0.72), Y(0.40), vlbl, 0.44 * W, S(0.05))


# ─── NIEUWE TIP-KAART (modern: groene TIP-pill + prijsblok) ────────────────────
def _split_price(s):
    """'1.79' / '1,79' -> ('1','79'); '2' -> ('2',''). Voor de tip-prijs (euro groot, centen superscript)."""
    s = str(s or '').strip().replace(',', '.')
    if not s:
        return '', ''
    if '.' in s:
        a, b = s.split('.', 1)
        return a, (b + '00')[:2]
    return s, ''

_tiptex_cache = {}
def _tip_tex(kind):
    """Laad de exacte tip-band/-paneel-textuur uit static/img (uit de PLUS-referentie geëxtraheerd)."""
    if kind in _tiptex_cache:
        return _tiptex_cache[kind]
    fn = 'tip_band.png' if kind == 'band' else 'tip_panel.png'
    im = None
    try:
        im = Image.open(os.path.join(os.path.dirname(__file__), 'static', 'img', fn)).convert('RGB')
    except Exception:
        im = None
    _tiptex_cache[kind] = im
    return im

def _tip_band_fill(canvas, draw, x0, y0, x1, y1, kind='band'):
    """Vul de tip-band/het paneel met de exacte textuur uit de referentie (val terug op de vlakke kleur)."""
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    tex = _tip_tex(kind)
    if tex is not None:
        canvas.paste(tex.resize((w, h), Image.LANCZOS), (x0, y0))
    else:
        draw.rectangle([x0, y0, x1, y1], fill=NEWTIP_BAND)

def _newtip_priceblock(canvas, draw, d, x0, y0, x1, y1):
    """Fel-groen prijsblok met witte gecentreerde 'inhoud'-regel (verpakking + inhoud) en witte prijs:
    euro groot (steekt onder de rand uit) + centen als superscript. 1:1 met de PLUS tip-referentie."""
    draw.rectangle([x0, y0, x1, y1], fill=NEWTIP_GREEN)
    bw = x1 - x0; bh = y1 - y0; cx = (x0 + x1) / 2
    def s(p): return max(6, int(p * bh))
    verp = str(d.get('verpakking', '')).strip(); inh = str(d.get('inhoud', '')).strip()
    lbl = ' '.join(filter(None, [verp, inh]))
    if lbl:
        lf, _, _, _ = fit(draw, lbl, W_BOLD, bw * 0.80, s(0.165))
        _center(draw, cx, y0 + bh * 0.135, lbl, lf, WHITE)
    price = str(d.get('av') or d.get('tipprijs') or d.get('prijs') or '').strip()
    euro, cents = _split_price(price)
    if not euro:
        return
    # 'euro.' groot + centen als kleinere superscript; de hele prijs GECENTREERD en BINNEN het vak.
    esz = 0.80
    while esz > 0.40:
        ef = F(W_BLACK, s(esz)); cf = F(W_BLACK, s(esz * 0.58))
        eb = draw.textbbox((0, 0), euro, font=ef); dgw = eb[2] - eb[0]
        dotw = _tw(draw, '.', ef)[0]
        cb = draw.textbbox((0, 0), cents, font=cf) if cents else (0, 0, 0, 0); cw = cb[2] - cb[0]
        gap = dotw * 0.25
        groupw = dgw + gap + cw
        if groupw <= bw * 0.88:
            break
        esz -= 0.04
    ef = F(W_BLACK, s(esz)); cf = F(W_BLACK, s(esz * 0.58))
    eb = draw.textbbox((0, 0), euro, font=ef); dgw = eb[2] - eb[0]
    dotw = _tw(draw, '.', ef)[0]
    cb = draw.textbbox((0, 0), cents, font=cf) if cents else (0, 0, 0, 0); cw = cb[2] - cb[0]
    gap = dotw * 0.25
    groupw = dgw + gap + cw
    gx = cx - groupw / 2                                 # links van de prijsgroep (gecentreerd in het vak)
    g_bottom = y1 - bh * 0.05                            # onderkant van de euro-cijfers met marge binnen het vak
    gy = g_bottom - eb[3]
    draw.text((gx - eb[0], gy), euro, font=ef, fill=WHITE)
    # vierkante punt (Gotham gebruikt een blokje, geen rond punt) op de basislijn, direct na de cijfers
    ds = (eb[3] - eb[1]) * 0.155
    dx = gx + dgw + dotw * 0.12
    draw.rectangle([dx, g_bottom - ds, dx + ds, g_bottom], fill=WHITE)
    if cents:
        cy = gy + eb[1] - cb[1]                          # centen glyph-top == euro glyph-top → superscript
        draw.text((gx - eb[0] + dgw + gap - cb[0], cy), cents, font=cf, fill=WHITE)

def _newtip_title(draw, d, lx, top_y, W, H, mk, pk, vk):
    """Merk (black) + Productomschrijving (black) + variëteitomschrijving (book), linksuitgelijnd.
    mk/pk/vk = fontgroottes (fractie van H). Tekst krimpt automatisch tot hij binnen de breedte past
    (ook lange woorden op smalle kaarten). Geeft de y na de titel terug."""
    tw = W - lx            # beschikbare breedte (van lx tot rechterrand)
    def S(p): return max(6, int(p * H))
    def fit_block(text, weight, base):
        """Grootste fontgrootte waarbij alle (afgebroken) regels binnen tw passen."""
        size = float(base)
        while size >= 6:
            f = F(weight, int(size))
            lines = wrap(text, f, tw, draw)
            if all(_tw(draw, l, f)[0] <= tw for l in lines):
                return f, int(size), lines
            size *= 0.93
        f = F(weight, 6)
        return f, 6, wrap(text, f, tw, draw)
    y = top_y
    merk = str(d.get('merk', '')).strip()
    kop  = str(d.get('koptekst', '')).strip()
    sub  = str(d.get('subtekst', '')).strip()
    if merk:
        f, sz, lines = fit_block(merk, W_BLACK, S(mk))
        for line in lines:
            draw.text((lx, y), line, font=f, fill=BLACK); y += sz * 1.07
    if kop:
        f, sz, lines = fit_block(kop, W_BLACK, S(pk))
        for line in lines:
            draw.text((lx, y), line, font=f, fill=BLACK); y += sz * 1.07
    if sub:
        f, sz, lines = fit_block(sub, W_BOOK, S(vk))
        for line in lines:
            draw.text((lx, y), line, font=f, fill=BLACK); y += sz * 1.14
    return y

def _newtip_footer(canvas, draw, d, lx, land_y, H, on_green_nix=False, nix_x=None, nix_y=None):
    """NIX18 (alleen bij alcohol) + 'Land van herkomst' + 'Aanvullende tekst', onderaan de kaart."""
    def S(p): return max(6, int(p * H))
    # NIX18-blok (leeftijdsgrens) - alleen bij alcohol=ja
    if str(d.get('alcohol', 'nee')).strip() == 'ja':
        nx = nix_x if nix_x is not None else lx
        ny = nix_y if nix_y is not None else (land_y - S(0.13))
        fnix = F(W_NARROW, S(0.017))
        col = WHITE if on_green_nix else GREY
        draw.text((nx, ny), '< 25 jaar? laat je legitimatie zien!', font=fnix, fill=col); ny += S(0.020)
        draw.text((nx, ny), '< 18 jaar verkopen wij geen alcohol', font=fnix, fill=col); ny += S(0.024)
        ni = _nix_logo_img(S(0.045), white=on_green_nix)
        if ni:
            canvas.paste(ni, (int(nx), int(ny)), ni)
    land = str(d.get('land', '')).strip()
    aanv = str(d.get('aanv', '')).strip()
    y = land_y
    if land:
        draw.text((lx, y), f'Land van herkomst: {land}', font=F(W_NARROW, S(0.016)), fill=BLACK)
    y += S(0.028)
    if aanv:
        draw.text((lx, y), f'Aanvullende tekst: {aanv}', font=F(W_NARROW, S(0.014)), fill=BLACK)

_nixw_cache = {}
def _nix_logo_img(h, white=False):
    """NIX18-logo; white=True geeft een wit gemaskeerde versie (voor op de groene achtergrond)."""
    im = nix_img(h)
    if im is None or not white:
        return im
    key = ('w', h)
    if key in _nixw_cache:
        return _nixw_cache[key]
    a = im.split()[-1]
    wimg = Image.new('RGBA', im.size, (255, 255, 255, 0))
    wimg.putalpha(a)
    solid = Image.new('RGBA', im.size, (255, 255, 255, 255))
    solid.putalpha(a)
    _nixw_cache[key] = solid
    return solid

# Per-formaat tip-configuratie (uit de PLUS-referenties gemeten). k='top' = groene band bovenaan,
# k='left' = groen linkerpaneel. mk/vk = titelgroottes (fractie van kaarthoogte); pb = prijsblok
# (x0,y0,x1,y1 als fractie); tr = rechterrand van de titel; land = y van 'Land van herkomst'.
_NEWTIP_CFG = {
    'a3_liggend': {'k': 'top',  'band': 0.267, 'tx': 0.030, 'ty': 0.31,  'mk': 0.049, 'vk': 0.036, 'tr': 0.56,
                   'pb': (0.583, 0.551, 0.970, 0.916), 'nix': 'bl', 'land': 0.905},
    'a5_staand':  {'k': 'top',  'band': 0.199, 'tx': 0.050, 'ty': 0.205, 'mk': 0.044, 'vk': 0.032, 'tr': 0.95,
                   'pb': (0.189, 0.631, 0.811, 0.923), 'nix': 'br', 'land': 0.940},
    'a4_staand':  {'k': 'top',  'band': 0.201, 'tx': 0.048, 'ty': 0.205, 'mk': 0.054, 'vk': 0.036, 'tr': 0.95,
                   'pb': (0.208, 0.650, 0.792, 0.926), 'nix': 'br', 'land': 0.941},
    'a3_staand':  {'k': 'top',  'band': 0.202, 'tx': 0.067, 'ty': 0.215, 'mk': 0.046, 'vk': 0.034, 'tr': 0.95,
                   'pb': (0.190, 0.634, 0.810, 0.926), 'nix': 'br', 'land': 0.939},
    'sk_maxi':    {'k': 'left', 'panel': 0.27,  'tx': 0.30, 'ty': 0.04,  'mk': 0.077, 'vk': 0.048, 'tr': 0.99,
                   'pb': (0.60, 0.50, 0.95, 0.87),  'land': 0.89},
    'sk_mini':    {'k': 'left', 'panel': 0.367, 'tx': 0.40, 'ty': 0.045, 'mk': 0.048, 'vk': 0.036, 'tr': 0.99,
                   'pb': (0.52, 0.62, 0.99, 0.88),  'land': 0.90},
    'sk_middel':  {'k': 'left', 'panel': 0.10,  'tx': 0.13, 'ty': 0.06,  'mk': 0.19,  'vk': 0.13,  'tr': 0.99,
                   'pb': (0.62, 0.22, 0.98, 0.90),  'land': 0.93},
}

def _newtip_topband(canvas, draw, d, ox, oy, W, H, cfg):
    """Tip-kaart met groene band bovenaan, titel links, groot (gecentreerd) prijsblok eronder."""
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    _tip_band_fill(canvas, draw, ox, oy, ox + W, oy + cfg['band'] * H, 'band')
    lx = X(cfg['tx'])
    _newtip_title(draw, d, lx, Y(cfg['ty']), X(cfg['tr']), H, mk=cfg['mk'], pk=cfg['mk'], vk=cfg['vk'])
    pb = cfg['pb']
    _newtip_priceblock(canvas, draw, d, X(pb[0]), Y(pb[1]), X(pb[2]), Y(pb[3]))
    ly = Y(cfg['land'])
    if cfg.get('nix') == 'br':      # portret: NIX18 rechtsonder, land/aanvullende linksonder
        _newtip_footer(canvas, draw, d, lx, ly, H, on_green_nix=False,
                       nix_x=X(0.80), nix_y=Y(cfg['land'] - 0.055))
    else:                           # A3 liggend: NIX18 linksonder boven de land-regel
        _newtip_footer(canvas, draw, d, lx, ly, H, on_green_nix=False, nix_y=Y(cfg['land'] - 0.11))

def _newtip_leftpanel(canvas, draw, d, ox, oy, W, H, cfg):
    """Compacte tip-kaart met groen linkerpaneel (NIX18 onderin), titel + prijsblok rechts."""
    def X(p): return ox + p * W
    def Y(p): return oy + p * H
    _tip_band_fill(canvas, draw, ox, oy, ox + cfg['panel'] * W, oy + H, 'panel')
    lx = X(cfg['tx'])
    _newtip_title(draw, d, lx, Y(cfg['ty']), X(cfg['tr']), H, mk=cfg['mk'], pk=cfg['mk'], vk=cfg['vk'])
    pb = cfg['pb']
    _newtip_priceblock(canvas, draw, d, X(pb[0]), Y(pb[1]), X(pb[2]), Y(pb[3]))
    _newtip_footer(canvas, draw, d, lx, Y(cfg['land']), H, on_green_nix=True,
                   nix_x=X(cfg['panel'] * 0.08), nix_y=Y(cfg['land'] - 0.11))

def _newtip(canvas, draw, d, ox, oy, W, H):
    """Nieuwe tip-kaart, per formaat 1:1 met de PLUS-referentie."""
    fmt = str(d.get('_fmt', '')).strip()
    cfg = _NEWTIP_CFG.get(fmt) or (_NEWTIP_CFG['a3_liggend'] if W >= H else _NEWTIP_CFG['a4_staand'])
    if cfg['k'] == 'left':
        _newtip_leftpanel(canvas, draw, d, ox, oy, W, H, cfg)
    else:
        _newtip_topband(canvas, draw, d, ox, oy, W, H, cfg)
    # Barcodes (net als de actie-kaart, 1:1 de A3-referentie). Liggend: onderin.
    # Staand: net boven het prijsblok, zodat de tegels het prijsblok niet overlappen.
    land = W >= H
    yb = None if land else max(0.30, cfg['pb'][1] - 0.03)
    _draw_scans(canvas, draw, d, ox, oy, W, H, land, y_bottom_frac=yb)


def _prijs_zwart(canvas, draw, d, x0, x1, y0, y1):
    """Groene labelbalk + grote ZWARTE prijs (voor de moderne tip-kaart)."""
    bw = x1 - x0; cx = (x0 + x1) / 2
    verp = str(d.get('verpakking', '')).strip(); inh = str(d.get('inhoud', '')).strip()
    vlbl = ' '.join(filter(None, [verp, inh]))
    top = y0
    if vlbl:
        fl, lw, lh, lb = fit(draw, vlbl, W_BOLD, bw * 0.9, (y1 - y0) * 0.16)
        draw.rectangle([x0, y0, x1, y0 + lh * 1.9], fill=GREEN)
        _center(draw, cx, y0 + lh * 0.95, vlbl, fl, WHITE)
        top = y0 + lh * 1.9 + (y1 - y0) * 0.03
    av = str(d.get('av', '')).replace('€', '').replace(',', '.').strip()
    if not av:
        return
    parts = av.split('.'); whole = (parts[0] or '0') + '.'
    cent = (parts[1] + '00')[:2] if len(parts) > 1 and parts[1] else '-'
    fg, gw, gh, gb = fit(draw, whole, W_BLACK, bw * 0.64, (y1 - top) * 0.96)
    fc = F(W_BLACK, max(6, int(fg.size * 0.55)))
    cw, ch, cb = _tw(draw, cent, fc)
    sx = cx - (gw + cw) / 2
    gy = y1 - gb[3]
    draw.text((sx - gb[0], gy), whole, font=fg, fill=BLACK)
    draw.text((sx + gw - cb[0], gy + gb[1] - cb[1]), cent, font=fc, fill=BLACK)


_PRINT_DPI = 300
# Printformaten in millimeter (breedte, hoogte)
# Printformaten in millimeter (breedte, hoogte). SK Maxi = A4-liggend vel met 4 kaarten.
_SIZES_MM = {
    'sk_mini':    (75,  88),
    'sk_middel':  (270, 70),
    'sk_maxi':    (297, 210),   # A4 liggend vel - 4 kaarten (afscheurpapier)
    'a5_staand':  (148, 210),
    'a4_staand':  (210, 297),
    'a3_staand':  (297, 420),
    'a3_liggend': (420, 297),
}

# Nette labels voor opslag/weergave
FORMAAT_LABELS = {
    'sk_mini':   'SK Mini', 'sk_middel': 'SK Middel', 'sk_maxi': 'SK Maxi',
    'a5_staand': 'A5 staand', 'a4_staand': 'A4 staand',
    'a3_staand': 'A3 staand', 'a3_liggend': 'A3 liggend',
}

# SK Maxi: 4 kaart-cellen op het A4-vel, posities afgestemd op de originele PDF (afscheurpapier)
_SK_CELLS = [(0.075, 0.082), (0.075 + 0.462, 0.082),
             (0.075, 0.082 + 0.418), (0.075 + 0.462, 0.082 + 0.418)]
_SK_CW, _SK_CH = 0.462, 0.418

def _px(mm, dpi=_PRINT_DPI):
    return max(1, round(mm / 25.4 * dpi))

def card_basename(image_name):
    """kaart_XXX.png -> kaart_XXX (zonder extensie)."""
    return os.path.splitext(image_name)[0]

def remove_card_files(image_name):
    """Verwijder zowel de PNG-preview als de PDF van een kaart."""
    if not image_name:
        return
    stem = card_basename(image_name)
    for ext in ('.png', '.pdf'):
        try:
            p = os.path.join(app.config['EXPORT_FOLDER'], stem + ext)
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

def _dir_size(path):
    """Totale grootte (bytes) van alle bestanden in een map."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total

def run_card_cleanup(days):
    """Verwijder kaarten (rijen + bestanden) ouder dan `days` dagen. Geeft (aantal, vrijgemaakte_bytes)."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return (0, 0)
    if days <= 0:
        return (0, 0)
    cutoff = datetime.now() - timedelta(days=days)
    old = Card.query.filter(Card.timestamp < cutoff).all()
    freed = 0
    for c in old:
        stem = card_basename(c.image) if c.image else ''
        if stem:
            for ext in ('.png', '.pdf'):
                p = os.path.join(app.config['EXPORT_FOLDER'], stem + ext)
                try:
                    if os.path.exists(p):
                        freed += os.path.getsize(p)
                except OSError:
                    pass
        remove_card_files(c.image)
        db.session.delete(c)
    if old:
        db.session.commit()
    return (len(old), freed)

def auto_cleanup_tick():
    """Draait de automatische opruimregel hooguit één keer per 6 uur (indien ingeschakeld)."""
    if get_setting('cleanup_auto', '0') != '1':
        return
    try:
        days = int(get_setting('cleanup_days', '0') or '0')
    except ValueError:
        days = 0
    if days <= 0:
        return
    last = get_setting('cleanup_last_run', '')
    if last:
        try:
            if datetime.now() - datetime.fromisoformat(last) < timedelta(hours=6):
                return
        except ValueError:
            pass
    n, _ = run_card_cleanup(days)
    set_setting('cleanup_last_run', datetime.now().isoformat(timespec='seconds'))

_CARD_CONTENT_KEYS = ['merk', 'koptekst', 'subtekst', 'vbtekst', 'aanv', 'verpakking', 'inhoud',
                      'vp1', 'vp2', 'av', 'av2', 'prijs', 'tipprijs', 'kilo', 'land', 'code',
                      'scans', 'overlay']
def _card_has_content(d):
    """True als de kaart iets ingevuld heeft (tekst, prijs, barcode of foto)."""
    return isinstance(d, dict) and any(str(d.get(k, '')).strip() for k in _CARD_CONTENT_KEYS)

def generate_kaart(kaarten, formaat):
    w_mm, h_mm = _SIZES_MM.get(formaat, (420, 297))
    W, H = _px(w_mm), _px(h_mm)
    canvas = Image.new('RGB', (W, H), WHITE)
    draw   = ImageDraw.Draw(canvas)

    if formaat == 'sk_maxi':
        cw, ch = int(_SK_CW * W), int(_SK_CH * H)
        for i, (fx, fy) in enumerate(_SK_CELLS):
            d = dict(kaarten[i]) if i < len(kaarten) and isinstance(kaarten[i], dict) else {}
            if not _card_has_content(d):
                continue                      # lege kaart → blijft wit (bespaart inkt)
            d['_fmt'] = formaat
            draw_kaart(canvas, draw, d, int(fx * W), int(fy * H), cw, ch)
    else:
        d = dict(kaarten[0]) if kaarten and isinstance(kaarten[0], dict) else {}
        d['_fmt'] = formaat
        draw_kaart(canvas, draw, d, 0, 0, W, H)

    # Naam met tijdstempel én een korte willekeurige sleutel, zodat bestandsnamen niet te raden zijn.
    stem   = f"kaart_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:18]}_{secrets.token_hex(4)}"
    folder = app.config['EXPORT_FOLDER']

    # 1) PDF - het te printen product, op exact A3/A4-formaat (300 dpi)
    canvas.save(os.path.join(folder, stem + '.pdf'), 'PDF',
                resolution=_PRINT_DPI)

    # 2) PNG - lichte preview voor het dashboard
    prev = canvas.copy()
    prev.thumbnail((1400, 1400), Image.LANCZOS)
    prev.save(os.path.join(folder, stem + '.png'), 'PNG', optimize=True, compress_level=6)

    return stem + '.png'   # Card.image blijft de preview; PDF heeft dezelfde stam

# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────
# Sterke wachtwoord-hashing: scrypt is geheugen-hard (bestand tegen GPU/ASIC brute force) én snel.
_PW_METHOD = 'scrypt'
def hash_password(pw):
    """Hash een wachtwoord met het huidige sterke schema (scrypt)."""
    return generate_password_hash(pw, method=_PW_METHOD)

def _check_pw(user, pw):
    """Controleer het wachtwoord. Ondersteunt oude (pbkdf2/plain-text) hashes en upgradet die bij een
    geslaagde login transparant naar scrypt - zo blijven alle bestaande accounts werken."""
    stored = user.password or ''
    if stored.startswith('scrypt:') or stored.startswith('pbkdf2:'):
        if not check_password_hash(stored, pw):
            return False
        if not stored.startswith('scrypt:'):        # upgrade oud schema → scrypt
            try:
                user.password = hash_password(pw); db.session.commit()
            except Exception:
                db.session.rollback()
        return True
    # Legacy plain-text - check en direct hashen
    if stored == pw:
        try:
            user.password = hash_password(pw); db.session.commit()
        except Exception:
            db.session.rollback()
        return True
    return False

def find_user_by_name(username):
    """Zoek gebruiker op gebruikersnaam - hoofdletterongevoelig."""
    if not username:
        return None
    return User.query.filter(func.lower(User.username) == username.strip().lower()).first()

def find_user_by_email(email):
    if not email:
        return None
    return User.query.filter(func.lower(User.email) == email.strip().lower()).first()

def get_current_user():
    uid = session.get('uid')
    if uid is None:
        # migratie van oude sessies die nog op gebruikersnaam draaien
        if 'username' in session:
            u = find_user_by_name(session['username'])
            if u: session['uid'] = u.id
            return u
        return None
    u = User.query.get(uid)
    # Sessie ongeldig zodra het wachtwoord is gewijzigd (marker uit login komt niet meer overeen).
    if u is not None and 'pwv' in session and session.get('pwv') != _pw_marker(u):
        session.clear()
        return None
    return u

def login_required(f):
    from functools import wraps
    @wraps(f)
    def dec(*a,**k):
        if 'uid' not in session and 'username' not in session: return redirect(url_for('login'))
        return f(*a,**k)
    return dec

# ─── LOGIN-THROTTLE (server-side, per IP+account) ─────────────────────────────
# De oude teller stond alleen in de sessiecookie en was dus te omzeilen door de cookie weg te gooien.
# Deze teller staat in het serverproces: een aanvaller kan hem niet resetten. Bij een herstart is hij
# leeg - acceptabel, en legitieme gebruikers merken er niets van.
_LOGIN_MAX = 8                # max mislukte pogingen ...
_LOGIN_WINDOW = 300           # ... binnen dit venster (seconden) → daarna tijdelijk blokkeren
# Rate-limiting staat in de GEDEELDE store (sharedstate) zodat 'ie klopt over meerdere gunicorn-workers.

def _login_key(un):
    return (client_ip() or '?') + '|' + (un or '').strip().lower()

def _login_blocked_secs(un):
    now = time.time()
    count, oldest = sharedstate.rl_active(_login_key(un), _LOGIN_WINDOW, now)
    if count >= _LOGIN_MAX:
        return int(_LOGIN_WINDOW - (now - oldest)) + 1
    return 0

def _login_record_fail(un):
    sharedstate.rl_record(_login_key(un))
    # af en toe globaal opschonen zodat de tabel niet groeit
    if secrets.randbelow(50) == 0:
        sharedstate.rl_gc(_LOGIN_WINDOW)

def _login_reset(un):
    sharedstate.rl_reset(_login_key(un))

# ─── TWEE-FACTOR-AUTHENTICATIE (TOTP) ─────────────────────────────────────────
def mfa_required_for(user):
    """MFA is verplicht voor superadmins; overige rollen alleen als ze het zelf inschakelen."""
    return user is not None and user.role == 'admin'

def _mfa_uri(user):
    import pyotp
    label = user.email or user.username or f'user{user.id}'
    return pyotp.totp.TOTP(user.mfa_secret).provisioning_uri(name=label, issuer_name='PLUSLokaal Schapkaarten')

def _mfa_qr_datauri(uri):
    """Genereer een QR-code voor de otpauth-URI als data-URL (PNG), voor de setup-pagina."""
    import qrcode, io, base64
    img = qrcode.make(uri)
    bio = io.BytesIO(); img.save(bio, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(bio.getvalue()).decode()

def _mfa_check(user, code):
    import pyotp
    code = (code or '').replace(' ', '').strip()
    if not user or not user.mfa_secret or not code.isdigit():
        return False
    return pyotp.TOTP(user.mfa_secret).verify(code, valid_window=1)

def _pw_marker(user):
    """Korte vingerafdruk van het huidige wachtwoord-hash. Verandert zodra het wachtwoord wijzigt,
    zodat bestaande sessies dan automatisch ongeldig worden (= 'ingelogd tot wachtwoordwijziging')."""
    import hashlib
    return hashlib.sha256((getattr(user, 'password', '') or '').encode()).hexdigest()[:16]

def _finish_login(user):
    """Rond de login volledig af (na wachtwoord + evt. MFA)."""
    # "Aangemeld blijven" (standaard aan): permanente cookie die het sluiten van de browser overleeft.
    # Uitgevinkt → sessiecookie die bij het afsluiten verdwijnt. Keuze komt uit de wachtwoordstap.
    session.permanent = session.pop('remember_me', True)
    session['uid'] = user.id
    session['pwv'] = _pw_marker(user)   # ongeldig zodra het wachtwoord wijzigt
    session.pop('username', None)
    session.pop('pre_auth_uid', None)
    session.pop('login_fails', None)
    session.pop('login_fail_time', None)
    # Rondleiding staat aan? Dan bij ELKE login opnieuw aanbieden (los van eerder wegklikken).
    session['plt_welcome_pending'] = bool(getattr(user, 'show_tour', False))
    log_action('login', ('ingelogd' + (f' · {device_str()}' if device_str() else '')), user=user)
    if getattr(user, 'must_change_password', False):
        flash('Stel eerst een eigen wachtwoord in.', 'success')
        return redirect(url_for('profile'))
    return redirect(url_for('dashboard'))

# ─── FILIAAL NAAM HELPER ──────────────────────────────────────────────────────
def filiaal_display(user_or_filiaal, naam=None):
    nr = user_or_filiaal.filiaal if hasattr(user_or_filiaal, 'filiaal') else user_or_filiaal
    if not naam:
        f = Filiaal.query.filter_by(nummer=nr).first()
        naam = f.naam if f else None
    if naam:
        return f'PLUS {naam} ({nr})'
    return f'PLUS Filiaal {nr}'

app.jinja_env.globals['filiaal_display'] = filiaal_display

@app.after_request
def _security_headers(resp):
    """Veilige standaard-headers. HSTS dwingt HTTPS af (browsers negeren het over http).
    De overige headers hebben geen zichtbaar effect op normaal gebruik, maar sluiten
    clickjacking en MIME-sniffing uit."""
    resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    # Statische bestanden laten cachen door de browser → niet elk bezoek opnieuw ophalen (voorkomt dat een
    # hapering in één van die vele verzoeken de pagina ongestyled laat of foto's laat missen; ook sneller).
    try:
        p = request.path or ''
        if request.method == 'GET' and resp.status_code == 200 and p.startswith('/static/'):
            if p.startswith('/static/export/'):
                resp.headers['Cache-Control'] = 'private, max-age=2592000, immutable'   # unieke bestandsnaam per render
            elif p.startswith(('/static/fonts/', '/static/img/', '/static/sjablonen/')):
                resp.headers['Cache-Control'] = 'public, max-age=86400'                  # wijzigen zelden
            elif p.startswith('/static/css/') or p.startswith('/static/js/'):
                # CSS/JS worden via ?v=versie in de templates ge-cache-bust → mogen lang gecachet worden;
                # bij een nieuwe versie verandert de URL vanzelf, dus nooit een verouderde stijl/script.
                if request.args.get('v'):
                    resp.headers['Cache-Control'] = 'public, max-age=604800'
                else:
                    resp.headers['Cache-Control'] = 'public, max-age=3600'
    except Exception:
        pass
    return resp

@app.before_request
def _guard_export_files():
    """De gerenderde kaart-PDF's/PNG's in static/export mogen niet publiek/anoniem opvraagbaar zijn
    (bestandsnamen zouden anders te raden zijn). Alleen ingelogde gebruikers krijgen ze - de URL's
    zelf blijven exact gelijk, dus voor ingelogde gebruikers verandert er niets."""
    p = request.path or ''
    if p.startswith('/static/export/'):
        # Alleen op de SESSIE checken (geen DB-query per thumbnail → sneller en kan niet falen onder druk;
        # met 14 kaarten op het dashboard scheelt dat 14 database-queries per pagina-load).
        if not (session.get('uid') or session.get('username')):
            return Response('Niet gevonden', status=404, mimetype='text/plain')

# ─── CSRF-BESCHERMING (lichtgewicht, session-token) ───────────────────────────
_CSRF_EXEMPT = {'portaal_view',           # de proxy zet pluslokaal.nl-formulieren (hún eigen tokens) door
                'agent_poll', 'agent_result',   # print-agent authenticeert met X-Agent-Key, geen sessie
                'agent_webpoll', 'agent_webresult'}

@app.context_processor
def _inject_csrf():
    return {'csrf_token': lambda: session.get('csrf_token', '')}

@app.context_processor
def _inject_store_printer():
    """Winkelprinter van de winkel waarin de gebruiker werkt (voor de knop 'Printen op …').
    Superadmin: de gekozen winkel; nog niets gekozen → reason 'choose'. Overigen: eigen winkel."""
    def _store_printer():
        try:
            u = get_current_user()
            if not u:
                return None
            fil = _active_filiaal()
            if fil is None:
                # superadmin die nog geen winkel koos
                return {'name': 'de winkelprinter', 'ready': False, 'reason': 'choose'}
            f = Filiaal.query.filter_by(nummer=fil).first()
            po = bool(f and getattr(f, 'print_only', False))
            if f and (f.doc_printer_ip or _agent_online(f)):
                return {'name': f.doc_printer_name or 'de winkelprinter', 'ready': True, 'print_only': po}
            return {'name': 'de winkelprinter', 'ready': False, 'reason': 'none', 'print_only': po}
        except Exception:
            return {'name': 'de winkelprinter', 'ready': False, 'reason': 'none', 'print_only': False}
    return {'store_printer': _store_printer()}

@app.context_processor
def _inject_winkelkiezer():
    """Voor de superadmin: de lijst winkels + de nu gekozen winkel (header-kiezer)."""
    def _data():
        try:
            u = get_current_user()
            if not is_superadmin(u):
                return None
            fil = session.get('sa_filiaal')
            fils = Filiaal.query.order_by(Filiaal.nummer).all()
            cur = next((f for f in fils if f.nummer == fil), None)
            return {'stores': fils, 'current': cur}
        except Exception:
            return None
    return {'winkelkiezer': _data()}

@app.before_request
def _csrf_protect():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if request.endpoint in _CSRF_EXEMPT:
            return
        sent = request.form.get('_csrf') or request.headers.get('X-CSRFToken', '')
        if not sent or sent != session.get('csrf_token'):
            abort(400)

# ─── LABELS: audit, rollen/capabilities, IP-toegangsbeleid ────────────────────
import ipaddress as _ipaddr

def client_ip():
    """Echte client-IP (achter Cloudflare/nginx)."""
    h = request.headers
    return (h.get('CF-Connecting-IP')
            or (h.get('X-Forwarded-For', '').split(',')[0].strip() or None)
            or request.remote_addr)

def device_str():
    """Korte, leesbare omschrijving van het toestel/de browser uit de User-Agent (voor de logs)."""
    ua = (request.headers.get('User-Agent') or '').strip()
    if not ua:
        return ''
    os_ = ('iPhone' if 'iPhone' in ua else 'iPad' if 'iPad' in ua else 'Android' if 'Android' in ua
           else 'Windows' if 'Windows' in ua else 'macOS' if ('Macintosh' in ua or 'Mac OS' in ua) else
           'Linux' if 'Linux' in ua else '')
    br = ('Edge' if 'Edg' in ua else 'Chrome' if 'Chrome' in ua else 'Firefox' if 'Firefox' in ua
          else 'Safari' if 'Safari' in ua else '')
    return ' · '.join([p for p in (os_, br) if p]) or ua[:40]

def _store_login_hint(ip):
    """Geef de door de beheerder ingestelde inlog-hint van de winkel die bij dit IP hoort (of '')."""
    if not ip:
        return ''
    try:
        for f in Filiaal.query.filter(Filiaal.login_hint.isnot(None)).all():
            if f.allowed_ips and (f.login_hint or '').strip() and ip_in_list(ip, f.allowed_ips):
                return f.login_hint.strip()
    except Exception:
        pass
    return ''

def ip_in_list(ip, cidr_text):
    """True als `ip` binnen de lijst IP's/CIDR's (`cidr_text`) valt."""
    if not ip or not cidr_text:
        return False
    try:
        addr = _ipaddr.ip_address(ip)
    except ValueError:
        return False
    for tok in re.split(r'[\s,;]+', cidr_text):
        if not tok:
            continue
        try:
            if '/' in tok:
                if addr in _ipaddr.ip_network(tok, strict=False):
                    return True
            elif addr == _ipaddr.ip_address(tok):
                return True
        except ValueError:
            continue
    return False

def user_allowed_ips(user):
    """Toegestane IP's voor een gebruiker: eigen lijst, anders die van de winkel."""
    if getattr(user, 'allowed_ips', None):
        return user.allowed_ips
    f = Filiaal.query.filter_by(nummer=user.filiaal).first()
    return (f.allowed_ips if f and f.allowed_ips else '')

def label_role(user):
    """Map pluslokaal-rol → label-rol: admin→superadmin, ondernemer→owner, rest→user."""
    r = (user.role or '').strip()
    return {'admin': 'superadmin', 'ondernemer': 'owner'}.get(r, 'user')

def is_superadmin(user):
    return user is not None and user.role == 'admin'

# ─── DEMO-ACCOUNT ─────────────────────────────────────────────────────────────
# Een speelaccount (login demo/demo) dat werkt als een gewone medewerker, maar zonder echte
# gevolgen: printen wordt gesimuleerd (er gaat niets naar een printer) en de data staat in een
# aparte 'Demo'-winkel (concept). In/uit te schakelen door de superadmin (Setting 'demo_enabled').
DEMO_FILIAAL = 9000
DEMO_PRINTER_NAAM = 'Demo-printer (concept)'

def is_demo(user):
    return bool(user and (user.username or '').lower() == 'demo')

def demo_enabled():
    return get_setting('demo_enabled', '1') == '1'

# Toewijsbare permissies voor rollen (winkel-niveau). Admin heeft altijd alles.
ASSIGNABLE_PERMS = [
    ('labels_make',    'Labels maken'),
    ('labels_history', 'Labelhistorie bekijken'),
    ('products',       'Producten beheren'),
    ('team',           'Eigen team beheren (leden + uitnodigen + goedkeuren)'),
    ('view_audit',     'Logboek van eigen winkel bekijken'),
    ('w2p_sync',       'Winkelpakketten synchroniseren met printsysteem'),
]
_ASSIGNABLE_KEYS = [k for k, _ in ASSIGNABLE_PERMS]

_role_perm_cache = {}
def _role_perms(name):
    r = Role.query.filter_by(name=name).first()
    if not r:
        return None
    try:
        return set(json.loads(r.permissions or '[]'))
    except Exception:
        return set()

def can(user, capability):
    """Admin = altijd alles. Overige rollen: permissies uit de Role-tabel (met terugval)."""
    if user is None:
        return False
    if user.role == 'admin':
        return True
    perms = _role_perms(user.role)
    if perms is not None:
        return capability in perms
    # Terugval als de rol (nog) niet in de tabel staat
    return capability in {'ondernemer': {'labels_make', 'labels_history', 'products', 'team', 'view_audit'}}.get(
        user.role, {'labels_make', 'labels_history', 'products'})

def log_action(action, detail='', user=None, filiaal=None):
    """Schrijf een auditregel (faalt stil)."""
    try:
        u = user if user is not None else get_current_user()
        db.session.add(AuditLog(
            action=action, detail=(detail or '')[:500], ip=client_ip(),
            user_id=(u.id if u else None), username=(u.username if u else None),
            filiaal=(filiaal if filiaal is not None else (u.filiaal if u else None)),
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

app.jinja_env.globals['can'] = can
app.jinja_env.globals['label_role'] = label_role

# ─── FEEDBACK / KENNISBANK: helpers ───────────────────────────────────────────
FEEDBACK_TYPES = {
    'probleem':  ('Probleem',  'fa-triangle-exclamation'),
    'suggestie': ('Suggestie', 'fa-lightbulb'),
    'idee':      ('Idee',      'fa-wand-magic-sparkles'),
}
FEEDBACK_STATUS = {
    'nieuw':          ('Nieuw',          '#0b5cad', '#e7f0fb'),
    'in_behandeling': ('In behandeling', '#8a5a00', '#fdf1d8'),
    'opgelost':       ('Opgelost',       '#2c6b0f', '#eaf6df'),
    'afgewezen':      ('Afgewezen',      '#8a2020', '#fbe5e5'),
}
app.jinja_env.globals['FEEDBACK_TYPES'] = FEEDBACK_TYPES
app.jinja_env.globals['FEEDBACK_STATUS'] = FEEDBACK_STATUS

def feedback_unread_count():
    try:
        return Feedback.query.filter_by(is_read=False).count()
    except Exception:
        return 0

def user_unread_replies(user):
    """Aantal beheer-reacties op de eigen meldingen die de melder nog niet zag."""
    try:
        if not user:
            return 0
        return (FeedbackMessage.query
                .join(Feedback, FeedbackMessage.feedback_id == Feedback.id)
                .filter(Feedback.user_id == user.id,
                        FeedbackMessage.is_admin == True,
                        FeedbackMessage.read_by_user == False)
                .count())
    except Exception:
        return 0

def _fb_msg_dict(msg):
    return {
        'id': msg.id, 'is_admin': bool(msg.is_admin),
        'author': msg.author_name or ('Beheer' if msg.is_admin else 'Melder'),
        'body': msg.body, 'at': msg.created_at.strftime('%d-%m-%Y %H:%M'),
    }

def _feedback_thread(fb, include_opening=True):
    """Volledige gespreksdraad van een melding als lijst dicts (opening + reacties)."""
    msgs = []
    if include_opening:
        msgs.append({
            'id': 0, 'is_admin': False, 'author': fb.username or 'Melder',
            'body': fb.message, 'at': fb.created_at.strftime('%d-%m-%Y %H:%M'), 'opening': True,
        })
    for m in (FeedbackMessage.query.filter_by(feedback_id=fb.id)
              .order_by(FeedbackMessage.created_at, FeedbackMessage.id).all()):
        msgs.append(_fb_msg_dict(m))
    return msgs

@app.context_processor
def _inject_feedback_badge():
    """Tellers voor de UI: beheer-menu (ongelezen meldingen) en het ?-widget (nieuwe reacties voor de melder)."""
    def _admin_c():
        try:
            u = get_current_user()
            return feedback_unread_count() if is_superadmin(u) else 0
        except Exception:
            return 0
    def _my_c():
        try:
            return user_unread_replies(get_current_user())
        except Exception:
            return 0
    # Changelog is zichtbaar voor iedereen behalve het demo-account.
    try:
        _changelog_ok = not is_demo(get_current_user())
    except Exception:
        _changelog_ok = False
    # Rondleiding-welkom: op het dashboard tonen zodra er na de login een 'pending' vlag staat.
    plt_welcome = False
    try:
        if request.endpoint == 'dashboard' and session.get('plt_welcome_pending'):
            session.pop('plt_welcome_pending', None)
            plt_welcome = True
    except Exception:
        plt_welcome = False
    return {'feedback_unread': _admin_c(), 'feedback_my_unread': _my_c(),
            'app_version': APP_VERSION, 'changelog_visible': _changelog_ok,
            'now_year': datetime.now().year, 'plt_welcome': plt_welcome}

@app.context_processor
def _inject_current_user():
    """Zorg dat `user` overal in templates beschikbaar is (routes die het expliciet
    meegeven overschrijven dit gewoon)."""
    try:
        return {'user': get_current_user()}
    except Exception:
        return {'user': None}

def _fb_log(fb, text, who=None):
    """Voeg een regel toe aan het logboek van een melding."""
    try:
        events = json.loads(fb.log_json or '[]')
    except Exception:
        events = []
    events.append({
        'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'who': who or 'systeem',
        'text': text,
    })
    fb.log_json = json.dumps(events, ensure_ascii=False)

@app.template_filter('fb_log')
def _tpl_fb_log(s):
    try:
        return json.loads(s or '[]')
    except Exception:
        return []

def _slugify(text):
    import re, unicodedata
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    text = re.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()
    return text or 'artikel'

def render_markdown(src):
    """Lichtgewicht, veilige Markdown→HTML renderer (geen externe dependency).
    Ondersteunt: koppen, vet/cursief, inline+block code, lijsten, citaten, links,
    afbeeldingen, horizontale lijn, tabellen zijn niet nodig."""
    import re
    from markupsafe import Markup, escape
    if not src:
        return Markup('')
    lines = src.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    def inline(t):
        t = str(escape(t))
        # afbeeldingen ![alt](src)
        t = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)\)',
                   r'<img src="\2" alt="\1" loading="lazy">', t)
        # links [tekst](url)
        t = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)',
                   r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
        # inline code
        t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
        # vet **x** en cursief *x*
        t = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'(?<!\*)\*(?!\*)([^*]+)\*(?!\*)', r'<em>\1</em>', t)
        return t

    html, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        # codeblok ```
        if line.strip().startswith('```'):
            i += 1; buf = []
            while i < n and not lines[i].strip().startswith('```'):
                buf.append(str(escape(lines[i]))); i += 1
            i += 1
            html.append('<pre class="kb-code"><code>' + '\n'.join(buf) + '</code></pre>')
            continue
        # horizontale lijn
        if re.match(r'^\s*(---|\*\*\*|___)\s*$', line):
            html.append('<hr>'); i += 1; continue
        # koppen
        m = re.match(r'^(#{1,6})\s+(.*)$', line)
        if m:
            lvl = len(m.group(1))
            html.append(f'<h{lvl}>{inline(m.group(2).strip())}</h{lvl}>')
            i += 1; continue
        # citaat
        if re.match(r'^\s*>\s?', line):
            buf = []
            while i < n and re.match(r'^\s*>\s?', lines[i]):
                buf.append(inline(re.sub(r'^\s*>\s?', '', lines[i]))); i += 1
            html.append('<blockquote>' + '<br>'.join(buf) + '</blockquote>')
            continue
        # ongeordende lijst
        if re.match(r'^\s*[-*]\s+', line):
            buf = []
            while i < n and re.match(r'^\s*[-*]\s+', lines[i]):
                buf.append('<li>' + inline(re.sub(r'^\s*[-*]\s+', '', lines[i])) + '</li>'); i += 1
            html.append('<ul>' + ''.join(buf) + '</ul>')
            continue
        # geordende lijst
        if re.match(r'^\s*\d+\.\s+', line):
            buf = []
            while i < n and re.match(r'^\s*\d+\.\s+', lines[i]):
                buf.append('<li>' + inline(re.sub(r'^\s*\d+\.\s+', '', lines[i])) + '</li>'); i += 1
            html.append('<ol>' + ''.join(buf) + '</ol>')
            continue
        # lege regel
        if not line.strip():
            i += 1; continue
        # paragraaf (aaneengesloten niet-lege, niet-speciale regels)
        buf = []
        while i < n and lines[i].strip() and not re.match(
                r'^\s*(#{1,6}\s|>|[-*]\s|\d+\.\s|```|---|\*\*\*|___)', lines[i]):
            buf.append(inline(lines[i])); i += 1
        html.append('<p>' + '<br>'.join(buf) + '</p>')
    return Markup('\n'.join(html))

app.jinja_env.globals['render_markdown'] = render_markdown

@app.template_filter('from_json')
def _tpl_from_json(s):
    try:
        return json.loads(s or '[]')
    except Exception:
        return []

def _card_is_scan(card):
    """True als deze Card een scankaart is (kaart_data mode == 'scan')."""
    try:
        return json.loads(card.kaart_data or '{}').get('mode') == 'scan'
    except Exception:
        return False

# ─── INSTELLINGEN (key/value) ─────────────────────────────────────────────────
def _load_mail_secret():
    """Haal de SMTP/Resend-key op uit de omgeving of een los, niet-versiebeheerd bestand
    (.mail_secret). Zo staat de sleutel niet meer in de broncode. Valt stil terug op ''."""
    v = os.environ.get('PLUSLOKAAL_SMTP_PASS')
    if v:
        return v.strip()
    try:
        return open(os.path.join(os.path.dirname(__file__), '.mail_secret')).read().strip()
    except Exception:
        return ''

_SMTP_DEFAULTS = {
    'smtp_host': 'smtp.resend.com',
    'smtp_port': '587',
    'smtp_user': 'resend',
    'smtp_pass': _load_mail_secret(),
    'smtp_from': 'info@mail.pluslokaal.com',
    'smtp_from_name': 'PLUSLokaal',
    'mail_enabled': '1',
    'app_url': '',
}

def get_setting(key, default=''):
    s = Setting.query.get(key)
    if s is not None and s.value is not None:
        return s.value
    return _SMTP_DEFAULTS.get(key, default)

def set_setting(key, value):
    s = Setting.query.get(key)
    if s is None:
        s = Setting(key=key, value=value)
        db.session.add(s)
    else:
        s.value = value
    db.session.commit()

def mail_enabled():
    return get_setting('mail_enabled', '1') == '1'

# ─── E-MAIL ───────────────────────────────────────────────────────────────────
_logo_cache = {}
def _logo_png():
    """Render het echte PLUS-logo (SVG) naar PNG-bytes voor in de e-mail (SVG rendert niet in mailclients)."""
    if 'png' not in _logo_cache:
        try:
            import cairosvg
            svg = os.path.join(os.path.dirname(__file__), 'static', 'img', 'plus-logo.svg')
            _logo_cache['png'] = cairosvg.svg2png(url=svg, output_height=64)
        except Exception:
            _logo_cache['png'] = None
    return _logo_cache['png']

def _mail_wrapper(title, body_html):
    """PLUS-vormgegeven HTML-mail (groene header met echt logo, wit content-blok)."""
    return f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f5f3;font-family:'Open Sans',Arial,sans-serif;color:#333333;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f3;padding:24px 12px;">
<tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <tr><td style="background:#80bd1d;padding:18px 28px;">
      <img src="cid:pluslogo" alt="PLUS Lokaal" height="30" style="height:30px;display:inline-block;vertical-align:middle;border:0;">
      <span style="color:#ffffff;font-size:14px;font-weight:700;opacity:.92;vertical-align:middle;"> &nbsp;|&nbsp; Lokaal</span>
    </td></tr>
    <tr><td style="padding:32px 28px;">
      <h1 style="margin:0 0 16px;color:#115013;font-size:22px;font-weight:800;line-height:1.25;">{title}</h1>
      {body_html}
    </td></tr>
    <tr><td style="padding:18px 28px;border-top:1px solid #eaeae7;color:#6c6c6c;font-size:12px;line-height:1.5;">
      Deze e-mail is verstuurd door de PLUSLokaal schapkaarten-omgeving.
    </td></tr>
  </table>
</td></tr></table></body></html>"""

def _btn(url, label):
    return (f'<a href="{url}" style="display:inline-block;background:#80bd1d;color:#ffffff;'
            f'text-decoration:none;font-weight:700;font-size:15px;padding:12px 26px;'
            f'border-radius:24px 24px 24px 4px;">{label}</a>')

# Afzender-adressen per soort mail (bewust NIET no-reply). Het domein mail.pluslokaal.com is geverifieerd.
MAIL_FROM_WELCOME = 'info@mail.pluslokaal.com'
MAIL_FROM_RESET   = 'passwordreset@mail.pluslokaal.com'

def send_mail(to_addr, subject, html, from_addr=None, from_name=None):
    """Verstuur een HTML-mail via de ingestelde SMTP-server. Geeft (ok, foutmelding).
    ``from_addr``/``from_name``: overschrijf de standaard-afzender (bv. info@ voor welkom,
    passwordreset@ voor wachtwoord-reset)."""
    if not to_addr:
        return False, 'Geen e-mailadres'
    if not mail_enabled():
        return False, 'E-mail staat uit'
    host = get_setting('smtp_host'); port = int(get_setting('smtp_port', '587') or 587)
    user = get_setting('smtp_user'); pwd = get_setting('smtp_pass')
    frm  = from_addr or get_setting('smtp_from'); frm_name = from_name or get_setting('smtp_from_name', 'PLUSLokaal')
    if not (host and frm):
        return False, 'SMTP niet volledig ingesteld'
    msg = MIMEMultipart('related')
    msg['Subject'] = subject
    msg['From'] = formataddr((frm_name, frm))
    msg['To'] = to_addr
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(html, 'html', 'utf-8'))
    msg.attach(alt)
    if 'cid:pluslogo' in html:
        png = _logo_png()
        if png:
            img = MIMEImage(png, 'png')
            img.add_header('Content-ID', '<pluslogo>')
            img.add_header('Content-Disposition', 'inline', filename='plus-lokaal.png')
            msg.attach(img)
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.ehlo()
            try:
                s.starttls(context=ssl.create_default_context()); s.ehlo()
            except Exception:
                pass
            if user and pwd:
                s.login(user, pwd)
            s.sendmail(frm, [to_addr], msg.as_string())
        return True, None
    except Exception as e:
        app.logger.error(f'Mail versturen mislukt: {e}')
        return False, str(e)

def send_mail_async(to_addr, subject, html, from_addr=None, from_name=None):
    """Verstuur op de achtergrond zodat de request niet wacht op de mailserver."""
    def _run():
        with app.app_context():
            try:
                send_mail(to_addr, subject, html, from_addr=from_addr, from_name=from_name)
            except Exception as e:
                app.logger.error(f'Async mail mislukt: {e}')
    threading.Thread(target=_run, daemon=True).start()

# ─── SET-PASSWORD TOKENS ──────────────────────────────────────────────────────
def _pw_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt='pluslokaal-set-password')

def make_setpw_token(uid):
    # Bind het token aan de huidige wachtwoord-hash: zodra het wachtwoord verandert (bv. omdat de
    # gebruiker de link al gebruikte), wordt het token automatisch ongeldig → feitelijk eenmalig.
    u = User.query.get(uid)
    pm = _pw_marker(u) if u else ''
    return _pw_serializer().dumps({'uid': uid, 'pm': pm})

def verify_setpw_token(token, max_age=7*24*3600):
    try:
        data = _pw_serializer().loads(token, max_age=max_age)
        u = User.query.get(data['uid'])
        if u is None:
            return None
        # Nieuwe tokens dragen een 'pm'-marker; controleer dat het wachtwoord sindsdien niet wijzigde.
        # Oudere tokens (zonder 'pm') blijven geldig tot hun vervaltijd, zodat lopende uitnodigingen
        # blijven werken.
        pm = data.get('pm')
        if pm is not None and pm != _pw_marker(u):
            return None
        return u
    except Exception:
        return None

# Tip die we tonen bij het instellen/resetten van een wachtwoord
PW_HINT = ('Tip: kies bij voorkeur hetzelfde wachtwoord als dat van je e-mailaccount, '
           'dan hoef je maar één wachtwoord te onthouden en vergeet je het niet.')

def _ext_url(endpoint, **kw):
    """Bouw een externe URL; gebruik het ingestelde App-URL-domein indien aanwezig (correct achter een proxy/tunnel)."""
    base = get_setting('app_url')
    path = url_for(endpoint, _external=False, **kw)
    if base:
        from urllib.parse import urlparse
        o = urlparse(base)
        if o.scheme and o.netloc:
            return f'{o.scheme}://{o.netloc}{path}'
    try:
        return url_for(endpoint, _external=True, **kw)
    except Exception:
        return path

def send_setpw_invite(user_obj, kind='welcome'):
    """Stuur een mail met een link waarmee de gebruiker zelf een wachtwoord instelt."""
    if not (user_obj.email and mail_enabled()):
        return False, 'Gebruiker heeft geen e-mailadres of mail staat uit'
    link = _ext_url('set_password', token=make_setpw_token(user_obj.id))
    if kind == 'reset':
        title = 'Wachtwoord opnieuw instellen'
        subject = 'Stel je PLUSLokaal-wachtwoord opnieuw in'
        intro = ('<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">'
                 'Er is verzocht om je wachtwoord voor de PLUSLokaal schapkaarten-omgeving opnieuw in te stellen. '
                 'Klik op de knop hieronder om een nieuw wachtwoord te kiezen.</p>')
    else:
        title = 'Welkom bij PLUSLokaal'
        subject = 'Welkom bij PLUSLokaal Schapkaarten'
        intro = ('<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">'
                 'Welkom bij de <strong>nieuwe PLUSLokaal schapkaarten-omgeving</strong>. Hierin maak je eenvoudig '
                 'professionele PLUS-schapkaarten (prijs, korting, 2e halve prijs en meer) en download je ze direct '
                 'als print-klare PDF.</p>'
                 '<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">Kies hieronder je eigen wachtwoord om te beginnen.</p>')
    body = f"""
      <p style="font-size:15px;line-height:1.6;margin:0 0 14px;">Beste {user_obj.username},</p>
      {intro}
      <p style="font-size:15px;line-height:1.6;margin:0 0 6px;color:#6c6c6c;">Gebruikersnaam: <strong style="color:#333;">{user_obj.username}</strong></p>
      <p style="margin:18px 0 20px;">{_btn(link, 'Wachtwoord instellen')}</p>
      <p style="font-size:14px;line-height:1.6;margin:0 0 14px;background:#eef6e1;border-radius:8px;padding:12px 14px;color:#115013;">
        💡 {PW_HINT}</p>
      <p style="font-size:13px;color:#6c6c6c;line-height:1.6;margin:0;">
        Deze link is 7 dagen geldig. Werkt de knop niet? Kopieer dan deze link:<br>
        <span style="word-break:break-all;color:#115013;">{link}</span></p>"""
    frm = MAIL_FROM_RESET if kind == 'reset' else MAIL_FROM_WELCOME
    send_mail_async(user_obj.email, subject, _mail_wrapper(title, body), from_addr=frm)
    return True, None

# ─── ACCOUNT AANMAKEN: uitnodiging met tijdelijk wachtwoord (Label-Manager-stijl) ─────────────
def generate_temp_password(length=12):
    """Sterk, leesbaar tijdelijk wachtwoord (min. 1 hoofdletter/kleine letter/cijfer/teken)."""
    import string
    length = max(length, 10)
    punct = '!@#$%&*?'
    picks = [secrets.choice(string.ascii_uppercase), secrets.choice(string.ascii_lowercase),
             secrets.choice(string.digits), secrets.choice(punct)]
    pool = string.ascii_letters + string.digits + punct
    picks += [secrets.choice(pool) for _ in range(length - len(picks))]
    secrets.SystemRandom().shuffle(picks)
    return ''.join(picks)

def _mail_p(text):
    return f'<p style="font-size:15px;line-height:1.6;margin:0 0 14px;">{text}</p>'

def _mail_features(items):
    lis = ''.join(
        '<tr><td style="vertical-align:top;padding:0 8px 10px 0;color:#80bd1d;font-size:15px;font-weight:800;">✓</td>'
        f'<td style="padding:0 0 10px 0;font-size:14px;line-height:1.5;color:#333;">{it}</td></tr>'
        for it in items)
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 18px;">{lis}</table>'

def _mail_creds(email, temp):
    return ('<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;background:#eef6e1;'
            'border-radius:8px;margin:0 0 18px;"><tr><td style="padding:16px 18px;font-size:14px;color:#333;">'
            '<div style="color:#6c8a3f;font-size:12px;margin-bottom:6px;font-weight:800;">Inloggegevens</div>'
            f'<strong>Inlognaam (e-mail):</strong> {email}<br>'
            f'<strong>Tijdelijk wachtwoord:</strong> <code style="font-size:15px;background:#fff;padding:2px 6px;'
            f'border-radius:4px;border:1px solid #d8e4c4;">{temp}</code></td></tr></table>')

def pluslokaal_features(role):
    feats = [
        'Professionele <strong>schapkaarten</strong> maken in álle PLUS-formaten (SK Mini/Middel/Maxi, A5, A4, '
        'A3 staand én liggend), als <strong>actie- of tipkaart</strong>, met alle actievormen (prijs, 2e halve prijs, '
        'X% korting, X+Y gratis, enz.) - en printen als print-klare PDF.',
        '<strong>Scankaarten</strong> met barcode maken en beheren.',
        '<strong>Prijslabels</strong> (schaplabels met logo, prijs en barcode) maken en rechtstreeks naar de '
        'labelprinter sturen.',
        '<strong>Printbare winkelpakketten</strong>: de wekelijkse actiekaarten per afdeling - selecteren, '
        'samenvoegen en in één keer printen of downloaden.',
        '<strong>Direct op de winkelprinter printen</strong> - ook meerdere kaarten tegelijk - met automatisch '
        'het juiste papierformaat uit de juiste lade, én live voortgang met annuleren.',
        'Alles op <strong>één centrale plek</strong>, netjes per winkel, in je browser op computer, tablet of telefoon.',
    ]
    if role in ('ondernemer', 'admin'):
        feats.append('Je eigen <strong>team beheren</strong>: medewerkers toevoegen, uitnodigen, '
                     'registratie-aanvragen goedkeuren en wachtwoorden resetten.')
    return feats

def pluslokaal_improvements():
    """Wat er beter is t.o.v. de oude omgeving."""
    return [
        '<strong>Rechtstreeks printen op de winkelprinter</strong> - geen bestanden meer downloaden en geen '
        'gedoe met lade-instellingen; elk formaat rolt automatisch uit de juiste lade.',
        '<strong>Meerdere kaarten in één keer</strong> selecteren en samen printen, ook door verschillende '
        'formaten heen - de selectie wordt daarna netjes gewist.',
        '<strong>Live voortgang</strong> tijdens het printen, met meldingen rechtsboven en een annuleerknop.',
        'Alle kaartontwerpen <strong>1-op-1 in de PLUS-huisstijl</strong> - zowel het nieuwe als het oude ontwerp, '
        'actie- én tipkaarten, op exact formaat.',
        '<strong>Schapkaarten, scankaarten, prijslabels én winkelpakketten</strong> in één omgeving, in plaats van '
        'losse systemen en handwerk.',
        '<strong>Sneller en moderner</strong>: blijvend ingelogd, wachtwoord vergeten-herstel, extra beveiliging '
        '(2FA) en volledig mobielvriendelijk.',
    ]

def send_welcome_invite(user_obj):
    """Maak een tijdelijk wachtwoord aan, dwing wachtwoordwijziging af, en mail de uitnodiging met
    inloggegevens + uitleg over PLUSLokaal. Geeft (ok, err, temp) terug - toon 'temp' aan de beheerder
    als de mail niet verstuurd kon worden, zodat die het persoonlijk kan doorgeven."""
    temp = generate_temp_password()
    user_obj.password = hash_password(temp)
    user_obj.must_change_password = True
    if hasattr(user_obj, 'approved'):
        user_obj.approved = True
    db.session.commit()
    if not (user_obj.email and mail_enabled()):
        return False, 'geen e-mailadres of mail staat uit', temp
    login_url = _ext_url('login')
    hd = ('font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;'
          'color:#115013;margin:14px 0 8px;')
    body = (
        _mail_p(f'Beste {user_obj.username},') +
        _mail_p('Welkom bij <strong>PLUSLokaal</strong> - de nieuwe, centrale omgeving waarmee je in je winkel '
                'álle schapkaarten, scankaarten en prijslabels zelf maakt, beheert en print, volledig in de '
                'PLUS-huisstijl.') +
        f'<div style="{hd}">Wat je met PLUSLokaal kunt</div>' +
        _mail_features(pluslokaal_features(getattr(user_obj, 'role', 'medewerker'))) +
        f'<div style="{hd}">Wat er beter is dan voorheen</div>' +
        _mail_features(pluslokaal_improvements()) +
        _mail_p('Log in met onderstaande gegevens. Bij je eerste aanmelding stel je meteen je eigen wachtwoord in.') +
        _mail_creds(user_obj.email, temp) +
        f'<p style="margin:6px 0 20px;">{_btn(login_url, "Inloggen en wachtwoord instellen")}</p>' +
        _mail_p(f'<span style="font-size:13px;color:#6c6c6c;">💡 {PW_HINT}</span>')
    )
    ok, err = send_mail(user_obj.email, 'Welkom bij PLUSLokaal', _mail_wrapper('Welkom bij PLUSLokaal', body),
                        from_addr=MAIL_FROM_WELCOME)
    return ok, err, temp

def send_approved_notice(user_obj):
    """Laat een zelf-geregistreerde gebruiker weten dat hij is goedgekeurd - hij houdt zijn eigen
    (bij registratie gekozen) wachtwoord, dus we resetten NIETS."""
    if not (user_obj.email and mail_enabled()):
        return False, 'geen e-mailadres of mail staat uit'
    login_url = _ext_url('login')
    hd = ('font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;'
          'color:#115013;margin:14px 0 8px;')
    body = (
        _mail_p(f'Beste {user_obj.username},') +
        _mail_p('Goed nieuws - je account voor <strong>PLUSLokaal</strong> is goedgekeurd. Je kunt nu inloggen '
                'met je e-mailadres en het wachtwoord dat je bij de aanmelding hebt gekozen.') +
        f'<div style="{hd}">Wat je met PLUSLokaal kunt</div>' +
        _mail_features(pluslokaal_features(getattr(user_obj, 'role', 'medewerker'))) +
        f'<div style="{hd}">Wat er beter is dan voorheen</div>' +
        _mail_features(pluslokaal_improvements()) +
        f'<p style="margin:6px 0 20px;">{_btn(login_url, "Inloggen")}</p>'
    )
    return send_mail(user_obj.email, 'Je PLUSLokaal-account is goedgekeurd',
                     _mail_wrapper('Je account is goedgekeurd', body), from_addr=MAIL_FROM_WELCOME)

def send_printer_request(naam, email, telefoon, winkel, nr, aanvrager):
    """Stuur een winkelprinter-aanvraag naar de beheerder én een bevestiging (huisstijl) naar de aanvrager."""
    _e = lambda s: (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    hd = ('font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;'
          'color:#115013;margin:18px 0 8px;')
    def bullets(items):
        rows = ''.join(
            f'<li style="margin:0 0 8px;padding-left:24px;position:relative;list-style:none;line-height:1.55;">'
            f'<span style="position:absolute;left:0;top:0;color:#80bd1d;font-weight:800;">&#10003;</span>{t}</li>'
            for t in items)
        return f'<ul style="margin:0 0 12px;padding:0;">{rows}</ul>'

    # 1) Melding naar de beheerder
    admin_body = (
        _mail_p('Er is een aanvraag binnengekomen voor het koppelen van een winkelprinter.') +
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:10px 0 8px;font-size:15px;">'
        f'<tr><td style="padding:4px 18px 4px 0;color:#6c6c6c;">Winkel</td><td style="padding:4px 0;font-weight:700;">{_e(winkel)} ({nr})</td></tr>'
        f'<tr><td style="padding:4px 18px 4px 0;color:#6c6c6c;">Naam</td><td style="padding:4px 0;font-weight:700;">{_e(naam)}</td></tr>'
        f'<tr><td style="padding:4px 18px 4px 0;color:#6c6c6c;">E-mail</td><td style="padding:4px 0;font-weight:700;">{_e(email)}</td></tr>'
        f'<tr><td style="padding:4px 18px 4px 0;color:#6c6c6c;">Telefoon</td><td style="padding:4px 0;font-weight:700;">{_e(telefoon)}</td></tr>'
        f'<tr><td style="padding:4px 18px 4px 0;color:#6c6c6c;">Ingediend door</td><td style="padding:4px 0;">{_e(getattr(aanvrager, "username", "-"))}</td></tr>'
        '</table>')
    send_mail_async('admin@pluslokaal.com', f'Printeraanvraag - {winkel} ({nr})',
                    _mail_wrapper('Nieuwe printeraanvraag', admin_body), from_addr=MAIL_FROM_WELCOME)

    # 2) Bevestiging naar de ondernemer/winkelmanager
    pi_url = 'https://www.raspberrypi.com/products/raspberry-pi-5/'
    conf_body = (
        _mail_p(f'Beste {_e(naam)},') +
        _mail_p('Bedankt voor je aanvraag om een <strong>winkelprinter</strong> te koppelen aan PLUSLokaal '
                f'voor <strong>{_e(winkel)}</strong>. We hebben je aanvraag ontvangen en nemen binnenkort '
                'contact met je op om het samen in orde te maken.') +
        f'<div style="{hd}">Hoe werkt het?</div>' +
        _mail_p('Voor rechtstreeks printen komt er een klein computertje direct op de winkelprinter aangesloten - '
                f'een <a href="{pi_url}" style="color:#115013;font-weight:700;">Raspberry Pi 5</a>. Dit kastje maakt '
                'zelf een beveiligde verbinding met pluslokaal.com; er hoeft niets aan je winkel-netwerk of '
                'firewall aangepast te worden.') +
        f'<div style="{hd}">De voordelen</div>' +
        bullets([
            'Je print schapkaarten en scankaarten <strong>rechtstreeks op de juiste printer</strong> - geen pdf meer downloaden en handmatig openen.',
            'De agent kiest <strong>automatisch de juiste papierlade</strong> per formaat: A4, SK Maxi en A3 komen altijd uit de goede lade.',
            'Werkt voor het hele team, zonder installatie op de kassa- of kantoor-computers.',
        ]) +
        f'<div style="{hd}">Zelf een kastje regelen mag ook</div>' +
        _mail_p('Raspberry Pi\'s zijn op dit moment beperkt leverbaar. Je mag er zelf ook een aanschaffen '
                '(bijvoorbeeld bij een andere webshop). Heb je nog een <strong>oud werkstation of pc</strong> '
                'liggen? Ook die kunnen we prima gebruiken - dan is er geen nieuwe hardware nodig.') +
        _mail_p('We nemen contact met je op via dit e-mailadres of je telefoonnummer. Vragen in de tussentijd? '
                'Mail gerust naar <a href="mailto:admin@pluslokaal.com" style="color:#115013;font-weight:700;">admin@pluslokaal.com</a>.')
    )
    ok, _err = send_mail(email, 'We hebben je printeraanvraag ontvangen',
                         _mail_wrapper('Je printeraanvraag is binnen', conf_body), from_addr=MAIL_FROM_WELCOME)
    return ok

@app.route('/api/printer-aanvraag', methods=['POST'])
@login_required
def printer_aanvraag():
    u = get_current_user()
    naam = request.form.get('naam', '').strip()
    email = request.form.get('email', '').strip().lower()
    tel = request.form.get('telefoon', '').strip()
    if not (naam and email and tel):
        return jsonify({'ok': False, 'error': 'Vul je naam, e-mailadres en telefoonnummer in.'}), 400
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'ok': False, 'error': 'Vul een geldig e-mailadres in.'}), 400
    nr = _active_filiaal() or u.filiaal
    f = Filiaal.query.filter_by(nummer=nr).first()
    winkel = ('PLUS ' + f.naam) if (f and f.naam) else ('Filiaal ' + str(nr))
    try:
        send_printer_request(naam, email, tel, winkel, nr, u)
    except Exception as e:
        app.logger.warning(f'printeraanvraag mail: {e}')
    log_action('printer_aanvraag', f'{naam} <{email}> {tel}', filiaal=nr)
    return jsonify({'ok': True})

# ─── ERROR HANDLERS ───────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    # Vangnet voor het Portaal: pluslokaal.nl doet sommige navigaties/formulieren via JS met
    # root-relatieve URLs (bv. /W2P/Basket.aspx) die op ONZE origin belanden i.p.v. onder
    # /portaal/view/. Als het verzoek uit een portaal-pagina komt, sturen we het alsnog door de proxy.
    try:
        p = request.path or ''
        ref = request.referrer or ''
        from_portaal = ('/portaal/view/' in ref) or ('/portaal' in ref and p.startswith(
            ('/W2P/', '/custom/', '/Campaigns/', '/campaigns/', '/imagesvc/', '/img/', '/css/',
             '/js/', '/common/', '/search/', '/headermenu/', '/landelijke-activiteiten/',
             '/lokale-activiteiten/', '/winkel/', '/e-commerce/', '/social-media/')))
        if from_portaal and not p.startswith(('/portaal', '/static')):
            qs = ('?' + request.query_string.decode('latin-1')) if request.query_string else ''
            code = 307 if request.method != 'GET' else 302   # 307 behoudt POST-methode + body
            return redirect('/portaal/view' + p + qs, code=code)
    except Exception:
        pass
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html', error=str(e)), 500

@app.errorhandler(413)
def too_large(e):
    # Te grote upload (boven MAX_CONTENT_LENGTH). Nette melding i.p.v. een kale serverfout.
    if request.headers.get('X-Requested-With') == 'fetch' or request.path.startswith('/feedback'):
        return jsonify(ok=False, error='Bestand/afbeelding is te groot.'), 413
    flash('Het verzonden bestand is te groot.', 'error')
    return redirect(request.referrer or url_for('dashboard'))

# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def home():
    # Al ingelogd? Direct naar het dashboard - anders naar de loginpagina. (Voorheen ging '/' ALTIJD naar
    # /login, waardoor een nog geldige sessie tóch het inlogscherm zag = leek uitgelogd terwijl je 't niet was.)
    return redirect(url_for('dashboard' if get_current_user() else 'login'))

def _login_fail_page():
    """Toon de login-pagina na een mislukte poging, mét de door de beheerder ingestelde winkel-hint
    als de poging van een bekend winkel-IP komt."""
    return render_template('login.html', store_hint=_store_login_hint(client_ip()))

@app.route('/login', methods=['GET','POST'])
def login():
    # Al ingelogd en de loginpagina openen? Direct door naar het dashboard, zodat een geldige (permanente)
    # sessie na het heropenen van de browser niet onterecht het inlogscherm toont.
    if request.method == 'GET' and get_current_user():
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        # Eenvoudige rate limiting via session
        now = time.time()
        fails = session.get('login_fails', 0)
        fail_time = session.get('login_fail_time', 0)
        if fails >= 5:
            wait = 30 - (now - fail_time)
            if wait > 0:
                flash(f'Te veel mislukte pogingen. Wacht nog {int(wait)+1} seconden.', 'error')
                return _login_fail_page()
            else:
                session['login_fails'] = 0

        un = request.form.get('username','').strip()
        pw = request.form.get('password','').strip()
        # Server-side brute-force-rem (los van de sessiecookie).
        blk = _login_blocked_secs(un)
        if blk > 0:
            log_action('login_geblokkeerd', f'IP {client_ip()} - te veel pogingen')
            flash(f'Te veel mislukte pogingen. Wacht {blk} seconden en probeer het opnieuw.', 'error')
            return _login_fail_page()
        # inloggen met e-mailadres (of, als terugval, met de naam)
        user = find_user_by_email(un) or find_user_by_name(un)
        if user and _check_pw(user, pw):
            _login_reset(un)
            # Keuze "aangemeld blijven" onthouden (geldt ook na de MFA-stap).
            session['remember_me'] = bool(request.form.get('remember'))
            # Demo-account alleen bruikbaar als de superadmin het heeft ingeschakeld.
            if is_demo(user) and not demo_enabled():
                log_action('login_geweigerd', 'demo uitgeschakeld', user=user)
                flash('Het demo-account is momenteel uitgeschakeld.', 'error')
                return _login_fail_page()
            # Accountstatus: nog niet goedgekeurd?
            if getattr(user, 'approved', True) is False:
                log_action('login_geweigerd', 'account nog niet goedgekeurd', user=user)
                flash('Je account wacht nog op goedkeuring door een beheerder.', 'error')
                return _login_fail_page()
            # IP-beleid: alleen inloggen vanaf toegestane IP's
            if getattr(user, 'access_policy', 'anywhere') == 'ip_login':
                if not ip_in_list(client_ip(), user_allowed_ips(user)):
                    log_action('login_geweigerd', f'IP {client_ip()} niet toegestaan', user=user)
                    flash('Inloggen vanaf deze locatie/IP is niet toegestaan voor dit account.', 'error')
                    return _login_fail_page()
            session.pop('login_fails', None)
            session.pop('login_fail_time', None)
            # Twee-factor: superadmins (en wie MFA aan heeft) moeten een tweede stap doen.
            if user.mfa_enabled and user.mfa_secret:
                session['pre_auth_uid'] = user.id
                session.pop('uid', None); session.pop('username', None)
                return redirect(url_for('mfa_verify'))
            if mfa_required_for(user):
                # verplicht maar nog niet ingesteld → eenmalige inschrijving afdwingen
                session['pre_auth_uid'] = user.id
                session.pop('uid', None); session.pop('username', None)
                return redirect(url_for('mfa_setup'))
            return _finish_login(user)

        _login_record_fail(un)
        session['login_fails'] = session.get('login_fails', 0) + 1
        session['login_fail_time'] = now
        # Log de mislukte poging: wat is er ingevuld, van welk IP en welk toestel.
        dev = device_str()
        reason = 'onbekende gebruiker' if not user else 'onjuist wachtwoord'
        log_action('login_mislukt', f'ingevuld: "{un[:100]}" - {reason}' + (f' · {dev}' if dev else ''))
        flash('Ongeldige gebruikersnaam of wachtwoord.', 'error')
        return _login_fail_page()
    return render_template('login.html')

@app.route('/mfa', methods=['GET','POST'])
def mfa_verify():
    """Tweede login-stap: TOTP-code van de authenticator-app."""
    uid = session.get('pre_auth_uid')
    user = User.query.get(uid) if uid else None
    if not user or not (user.mfa_enabled and user.mfa_secret):
        return redirect(url_for('login'))
    if request.method == 'POST':
        if _mfa_check(user, request.form.get('code', '')):
            return _finish_login(user)
        log_action('mfa_mislukt', f'IP {client_ip()}', user=user)
        flash('Onjuiste of verlopen code. Probeer opnieuw.', 'error')
    return render_template('mfa_verify.html', user=user)

@app.route('/mfa/setup', methods=['GET','POST'])
def mfa_setup():
    """Eenmalige inschrijving (verplicht voor superadmins). Toont QR + geheime sleutel; verifieert
    een code voordat MFA wordt geactiveerd. Bereikbaar tijdens login (pre_auth) of als ingelogde
    gebruiker die MFA (opnieuw) wil instellen."""
    import pyotp
    uid = session.get('pre_auth_uid') or session.get('uid')
    user = User.query.get(uid) if uid else None
    if not user:
        return redirect(url_for('login'))
    # tijdelijk geheim in de sessie tot het geverifieerd is
    secret = session.get('mfa_setup_secret')
    if not secret:
        secret = pyotp.random_base32()
        session['mfa_setup_secret'] = secret
    tmp = User(email=user.email, username=user.username); tmp.id = user.id; tmp.mfa_secret = secret
    uri = _mfa_uri(tmp)
    if request.method == 'POST':
        if _mfa_check(tmp, request.form.get('code', '')):
            user.mfa_secret = secret
            user.mfa_enabled = True
            db.session.commit()
            session.pop('mfa_setup_secret', None)
            log_action('mfa_ingesteld', '', user=user)
            if session.get('pre_auth_uid'):
                flash('Twee-factor-authenticatie is ingesteld. Je bent ingelogd.', 'success')
                return _finish_login(user)
            flash('Twee-factor-authenticatie is ingesteld.', 'success')
            return redirect(url_for('profile'))
        flash('Onjuiste code - controleer de app en probeer opnieuw.', 'error')
    return render_template('mfa_setup.html', user=user, secret=secret,
                           qr=_mfa_qr_datauri(uri), forced=(session.get('pre_auth_uid') is not None))

# ─── ZELF-REGISTRATIE (medewerker vraagt account aan; ondernemer keurt goed) ──────────────────
ALLOWED_SIGNUP_DOMAINS = ('plus.nl', 'plusretail.nl')

def validate_password(pw):
    """Wachtwoordbeleid voor zelf-registratie (min. 10, hoofdletter, cijfer, leesteken)."""
    pw = pw or ''
    if len(pw) < 10:
        return 'Wachtwoord moet minimaal 10 tekens zijn.'
    if not re.search(r'[A-Z]', pw):
        return 'Gebruik minstens één hoofdletter.'
    if not re.search(r'[a-z]', pw):
        return 'Gebruik minstens één kleine letter.'
    if not re.search(r'[0-9]', pw):
        return 'Gebruik minstens één cijfer.'
    if not re.search(r'[^A-Za-z0-9]', pw):
        return 'Gebruik minstens één leesteken.'
    return None

def _notify_owners_pending(pending_user):
    """Mail de winkelondernemer(s) van de winkel (anders de superadmins) dat er iemand wacht op goedkeuring."""
    if not mail_enabled():
        return
    store = Filiaal.query.filter_by(nummer=pending_user.filiaal).first()
    store_name = f'PLUS {store.naam}' if store and store.naam else f'winkel {pending_user.filiaal}'
    owners = User.query.filter_by(filiaal=pending_user.filiaal, role='ondernemer').all()
    recipients = [o.email for o in owners if o.email and getattr(o, 'approved', True)]
    if not recipients:
        recipients = [a.email for a in User.query.filter_by(role='admin').all() if a.email]
    if not recipients:
        return
    approve_url = _ext_url('team')
    body = (
        _mail_p(f'Er heeft zich een nieuwe medewerker aangemeld voor <strong>{store_name}</strong> in '
                f'PLUSLokaal. Wil je deze persoon goedkeuren?') +
        ('<table role="presentation" width="100%" style="background:#eef6e1;border-radius:8px;margin:0 0 18px;">'
         f'<tr><td style="padding:14px 16px;font-size:14px;color:#333;"><strong>{pending_user.username}</strong>'
         f'<br><span style="color:#6c6c6c;font-size:13px;">{pending_user.email}</span></td></tr></table>') +
        f'<p style="margin:6px 0 20px;">{_btn(approve_url, "Openen om goed te keuren")}</p>' +
        _mail_p('<span style="font-size:13px;color:#6c6c6c;">Log in en ga naar “Mijn team” om goed te keuren of af te wijzen.</span>')
    )
    html = _mail_wrapper('Nieuwe medewerker goedkeuren', body)
    for to in recipients:
        send_mail_async(to, f'Nieuwe medewerker wacht op goedkeuring - {store_name}', html)

@app.route('/registreren', methods=['GET', 'POST'])
def signup():
    if get_current_user():
        return redirect(url_for('dashboard'))
    filialen = [f for f in Filiaal.query.order_by(Filiaal.nummer).all() if f.nummer != DEMO_FILIAAL]
    form = {}
    if request.method == 'POST':
        name  = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        fil   = request.form.get('filiaal', type=int)
        form  = {'username': name, 'email': email, 'filiaal': fil}
        domain = email.rsplit('@', 1)[-1] if '@' in email else ''
        errors = []
        if not name:
            errors.append('Vul je naam in.')
        if domain not in ALLOWED_SIGNUP_DOMAINS:
            errors.append('Registreren kan alleen met een @plus.nl of @plusretail.nl e-mailadres.')
        pe = validate_password(pw)
        if pe:
            errors.append(pe)
        if not fil or not Filiaal.query.filter_by(nummer=fil).first():
            errors.append('Kies een winkel.')
        if find_user_by_email(email):
            errors.append('Er bestaat al een account met dit e-mailadres.')
        if find_user_by_name(name):
            errors.append('Die naam is al in gebruik.')
        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('registreren.html', filialen=filialen, form=form)
        f_obj = Filiaal.query.filter_by(nummer=fil).first()
        u = User(username=name, email=email, role='medewerker', filiaal=fil,
                 filiaal_naam=(f_obj.naam if f_obj else None),
                 password=hash_password(pw), approved=False, must_change_password=False)
        db.session.add(u); db.session.commit()
        log_action('registratie_aangevraagd', f'{name} ({email})', filiaal=fil)
        _notify_owners_pending(u)
        flash('Je account is aangemaakt en wacht op goedkeuring door de winkelondernemer. '
              'Je ontvangt bericht zodra je toegang hebt.', 'success')
        return redirect(url_for('login'))
    return render_template('registreren.html', filialen=filialen, form=form)

@app.route('/forgot', methods=['GET','POST'])
def forgot():
    if request.method == 'POST':
        ident = request.form.get('identifier','').strip()
        # Rate-limit per IP én per doel-account (max 3 verzoeken/kwartier): voorkomt dat iemand met de
        # bekende winkel-e-mailadressen massaal reset-mails laat sturen (spam + mailreputatie-schade).
        now = time.time()
        ip_key = f'forgot_ip:{client_ip()}'
        id_key = f'forgot_id:{ident.lower()[:120]}'
        ip_n, _ = sharedstate.rl_active(ip_key, 900, now)
        id_n, _ = sharedstate.rl_active(id_key, 900, now)
        if ip_n >= 10 or id_n >= 3:
            flash('Te veel verzoeken. Probeer het over een kwartier opnieuw.', 'error')
            return redirect(url_for('login'))
        sharedstate.rl_record(ip_key); sharedstate.rl_record(id_key)
        user = find_user_by_name(ident) or find_user_by_email(ident)
        if user and user.email and mail_enabled():
            try:
                send_setpw_invite(user, 'reset')
            except Exception as e:
                app.logger.error(f'Forgot-mail mislukt: {e}')
        # generieke melding (geen account-enumeratie)
        flash('Als er een account bij deze gegevens hoort met een e-mailadres, '
              'is er een e-mail verstuurd om je wachtwoord opnieuw in te stellen.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot.html')

@app.route('/logout')
def logout():
    session.pop('uid', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    try:
        auto_cleanup_tick()
    except Exception:
        db.session.rollback()
    fil = _active_filiaal()   # superadmin: gekozen winkel of None=alle
    q = Card.query.order_by(Card.timestamp.desc()) if fil is None \
        else Card.query.filter_by(filiaal=fil).order_by(Card.timestamp.desc())
    cards = [c for c in q.all() if not _card_is_scan(c)]   # scankaarten hebben hun eigen dashboard
    return render_template('dashboard.html', cards=cards, user=user,
                           printers=_printers_for_cards(cards))

@app.route('/new')
@login_required
def new_card_select(): return redirect(url_for('kaart_editor'))

@app.route('/kaart_a3')
@login_required
def kaart_a3(): return redirect(url_for('kaart_editor'))

@app.route('/kaart_editor', methods=['GET','POST'])
@login_required
def kaart_editor():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    edit_card = None; edit_data = None
    card_id = request.args.get('edit', type=int)
    if card_id:
        edit_card = Card.query.get(card_id)
        if edit_card and (user.role == 'admin' or edit_card.filiaal == user.filiaal):
            try:
                edit_data = json.loads(edit_card.kaart_data or '{}')
            except Exception:
                edit_data = {}
        else:
            edit_card = None

    if request.method == 'POST':
        g = lambda k: request.form.get(k,'').strip()
        # 'Opslaan & printen' / Ctrl+P uit de editor: geen redirect, maar JSON met kaart-id + PDF terug,
        # zodat de editor de print-keuze (winkelprinter/downloaden) kan tonen zonder de pagina te verlaten.
        want_print = request.form.get('want') == 'print'
        def _card_print_json(card):
            pdf = card_basename(card.image) + '.pdf'
            if not os.path.exists(os.path.join(app.config['EXPORT_FOLDER'], pdf)):
                pdf = card.image
            return jsonify(ok=True, id=card.id, title=card.title,
                           pdf=url_for('static', filename='export/' + pdf))
        formaat = g('formaat') or 'a3_liggend'
        count = int(g('kaart_count') or '1')
        fields = ['merk','koptekst','subtekst','vbtekst','aanv',
                  'verpakking','inhoud','vp1','vp2','actietype','av','av2',
                  'kilo','land','max','kem','alcohol','mix','code','kaarttype','layout','scans','overlay']
        kaarten = []
        for i in range(count):
            dd = {f: g(f'k{i}_{f}') for f in fields}
            # 'max' (aanbiedingen per klant) mag leeg blijven → dan geen 'Maximaal … per klant'-regel.
            kaarten.append(dd)
        labels = FORMAAT_LABELS

        try:
            filename = generate_kaart(kaarten, formaat)
        except Exception as e:
            app.logger.error(f'Kaartgeneratie mislukt: {e}')
            if want_print:
                return jsonify(ok=False, error=f'Kaart genereren mislukt: {e}'), 400
            flash(f'Kaart genereren mislukt: {e}', 'error')
            return redirect(url_for('kaart_editor'))

        title  = kaarten[0].get('koptekst') or kaarten[0].get('merk') or 'Naamloos'
        actie  = kaarten[0].get('actietype','')
        if kaarten[0].get('av'): actie += ' ' + kaarten[0]['av']
        kdata  = json.dumps({'formaat':formaat, 'kaarten':kaarten})
        fn     = user.filiaal_naam or None

        edit_id = request.form.get('edit_id', type=int)
        if edit_id:
            card = Card.query.get(edit_id)
            if card and (user.role == 'admin' or card.filiaal == user.filiaal):
                remove_card_files(card.image)
                card.title = title; card.price = actie or '-'
                card.image = filename; card.formaat = labels.get(formaat, formaat)
                card.kaart_data = kdata; card.timestamp = datetime.now()
                card.filiaal_naam = fn
                db.session.commit()
                if want_print:
                    return _card_print_json(card)
                flash('Kaart bijgewerkt!', 'success')
                return redirect(url_for('dashboard'))

        card = Card(title=title, price=actie or '-', image=filename,
                    formaat=labels.get(formaat,formaat), kaart_data=kdata,
                    username=user.username, filiaal=user.filiaal,
                    filiaal_naam=fn)
        db.session.add(card)
        db.session.commit()
        if want_print:
            return _card_print_json(card)
        flash('Kaart aangemaakt!', 'success')
        return redirect(url_for('dashboard'))

    # Scankaarten worden in hun eigen editor bewerkt
    if edit_data and edit_data.get('mode') == 'scan':
        return redirect(url_for('scankaart_new', edit=card_id))
    return render_template('kaart_editor.html', user=user,
                           edit_card=edit_card, edit_data=edit_data)

@app.route('/scankaarten')
@login_required
def scankaarten():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    fil = _active_filiaal()
    q = Card.query.order_by(Card.timestamp.desc()) if fil is None \
        else Card.query.filter_by(filiaal=fil).order_by(Card.timestamp.desc())
    cards = [c for c in q.all() if _card_is_scan(c)]
    return render_template('scankaarten_dashboard.html', cards=cards, user=user,
                           printers=_printers_for_cards(cards))

@app.route('/demo-account', methods=['GET', 'POST'])
@login_required
def demo_account():
    """Superadmin: het demo-account in-/uitschakelen (login demo/demo)."""
    u = get_current_user()
    if not is_superadmin(u):
        flash('Alleen de superadmin kan het demo-account beheren.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        on = request.form.get('demo_enabled') == 'on'
        set_setting('demo_enabled', '1' if on else '0')
        log_action('demo_toggle', 'aan' if on else 'uit')
        flash('Demo-account ' + ('ingeschakeld.' if on else 'uitgeschakeld.'), 'success')
        return redirect(url_for('demo_account'))
    return render_template('demo_account.html', user=u, enabled=demo_enabled())

@app.route('/kies-winkel/<int:nummer>')
@login_required
def kies_winkel(nummer):
    """Superadmin kiest de winkel waarin 'ie werkt (0 = wissen). Redirect terug naar de vorige pagina."""
    u = get_current_user()
    if is_superadmin(u):
        if nummer == 0:
            session.pop('sa_filiaal', None)
        elif Filiaal.query.filter_by(nummer=nummer).first():
            session['sa_filiaal'] = nummer
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/scankaarten/nieuw', methods=['GET','POST'])
@login_required
def scankaart_new():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    edit_card = None; edit_data = None
    card_id = request.args.get('edit', type=int)
    if card_id:
        edit_card = Card.query.get(card_id)
        if edit_card and (user.role == 'admin' or edit_card.filiaal == user.filiaal):
            try:
                edit_data = json.loads(edit_card.kaart_data or '{}')
            except Exception:
                edit_data = {}
        else:
            edit_card = None

    if request.method == 'POST':
        raw = request.form.get('cards', '').strip()
        try:
            parsed = json.loads(raw) if raw else []
        except Exception:
            parsed = []
        # 4 kaarten, elk een lijst producten
        cells = []
        for i in range(4):
            lst = parsed[i] if i < len(parsed) and isinstance(parsed[i], list) else []
            cells.append({'products': [p for p in lst if isinstance(p, dict)]})
        try:
            filename = generate_scankaart(cells)
        except Exception as e:
            app.logger.error(f'Scankaart mislukt: {e}')
            flash(f'Scankaart genereren mislukt: {e}', 'error')
            return redirect(url_for('scankaart_new'))
        first = next((p.get('naam') for c in cells for p in c['products'] if p.get('naam')), None)
        title = first or 'Scankaart'
        kdata = json.dumps({'mode': 'scan', 'cells': cells})
        fn = user.filiaal_naam or None
        edit_id = request.form.get('edit_id', type=int)
        if edit_id:
            card = Card.query.get(edit_id)
            if card and (user.role == 'admin' or card.filiaal == user.filiaal):
                remove_card_files(card.image)
                card.title = title; card.price = 'Scankaart'; card.image = filename
                card.formaat = 'Scankaart (SK Maxi)'; card.kaart_data = kdata
                card.timestamp = datetime.now(); card.filiaal_naam = fn
                db.session.commit()
                flash('Scankaart bijgewerkt!', 'success')
                return redirect(url_for('scankaarten'))
        db.session.add(Card(title=title, price='Scankaart', image=filename,
                            formaat='Scankaart (SK Maxi)', kaart_data=kdata,
                            username=user.username, filiaal=user.filiaal, filiaal_naam=fn))
        db.session.commit()
        flash('Scankaart aangemaakt!', 'success')
        return redirect(url_for('scankaarten'))

    return render_template('scankaart_editor.html', user=user,
                           edit_card=edit_card, edit_data=edit_data)

@app.route('/print/<int:card_id>')
@login_required
def print_card(card_id):
    """Open de PDF (het printbare product) in de browser."""
    card = Card.query.get_or_404(card_id)
    u = get_current_user()
    # Alleen eigen-winkel kaarten (of superadmin) - voorkomt inkijk in kaarten van andere filialen.
    if not is_superadmin(u) and card.filiaal != (u.filiaal if u else None):
        abort(403)
    pdf = card_basename(card.image) + '.pdf'
    if not os.path.exists(os.path.join(app.config['EXPORT_FOLDER'], pdf)):
        # Oudere kaart zonder PDF: val terug op de PNG-preview
        pdf = card.image
    return redirect(url_for('static', filename='export/' + pdf))

@app.route('/delete_card/<int:card_id>', methods=['POST'])
@login_required
def delete_card(card_id):
    user = get_current_user()
    card = Card.query.get_or_404(card_id)
    if user.role != 'admin' and card.filiaal != user.filiaal:
        flash('Geen rechten.', 'error')
        return redirect(url_for('dashboard'))
    remove_card_files(card.image)
    db.session.delete(card); db.session.commit()
    flash('Kaart verwijderd.', 'success')
    return redirect(url_for('dashboard'))

def _safe_next(default_endpoint):
    """Interne redirect-URL uit het 'next'-veld (voor modals op andere pagina's), met terugval."""
    nxt = request.values.get('next', '')
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return url_for(default_endpoint)

@app.route('/register', methods=['GET','POST'])
@login_required
def register():
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Alleen de superadmin kan het volledige gebruikersbeheer doen. Gebruik "Mijn team" voor je eigen winkel.', 'error')
        return redirect(url_for('team') if can(user, 'team') else url_for('dashboard'))
    if request.method == 'POST':
        un   = request.form.get('username','').strip()
        ro   = request.form.get('role','')
        fi   = int(request.form.get('filiaal', user.filiaal) or user.filiaal)
        if not Role.query.filter_by(name=ro).first():
            flash('Ongeldige rol.', 'error')
            return redirect(_safe_next('register'))
        if not un:
            flash('Vul een naam in.', 'error')
            return redirect(_safe_next('register'))
        if find_user_by_name(un):
            flash('Er bestaat al een gebruiker met deze naam.', 'error')
            return redirect(_safe_next('register'))
        email = (request.form.get('email','').strip().lower() or None)
        if not email:
            flash('E-mailadres is verplicht (dit is het inlogadres).', 'error')
            return redirect(_safe_next('register'))
        if find_user_by_email(email):
            flash('Er bestaat al een gebruiker met dit e-mailadres.', 'error')
            return redirect(_safe_next('register'))
        f_obj = Filiaal.query.filter_by(nummer=fi).first()
        fn = f_obj.naam if f_obj else None
        # Account aanmaken zoals in de Label Manager: geen wachtwoord invoeren; het systeem maakt een
        # tijdelijk wachtwoord aan en mailt de uitnodiging. Faalt de mail, dan tonen we het wachtwoord.
        new_user = User(username=un, password='!', role=ro, filiaal=fi, filiaal_naam=fn, email=email)
        db.session.add(new_user)
        db.session.commit()
        ok, err, temp = send_welcome_invite(new_user)
        if ok:
            flash(f'Gebruiker "{un}" aangemaakt. Uitnodiging met tijdelijk wachtwoord gemaild naar {email}.', 'success')
        else:
            flash(f'Gebruiker "{un}" aangemaakt, maar de uitnodiging kon niet worden verstuurd ({err}). '
                  f'Tijdelijk wachtwoord: {temp} - geef dit persoonlijk door.', 'warning')
        return redirect(_safe_next('register'))
    users = User.query.all()
    filialen = Filiaal.query.order_by(Filiaal.nummer).all()
    roles = Role.query.order_by(Role.is_system.desc(), Role.label).all()
    role_labels = {r.name: r.label for r in roles}
    return render_template('register.html', users=users, current_user=user, user=user,
                           filialen=filialen, roles=roles, role_labels=role_labels)

@app.route('/user/<int:user_id>', methods=['GET','POST'])
@login_required
def edit_user(user_id):
    cur = get_current_user()
    tgt = User.query.get_or_404(user_id)
    if not (cur.role in ['admin','ondernemer']):
        flash('Geen toegang.', 'error'); return redirect(url_for('dashboard'))
    # ondernemer mag alleen eigen-filiaal, niet-admin gebruikers openen
    if cur.role == 'ondernemer' and not (tgt.filiaal == cur.filiaal and tgt.role != 'admin'):
        flash('Geen rechten voor deze gebruiker.', 'error'); return redirect(url_for('register'))
    is_admin = cur.role == 'admin'

    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'mfa_reset':
            if not is_admin:
                flash('Alleen een beheerder kan 2FA resetten.', 'error')
            else:
                tgt.mfa_enabled = False; tgt.mfa_secret = None
                db.session.commit()
                log_action('mfa_gereset', tgt.username, filiaal=tgt.filiaal)
                flash(f'Twee-factor-authenticatie van {tgt.username} is gereset - de gebruiker stelt '
                      'deze bij de volgende login opnieuw in.', 'success')
            return redirect(url_for('edit_user', user_id=tgt.id))
        if action == 'welcome':
            if not tgt.email:
                flash('Voeg eerst een e-mailadres toe.', 'error')
            else:
                ok, err, temp = send_welcome_invite(tgt)
                flash(f'Uitnodiging met tijdelijk wachtwoord verstuurd naar {tgt.email}.' if ok
                      else f'Mail mislukt ({err}). Tijdelijk wachtwoord: {temp} - geef dit door.',
                      'success' if ok else 'warning')
            return redirect(url_for('edit_user', user_id=tgt.id))
        if action == 'reset':
            if tgt.email and mail_enabled():
                ok, err = send_setpw_invite(tgt, 'reset')
                flash(f'Wachtwoord-instellink verstuurd naar {tgt.email}.' if ok else f'Mail mislukt: {err}',
                      'success' if ok else 'error')
            else:
                new = request.form.get('new_password','').strip()
                if len(new) < 8:
                    flash('Geen e-mailadres: geef een wachtwoord van minimaal 8 tekens op.', 'error')
                else:
                    tgt.password = hash_password(new); db.session.commit()
                    flash('Wachtwoord direct ingesteld.', 'success')
            return redirect(url_for('edit_user', user_id=tgt.id))
        if action == 'delete':
            if tgt.username == cur.username:
                flash('Je kunt jezelf niet verwijderen.', 'error')
                return redirect(url_for('edit_user', user_id=tgt.id))
            cards = Card.query.filter_by(username=tgt.username).all()
            dest_id = request.form.get('transfer_to', type=int)
            moved = 0
            if cards and dest_id:
                dest = User.query.get(dest_id)
                if dest and dest.id != tgt.id:
                    for c in cards:
                        c.username = dest.username
                        c.filiaal = dest.filiaal
                        c.filiaal_naam = dest.filiaal_naam
                    moved = len(cards)
            db.session.delete(tgt); db.session.commit()
            extra = f' {moved} kaart(en) overgezet naar de gekozen gebruiker.' if moved else ''
            flash(f'"{tgt.username}" verwijderd.{extra}', 'success')
            return redirect(url_for('register'))

        # save-gegevens
        un = request.form.get('username','').strip()
        if un and un.lower() != tgt.username.lower():
            dup = find_user_by_name(un)
            if dup and dup.id != tgt.id:
                flash('Er bestaat al een gebruiker met deze naam.', 'error')
                return redirect(url_for('edit_user', user_id=tgt.id))
            tgt.username = un
        email = request.form.get('email','').strip().lower() or None
        if email:
            dupe = find_user_by_email(email)
            if dupe and dupe.id != tgt.id:
                flash('Er bestaat al een gebruiker met dit e-mailadres.', 'error')
                return redirect(url_for('edit_user', user_id=tgt.id))
        tgt.email = email
        if is_admin:
            ro = request.form.get('role','')
            if Role.query.filter_by(name=ro).first():
                tgt.role = ro
            try:
                fi = int(request.form.get('filiaal', tgt.filiaal) or tgt.filiaal)
            except ValueError:
                fi = tgt.filiaal
            tgt.filiaal = fi
            f_obj = Filiaal.query.filter_by(nummer=fi).first()
            tgt.filiaal_naam = f_obj.naam if f_obj else None
            # Toegang & beveiliging
            pol = request.form.get('access_policy', 'anywhere')
            tgt.access_policy = pol if pol in ('anywhere', 'ip_login', 'ip_print') else 'anywhere'
            tgt.allowed_ips = request.form.get('allowed_ips', '').strip() or None
            tgt.approved = bool(request.form.get('approved'))
            tgt.must_change_password = bool(request.form.get('must_change_password'))
            tgt.show_tour = bool(request.form.get('show_tour'))
        db.session.commit()
        log_action('gebruiker_gewijzigd', tgt.username, filiaal=tgt.filiaal)
        flash('Gegevens opgeslagen.', 'success')
        return redirect(url_for('edit_user', user_id=tgt.id))

    filialen = Filiaal.query.order_by(Filiaal.nummer).all()
    roles = Role.query.order_by(Role.is_system.desc(), Role.label).all()
    role_labels = {r.name: r.label for r in roles}
    card_count = Card.query.filter_by(username=tgt.username).count()
    others_q = User.query.filter(User.id != tgt.id)
    if cur.role == 'ondernemer':
        others_q = others_q.filter_by(filiaal=cur.filiaal)
    others = others_q.order_by(User.username).all()
    return render_template('user_edit.html', user=cur, tgt=tgt, filialen=filialen,
                           is_admin=is_admin, card_count=card_count, others=others,
                           roles=roles, role_labels=role_labels)

def _may_manage(cur, tgt):
    """Mag cur het account van tgt beheren (reset/welkomstmail)?"""
    return cur.role == 'admin' or (
        cur.role == 'ondernemer' and tgt.filiaal == cur.filiaal and tgt.role != 'admin')

@app.route('/send_welcome/<int:user_id>', methods=['POST'])
@login_required
def send_welcome(user_id):
    cur = get_current_user()
    tgt = User.query.get_or_404(user_id)
    if not _may_manage(cur, tgt):
        flash('Geen rechten.', 'error'); return redirect(url_for('register'))
    if not tgt.email:
        flash(f'"{tgt.username}" heeft geen e-mailadres; voeg dat eerst toe.', 'error')
        return redirect(url_for('register'))
    ok, err, temp = send_welcome_invite(tgt)
    flash(f'Uitnodiging met tijdelijk wachtwoord verstuurd naar {tgt.email}.' if ok
          else f'Mail mislukt ({err}). Tijdelijk wachtwoord: {temp} - geef dit door.',
          'success' if ok else 'warning')
    return redirect(url_for('register'))

@app.route('/reset_password/<int:user_id>', methods=['POST'])
@login_required
def reset_password(user_id):
    cur = get_current_user()
    tgt = User.query.get_or_404(user_id)
    if not _may_manage(cur, tgt):
        flash('Geen rechten om dit wachtwoord te resetten.', 'error')
        return redirect(url_for('register'))
    mode = request.form.get('mode', 'invite')
    if mode == 'invite':
        ok, err = send_setpw_invite(tgt, 'reset')
        flash(f'Wachtwoord-instellink verstuurd naar {tgt.email}.' if ok
              else f'Mail mislukt: {err}', 'success' if ok else 'error')
        return redirect(url_for('register'))
    # directe reset (bijv. voor gebruikers zonder e-mailadres)
    new = request.form.get('new_password','').strip()
    if len(new) < 8:
        flash('Wachtwoord moet minimaal 8 tekens zijn.', 'error')
        return redirect(url_for('register'))
    tgt.password = hash_password(new)
    db.session.commit()
    flash(f'Wachtwoord van "{tgt.username}" direct ingesteld.', 'success')
    return redirect(url_for('register'))

@app.route('/set-password/<token>', methods=['GET','POST'])
def set_password(token):
    tgt = verify_setpw_token(token)
    if not tgt:
        flash('Deze link is ongeldig of verlopen. Vraag een nieuwe aan.', 'error')
        return redirect(url_for('forgot'))
    if request.method == 'POST':
        new1 = request.form.get('new','').strip()
        new2 = request.form.get('new2','').strip()
        if new1 != new2:
            flash('Wachtwoorden komen niet overeen.', 'error')
            return render_template('set_password.html', token=token, username=tgt.username, hint=PW_HINT)
        if len(new1) < 8:
            flash('Wachtwoord moet minimaal 8 tekens zijn.', 'error')
            return render_template('set_password.html', token=token, username=tgt.username, hint=PW_HINT)
        tgt.password = hash_password(new1)
        db.session.commit()
        flash('Je wachtwoord is ingesteld. Je kunt nu inloggen.', 'success')
        return redirect(url_for('login'))
    return render_template('set_password.html', token=token, username=tgt.username, hint=PW_HINT)

# ─── FILIALEN (superadmin) ────────────────────────────────────────────────────
@app.route('/filialen', methods=['GET','POST'])
@login_required
def filialen():
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Alleen de superadmin kan filialen beheren.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        try:
            nummer = int(request.form.get('nummer','').strip())
        except ValueError:
            flash('Winkelnummer moet een getal zijn.', 'error')
            return redirect(_safe_next('filialen'))
        naam = request.form.get('naam','').strip() or None
        if Filiaal.query.filter_by(nummer=nummer).first():
            flash(f'Filiaal met winkelnummer {nummer} bestaat al.', 'error')
            return redirect(_safe_next('filialen'))
        db.session.add(Filiaal(nummer=nummer, naam=naam))
        db.session.commit()
        flash(f'Filiaal {nummer} aangemaakt.', 'success')
        return redirect(_safe_next('filialen'))
    items = Filiaal.query.order_by(Filiaal.nummer).all()
    counts = {f.nummer: User.query.filter_by(filiaal=f.nummer).count() for f in items}
    return render_template('filialen.html', filialen=items, counts=counts, user=user)

@app.route('/beheer/gebruikers-winkels')
@login_required
def gebruikers_winkels():
    """Gecombineerd gebruikers- en winkelbeheer op één pagina (tabbladen Gebruikers / Winkels)."""
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Alleen de superadmin kan dit beheren.', 'error')
        return redirect(url_for('dashboard'))
    users = User.query.order_by(User.username).all()
    filialen = Filiaal.query.order_by(Filiaal.nummer).all()
    counts, ond_counts = {}, {}
    for u in users:
        counts[u.filiaal] = counts.get(u.filiaal, 0) + 1
        if u.role in ('ondernemer', 'admin'):
            ond_counts[u.filiaal] = ond_counts.get(u.filiaal, 0) + 1
    def _st(f):
        if not f.agent_key:
            return 'direct'
        return 'on' if _agent_online(f) else 'off'
    disp = lambda f: ('PLUS ' + f.naam) if f.naam else ('Filiaal ' + str(f.nummer))
    snames = {f.nummer: disp(f) for f in filialen}
    stores = [{'nr': f.nummer, 'naam': disp(f), 'count': counts.get(f.nummer, 0),
               'ond': ond_counts.get(f.nummer, 0), 'status': _st(f)} for f in filialen]
    roles = Role.query.order_by(Role.is_system.desc(), Role.label).all()
    role_labels = {r.name: r.label for r in roles}
    return render_template('beheer_gwb.html', user=user, users=users, filialen=filialen,
                           stores=stores, stores_json=json.dumps(stores), snames=snames,
                           role_labels=role_labels, roles=roles)

@app.route('/delete_filiaal/<int:fid>', methods=['POST'])
@login_required
def delete_filiaal(fid):
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Geen rechten.', 'error')
        return redirect(url_for('dashboard'))
    f = Filiaal.query.get_or_404(fid)
    if User.query.filter_by(filiaal=f.nummer).count() > 0:
        flash('Filiaal heeft nog gebruikers; verwijder die eerst.', 'error')
        return redirect(url_for('filialen'))
    db.session.delete(f); db.session.commit()
    flash(f'Filiaal {f.nummer} verwijderd.', 'success')
    return redirect(url_for('filialen'))

# ─── MAIL-INSTELLINGEN (superadmin) ───────────────────────────────────────────
_MAIL_KEYS = ['smtp_host','smtp_port','smtp_user','smtp_pass','smtp_from','smtp_from_name','app_url']

@app.route('/mail_settings', methods=['GET','POST'])
@login_required
def mail_settings():
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Alleen de superadmin kan mailinstellingen beheren.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        action = request.form.get('action','save')
        if action == 'test':
            to = request.form.get('test_to','').strip() or user.email
            if not to:
                flash('Vul een test-e-mailadres in.', 'error')
                return redirect(url_for('mail_settings'))
            body = ('<p style="font-size:15px;line-height:1.6;">Dit is een testmail vanuit de '
                    'PLUSLokaal schapkaarten-omgeving. Als je deze ontvangt, werkt je SMTP-instelling. 🎉</p>')
            ok, err = send_mail(to, 'PLUSLokaal - testmail', _mail_wrapper('Testmail', body))
            flash('Testmail verstuurd naar ' + to if ok else f'Testmail mislukt: {err}',
                  'success' if ok else 'error')
            return redirect(url_for('mail_settings'))
        for k in _MAIL_KEYS:
            val = request.form.get(k, '').strip()
            # Het wachtwoord/de API-key wordt in het formulier NIET voorgevuld; een leeg veld
            # betekent 'ongewijzigd laten' i.p.v. de sleutel wissen.
            if k == 'smtp_pass' and val == '':
                continue
            set_setting(k, val)
        set_setting('mail_enabled', '1' if request.form.get('mail_enabled') else '0')
        flash('Mailinstellingen opgeslagen.', 'success')
        return redirect(url_for('mail_settings'))
    cfg = {k: get_setting(k) for k in _MAIL_KEYS}
    # De echte sleutel niet naar de browser sturen; alleen tonen ÓF er een is ingesteld.
    cfg['smtp_pass'] = ''
    cfg['smtp_pass_set'] = bool(get_setting('smtp_pass'))
    cfg['mail_enabled'] = mail_enabled()
    return render_template('mail_settings.html', user=user, cfg=cfg)

@app.route('/opslag', methods=['GET','POST'])
@login_required
def opslag():
    import shutil
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Alleen de superadmin kan de opslag beheren.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        action = request.form.get('action', 'save')
        if action == 'cleanup':
            days = request.form.get('cleanup_days_manual', '').strip()
            n, freed = run_card_cleanup(days)
            if n:
                flash(f'{n} kaart(en) ouder dan {days} dagen verwijderd ({freed/1048576:.1f} MB vrijgemaakt).', 'success')
            else:
                flash('Geen kaarten gevonden ouder dan de opgegeven periode.', 'success')
        elif action == 'w2p_delete_week':
            pid = request.form.get('period_id', type=int)
            n, freed, cards = _delete_w2p_week(pid)
            flash(f'Week volledig verwijderd: {cards} kaart(en) en {n} PDF-bestand(en) weg '
                  f'({freed/1048576:.1f} MB vrijgemaakt). De week is nu ook niet meer zichtbaar bij Winkelpakketten.', 'success')
        elif action == 'w2p_delete_all':
            n, freed = _delete_w2p_cache()
            flash(f'Volledige winkelpakket-cache verwijderd: {n} bestand(en) ({freed/1048576:.1f} MB vrijgemaakt).', 'success')
        else:  # save auto-rule
            set_setting('cleanup_auto', '1' if request.form.get('cleanup_auto') else '0')
            set_setting('cleanup_days', request.form.get('cleanup_days', '').strip() or '0')
            flash('Opruimregel opgeslagen.', 'success')
        return redirect(url_for('opslag'))

    export_dir = app.config['EXPORT_FOLDER']
    db_path = os.path.join(app.instance_path, 'pluslokaal.db')
    export_bytes = _dir_size(export_dir)
    db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    try:
        du = shutil.disk_usage(export_dir)
        disk_total, disk_used, disk_free = du.total, du.used, du.free
    except OSError:
        disk_total = disk_used = disk_free = 0
    cards = Card.query.order_by(Card.timestamp.asc()).all()
    oldest = cards[0].timestamp if cards else None
    stats = {
        'export_bytes': export_bytes,
        'db_bytes': db_bytes,
        'disk_total': disk_total,
        'disk_used': disk_used,
        'disk_free': disk_free,
        'disk_pct': round(disk_used / disk_total * 100, 1) if disk_total else 0,
        'card_count': len(cards),
        'file_count': len([f for f in os.listdir(export_dir) if os.path.isfile(os.path.join(export_dir, f))]) if os.path.isdir(export_dir) else 0,
        'oldest': oldest,
        'cleanup_auto': get_setting('cleanup_auto', '0') == '1',
        'cleanup_days': get_setting('cleanup_days', '0'),
        'cleanup_last_run': get_setting('cleanup_last_run', ''),
    }
    return render_template('opslag.html', user=user, s=stats, w2p=_w2p_cache_stats())

def _delete_w2p_cache(period_id=None):
    """Verwijder gecachete winkelpakket-PDF's (alle, of van één week/periode) + hun DB-rijen.
    Geeft (aantal_bestanden, vrijgemaakte_bytes)."""
    q = W2PCachedPdf.query
    if period_id is not None:
        q = q.filter_by(period_id=period_id)
    rows = q.all()
    pdf_dir = _w2p_pdf_dir(); n = 0; freed = 0
    for r in rows:
        p = os.path.join(pdf_dir, r.path)
        if os.path.exists(p):
            try:
                freed += os.path.getsize(p); os.remove(p); n += 1
            except OSError:
                pass
        db.session.delete(r)
    db.session.commit()
    return n, freed

def _delete_w2p_week(period_id):
    """Verwijder een week VOLLEDIG: gedownloade PDF's + cache-rijen, de metadata (kaarten) én de
    thumbnails. Daarna is de week nergens meer zichtbaar - ook niet bij Winkelpakketten - en wordt
    'ie ook niet meer automatisch aangevuld."""
    n, freed = _delete_w2p_cache(period_id=period_id)     # PDF-bestanden + cache-rijen
    thumb_dir = _w2p_thumb_dir()
    docs = W2PDocument.query.filter_by(period_id=period_id).all()
    for d in docs:
        tp = os.path.join(thumb_dir, f'{d.promotion_document_id}.png')
        if os.path.exists(tp):
            try:
                freed += os.path.getsize(tp); os.remove(tp)
            except OSError:
                pass
        db.session.delete(d)
    db.session.commit()
    return n, freed, len(docs)

def _w2p_cache_stats():
    """Statistieken over de winkelpakket-cache voor de opslag-pagina: totaal + per week (periode)."""
    from collections import defaultdict
    pdf_dir = _w2p_pdf_dir()
    cat_labels = dict(W2P_CATEGORIES)
    period_labels = {p: l for p, l in
                     db.session.query(W2PDocument.period_id, W2PDocument.period_label).distinct().all()}
    period_cat = {p: c for p, c in
                  db.session.query(W2PDocument.period_id, W2PDocument.category_id).distinct().all()}
    total_cards = {p: cnt for p, cnt in
                   db.session.query(W2PDocument.period_id, func.count()).group_by(W2PDocument.period_id).all()}
    total_groups = {p: cnt for p, cnt in
                    db.session.query(W2PDocument.period_id, func.count(func.distinct(W2PDocument.group_id)))
                    .group_by(W2PDocument.period_id).all()}
    per = defaultdict(lambda: {'groups': set(), 'rows': 0, 'cards': 0, 'bytes': 0})
    for r in W2PCachedPdf.query.all():
        e = per[r.period_id]
        e['groups'].add(r.group_id); e['rows'] += 1
        try:
            e['cards'] += len(json.loads(r.doc_ids))
        except Exception:
            pass
        p = os.path.join(pdf_dir, r.path)
        if os.path.exists(p):
            e['bytes'] += os.path.getsize(p)
    # ALLE weken tonen: zowel gedownloade als weken die alleen metadata (in cache) hebben, met een
    # duidelijke status - zo zie je precies wat er bekend is vs. wat print-klaar op de server staat.
    weeks = []
    for pid in set(list(period_labels.keys()) + list(per.keys())):
        e = per.get(pid, {'groups': set(), 'rows': 0, 'cards': 0, 'bytes': 0})
        tg = total_groups.get(pid, 0)
        dg = len(e['groups'])
        if e['rows'] == 0:
            status = 'none'          # alleen in cache (metadata), nog niet gedownload
        elif tg and dg >= tg:
            status = 'full'          # volledig gedownload / print-klaar
        else:
            status = 'partial'       # deels gedownload
        weeks.append({'period_id': pid, 'label': period_labels.get(pid, str(pid)),
                      'category': cat_labels.get(period_cat.get(pid), ''),
                      'groups': tg, 'downloaded_groups': dg, 'rows': e['rows'], 'cards': e['cards'],
                      'total_cards': total_cards.get(pid, 0), 'bytes': e['bytes'], 'status': status})
    weeks.sort(key=lambda x: x['label'], reverse=True)
    return {
        'total_bytes': _dir_size(pdf_dir),
        'thumb_bytes': _dir_size(_w2p_thumb_dir()),
        'total_rows': W2PCachedPdf.query.count(),
        'total_cards': sum(w['cards'] for w in weeks),
        'weeks': weeks,
        'syncing': _w2p_pdf_state['running'] or _w2p_meta_state['running'],
    }

@app.route('/w2p-accounts', methods=['GET', 'POST'])
@login_required
def w2p_accounts():
    """Beheer de pluslokaal.nl-accounts (max. 6) waarmee winkelpakketten op de achtergrond worden
    gedownload. Meer accounts = meer parallelle downloads (elk account krijgt z'n eigen download-worker
    met een eigen winkelmandje). Plus: per superadmin instellen of 'ie mail krijgt bij een mislukte
    sync/download."""
    user = get_current_user()
    if not user or user.role != 'admin':
        flash('Alleen de superadmin kan winkelpakket-accounts beheren.', 'error')
        return redirect(url_for('dashboard'))
    import w2p_client
    if request.method == 'POST':
        changed = False
        for i in range(1, W2P_MAX_ACCOUNTS + 1):
            uk = 'w2p_user' if i == 1 else f'w2p_user{i}'
            pk = 'w2p_pass' if i == 1 else f'w2p_pass{i}'
            un = request.form.get(f'user{i}', '').strip()
            pw = request.form.get(f'pass{i}', '')          # leeg = wachtwoord behouden
            old_un = get_setting(uk)
            set_setting(uk, un)
            if not un:
                if get_setting(pk):
                    set_setting(pk, '')                    # gebruikersnaam leeg → account gewist
                    changed = True
            elif pw:
                set_setting(pk, _w2p_pass_store(pw))
                changed = True
            if un != old_un:
                changed = True
        set_setting('w2p_base', request.form.get('base', '').strip() or 'https://pluslokaal.nl')
        for a in User.query.filter_by(role='admin').all():
            a.notify_w2p_fail = bool(request.form.get(f'notify_{a.id}'))
        db.session.commit()
        if changed:
            try:
                w2p_client.reset_pool()                    # workers verse inloggegevens laten inlezen
            except Exception:
                pass
        flash('Winkelpakket-accounts opgeslagen.' + (' De download-workers zijn vernieuwd met de nieuwe gegevens.' if changed else ''), 'success')
        return redirect(url_for('w2p_accounts'))
    accounts = []
    for i in range(1, W2P_MAX_ACCOUNTS + 1):
        uk = 'w2p_user' if i == 1 else f'w2p_user{i}'
        pk = 'w2p_pass' if i == 1 else f'w2p_pass{i}'
        accounts.append({'i': i, 'user': get_setting(uk), 'pass_set': bool(get_setting(pk))})
    n_active = sum(1 for a in accounts if a['user'] and a['pass_set'])
    admins = User.query.filter_by(role='admin').order_by(User.username).all()
    return render_template('w2p_accounts.html', user=user, accounts=accounts, admins=admins,
                           base=get_setting('w2p_base') or 'https://pluslokaal.nl',
                           n_active=n_active, max_accounts=W2P_MAX_ACCOUNTS)

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    cur = get_current_user()
    tgt = User.query.get_or_404(user_id)
    if tgt.username == cur.username:
        flash('Uzelf verwijderen is niet mogelijk.', 'error')
        return redirect(url_for('register'))
    if cur.role == 'admin' or (cur.role == 'ondernemer' and tgt.filiaal == cur.filiaal):
        db.session.delete(tgt); db.session.commit()
        flash(f'"{tgt.username}" verwijderd.', 'success')
    else:
        flash('Geen rechten.', 'error')
    return redirect(url_for('register'))

@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    user = get_current_user()
    if not user: return redirect(url_for('login'))
    if request.method == 'POST':
        if is_demo(user):    # demo is een gedeeld account → niet wijzigbaar (anders lockt 'ie zichzelf uit)
            flash('In de demo kun je het account niet wijzigen.', 'error')
            return redirect(url_for('profile'))
        action = request.form.get('action','')
        if action == 'avatar':
            data = request.form.get('avatar_data','').strip()
            if data.startswith('data:image/') and len(data) < 3_000_000:
                user.avatar = data
                db.session.commit()
                flash('Profielfoto bijgewerkt.', 'success')
            elif data == 'remove':
                user.avatar = None
                db.session.commit()
                flash('Profielfoto verwijderd.', 'success')
            else:
                flash('Ongeldige afbeelding.', 'error')
            return redirect(url_for('profile'))
        if action == 'portaal':
            # pluslokaal.nl-koppeling instellen/bijwerken vanuit het profiel
            pu = (request.form.get('portaal_user') or '').strip()
            pw = request.form.get('portaal_pass') or ''
            if not pu:
                flash('Vul je pluslokaal.nl-gebruikersnaam in.', 'error')
                return redirect(url_for('profile'))
            if not pw:                       # leeg → bestaand wachtwoord hergebruiken
                pw = _portaal_decrypt(user.portaal_pass_enc) or ''
            if not pw:
                flash('Vul ook je pluslokaal.nl-wachtwoord in.', 'error')
                return redirect(url_for('profile'))
            import portaal as _pmod
            ok, msg = _pmod.login(user.id, pu, pw)
            if not ok:
                flash(f'Koppelen mislukt: {msg}', 'error')
                return redirect(url_for('profile'))
            user.portaal_user = pu
            user.portaal_pass_enc = _portaal_encrypt(pw)
            user.portaal_status = 'ok'
            user.portaal_checked = datetime.now()
            _portaal_home_cache.pop(user.id, None)
            db.session.commit()
            flash('pluslokaal.nl-koppeling bijgewerkt.', 'success')
            return redirect(url_for('profile'))
        if action == 'portaal_unlink':
            try:
                import portaal as _pmod
                _pmod.logout(user.id)
            except Exception:
                pass
            user.portaal_user = None
            user.portaal_pass_enc = None
            user.portaal_status = 'none'
            user.portaal_checked = None
            _portaal_home_cache.pop(user.id, None)
            db.session.commit()
            flash('pluslokaal.nl ontkoppeld.', 'success')
            return redirect(url_for('profile'))
        # wachtwoord wijzigen
        new1 = request.form.get('new',''); new2 = request.form.get('new2','')
        if new1 != new2:
            flash('Wachtwoorden komen niet overeen.', 'error')
            return redirect(url_for('profile'))
        if len(new1) < 8:
            flash('Wachtwoord moet minimaal 8 tekens zijn.', 'error')
            return redirect(url_for('profile'))
        if not _check_pw(user, request.form.get('current','')):
            flash('Huidig wachtwoord klopt niet.', 'error')
            return redirect(url_for('profile'))
        user.password = hash_password(new1)
        db.session.commit()
        session['pwv'] = _pw_marker(user)   # dit apparaat ingelogd houden; andere sessies vervallen
        flash('Wachtwoord gewijzigd.', 'success')
        return redirect(url_for('profile'))
    return render_template('profile.html', user=user)

@app.route('/change_password')
@login_required
def change_password():
    return redirect(url_for('profile'))

# ─── LABELS-MODULE: routes ────────────────────────────────────────────────────
import labelimage as _labelimage
import labelgen as _labelgen

_label_logo_cache = {}
def _label_logo_path():
    """Rasterlogo (PNG) voor op het thermische label; gerenderd uit de PLUS-SVG. None bij falen."""
    key = get_setting('label_logo', '')
    if key:
        p = os.path.join(os.path.dirname(__file__), key.lstrip('/'))
        if os.path.exists(p):
            return p
    p = os.path.join(app.config.get('EXPORT_FOLDER', ''), '..', 'img', 'label-logo.png')
    p = os.path.join(os.path.dirname(__file__), 'static', 'img', 'label-logo.png')
    if 'gen' not in _label_logo_cache:
        _label_logo_cache['gen'] = True
        try:
            import cairosvg
            svg = os.path.join(os.path.dirname(__file__), 'static', 'img', 'plus-logo.svg')
            cairosvg.svg2png(url=svg, write_to=p, output_height=200, background_color='white')
        except Exception:
            pass
    return p if os.path.exists(p) else None

def _render_label_png(item, opts, Lw, Lh, dpi=120, show_logo=False):
    from io import BytesIO
    logo = _label_logo_path() if show_logo else None
    img = _labelimage.render_label(item, opts, Lw, Lh, dpi=dpi,
                                   logo_path=logo, show_logo=bool(show_logo and logo))
    bio = BytesIO(); img.convert('L').save(bio, 'PNG'); bio.seek(0)
    return bio

def _active_filiaal():
    """De winkel waarin de gebruiker nu 'werkt'. Superadmin kiest een winkel (session sa_filiaal,
    instelbaar via ?filiaal= of de winkelkiezer in de header); None = nog geen winkel gekozen.
    Overige rollen: hun eigen winkel."""
    u = get_current_user()
    if is_superadmin(u):
        f = request.args.get('filiaal', type=int)
        if f is not None:
            if f == 0: session.pop('sa_filiaal', None)
            else: session['sa_filiaal'] = f
        return session.get('sa_filiaal')
    return u.filiaal if u else None

def _label_filiaal():
    """Effectief winkelnummer voor de labelscope (alias van de gedeelde winkelkeuze)."""
    return _active_filiaal()

def _label_store_w_h(fil):
    f = Filiaal.query.filter_by(nummer=fil).first() if fil else None
    Lw = (f.printer_label_w if f and f.printer_label_w else None) or 45.0
    Lh = (f.printer_label_h if f and f.printer_label_h else None) or 40.0
    return Lw, Lh

def _num(v):
    try:
        return float(str(v).replace(',', '.'))
    except (TypeError, ValueError):
        return None

# ════════════════════════════════════════════════════════════════════════════
#  DESIGNER (Bèta) - vrije canvas-editor voor labels én papier (kaarten/posters)
#  Editor is client-side (DOM, slepen/schalen/draaien); de PRINT/PDF-uitvoer wordt
#  server-side met PIL gerenderd vanuit de design-JSON (print-perfect, zelfde
#  Montserrat-font als de kaarten). Elementen hebben fractionele coördinaten
#  (x,y,w,h in 0..1 van het canvas) zodat één ontwerp op elke resolutie klopt.
# ════════════════════════════════════════════════════════════════════════════
_DESIGNER_PAPER = [
    ('A6 (105×148)', 105, 148), ('A5 (148×210)', 148, 210),
    ('A4 (210×297)', 210, 297), ('A3 (297×420)', 297, 420),
    ('Vierkant 20×20', 200, 200), ('Poster A2 (420×594)', 420, 594),
]
_DESIGNER_FONTS = {
    'montserrat': ('static/fonts/Montserrat-var.ttf', 'static/fonts/Montserrat-var.ttf', 'Montserrat'),
    'gothic':     ('static/fonts/gothica1/GothicA1-Regular.ttf', 'static/fonts/gothica1/GothicA1-Bold.ttf', 'Gothic A1'),
}
# FontAwesome-iconen die je kunt invoegen; de editor rastert het gekozen icoon naar een afbeelding
# (met de gekozen kleur), zodat de server het gewoon als afbeelding rendert.
_DESIGNER_ICONS = ['star', 'heart', 'check', 'circle-info', 'triangle-exclamation', 'euro-sign', 'tag',
                   'cart-shopping', 'phone', 'location-dot', 'clock', 'calendar-days', 'envelope', 'gift',
                   'fire', 'leaf', 'thumbs-up', 'bell', 'truck', 'percent', 'crown', 'bolt', 'snowflake',
                   'sun', 'mug-hot', 'utensils', 'basket-shopping', 'wine-glass', 'cheese', 'apple-whole']

# ─── PLUS-SJABLONEN ("We doen met je mee"-social posts, natief nagemaakt) ──────
# De campagne "vers bij [naam] vandaan" (social portret 4:5). Winkels vervangen [naam]
# door hun winkelnaam en slepen een eigen foto in het fotovlak.
_DESIGNER_TPL_SOCIAL = [
    ('vers-lokaal', 'Vers lokaal (We doen met je mee)', 'Onze [product] komen vers bij [naam] vandaan!'),
]
_DESIGNER_TPL_W_MM, _DESIGNER_TPL_H_MM = 108.0, 135.0   # 4:5 social portret

def _designer_template_pages(headline):
    """Elementen (fracties van het blad) voor een 'We doen met je mee'-sjabloon."""
    return {'bg': '#ffffff', 'elements': [
        {'type': 'text', 'x': 0.06, 'y': 0.05, 'w': 0.88, 'h': 0.30,
         'text': headline, 'color': '#80bd1d', 'bold': True, 'size': 0.072, 'align': 'left',
         'font': 'montserrat', 'valign': 'top', 'lineh': 1.05, 'autofit': True},
        {'type': 'shape', 'shape': 'rect', 'x': 0.06, 'y': 0.37, 'w': 0.88, 'h': 0.40,
         'fill': '#f4f5f3', 'radius': 0.02},
        {'type': 'text', 'x': 0.06, 'y': 0.55, 'w': 0.88, 'h': 0.06,
         'text': 'Sleep hier je foto', 'color': '#b7bdb0', 'size': 0.026, 'align': 'center'},
        {'type': 'shape', 'shape': 'rect', 'x': 0.0, 'y': 0.82, 'w': 1.0, 'h': 0.18, 'fill': '#80bd1d'},
        {'type': 'text', 'x': 0.08, 'y': 0.845, 'w': 0.84, 'h': 0.06,
         'text': 'We doen met je mee.', 'color': '#ffffff', 'bold': True, 'size': 0.042, 'align': 'center'},
        {'type': 'image', 'static': 'plus-logo-wit.svg', 'src': '/static/img/plus-logo-wit.svg',
         'x': 0.30, 'y': 0.935, 'w': 0.20, 'h': 0.042, 'fit': 'contain'},
        {'type': 'text', 'x': 0.515, 'y': 0.925, 'w': 0.40, 'h': 0.06,
         'text': 'winkelnaam', 'color': '#ffffff', 'bold': True, 'size': 0.028, 'align': 'left',
         'valign': 'middle', 'autofit': True},
    ]}

def _designer_template(tid):
    for id_, name, headline in _DESIGNER_TPL_SOCIAL:
        if id_ == tid:
            return {'id': id_, 'name': name, 'headline': headline,
                    'w_mm': _DESIGNER_TPL_W_MM, 'h_mm': _DESIGNER_TPL_H_MM,
                    'data': {'w_mm': _DESIGNER_TPL_W_MM, 'h_mm': _DESIGNER_TPL_H_MM,
                             'pages': [_designer_template_pages(headline)]}}
    return None

def _designer_font(font_id, bold, size_px):
    from PIL import ImageFont
    fam = _DESIGNER_FONTS.get(font_id) or _DESIGNER_FONTS['montserrat']
    rel = fam[1] if bold else fam[0]
    path = os.path.join(os.path.dirname(__file__), rel)
    try:
        f = ImageFont.truetype(path, max(6, int(round(size_px))))
        if font_id == 'montserrat':
            try:
                f.set_variation_by_axes([700 if bold else 400])
            except Exception:
                pass
        return f
    except Exception:
        return ImageFont.load_default()

def _designer_wrap(draw, text, font, maxw):
    """Woordwrap (met behoud van expliciete regels) binnen een breedte, voor tabel-cellen/tekst."""
    out = []
    for para in str(text).split('\n'):
        words = para.split(' '); cur = ''
        for w in words:
            trial = (cur + ' ' + w).strip()
            if draw.textlength(trial, font=font) <= maxw or not cur:
                cur = trial
            else:
                out.append(cur); cur = w
        out.append(cur)
    return out

def _designer_decode_img(src):
    """Decodeer een data-URL (of doorgezette base64) naar een RGBA PIL-afbeelding."""
    from PIL import Image
    import base64, io
    if not src:
        return None
    try:
        if src.startswith('data:'):
            src = src.split(',', 1)[1]
        raw = base64.b64decode(src)
        return Image.open(io.BytesIO(raw)).convert('RGBA')
    except Exception:
        return None

_designer_static_cache = {}
def _designer_static_img(name, want_w=600):
    """Laad een eigen asset uit static/img (SVG via cairosvg, anders PIL). Alleen een bestandsnaam,
    geen paden - SSRF/traversal-veilig. Voor sjabloon-elementen zoals het PLUS-logo."""
    from PIL import Image
    import io, os as _os
    base = _os.path.basename(str(name or ''))
    if not base or base != str(name):
        return None
    key = (base, int(want_w))
    if key in _designer_static_cache:
        return _designer_static_cache[key]
    path = _os.path.join(_os.path.dirname(__file__), 'static', 'img', base)
    im = None
    try:
        if base.lower().endswith('.svg'):
            import cairosvg
            png = cairosvg.svg2png(url=path, output_width=max(16, int(want_w)))
            im = Image.open(io.BytesIO(png)).convert('RGBA')
        else:
            im = Image.open(path).convert('RGBA')
    except Exception:
        im = None
    _designer_static_cache[key] = im
    return im

def _designer_draw_el(canvas, el, W, H, dpi):
    from PIL import Image, ImageDraw
    t = el.get('type')
    ew = max(1, int(round(float(el.get('w', .2)) * W)))
    eh = max(1, int(round(float(el.get('h', .1)) * H)))
    cx = float(el.get('x', 0)) * W + ew / 2.0
    cy = float(el.get('y', 0)) * H + eh / 2.0
    rot = float(el.get('rot', 0) or 0)
    layer = Image.new('RGBA', (ew, eh), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    if t == 'text':
        txt = str(el.get('text', ''))
        col = el.get('color', '#231f20')
        bold = bool(el.get('bold'))
        align = el.get('align', 'left')
        fontid = el.get('font', 'montserrat')
        lineh_f = float(el.get('lineh', 1.15))
        # tekstgrootte = fractie van canvas-hoogte → px (zo schaalt editor↔render 1:1)
        size_px = float(el.get('size', .05)) * H

        def _wrap_lines(fnt):
            out = []
            for para in txt.split('\n'):
                words = para.split(' '); cur = ''
                for w in words:
                    trial = (cur + ' ' + w).strip()
                    if d.textlength(trial, font=fnt) <= ew or not cur:
                        cur = trial
                    else:
                        out.append(cur); cur = w
                out.append(cur)
            return out

        font = _designer_font(fontid, bold, size_px)
        lines = _wrap_lines(font)
        # autofit: krimp de tekst tot 'ie binnen het vak past (breedte + hoogte). Handig voor bv. de
        # winkelnaam of een lange kop, zodat elke lengte netjes past.
        if el.get('autofit') and txt.strip():
            for _ in range(24):
                lines = _wrap_lines(font)
                widest = max((d.textlength(ln, font=font) for ln in lines), default=0)
                total_h = size_px * lineh_f * len(lines)
                if widest <= ew and total_h <= eh:
                    break
                size_px *= 0.92
                if size_px < 6:
                    break
                font = _designer_font(fontid, bold, size_px)
            lines = _wrap_lines(font)
        lh = size_px * lineh_f
        total = lh * len(lines)
        y = max(0, (eh - total) / 2.0) if el.get('valign', 'middle') == 'middle' else 0
        for ln in lines:
            lw = d.textlength(ln, font=font)
            x = 0 if align == 'left' else (ew - lw if align == 'right' else (ew - lw) / 2.0)
            d.text((x, y), ln, font=font, fill=col)
            y += lh

    elif t in ('image', 'icon'):
        im = None
        if el.get('static'):                           # eigen asset uit static/img (bv. PLUS-logo in sjablonen)
            im = _designer_static_img(el['static'], want_w=max(ew, eh))
        if im is None and el.get('url') and not el.get('src'):  # plus.nl-productfoto → server-side ophalen
            try:
                im = _fetch_overlay(el['url'], want_w=max(ew, eh))
            except Exception:
                im = None
        if im is None:
            im = _designer_decode_img(el.get('src'))
        if im is not None:
            fit = el.get('fit', 'contain')
            if fit == 'fill':
                im = im.resize((ew, eh))
            else:
                r = (max(ew / im.width, eh / im.height) if fit == 'cover'
                     else min(ew / im.width, eh / im.height))
                nw, nh = max(1, int(im.width * r)), max(1, int(im.height * r))
                im = im.resize((nw, nh))
                ox, oy = (ew - nw) // 2, (eh - nh) // 2
                base = Image.new('RGBA', (ew, eh), (0, 0, 0, 0))
                base.paste(im, (ox, oy), im)
                im = base
            layer.alpha_composite(im)

    elif t == 'barcode':
        bc = _labelimage._barcode_image(el.get('value', ''), target_w=ew, dpi=dpi,
                                        module_height=max(3.0, eh / (dpi / 25.4) * 0.7))
        if bc is not None:
            bc = bc.convert('RGBA')
            # zwart op transparant
            bc = bc.resize((ew, min(eh, bc.height)))
            oy = (eh - bc.height) // 2
            layer.alpha_composite(bc, (0, max(0, oy)))
        if el.get('showText') and el.get('value'):
            f = _designer_font('montserrat', False, max(8, eh * 0.16))
            s = str(el.get('value'))
            lw = d.textlength(s, font=f)
            d.text(((ew - lw) / 2.0, eh - eh * 0.18), s, font=f, fill='#000000')

    elif t == 'shape':
        shp = el.get('shape', 'rect')
        fill = el.get('fill') if el.get('fill') not in (None, '', 'none') else None
        stroke = el.get('stroke') if el.get('stroke') not in (None, '', 'none') else None
        sw = max(0, int(round(float(el.get('strokeW', 0)) * (dpi / 96.0))))
        m = sw // 2
        if shp == 'ellipse':
            d.ellipse([m, m, ew-1-m, eh-1-m], fill=fill, outline=stroke, width=sw or 1)
        elif shp == 'line':
            d.line([0, eh//2, ew, eh//2], fill=stroke or fill or '#231f20', width=max(1, sw or int(eh*0.1)))
        elif shp == 'arrow':
            col = stroke or fill or '#231f20'
            th = max(1, sw or int(eh * 0.14)); cyl = eh // 2; hd = min(ew * 0.4, eh * 0.5)
            d.line([0, cyl, ew - hd, cyl], fill=col, width=th)
            d.polygon([(ew, cyl), (ew - hd, cyl - eh * 0.4), (ew - hd, cyl + eh * 0.4)], fill=col)
        elif shp == 'triangle':
            d.polygon([(ew/2, m), (ew-1-m, eh-1-m), (m, eh-1-m)], fill=fill, outline=stroke)
        elif shp == 'star':
            import math
            cxp, cyp, ro = ew/2, eh/2, min(ew, eh)/2 - m; ri = ro * 0.42; pts = []
            for i in range(10):
                r = ro if i % 2 == 0 else ri; a = math.pi/2 + i * math.pi/5
                pts.append((cxp + r*math.cos(a), cyp - r*math.sin(a)))
            d.polygon(pts, fill=fill, outline=stroke)
        else:
            d.rounded_rectangle([m, m, ew-1-m, eh-1-m],
                                radius=int(el.get('radius', 0) * min(ew, eh)),
                                fill=fill, outline=stroke, width=sw or 1)

    elif t == 'table':
        rows = max(1, int(el.get('rows', 1))); cols = max(1, int(el.get('cols', 1)))
        cells = el.get('cells') or []
        border = el.get('border', '#d8d8d8'); hbg = el.get('headerBg', '#eef6e1')
        col = el.get('color', '#231f20'); header = bool(el.get('header'))
        fs = float(el.get('size', .026)) * H
        font = _designer_font('montserrat', False, fs); fontB = _designer_font('montserrat', True, fs)
        cw = ew / cols; rh = eh / rows; lw = max(1, int(round(dpi / 96)))
        for r in range(rows):
            for c in range(cols):
                x0, y0 = c * cw, r * rh
                if header and r == 0:
                    d.rectangle([x0, y0, x0 + cw, y0 + rh], fill=hbg)
                d.rectangle([x0, y0, x0 + cw, y0 + rh], outline=border, width=lw)
                try:
                    txt = str(cells[r][c])
                except Exception:
                    txt = ''
                if txt:
                    fnt = fontB if (header and r == 0) else font
                    lines = _designer_wrap(d, txt, fnt, cw - 8)
                    ty = y0 + max(0, (rh - fs * 1.15 * len(lines)) / 2)
                    for ln in lines:
                        d.text((x0 + 5, ty), ln, font=fnt, fill=col); ty += fs * 1.15

    op = float(el.get('op', 1) or 1)
    if op < 1:
        a = layer.split()[3].point(lambda v: int(v * op))
        layer.putalpha(a)
    if rot:
        layer = layer.rotate(-rot, expand=True, resample=Image.BICUBIC)
    lw, lh2 = layer.size
    canvas.paste(layer, (int(round(cx - lw / 2.0)), int(round(cy - lh2 / 2.0))), layer)

def _designer_pages(design):
    """Geef de pagina's van een ontwerp terug; ondersteunt zowel het nieuwe multi-page-formaat
    ({pages:[{bg,elements}]}) als het oude enkel-pagina-formaat ({bg,elements})."""
    try:
        data = json.loads(design.data_json or '{}')
    except Exception:
        data = {}
    if isinstance(data.get('pages'), list) and data['pages']:
        return data['pages']
    return [{'bg': data.get('bg', '#ffffff'), 'elements': data.get('elements', [])}]

def _designer_render(design, dpi=200, page=0):
    from PIL import Image
    W = max(1, int(round(design.w_mm * dpi / 25.4)))
    H = max(1, int(round(design.h_mm * dpi / 25.4)))
    pages = _designer_pages(design)
    pg = pages[page] if 0 <= page < len(pages) else pages[0]
    canvas = Image.new('RGB', (W, H), pg.get('bg') or '#ffffff')
    for el in (pg.get('elements') or []):
        try:
            _designer_draw_el(canvas, el, W, H, dpi)
        except Exception:
            pass
    return canvas

def _designer_get(design_id):
    u = get_current_user()
    des = Design.query.get_or_404(design_id)
    if not is_superadmin(u) and des.filiaal and des.filiaal != u.filiaal:
        abort(403)
    return u, des

@app.route('/designer')
@login_required
def designer_dashboard():
    u = get_current_user()
    fil = _active_filiaal() or u.filiaal
    q = Design.query
    if not is_superadmin(u):
        q = q.filter(Design.filiaal == fil)
    elif fil:
        q = q.filter(Design.filiaal == fil)
    designs = q.order_by(Design.updated_at.desc()).all()
    return render_template('designer_dashboard.html', designs=designs)

@app.route('/designer/nieuw', methods=['GET', 'POST'])
@login_required
def designer_new():
    u = get_current_user()
    if request.method == 'POST':
        kind = request.form.get('kind', 'paper')
        title = (request.form.get('title') or 'Naamloos ontwerp').strip()
        orient = request.form.get('orientation', 'portrait')
        if kind == 'label':
            fmt = LabelFormat.query.get(int(request.form.get('label_format') or 0))
            w_mm, h_mm = (fmt.width_mm, fmt.height_mm) if fmt else (45.0, 40.0)
        else:
            try:
                idx = int(request.form.get('paper_format', 2))
                _, w_mm, h_mm = _DESIGNER_PAPER[idx]
            except Exception:
                w_mm, h_mm = 210.0, 297.0
            if orient == 'landscape':
                w_mm, h_mm = h_mm, w_mm
        des = Design(title=title, kind=kind, w_mm=w_mm, h_mm=h_mm,
                     data_json='{"bg":"#ffffff","elements":[]}',
                     username=u.username, filiaal=(_active_filiaal() or u.filiaal))
        db.session.add(des); db.session.commit()
        return redirect(url_for('designer_editor', design_id=des.id))
    formats = LabelFormat.query.filter(
        (LabelFormat.filiaal == None) | (LabelFormat.filiaal == (_active_filiaal() or u.filiaal))
    ).order_by(LabelFormat.name).all()
    return render_template('designer_new.html', label_formats=formats, paper=_DESIGNER_PAPER,
                           social_templates=_DESIGNER_TPL_SOCIAL)

@app.route('/designer/sjabloon/<tid>.png')
@login_required
def designer_template_thumb(tid):
    import io, types
    tpl = _designer_template(tid)
    if not tpl:
        abort(404)
    des = types.SimpleNamespace(w_mm=tpl['w_mm'], h_mm=tpl['h_mm'], data_json=json.dumps(tpl['data']))
    img = _designer_render(des, dpi=90, page=0)
    bio = io.BytesIO(); img.save(bio, 'PNG'); bio.seek(0)
    return send_file(bio, mimetype='image/png')

@app.route('/designer/sjabloon/<tid>/gebruik')
@login_required
def designer_template_use(tid):
    u = get_current_user()
    tpl = _designer_template(tid)
    if not tpl:
        flash('Sjabloon niet gevonden.', 'error'); return redirect(url_for('designer_new'))
    des = Design(title=tpl['name'], kind='social', w_mm=tpl['w_mm'], h_mm=tpl['h_mm'],
                 data_json=json.dumps(tpl['data']),
                 username=u.username, filiaal=(_active_filiaal() or u.filiaal))
    db.session.add(des); db.session.commit()
    return redirect(url_for('designer_editor', design_id=des.id))

@app.route('/designer/<int:design_id>')
@login_required
def designer_editor(design_id):
    u, des = _designer_get(design_id)
    fonts = [(k, v[2]) for k, v in _DESIGNER_FONTS.items()]
    # Nieuwe Fabric.js-editor via ?fabric=1 (opt-in tijdens de migratie); anders de klassieke.
    engine = 'fabric' if request.args.get('fabric') == '1' else 'classic'
    return render_template('designer_editor.html', design=des, fonts=fonts,
                           icons=_DESIGNER_ICONS, engine=engine)

@app.route('/designer/<int:design_id>/opslaan', methods=['POST'])
@login_required
def designer_save(design_id):
    u, des = _designer_get(design_id)
    payload = request.get_json(silent=True) or {}
    if 'title' in payload:
        des.title = (payload.get('title') or 'Naamloos ontwerp').strip()[:200]
    if 'data' in payload:
        des.data_json = json.dumps(payload['data'])[:4_000_000]
    thumb = payload.get('thumb')
    if thumb and thumb.startswith('data:image'):
        try:
            import base64
            fn = f'design_{des.id}.png'
            with open(os.path.join(app.static_folder, 'export', fn), 'wb') as fh:
                fh.write(base64.b64decode(thumb.split(',', 1)[1]))
            des.thumb = fn
        except Exception:
            pass
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/designer/<int:design_id>/verwijder', methods=['POST'])
@login_required
def designer_delete(design_id):
    u, des = _designer_get(design_id)
    try:
        if des.thumb:
            os.remove(os.path.join(app.static_folder, 'export', des.thumb))
    except Exception:
        pass
    db.session.delete(des); db.session.commit()
    flash('Ontwerp verwijderd.', 'success')
    return redirect(url_for('designer_dashboard'))

@app.route('/designer/barcode.png')
@login_required
def designer_barcode():
    import io
    val = request.args.get('value', '') or '0000000000000'
    show = request.args.get('showtext') == '1'
    bc = _labelimage._barcode_image(val, target_w=600, dpi=200, module_height=12.0)
    from PIL import Image, ImageDraw
    if bc is None:
        img = Image.new('RGB', (600, 120), '#ffffff')
        ImageDraw.Draw(img).text((10, 50), 'ongeldige code', fill='#999999')
    else:
        img = bc.convert('RGB')
        if show:
            f = _designer_font('montserrat', False, 26)
            base = Image.new('RGB', (img.width, img.height + 34), '#ffffff')
            base.paste(img, (0, 0)); d = ImageDraw.Draw(base)
            lw = d.textlength(val, font=f); d.text(((img.width - lw) / 2, img.height + 2), val, font=f, fill='#000')
            img = base
    bio = io.BytesIO(); img.save(bio, 'PNG'); bio.seek(0)
    return Response(bio.getvalue(), mimetype='image/png')

@app.route('/designer/<int:design_id>/preview.png')
@login_required
def designer_preview(design_id):
    import io
    u, des = _designer_get(design_id)
    dpi = int(request.args.get('dpi', 150))
    page = int(request.args.get('page', 0))
    img = _designer_render(des, dpi=min(300, max(72, dpi)), page=page)
    bio = io.BytesIO(); img.save(bio, 'PNG'); bio.seek(0)
    headers = {}
    if request.args.get('dl') == '1':                      # als download (voor social posts)
        safe = re.sub(r'[^A-Za-z0-9_-]+', '_', des.title or 'ontwerp')[:40] or 'ontwerp'
        headers['Content-Disposition'] = f'attachment; filename="{safe}.png"'
    return Response(bio.getvalue(), mimetype='image/png', headers=headers)

def _designer_png_from_dataurl(durl):
    """Decodeer een 'data:image/png;base64,...'-string naar ruwe bytes (of None)."""
    import base64
    try:
        if durl and durl.startswith('data:image'):
            return base64.b64decode(durl.split(',', 1)[1])
    except Exception:
        pass
    return None

@app.route('/designer/<int:design_id>/pdf', methods=['GET', 'POST'])
@login_required
def designer_pdf(design_id):
    import io, fitz
    u, des = _designer_get(design_id)
    doc = fitz.open()
    pw, ph = des.w_mm * 72 / 25.4, des.h_mm * 72 / 25.4   # punten
    # Nieuwe (Fabric-)editor stuurt de client-gerenderde PNG's mee → 1:1 met wat je ziet.
    client_pngs = (request.get_json(silent=True) or {}).get('pages') if request.method == 'POST' else None
    if client_pngs:
        for durl in client_pngs:
            raw = _designer_png_from_dataurl(durl)
            if not raw:
                continue
            page = doc.new_page(width=pw, height=ph)
            page.insert_image(fitz.Rect(0, 0, pw, ph), stream=raw)
    else:                                                  # fallback: server-render (oude ontwerpen)
        for i in range(len(_designer_pages(des))):
            img = _designer_render(des, dpi=300, page=i)
            pbio = io.BytesIO(); img.save(pbio, 'PNG'); pbio.seek(0)
            page = doc.new_page(width=pw, height=ph)
            page.insert_image(fitz.Rect(0, 0, pw, ph), stream=pbio.getvalue())
    if doc.page_count == 0:
        doc.new_page(width=pw, height=ph)
    out = doc.tobytes(); doc.close()
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', des.title or 'ontwerp')[:40] or 'ontwerp'
    return Response(out, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{safe}.pdf"'})

@app.route('/designer/<int:design_id>/print-label', methods=['POST'])
@login_required
def designer_print_label(design_id):
    u, des = _designer_get(design_id)
    f = Filiaal.query.filter_by(nummer=(des.filiaal or u.filiaal)).first()
    if not f or not (f.printer_ip or _agent_online(f)):
        return jsonify({'error': 'Voor deze winkel is geen labelprinter ingesteld (Beheer → Filialen).'}), 400
    if getattr(u, 'access_policy', 'anywhere') == 'ip_print':
        cip = client_ip()
        if not ip_in_list(cip, f.allowed_ips or ''):
            return jsonify({'error': f'Printen kan alleen vanaf het winkelnetwerk. Jouw IP: {cip}.'}), 403
    dpi = int(f.printer_dpi or 300)
    body = request.get_json(silent=True) or {}
    raw = _designer_png_from_dataurl(body.get('png'))
    if raw:                                                # client-gerenderd (Fabric) → 1:1
        import io
        img = Image.open(io.BytesIO(raw)).convert('L').point(lambda p: 0 if p < 128 else 1, mode='1')
    else:                                                  # fallback: server-render
        img = _designer_render(des, dpi=dpi).convert('L').point(lambda p: 0 if p < 128 else 1, mode='1')
    qty = int(body.get('copies', 1) or 1)
    payload = _labelimage.image_to_tspl(img, des.w_mm, des.h_mm, dpi=dpi, copies=max(1, qty))
    try:
        _send_label(f, payload)
    except OSError as e:
        return jsonify({'error': f'Kon niet naar de printer sturen: {e}'}), 502
    log_action('designer_print_label', f'ontwerp {des.id} ({des.title})', filiaal=f.nummer)
    return jsonify({'ok': True})

@app.route('/labels')
@login_required
def labels_dashboard():
    u = get_current_user()
    if not can(u, 'labels_make'):
        flash('Je hebt geen toegang tot Labels.', 'error'); return redirect(url_for('dashboard'))
    fil = _label_filiaal()
    q = LabelJob.query
    if fil is not None:
        q = q.filter_by(filiaal=fil)
    jobs = q.order_by(LabelJob.created_at.desc()).limit(50).all()
    filialen = Filiaal.query.order_by(Filiaal.nummer).all() if is_superadmin(u) else []
    return render_template('labels_dashboard.html', user=u, jobs=jobs,
                           filialen=filialen, sel_filiaal=fil)

@app.route('/labels/nieuw', methods=['GET', 'POST'])
@login_required
def label_new():
    u = get_current_user()
    if not can(u, 'labels_make'):
        flash('Je hebt geen toegang tot Labels.', 'error'); return redirect(url_for('dashboard'))
    fil = _label_filiaal()
    if fil is None:
        fil = u.filiaal
    # Bewerken: bestaande batch heropenen
    edit_job = None
    eid = request.args.get('edit', type=int)
    if eid:
        ej = LabelJob.query.get(eid)
        if ej and (is_superadmin(u) or ej.filiaal == u.filiaal):
            edit_job = ej; fil = ej.filiaal
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
        if not items:
            return jsonify({'error': 'Geen producten toegevoegd.'}), 400
        Lw = _num(data.get('width')); Lh = _num(data.get('height'))
        if not Lw or not Lh:
            Lw, Lh = _label_store_w_h(fil)
        edit_id = data.get('edit_id')
        job = None
        if edit_id:
            job = LabelJob.query.get(edit_id)
            if not job or (not is_superadmin(u) and job.filiaal != u.filiaal):
                return jsonify({'error': 'Geen toegang tot deze batch.'}), 403
            fil = job.filiaal
        if job:
            job.name = data.get('name') or job.name
            job.items_json = json.dumps(items)
            job.price_unit = data.get('price_unit') or 'stuk'
            job.extra_line1 = data.get('extra_line1') or ''; job.extra_line2 = data.get('extra_line2') or ''
            job.show_date = bool(data.get('show_date')); job.show_logo = bool(data.get('show_logo'))
        else:
            job = LabelJob(
                filiaal=fil, created_by=u.username,
                name=data.get('name') or f'Labels {datetime.now().strftime("%d-%m-%Y %H:%M")}',
                items_json=json.dumps(items), status='concept',
                price_unit=data.get('price_unit') or 'stuk',
                extra_line1=data.get('extra_line1') or '', extra_line2=data.get('extra_line2') or '',
                show_date=bool(data.get('show_date')), show_logo=bool(data.get('show_logo')),
            )
            db.session.add(job)
        db.session.commit()
        # onbekende producten aan de catalogus toevoegen
        for it in items:
            bc = str(it.get('barcode') or '').strip()
            nm = str(it.get('name') or '').strip()
            if nm and bc and not Product.query.filter_by(filiaal=fil, barcode=bc).first():
                db.session.add(Product(filiaal=fil, name=nm, barcode=bc,
                                       barcode_type=it.get('barcode_type') or 'ean13',
                                       price=_num(it.get('price'))))
        db.session.commit()
        log_action('label_bijgewerkt' if edit_id else 'label_aangemaakt',
                   f'"{job.name}" ({len(items)} labels)', filiaal=fil)
        return jsonify({'success': True, 'job_id': job.id})
    Lw, Lh = _label_store_w_h(fil)
    products = Product.query.filter_by(filiaal=fil, active=True).order_by(Product.name).limit(500).all()
    edit_data = None
    if edit_job:
        edit_data = {'id': edit_job.id, 'name': edit_job.name,
                     'items': json.loads(edit_job.items_json or '[]'),
                     'price_unit': edit_job.price_unit, 'extra_line1': edit_job.extra_line1 or '',
                     'extra_line2': edit_job.extra_line2 or '', 'show_date': edit_job.show_date,
                     'show_logo': edit_job.show_logo}
    return render_template('label_builder.html', user=u, filiaal=fil,
                           def_w=Lw, def_h=Lh, products=products, edit_data=edit_data)

@app.route('/labels/<int:job_id>/verwijder', methods=['POST'])
@login_required
def label_delete(job_id):
    u = get_current_user()
    job = LabelJob.query.get_or_404(job_id)
    if not is_superadmin(u) and job.filiaal != u.filiaal:
        abort(403)
    nm = job.name
    db.session.delete(job); db.session.commit()
    log_action('label_verwijderd', f'"{nm}"', filiaal=job.filiaal)
    flash('Labelbatch verwijderd.', 'success')
    return redirect(url_for('labels_dashboard'))

@app.route('/labels/<int:job_id>')
@login_required
def label_view(job_id):
    u = get_current_user()
    job = LabelJob.query.get_or_404(job_id)
    if not is_superadmin(u) and job.filiaal != u.filiaal:
        abort(403)
    items = [it for it in json.loads(job.items_json or '[]')
             if str(it.get('name') or '').strip() or str(it.get('barcode') or '').strip()]
    f = Filiaal.query.filter_by(nummer=job.filiaal).first()
    Lw, Lh = _label_store_w_h(job.filiaal)
    return render_template('label_view.html', user=u, job=job, items=items, fil=f, Lw=Lw, Lh=Lh)

@app.route('/labels/preview.png')
@login_required
def label_preview_live():
    """Live voorbeeld van één label (canoniek ontwerp) uit query-params."""
    item = {'name': request.args.get('name', ''),
            'barcode': request.args.get('barcode', ''),
            'price': _num(request.args.get('price')),
            'old_price': _num(request.args.get('old_price')),
            'uc_code': (request.args.get('uc', '') or '').strip().upper()}
    opts = {'price_unit': request.args.get('unit', 'stuk'),
            'extra_line1': request.args.get('extra1', ''),
            'extra_line2': request.args.get('extra2', ''),
            'show_date': request.args.get('show_date') == '1',
            'today': datetime.now().strftime('%d-%m-%Y')}
    Lw = _num(request.args.get('w')) or 45.0
    Lh = _num(request.args.get('h')) or 40.0
    show_logo = request.args.get('show_logo') == '1'
    from flask import send_file
    try:
        bio = _render_label_png(item, opts, Lw, Lh, dpi=120, show_logo=show_logo)
        return send_file(bio, mimetype='image/png')
    except Exception:
        abort(404)

@app.route('/labels/<int:job_id>/voorbeeld.png')
@login_required
def label_job_preview(job_id):
    u = get_current_user()
    job = LabelJob.query.get_or_404(job_id)
    if not is_superadmin(u) and job.filiaal != u.filiaal:
        abort(403)
    items = json.loads(job.items_json or '[]')
    if not items:
        abort(404)
    idx = request.args.get('i', 0, type=int)
    idx = max(0, min(idx, len(items) - 1))
    Lw, Lh = _label_store_w_h(job.filiaal)
    opts = {'price_unit': job.price_unit or 'stuk', 'extra_line1': job.extra_line1,
            'extra_line2': job.extra_line2, 'show_date': job.show_date,
            'today': datetime.now().strftime('%d-%m-%Y')}
    from flask import send_file
    try:
        bio = _render_label_png(items[idx], opts, Lw, Lh, dpi=120, show_logo=job.show_logo)
        return send_file(bio, mimetype='image/png')
    except Exception:
        abort(404)

_PKG_SINGULAR = {'zakje': 'zak', 'flesje': 'fles', 'blikje': 'blik', 'pakje': 'pak', 'bakje': 'bak',
                 'bosje': 'bos', 'doosje': 'doos', 'potje': 'pot', 'stuks': 'stuk', 'plakken': 'plak',
                 'kuipjes': 'kuipje', 'rollen': 'rol', 'zakjes': 'zak', 'flessen': 'fles'}
_PKG_WORDS = ['flessen', 'flesje', 'fles', 'blikje', 'blik', 'pakje', 'pak', 'zakjes', 'zakje', 'zak',
              'bakje', 'bak', 'bosje', 'bos', 'doosje', 'doos', 'potje', 'pot', 'kuipjes', 'kuipje',
              'krat', 'tray', 'rollen', 'rol', 'net', 'bundel', 'stuks', 'stuk', 'plakken', 'plak', 'beker']

def _plus_pack_weight(href, unit):
    """Splits de plus.nl-info in (verpakking, gewicht). De tegel geeft alleen gewicht ('Per 175 g');
    de verpakking (zak/stuk/fles/tray/…) staat in de product-URL. Verpakking → het groene 'Per …'-label
    (altijd gevuld: 'Per ?' als onbekend). Gewicht → de subtekst ('175 g')."""
    ul = (unit or '').lower()
    slug = (href or '').lower()
    weight = ''
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(kg|gram|g|liter|l|ml|cl)\b', ul)
    if m:
        un = {'gram': 'g', 'liter': 'l'}.get(m.group(2), m.group(2))
        weight = f'{m.group(1)} {un}'
    pkg = ''
    for w in _PKG_WORDS:            # 1) uit de eenheid-tekst zelf (bv. 'Per stuk')
        if re.search(r'\b' + w + r'\b', ul):
            pkg = _PKG_SINGULAR.get(w, w); break
    if not pkg:
        for w in _PKG_WORDS:        # 2) anders uit de product-URL-slug (bv. '…-zak-175-g')
            if re.search(r'-' + w + r'(?:-|$)', slug):
                pkg = _PKG_SINGULAR.get(w, w); break
    if pkg:
        verp = 'Per ' + pkg
    else:
        verp = _weight_perlabel(weight) or 'Per ?'   # geen verpakking → val terug op gewicht
    return verp, weight

def _weight_perlabel(weight):
    """Maak van een gewicht een 'Per …'-label: '600 g' → 'Per 600 gram', 1000 g / 1 kg → 'Per kilo',
    1 l → 'Per liter'. Geeft None als er geen gewicht is."""
    m = re.match(r'([\d.,]+)\s*(kg|g|l|ml|cl)$', (weight or '').strip())
    if not m:
        return None
    num, unit = m.group(1), m.group(2)
    try:
        val = float(num.replace(',', '.'))
    except ValueError:
        val = None
    if unit == 'g':
        return 'Per kilo' if val == 1000 else f'Per {num} gram'
    if unit == 'kg':
        return 'Per kilo' if val == 1 else f'Per {num} kilo'
    if unit == 'l':
        return 'Per liter' if val == 1 else f'Per {num} liter'
    return f'Per {num} {unit}'

# ─── MERK AFLEIDEN UIT DE PRODUCTNAAM (plus.nl geeft het merk niet apart) ──────
# plus.nl stelt het merk nergens los beschikbaar, dus leiden we 't uit de naam af. De naam begint bijna
# altijd met het merk, maar dat is niet altijd het eerste woord ("Biologisch PLUS") of één woord
# ("PLUS Korenlanders"). Daarom: (1) leidende kwalificaties overslaan, (2) langst passende bekende merk
# aan het begin pakken, (3) anders het eerste woord. De gebruiker kan een uitzondering in de preview
# aanpassen (alles is daar bewerkbaar).
#
# ▶ MERKENLIJST UITBREIDEN: zet een merk hieronder in `_MERKEN` (meerwoordig mag; hoofdletters zoals je 't
#   getoond wilt hebben). Meerwoordige/PLUS-lijnen zijn het belangrijkst - enkelwoord-merken worden meestal
#   al goed door de terugval (eerste woord) gepakt. Langste match wint automatisch.
_MERKEN = [
    # PLUS-eigenmerk-lijnen
    'PLUS Korenlanders', 'PLUS Biologisch', 'PLUS Bewuste Keuze', 'PLUS Wereldkeuken',
    'PLUS Kids', 'PLUS Basic', 'PLUS Select', 'PLUS',
    # meerwoordige A-merken (waar 'eerste woord' tekortschiet)
    'Douwe Egberts', 'Ben & Jerry’s', 'Grand’Italia', 'Old Amsterdam', 'De Ruijter',
    'Dr. Oetker', 'Uncle Ben’s', 'La Place', 'Karvan Cévitam', 'Red Bull', 'Milk & Fruit',
    'Coca-Cola', 'Iglo', 'Hak', 'Honig', 'Knorr', 'Unox', 'Calvé', 'Bertolli', 'Conimex',
    'Senseo', 'Nespresso', 'Pickwick', 'Lipton', 'Chocomel', 'Roosvicee', 'Optimel', 'Almhof',
    'Campina', 'Milner', 'Alpro', 'Danone', 'Lay’s', 'Croky', 'Doritos', 'Bifi', 'Mora',
]
_MERK_KWALIFICATIES = {'biologisch', 'bio', 'vers', 'verse', 'nieuw', 'ambachtelijk', 'echte', 'echt'}
_MERKEN_SORTED = sorted(_MERKEN, key=len, reverse=True)

def _plus_merk(name):
    """Geef (merk, koptekst) terug, afgeleid uit de productnaam."""
    n = (name or '').strip()
    if not n:
        return '', ''
    words = n.split()
    i = 0
    while i < len(words) and words[i].lower().strip('.,') in _MERK_KWALIFICATIES:
        i += 1
    qual = words[:i]                 # kwalificaties vóór het merk (blijven in de koptekst)
    rest_words = words[i:] or words
    rest = ' '.join(rest_words)
    rlow = rest.lower()
    merk, kop_rest = '', ''
    for brand in _MERKEN_SORTED:
        bl = brand.lower()
        if rlow == bl or rlow.startswith(bl + ' '):
            merk = rest[:len(brand)]
            kop_rest = rest[len(brand):].strip()
            break
    if not merk:                     # terugval: eerste woord (na kwalificaties) = merk
        merk = rest_words[0]
        kop_rest = ' '.join(rest_words[1:]).strip()
    kop = ' '.join(qual + ([kop_rest] if kop_rest else [])).strip()
    return merk, (kop or merk)


def _norm_plus(r):
    all_ps = sorted({float(x) for x in (r.get('prices') or []) if x})
    dtxt = (r.get('deal') or '')
    # 'X gram voor €Y' is een REFERENTIEprijs per gewicht (bv. 500 gram voor 4.99), niet de pakprijs.
    # Haal die uit de prijzen zodat de echte pakprijs (bv. 5.98 voor een 600 g-pak) overblijft.
    mg = re.match(r'(\d+)\s*gram\s*voor\s*(\d+\.\d{2})', dtxt, re.I)
    ref = float(mg.group(2)) if mg else None
    ps = [p for p in all_ps if p != ref] if ref is not None else all_ps
    if len(ps) < 2:                 # te weinig echte pakprijzen over → toch alle prijzen gebruiken
        ps = all_ps
    prijs = actie = van = None
    if len(ps) >= 2:
        actie, van = ps[0], ps[-1]
    elif len(ps) == 1:
        prijs = ps[0]
    verp, weight = _plus_pack_weight(r.get('href', ''), r.get('unit', ''))
    # Kaart-deal: 'X voor €Y' of 'X% korting' (de gram-referentie is GEEN deal → gewone prijs actie/van)
    deal = None
    mv = re.match(r'(\d+)\s*voor\s*(\d+\.\d{2})', dtxt, re.I)
    mp = re.match(r'(\d+)\s*%\s*korting', dtxt, re.I)
    if mv:
        deal = {'kind': 'voory', 'qty': mv.group(1), 'total': mv.group(2)}
    elif mp:
        deal = {'kind': 'pct', 'pct': mp.group(1)}
    merk, koptekst = _plus_merk(r.get('name', ''))
    return {'naam': r.get('name', ''), 'merk': merk, 'koptekst': koptekst,
            'verpakking': verp, 'eenheid': weight,
            'prijs': prijs, 'actie': actie, 'van': van, 'img': r.get('img', ''), 'deal': deal}

@app.route('/api/plus/zoek')
@login_required
def api_plus_zoek():
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    try:
        import plus_search
        res = plus_search.search(q)
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 502
    if isinstance(res, dict):
        return jsonify(res), 502
    return jsonify([_norm_plus(r) for r in res])

# ─── PORTAAL: pluslokaal.nl in ons jasje (transparante per-gebruiker proxy) ───────────────────────
# Elke gebruiker koppelt eenmalig zijn pluslokaal.nl-inloggegevens; wij loggen op de achtergrond in
# (portaal.py, warme requests.Session), bewaren de sessie server-side en tonen de site in een iframe
# onder onze eigen app-header. Het wachtwoord wordt versleuteld opgeslagen (Fernet) en nooit getoond.
from urllib.parse import urljoin as _urljoin, urlsplit as _urlsplit

_PORTAAL_HOSTS = {'www.pluslokaal.nl', 'pluslokaal.nl'}
_PORTAAL_PREFIX = '/portaal/view/'
_PORTAAL_KEY_FILE = os.path.join(os.path.dirname(__file__), '.portaal_secret')

def _portaal_fernet():
    from cryptography.fernet import Fernet
    if os.path.exists(_PORTAAL_KEY_FILE):
        k = open(_PORTAAL_KEY_FILE, 'rb').read().strip()
    else:
        k = Fernet.generate_key()
        try:
            open(_PORTAAL_KEY_FILE, 'wb').write(k)
            os.chmod(_PORTAAL_KEY_FILE, 0o600)
        except Exception:
            pass
    return Fernet(k)

def _portaal_encrypt(s):
    return _portaal_fernet().encrypt((s or '').encode()).decode()

def _portaal_decrypt(t):
    if not t:
        return None
    try:
        return _portaal_fernet().decrypt(t.encode()).decode()
    except Exception:
        return None

# ─── W2P-account-wachtwoorden (versleuteld opgeslagen, met terugval op oude plaintext) ────────────
def _w2p_pass_store(plain):
    """Versleutel een W2P-wachtwoord voor opslag (zelfde Fernet als het portaal)."""
    try:
        return _portaal_encrypt(plain)
    except Exception:
        return plain

def _w2p_pass_plain(stored):
    """Ontsleutel een opgeslagen W2P-wachtwoord; val terug op de waarde zelf (oude plaintext-settings)."""
    if not stored:
        return stored
    dec = _portaal_decrypt(stored)
    return dec if dec is not None else stored

W2P_MAX_ACCOUNTS = 6

def _w2p_notify_admins(subject, detail):
    """Mail de superadmins die dat willen (User.notify_w2p_fail) bij een mislukte W2P-sync/download."""
    try:
        with app.app_context():
            admins = User.query.filter_by(role='admin', notify_w2p_fail=True).all()
            recips = [a.email for a in admins if a.email]
            if not recips:
                return
            det = str(detail or '')[:600].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            body = (f'<p style="font-size:15px;line-height:1.6;">Er is een probleem met de '
                    f'<b>Winkelpakketten</b> (W2P):</p><p style="font-size:15px;line-height:1.6;color:#b45309;">'
                    f'{det}</p>'
                    f'<p style="font-size:13px;color:#6c6c6c;">Je krijgt deze mail omdat je in Beheer → '
                    f'Winkelpakket-accounts hebt aangevinkt dat je meldingen wilt. Daar kun je dit ook uitzetten.</p>')
            html = _mail_wrapper('Winkelpakketten: actie mislukt', body)
            for r in recips:
                send_mail_async(r, 'PLUSLokaal - ' + subject, html)
    except Exception as e:
        try:
            app.logger.error(f'W2P-faalmelding versturen mislukt: {e}')
        except Exception:
            pass

def _portaal_creds(user):
    """(gebruikersnaam, wachtwoord) van de gekoppelde pluslokaal.nl-account, of (None, None)."""
    if not user or not user.portaal_user or not user.portaal_pass_enc:
        return None, None
    return user.portaal_user, _portaal_decrypt(user.portaal_pass_enc)

def _portaal_rewrite_url(u, page_url):
    """Herschrijf een URL uit een pluslokaal.nl-pagina zodat hij binnen onze proxy blijft.
    Externe (andere host) en niet-http URL's blijven ongewijzigd."""
    if not u:
        return u
    u = u.strip()
    low = u.lower()
    if low.startswith(('data:', 'javascript:', 'mailto:', 'tel:', 'blob:', '#', 'about:')):
        return u
    absu = _urljoin(page_url, u)
    sp = _urlsplit(absu)
    if sp.scheme and sp.scheme not in ('http', 'https'):
        return u
    if sp.netloc and sp.netloc.lower() not in _PORTAAL_HOSTS:
        return u  # externe host → laten staan (CDN's e.d.)
    path = sp.path or '/'
    q = ('?' + sp.query) if sp.query else ''
    return _PORTAAL_PREFIX + path.lstrip('/') + q

def _portaal_rewrite_css(text, page_url):
    """Herschrijf url(...) en @import in CSS zodat verwijzingen via de proxy lopen."""
    def _u(m):
        raw = m.group(2)
        return f"url({m.group(1)}{_portaal_rewrite_url(raw, page_url)}{m.group(1)})"
    text = re.sub(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", _u, text)
    def _imp(m):
        return f'@import {m.group(1)}{_portaal_rewrite_url(m.group(2), page_url)}{m.group(1)}'
    text = re.sub(r"""@import\s+(['"])([^'"]+)\1""", _imp, text)
    return text

def _portaal_rewrite_attrs_only(html, page_url):
    """Herschrijf alleen de URL-attributen (href/src/action/srcset/inline-style url()) naar de proxy -
    zonder onze <base>/<style>/<script> te injecteren. Gebruikt voor AJAX-HTML-fragmenten."""
    def _attr(m):
        return f'{m.group(1)}={m.group(2)}{_portaal_rewrite_url(m.group(3), page_url)}{m.group(2)}'
    html = re.sub(r'\b(href|src|action|poster|data-src|data-href)=("|\')([^"\']*)\2',
                  _attr, html, flags=re.I)

    def _srcset(m):
        parts = []
        for chunk in m.group(2).split(','):
            bits = chunk.strip().split(None, 1)
            if bits:
                bits[0] = _portaal_rewrite_url(bits[0], page_url)
                parts.append(' '.join(bits))
        return f'srcset={m.group(1)}{", ".join(parts)}{m.group(1)}'
    html = re.sub(r'\bsrcset=("|\')([^"\']*)\1', _srcset, html, flags=re.I)

    # url(...) / @import in inline <style>-blokken en style=""-attributen (achtergrondafbeeldingen)
    def _styleblock(m):
        return m.group(1) + _portaal_rewrite_css(m.group(2), page_url) + m.group(3)
    html = re.sub(r'(<style[^>]*>)(.*?)(</style>)', _styleblock, html, flags=re.I | re.S)
    def _styleattr(m):
        return f'style={m.group(1)}{_portaal_rewrite_css(m.group(2), page_url)}{m.group(1)}'
    html = re.sub(r'style=("|\')([^"\']*url\([^"\']*)\1', _styleattr, html, flags=re.I)
    return html

def _portaal_reskin_html(html, page_url):
    """Volledige pagina: URL-attributen herschrijven (structuur blijft byte-voor-byte intact - NIET met
    een parser herserialiseren, want pluslokaal.nl heeft geneste <form>'s die daardoor sneuvelen) én onze
    stijl/scripts injecteren."""
    html = _portaal_rewrite_attrs_only(html, page_url)

    # Klikken/formulieren binnen het iframe houden + de layout herstellen. pluslokaal.nl verbergt de
    # sidebar (Jaarkalender/Tarieven/Mutatieformulieren/"Vraag een opdracht aan") standaard met
    # display:none en toont die via runtime-JS dat het proxyen niet schoon overleeft. We forceren de
    # sidebar zichtbaar op desktop en zetten de content ernaast (twee kolommen, net als het origineel).
    # De top-categoriebalk (Landelijke activiteiten e.d.) wordt door hun JS opgebouwd en valt weg - de
    # nav-panels (uitgeklapt mega-menu) verbergen we zodat dat geen rommelig blok toont.
    # Ook: het "Schapkaarten"-item uit de sidebar halen (dat hebben we zelf al in de app).
    inject = (
        # VROEG: XMLHttpRequest/fetch onderscheppen en pluslokaal.nl-/root-relatieve URLs door onze proxy
        # sturen. pluslokaal.nl doet o.a. "Bestellen"/toevoegen-aan-mandje via AJAX met root-relatieve
        # URLs (bv. /W2P/...); die zouden anders naar onze eigen origin gaan → 404 → "something went wrong".
        '<script>(function(){var P="/portaal/view";'
        'function rw(u){try{if(typeof u!=="string"||!u)return u;'
        'if(u.indexOf(P+"/")===0)return u;'
        'var m=u.match(/^(?:https?:)?\\/\\/(?:www\\.)?pluslokaal\\.nl(\\/.*)?$/i);'
        'if(m)return P+(m[1]||"/");'
        'if(u.charAt(0)==="/"&&u.charAt(1)!=="/")return P+u;'
        'return u;}catch(e){return u;}}'
        'var O=XMLHttpRequest.prototype.open;'
        'XMLHttpRequest.prototype.open=function(){var a=[].slice.call(arguments);a[1]=rw(a[1]);'
        'try{if(/basket|adddocument|deletedocument|emptybasket/i.test(a[1]||"")){'
        'this.addEventListener("loadend",function(){try{if(window.parent&&window.parent!==window&&'
        'window.parent.refreshBasket)setTimeout(window.parent.refreshBasket,150);}catch(e){}});}}catch(e){}'
        'return O.apply(this,a);};'
        'if(window.fetch){var F=window.fetch;window.fetch=function(u,o){try{if(typeof u==="string")u=rw(u);}catch(e){}return F.call(this,u,o);};}'
        '})();</script>'
        '<base target="_self">'
        '<style>'
        'html,body{background:#f4f5f3;}'
        '@media(min-width:768px){'
        '.sidebar{display:inline-block !important;float:left;width:31%;}'
        '.container:has(.sidebar) .pagecontent{float:right !important;width:67% !important;'
        'display:inline-block !important;}'
        '}'
        '.nav-panels{display:none !important;}'
        # pluslokaal.nl 1-op-1 in onze pluslokaal.com-stijl. Deze CSS draait ALLEEN binnen het iframe
        # (dus raakt alleen pluslokaal.nl-elementen; hun eigen header/nav is toch verborgen), daarom
        # mogen de selectors iframe-breed - zo geldt de stijl óók op W2P-pagina's (winkelmandje,
        # bestelgeschiedenis) die geen .pagecontent gebruiken.
        # Knoppen (PLUS-groen + onze "spraakwolk"-radius)
        '.btn,input[type=submit],input[type=button],button[type=submit]{'
        "font-family:'Open Sans',-apple-system,'Segoe UI',sans-serif !important;font-weight:700 !important;"
        'border-radius:22px 22px 22px 4px !important;border:none !important;cursor:pointer;'
        'padding:9px 20px !important;box-shadow:none !important;}'
        '.btn-primary,input[type=submit],button[type=submit],.btn-green,.btn-success{'
        'background:#80bd1d !important;color:#fff !important;}'
        '.btn-primary:hover,input[type=submit]:hover,button[type=submit]:hover,.btn-green:hover{'
        'background:#77b01a !important;color:#fff !important;}'
        '.btn-secondary,.btn-default:not(.btn-search){background:#eef6e1 !important;color:#115013 !important;}'
        '.btn-secondary:hover,.btn-default:not(.btn-search):hover{background:#e2efce !important;}'
        '.btn-danger{background:#dd350d !important;color:#fff !important;}'
        # Invoervelden
        'input.form-control,select.form-control,textarea,input[type=text],input[type=email],'
        'input[type=tel],input[type=number],input[type=password]{'
        "font-family:'Open Sans',-apple-system,'Segoe UI',sans-serif !important;"
        'border:1px solid #d8d8d8 !important;border-radius:8px !important;padding:10px 12px !important;'
        'box-shadow:none !important;background:#fff !important;color:#333 !important;}'
        'input.form-control:focus,select.form-control:focus,textarea:focus,input[type=text]:focus,'
        'input[type=email]:focus,input[type=password]:focus{'
        'border-color:#115013 !important;box-shadow:none !important;outline:none !important;}'
        # Content-blokken (tegels BBQ/Mepal e.d.)
        '.blockitem{border-radius:12px !important;overflow:hidden;background:#fff;'
        'box-shadow:0 1px 3px rgba(0,0,0,.1),0 1px 2px rgba(0,0,0,.06);'
        'transition:transform .15s,box-shadow .15s;}'
        '.blockitem:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(0,0,0,.13);}'
        '.blockitem a{text-decoration:none;color:inherit;}'
        ".blockitem h2{font-family:'Open Sans',-apple-system,sans-serif !important;font-weight:800 !important;}"
        # Koppen
        '.optiongroup>h2,.portal-options-title,h1,h1 span,.optiongroup>h2 span,h1.mb-0{'
        "font-family:'Open Sans',-apple-system,sans-serif !important;color:#115013 !important;font-weight:800 !important;}"
        # Panelen
        '.panel,.panel-default{border-radius:12px !important;overflow:hidden;'
        'border:1px solid #eaeae7 !important;box-shadow:0 1px 3px rgba(0,0,0,.08) !important;}'
        '.panel-heading-custom,.panel-heading{background:#80bd1d !important;color:#fff !important;'
        "font-weight:800 !important;font-family:'Open Sans',-apple-system,sans-serif !important;border:none !important;}"
        # Tabellen (incl. bootstrap-table met .fixed-table-header)
        'table.table,.table{border-collapse:collapse;}'
        '.table thead th,table.table thead th,.fixed-table-header th,.fixed-table-container thead th{'
        'background:#eef6e1 !important;color:#115013 !important;font-weight:800 !important;'
        'border:none !important;border-bottom:2px solid #dbe9c4 !important;padding:12px 14px !important;}'
        '.table td,.table th{border-color:#eaeae7 !important;}'
        '.table-striped>tbody>tr:nth-child(odd)>td{background:#f9fbf5 !important;}'
        '.table-hover>tbody>tr:hover>td{background:#eef6e1 !important;}'
        # Links in ons groen (knoppen/tegels uitgezonderd via hun eigen !important-regels)
        'a{color:#115013;}a:hover{color:#0b380d;}.blockitem a,.btn{color:inherit;}'
        '</style>'
        '<script>(function(){function clean(){try{document.querySelectorAll(".sidebar a").forEach('
        'function(a){if(/schapkaarten/i.test((a.textContent||"").trim())){var li=a.closest("li");'
        '(li||a).remove();}});}catch(e){}}'
        'if(document.readyState!=="loading")clean();'
        'document.addEventListener("DOMContentLoaded",clean);setTimeout(clean,800);'
        # Bij ELKE navigatie binnen het iframe (ook links in de pluslokaal.nl-sidebar/-content) de
        # laad-spinner van de parent tonen; de parent verbergt hem weer op het iframe-load-event.
        'function pload(){try{if(window.parent&&window.parent!==window&&window.parent.showLoad)'
        'window.parent.showLoad();}catch(e){}}'
        'window.addEventListener("beforeunload",pload);'
        'document.addEventListener("click",function(e){var a=e.target&&e.target.closest&&e.target.closest("a[href]");'
        'if(!a)return;var h=a.getAttribute("href")||"";if(a.target&&a.target!=="_self")return;'
        'if(/^(#|javascript:|mailto:|tel:)/i.test(h))return;pload();},true);})();</script>'
    )
    if re.search(r'<head[^>]*>', html, flags=re.I):
        html = re.sub(r'(<head[^>]*>)', lambda m: m.group(1) + inject, html, count=1, flags=re.I)
    else:
        html = inject + html
    return html

def _portaal_upstream_path():
    """Bouw het pluslokaal.nl-pad (incl. querystring) uit het huidige proxy-request."""
    sub = request.view_args.get('sub', '') if request.view_args else ''
    path = '/' + (sub or '')
    qs = request.query_string.decode('latin-1') if request.query_string else ''
    if qs:
        path += '?' + qs
    return path

# De vaste top-categorieën van pluslokaal.nl (headeritems). Stabiel; tonen we in ons eigen design.
_PORTAAL_CATS = [
    ('Landelijke activiteiten', 'landelijke-activiteiten/'),
    ('Lokale activiteiten',     'lokale-activiteiten/'),
    ('Winkel',                  'winkel/'),
    ('E-commerce',              'e-commerce/'),
    ('Social Media',            'social-media/'),
    ('Helpdesk',                'headermenu/helpdesk/'),
]
# De 3 gebruikersbalk-iconen van pluslokaal.nl (label, pad, FontAwesome-icoon).
_PORTAAL_ICONS = [
    ('Winkelmandje',            'W2P/Basket.aspx',        'fa-basket-shopping'),
    ('Mijn bestelgeschiedenis', 'W2P/OrderHistory.aspx',  'fa-clock-rotate-left'),
    ('Mijn actieoverzicht',     'Campaigns/Home.aspx',    'fa-tag'),
]

# Menu-boom (categorie → sectie → item) uit pluslokaal.nl; gereconstrueerd uit de URL-padstructuur van
# de item-links. Gecachet (verandert zelden) zodat we niet bij elke paginaweergave opnieuw ophalen.
_portaal_menu_cache = {'ts': 0.0, 'tree': None}
_PORTAAL_MENU_TTL = 1800  # 30 min
# Home-HTML kort per gebruiker cachen (menu + winkelmandje-teller delen dezelfde ophaal).
_portaal_home_cache = {}   # uid -> (ts, doc)
_PORTAAL_HOME_TTL = 45

def _portaal_home_doc(user):
    now = time.time()
    ent = _portaal_home_cache.get(user.id)
    if ent and now - ent[0] < _PORTAAL_HOME_TTL:
        return ent[1]
    pu, pw = _portaal_creds(user)
    if not pu:
        return None
    import portaal as _pmod
    r = _pmod.fetch(user.id, '/', pu, pw)
    doc = r.text if r is not None else None
    if doc:
        _portaal_home_cache[user.id] = (now, doc)
    return doc

def _portaal_basket_count(user):
    """Aantal artikelen in het pluslokaal.nl-winkelmandje (rode badge). 0 = geen badge."""
    doc = _portaal_home_doc(user)
    if not doc:
        return 0
    i = doc.find('fa-shopping-basket')
    if i < 0:
        return 0
    m = re.search(r'class="counter"[^>]*>\s*(\d+)\s*<', doc[i:i + 220])
    try:
        return int(m.group(1)) if m else 0
    except Exception:
        return 0

def _portaal_build_menu(user, doc=None):
    if doc is None:
        doc = _portaal_home_doc(user)
    if not doc:
        return None
    import html as _html
    i = doc.find('module-navbar')
    j = doc.find('pagecontent', i) if i >= 0 else -1
    region = doc[i:j] if (i >= 0 and j > i) else doc
    raw = re.findall(r"<a[^>]*class=['\"]item-link['\"][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                     region, re.S)
    items = []
    seen = set()
    for href, txt in raw:
        t = _html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', txt))).strip()
        if not t or not href.startswith('/') or href in seen:
            continue
        seen.add(href)
        items.append((href, t))
    cats = []
    for label, cpath in _PORTAAL_CATS:
        base = '/' + cpath.lstrip('/')
        if cpath == 'headermenu/helpdesk/':
            cats.append({'label': label, 'href': base, 'sections': []})
            continue
        d0 = base.rstrip('/').count('/')
        secs = []
        for href, t in items:
            if href.startswith(base) and href != base and href.rstrip('/').count('/') == d0 + 1:
                kids = [{'label': ct, 'href': ch} for ch, ct in items
                        if ch.startswith(href) and ch != href and ch.rstrip('/').count('/') == d0 + 2]
                secs.append({'label': t, 'href': href, 'children': kids})
        cats.append({'label': label, 'href': base, 'sections': secs})
    return cats

def _portaal_menu(user, doc=None):
    now = time.time()
    c = _portaal_menu_cache
    if c['tree'] and (now - c['ts'] < _PORTAAL_MENU_TTL):
        return c['tree']
    try:
        tree = _portaal_build_menu(user, doc)
    except Exception:
        tree = None
    if tree:
        c['tree'] = tree
        c['ts'] = now
    # Bij een ophaalfout: val terug op de vorige cache, anders op de kale categorielijst.
    if c['tree']:
        return c['tree']
    return [{'label': lb, 'href': '/' + cp.lstrip('/'), 'sections': []} for lb, cp in _PORTAAL_CATS]

@app.route('/portaal')
@login_required
def portaal():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    linked = bool(user.portaal_user and user.portaal_pass_enc)
    menu = None
    basket = 0
    if linked:
        doc = _portaal_home_doc(user)          # één ophaal, gedeeld door menu + mandjeteller
        basket = _portaal_basket_count(user)
        menu = _portaal_menu(user, doc)
    return render_template('portaal.html', user=user, linked=linked,
                           portaal_status=user.portaal_status,
                           portaal_menu=menu, portaal_icons=_PORTAAL_ICONS,
                           portaal_basket=basket)

@app.route('/portaal/koppel', methods=['POST'])
@login_required
def portaal_koppel():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if is_demo(user):
        flash('In de demo kun je geen portaal koppelen.', 'error')
        return redirect(url_for('portaal'))
    pu = (request.form.get('portaal_user') or '').strip()
    pw = request.form.get('portaal_pass') or ''
    if not pu or not pw:
        flash('Vul je pluslokaal.nl-gebruikersnaam en -wachtwoord in.', 'error')
        return redirect(url_for('portaal'))
    import portaal as _pmod
    ok, msg = _pmod.login(user.id, pu, pw)
    if not ok:
        flash(f'Koppelen mislukt: {msg}', 'error')
        return redirect(url_for('portaal'))
    user.portaal_user = pu
    user.portaal_pass_enc = _portaal_encrypt(pw)
    user.portaal_status = 'ok'
    user.portaal_checked = datetime.now()
    db.session.commit()
    flash('pluslokaal.nl gekoppeld - je bent nu automatisch ingelogd.', 'success')
    return redirect(url_for('portaal'))

@app.route('/portaal/ontkoppel', methods=['POST'])
@login_required
def portaal_ontkoppel():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    try:
        import portaal as _pmod
        _pmod.logout(user.id)
    except Exception:
        pass
    user.portaal_user = None
    user.portaal_pass_enc = None
    user.portaal_status = 'none'
    user.portaal_checked = None
    db.session.commit()
    flash('pluslokaal.nl ontkoppeld.', 'success')
    return redirect(url_for('portaal'))

@app.route('/portaal/basket-count')
@login_required
def portaal_basket_count_api():
    """Actuele winkelmandje-teller (verse ophaal) - de parent roept dit na elke iframe-navigatie aan om
    de badge bij het mandje-icoon live bij te werken."""
    user = get_current_user()
    if not user or not user.portaal_user or not user.portaal_pass_enc:
        return jsonify({'count': 0})
    _portaal_home_cache.pop(user.id, None)   # forceer een verse teller
    return jsonify({'count': _portaal_basket_count(user)})

@app.route('/portaal/view/', defaults={'sub': ''}, methods=['GET', 'POST'])
@app.route('/portaal/view/<path:sub>', methods=['GET', 'POST'])
@login_required
def portaal_view(sub):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    pu, pw = _portaal_creds(user)
    if not pu or not pw:
        return Response('<p style="font:15px sans-serif;padding:24px">Koppel eerst je pluslokaal.nl-account '
                        'onder <b>Portaal</b>.</p>', mimetype='text/html')
    import portaal as _pmod
    path = _portaal_upstream_path()
    data = request.get_data() if request.method == 'POST' else None
    # Statische assets (css/js/afbeeldingen/fonts) hoeven de stateful ASP.NET-sessielock niet vast te
    # houden → parallel ophalen; navigatie/POST blijft geserialiseerd.
    _static_ext = ('.css', '.js', '.mjs', '.map', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
                   '.ico', '.bmp', '.woff', '.woff2', '.ttf', '.otf', '.eot')
    _is_static = (request.method == 'GET'
                  and path.split('?', 1)[0].lower().endswith(_static_ext))
    r = _pmod.fetch(user.id, path, pu, pw, method=request.method, data=data,
                    content_type=request.content_type, stateful=not _is_static)
    if r is None:
        # Sessie/verbinding faalde → status markeren zodat de UI het toont
        try:
            if user.portaal_status != 'fout':
                user.portaal_status = 'fout'; db.session.commit()
        except Exception:
            pass
        return Response('<p style="font:15px sans-serif;padding:24px">pluslokaal.nl is nu niet '
                        'bereikbaar. Probeer het zo opnieuw.</p>', status=502, mimetype='text/html')
    ct = (r.headers.get('Content-Type') or '').lower()
    page_url = str(r.url)
    if 'text/html' in ct:
        txt = r.text
        low = txt[:1500].lower()
        # Volledige pagina → reskinnen. AJAX-fragmenten (geen <html>/<head>) alleen URL-herschrijven,
        # niet onze <base>/<style>/<script> injecteren (dat zou het fragment corrumperen).
        if '<html' in low or '<head' in low or '<!doctype' in low:
            body = _portaal_reskin_html(txt, page_url)
        else:
            body = _portaal_rewrite_attrs_only(txt, page_url)
        return Response(body, status=r.status_code, mimetype='text/html')
    if 'text/css' in ct:
        body = _portaal_rewrite_css(r.text, page_url)
        resp = Response(body, status=r.status_code, content_type=r.headers.get('Content-Type'))
        if _is_static and r.status_code == 200:
            resp.headers['Cache-Control'] = 'private, max-age=1800'
        return resp
    # Overige (JS, afbeeldingen, PDF/downloads) → byte-voor-byte doorzetten
    resp = Response(r.content, status=r.status_code, content_type=r.headers.get('Content-Type'))
    cd = r.headers.get('Content-Disposition')
    if cd:
        resp.headers['Content-Disposition'] = cd
    # Statische assets mag de browser cachen → bij portaal-navigatie niet telkens opnieuw door de proxy.
    # 'private' zodat een gedeelde proxy (Cloudflare) het niet tussen gebruikers deelt.
    elif _is_static and r.status_code == 200:
        resp.headers['Cache-Control'] = 'private, max-age=1800'
    return resp

@app.route('/api/labels/zoeken')
@login_required
def api_label_search():
    """Zoek producten op plus.nl (zelfde warme-browserzoek als bij Schapkaarten) en neem naam + prijs over.
    plus.nl toont geen EAN/streepjescode, dus de barcode blijft handmatig (of uit de eigen productcatalogus)."""
    u = get_current_user()
    fil = _label_filiaal() or u.filiaal
    term = (request.args.get('q') or '').strip()
    if len(term) < 2:
        return jsonify([])
    try:
        import plus_search
        res = plus_search.search(term)
    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 502
    if isinstance(res, dict):
        return jsonify(res), 502
    # Eigen catalogus-barcodes klaarzetten om, waar de naam matcht, tóch een barcode te kunnen invullen.
    cat = {p.name.strip().lower(): p for p in Product.query.filter_by(filiaal=fil, active=True).limit(1000).all()}
    out = []
    for r in res:
        n = _norm_plus(r)
        naam = (n.get('naam') or '').strip()
        price = n.get('prijs') or n.get('actie') or None
        hit = cat.get(naam.lower())
        out.append({'name': naam, 'price': price,
                    'verpakking': n.get('verpakking') or '', 'eenheid': n.get('eenheid') or '',
                    'prijs': n.get('prijs'), 'actie': n.get('actie'), 'van': n.get('van'),
                    'deal': n.get('deal'),
                    'unit': n.get('verpakking') or '',
                    'img': n.get('img') or '', 'href': r.get('href') or '',
                    'barcode': (hit.barcode if hit else '') or '',
                    'barcode_type': (hit.barcode_type if hit else 'ean13')})
    return jsonify(out)

@app.route('/api/labels/ean')
@login_required
def api_label_ean():
    """Haal de EAN('s) van een plus.nl-product op (product-detail-API). Leeg als plus.nl niets geeft.
    Kan meerdere barcodes teruggeven → de gebruiker kiest er dan één."""
    href = (request.args.get('href') or '').strip()
    if not href:
        return jsonify({'eans': []})
    try:
        import plus_search
        eans = plus_search.product_eans(href)
    except Exception:
        eans = []
    return jsonify({'eans': eans})

@app.route('/labels/producten', methods=['GET', 'POST'])
@login_required
def label_products():
    u = get_current_user()
    if not can(u, 'products'):
        flash('Je hebt geen toegang tot producten.', 'error'); return redirect(url_for('labels_dashboard'))
    fil = _label_filiaal() or u.filiaal
    if request.method == 'POST':
        act = request.form.get('action', 'add')
        if act == 'delete':
            p = Product.query.get(request.form.get('id', type=int))
            if p and (is_superadmin(u) or p.filiaal == u.filiaal):
                db.session.delete(p); db.session.commit()
                flash('Product verwijderd.', 'success')
        elif act == 'edit':
            p = Product.query.get(request.form.get('id', type=int))
            if p and (is_superadmin(u) or p.filiaal == u.filiaal):
                nm = request.form.get('name', '').strip()
                if nm:
                    p.name = nm
                    p.barcode = request.form.get('barcode', '').strip()
                    p.barcode_type = request.form.get('barcode_type', 'ean13')
                    p.price = _num(request.form.get('price'))
                    db.session.commit()
                    flash('Product bijgewerkt.', 'success')
        else:
            nm = request.form.get('name', '').strip()
            bc = request.form.get('barcode', '').strip()
            if nm:
                db.session.add(Product(filiaal=fil, name=nm, barcode=bc,
                                       barcode_type=request.form.get('barcode_type', 'ean13'),
                                       price=_num(request.form.get('price'))))
                db.session.commit()
                flash('Product toegevoegd.', 'success')
        return redirect(url_for('label_products'))
    term = (request.args.get('q') or '').strip()
    q = Product.query.filter_by(filiaal=fil, active=True)
    if term:
        q = q.filter(Product.name.ilike(f'%{term}%') | Product.barcode.ilike(f'%{term}%'))
    products = q.order_by(Product.name).limit(300).all()
    return render_template('label_products.html', user=u, products=products, q=term)

@app.route('/labels/geschiedenis')
@login_required
def label_history():
    u = get_current_user()
    if not can(u, 'labels_history'):
        flash('Je hebt geen toegang tot de labelhistorie.', 'error'); return redirect(url_for('labels_dashboard'))
    fil = _label_filiaal()
    q = LabelJob.query
    if fil is not None:
        q = q.filter_by(filiaal=fil)
    jobs = q.order_by(LabelJob.created_at.desc()).limit(200).all()
    return render_template('label_history.html', user=u, jobs=jobs)

# ─── LABELS-MODULE: netwerkprinten (TCP 9100) + printerconfig ─────────────────
def _send_raw(ip, port, payload, timeout=8):
    """Stuur rauwe bytes naar een netwerkprinter (poort 9100). Raise OSError bij falen."""
    import socket, time
    with socket.create_connection((ip, int(port)), timeout=timeout) as sock:
        sock.sendall(bytes(payload))
        time.sleep(0.4)   # printer tijd geven vóór sluiten (anders onvolledige job)

# ─── PRINT-AGENT (Raspberry Pi in de winkel) ──────────────────────────────────
# De Pi verbindt ZELF (uitgaand, HTTPS) met pluslokaal.com en pollt om printopdrachten:
# geen firewall-gaten in het winkelnetwerk nodig. Printers hangen via USB aan de Pi.
AGENT_ONLINE_WINDOW = 120       # seconden sinds laatste poll om als 'online' te gelden
_AGENT_FILE = os.path.join(os.path.dirname(__file__), 'agent', 'pluslokaal_agent.py')
_AGENT_INSTALL = os.path.join(os.path.dirname(__file__), 'agent', 'install.sh')

def _agent_online(f):
    return bool(f and f.agent_key and f.agent_seen
                and (datetime.now() - f.agent_seen).total_seconds() < AGENT_ONLINE_WINDOW)

def _agent_by_key():
    key = (request.headers.get('X-Agent-Key') or '').strip()
    if not key or len(key) < 20:
        return None
    return Filiaal.query.filter_by(agent_key=key).first()

def _agent_enqueue(filiaal_nummer, kind, payload_bytes, meta):
    import base64
    job = AgentJob(filiaal=filiaal_nummer, kind=kind,
                   payload=base64.b64encode(bytes(payload_bytes)).decode('ascii'),
                   meta_json=json.dumps(meta or {}))
    db.session.add(job); db.session.commit()
    return job.id

def _agent_wait(ajid, job_id=None, base=0, span=100, label='', timeout=180):
    """Wacht (pollend) tot de agent de job afrondt. Werkt de print-voortgang bij; raise OSError bij fout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if job_id and _pj_is_cancelled(job_id):
            with app.app_context():
                r = db.session.get(AgentJob, ajid)
                if r and r.status in ('pending', 'fetched'):
                    r.status = 'cancelled'; db.session.commit()
            raise _PrintCancelled()
        with app.app_context():
            r = db.session.get(AgentJob, ajid)
            st = r.status if r else 'error'
            err = (r.error if r else 'agentopdracht verdwenen')
        if st == 'done':
            return
        if st in ('error', 'cancelled'):
            raise OSError(err or 'de print-agent meldde een fout')
        if job_id:
            pct = base + int(span * min(0.9, (time.time() - t0) / 30.0))
            _pj_set(job_id, percent=max(pct, sharedstate.job_field(job_id, 'percent', 0)),
                    message=f'{label}: via winkel-agent…')
        time.sleep(1)
    raise OSError('de print-agent reageerde niet binnen de tijd (staat de Pi aan?)')

def _send_label(f, payload, timeout=45):
    """Stuur een labelprinter-payload naar de winkel: via de online print-agent (USB aan de Pi) als
    die er is, anders rechtstreeks naar het printer-IP (poort 9100). Raise OSError bij falen."""
    if _agent_online(f):
        ajid = _agent_enqueue(f.nummer, 'label', payload, {'label': 'labels'})
        t0 = time.time()
        while time.time() - t0 < timeout:
            db.session.expire_all()
            r = db.session.get(AgentJob, ajid)
            if r and r.status == 'done':
                return
            if r and r.status in ('error', 'cancelled'):
                raise OSError(r.error or 'de print-agent meldde een fout')
            time.sleep(1)
        raise OSError('de print-agent reageerde niet (staat de Pi aan?)')
    if not f.printer_ip:
        raise OSError('geen labelprinter ingesteld en geen agent online')
    _send_label(f, payload)

def _agent_jobs_cleanup(max_age_seconds=3600):
    try:
        cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
        AgentJob.query.filter(AgentJob.created_at < cutoff).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()

def _agent_version_info():
    """(versie, sha256) van het agent-bestand op schijf."""
    import hashlib
    try:
        src = open(_AGENT_FILE, 'rb').read()
        m = re.search(rb"AGENT_VERSION\s*=\s*'([^']+)'", src)
        ver = m.group(1).decode() if m else '0.0.0'
        return ver, hashlib.sha256(src).hexdigest()
    except Exception:
        return '0.0.0', ''

@app.route('/api/agent/poll', methods=['POST'])
def agent_poll():
    import base64
    f = _agent_by_key()
    if not f:
        abort(401)
    body = request.get_json(silent=True) or {}
    f.agent_seen = datetime.now()
    # Self-healing: oude sleutels zonder webinterface-wachtwoord alsnog voorzien,
    # zodat de Pi altijd een login kan synchroniseren.
    if not f.agent_web_pass:
        import string as _string
        _alf = _string.ascii_letters + _string.digits
        f.agent_web_pass = ''.join(secrets.choice(_alf) for _ in range(14))
    f.agent_version = str(body.get('version') or '')[:20]
    try:
        f.agent_info = json.dumps(body.get('info') or {})[:2000]
    except Exception:
        pass
    db.session.commit()
    _agent_jobs_cleanup()
    jobs = (AgentJob.query.filter_by(filiaal=f.nummer, status='pending')
            .order_by(AgentJob.id).limit(3).all())
    out = []
    for j in jobs:
        j.status = 'fetched'
        out.append({'id': j.id, 'kind': j.kind, 'meta': json.loads(j.meta_json or '{}'),
                    'payload_b64': j.payload})
    db.session.commit()
    ver, sha = _agent_version_info()
    import hashlib as _hl
    return jsonify({'jobs': out, 'agent_version': ver,
                    'store': {'nummer': f.nummer, 'naam': f.naam or ''},
                    'web_pass_sha256': (_hl.sha256(f.agent_web_pass.encode()).hexdigest()
                                        if f.agent_web_pass else ''),
                    'web_tunnel_until': _tunnel_active_until(f.nummer)})

@app.route('/api/agent/result', methods=['POST'])
def agent_result():
    f = _agent_by_key()
    if not f:
        abort(401)
    body = request.get_json(silent=True) or {}
    j = db.session.get(AgentJob, int(body.get('job_id') or 0))
    if not j or j.filiaal != f.nummer:
        abort(404)
    j.status = 'done' if body.get('ok') else 'error'
    j.error = (str(body.get('error') or '')[:400]) or None
    j.done_at = datetime.now()
    db.session.commit()
    return jsonify({'ok': True})

# ─── Webinterface van de Pi/mini-pc op afstand tonen (via de uitgaande verbinding) ──
# Geen open poorten in de winkel: als een admin in PLUSLokaal de interface opent, zet
# de server het browserverzoek in de DB; de agent haalt het op (webpoll), voert het lokaal
# uit en schrijft het antwoord terug (webresult). Via de DB werkt dit over alle gunicorn-
# workers heen. On-demand: de agent tunnelt alleen zolang er recent activiteit is.
_TUNNEL_WINDOW = 120        # sec dat de agent blijft tunnelen na de laatste actie

def _ver_tuple(s):
    try:
        return tuple(int(x) for x in str(s or '0').split('.')[:3])
    except Exception:
        return (0,)

def _tunnel_active_until(nr):
    row = (AgentWebReq.query.filter_by(filiaal=nr)
           .order_by(AgentWebReq.created_at.desc()).first())
    if not row:
        return 0
    until = row.created_at.timestamp() + _TUNNEL_WINDOW
    return until if until > time.time() else 0

def _tunnel_cleanup():
    cutoff = datetime.now() - timedelta(seconds=_TUNNEL_WINDOW)
    AgentWebReq.query.filter(AgentWebReq.created_at < cutoff).delete()
    db.session.commit()

@app.route('/api/agent/webpoll', methods=['POST'])
def agent_webpoll():
    f = _agent_by_key()
    if not f:
        abort(401)
    # Long-poll: wacht tot er een openstaand verzoek is (of tot de time-out).
    deadline = time.time() + 25
    while time.time() < deadline:
        row = (AgentWebReq.query.filter_by(filiaal=f.nummer, status='pending')
               .order_by(AgentWebReq.id).first())
        if row:
            return jsonify({'id': row.req_id, 'method': row.method, 'path': row.path,
                            'ctype': row.ctype or '', 'body_b64': row.body or ''})
        time.sleep(0.3)
        db.session.remove()     # verse sessie voor de volgende ronde
    return jsonify({})

@app.route('/api/agent/webresult', methods=['POST'])
def agent_webresult():
    f = _agent_by_key()
    if not f:
        abort(401)
    body = request.get_json(silent=True) or {}
    row = AgentWebReq.query.filter_by(filiaal=f.nummer, req_id=str(body.get('id') or '')).first()
    if row:
        row.resp_status = int(body.get('status') or 502)
        row.resp_ctype = (body.get('ctype') or 'text/html; charset=utf-8')[:120]
        row.resp_loc = body.get('location') or ''
        row.resp_body = body.get('body_b64') or ''
        row.status = 'answered'
        db.session.commit()
    return jsonify({'ok': True})

@app.route('/filialen/<int:nummer>/agent-web/', methods=['GET', 'POST'])
@app.route('/filialen/<int:nummer>/agent-web/<path:sub>', methods=['GET', 'POST'])
@login_required
def agent_web_proxy(nummer, sub=''):
    import base64 as _b64
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    f = Filiaal.query.filter_by(nummer=nummer).first()
    if not f or not f.agent_key:
        abort(404)
    if not _agent_online(f):
        return ("<h3 style='font-family:sans-serif'>Agent offline</h3>"
                "<p style='font-family:sans-serif'>Deze Pi/mini-pc is nu niet verbonden. "
                "Zodra 'ie online is kun je de webinterface hier openen.</p>"), 503
    # Toegang op afstand vereist agent v1.4.0+ (daarin zit de tunnel).
    if _ver_tuple(f.agent_version) < (1, 4, 0):
        return ("<div style='font-family:sans-serif;max-width:520px;margin:40px auto'>"
                "<h3>Agent bijwerken nodig</h3><p>Deze winkel draait nog agent "
                f"v{f.agent_version or '?'}. Toegang op afstand werkt vanaf v1.4.0.</p>"
                "<p>De agent werkt zichzelf automatisch bij (binnen enkele uren). Sneller: open op "
                "de Pi zelf de webinterface en klik op <b>Zoek naar updates</b>. Daarna werkt deze knop.</p></div>"), 409
    base = url_for('agent_web_proxy', nummer=nummer)   # .../agent-web/
    _tunnel_cleanup()
    path = '/' + sub
    if request.query_string:
        path += '?' + request.query_string.decode()
    rid = secrets.token_hex(8)
    row = AgentWebReq(filiaal=nummer, req_id=rid, method=request.method, path=path,
                      ctype=request.headers.get('Content-Type', '')[:120],
                      body=_b64.b64encode(request.get_data() or b'').decode(), status='pending')
    db.session.add(row)
    db.session.commit()
    # wacht op het antwoord van de agent (die via webpoll/webresult werkt)
    resp, deadline = None, time.time() + 30
    while time.time() < deadline:
        db.session.remove()
        r2 = AgentWebReq.query.filter_by(filiaal=nummer, req_id=rid).first()
        if r2 and r2.status == 'answered':
            resp = {'status': r2.resp_status, 'ctype': r2.resp_ctype,
                    'location': r2.resp_loc, 'body_b64': r2.resp_body}
            db.session.delete(r2)
            db.session.commit()
            break
        time.sleep(0.2)
    if resp is None:
        AgentWebReq.query.filter_by(filiaal=nummer, req_id=rid).delete()
        db.session.commit()
        return ("<h3 style='font-family:sans-serif'>Geen antwoord van de agent</h3>"
                "<p style='font-family:sans-serif'>De Pi/mini-pc reageert nu niet. Probeer het zo nog eens.</p>"), 504
    status = int(resp.get('status') or 502)
    ctype = resp.get('ctype') or 'text/html; charset=utf-8'
    data = _b64.b64decode(resp.get('body_b64') or '')
    if 'text/html' in ctype:
        html = data.decode('utf-8', 'replace')
        # root-relatieve links/acties naar het proxy-pad wijzen
        html = re.sub(r"(action|href|src)=(['\"]?)/(?!/)",
                      lambda m: f"{m.group(1)}={m.group(2)}{base}", html)
        # onze CSRF-token in elk agent-formulier zetten zodat POSTs door de proxy heen mogen
        tok = session.get('csrf_token', '')
        html = re.sub(r"(<form\b[^>]*>)",
                      lambda m: f"{m.group(1)}<input type=hidden name=_csrf value=\"{tok}\">", html)
        data = html.encode('utf-8')
    r = app.response_class(data, status=status, content_type=ctype)
    loc = resp.get('location') or ''
    if loc.startswith('/') and not loc.startswith('//'):
        loc = base + loc[1:]
    if loc:
        r.headers['Location'] = loc
    r.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return r

@app.route('/api/agent/update')
def agent_update():
    ver, sha = _agent_version_info()
    return jsonify({'version': ver, 'sha256': sha})

@app.route('/api/agent/download')
def agent_download():
    if not os.path.exists(_AGENT_FILE):
        abort(404)
    return send_file(_AGENT_FILE, mimetype='text/x-python', as_attachment=True,
                     download_name='pluslokaal_agent.py')

@app.route('/agent/install.sh')
def agent_install_sh():
    if not os.path.exists(_AGENT_INSTALL):
        abort(404)
    return send_file(_AGENT_INSTALL, mimetype='text/x-shellscript')

@app.route('/agent/user-data')
@login_required
def agent_userdata_generic():
    """GENERIEK cloud-init-bestand (zelfde voor élke winkel): flash Ubuntu Server, vervang 'user-data'
    door dit bestand, en de Pi installeert zichzelf. De winkel-sleutel plak je daarna ter plekke in de
    Pi-webinterface (welkomstscherm). Alleen voor ingelogde admins: het RMM-installatiescript (met
    enrollment-tokens) wordt er inline in meegebakken."""
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    return Response(_agent_userdata_text(), mimetype='text/yaml',
                    headers={'Content-Disposition': 'attachment; filename="user-data"'})

def _kiosk_enabled():
    return (get_setting('agent_kiosk', '1') or '1') == '1'

def _kiosk_install_sh(user):
    """Bash die een minimaal bureaublad + kioskbrowser installeert die de lokale webinterface
    (http://localhost/) toont. Zo is de interface zichtbaar op een aangesloten scherm EN via
    RMM remote-desktop (MeshCentral pakt de X-sessie van de automatisch ingelogde gebruiker)."""
    return r'''#!/usr/bin/env bash
# PLUSLokaal kiosk - toont de webinterface op een scherm en via RMM remote-desktop.
export DEBIAN_FRONTEND=noninteractive
apt-get update || true
apt-get install -y --no-install-recommends xserver-xorg xinit openbox unclutter chromium-browser \
  || apt-get install -y --no-install-recommends xserver-xorg xinit openbox unclutter chromium || true
U="__USER__"
H="$(getent passwd "$U" | cut -d: -f6)"
[ -z "$H" ] && H="/home/$U"
cat >/opt/pluslokaal-kiosk.sh <<'K'
#!/bin/bash
xset -dpms 2>/dev/null; xset s off 2>/dev/null; unclutter -idle 1 &
B="$(command -v chromium-browser || command -v chromium)"
while true; do
  "$B" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
       --incognito --check-for-update-interval=31536000 http://localhost/ || true
  sleep 3
done
K
chmod 755 /opt/pluslokaal-kiosk.sh
cat >"$H/.xinitrc" <<'X'
#!/bin/sh
openbox-session &
exec /opt/pluslokaal-kiosk.sh
X
chown "$U:$U" "$H/.xinitrc"; chmod 755 "$H/.xinitrc"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat >/etc/systemd/system/getty@tty1.service.d/override.conf <<O
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $U --noclear %I \$TERM
O
PROF="$H/.bash_profile"
if ! grep -q pluslokaal-kiosk-startx "$PROF" 2>/dev/null; then
cat >>"$PROF" <<'P'
# pluslokaal-kiosk-startx
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then exec startx; fi
P
fi
chown "$U:$U" "$PROF" 2>/dev/null || true
# Alleen naar grafische modus als er echt een browser is geinstalleerd - anders headless laten.
if command -v chromium-browser >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1; then
  systemctl set-default graphical.target 2>/dev/null || true
fi
'''.replace('__USER__', user)

def _firstboot_sh(server, user):
    """First-boot-script: zet ZO SNEL MOGELIJK de agent (en dus de webinterface met voortgang) op -
    daarvoor is alleen het al aanwezige python3 nodig. Zware stappen (CUPS, RMM) draaien daarna met
    time-outs, zodat een hapering de webinterface nooit blokkeert. Statusregels sturen de voortgangs-
    pagina aan."""
    kiosk = 'yes' if _kiosk_enabled() else 'no'
    return f"""#!/usr/bin/env bash
# PLUSLokaal Print-Agent - eerste installatie (met live voortgang op de webinterface).
D=/etc/pluslokaal-agent
mkdir -p /opt/pluslokaal-agent "$D"
S(){{ printf '{{"step":"%s","pct":%s,"done":%s}}\\n' "$1" "$2" "${{3:-false}}" > "$D/setup-status.json"; }}
S "Systeem voorbereiden" 5
# wacht kort op netwerk (max ~60s)
for i in $(seq 1 30); do
  python3 -c "import socket;socket.create_connection(('pluslokaal.com',443),3)" 2>/dev/null && break
  sleep 2
done
S "Print-agent installeren" 20
python3 - <<'PY' || true
import urllib.request
open("/opt/pluslokaal-agent/pluslokaal_agent.py","wb").write(
    urllib.request.urlopen("{server}/api/agent/download", timeout=60).read())
PY
chmod 755 /opt/pluslokaal-agent/pluslokaal_agent.py
python3 /opt/pluslokaal-agent/pluslokaal_agent.py --install || true
# ↑ vanaf hier is de webinterface bereikbaar en toont deze voortgang
S "Printersoftware (CUPS) installeren" 50
export DEBIAN_FRONTEND=noninteractive
timeout 180 apt-get update || true
timeout 600 apt-get install -y --no-install-recommends cups cups-client || true
usermod -aG lpadmin {user} 2>/dev/null || true
cupsctl --remote-admin 2>/dev/null || true
S "Beheer-software installeren" 75
[ -f /opt/pluslokaal-rmm-install.sh ] && timeout 900 bash /opt/pluslokaal-rmm-install.sh || true
if [ "{kiosk}" = "yes" ] && [ -f /opt/pluslokaal-kiosk-install.sh ]; then timeout 900 bash /opt/pluslokaal-kiosk-install.sh || true; fi
S "Afronden" 100 true
"""

def _agent_userdata_text():
    """De generieke cloud-init user-data - gedeeld door de download-route en de .img-bouwer.
    Bewust GEEN 'packages:' met apt-blokkers vooraf: het first-boot-script haalt de agent zelf op
    (alleen python3 nodig) zodat de webinterface met voortgang meteen bereikbaar is."""
    import base64 as _b64
    server = 'https://pluslokaal.com'
    wf = [('/opt/pluslokaal-firstboot.sh',
           _b64.b64encode(_firstboot_sh(server, 'ubuntu').encode()).decode())]
    rmm = (get_setting('agent_rmm_cmd', '') or '').strip()
    if rmm:
        script = rmm if rmm.startswith('#!') else '#!/usr/bin/env bash\n' + rmm
        wf.append(('/opt/pluslokaal-rmm-install.sh', _b64.b64encode(script.encode()).decode()))
    if _kiosk_enabled():
        wf.append(('/opt/pluslokaal-kiosk-install.sh',
                   _b64.b64encode(_kiosk_install_sh('ubuntu').encode()).decode()))
    write_files = "write_files:\n" + ''.join(
        ("  - path: %s\n    permissions: '0700'\n    encoding: b64\n    content: %s\n" % (p, b))
        for p, b in wf)
    ud = f"""#cloud-config
# PLUSLokaal Print-Agent - generieke installatie (voor elke winkel gelijk).
# Na de eerste boot: open http://<pi-adres>/ - je ziet de installatie-voortgang, daarna koppel je de winkel.
hostname: pluslokaal-agent
package_update: false
{write_files}runcmd:
  - [bash, /opt/pluslokaal-firstboot.sh]
"""
    return ud

# ─── Kant-en-klaar .IMG bouwen (flashen → aansluiten → klaar) ─────────────────
# We nemen het officiële Ubuntu Server-image voor de Pi en bakken onze user-data (agent + RMM) er
# direct in (mcopy op de FAT-bootpartitie - geen root/loop-mounts nodig). Het resultaat is een
# .img.gz die de Raspberry Pi Imager direct kan flashen ("Gebruik eigen bestand").
_IMG_DIR = os.path.join(os.path.dirname(__file__), 'instance', 'agent-img')
_img_state = {'status': 'idle', 'message': '', 'error': None}
_img_lock = threading.Lock()

def _img_artifact():
    p = os.path.join(_IMG_DIR, 'pluslokaal-agent.img.gz')
    return p if os.path.exists(p) else None

def _mbr_part1_offset(img_path):
    """Byte-offset van partitie 1 (de FAT 'system-boot') uit de MBR-partitietabel."""
    with open(img_path, 'rb') as fh:
        mbr = fh.read(512)
    if mbr[510:512] != b'\x55\xaa':
        raise RuntimeError('geen geldige MBR in basis-image')
    lba = int.from_bytes(mbr[446 + 8:446 + 12], 'little')
    if not lba:
        raise RuntimeError('partitie 1 niet gevonden')
    return lba * 512

def _img_find_base_url():
    """Zoek de actuele Ubuntu Server preinstalled arm64+raspi-image op cdimage.ubuntu.com."""
    import urllib.request
    for rel in ('24.04', 'noble'):
        idx = f'https://cdimage.ubuntu.com/releases/{rel}/release/'
        try:
            html = urllib.request.urlopen(idx, timeout=30).read().decode(errors='replace')
            m = re.findall(r'href="(ubuntu-[\d.]+-preinstalled-server-arm64\+raspi\.img\.xz)"', html)
            if m:
                return idx + sorted(set(m))[-1]
        except Exception:
            continue
    raise RuntimeError('kon het Ubuntu-basisimage niet vinden op cdimage.ubuntu.com')

def _mbr_part2(img_path):
    """Byte-offset en grootte van partitie 2 (de ext4-root) uit de MBR."""
    with open(img_path, 'rb') as fh:
        mbr = fh.read(512)
    e = mbr[446 + 16:446 + 32]
    lba = int.from_bytes(e[8:12], 'little')
    cnt = int.from_bytes(e[12:16], 'little')
    if not (lba and cnt):
        raise RuntimeError('ext4-rootpartitie niet gevonden')
    return lba * 512, cnt * 512

def _firstboot_baked_sh(server):
    """First-boot voor het KANT-EN-KLARE image: de agent draait al (gebakken + aangezet), dus we
    installeren alleen nog de printersoftware (CUPS) en de beheer-agent (RMM), met time-outs."""
    return f"""#!/usr/bin/env bash
# PLUSLokaal - de agent is al kant-en-klaar geinstalleerd; alleen CUPS + RMM nog.
export DEBIAN_FRONTEND=noninteractive
timeout 180 apt-get update || true
timeout 600 apt-get install -y --no-install-recommends cups cups-client || true
usermod -aG lpadmin ubuntu 2>/dev/null || true
cupsctl --remote-admin 2>/dev/null || true
[ -f /opt/pluslokaal-rmm-install.sh ] && timeout 900 bash /opt/pluslokaal-rmm-install.sh || true
touch /etc/pluslokaal-agent/.firstboot-done
"""

def _agent_userdata_baked_text():
    """Minimale cloud-init voor het gebakken image: hostnaam + de first-boot (CUPS/RMM). De agent en
    z'n service zitten al in het image, dus die hoeft hier niet meer geinstalleerd te worden."""
    return ("#cloud-config\n"
            "# PLUSLokaal Print-Agent - kant-en-klaar image (agent draait al bij de eerste boot).\n"
            "hostname: pluslokaal-agent\n"
            "package_update: false\n"
            "runcmd:\n"
            "  - [bash, /opt/pluslokaal-firstboot.sh]\n")

def _bake_agent_into_rootfs(build):
    """Bak de agent + de AANGEZETTE systemd-service (+ first-boot & RMM) kant-en-klaar in de ext4-root
    met debugfs (zonder mounten). Zo start de webinterface direct bij de eerste boot - geen download of
    --install meer nodig, en dus niets dat kan blokkeren."""
    server = 'https://pluslokaal.com'
    off, size = _mbr_part2(build)
    root = os.path.join(_IMG_DIR, 'root.ext4')
    _img_state.update(message='Rootpartitie uitlezen…')
    subprocess.run(['dd', f'if={build}', f'of={root}', 'bs=512',
                    f'skip={off // 512}', f'count={size // 512}', 'status=none'],
                   check=True, timeout=1200)
    # bestanden voorbereiden
    tmpd = os.path.join(_IMG_DIR, '_bake')
    shutil.rmtree(tmpd, ignore_errors=True); os.makedirs(tmpd)
    unit = os.path.join(tmpd, 'unit'); open(unit, 'w').write(
        "[Unit]\nDescription=PLUSLokaal Print-Agent\nAfter=network-online.target\n"
        "[Service]\nExecStart=/usr/bin/python3 /opt/pluslokaal-agent/pluslokaal_agent.py\n"
        "Restart=always\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n")
    fb = os.path.join(tmpd, 'firstboot'); open(fb, 'w').write(_firstboot_baked_sh(server))
    cmds = [
        'mkdir /opt/pluslokaal-agent',
        'mkdir /etc/pluslokaal-agent',
        f'write {_AGENT_FILE} /opt/pluslokaal-agent/pluslokaal_agent.py',
        'sif /opt/pluslokaal-agent/pluslokaal_agent.py mode 0100755',
        f'write {unit} /etc/systemd/system/pluslokaal-agent.service',
        'symlink /etc/systemd/system/multi-user.target.wants/pluslokaal-agent.service /etc/systemd/system/pluslokaal-agent.service',
        f'write {fb} /opt/pluslokaal-firstboot.sh',
        'sif /opt/pluslokaal-firstboot.sh mode 0100755',
    ]
    rmm = (get_setting('agent_rmm_cmd', '') or '').strip()
    if rmm:
        script = rmm if rmm.startswith('#!') else '#!/usr/bin/env bash\n' + rmm
        rf = os.path.join(tmpd, 'rmm'); open(rf, 'w').write(script)
        cmds += [f'write {rf} /opt/pluslokaal-rmm-install.sh', 'sif /opt/pluslokaal-rmm-install.sh mode 0100700']
    cmds.append('quit')
    cmdfile = os.path.join(tmpd, 'cmds'); open(cmdfile, 'w').write('\n'.join(cmds) + '\n')
    _img_state.update(message='Agent kant-en-klaar in het image bakken…')
    subprocess.run(['debugfs', '-w', '-f', cmdfile, root], check=True, timeout=600, capture_output=True)
    subprocess.run(['e2fsck', '-fy', root], timeout=600)   # checksums herstellen (exit 1 = gecorrigeerd)
    _img_state.update(message='Rootpartitie terugschrijven…')
    subprocess.run(['dd', f'if={root}', f'of={build}', 'bs=512',
                    f'seek={off // 512}', 'conv=notrunc', 'status=none'],
                   check=True, timeout=1200)
    os.remove(root); shutil.rmtree(tmpd, ignore_errors=True)

def _img_build_worker():
    import urllib.request
    try:
        os.makedirs(_IMG_DIR, exist_ok=True)
        base_img = os.path.join(_IMG_DIR, 'base.img')
        if not os.path.exists(base_img):
            _img_state.update(message='Basis-image zoeken…')
            url = _img_find_base_url()
            base_xz = os.path.join(_IMG_DIR, 'base.img.xz')
            _img_state.update(message='Basis-image downloaden (±1,2 GB)…')
            req = urllib.request.Request(url, headers={'User-Agent': 'pluslokaal'})
            with urllib.request.urlopen(req, timeout=120) as r, open(base_xz + '.part', 'wb') as out:
                done = 0
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk); done += len(chunk)
                    if done % (50 * 1024 * 1024) < 1024 * 1024:
                        _img_state.update(message=f'Basis-image downloaden… {done // (1024*1024)} MB')
            os.replace(base_xz + '.part', base_xz)
            _img_state.update(message='Basis-image uitpakken…')
            subprocess.run(['xz', '-d', '-k', '-f', base_xz], check=True, timeout=1800)
        _img_state.update(message='Eigen image samenstellen…')
        build = os.path.join(_IMG_DIR, 'build.img')
        shutil.copyfile(base_img, build)
        # Agent + service KANT-EN-KLAAR in de rootpartitie bakken (start direct bij boot).
        with app.app_context():
            _bake_agent_into_rootfs(build)
            ud_text = _agent_userdata_baked_text()
        offset = _mbr_part1_offset(build)
        udf = os.path.join(_IMG_DIR, 'user-data.tmp')
        open(udf, 'w').write(ud_text)
        subprocess.run(['mcopy', '-o', '-i', f'{build}@@{offset}', udf, '::user-data'],
                       check=True, timeout=300, capture_output=True)
        os.remove(udf)
        _img_state.update(message='Comprimeren (kan een paar minuten duren)…')
        out_gz = os.path.join(_IMG_DIR, 'pluslokaal-agent.img.gz')
        with open(build, 'rb') as fin:
            p = subprocess.Popen(['gzip', '-1', '-c'], stdin=fin, stdout=open(out_gz + '.part', 'wb'))
            p.wait(timeout=1800)
            if p.returncode != 0:
                raise RuntimeError('gzip faalde')
        os.replace(out_gz + '.part', out_gz)
        os.remove(build)
        _img_state.update(status='done', message='Klaar', error=None)
    except Exception as e:
        _img_state.update(status='error', message='', error=str(e)[:300])

# ─── Kant-en-klare installer-ISO voor x86 mini-pc's (Wyse/Futro/HP/Lenovo…) ───
# Zelfde idee als het Pi-image, maar voor gewone (refurb) mini-pc's: we nemen de officiële
# Ubuntu Server ISO en bakken er een volledig automatische installatie in (autoinstall/
# NoCloud). USB flashen → opstarten → machine installeert zichzelf (schijf wordt GEWIST),
# daarna zelfde ervaring als de Pi: IP intypen, sleutel plakken, klaar.
_iso_state = {'status': 'idle', 'message': '', 'error': None}

def _iso_artifact():
    p = os.path.join(_IMG_DIR, 'pluslokaal-installer.iso')
    return p if os.path.exists(p) else None

def _agent_autoinstall_text():
    """Ubuntu autoinstall (NoCloud) user-data voor mini-pc's: volautomatische installatie +
    first-boot-setup (agent + RMM), identiek eindresultaat als het Pi-image."""
    import base64 as _b64, crypt as _crypt
    server = 'https://pluslokaal.com'
    # lokale login op de mini-pc (voor noodgevallen; RMM geeft normaliter de shell)
    pw_hash = _crypt.crypt('PLUSlokaal!2026', _crypt.mksalt(_crypt.METHOD_SHA512))
    setup = f"""#!/bin/bash
# PLUSLokaal first-boot setup (mini-pc) - agent eerst (voortgang zichtbaar), rest met time-outs.
D=/etc/pluslokaal-agent
mkdir -p /opt/pluslokaal-agent "$D"
S(){{ printf '{{"step":"%s","pct":%s,"done":%s}}\\n' "$1" "$2" "${{3:-false}}" > "$D/setup-status.json"; }}
S "Print-agent installeren" 20
python3 - <<'PY' || curl -fsSL {server}/api/agent/download -o /opt/pluslokaal-agent/pluslokaal_agent.py
import urllib.request
open("/opt/pluslokaal-agent/pluslokaal_agent.py","wb").write(
    urllib.request.urlopen("{server}/api/agent/download", timeout=60).read())
PY
chmod 755 /opt/pluslokaal-agent/pluslokaal_agent.py
python3 /opt/pluslokaal-agent/pluslokaal_agent.py --install || true
S "Printersoftware instellen" 55
usermod -aG lpadmin plus || true
cupsctl --remote-admin || true
S "Beheer-software installeren" 75
[ -f /opt/pluslokaal-rmm-install.sh ] && timeout 900 bash /opt/pluslokaal-rmm-install.sh || true
[ -f /opt/pluslokaal-kiosk-install.sh ] && timeout 900 bash /opt/pluslokaal-kiosk-install.sh || true
S "Afronden" 100 true
touch /etc/pluslokaal-agent/.firstboot-done
"""
    unit = """[Unit]
Description=PLUSLokaal first-boot setup
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/etc/pluslokaal-agent/.firstboot-done

[Service]
Type=oneshot
ExecStart=/opt/pluslokaal-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    setup_b64 = _b64.b64encode(setup.encode()).decode()
    unit_b64 = _b64.b64encode(unit.encode()).decode()
    late = [
        f"curtin in-target -- bash -c \"echo {setup_b64} | base64 -d > /opt/pluslokaal-setup.sh && chmod 700 /opt/pluslokaal-setup.sh\"",
        f"curtin in-target -- bash -c \"echo {unit_b64} | base64 -d > /etc/systemd/system/pluslokaal-firstboot.service\"",
        "curtin in-target -- systemctl enable pluslokaal-firstboot.service",
    ]
    rmm = (get_setting('agent_rmm_cmd', '') or '').strip()
    if rmm:
        script = rmm if rmm.startswith('#!') else '#!/usr/bin/env bash\n' + rmm
        rmm_b64 = _b64.b64encode(script.encode()).decode()
        late.insert(0, f"curtin in-target -- bash -c \"echo {rmm_b64} | base64 -d > /opt/pluslokaal-rmm-install.sh && chmod 700 /opt/pluslokaal-rmm-install.sh\"")
    if _kiosk_enabled():
        kiosk_b64 = _b64.b64encode(_kiosk_install_sh('plus').encode()).decode()
        late.insert(0, f"curtin in-target -- bash -c \"echo {kiosk_b64} | base64 -d > /opt/pluslokaal-kiosk-install.sh && chmod 700 /opt/pluslokaal-kiosk-install.sh\"")
    late_yaml = '\n'.join(f'    - {json.dumps(c)}' for c in late)
    return f"""#cloud-config
# PLUSLokaal Print-Agent - volautomatische installatie voor mini-pc's.
# LET OP: de schijf van de machine wordt volledig GEWIST.
autoinstall:
  version: 1
  locale: nl_NL.UTF-8
  keyboard:
    layout: us
  identity:
    hostname: pluslokaal-agent
    username: plus
    password: {json.dumps(pw_hash)}
  ssh:
    install-server: true
    allow-pw: true
  storage:
    layout:
      name: direct
  packages:
    - python3
    - cups
    - cups-client
    - curl
  late-commands:
{late_yaml}
  shutdown: reboot
"""

def _iso_find_base_url():
    import urllib.request
    idx = 'https://releases.ubuntu.com/24.04/'
    html = urllib.request.urlopen(idx, timeout=30).read().decode(errors='replace')
    m = re.findall(r'href="(ubuntu-[\d.]+-live-server-amd64\.iso)"', html)
    if not m:
        raise RuntimeError('kon de Ubuntu Server ISO niet vinden op releases.ubuntu.com')
    return idx + sorted(set(m))[-1]

def _iso_build_worker():
    import urllib.request
    try:
        os.makedirs(_IMG_DIR, exist_ok=True)
        base_iso = os.path.join(_IMG_DIR, 'base-amd64.iso')
        if not os.path.exists(base_iso):
            _iso_state.update(message='Basis-ISO zoeken…')
            url = _iso_find_base_url()
            _iso_state.update(message='Basis-ISO downloaden (±3 GB)…')
            req = urllib.request.Request(url, headers={'User-Agent': 'pluslokaal'})
            with urllib.request.urlopen(req, timeout=120) as r, open(base_iso + '.part', 'wb') as out:
                done = 0
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk); done += len(chunk)
                    if done % (100 * 1024 * 1024) < 1024 * 1024:
                        _iso_state.update(message=f'Basis-ISO downloaden… {done // (1024*1024)} MB')
            os.replace(base_iso + '.part', base_iso)
        # grub.cfg uit de ISO halen en 'autoinstall' aan de kernelregels toevoegen
        _iso_state.update(message='Installer samenstellen…')
        work = os.path.join(_IMG_DIR, 'iso-work')
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(os.path.join(work, 'nocloud'), exist_ok=True)
        subprocess.run(['xorriso', '-osirrox', 'on', '-indev', base_iso,
                        '-extract', '/boot/grub/grub.cfg', os.path.join(work, 'grub.cfg')],
                       check=True, timeout=300, capture_output=True)
        os.chmod(os.path.join(work, 'grub.cfg'), 0o644)
        g = open(os.path.join(work, 'grub.cfg')).read()
        g = g.replace('linux\t/casper/vmlinuz  ---',
                      'linux\t/casper/vmlinuz autoinstall ds=nocloud\\;s=/cdrom/nocloud/  ---')
        g = g.replace('linux    /casper/vmlinuz  ---',
                      'linux    /casper/vmlinuz autoinstall ds=nocloud\\;s=/cdrom/nocloud/  ---')
        open(os.path.join(work, 'grub.cfg'), 'w').write(g)
        with app.app_context():
            open(os.path.join(work, 'nocloud', 'user-data'), 'w').write(_agent_autoinstall_text())
        open(os.path.join(work, 'nocloud', 'meta-data'), 'w').write('')
        out_iso = os.path.join(_IMG_DIR, 'pluslokaal-installer.iso')
        if os.path.exists(out_iso):
            os.remove(out_iso)
        subprocess.run(['xorriso', '-indev', base_iso, '-outdev', out_iso,
                        '-boot_image', 'any', 'replay',
                        '-map', os.path.join(work, 'nocloud'), '/nocloud',
                        '-map', os.path.join(work, 'grub.cfg'), '/boot/grub/grub.cfg'],
                       check=True, timeout=1800, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
        _iso_state.update(status='done', message='Klaar', error=None)
    except subprocess.CalledProcessError as e:
        _iso_state.update(status='error', message='',
                          error=(e.stderr or b'')[-300:].decode(errors='replace') or str(e))
    except Exception as e:
        _iso_state.update(status='error', message='', error=str(e)[:300])

@app.route('/agent/iso-build', methods=['POST'])
@login_required
def agent_iso_build():
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    with _img_lock:
        if _iso_state['status'] == 'building':
            flash('Er wordt al een installer-ISO gebouwd.', 'error')
        else:
            _iso_state.update(status='building', message='Starten…', error=None)
            threading.Thread(target=_iso_build_worker, daemon=True).start()
            flash('ISO-bouw gestart - ververs deze pagina voor de voortgang.', 'success')
            log_action('agent_iso_build', 'mini-pc installer-ISO bouwen gestart')
    return redirect(request.form.get('next') or url_for('filialen'))

@app.route('/agent/iso-download')
@login_required
def agent_iso_download():
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    p = _iso_artifact()
    if not p:
        abort(404)
    return send_file(p, mimetype='application/x-iso9660-image', as_attachment=True,
                     download_name='pluslokaal-installer.iso')

@app.route('/agent/img-build', methods=['POST'])
@login_required
def agent_img_build():
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    with _img_lock:
        if _img_state['status'] == 'building':
            flash('Er wordt al een image gebouwd.', 'error')
        else:
            _img_state.update(status='building', message='Starten…', error=None)
            threading.Thread(target=_img_build_worker, daemon=True).start()
            flash('Image-bouw gestart - ververs deze pagina voor de voortgang.', 'success')
            log_action('agent_img_build', 'kant-en-klaar Pi-image bouwen gestart')
    return redirect(request.form.get('next') or url_for('filialen'))

@app.route('/agent/img-download')
@login_required
def agent_img_download():
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    p = _img_artifact()
    if not p:
        abort(404)
    return send_file(p, mimetype='application/gzip', as_attachment=True,
                     download_name='pluslokaal-agent.img.gz')

@app.route('/filiaal/<int:nummer>/agent-userdata')
@login_required
def agent_userdata(nummer):
    """Genereer een kant-en-klaar cloud-init 'user-data'-bestand voor op de SD-kaart (Ubuntu Server
    voor Raspberry Pi). De winkel-sleutel zit er al in: SD-kaart flashen → dit bestand op de
    'system-boot'-partitie over het bestaande 'user-data' heen zetten → Pi aansluiten → na de eerste
    boot is de agent volledig geïnstalleerd en hoef je alleen nog printers te kiezen op :8080."""
    u = get_current_user()
    if not u or u.role != 'admin':
        abort(403)
    f = Filiaal.query.filter_by(nummer=nummer).first_or_404()
    if not f.agent_key:
        flash('Genereer eerst een agent-sleutel.', 'error')
        return redirect(url_for('filiaal_detail', nummer=nummer))
    server = 'https://pluslokaal.com'
    cfg = json.dumps({'server': server, 'key': f.agent_key, 'label_device': '', 'doc_queue': '',
                      'tray_map': {}, 'poll_interval': 3, 'web_port': 8080, 'auto_update': True},
                     indent=2)
    cfg_ind = '\n'.join('      ' + line for line in cfg.splitlines())
    ud = f"""#cloud-config
# PLUSLokaal Print-Agent - winkel {f.nummer} ({f.naam or ''})
# Zet dit bestand als 'user-data' op de system-boot-partitie van een vers geflashte
# Ubuntu Server (Raspberry Pi) SD-kaart. Bij de eerste boot installeert alles zichzelf.
hostname: pluslokaal-agent-{f.nummer}
package_update: true
packages:
  - python3
  - cups
  - cups-client
write_files:
  - path: /etc/pluslokaal-agent/config.json
    permissions: '0600'
    content: |
{cfg_ind}
runcmd:
  - mkdir -p /opt/pluslokaal-agent
  - curl -fsSL {server}/api/agent/download -o /opt/pluslokaal-agent/pluslokaal_agent.py
  - chmod 755 /opt/pluslokaal-agent/pluslokaal_agent.py
  - python3 /opt/pluslokaal-agent/pluslokaal_agent.py --install
  - usermod -aG lpadmin ubuntu || true
  - cupsctl --remote-admin || true
"""
    log_action('agent_userdata', f'SD-bestand gedownload voor winkel {f.nummer}', filiaal=f.nummer)
    return Response(ud, mimetype='text/yaml',
                    headers={'Content-Disposition': 'attachment; filename="user-data"'})

def _build_label_payload(items, f, opts, quantities=None):
    """Bouw de printer-payload voor een lijst items volgens de winkel-printertaal. Geeft (bytes, aantal)."""
    proto = (f.printer_protocol or 'tspl').lower()
    Lw = f.printer_label_w or 45.0; Lh = f.printer_label_h or 40.0
    dpi = int(f.printer_dpi or 300)
    logo = _label_logo_path()
    grouped = {}
    for it in items:
        bc = str(it.get('barcode') or '')
        g = grouped.setdefault(bc or id(it), {'name': it.get('name', ''), 'barcode': bc,
             'price': it.get('price'), 'old_price': it.get('old_price'), 'qty': 0})
        g['qty'] += int(it.get('qty') or 1)
    payload = bytearray(); total = 0
    for key, g in grouped.items():
        qty = int((quantities or {}).get(g['barcode'], g['qty']) or 0)
        if qty <= 0:
            continue
        show_logo = bool(g and logo)
        if proto == 'tspl':
            one = _labelimage.build_tspl_graphic(g, opts, Lw, Lh, dpi, logo, show_logo, copies=qty) \
                  or _labelgen.build_label('tspl', g, opts, Lw, Lh, dpi, copies=qty)
        else:
            one = _labelgen.build_label(proto, g, opts, Lw, Lh, dpi, copies=qty)
        if one is None:
            continue
        payload += one; total += qty
    return bytes(payload), total

@app.route('/labels/<int:job_id>/print-netwerk', methods=['POST'])
@login_required
def label_print_network(job_id):
    u = get_current_user()
    job = LabelJob.query.get_or_404(job_id)
    if not is_superadmin(u) and job.filiaal != u.filiaal:
        abort(403)
    f = Filiaal.query.filter_by(nummer=job.filiaal).first()
    if not f or not (f.printer_ip or _agent_online(f)):
        return jsonify({'error': 'Voor deze winkel is geen printer-IP ingesteld (Beheer → Printers).'}), 400
    # IP-beleid: sommige accounts mogen alleen printen vanaf het winkelnetwerk
    if getattr(u, 'access_policy', 'anywhere') == 'ip_print':
        cip = client_ip()
        if not ip_in_list(cip, f.allowed_ips or ''):
            log_action('print_geblokkeerd_ip', f'IP {cip} niet op winkelnetwerk', filiaal=f.nummer)
            return jsonify({'error': f'Printen op de winkelprinter kan alleen vanaf het winkelnetwerk. Jouw IP: {cip}.'}), 403
    items = json.loads(job.items_json or '[]')
    opts = {'price_unit': job.price_unit or 'stuk', 'extra_line1': job.extra_line1,
            'extra_line2': job.extra_line2, 'show_date': job.show_date,
            'today': datetime.now().strftime('%d-%m-%Y')}
    quantities = (request.get_json(silent=True) or {}).get('quantities')
    payload, total = _build_label_payload(items, f, opts, quantities)
    if total == 0:
        return jsonify({'error': 'Geen labels om te printen.'}), 400
    try:
        _send_label(f, payload)
    except OSError as e:
        log_action('print_mislukt', f'{f.printer_ip}:{f.printer_port} ({e.__class__.__name__})', filiaal=f.nummer)
        return jsonify({'error': f'Kan {f.printer_ip}:{f.printer_port} niet bereiken ({e.__class__.__name__}). Gebruik anders browserprint.'}), 502
    job.printed_at = datetime.now(); job.status = 'geprint'; db.session.commit()
    log_action('print_netwerk', f'{total} label(s) → {f.printer_ip}:{f.printer_port} - "{job.name}"', filiaal=f.nummer)
    return jsonify({'success': True, 'sent': total,
                    'message': f'{total} label(s) verstuurd naar {f.printer_name or "printer"} '
                               f'({f.printer_ip}:{f.printer_port}, {(f.printer_protocol or "tspl").upper()}). '
                               f'Komt er niets uit? Dan spreekt de printer die taal niet - kies een andere in Beheer → Filialen.'})

# ─── WINKELPRINTER (schapkaarten/scankaarten, IPP - A3/A4-kantoorprinter) ──────
# Toshiba e-STUDIO en soortgelijke moderne kantoorprinters spreken "IPP Everywhere"
# (AirPrint/Mopria): ze nemen kant-en-klare PDF's aan zonder fabrikant-driver, en
# rapporteren hun papierladen als media-source keywords (tray-1..tray-4, by-pass-tray,
# auto). Daarom bouwen we hier zelf een minimale IPP Print-Job-request (net als de
# rauwe TSPL-payload hierboven) i.p.v. een CUPS-driver/PPD te vereisen.
_DOC_TRAY_FORMATS = ['a3_staand', 'a3_liggend', 'a4_staand', 'a5_staand', 'sk_maxi']
_DOC_MEDIA = {
    'a3_staand':  'iso_a3_297x420mm', 'a3_liggend': 'iso_a3_297x420mm',
    'a4_staand':  'iso_a4_210x297mm', 'a5_staand':  'iso_a5_148x210mm',
    'sk_maxi':    'iso_a4_210x297mm',   # SK Maxi = 4-up op 1 A4-liggend vel
}
_DOC_ORIENT = {'a3_liggend': 'landscape', 'sk_maxi': 'landscape'}
_DEFAULT_DOC_TRAYS = {'a3_staand': 'tray-4', 'a3_liggend': 'tray-4', 'sk_maxi': 'tray-3',
                      'a4_staand': 'auto', 'a5_staand': 'auto'}

def _doc_trays(f):
    """Winkel-specifieke lade-toewijzing per formaat (met fallback op de standaardverdeling)."""
    try:
        saved = json.loads(f.doc_printer_trays or '{}')
    except Exception:
        saved = {}
    out = dict(_DEFAULT_DOC_TRAYS)
    out.update({k: v for k, v in saved.items() if k in _DOC_TRAY_FORMATS and v})
    return out

def _printers_for_cards(cards):
    """Map {filiaal_nummer: {'name':..., 'ready':bool}} voor de winkels van deze kaarten.
    Zo weet elke kaart of er voor ZIJN EIGEN winkel een printer is ingesteld (niet die van de
    ingelogde gebruiker) - belangrijk voor de superadmin die kaarten van meerdere winkels ziet."""
    nums = {c.filiaal for c in cards if c.filiaal is not None}
    out = {}
    if not nums:
        return out
    for f in Filiaal.query.filter(Filiaal.nummer.in_(nums)).all():
        out[f.nummer] = {'name': f.doc_printer_name or 'de winkelprinter',
                         'ready': bool(f.doc_printer_ip or _agent_online(f))}
    return out

def _card_format_key(card):
    """Herleid de interne formaat-sleutel (bv. 'a3_liggend') van een Card."""
    try:
        d = json.loads(card.kaart_data or '{}')
        if d.get('formaat'):
            return d['formaat']
    except Exception:
        pass
    if (card.formaat or '').lower().startswith('scankaart'):
        return 'sk_maxi'
    inv = {v: k for k, v in FORMAAT_LABELS.items()}
    return inv.get(card.formaat, 'a3_liggend')

_IPP_TAG = {'charset': 0x47, 'naturalLanguage': 0x48, 'uri': 0x45, 'nameWithoutLanguage': 0x42,
            'mimeMediaType': 0x49, 'keyword': 0x44, 'integer': 0x21, 'enum': 0x23}

def _ipp_attr(kind, name, value):
    tag = _IPP_TAG[kind]
    if kind in ('integer', 'enum'):
        vb = value.to_bytes(4, 'big', signed=True)
    else:
        vb = str(value).encode('utf-8')
    nb = name.encode('ascii')
    return bytes([tag]) + len(nb).to_bytes(2, 'big') + nb + len(vb).to_bytes(2, 'big') + vb

def _ipp_build_print_job(printer_uri, pdf_bytes, media, media_source, orientation, copies, job_name, user_name='pluslokaal'):
    body = bytearray()
    body += b'\x01\x01'                                    # IPP-versie 1.1
    body += (0x0002).to_bytes(2, 'big')                     # operation-id: Print-Job
    body += (1).to_bytes(4, 'big')                          # request-id
    body += b'\x01'                                         # operation-attributes-tag
    body += _ipp_attr('charset', 'attributes-charset', 'utf-8')
    body += _ipp_attr('naturalLanguage', 'attributes-natural-language', 'en')
    body += _ipp_attr('uri', 'printer-uri', printer_uri)
    body += _ipp_attr('nameWithoutLanguage', 'requesting-user-name', user_name)
    body += _ipp_attr('nameWithoutLanguage', 'job-name', (job_name or 'pluslokaal')[:127])
    body += _ipp_attr('mimeMediaType', 'document-format', 'application/pdf')
    body += b'\x02'                                         # job-attributes-tag
    body += _ipp_attr('integer', 'copies', max(1, int(copies)))
    if media:
        body += _ipp_attr('keyword', 'media', media)
    if media_source and media_source != 'auto':
        body += _ipp_attr('keyword', 'media-source', media_source)
    orient_enum = {'portrait': 3, 'landscape': 4}.get(orientation)
    if orient_enum:
        body += _ipp_attr('enum', 'orientation-requested', orient_enum)
    body += b'\x03'                                         # end-of-attributes-tag
    body += pdf_bytes
    return bytes(body)

def _print_card_network(ip, port, pdf_bytes, media, media_source, orientation, copies, job_name, path='/ipp/print', timeout=20):
    """Stuur een PDF als IPP Print-Job naar een netwerkprinter. Raise OSError bij falen."""
    import http.client
    printer_uri = f'ipp://{ip}:{port}{path}'
    body = _ipp_build_print_job(printer_uri, pdf_bytes, media, media_source, orientation, copies, job_name)
    conn = http.client.HTTPConnection(ip, int(port), timeout=timeout)
    try:
        conn.request('POST', path, body=body, headers={'Content-Type': 'application/ipp',
                                                         'Content-Length': str(len(body))})
        resp = conn.getresponse()
        data = resp.read()
    finally:
        conn.close()
    if resp.status != 200 or len(data) < 8:
        raise OSError(f'ongeldig antwoord (HTTP {resp.status})')
    status_code = int.from_bytes(data[2:4], 'big')
    if status_code >= 0x0400:
        raise OSError(f'printer wees de opdracht af (IPP 0x{status_code:04x})')
    return status_code

def _ipp_parse(data):
    """Minimale IPP-response-parser → (ipp_status, {attr_naam: [waarden]}). Genoeg voor Get-Job-Attributes."""
    attrs = {}
    if not data or len(data) < 8:
        return None, attrs
    ipp_status = int.from_bytes(data[2:4], 'big')
    i, n, cur = 8, len(data), None
    while i < n:
        tag = data[i]; i += 1
        if tag <= 0x0F:                        # delimiter-tag (0x03 = end-of-attributes)
            if tag == 0x03:
                break
            cur = None
            continue
        if i + 2 > n: break
        nlen = int.from_bytes(data[i:i+2], 'big'); i += 2
        name = data[i:i+nlen].decode('utf-8', 'replace') if nlen else cur
        i += nlen
        if i + 2 > n: break
        vlen = int.from_bytes(data[i:i+2], 'big'); i += 2
        raw = data[i:i+vlen]; i += vlen
        if tag in (0x21, 0x23):                # integer / enum
            val = int.from_bytes(raw, 'big', signed=True) if raw else None
        elif tag == 0x22:                      # boolean
            val = bool(raw and raw[0])
        else:
            val = raw.decode('utf-8', 'replace')
        if nlen:
            cur = name
        attrs.setdefault(name, []).append(val)
    return ipp_status, attrs

def _ipp_send_print_job(ip, port, path, pdf_bytes, media, media_source, orientation, copies, job_name, timeout=30):
    """Verstuur een Print-Job en geef de door de printer toegekende job-id terug (of None). Raise OSError bij falen."""
    import http.client
    printer_uri = f'ipp://{ip}:{port}{path}'
    body = _ipp_build_print_job(printer_uri, pdf_bytes, media, media_source, orientation, copies, job_name)
    conn = http.client.HTTPConnection(ip, int(port), timeout=timeout)
    try:
        conn.request('POST', path, body=body, headers={'Content-Type': 'application/ipp',
                                                        'Content-Length': str(len(body))})
        resp = conn.getresponse(); data = resp.read()
    finally:
        conn.close()
    if resp.status != 200 or len(data) < 8:
        raise OSError(f'ongeldig antwoord (HTTP {resp.status})')
    ipp_status, attrs = _ipp_parse(data)
    if ipp_status is not None and ipp_status >= 0x0400:
        raise OSError(f'printer wees de opdracht af (IPP 0x{ipp_status:04x})')
    jids = attrs.get('job-id') or []
    return jids[0] if jids else None

def _ipp_get_job_attrs(ip, port, path, printer_job_id, timeout=10):
    """Vraag de status van een printer-job op → dict met o.a. job-state, job-impressions(-completed)."""
    import http.client
    printer_uri = f'ipp://{ip}:{port}{path}'
    body = bytearray()
    body += b'\x01\x01'
    body += (0x0009).to_bytes(2, 'big')          # Get-Job-Attributes
    body += (1).to_bytes(4, 'big')
    body += b'\x01'
    body += _ipp_attr('charset', 'attributes-charset', 'utf-8')
    body += _ipp_attr('naturalLanguage', 'attributes-natural-language', 'en')
    body += _ipp_attr('uri', 'printer-uri', printer_uri)
    body += _ipp_attr('integer', 'job-id', int(printer_job_id))
    body += _ipp_attr('nameWithoutLanguage', 'requesting-user-name', 'pluslokaal')
    body += b'\x03'
    conn = http.client.HTTPConnection(ip, int(port), timeout=timeout)
    try:
        conn.request('POST', path, body=bytes(body), headers={'Content-Type': 'application/ipp',
                                                               'Content-Length': str(len(body))})
        resp = conn.getresponse(); data = resp.read()
    finally:
        conn.close()
    _st, attrs = _ipp_parse(data)
    return attrs

def _ipp_cancel_job(ip, port, path, printer_job_id, timeout=10):
    """Annuleer een lopende/gequeuede printer-job (IPP Cancel-Job). Stil falen mag (job kan al weg zijn)."""
    import http.client
    printer_uri = f'ipp://{ip}:{port}{path}'
    body = bytearray()
    body += b'\x01\x01'
    body += (0x0008).to_bytes(2, 'big')          # Cancel-Job
    body += (1).to_bytes(4, 'big')
    body += b'\x01'
    body += _ipp_attr('charset', 'attributes-charset', 'utf-8')
    body += _ipp_attr('naturalLanguage', 'attributes-natural-language', 'en')
    body += _ipp_attr('uri', 'printer-uri', printer_uri)
    body += _ipp_attr('integer', 'job-id', int(printer_job_id))
    body += _ipp_attr('nameWithoutLanguage', 'requesting-user-name', 'pluslokaal')
    body += b'\x03'
    try:
        conn = http.client.HTTPConnection(ip, int(port), timeout=timeout)
        conn.request('POST', path, body=bytes(body), headers={'Content-Type': 'application/ipp',
                                                              'Content-Length': str(len(body))})
        conn.getresponse().read(); conn.close()
        return True
    except OSError:
        return False

class _PrintCancelled(Exception):
    """De gebruiker heeft de print-job geannuleerd."""

# IPP job-state (RFC 8011 §5.3.7): 3 pending, 4 pending-held, 5 processing, 6 processing-stopped,
# 7 canceled, 8 aborted, 9 completed.
_JOB_STATE_MSG = {3: 'In de wachtrij bij de printer…', 4: 'Vastgehouden door de printer…',
                  5: 'Wordt geprint…', 6: 'Printer gepauzeerd (controleer papier/lade)…',
                  7: 'Geannuleerd op de printer.', 8: 'Afgebroken door de printer.',
                  9: 'Klaar met printen.'}

# ─── Achtergrond-printtaken (schapkaarten/scankaarten/weekpakketten) met voortgang ────────────
# job_id -> {status:'running'|'done'|'error', percent, message, title, error, created_at}
# Print-jobs staan in de GEDEELDE store (sharedstate) zodat statuspolling/annuleren over meerdere
# gunicorn-workers heen werkt. Deze helpers houden dezelfde namen/semantiek als voorheen.
def _pj_set(job_id, **kw):
    sharedstate.job_set(job_id, **kw)

def _pj_get(job_id, default=None):
    return sharedstate.job_get(job_id, default)

def _print_jobs_cleanup(max_age=1800):
    sharedstate.job_cleanup('print', max_age, ('done', 'error', 'cancelled'))

def _pj_is_cancelled(job_id):
    return bool(sharedstate.job_field(job_id, 'cancel'))

def _abort_current_printer_job(job_id):
    """Annuleer (indien mogelijk) de printer-job die nu voor deze taak loopt."""
    j = sharedstate.job_get(job_id) or {}
    pjid = j.get('printer_job_id'); ip = j.get('ip'); port = j.get('port'); path = j.get('path')
    if pjid and ip:
        _ipp_cancel_job(ip, port, path or '/ipp/print', pjid)

def _poll_printer_job(ip, port, path, pjid, job_id, base, span, label):
    """Volg één printer-job en werk de voortgang bij tussen base..base+span procent.
    Raise _PrintCancelled zodra de gebruiker de taak annuleert (en annuleert 'm ook op de printer)."""
    _pj_set(job_id, printer_job_id=pjid)      # zodat de cancel-route deze job kan afbreken
    if not pjid:
        time.sleep(0.5)   # printer gaf geen job-id terug: taak is verstuurd, korte pauze
        return
    warned_stopped = 0
    for _ in range(90):                      # ~90s plafond per document
        if _pj_is_cancelled(job_id):
            _ipp_cancel_job(ip, port, path, pjid)
            raise _PrintCancelled()
        try:
            attrs = _ipp_get_job_attrs(ip, port, path, pjid)
        except OSError:
            return                            # niet meer op te vragen → aannemen dat 'ie loopt
        state = (attrs.get('job-state') or [None])[0]
        done = (attrs.get('job-impressions-completed') or [0])[0] or 0
        tot = (attrs.get('job-impressions') or [0])[0] or 0
        frac = min(1.0, done / tot) if tot else 0.0
        pct = min(base + span, base + int(span * frac))
        _pj_set(job_id, percent=max(pct, sharedstate.job_field(job_id, 'percent', 0)),
                message=f'{label}: {_JOB_STATE_MSG.get(state, "Bezig…")}')
        if state == 9:
            return
        if state in (7, 8):
            raise OSError('de printer heeft de taak geannuleerd of afgebroken')
        if state == 6:                        # processing-stopped (bv. verkeerde/lege lade)
            warned_stopped += 1
            if warned_stopped >= 8:           # blijft hangen → als "in wachtrij" afronden
                return
        time.sleep(1)

def _run_print_task(job_id, ip, port, path, docs, printer_label, filiaal=None):
    """Achtergrond-worker: verstuur 1+ documenten (elk naar z'n eigen lade) en houd voortgang bij.
    Elk document mag een eigen 'ip'/'port'/'path' hebben (bulk over meerdere winkelprinters)."""
    _pj_set(job_id, status='running', percent=3, message='Verbinden met de printer…')
    total = max(1, len(docs))
    # Winkel met een online print-agent (Pi)? Dan gaan de documenten via de agent (USB-printers),
    # niet rechtstreeks over het netwerk - dat kan van buitenaf immers niet.
    with app.app_context():
        f_agent = Filiaal.query.filter_by(nummer=filiaal).first() if filiaal else None
        use_agent = _agent_online(f_agent)
    try:
        for idx, d in enumerate(docs):
            if _pj_is_cancelled(job_id):
                raise _PrintCancelled()
            base = int(idx / total * 100)
            span = max(1, int(100 / total))
            _pj_set(job_id, percent=max(base + 1, sharedstate.job_field(job_id, 'percent', 0)),
                    message=f'{d["label"]}: versturen…')
            if use_agent and not d.get('ip'):        # per-doc eigen printer (bulk) blijft direct
                with app.app_context():
                    ajid = _agent_enqueue(filiaal, 'document', d['pdf'],
                                          {'media': d.get('media'), 'source': d.get('source'),
                                           'orient': d.get('orient'), 'copies': d.get('copies', 1),
                                           'job_name': d.get('job_name', 'pluslokaal'), 'label': d['label']})
                _agent_wait(ajid, job_id, base, span, d['label'])
                continue
            dip = d.get('ip', ip); dport = d.get('port', port); dpath = d.get('path', path)
            _pj_set(job_id, ip=dip, port=dport, path=dpath)   # zodat annuleren de juiste printer raakt
            pjid = _ipp_send_print_job(dip, dport, dpath, d['pdf'], d.get('media'), d.get('source'),
                                       d.get('orient'), d.get('copies', 1), d.get('job_name', 'pluslokaal'))
            _poll_printer_job(dip, dport, dpath, pjid, job_id, base, span, d['label'])
        skipped = sharedstate.job_field(job_id, 'skipped', 0) or 0
        extra = f' ({skipped} te klein/andere winkel overgeslagen.)' if skipped else ''
        _pj_set(job_id, status='done', percent=100, printer_job_id=None,
                message=f'Klaar - {total} verstuurd naar {printer_label}.{extra}')
        with app.app_context():
            log_action('print_netwerk_klaar', f'{total} doc(en) → {printer_label}', filiaal=filiaal)
    except _PrintCancelled:
        _pj_set(job_id, status='cancelled', percent=100, printer_job_id=None,
                message='Geannuleerd.')
        with app.app_context():
            log_action('print_geannuleerd', f'{printer_label}', filiaal=filiaal)
    except OSError as e:
        _pj_set(job_id, status='error', percent=100, error=str(e),
                message=f'Printen mislukt: {e}')
        with app.app_context():
            log_action('print_mislukt', f'{printer_label} ({e})', filiaal=filiaal)

def _enqueue_print(title, ip, port, path, docs, printer_label, filiaal=None):
    _print_jobs_cleanup()
    job_id = secrets.token_hex(12)
    sharedstate.job_create(job_id, 'print',
                           {'status': 'running', 'percent': 1, 'message': 'Bezig met starten…',
                            'title': title, 'error': None, 'created_at': time.time(),
                            'cancel': False, 'printer_job_id': None,
                            'ip': ip, 'port': port, 'path': path, 'filiaal': filiaal})
    threading.Thread(target=_run_print_task,
                     args=(job_id, ip, port, path, docs, printer_label, filiaal), daemon=True).start()
    return job_id

def _enqueue_demo_print(title, labels):
    """Gesimuleerde printtaak voor het demo-account: toont voortgang alsof er geprint wordt, maar
    er gaat NIETS naar een printer. Annuleren werkt gewoon."""
    _print_jobs_cleanup()
    job_id = secrets.token_hex(12)
    sharedstate.job_create(job_id, 'print',
                           {'status': 'running', 'percent': 1, 'title': title,
                            'message': 'Voorbereiden… (demo)', 'error': None,
                            'created_at': time.time(), 'cancel': False, 'printer_job_id': None,
                            'ip': None, 'port': None, 'path': None, 'filiaal': DEMO_FILIAAL, 'demo': True})
    steps = labels or ['Document']

    def run():
        n = max(1, len(steps))
        for i, lab in enumerate(steps):
            base = int(i / n * 100); span = max(1, int(100 / n))
            for k in (1, 2, 3):
                if _pj_is_cancelled(job_id):
                    _pj_set(job_id, status='cancelled', percent=100, message='Geannuleerd.')
                    return
                _pj_set(job_id, percent=min(base + span, base + int(span * k / 3)),
                        message=f'{lab}: wordt geprint… (demo)')
                time.sleep(0.5)
        _pj_set(job_id, status='done', percent=100,
                message='Klaar - dit is een demo, er is niets echt geprint.')
    threading.Thread(target=run, daemon=True).start()
    return job_id

@app.route('/print-status/<job_id>')
@login_required
def print_status(job_id):
    j = sharedstate.job_get(job_id)
    if not j:
        return jsonify({'status': 'unknown'}), 404
    return jsonify({'status': j['status'], 'percent': j.get('percent', 0),
                    'message': j.get('message', ''), 'title': j.get('title', ''),
                    'error': j.get('error')})

@app.route('/print-cancel/<job_id>', methods=['POST'])
@login_required
def print_cancel(job_id):
    """Markeer de print-job als geannuleerd; de achtergrond-worker stopt en annuleert de printer-job."""
    j = sharedstate.job_get(job_id)
    if not j:
        return jsonify({'error': 'onbekende taak'}), 404
    if j['status'] in ('done', 'error', 'cancelled'):
        return jsonify({'success': True, 'status': j['status']})   # niets meer te annuleren
    sharedstate.job_set(job_id, cancel=True, message='Annuleren…')
    _abort_current_printer_job(job_id)     # meteen ook op de printer proberen af te breken
    return jsonify({'success': True, 'status': 'cancelling'})

@app.route('/print-netwerk/<int:card_id>', methods=['POST'])
@login_required
def card_print_network(card_id):
    u = get_current_user()
    card = Card.query.get_or_404(card_id)
    if u.role != 'admin' and card.filiaal != u.filiaal:
        abort(403)
    if is_demo(u):
        return jsonify({'success': True, 'printer': DEMO_PRINTER_NAAM,
                        'job_id': _enqueue_demo_print(card.title, [card.title])})
    f = Filiaal.query.filter_by(nummer=card.filiaal).first()
    if not f or not (f.doc_printer_ip or _agent_online(f)):
        return jsonify({'error': 'Voor deze winkel is geen winkelprinter ingesteld (Beheer → Filialen).'}), 400
    if getattr(u, 'access_policy', 'anywhere') == 'ip_print':
        cip = client_ip()
        if not ip_in_list(cip, f.allowed_ips or ''):
            log_action('print_geblokkeerd_ip', f'IP {cip} niet op winkelnetwerk', filiaal=f.nummer)
            return jsonify({'error': f'Printen op de winkelprinter kan alleen vanaf het winkelnetwerk. Jouw IP: {cip}.'}), 403
    fmt = _card_format_key(card)
    if fmt not in _DOC_TRAY_FORMATS:
        return jsonify({'error': f'Dit formaat ({card.formaat}) is te klein voor de winkelprinter - gebruik browserprint of downloaden.'}), 400
    pdf_name = card_basename(card.image) + '.pdf'
    pdf_path = os.path.join(app.config['EXPORT_FOLDER'], pdf_name)
    if not os.path.exists(pdf_path):
        return jsonify({'error': 'PDF niet gevonden voor deze kaart.'}), 404
    with open(pdf_path, 'rb') as fh:
        pdf_bytes = fh.read()
    try:
        copies = max(1, min(50, int((request.get_json(silent=True) or {}).get('copies', 1))))
    except Exception:
        copies = 1
    tray = _doc_trays(f).get(fmt, 'auto')
    plabel = f.doc_printer_name or f.doc_printer_ip or 'de winkelprinter (via agent)'
    docs = [{'pdf': pdf_bytes, 'media': _DOC_MEDIA.get(fmt), 'source': tray,
             'orient': _DOC_ORIENT.get(fmt), 'copies': copies,
             'job_name': f'pluslokaal-{card.title}', 'label': card.title}]
    job_id = _enqueue_print(card.title, f.doc_printer_ip, f.doc_printer_port or 631,
                            '/ipp/print', docs, plabel, filiaal=f.nummer)
    log_action('print_netwerk_start', f'{copies}x "{card.title}" ({fmt}) → {plabel} lade {tray}', filiaal=f.nummer)
    return jsonify({'success': True, 'job_id': job_id, 'printer': plabel})

def _selected_cards(u, ids):
    """Kaarten die de gebruiker mag zien uit een lijst id's (admin = alles, anders eigen filiaal)."""
    if not ids:
        return []
    q = Card.query.filter(Card.id.in_(ids))
    cards = q.all()
    if u.role != 'admin':
        cards = [c for c in cards if c.filiaal == u.filiaal]
    # bewaar de volgorde zoals aangeleverd
    by_id = {c.id: c for c in cards}
    return [by_id[i] for i in ids if i in by_id]

@app.route('/kaarten/print', methods=['POST'])
@login_required
def cards_print_network():
    """Meerdere geselecteerde kaarten in één keer printen - elke kaart naar de printer van ZIJN
    EIGEN winkel (filiaal), en elk formaat naar z'n eigen lade."""
    u = get_current_user()
    ids = [int(x) for x in request.form.getlist('card_ids') if str(x).isdigit()]
    cards = _selected_cards(u, ids)
    if not cards:
        return jsonify({'error': 'Geen kaarten geselecteerd.'}), 400
    if is_demo(u):
        return jsonify({'success': True, 'printer': DEMO_PRINTER_NAAM, 'sent': len(cards), 'skipped': 0,
                        'job_id': _enqueue_demo_print(f'{len(cards)} kaart(en) printen',
                                                      [c.title for c in cards])})
    # Winkelprinters van alle betrokken filialen ophalen.
    fils = {f.nummer: f for f in Filiaal.query.filter(
        Filiaal.nummer.in_({c.filiaal for c in cards if c.filiaal is not None})).all()}
    ip_print = getattr(u, 'access_policy', 'anywhere') == 'ip_print'
    cip = client_ip()
    docs, skipped, printers = [], [], {}
    for card in cards:
        f = fils.get(card.filiaal)
        if not f or not (f.doc_printer_ip or _agent_online(f)):   # geen printer voor die winkel
            skipped.append(card.title); continue
        if ip_print and not ip_in_list(cip, f.allowed_ips or ''):
            skipped.append(card.title); continue
        fmt = _card_format_key(card)
        if fmt not in _DOC_TRAY_FORMATS:                  # te klein voor deze printer
            skipped.append(card.title); continue
        pdf_path = os.path.join(app.config['EXPORT_FOLDER'], card_basename(card.image) + '.pdf')
        if not os.path.exists(pdf_path):
            skipped.append(card.title); continue
        pname = f.doc_printer_name or f.doc_printer_ip or 'de winkelprinter (via agent)'
        printers[pname] = printers.get(pname, 0) + 1
        with open(pdf_path, 'rb') as fh:
            docs.append({'pdf': fh.read(), 'media': _DOC_MEDIA.get(fmt),
                         'source': _doc_trays(f).get(fmt, 'auto'), 'orient': _DOC_ORIENT.get(fmt),
                         'copies': 1, 'job_name': f'pluslokaal-{card.title}',
                         'label': f'{card.title} ({FORMAAT_LABELS.get(fmt, fmt)}) → {pname}',
                         'ip': f.doc_printer_ip, 'port': f.doc_printer_port or 631, 'path': '/ipp/print'})
    if not docs:
        return jsonify({'error': 'Geen van de geselecteerde kaarten kan op een winkelprinter '
                                 '(geen printer ingesteld, te klein, of geen toegang).'}), 400
    plabel = list(printers)[0] if len(printers) == 1 else f'{len(printers)} winkelprinters'
    d0 = docs[0]
    job_id = _enqueue_print(f'{len(docs)} kaart(en) printen', d0['ip'], d0['port'], d0['path'],
                            docs, plabel, filiaal=u.filiaal)
    if skipped:
        sharedstate.job_set(job_id, skipped=len(skipped))
    log_action('print_netwerk_bulk', f'{len(docs)} kaart(en) → {plabel}'
               + (f' ({len(skipped)} overgeslagen)' if skipped else ''), filiaal=u.filiaal)
    return jsonify({'success': True, 'job_id': job_id, 'printer': plabel,
                    'sent': len(docs), 'skipped': len(skipped)})

@app.route('/kaarten/download', methods=['POST'])
@login_required
def cards_download():
    """Meerdere geselecteerde kaarten downloaden - één PDF, of een ZIP bij meerdere."""
    u = get_current_user()
    ids = [int(x) for x in request.form.getlist('card_ids') if str(x).isdigit()]
    cards = _selected_cards(u, ids)
    if not cards:
        abort(400)
    files = []
    for card in cards:
        p = os.path.join(app.config['EXPORT_FOLDER'], card_basename(card.image) + '.pdf')
        if os.path.exists(p):
            files.append((card, p))
    if not files:
        abort(404)
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    if len(files) == 1:
        card, p = files[0]
        return send_file(p, mimetype='application/pdf', as_attachment=True,
                         download_name=f'{_safe_name(card.title)}.pdf')
    import zipfile
    zbio = io.BytesIO()
    used = {}
    with zipfile.ZipFile(zbio, 'w', zipfile.ZIP_DEFLATED) as z:
        for card, p in files:
            name = _safe_name(card.title) or f'kaart_{card.id}'
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                name = f'{name}_{used[name]}'
            z.write(p, f'{name}.pdf')
    zbio.seek(0)
    return send_file(zbio, mimetype='application/zip', as_attachment=True,
                     download_name=f'schapkaarten_{stamp}.zip')

def _safe_name(s):
    return re.sub(r'[^\w\-]+', '_', (s or '').strip()).strip('_')[:60]

def _build_test_doc_pdf(fmt):
    """Bouw een kleine testpagina (zelfde renderer als de kaarten) voor de winkelprinter-test."""
    w_mm, h_mm = _SIZES_MM.get(fmt, (210, 297))
    W, H = _px(w_mm), _px(h_mm)
    canvas = Image.new('RGB', (W, H), WHITE)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([12, 12, W - 13, H - 13], outline=BLACK, width=8)
    lines = ['PLUSLOKAAL', 'TESTPRINT', f'{fmt} · {datetime.now().strftime("%d-%m-%Y %H:%M")}']
    y = int(H * 0.4)
    for i, line in enumerate(lines):
        draw.text((int(W * 0.08), y), line, font=F(W_BOLD, 60 if i < 2 else 34), fill=BLACK)
        y += 90 if i < 2 else 60
    buf = io.BytesIO()
    canvas.save(buf, 'PDF', resolution=_PRINT_DPI)
    return buf.getvalue()

@app.route('/filialen/<int:nummer>', methods=['GET', 'POST'])
@login_required
def filiaal_detail(nummer):
    u = get_current_user()
    if not u or u.role != 'admin':
        flash('Alleen de superadmin kan filialen beheren.', 'error')
        return redirect(url_for('dashboard'))
    f = Filiaal.query.filter_by(nummer=nummer).first_or_404()
    if request.method == 'POST':
        act = request.form.get('action', 'save')
        if act == 'agent_key':
            # Nieuwe agent-sleutel + webinterface-login genereren (oude worden ongeldig).
            f.agent_key = secrets.token_urlsafe(32)
            import string as _string
            alfabet = _string.ascii_letters + _string.digits
            f.agent_web_pass = ''.join(secrets.choice(alfabet) for _ in range(14))
            f.agent_seen = None; f.agent_version = None
            db.session.commit()
            log_action('agent_sleutel', f'nieuwe sleutel voor winkel {f.nummer}', filiaal=f.nummer)
            flash(f'Nieuwe agent-sleutel: {f.agent_key}', 'success')
            return redirect(url_for('filiaal_detail', nummer=nummer))
        if act == 'agent_rmm':
            set_setting('agent_rmm_cmd', (request.form.get('rmm_cmd') or '').strip())
            flash('RMM-installatieopdracht opgeslagen (geldt voor alle nieuwe Pi-installaties).', 'success')
            return redirect(url_for('filiaal_detail', nummer=nummer))
        if act == 'agent_revoke':
            f.agent_key = None; f.agent_seen = None; f.agent_version = None; f.agent_info = None
            db.session.commit()
            log_action('agent_sleutel_ingetrokken', f'winkel {f.nummer}', filiaal=f.nummer)
            flash('Agent-sleutel ingetrokken - de Pi kan niet meer verbinden.', 'success')
            return redirect(url_for('filiaal_detail', nummer=nummer))
        if act == 'test':
            if not (f.printer_ip or _agent_online(f)):
                flash('Stel eerst een printer-IP in (of zet de winkel-agent aan) en sla op.', 'error')
                return redirect(url_for('filiaal_detail', nummer=nummer))
            item = {'name': 'PLUS TEST', 'barcode': '8710400145829', 'price': 1.23}
            opts = {'price_unit': 'stuk', 'today': datetime.now().strftime('%d-%m-%Y')}
            payload, _t = _build_label_payload([item], f, opts)
            try:
                _send_label(f, payload)
                log_action('printer_test', f'{f.printer_ip}:{f.printer_port}', filiaal=f.nummer)
                flash(f'Testlabel verstuurd naar {f.printer_ip}:{f.printer_port}. Komt er niets uit? Kies een andere printertaal.', 'success')
            except OSError as e:
                flash(f'Kan {f.printer_ip}:{f.printer_port} niet bereiken ({e.__class__.__name__}).', 'error')
            return redirect(url_for('filiaal_detail', nummer=nummer))
        if act == 'test_doc':
            if not f.doc_printer_ip:
                flash('Stel eerst een winkelprinter-IP in en sla op.', 'error')
                return redirect(url_for('filiaal_detail', nummer=nummer))
            fmt = request.form.get('test_fmt') or 'sk_maxi'
            if fmt not in _DOC_TRAY_FORMATS:
                fmt = 'sk_maxi'
            tray = _doc_trays(f).get(fmt, 'auto')
            try:
                pdf_bytes = _build_test_doc_pdf(fmt)
                _print_card_network(f.doc_printer_ip, f.doc_printer_port or 631, pdf_bytes,
                                     _DOC_MEDIA.get(fmt), tray, _DOC_ORIENT.get(fmt), 1,
                                     job_name='pluslokaal-testprint')
                log_action('printer_test_doc', f'{f.doc_printer_ip} lade {tray} ({fmt})', filiaal=f.nummer)
                flash(f'Testpagina ({FORMAAT_LABELS.get(fmt, fmt)}) verstuurd naar {f.doc_printer_ip} - lade {tray}.', 'success')
            except OSError as e:
                flash(f'Kan winkelprinter {f.doc_printer_ip} niet bereiken: {e}', 'error')
            return redirect(url_for('filiaal_detail', nummer=nummer))
        # Twee losse formulieren op één pagina: bewerk alleen de sectie die werd opgeslagen,
        # zodat het opslaan van de ene printer de andere niet wist.
        section = request.form.get('section', 'label')
        if section == 'label':
            # Label-render-instellingen. Bij PA-winkels vervallen IP/poort (printers via de agent);
            # bij directe-IP-winkels (bv. PLUS Koelhuis) worden die velden wel meegestuurd.
            f.printer_dpi = request.form.get('printer_dpi', type=int) or 300
            f.printer_protocol = request.form.get('printer_protocol', 'tspl')
            f.printer_label_w = _num(request.form.get('printer_label_w')) or 45.0
            f.printer_label_h = _num(request.form.get('printer_label_h')) or 40.0
            if 'printer_ip' in request.form:
                f.printer_name = request.form.get('printer_name', '').strip() or None
                f.printer_ip = request.form.get('printer_ip', '').strip() or None
                f.printer_port = request.form.get('printer_port', type=int) or 9100
        elif section == 'doc':
            f.print_only = bool(request.form.get('print_only'))
            if 'doc_printer_ip' in request.form:
                f.doc_printer_name = request.form.get('doc_printer_name', '').strip() or None
                f.doc_printer_ip = request.form.get('doc_printer_ip', '').strip() or None
                f.doc_printer_port = request.form.get('doc_printer_port', type=int) or 631
            trays = {}
            for fmt in _DOC_TRAY_FORMATS:
                v = request.form.get(f'tray_{fmt}', '').strip()
                if v:
                    trays[fmt] = v
            f.doc_printer_trays = json.dumps(trays) if trays else None
        elif section == 'filiaal':
            f.naam = request.form.get('naam', '').strip() or None
            f.allowed_ips = request.form.get('allowed_ips', '').strip() or None
            f.login_hint = request.form.get('login_hint', '').strip() or None
        db.session.commit()
        log_action('filiaal_config', f'winkel {f.nummer}', filiaal=f.nummer)
        flash('Filiaal opgeslagen.', 'success')
        return redirect(url_for('filiaal_detail', nummer=nummer))
    ucount = User.query.filter_by(filiaal=f.nummer).count()
    return render_template('filiaal_detail.html', user=u, f=f, ucount=ucount,
                           doc_trays=_doc_trays(f), doc_formats=_DOC_TRAY_FORMATS,
                           formaat_labels=FORMAAT_LABELS,
                           agent_online=_agent_online(f),
                           agent_info=(json.loads(f.agent_info) if f.agent_info else {}),
                           rmm_cmd=get_setting('agent_rmm_cmd', ''),
                           img_state=_img_state,
                           img_info=(lambda p: {'size_mb': os.path.getsize(p) // (1024*1024),
                                                'mtime': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%d-%m %H:%M')}
                                     if p else None)(_img_artifact()),
                           iso_state=_iso_state,
                           iso_info=(lambda p: {'size_mb': os.path.getsize(p) // (1024*1024),
                                                'mtime': datetime.fromtimestamp(os.path.getmtime(p)).strftime('%d-%m %H:%M')}
                                     if p else None)(_iso_artifact()))

# ─── ROLLENBEHEER (admin) ─────────────────────────────────────────────────────
def _slugify_role(txt):
    s = re.sub(r'[^a-z0-9]+', '_', (txt or '').lower()).strip('_')
    return s or 'rol'

@app.route('/rollen', methods=['GET', 'POST'])
@login_required
def rollen():
    u = get_current_user()
    if not u or u.role != 'admin':
        flash('Alleen de superadmin kan rollen beheren.', 'error')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        act = request.form.get('action', 'save')
        perms = [p for p in request.form.getlist('perms') if p in _ASSIGNABLE_KEYS]
        if act == 'create':
            label = request.form.get('label', '').strip()
            if not label:
                flash('Geef de rol een naam.', 'error')
                return redirect(url_for('rollen'))
            name = _slugify_role(label)
            base = name; i = 2
            while Role.query.filter_by(name=name).first():
                name = f'{base}_{i}'; i += 1
            db.session.add(Role(name=name, label=label, permissions=json.dumps(perms),
                                is_system=False, store_scoped=True))
            db.session.commit()
            log_action('rol_aangemaakt', f'{label} ({name})')
            flash(f'Rol "{label}" aangemaakt.', 'success')
        elif act == 'save':
            r = Role.query.get(request.form.get('id', type=int))
            if r:
                r.permissions = json.dumps(perms)
                if not r.is_system:
                    r.label = request.form.get('label', r.label).strip() or r.label
                db.session.commit()
                log_action('rol_gewijzigd', r.name)
                flash(f'Rechten van "{r.label}" opgeslagen.', 'success')
        elif act == 'delete':
            r = Role.query.get(request.form.get('id', type=int))
            if r and not r.is_system:
                if User.query.filter_by(role=r.name).count() > 0:
                    flash('Deze rol is nog in gebruik en kan niet verwijderd worden.', 'error')
                else:
                    db.session.delete(r); db.session.commit()
                    log_action('rol_verwijderd', r.name)
                    flash('Rol verwijderd.', 'success')
        return redirect(url_for('rollen'))
    roles = Role.query.order_by(Role.is_system.desc(), Role.label).all()
    counts = {r.name: User.query.filter_by(role=r.name).count() for r in roles}
    role_perms = {r.name: set(json.loads(r.permissions or '[]')) for r in roles}
    return render_template('rollen.html', user=u, roles=roles, perm_defs=ASSIGNABLE_PERMS,
                           counts=counts, role_perms=role_perms)

# ─── MIJN TEAM (ondernemer, winkel-gescoped) ──────────────────────────────────
def _assignable_roles():
    """Rollen die een teambeheerder mag toewijzen (winkel-gescoped, dus niet admin)."""
    return Role.query.filter_by(store_scoped=True).order_by(Role.is_system.desc(), Role.label).all()

@app.route('/team', methods=['GET', 'POST'])
@login_required
def team():
    u = get_current_user()
    if not u or not can(u, 'team'):
        flash('Geen toegang tot teambeheer.', 'error')
        return redirect(url_for('dashboard'))
    fil = u.filiaal
    if u.role == 'admin':
        fil = request.args.get('filiaal', type=int) or u.filiaal
    f_obj = Filiaal.query.filter_by(nummer=fil).first()
    valid_roles = {r.name for r in _assignable_roles()}
    if request.method == 'POST':
        act = request.form.get('action', 'add')
        if act == 'add':
            nm = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip().lower() or None
            ro = request.form.get('role', 'medewerker')
            if ro not in valid_roles:
                ro = 'medewerker'
            if not nm or not email:
                flash('Naam en e-mailadres zijn verplicht.', 'error')
            elif find_user_by_name(nm) or find_user_by_email(email):
                flash('Er bestaat al een gebruiker met deze naam of e-mail.', 'error')
            else:
                m = User(username=nm, email=email, role=ro, filiaal=fil,
                         filiaal_naam=(f_obj.naam if f_obj else None),
                         password='!', approved=True, must_change_password=True)
                db.session.add(m); db.session.commit()
                log_action('teamlid_toegevoegd', f'{nm} ({email}) als {ro}', filiaal=fil)
                ok, err, temp = send_welcome_invite(m)
                flash(f'{nm} toegevoegd. Uitnodiging met tijdelijk wachtwoord verstuurd.' if ok
                      else f'{nm} toegevoegd, maar mail mislukt ({err}). Tijdelijk wachtwoord: {temp} - geef dit door.',
                      'success' if ok else 'warning')
            return redirect(url_for('team', filiaal=fil) if u.role == 'admin' else url_for('team'))
        # acties op een bestaand lid (binnen de eigen winkel)
        m = User.query.get(request.form.get('id', type=int))
        if not m or m.filiaal != fil or m.role == 'admin':
            flash('Geen toegang tot dit lid.', 'error')
            return redirect(url_for('team', filiaal=fil) if u.role == 'admin' else url_for('team'))
        if act == 'approve':
            m.approved = True; db.session.commit()
            log_action('teamlid_goedgekeurd', m.username, filiaal=fil)
            if m.email:
                # Zelf-geregistreerd (eigen wachtwoord gekozen) → alleen "goedgekeurd"-melding, geen reset.
                # Anders (nog placeholder-wachtwoord) → volledige uitnodiging met tijdelijk wachtwoord.
                if getattr(m, 'must_change_password', False):
                    send_welcome_invite(m)
                else:
                    send_approved_notice(m)
            flash(f'{m.username} goedgekeurd.', 'success')
        elif act == 'invite':
            if m.email:
                ok, err, temp = send_welcome_invite(m)
                flash('Uitnodiging met tijdelijk wachtwoord opnieuw verstuurd.' if ok
                      else f'Mail mislukt ({err}). Tijdelijk wachtwoord: {temp} - geef dit door.',
                      'success' if ok else 'warning')
        elif act == 'reset':
            if m.email and mail_enabled():
                ok, err = send_setpw_invite(m, 'reset')
                flash('Reset-mail verstuurd.' if ok else f'Mail mislukt: {err}', 'success' if ok else 'error')
        elif act == 'role':
            ro = request.form.get('role', m.role)
            if ro in valid_roles:
                m.role = ro; db.session.commit()
                log_action('teamlid_rol', f'{m.username} → {ro}', filiaal=fil)
                flash('Rol gewijzigd.', 'success')
        elif act == 'remove':
            if m.id == u.id:
                flash('Je kunt jezelf niet verwijderen.', 'error')
            else:
                db.session.delete(m); db.session.commit()
                log_action('teamlid_verwijderd', m.username, filiaal=fil)
                flash(f'{m.username} verwijderd.', 'success')
        return redirect(url_for('team', filiaal=fil) if u.role == 'admin' else url_for('team'))
    members = User.query.filter_by(filiaal=fil).order_by(User.approved.asc(), User.username).all()
    pending = [m for m in members if not m.approved]
    active = [m for m in members if m.approved]
    role_labels = {r.name: r.label for r in Role.query.all()}
    filialen = Filiaal.query.order_by(Filiaal.nummer).all() if u.role == 'admin' else []
    return render_template('team.html', user=u, fil=fil, f_obj=f_obj, pending=pending,
                           active=active, roles=_assignable_roles(), role_labels=role_labels,
                           filialen=filialen, mail_on=mail_enabled())

# ─── PRINTBARE WINKELPAKKETTEN (oud W2P-systeem) ──────────────────────────────
def _w2p_thumb_dir():
    d = os.path.join(os.path.dirname(__file__), 'static', 'w2p_thumbs')
    os.makedirs(d, exist_ok=True)
    return d

def _w2p_pdf_dir():
    d = os.path.join(os.path.dirname(__file__), 'static', 'w2p_pdfs')
    os.makedirs(d, exist_ok=True)
    return d

# Categorieën die we tonen/synchroniseren (id -> label). 'leeg' (9) en 'VINT' (10) bevatten
# geen bruikbare weekpakketten en worden bewust overgeslagen.
W2P_CATEGORIES = [(7, 'Weekpakketten'), (8, 'Slijterij')]

# Bekende fysieke papierformaten (de W2P-downloadbuckets). Elke tegel-tekst met decoratie eromheen
# (bv. "Briljant …", "… OLF", "… Dagdeal ma", of een TOEKOMSTIGE campagne-suffix) hoort bij het
# basisformaat dat erin voorkomt. Zo hoeven we niet elke nieuwe variant handmatig toe te voegen.
_W2P_BASE_FORMATS = [
    'A3 liggend', 'A3 staand', 'A4 liggend', 'A4 staand', 'A5 liggend', 'A5 staand',
    'A6 liggend', 'A6 staand', 'SK Maxi', 'SK Middel', 'SK Mini',
]

def _normalize_formaat(formaat):
    """Koppel een tegel-formaat aan het fysieke papierformaat (W2P-downloadbucket). W2P bundelt alle
    varianten van een formaat (Briljant/OLF/Dagdeal/…) in ÉÉN per-formaat-PDF; deze normalisatie zorgt
    dat onze paginacount klopt en de lokale cache gebruikt wordt i.p.v. onnodig live te bestellen.

    Zelf-aanpassend: bevat de tekst een bekend basisformaat, dan geeft die het basisformaat terug -
    ook voor nog-onbekende suffixen. Alleen als geen basisformaat herkend wordt, valt 'ie terug op
    het strippen van bekende decoraties."""
    f = (formaat or '').strip()
    low = f.lower()
    for base in _W2P_BASE_FORMATS:
        if base.lower() in low:
            return base
    # Terugval voor een onbekend basisformaat: strip bekende decoraties.
    if f.startswith('Briljant '):
        f = f[len('Briljant '):]
    if f.endswith(' OLF'):
        f = f[:-len(' OLF')]
    f = re.sub(r'\s+Dagdeal\b.*$', '', f, flags=re.I)
    return f.strip()

def _is_briljant(formaat):
    """True als dit een 'Briljant'-ontwerpvariant is (wordt in de praktijk niet gebruikt)."""
    return (formaat or '').strip().startswith('Briljant ')

# ── Multi-up vellen uitsplitsen ───────────────────────────────────────────────
# Sommige formaten bundelen meerdere kaarten op één afscheurvel. De gecachte vel-PDF is een net
# raster; per formaat weten we hoe de NIET-briljant (=gebruikte) kaarten daarin liggen, zodat we een
# gekozen kaart uit z'n cel kunnen knippen en alleen de gekozen kaarten opnieuw op vellen zetten -
# i.p.v. het hele vel (alle kaarten) mee te sturen of live opnieuw te bestellen. Elke gesneden kaart
# wordt geverifieerd tegen z'n eigen productnaam (tekst uit de PDF); klopt het niet, dan valt die
# kaart terug op live bestellen. Layout per formaat (empirisch gemeten + geverifieerd, A4-liggend):
#   plain_per_page = aantal gebruikte (niet-briljant) kaarten per bronpagina, in sort_index-volgorde
#   src_cells      = clip-rechthoek (fracties van de bronpagina) per kaart-positie binnen een pagina
#   out_targets    = doelrechthoek(en) per uitvoer-slot (dupliceren = meerdere afscheurstroken)
# Voor de fysieke afscheur-vellen (bv. SK Maxi) is UITLIJNING cruciaal: de kaarten moeten exact op de
# posities van het aangeleverde vel blijven staan. Daarom herpositioneren we NIET, maar houden we de
# originele vel-pagina's aan en maken we alleen de niet-gekozen cellen wit. Per formaat:
#   plain_per_page = aantal gebruikte (niet-briljant) kaarten per bronpagina, in sort_index-volgorde
#   plain_cells    = rechthoek (fracties) van elke gebruikte kaart-cel (voor tekst-verificatie + behoud)
#   blank_rects    = extra vaste witte vlakken (bv. de ongebruikte Briljant-kolom) op elk bewaard vel
_MULTIUP_LAYOUTS = {
    # SK Maxi: 2×2 raster op A4-liggend, 4 kaart-cellen (elk 354×221pt), cellen hangen tegen de
    # midden-gutter. In het bron-vel staat de gebruikte kaart in de LINKERkolom (boven/onder), de
    # ongebruikte Briljant-variant rechts. We knippen de gebruikte kaart uit z'n cel en zetten 'm op
    # EXACT dezelfde 4 posities in de uitvoer (4 per vel; de 5e begint een nieuw vel). Omdat alle 4 de
    # cellen even groot zijn is dat een zuivere verplaatsing (geen vervorming) → kaart valt precies op
    # de plek van het aangeleverde vel, geschikt voor het fysieke afscheur-papier.
    'SK Maxi': {
        'plain_per_page': 2,
        # bron-cel per gebruikte kaart-positie (even plain-index → boven-links, oneven → onder-links)
        'src_cells': [
            (0.0950, 0.1059, 0.5154, 0.4773),   # boven-links
            (0.0950, 0.5244, 0.5154, 0.8975),   # onder-links
        ],
        # 4 doelposities op het uitvoer-vel (zelfde raster als origineel): TL, TR, BL, BR
        'slots': [
            (0.0950, 0.1059, 0.5154, 0.4773),
            (0.5582, 0.1059, 0.9786, 0.4773),
            (0.0950, 0.5244, 0.5154, 0.8975),
            (0.5582, 0.5244, 0.9786, 0.8975),
        ],
    },
}

def _card_keywords(naam):
    """Distinctieve woorden uit een kaartnaam (zonder code-prefix) voor tekst-verificatie."""
    import re as _re
    t = _re.sub(r'^\d+-\d+-F?\d*-?', '', naam or '').strip()
    return [w.lower() for w in _re.findall(r'[A-Za-z]{3,}', t)][:3]

def _assemble_multiup_from_cache(cat_id, pid, gid, norm_fmt, selected_ids, quantities=None):
    """Knip de opgegeven (niet-briljant) kaarten uit het gecachte multi-up vel en zet ze op nieuwe
    vellen. Geeft (pdf_bytes, verwerkte_ids) terug; kaarten die niet geverifieerd konden worden
    (tekst matcht niet, geen layout, geen cache) komen NIET in verwerkte_ids → caller regelt die live.
    Bij een lege/onbruikbare uitkomst: (None, []). ``quantities`` = {doc_id: aantal>1} → die kaart
    wordt zo vaak op de vellen gezet (maar in verwerkte_ids blijft 'ie ÉÉN keer, zodat de live-fallback
    niet per ongeluk dubbel bestelt)."""
    quantities = quantities or {}
    def _qty(doc_id):
        try:
            return max(1, int(quantities.get(doc_id) or quantities.get(str(doc_id)) or 1))
        except Exception:
            return 1
    layout = _MULTIUP_LAYOUTS.get(norm_fmt)
    if not layout:
        return None, []
    row = W2PCachedPdf.query.filter_by(category_id=cat_id, period_id=pid, group_id=gid, formaat=norm_fmt).first()
    if not row:
        return None, []
    path = os.path.join(_w2p_pdf_dir(), row.path)
    if not os.path.exists(path):
        return None, []
    # Gebruikte (niet-briljant) kaarten van dit formaat in tegel-volgorde → hun vel-positie (plain-index).
    ordered = [d for d in (W2PDocument.query.filter_by(category_id=cat_id, period_id=pid, group_id=gid)
                           .order_by(W2PDocument.sort_index).all())
               if _normalize_formaat(d.formaat) == norm_fmt and not _is_briljant(d.formaat)]
    pos = {d.promotion_document_id: i for i, d in enumerate(ordered)}
    naam = {d.promotion_document_id: d.naam for d in ordered}
    import fitz
    src = fitz.open(path)
    ppp = layout['plain_per_page']
    W, H = src[0].rect.width, src[0].rect.height

    def frac(r):
        return fitz.Rect(r[0] * W, r[1] * H, r[2] * W, r[3] * H)

    # Verifieer elke gekozen kaart (tekst in z'n bron-cel) en verzamel z'n bron-(pagina, cel-rect).
    verified = []   # (doc_id, src_page, src_clip_rect)
    for did in selected_ids:
        idx = pos.get(did)
        if idx is None:
            continue
        page_no = idx // ppp
        cell = idx % ppp
        if page_no >= src.page_count:
            continue
        clip = frac(layout['src_cells'][cell])
        text = src[page_no].get_text('text', clip=clip).lower()
        kws = _card_keywords(naam.get(did, ''))
        if not kws or not all(k in text for k in kws):
            continue  # verificatie faalt → live fallback voor deze kaart
        verified.append((did, page_no, clip))

    if not verified:
        src.close()
        return None, []

    # Pak de gekozen kaarten 4-op-1 (of layout['slots']-aantal): elke kaart wordt uit z'n bron-cel
    # geknipt en op de VOLGENDE vaste rasterpositie gezet - exact dezelfde grootte/plek als het
    # origineel (zuivere verplaatsing, geen vervorming). Bij vol vel begint de volgende kaart een
    # nieuw vel. Geschikt voor het fysieke afscheur-papier.
    slots = layout['slots']
    per = len(slots)
    # Aantal toepassen: elke geverifieerde kaart komt _qty keer op de vellen (done blijft uniek).
    placements = []
    for (did, page_no, clip) in verified:
        for _ in range(_qty(did)):
            placements.append((did, page_no, clip))
    out = fitz.open()
    cur = None
    for i, (did, page_no, clip) in enumerate(placements):
        slot = i % per
        if slot == 0:
            cur = out.new_page(width=W, height=H)
        cur.show_pdf_page(frac(slots[slot]), src, page_no, clip=clip)
    data = out.tobytes()
    out.close(); src.close()
    return data, [d for d, _, _ in verified]

def _fetch_group_pdfs(cat_id, pid, gid, group_docs, detail_job_id=None):
    """Bestel ALLE kaarten van deze ene groep-pagina in één keer bij W2P, valideer per formaat de
    paginacount en schrijf de geldige verzameldocumenten naar static/w2p_pdfs/. Doet BEWUST GEEN
    DB-toegang, zodat deze functie veilig vanuit meerdere threads tegelijk kan draaien (parallelle
    afdeling-downloads); de DB-upsert gebeurt daarna serieel in de hoofd-thread met de teruggegeven
    beschrijvingen.

    Geeft een lijst ``[{'formaat','path','doc_ids','page_count'}]`` terug (de te bewaren cache-rijen).

    We hebben empirisch geverifieerd dat W2P's per-formaat PDF een vaste, van onze selectie-volgorde
    onafhankelijke paginavolgorde heeft; die volgorde (de tegel-volgorde ``sort_index``) bewaren we
    in ``doc_ids`` zodat we later exact weten welke pagina bij welk document hoort. Klopt de
    paginacount niet met het verwachte aantal kaarten (bv. multi-up vellen), dan slaan we dat formaat
    over - download valt daar later terug op live ophalen.

    ``detail_job_id``: als gezet, geeft w2p_client.order_and_download hierop de fijnmazige stappen
    door ("Inloggen…", "Kaarten aanvinken…", "PDF ophalen: <formaat>…").
    """
    import w2p_client
    ids = [d['id'] for d in group_docs]
    if not ids:
        return []
    targets = {str(i): {'period_id': pid, 'group_id': gid, 'category_id': cat_id} for i in ids}
    pdfs = w2p_client.order_and_download(ids, targets=targets, job_id=detail_job_id, timeout=600)
    if isinstance(pdfs, dict) and pdfs.get('error'):
        app.logger.warning(f'W2P pre-fetch groep {gid} (periode {pid}) mislukt: {pdfs["error"]}')
        return []
    import fitz
    by_formaat = {}
    for d in group_docs:
        by_formaat.setdefault(_normalize_formaat(d['formaat']), []).append(d['id'])
    pdf_dir = _w2p_pdf_dir()
    rows = []
    for raw_formaat, data in (pdfs or {}).items():
        if not isinstance(data, (bytes, bytearray)):
            continue
        # De W2P-downloadknop heeft soms een "Briljant …"-label terwijl onze verwachte kaarten onder
        # het genormaliseerde formaat staan - daarom ook hier normaliseren voor de match + opslag.
        formaat = _normalize_formaat(raw_formaat)
        expected = by_formaat.get(formaat, [])
        if not expected:
            continue
        try:
            doc = fitz.open(stream=bytes(data), filetype='pdf')
            page_count = doc.page_count
            doc.close()
        except Exception:
            continue
        # ALLE formaten cachen. Voor "single-up" formaten (1 pagina per kaart, page_count == #kaarten)
        # kunnen we later exact per kaart een pagina knippen. Voor "multi-up" formaten (bv. SK Maxi:
        # meerdere kaarten op één afscheurvel, page_count < #kaarten) kan dat niet - dan bewaren we het
        # hele verzameldocument en leveren we bij download het complete vel-bestand (dat is de
        # natuurlijke printbare eenheid). Of het per-kaart-knipbaar is leiden we af uit page_count.
        if page_count != len(expected):
            app.logger.info(f'W2P cache: multi-up formaat "{formaat}" groep {gid} '
                            f'({page_count} pagina\'s voor {len(expected)} kaarten) - heel vel bewaard.')
        safe_fmt = re.sub(r'[^a-zA-Z0-9]+', '_', formaat).strip('_') or 'onbekend'
        fname = f'{cat_id}_{pid}_{gid}_{safe_fmt}.pdf'
        with open(os.path.join(pdf_dir, fname), 'wb') as f:
            f.write(data)
        rows.append({'formaat': formaat, 'path': fname, 'doc_ids': expected, 'page_count': page_count})
    return rows

def _persist_group_cache(cat_id, pid, gid, rows):
    """Schrijf de door _fetch_group_pdfs teruggegeven cache-rijen naar de DB (serieel, hoofd-thread).
    Geeft het aantal weggeschreven formaten terug."""
    for r in rows:
        row = (W2PCachedPdf.query.filter_by(category_id=cat_id, period_id=pid, group_id=gid, formaat=r['formaat']).first()
               or W2PCachedPdf(category_id=cat_id, period_id=pid, group_id=gid, formaat=r['formaat']))
        row.path = r['path']; row.doc_ids = json.dumps(r['doc_ids']); row.page_count = r['page_count']
        row.synced_at = datetime.now()
        db.session.add(row)
    db.session.commit()
    return len(rows)

def _cache_group_pdfs(cat_id, pid, gid, group_docs, detail_job_id=None):
    """Serieel gemak: fetch + persist voor één groep. Geeft het aantal gecachete formaten terug."""
    rows = _fetch_group_pdfs(cat_id, pid, gid, group_docs, detail_job_id=detail_job_id)
    return _persist_group_cache(cat_id, pid, gid, rows)

def sync_w2p_metadata():
    """Cache-sync: crawl alle categorieën × periodes × groepen uit W2P en sla metadata (+ later lazy
    thumbnails) op. Licht/snel - bestelt NIETS bij W2P. Rapporteert voortgang via
    w2p_client.set_progress('sync_meta', ...). Geeft ook terug welke groepen nieuwe documenten
    kregen (voor de nachtelijke aanvul-taak, zie sync_w2p_pdfs)."""
    import w2p_client
    w2p_client.set_progress('sync_meta', 1, 'Categorieën en periodes ophalen…')

    # Fase 1: eerst alle (categorie,periode,groep)-combinaties verzamelen (lichte metadata-calls),
    # zodat we het totaal kennen en dus een echt voortgangspercentage kunnen tonen.
    work = []
    for cat_id, cat_label in W2P_CATEGORIES:
        periods = w2p_client.list_periods(cat_id)
        if isinstance(periods, dict):
            continue
        for per in periods:
            groups = w2p_client.list_groups(per['period_id'], cat_id)
            if isinstance(groups, dict):
                continue
            for grp in groups:
                work.append((cat_id, cat_label, int(per['period_id']), per.get('label'),
                             int(grp['group_id']), grp.get('label')))

    # Fase 2: per groep de kaart-tegels crawlen en wegschrijven.
    total = len(work) or 1
    n_docs = 0; n_new = 0
    n_periods = len({(c, p) for c, _, p, _, _, _ in work})
    changed_groups = []  # (cat_id, pid, gid) met >=1 nieuw document sinds vorige sync
    for i, (cat_id, cat_label, pid, plabel, gid, glabel) in enumerate(work):
        pct = 2 + int(96 * i / total)
        w2p_client.set_progress('sync_meta', pct, f'{cat_label} / {plabel} / {glabel} ({i + 1}/{total})…',
                                 groups_done=i, groups_total=total, docs_found=n_docs, new_docs=n_new)
        docs = w2p_client.crawl(str(pid), cat_id, str(gid))
        if isinstance(docs, dict):
            continue
        group_has_new = False
        for idx, d in enumerate(docs):
            did = int(d['promotion_document_id'])
            existing = db.session.get(W2PDocument, did)
            row = existing or W2PDocument(promotion_document_id=did)
            row.period_id = pid; row.period_label = plabel
            row.category_id = cat_id; row.group_id = gid; row.group_label = glabel
            row.formaat = d.get('formaat'); row.naam = d.get('naam'); row.sort_index = idx
            row.synced_at = datetime.now()
            db.session.add(row); n_docs += 1
            if not existing:
                n_new += 1; group_has_new = True
        db.session.commit()   # thumbnails worden lazy (on-demand) gecachet via /winkelpakketten/thumb
        if group_has_new:
            changed_groups.append((cat_id, pid, gid))

    set_setting('w2p_synced_at', datetime.now().isoformat(timespec='seconds'))
    w2p_client.set_progress('sync_meta', 100, 'Klaar', groups_done=total, groups_total=total,
                             docs_found=n_docs, new_docs=n_new)
    return {'ok': True, 'periods': n_periods, 'documents': n_docs, 'new_documents': n_new,
            'changed_groups': changed_groups}

def _w2p_sync_concurrency():
    """Aantal afdelingen dat de download-sync tegelijk verwerkt. HARDE LIMIET = aantal geconfigureerde
    W2P-accounts: één W2P-account **deelt zijn winkelmandje over al zijn sessies**, dus twee orders
    op hetzelfde account tegelijk vervuilen elkaar (empirisch bevestigd: gaf verkeerde paginacounts).
    Daarom precies één parallelle order per account → met 2 accounts max 2 sessies tegelijk."""
    import w2p_client
    return max(1, w2p_client.account_count())

def sync_w2p_pdfs(only_groups=None):
    """Download-sync: bestel per groep alle kaarten in één keer bij W2P en cache de resulterende
    per-formaat PDF's lokaal, zodat downloaden achteraf lokale pagina's kan knippen. Verwerkt
    meerdere afdelingen PARALLEL over een pool van browser-workers (elk met eigen sessie/account →
    eigen winkelmandje). De W2P-order + PDF-fetch + schijf-write gebeurt in de workers (geen DB);
    de DB-upsert gebeurt serieel in deze hoofd-thread (SQLite-veilig). Rapporteert voortgang via
    w2p_client.set_progress('sync_pdfs', ...).

    ``only_groups``: optionele lijst ``[(category_id,period_id,group_id), ...]`` om alleen die
    groepen te verversen (nachtelijke taak vult zo enkel nieuwe kaarten aan); zonder wordt de hele
    catalogus gedaan en slaan we groepen die al minstens één gecachete PDF hebben over."""
    import w2p_client, concurrent.futures as _cf, threading as _th, queue as _pyqueue
    concurrency = _w2p_sync_concurrency()
    w2p_client.set_pool_size(concurrency)
    w2p_client.set_progress('sync_pdfs', 1, 'Groepen bepalen…')

    skip_existing = only_groups is None
    if only_groups is not None:
        triples = list(dict.fromkeys(only_groups))
    else:
        triples = [(c, p, g) for c, p, g in
                   db.session.query(W2PDocument.category_id, W2PDocument.period_id, W2PDocument.group_id)
                   .distinct().all()]
    # Aantal reeds gecachte formaten per groep (om VOLLEDIG gecachte groepen over te slaan; een groep
    # die nog niet al z'n formaten heeft - bv. de multi-up formaten ontbraken nog - wordt wél gedaan).
    cached_fmt_count = {}
    if skip_existing:
        for c, p, g, cnt in (db.session.query(
                W2PCachedPdf.category_id, W2PCachedPdf.period_id, W2PCachedPdf.group_id, func.count())
                .group_by(W2PCachedPdf.category_id, W2PCachedPdf.period_id, W2PCachedPdf.group_id).all()):
            cached_fmt_count[(c, p, g)] = cnt

    cat_labels = dict(W2P_CATEGORIES)
    total = len(triples) or 1

    # Alle benodigde groep-data vooraf in de hoofd-thread inlezen (geen DB-toegang in de workers).
    jobs = []  # (cat_id, pid, gid, label, group_docs)
    n_skipped = 0
    for (cat_id, pid, gid) in triples:
        docs = (W2PDocument.query.filter_by(category_id=cat_id, period_id=pid, group_id=gid)
                .order_by(W2PDocument.sort_index).all())
        if not docs:
            continue
        # Verwacht aantal formaten = distinct genormaliseerde formaten in deze groep.
        expected_fmts = len({_normalize_formaat(d.formaat) for d in docs})
        if skip_existing and cached_fmt_count.get((cat_id, pid, gid), 0) >= expected_fmts:
            n_skipped += 1
            continue
        label = f'{cat_labels.get(cat_id, cat_id)} / {docs[0].period_label} / {docs[0].group_label}'
        group_docs = [{'id': d.promotion_document_id, 'formaat': d.formaat} for d in docs]
        jobs.append((cat_id, pid, gid, label, group_docs))

    n_cached = 0; n_done = n_skipped
    lock = _th.Lock()
    active = {}                       # slot -> label (welke afdelingen nu in verwerking zijn)
    slots = _pyqueue.Queue()          # vrije detail-slots (0..concurrency-1)
    for s in range(concurrency):
        slots.put(s)

    def _report():
        pct = 2 + int(96 * n_done / total)
        act = ', '.join(sorted(active.values())) or '-'
        w2p_client.set_progress('sync_pdfs', pct,
                                 f'{len(active)} afdeling(en) tegelijk · {n_done}/{total} klaar…',
                                 groups_done=n_done, groups_total=total, pdfs_cached=n_cached,
                                 skipped=n_skipped, active=act, concurrency=concurrency)

    def _fetch_job(job):
        cat_id, pid, gid, label, group_docs = job
        slot = slots.get()
        with lock:
            active[slot] = label
            _report()
        try:
            rows = _fetch_group_pdfs(cat_id, pid, gid, group_docs,
                                     detail_job_id=f'sync_pdfs_d{slot}')
        except Exception as e:
            app.logger.warning(f'W2P pre-fetch groep {gid} (periode {pid}) gaf een fout: {e}')
            rows = []
        finally:
            w2p_client.clear_progress(f'sync_pdfs_d{slot}')
            with lock:
                active.pop(slot, None)
            slots.put(slot)
        return (cat_id, pid, gid, rows)

    _report()
    with _cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
        for cat_id, pid, gid, rows in ex.map(_fetch_job, jobs):
            # DB-write serieel in de hoofd-thread (SQLite-veilig).
            try:
                n_cached += _persist_group_cache(cat_id, pid, gid, rows)
            except Exception as e:
                app.logger.warning(f'W2P cache opslaan groep {gid} mislukt: {e}')
            with lock:
                n_done += 1
                _report()

    w2p_client.set_progress('sync_pdfs', 100, 'Klaar', groups_done=total, groups_total=total,
                             pdfs_cached=n_cached, skipped=n_skipped, active='-', concurrency=concurrency)
    for s in range(concurrency):
        w2p_client.clear_progress(f'sync_pdfs_d{s}')
    return {'ok': True, 'groups': len(jobs), 'cached_pdfs': n_cached, 'skipped': n_skipped}

_w2p_meta_state = {'running': False, 'error': None}
_w2p_pdf_state  = {'running': False, 'error': None}

@app.context_processor
def _inject_w2p_busy():
    """Vlag voor de melding 'winkelpakketten worden aangevuld' (mogelijke vertraging)."""
    try:
        return {'w2p_busy': bool(_w2p_pdf_state.get('running'))}
    except Exception:
        return {'w2p_busy': False}

def _w2p_meta_bg(on_done=None):
    """Start de cache-sync (metadata) op de achtergrond. Geeft False als er al één loopt."""
    if _w2p_meta_state['running']:
        return False
    _w2p_meta_state['running'] = True
    _w2p_meta_state['error'] = None
    def run():
        with app.app_context():
            try:
                result = sync_w2p_metadata()
                if on_done:
                    on_done(result)
            except Exception as e:
                app.logger.error(f'W2P cache-sync mislukt: {e}')
                _w2p_meta_state['error'] = str(e)[:300]
            finally:
                _w2p_meta_state['running'] = False
    threading.Thread(target=run, daemon=True).start()
    return True

def _w2p_pdf_bg(only_groups=None):
    """Start de download-sync (PDF-precache) op de achtergrond. Geeft False als er al één loopt."""
    if _w2p_pdf_state['running']:
        return False
    _w2p_pdf_state['running'] = True
    _w2p_pdf_state['error'] = None
    def run():
        with app.app_context():
            try:
                sync_w2p_pdfs(only_groups=only_groups)
            except Exception as e:
                app.logger.error(f'W2P download-sync mislukt: {e}')
                _w2p_pdf_state['error'] = str(e)[:300]
                _w2p_notify_admins('winkelpakket-synchronisatie mislukt', str(e))
            finally:
                _w2p_pdf_state['running'] = False
    threading.Thread(target=run, daemon=True).start()
    return True

def _w2p_mark_unavailable(doc_ids):
    """Markeer kaarten als 'niet meer beschikbaar' (pluslokaal.nl heeft ze niet meer). Zo proberen we
    ze niet eeuwig opnieuw te downloaden en kan de UI ze als onbeschikbaar tonen."""
    ids = [int(i) for i in doc_ids if str(i).strip().isdigit()]
    if not ids:
        return
    try:
        W2PDocument.query.filter(W2PDocument.promotion_document_id.in_(ids),
                                 W2PDocument.unavailable_at.is_(None)).update(
            {'unavailable_at': datetime.now()}, synchronize_session=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback(); app.logger.error(f'W2P markeren onbeschikbaar mislukt: {e}')

def _w2p_mark_available(doc_ids):
    """Zet kaarten weer op 'beschikbaar' zodra ze tóch gevonden/gedownload worden (bv. weer terug)."""
    ids = [int(i) for i in doc_ids if str(i).strip().isdigit()]
    if not ids:
        return
    try:
        W2PDocument.query.filter(W2PDocument.promotion_document_id.in_(ids),
                                 W2PDocument.unavailable_at.isnot(None)).update(
            {'unavailable_at': None}, synchronize_session=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback(); app.logger.error(f'W2P markeren beschikbaar mislukt: {e}')

def _w2p_missing_recent_groups(recent=5):
    """Detecteer groepen in de MEEST RECENTE weken die metadata hebben maar nog HELEMAAL GEEN
    PDF-cache - dat is een week/afdeling die simpelweg niet gedownload is (zoals week 31 was). We
    kijken bewust alleen naar de nieuwste `recent` periodes (oudere, opgeschoonde weken laten we met
    rust) en alleen naar cache==0 (een deels-gedownloade groep laten we staan; sommige formaten
    bestaan nu eenmaal niet voor elke afdeling - anders zou 'ie eeuwig opnieuw willen downloaden)."""
    try:
        periods = [pid for (pid,) in db.session.query(W2PDocument.period_id).distinct()
                   .order_by(W2PDocument.period_id.desc()).limit(recent).all()]
        if not periods:
            return []
        have_cache = {(c, p, g) for c, p, g in db.session.query(
            W2PCachedPdf.category_id, W2PCachedPdf.period_id, W2PCachedPdf.group_id)
            .filter(W2PCachedPdf.period_id.in_(periods)).distinct().all()}
        # 'niet meer beschikbaar'-kaarten tellen NIET mee als ontbrekend → geen eeuwige downloadpogingen
        # voor een week/afdeling die van pluslokaal.nl verdwenen is.
        missing = [(c, p, g) for c, p, g in db.session.query(
            W2PDocument.category_id, W2PDocument.period_id, W2PDocument.group_id)
            .filter(W2PDocument.period_id.in_(periods), W2PDocument.unavailable_at.is_(None)).distinct().all()
            if (c, p, g) not in have_cache]
        return missing
    except Exception as e:
        app.logger.error(f'W2P ontbrekende-groepen bepalen mislukt: {e}')
        return []

def _w2p_nightly_scheduler():
    """Zelfhelende W2P-download:
    • Kort na het OPSTARTEN vult 'ie direct aan wat er ontbreekt (metadata aanwezig, PDF-cache niet) -
      zo hoeft niemand handmatig te downloaden als een week is blijven hangen (zoals week 31).
    • Elke nacht om 00:00: lichte cache-sync (metadata) en daarna de download-sync voor de gewijzigde
      groepen ÉN alles wat nog ontbreekt in de recente weken."""
    def catchup(reason):
        try:
            with app.app_context():
                miss = _w2p_missing_recent_groups()
                if miss and not _w2p_pdf_state['running'] and not _w2p_meta_state['running']:
                    wk = len({m[1] for m in miss})
                    app.logger.info(f'W2P {reason}: {len(miss)} groep(en) in {wk} week/weken ontbreken, aanvullen…')
                    _w2p_pdf_bg(only_groups=miss)
        except Exception as e:
            app.logger.error(f'W2P {reason} mislukt: {e}')
    def loop():
        time.sleep(25)                     # even wachten tot de app volledig staat
        catchup('start-aanvulling')
        while True:
            now = datetime.now()
            nxt = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
            time.sleep(max(60, (nxt - datetime.now()).total_seconds()))
            def after_meta(result):
                changed = result.get('changed_groups') or []
                try:
                    with app.app_context():
                        todo = list(dict.fromkeys(list(changed) + _w2p_missing_recent_groups()))
                    if todo:
                        app.logger.info(f'W2P nachtelijke sync: {len(todo)} groep(en) aanvullen (gewijzigd + ontbrekend).')
                        _w2p_pdf_bg(only_groups=todo)
                except Exception as e:
                    app.logger.error(f'W2P nachtelijke download-start mislukt: {e}')
            try:
                with app.app_context():
                    started = _w2p_meta_bg(on_done=after_meta)
                    if not started:
                        app.logger.info('W2P nachtelijke sync overgeslagen: er liep al een cache-sync.')
            except Exception as e:
                app.logger.error(f'W2P nachtelijke sync kon niet starten: {e}')
    threading.Thread(target=loop, daemon=True).start()

@app.route('/winkelpakketten')
@login_required
def winkelpakketten():
    u = get_current_user()
    sel_category = request.args.get('category', type=int) or 7
    sel_period = request.args.get('period', type=int)
    sel_group = request.args.get('group', type=int)
    categories = [{'category_id': cid, 'label': label} for cid, label in W2P_CATEGORIES]
    periods = [{'period_id': p, 'label': l} for p, l in
               db.session.query(W2PDocument.period_id, W2PDocument.period_label)
               .filter_by(category_id=sel_category)
               .distinct().order_by(W2PDocument.period_label.desc()).all()]
    groups, documents = [], []
    if sel_period:
        groups = [{'group_id': g, 'label': l} for g, l in
                  db.session.query(W2PDocument.group_id, W2PDocument.group_label)
                  .filter_by(category_id=sel_category, period_id=sel_period)
                  .distinct().order_by(W2PDocument.group_label).all()]
    show_briljant = request.args.get('briljant') == '1'
    if sel_period and sel_group:
        rows = (W2PDocument.query.filter_by(category_id=sel_category, period_id=sel_period, group_id=sel_group)
                .order_by(W2PDocument.naam, W2PDocument.formaat).all())
        # "Briljant"-varianten worden in de praktijk niet gebruikt → standaard verborgen (checkbox toont ze).
        if not show_briljant:
            rows = [d for d in rows if not _is_briljant(d.formaat)]
        documents = [{'promotion_document_id': d.promotion_document_id, 'formaat': d.formaat, 'naam': d.naam,
                      'unavailable': d.unavailable_at is not None}
                     for d in rows]
    return render_template('winkelpakketten.html', user=u, categories=categories, sel_category=sel_category,
                           periods=periods, sel_period=sel_period,
                           groups=groups, sel_group=sel_group, documents=documents, show_briljant=show_briljant,
                           synced_at=get_setting('w2p_synced_at', ''),
                           meta_syncing=_w2p_meta_state['running'], pdf_syncing=_w2p_pdf_state['running'],
                           can_sync=can(u, 'w2p_sync'))

def _w2p_local_thumb(doc_id):
    """Render de thumbnail rechtstreeks uit een al lokaal gecachte PDF (voor gedownloade weken).
    Snel en betrouwbaar - geen plus.nl-verkeer, dus geen 'laadt pas na F5' onder een grid vol tegels.
    Alleen voor single-up formaten (1 pagina per kaart), waar we de exacte pagina kennen."""
    d = db.session.get(W2PDocument, int(doc_id))
    if not d:
        return None
    nf = _normalize_formaat(d.formaat)
    row = W2PCachedPdf.query.filter_by(category_id=d.category_id, period_id=d.period_id,
                                       group_id=d.group_id, formaat=nf).first()
    if not row:
        return None
    try:
        doc_ids = json.loads(row.doc_ids or '[]')
    except Exception:
        doc_ids = []
    path = os.path.join(_w2p_pdf_dir(), row.path)
    if not os.path.exists(path):
        return None
    import fitz, io as _io
    if d.promotion_document_id in doc_ids and row.page_count == len(doc_ids):
        # Single-up: 1 pagina per kaart → render die pagina.
        idx = doc_ids.index(d.promotion_document_id)
        try:
            src = fitz.open(path)
            if idx >= src.page_count:
                src.close(); return None
            pix = src[idx].get_pixmap(matrix=fitz.Matrix(0.5, 0.5))   # ~halve resolutie = nette thumbnail
            bio = _io.BytesIO(pix.tobytes('png')); src.close()
            return bio.getvalue()
        except Exception:
            return None
    # Multi-up (bv. SK Maxi): knip de kaart-cel uit het gecachte vel (zelfde logica als bij downloaden,
    # incl. tekst-verificatie zodat we nooit de verkeerde kaart tonen).
    layout = _MULTIUP_LAYOUTS.get(nf)
    if not layout or _is_briljant(d.formaat):
        return None
    ordered = [x for x in (W2PDocument.query.filter_by(category_id=d.category_id, period_id=d.period_id,
                                                       group_id=d.group_id)
                           .order_by(W2PDocument.sort_index).all())
               if _normalize_formaat(x.formaat) == nf and not _is_briljant(x.formaat)]
    pos = {x.promotion_document_id: i for i, x in enumerate(ordered)}
    idx = pos.get(d.promotion_document_id)
    if idx is None:
        return None
    try:
        src = fitz.open(path)
        ppp = layout['plain_per_page']
        page_no, cell = idx // ppp, idx % ppp
        if page_no >= src.page_count:
            src.close(); return None
        W, H = src[0].rect.width, src[0].rect.height
        r = layout['src_cells'][cell]
        clip = fitz.Rect(r[0] * W, r[1] * H, r[2] * W, r[3] * H)
        text = src[page_no].get_text('text', clip=clip).lower()
        kws = _card_keywords(d.naam or '')
        if not kws or not all(k in text for k in kws):
            src.close(); return None                # verificatie faalt → geen (mogelijk verkeerde) thumb
        pix = src[page_no].get_pixmap(matrix=fitz.Matrix(1.0, 1.0), clip=clip)
        bio = _io.BytesIO(pix.tobytes('png')); src.close()
        return bio.getvalue()
    except Exception:
        return None

# Achtergrond-ophaler voor W2P-thumbnails: één werker haalt ze serieel op, zodat een grid vol tegels
# nooit alle webserver-threads blokkeert (dat was de oorzaak van 'laadt pas na F5'). De browser vraagt
# een ontbrekende thumbnail gewoon opnieuw op (auto-retry) tot 'ie er is.
_thumb_fetch_lock = threading.Lock()
_thumb_fetch_pending = []      # doc_ids in volgorde van aanvraag
_thumb_fetch_set = set()       # zelfde inhoud, voor snelle dedupe
_thumb_fetch_running = False

def _thumb_fetch_worker():
    global _thumb_fetch_running
    import w2p_client
    while True:
        with _thumb_fetch_lock:
            if not _thumb_fetch_pending:
                _thumb_fetch_running = False
                return
            did = _thumb_fetch_pending.pop(0)
            _thumb_fetch_set.discard(did)
        tp = os.path.join(_w2p_thumb_dir(), f'{did}.png')
        if os.path.exists(tp):
            continue
        try:
            b = w2p_client.thumbnail(did, timeout=45)
            if isinstance(b, (bytes, bytearray)) and len(b) > 500:
                open(tp, 'wb').write(b)
        except Exception:
            pass

def _thumb_fetch_enqueue(doc_id):
    """Zet een thumbnail in de achtergrond-ophaalrij (dedupe) en start de werker indien nodig."""
    global _thumb_fetch_running
    with _thumb_fetch_lock:
        if doc_id not in _thumb_fetch_set:
            _thumb_fetch_pending.append(doc_id)
            _thumb_fetch_set.add(doc_id)
        if not _thumb_fetch_running:
            _thumb_fetch_running = True
            threading.Thread(target=_thumb_fetch_worker, daemon=True).start()

@app.route('/winkelpakketten/thumb/<int:doc_id>')
@login_required
def winkelpakketten_thumb(doc_id):
    tp = os.path.join(_w2p_thumb_dir(), f'{doc_id}.png')
    if not os.path.exists(tp):
        # 1) Snelste + betrouwbaarste: render uit de al lokaal gecachte PDF (gedownloade weken).
        b = _w2p_local_thumb(doc_id)
        if isinstance(b, (bytes, bytearray)) and len(b) > 500:
            try:
                open(tp, 'wb').write(b)
            except Exception:
                pass
        else:
            # 2) Nog niet gedownload → in de achtergrondrij zetten en NIET blokkeren; de browser
            #    probeert het vanzelf opnieuw en de tegel vult zich zodra de werker 'm heeft.
            _thumb_fetch_enqueue(int(doc_id))
    if os.path.exists(tp):
        return send_file(tp, mimetype='image/png')
    resp = Response('', status=404)
    resp.headers['Cache-Control'] = 'no-store'      # browser mag de 404 niet onthouden
    return resp

# Achtergrond-jobs voor het (trage) bestellen+downloaden bij W2P, zodat de gebruiker niet op een
# hangende pagina hoeft te wachten: job_id -> {'status':'running'|'done'|'error','error','files'}.
# W2P-download-jobs staan in de GEDEELDE store (soort 'w2pdl') zodat voortgang-polling én het ophalen
# van het bestand over meerdere gunicorn-workers heen werken. De PDF-bytes worden base64 opgeslagen
# (tijdelijk; opgeruimd zodra gedownload of na 30 min).
def _wp_jobs_cleanup(max_age_seconds=1800):
    """Ruim oude afgeronde download-jobs op (voorkomt dat vergeten downloads bytes blijven bewaren)."""
    sharedstate.job_cleanup('w2pdl', max_age_seconds, ('done', 'error'))

def _wp_parse_quantities():
    """Lees het aantal-afdrukken-mapje uit het formulier: {promotion_document_id: aantal>1}."""
    try:
        raw = json.loads(request.form.get('quantities') or '{}')
        return {int(k): int(v) for k, v in raw.items() if int(v) > 1}
    except Exception:
        return {}

def _wp_assemble_items(ids, known, targets, job_id, quantities=None):
    """Stel per (genormaliseerd) formaat één PDF samen uit cache + live-bestellingen. Geeft
    [(formaat, pdf_bytes), ...]. Gedeeld door download én netwerk-printen van winkelpakketten.
    ``quantities`` = {promotion_document_id: aantal>1} → die kaart komt zo vaak in de output."""
    quantities = quantities or {}
    def _qty(doc_id):
        try:
            return max(1, int(quantities.get(doc_id) or quantities.get(str(doc_id)) or 1))
        except Exception:
            return 1
    import w2p_client
    w2p_client.set_progress(job_id, 3, 'Cache controleren…')
    # 1) Kijk per document of we 'm al hebben liggen van een eerdere sync-pre-fetch
    #    (static/w2p_pdfs/, zie _cache_group_pdfs) - dan hoeft er niets bij W2P besteld te worden.
    cache_rows = {}  # (cat,pid,gid,formaat) -> W2PCachedPdf
    seen_triples = {(t['category_id'], t['period_id'], t['group_id']) for t in targets.values()}
    for cat, pid, gid in seen_triples:
        for row in W2PCachedPdf.query.filter_by(category_id=cat, period_id=pid, group_id=gid).all():
            cache_rows[(cat, pid, gid, row.formaat)] = row

    # Zelf-herstel: synchroniseer de opgeslagen kaart-indeling (doc_ids) met de HUIDIGE documenten van
    # dit formaat. Verandert de normalisatie of duikt er een nieuwe formaat-variant op, dan corrigeert de
    # cache zichzelf bij de eerste download (i.p.v. voor altijd onnodig live te bestellen). De gecachte
    # PDF-bestanden blijven ongewijzigd; alleen de metadata (welke kaart bij welke pagina) wordt bijgewerkt.
    _healed = False
    for (cat, pid, gid, fmt), row in list(cache_rows.items()):
        try:
            cur_ids = json.loads(row.doc_ids or '[]')
        except Exception:
            cur_ids = []
        fresh = [d.promotion_document_id for d in
                 W2PDocument.query.filter_by(category_id=cat, period_id=pid, group_id=gid)
                 .order_by(W2PDocument.sort_index).all()
                 if _normalize_formaat(d.formaat) == fmt]
        if fresh and fresh != cur_ids:
            row.doc_ids = json.dumps(fresh); _healed = True
    if _healed:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    from collections import defaultdict as _dd
    cached_hits = []       # (formaat, cache_row, page_index) - single-up: exact per kaart knipbaar
    multiup = _dd(list)    # (cat,pid,gid,norm_fmt) -> [doc_id] - multi-up: uit gecacht vel knippen
    live_ids = []          # niet-gecacht (of split-verificatie faalt) → live bestellen
    for d in known:
        t = targets.get(str(d.promotion_document_id))
        norm_fmt = _normalize_formaat(d.formaat)
        row = cache_rows.get((t['category_id'], t['period_id'], t['group_id'], norm_fmt)) if t else None
        doc_ids_in_row = json.loads(row.doc_ids) if row else []
        if row and d.promotion_document_id in doc_ids_in_row and row.page_count == len(doc_ids_in_row):
            cached_hits.append((norm_fmt, row, doc_ids_in_row.index(d.promotion_document_id), d.promotion_document_id))
        elif (row and norm_fmt in _MULTIUP_LAYOUTS and not _is_briljant(d.formaat)):
            multiup[(t['category_id'], t['period_id'], t['group_id'], norm_fmt)].append(d.promotion_document_id)
        else:
            live_ids.append(d.promotion_document_id)
    unknown_ids = [i for i in ids if i not in {d.promotion_document_id for d in known}]
    live_ids.extend(unknown_ids)

    import fitz
    out_docs = {}   # formaat -> fitz.Document
    opened = {}     # cache_row.id -> fitz.Document (bron, blijft open tot we klaar zijn)
    try:
        if cached_hits or multiup:
            w2p_client.set_progress(job_id, 10, 'PDF\'s uit cache samenstellen…')
            pdf_dir = _w2p_pdf_dir()
            for formaat, row, idx, doc_id in cached_hits:
                src = opened.get(row.id)
                if src is None:
                    src = fitz.open(os.path.join(pdf_dir, row.path))
                    opened[row.id] = src
                out = out_docs.setdefault(formaat, fitz.open())
                for _ in range(_qty(doc_id)):
                    out.insert_pdf(src, from_page=idx, to_page=idx)
            for (cat, pid, gid, norm_fmt), sel_ids in multiup.items():
                data, done = _assemble_multiup_from_cache(cat, pid, gid, norm_fmt, sel_ids, quantities=quantities)
                if data:
                    mdoc = fitz.open(stream=data, filetype='pdf')
                    out_docs.setdefault(norm_fmt, fitz.open()).insert_pdf(mdoc)
                    mdoc.close()
                live_ids.extend([i for i in sel_ids if i not in set(done)])

        if live_ids:
            live_targets = {str(i): targets[str(i)] for i in live_ids if str(i) in targets}
            pdfs = w2p_client.order_and_download(live_ids, targets=live_targets, job_id=job_id, timeout=900)
            if isinstance(pdfs, dict) and pdfs.get('error'):
                raise RuntimeError(pdfs['error'])
            # Verdwenen kaarten (niet meer op pluslokaal.nl) markeren als 'niet meer beschikbaar'; de rest
            # is gevonden → weer op beschikbaar zetten (voor als 'ie eerder onterecht gemarkeerd was).
            nf = (pdfs or {}).get('_not_found') or []
            if nf:
                _w2p_mark_unavailable(nf)
            _w2p_mark_available([i for i in live_ids if str(i) not in {str(x) for x in nf}])
            for fmt, b in (pdfs or {}).items():
                if not isinstance(b, (bytes, bytearray)):
                    continue
                live_doc = fitz.open(stream=bytes(b), filetype='pdf')
                out_docs.setdefault(_normalize_formaat(fmt), fitz.open()).insert_pdf(live_doc)
                live_doc.close()
        elif not out_docs:
            raise RuntimeError('Geen van de geselecteerde kaarten kon gevonden worden.')
        else:
            w2p_client.set_progress(job_id, 90, 'PDF\'s samenstellen…')

        items = []
        for fmt, out in out_docs.items():
            if out.page_count:
                items.append((fmt, out.tobytes()))
            out.close()
    finally:
        for src in opened.values():
            src.close()
    return items

# W2P-formaatnaam (genormaliseerd) → interne formaat-sleutel voor lade/media-keuze.
_W2P_FMT_KEY = {
    'a3 liggend': 'a3_liggend', 'a3 staand': 'a3_staand',
    'a4 staand':  'a4_staand',  'a4 liggend': 'a4_staand',
    'a5 staand':  'a5_staand',  'sk maxi':    'sk_maxi',
}

def _w2p_fmt_key(formaat):
    return _W2P_FMT_KEY.get((formaat or '').strip().lower())

@app.route('/winkelpakketten/print/start', methods=['POST'])
@login_required
def winkelpakketten_print_start():
    u = get_current_user()
    ids = list(dict.fromkeys(int(x) for x in request.form.getlist('doc_ids') if str(x).isdigit()))
    qmap = _wp_parse_quantities()
    if not ids:
        return jsonify({'error': 'Selecteer minstens één kaart.'}), 400
    if is_demo(u):
        return jsonify({'success': True, 'printer': DEMO_PRINTER_NAAM,
                        'job_id': _enqueue_demo_print(f'{len(ids)} winkelpakket-kaart(en) printen',
                                                      [f'{len(ids)} winkelpakket-kaart(en)'])})
    fil = _active_filiaal()
    if fil is None:
        return jsonify({'error': 'Kies eerst een winkel (rechtsboven) om op te printen.'}), 400
    f = Filiaal.query.filter_by(nummer=fil).first()
    if not f or not (f.doc_printer_ip or _agent_online(f)):
        return jsonify({'error': 'Voor deze winkel is geen winkelprinter ingesteld (Beheer → Filialen).'}), 400
    if getattr(u, 'access_policy', 'anywhere') == 'ip_print':
        if not ip_in_list(client_ip(), f.allowed_ips or ''):
            return jsonify({'error': f'Printen op de winkelprinter kan alleen vanaf het winkelnetwerk. Jouw IP: {client_ip()}.'}), 403
    known = W2PDocument.query.filter(W2PDocument.promotion_document_id.in_(ids)).all()
    targets = {str(d.promotion_document_id): {'period_id': d.period_id, 'group_id': d.group_id,
                                              'category_id': d.category_id} for d in known}
    _print_jobs_cleanup()
    job_id = secrets.token_hex(12)
    plabel = f.doc_printer_name or f.doc_printer_ip or 'de winkelprinter (via agent)'
    trays = _doc_trays(f)
    ip, port = f.doc_printer_ip, f.doc_printer_port or 631
    filiaal = f.nummer
    sharedstate.job_create(job_id, 'print',
                           {'status': 'running', 'percent': 1, 'title': f'{len(ids)} winkelpakket-kaart(en)',
                            'message': 'Kaarten samenstellen…', 'error': None, 'created_at': time.time(),
                            'cancel': False, 'printer_job_id': None,
                            'ip': ip, 'port': port, 'path': '/ipp/print', 'filiaal': filiaal})

    def run():
        import w2p_client
        wp_pid = 'wpprint_' + job_id
        with app.app_context():
            try:
                # 1) PDF's samenstellen (0-70% van de balk), voortgang uit w2p_client doorgeven.
                def mirror():
                    for _ in range(6000):
                        p = w2p_client.get_progress(wp_pid) or {}
                        j = sharedstate.job_get(job_id)
                        if not j or j.get('status') != 'running':
                            return
                        upd = {'percent': max(j.get('percent', 0), min(70, int((p.get('percent', 0) or 0) * 0.7)))}
                        if p.get('message'):
                            upd['message'] = p['message']
                        sharedstate.job_set(job_id, **upd)
                        if j.get('_assembled'):
                            return
                        time.sleep(0.7)
                threading.Thread(target=mirror, daemon=True).start()
                items = _wp_assemble_items(ids, known, targets, wp_pid, quantities=qmap)
                _pj_set(job_id, _assembled=True)
                if _pj_is_cancelled(job_id):
                    raise _PrintCancelled()
                # 2) Onprintbare formaten (SK Mini/Middel) eruit filteren + waarschuwen.
                printable, skipped = [], []
                for fmt, pdf in items:
                    key = _w2p_fmt_key(fmt)
                    (printable if key else skipped).append((fmt, pdf, key))
                if not printable:
                    msg = 'Geen van de gekozen formaten kan op de winkelprinter (SK Mini/Middel → gebruik downloaden).'
                    _pj_set(job_id, status='error', percent=100, error=msg, message=msg)
                    return
                # 3) Per formaat naar de juiste lade printen (70-100%).
                docs = []
                for fmt, pdf, key in printable:
                    docs.append({'pdf': pdf, 'media': _DOC_MEDIA.get(key), 'source': trays.get(key, 'auto'),
                                 'orient': _DOC_ORIENT.get(key), 'copies': 1,
                                 'job_name': f'pluslokaal-{fmt}', 'label': fmt})
                total = max(1, len(docs))
                use_agent = _agent_online(Filiaal.query.filter_by(nummer=filiaal).first())
                for idx, d in enumerate(docs):
                    if _pj_is_cancelled(job_id):
                        raise _PrintCancelled()
                    base = 70 + int(idx / total * 30); span = max(1, int(30 / total))
                    _pj_set(job_id, percent=max(base, sharedstate.job_field(job_id, 'percent', 0)),
                            message=f'{d["label"]} → lade {d["source"]}: versturen…')
                    if use_agent:
                        ajid = _agent_enqueue(filiaal, 'document', d['pdf'],
                                              {'media': d['media'], 'source': d['source'],
                                               'orient': d['orient'], 'copies': d['copies'],
                                               'job_name': d['job_name'], 'label': d['label']})
                        _agent_wait(ajid, job_id, base, span, d['label'])
                        continue
                    pjid = _ipp_send_print_job(ip, port, '/ipp/print', d['pdf'], d['media'],
                                               d['source'], d['orient'], d['copies'], d['job_name'])
                    _poll_printer_job(ip, port, '/ipp/print', pjid, job_id, base, span, d['label'])
                extra = ''
                if skipped:
                    extra = f' ({len(skipped)} te klein formaat overgeslagen - download die apart.)'
                _pj_set(job_id, status='done', percent=100, printer_job_id=None,
                        message=f'Klaar - {len(docs)} formaat(en) verstuurd naar {plabel}.{extra}')
                log_action('winkelpakket_print', f'{len(docs)} formaat(en) → {plabel}', filiaal=filiaal)
            except _PrintCancelled:
                _pj_set(job_id, status='cancelled', percent=100, printer_job_id=None, message='Geannuleerd.')
                log_action('winkelpakket_print_geannuleerd', f'→ {plabel}', filiaal=filiaal)
            except Exception as e:
                _pj_set(job_id, status='error', percent=100, error=str(e)[:300],
                        message=f'Printen mislukt: {str(e)[:200]}')
            finally:
                w2p_client.clear_progress(wp_pid)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'success': True, 'job_id': job_id, 'printer': plabel})

@app.route('/winkelpakketten/cart-info', methods=['POST'])
@login_required
def winkelpakketten_cart_info():
    """Geef de kaartgegevens (naam/formaat/week/afdeling) van de geselecteerde doc_ids terug - voor
    het winkelmandje-overzicht (kaarten kunnen uit meerdere weken/afdelingen komen)."""
    ids = [int(x) for x in request.form.getlist('doc_ids') if str(x).isdigit()]
    if not ids:
        return jsonify([])
    docs = W2PDocument.query.filter(W2PDocument.promotion_document_id.in_(ids)).all()
    by = {d.promotion_document_id: d for d in docs}
    out = []
    for i in ids:                       # bewaar de volgorde van de selectie
        d = by.get(i)
        if not d:
            continue
        wk = (d.period_label or '').replace('PRINT_PLUS_WEEKPAKKET_', '').replace('_', ' ')
        out.append({'id': d.promotion_document_id, 'naam': d.naam, 'formaat': d.formaat,
                    'week': wk, 'afdeling': d.group_label or ''})
    return jsonify(out)

@app.route('/winkelpakketten/download/start', methods=['POST'])
@login_required
def winkelpakketten_download_start():
    _wp_jobs_cleanup()
    ids = list(dict.fromkeys(int(x) for x in request.form.getlist('doc_ids') if str(x).isdigit()))
    qmap = _wp_parse_quantities()
    if not ids:
        return jsonify({'error': 'Selecteer minstens één kaart.'}), 400
    # Bekende (categorie,periode,groep) per document meegeven uit onze sync-cache, zodat
    # w2p_client rechtstreeks naar de juiste pagina's kan navigeren i.p.v. blind te zoeken -
    # werkt ook als de selectie kaarten uit meerdere afdelingen/weken combineert.
    known = W2PDocument.query.filter(W2PDocument.promotion_document_id.in_(ids)).all()
    targets = {str(d.promotion_document_id): {'period_id': d.period_id, 'group_id': d.group_id,
                                               'category_id': d.category_id} for d in known}
    job_id = secrets.token_hex(16)
    filiaal = get_current_user().filiaal
    sharedstate.job_create(job_id, 'w2pdl',
                           {'status': 'running', 'error': None, 'files': None, 'created_at': time.time()})

    def run():
        import w2p_client, base64
        with app.app_context():
            try:
                items = _wp_assemble_items(ids, known, targets, job_id, quantities=qmap)
                if not items:
                    sharedstate.job_set(job_id, status='error',
                                        error='Geen PDF ontvangen van het printsysteem.', files=None)
                    _w2p_notify_admins('winkelpakket-download mislukt',
                                       'Geen PDF ontvangen van het printsysteem.')
                    return
                w2p_client.set_progress(job_id, 100, 'Klaar')
                log_action('winkelpakket_download', f'{len(ids)} kaart(en)', filiaal=filiaal)
                # PDF-bytes base64 in de gedeelde store, zodat elke worker het bestand kan serveren.
                files_b64 = [[fmt, base64.b64encode(b).decode('ascii')] for fmt, b in items]
                sharedstate.job_set(job_id, status='done', error=None, files=files_b64)
            except Exception as e:
                sharedstate.job_set(job_id, status='error', error=str(e)[:300], files=None)
                _w2p_notify_admins('winkelpakket-download mislukt', str(e))
            finally:
                w2p_client.clear_progress(job_id)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/winkelpakketten/download/progress/<job_id>')
@login_required
def winkelpakketten_download_progress(job_id):
    import w2p_client
    job = sharedstate.job_get(job_id)
    if not job:
        return jsonify({'status': 'unknown'}), 404
    prog = w2p_client.get_progress(job_id) or {}
    resp = {'status': job['status'],
            'percent': prog.get('percent', 100 if job['status'] == 'done' else 0),
            'message': prog.get('message', '')}
    if job['status'] == 'error':
        resp['error'] = job['error']
    if job['status'] == 'done' and job.get('files'):
        resp['file_count'] = len(job['files'])         # → client downloadt elk formaat als losse PDF
    return jsonify(resp)

@app.route('/winkelpakketten/download/file/<job_id>')
@login_required
def winkelpakketten_download_file(job_id):
    """Serveer de PDF('s) van een afgeronde download-job. Standaard (zonder ``i``) het eerste bestand;
    met ``?i=<index>`` een specifiek formaat. GEEN zip meer: de browser downloadt per formaat een losse
    PDF (personeel hoeft niets uit te pakken). De job blijft staan tot alle bestanden zijn opgehaald
    (bijgehouden in 'served'); daarna wordt 'ie opgeruimd - en anders door de 30-min-cleanup."""
    import base64
    job = sharedstate.job_get(job_id)
    if not (job and job.get('status') == 'done' and job.get('files')):
        abort(404)
    files = job['files']
    idx = request.args.get('i', 0, type=int)
    if not (0 <= idx < len(files)):
        abort(404)
    fmt, b64 = files[idx]
    data = base64.b64decode(b64)
    served = set(job.get('served') or [])
    served.add(idx)
    if len(served) >= len(files):
        sharedstate.job_delete(job_id)                 # alles opgehaald → bytes opruimen
    else:
        sharedstate.job_set(job_id, served=sorted(served))
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    bio = io.BytesIO(data); bio.seek(0)
    return send_file(bio, mimetype='application/pdf', as_attachment=True,
                     download_name=f'winkelpakket_{fmt}_{stamp}.pdf'.replace(' ', '_'))

def _w2p_sync_progress_response(job_id, state, detail_job_id=None, parallel_detail=False):
    import w2p_client
    prog = w2p_client.get_progress(job_id) or {}
    if state['running']:
        status = 'running'
    elif state.get('error'):
        status = 'error'
    elif prog.get('percent') == 100:
        status = 'done'
    else:
        status = 'idle'
    resp = {'status': status, 'percent': prog.get('percent', 0), 'message': prog.get('message', ''),
            'stats': prog.get('stats') or {}}
    if status == 'error':
        resp['error'] = state.get('error')
    if status == 'running':
        if parallel_detail:
            # Meerdere afdelingen tegelijk: verzamel de fijnmazige stap per actieve slot.
            lines = []
            for s in range(8):
                d = w2p_client.get_progress(f'sync_pdfs_d{s}')
                if d and d.get('message'):
                    lines.append(d['message'])
            resp['detail'] = ' · '.join(lines)
        elif detail_job_id:
            detail = w2p_client.get_progress(detail_job_id)
            resp['detail'] = detail.get('message', '') if detail else ''
    return jsonify(resp)

@app.route('/winkelpakketten/sync/meta', methods=['POST'])
@login_required
def winkelpakketten_sync_meta():
    if not can(get_current_user(), 'w2p_sync'):
        abort(403)
    if not _w2p_meta_bg():
        flash('Er loopt al een cache-synchronisatie.', 'error')
    else:
        flash('Cache-synchronisatie gestart.', 'success')
    return redirect(url_for('winkelpakketten'))

@app.route('/winkelpakketten/sync/meta/progress')
@login_required
def winkelpakketten_sync_meta_progress():
    return _w2p_sync_progress_response('sync_meta', _w2p_meta_state)

@app.route('/winkelpakketten/sync/pdfs', methods=['POST'])
@login_required
def winkelpakketten_sync_pdfs():
    if not can(get_current_user(), 'w2p_sync'):
        abort(403)
    # Optioneel: alleen één periode (week) downloaden i.p.v. de hele catalogus.
    period_id = request.form.get('period_id', type=int)
    only = None
    if period_id:
        only = [(c, p, g) for c, p, g in
                db.session.query(W2PDocument.category_id, W2PDocument.period_id, W2PDocument.group_id)
                .filter_by(period_id=period_id).distinct().all()]
        if not only:
            flash('Geen kaarten gevonden voor die week (eerst cache synchroniseren).', 'error')
            return redirect(url_for('winkelpakketten'))
    if not _w2p_pdf_bg(only_groups=only):
        flash('Er loopt al een download-synchronisatie.', 'error')
    else:
        flash('Download naar server gestart - dit kan lang duren (er wordt per afdeling echt besteld bij het oude systeem).', 'success')
    return redirect(url_for('winkelpakketten'))

@app.route('/winkelpakketten/sync/pdfs/progress')
@login_required
def winkelpakketten_sync_pdfs_progress():
    return _w2p_sync_progress_response('sync_pdfs', _w2p_pdf_state, parallel_detail=True)

# ─── FEEDBACK-WIDGET (melden: probleem / suggestie / idee) ────────────────────
_FB_MAX_SHOT = 4 * 1024 * 1024   # ~4MB data-URL-limiet voor een screenshot

@app.route('/feedback/submit', methods=['POST'])
@login_required
def feedback_submit():
    u = get_current_user()
    if u is None:
        return jsonify(ok=False, error='niet ingelogd'), 401
    ftype = (request.form.get('type') or 'probleem').strip().lower()
    if ftype not in FEEDBACK_TYPES:
        ftype = 'probleem'
    message = (request.form.get('message') or '').strip()
    title   = (request.form.get('title') or '').strip()[:200]
    if not message:
        return jsonify(ok=False, error='Vul een omschrijving in.'), 400
    # Simpele misbruik-/spamrem: max 20 meldingen per gebruiker per uur.
    try:
        since = datetime.now() - timedelta(hours=1)
        if Feedback.query.filter(Feedback.user_id == u.id, Feedback.created_at >= since).count() >= 20:
            return jsonify(ok=False, error='Je hebt net veel meldingen gestuurd. Probeer het over een uurtje weer.'), 429
    except Exception:
        pass
    if not title:
        title = (message[:60] + ('…' if len(message) > 60 else ''))
    shot = request.form.get('screenshot') or ''
    if shot and (not shot.startswith('data:image/') or len(shot) > _FB_MAX_SHOT):
        shot = ''   # ongeldig of te groot: laat weg i.p.v. de melding te weigeren
    fb = Feedback(
        ftype=ftype, status='nieuw', is_read=False,
        title=title, message=message[:5000],
        page_url=(request.form.get('page_url') or request.referrer or '')[:600],
        screenshot=(shot or None),
        user_id=u.id, username=u.username, user_email=u.email, user_role=u.role,
        filiaal=u.filiaal, filiaal_naam=u.filiaal_naam,
        ip=client_ip(), user_agent=(request.headers.get('User-Agent') or '')[:400],
        log_json='[]',
    )
    _fb_log(fb, f'Melding aangemaakt ({FEEDBACK_TYPES[ftype][0]})', who=u.username)
    db.session.add(fb)
    db.session.commit()
    log_action('feedback_new', f'{ftype}: {title}', user=u)
    return jsonify(ok=True)

def _require_admin():
    u = get_current_user()
    if not is_superadmin(u):
        abort(403)
    return u

@app.route('/beheer/feedback')
@login_required
def feedback_list():
    _require_admin()
    q       = (request.args.get('q') or '').strip()
    f_type  = (request.args.get('type') or '').strip()
    f_status= (request.args.get('status') or '').strip()
    query = Feedback.query
    if f_type in FEEDBACK_TYPES:
        query = query.filter(Feedback.ftype == f_type)
    if f_status in FEEDBACK_STATUS:
        query = query.filter(Feedback.status == f_status)
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            Feedback.title.ilike(like), Feedback.message.ilike(like),
            Feedback.username.ilike(like), Feedback.user_email.ilike(like),
            Feedback.filiaal_naam.ilike(like), Feedback.page_url.ilike(like),
        ))
    items = query.order_by(Feedback.created_at.desc()).all()
    # tellingen voor de filterknoppen
    counts = {
        'all': Feedback.query.count(),
        'unread': Feedback.query.filter_by(is_read=False).count(),
    }
    for k in FEEDBACK_STATUS:
        counts[k] = Feedback.query.filter_by(status=k).count()
    return render_template('feedback_list.html', items=items, counts=counts,
                           q=q, f_type=f_type, f_status=f_status)

@app.route('/beheer/feedback/<int:fid>')
@login_required
def feedback_detail(fid):
    _require_admin()
    fb = Feedback.query.get_or_404(fid)
    changed = False
    if not fb.is_read:
        fb.is_read = True
        _fb_log(fb, 'Gelezen', who=get_current_user().username)
        changed = True
    # markeer inkomende (melder-)berichten als door beheer gelezen
    for m in FeedbackMessage.query.filter_by(feedback_id=fb.id, is_admin=False, read_by_admin=False).all():
        m.read_by_admin = True; changed = True
    if changed:
        db.session.commit()
    thread = _feedback_thread(fb)
    return render_template('feedback_detail.html', fb=fb, thread=thread)

@app.route('/beheer/feedback/<int:fid>/status', methods=['POST'])
@login_required
def feedback_set_status(fid):
    u = _require_admin()
    fb = Feedback.query.get_or_404(fid)
    new = (request.form.get('status') or '').strip()
    note = (request.form.get('note') or '').strip()
    if new in FEEDBACK_STATUS and new != fb.status:
        old_lbl = FEEDBACK_STATUS.get(fb.status, (fb.status,))[0]
        fb.status = new
        _fb_log(fb, f'Status: {old_lbl} → {FEEDBACK_STATUS[new][0]}', who=u.username)
    if note:
        _fb_log(fb, f'Notitie: {note}', who=u.username)
    fb.is_read = True
    db.session.commit()
    flash('Melding bijgewerkt.', 'success')
    return redirect(url_for('feedback_detail', fid=fid))

@app.route('/beheer/feedback/<int:fid>/reageer', methods=['POST'])
@login_required
def feedback_admin_reply(fid):
    u = _require_admin()
    fb = Feedback.query.get_or_404(fid)
    body = (request.form.get('body') or '').strip()
    if not body:
        if request.headers.get('X-Requested-With') == 'fetch':
            return jsonify(ok=False, error='Leeg bericht.'), 400
        flash('Typ eerst een reactie.', 'error')
        return redirect(url_for('feedback_detail', fid=fid))
    msg = FeedbackMessage(feedback_id=fb.id, author_id=u.id, author_name=u.username,
                          is_admin=True, body=body[:5000], read_by_user=False, read_by_admin=True)
    db.session.add(msg)
    _fb_log(fb, 'Reactie naar melder verstuurd', who=u.username)
    # eerste beheer-reactie op een nieuwe melding → automatisch 'in behandeling'
    if fb.status == 'nieuw':
        fb.status = 'in_behandeling'
        _fb_log(fb, f'Status: Nieuw → {FEEDBACK_STATUS["in_behandeling"][0]}', who=u.username)
    fb.is_read = True
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify(ok=True, message=_fb_msg_dict(msg))
    flash('Reactie verstuurd.', 'success')
    return redirect(url_for('feedback_detail', fid=fid))

@app.route('/beheer/feedback/<int:fid>/lezen', methods=['POST'])
@login_required
def feedback_toggle_read(fid):
    u = _require_admin()
    fb = Feedback.query.get_or_404(fid)
    fb.is_read = not fb.is_read
    _fb_log(fb, 'Gemarkeerd als ' + ('gelezen' if fb.is_read else 'ongelezen'), who=u.username)
    db.session.commit()
    return redirect(request.referrer or url_for('feedback_list'))

@app.route('/beheer/feedback/<int:fid>/verwijder', methods=['POST'])
@login_required
def feedback_delete(fid):
    _require_admin()
    fb = Feedback.query.get_or_404(fid)
    FeedbackMessage.query.filter_by(feedback_id=fb.id).delete(synchronize_session=False)
    db.session.delete(fb)
    db.session.commit()
    flash('Melding verwijderd.', 'success')
    return redirect(url_for('feedback_list'))

# ── Gesprek: gedeelde endpoints voor melder én beheer ──
def _feedback_access(fid):
    """Geef (feedback, is_admin) terug als de huidige gebruiker de melding mag zien; anders abort."""
    u = get_current_user()
    fb = Feedback.query.get_or_404(fid)
    if is_superadmin(u):
        return fb, u, True
    if u and fb.user_id == u.id:
        return fb, u, False
    abort(403)

@app.route('/feedback/mijn')
@login_required
def feedback_mine():
    """JSON: de eigen meldingen van de huidige gebruiker (voor het ?-widget)."""
    u = get_current_user()
    items = (Feedback.query.filter_by(user_id=u.id)
             .order_by(Feedback.created_at.desc()).all())
    out = []
    for f in items:
        replies = FeedbackMessage.query.filter_by(feedback_id=f.id).count()
        unread = FeedbackMessage.query.filter_by(feedback_id=f.id, is_admin=True, read_by_user=False).count()
        smeta = FEEDBACK_STATUS.get(f.status, (f.status, '#666', '#eee'))
        tmeta = FEEDBACK_TYPES.get(f.ftype, ('?', 'fa-circle'))
        out.append({
            'id': f.id, 'title': f.title, 'ftype': f.ftype, 'ftype_label': tmeta[0], 'ftype_icon': tmeta[1],
            'status': f.status, 'status_label': smeta[0], 'status_color': smeta[1], 'status_bg': smeta[2],
            'at': f.created_at.strftime('%d-%m-%Y %H:%M'), 'replies': replies, 'unread': unread,
        })
    return jsonify(ok=True, items=out, unread_total=user_unread_replies(u))

@app.route('/feedback/updates')
@login_required
def feedback_updates():
    """JSON: lichte poll voor de melder - hoeveel nieuwe beheer-reacties zijn er?"""
    u = get_current_user()
    return jsonify(ok=True, unread_total=user_unread_replies(u))

@app.route('/feedback/<int:fid>/thread')
@login_required
def feedback_thread_json(fid):
    """JSON: volledige gespreksdraad. Toegankelijk voor beheer of de melder zelf.
    Bij openen worden de 'andere kant'-berichten als gelezen gemarkeerd."""
    fb, u, admin = _feedback_access(fid)
    changed = False
    if admin:
        for m in FeedbackMessage.query.filter_by(feedback_id=fb.id, is_admin=False, read_by_admin=False).all():
            m.read_by_admin = True; changed = True
    else:
        for m in FeedbackMessage.query.filter_by(feedback_id=fb.id, is_admin=True, read_by_user=False).all():
            m.read_by_user = True; changed = True
    if changed:
        db.session.commit()
    smeta = FEEDBACK_STATUS.get(fb.status, (fb.status, '#666', '#eee'))
    return jsonify(ok=True, id=fb.id, title=fb.title,
                   status=fb.status, status_label=smeta[0], status_color=smeta[1], status_bg=smeta[2],
                   messages=_feedback_thread(fb), unread_total=(0 if admin else user_unread_replies(u)))

@app.route('/feedback/<int:fid>/reageer', methods=['POST'])
@login_required
def feedback_user_reply(fid):
    """De melder reageert op de eigen melding."""
    fb, u, admin = _feedback_access(fid)
    body = (request.form.get('body') or '').strip()
    if not body:
        return jsonify(ok=False, error='Leeg bericht.'), 400
    msg = FeedbackMessage(feedback_id=fb.id, author_id=u.id, author_name=u.username,
                          is_admin=admin, body=body[:5000],
                          read_by_user=(not admin) or False, read_by_admin=admin or False)
    # een reactie van de melder zet de melding weer op 'ongelezen' voor beheer
    if not admin:
        fb.is_read = False
        _fb_log(fb, 'Melder reageerde', who=u.username)
    db.session.add(msg)
    db.session.commit()
    return jsonify(ok=True, message=_fb_msg_dict(msg))

# ─── LOGBOEK / AUDIT (beheer) ─────────────────────────────────────────────────
# Categorie → actie-patronen (SQL LIKE) voor het filter.
_LOG_CATS = {
    'inloggen':   ['login%', 'logout', 'mfa%'],
    'printen':    ['print%', 'winkelpakket_print%', 'printer%'],
    'gebruikers': ['gebruiker%', 'rol%', 'filiaal%', 'account%'],
    'feedback':   ['feedback%'],
}
# Vriendelijke labels voor veelvoorkomende acties (rest toont de ruwe code).
_LOG_LABELS = {
    'login': 'Ingelogd', 'login_mislukt': 'Mislukte login', 'login_geblokkeerd': 'Login geblokkeerd',
    'login_geweigerd': 'Login geweigerd', 'logout': 'Uitgelogd',
    'mfa_ingesteld': '2FA ingesteld', 'mfa_gereset': '2FA gereset', 'mfa_mislukt': '2FA-code onjuist',
    'print_netwerk_start': 'Print gestart', 'print_netwerk_klaar': 'Print klaar',
    'print_netwerk_bulk': 'Bulk-print', 'print_netwerk': 'Label geprint',
    'print_mislukt': 'Print mislukt', 'print_geannuleerd': 'Print geannuleerd',
    'print_geblokkeerd_ip': 'Print geblokkeerd (IP)', 'printer_test': 'Printertest',
    'printer_test_doc': 'Printertest (document)', 'winkelpakket_print': 'Winkelpakket geprint',
    'winkelpakket_print_geannuleerd': 'Winkelpakket-print geannuleerd',
    'feedback_new': 'Nieuwe feedback', 'gebruiker_gewijzigd': 'Gebruiker gewijzigd',
    'gebruiker_gemaakt': 'Gebruiker aangemaakt', 'gebruiker_verwijderd': 'Gebruiker verwijderd',
    'demo_toggle': 'Demo aan/uit', 'mfa_reset': '2FA gereset',
}
app.jinja_env.globals['LOG_LABELS'] = _LOG_LABELS

@app.route('/beheer/logs')
@login_required
def logs_view():
    _require_admin()
    q   = (request.args.get('q') or '').strip()
    cat = (request.args.get('cat') or '').strip()
    query = AuditLog.query
    if cat in _LOG_CATS:
        conds = [AuditLog.action.ilike(p) for p in _LOG_CATS[cat]]
        query = query.filter(db.or_(*conds))
    elif cat == 'overig':
        allpat = [p for pats in _LOG_CATS.values() for p in pats]
        for p in allpat:
            query = query.filter(~AuditLog.action.ilike(p))
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            AuditLog.action.ilike(like), AuditLog.username.ilike(like),
            AuditLog.detail.ilike(like), AuditLog.ip.ilike(like)))
    total = query.count()
    items = query.order_by(AuditLog.created_at.desc()).limit(500).all()
    counts = {'all': AuditLog.query.count()}
    return render_template('logs.html', items=items, total=total, shown=len(items),
                           q=q, cat=cat, cats=list(_LOG_CATS.keys()), counts=counts)

# ─── RONDLEIDING (onboarding) ─────────────────────────────────────────────────
@app.route('/tour/done', methods=['POST'])
@login_required
def tour_done():
    """Zet de rondleiding uit voor de huidige gebruiker (na afronden of 'niet meer tonen')."""
    u = get_current_user()
    if u:
        u.show_tour = False
        db.session.commit()
    return jsonify(ok=True)

# ─── CHANGELOG / VERSIEGESCHIEDENIS ───────────────────────────────────────────
@app.route('/changelog')
@login_required
def changelog():
    u = get_current_user()
    if is_demo(u):
        abort(404)   # niet zichtbaar voor het demo-account
    try:
        with open(os.path.join(os.path.dirname(__file__), 'changelog.md'), encoding='utf-8') as fh:
            body = fh.read()
    except Exception:
        body = '# Wat is er nieuw?\n\nEr is nog geen versiegeschiedenis beschikbaar.'
    return render_template('changelog.html', body=body, version=APP_VERSION)

# ─── KENNISBANK / WIKI (hulp) ─────────────────────────────────────────────────
@app.route('/hulp')
@login_required
def kb_index():
    arts = KbArticle.query.order_by(KbArticle.sort_index, KbArticle.title).all()
    # groepeer op categorie, in volgorde van eerste voorkomen
    cats, order = {}, []
    for a in arts:
        c = a.category or 'Overig'
        if c not in cats:
            cats[c] = []; order.append(c)
        cats[c].append(a)
    groups = [(c, cats[c]) for c in order]
    return render_template('kb_index.html', groups=groups, total=len(arts))

@app.route('/hulp/<slug>')
@login_required
def kb_article(slug):
    art = KbArticle.query.filter_by(slug=slug).first_or_404()
    others = (KbArticle.query.filter(KbArticle.category == art.category, KbArticle.id != art.id)
              .order_by(KbArticle.sort_index, KbArticle.title).all()) if art.category else []
    return render_template('kb_article.html', art=art, others=others)

@app.route('/beheer/kennisbank')
@login_required
def kb_manage():
    _require_admin()
    arts = KbArticle.query.order_by(KbArticle.sort_index, KbArticle.category, KbArticle.title).all()
    return render_template('kb_manage.html', arts=arts)

@app.route('/beheer/kennisbank/nieuw', methods=['GET', 'POST'])
@app.route('/beheer/kennisbank/<int:aid>/bewerken', methods=['GET', 'POST'])
@login_required
def kb_edit(aid=None):
    u = _require_admin()
    art = KbArticle.query.get_or_404(aid) if aid else None
    if request.method == 'POST':
        title = (request.form.get('title') or '').strip()
        if not title:
            flash('Titel is verplicht.', 'error')
            return render_template('kb_edit.html', art=art, form=request.form)
        slug = (request.form.get('slug') or '').strip() or _slugify(title)
        # zorg voor een uniek slug
        base, k = slug, 2
        exists = KbArticle.query.filter(KbArticle.slug == slug,
                                        KbArticle.id != (art.id if art else -1)).first()
        while exists:
            slug = f'{base}-{k}'; k += 1
            exists = KbArticle.query.filter(KbArticle.slug == slug,
                                            KbArticle.id != (art.id if art else -1)).first()
        if art is None:
            art = KbArticle()
            db.session.add(art)
        art.title      = title[:200]
        art.slug       = slug
        art.category   = (request.form.get('category') or '').strip()[:120] or 'Overig'
        art.icon       = (request.form.get('icon') or '').strip()[:40] or 'fa-book'
        art.summary    = (request.form.get('summary') or '').strip()[:400]
        art.body       = request.form.get('body') or ''
        try:
            art.sort_index = int(request.form.get('sort_index') or 0)
        except ValueError:
            art.sort_index = 0
        art.updated_by = u.username
        db.session.commit()
        flash('Artikel opgeslagen.', 'success')
        return redirect(url_for('kb_manage'))
    return render_template('kb_edit.html', art=art, form=None)

@app.route('/beheer/kennisbank/<int:aid>/verwijder', methods=['POST'])
@login_required
def kb_delete(aid):
    _require_admin()
    art = KbArticle.query.get_or_404(aid)
    db.session.delete(art)
    db.session.commit()
    flash('Artikel verwijderd.', 'success')
    return redirect(url_for('kb_manage'))

# ─── STARTUP ──────────────────────────────────────────────────────────────────
def _migrate_db():
    """Voeg nieuwe kolommen toe aan bestaande tabellen (SQLite)."""
    migrations = [
        "ALTER TABLE user ADD COLUMN filiaal_naam VARCHAR(100)",
        "ALTER TABLE card ADD COLUMN filiaal_naam VARCHAR(100)",
        "ALTER TABLE user ADD COLUMN avatar TEXT",
        "ALTER TABLE user ADD COLUMN email VARCHAR(200)",
        # Labels-module
        "ALTER TABLE user ADD COLUMN access_policy VARCHAR(20) DEFAULT 'anywhere'",
        "ALTER TABLE user ADD COLUMN allowed_ips TEXT",
        "ALTER TABLE user ADD COLUMN approved BOOLEAN DEFAULT 1",
        "ALTER TABLE user ADD COLUMN must_change_password BOOLEAN DEFAULT 0",
        "ALTER TABLE user ADD COLUMN mfa_secret VARCHAR(64)",
        "ALTER TABLE user ADD COLUMN mfa_enabled BOOLEAN DEFAULT 0",
        "ALTER TABLE user ADD COLUMN show_tour BOOLEAN DEFAULT 0",
        "ALTER TABLE user ADD COLUMN portaal_user VARCHAR(200)",
        "ALTER TABLE user ADD COLUMN portaal_pass_enc TEXT",
        "ALTER TABLE user ADD COLUMN portaal_status VARCHAR(20) DEFAULT 'none'",
        "ALTER TABLE user ADD COLUMN portaal_checked DATETIME",
        "ALTER TABLE filiaal ADD COLUMN login_hint TEXT",
        "ALTER TABLE filiaal ADD COLUMN print_only BOOLEAN DEFAULT 0",
        "ALTER TABLE filiaal ADD COLUMN printer_name VARCHAR(120)",
        "ALTER TABLE filiaal ADD COLUMN printer_ip VARCHAR(64)",
        "ALTER TABLE filiaal ADD COLUMN printer_port INTEGER DEFAULT 9100",
        "ALTER TABLE filiaal ADD COLUMN printer_dpi INTEGER DEFAULT 300",
        "ALTER TABLE filiaal ADD COLUMN printer_protocol VARCHAR(16) DEFAULT 'tspl'",
        "ALTER TABLE filiaal ADD COLUMN printer_label_w FLOAT DEFAULT 45.0",
        "ALTER TABLE filiaal ADD COLUMN printer_label_h FLOAT DEFAULT 40.0",
        "ALTER TABLE filiaal ADD COLUMN printer_offset_x INTEGER DEFAULT 0",
        "ALTER TABLE filiaal ADD COLUMN printer_offset_y INTEGER DEFAULT 0",
        "ALTER TABLE filiaal ADD COLUMN printer_rotation INTEGER DEFAULT 0",
        "ALTER TABLE filiaal ADD COLUMN allowed_ips TEXT",
        "ALTER TABLE filiaal ADD COLUMN doc_printer_name VARCHAR(120)",
        "ALTER TABLE filiaal ADD COLUMN doc_printer_ip VARCHAR(64)",
        "ALTER TABLE filiaal ADD COLUMN doc_printer_port INTEGER DEFAULT 631",
        "ALTER TABLE filiaal ADD COLUMN doc_printer_trays TEXT",
        "ALTER TABLE w2_p_document ADD COLUMN sort_index INTEGER DEFAULT 0",
        "ALTER TABLE w2_p_document ADD COLUMN unavailable_at DATETIME",
        "ALTER TABLE user ADD COLUMN notify_w2p_fail BOOLEAN DEFAULT 0",
    ]
    with db.engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
            except Exception:
                pass
    _migrate_w2p_dagdeal_docids()
    # (agent-kolommen)
    for sql in ["ALTER TABLE filiaal ADD COLUMN agent_key VARCHAR(64)",
                "ALTER TABLE filiaal ADD COLUMN agent_seen DATETIME",
                "ALTER TABLE filiaal ADD COLUMN agent_version VARCHAR(20)",
                "ALTER TABLE filiaal ADD COLUMN agent_info TEXT",
                "ALTER TABLE filiaal ADD COLUMN agent_web_pass VARCHAR(40)"]:
        try:
            with db.engine.connect() as conn:
                conn.execute(text(sql)); conn.commit()
        except Exception:
            pass
    # Afzender weg van no-reply (eenmalig; admin kan 'm daarna zelf aanpassen in Mailinstellingen).
    try:
        if (get_setting('smtp_from', '') or '').lower().startswith('noreply@'):
            set_setting('smtp_from', 'info@mail.pluslokaal.com')
    except Exception:
        pass

def _migrate_w2p_dagdeal_docids():
    """Eenmalige data-fix: herbereken de doc_ids van bestaande winkelpakket-cache-rijen met de nieuwe
    normalisatie (Dagdeal-suffix gestript). De gecachte PDF-bestanden bevatten al basis + Dagdeal-kaarten;
    alleen de opgeslagen doc_ids misten de Dagdeal-kaarten, waardoor downloaden onnodig live bestelde."""
    try:
        if get_setting('w2p_dagdeal_docids_fix', '') == '1':
            return
        rows = W2PCachedPdf.query.all()
        for r in rows:
            docs = (W2PDocument.query.filter_by(category_id=r.category_id, period_id=r.period_id,
                                                group_id=r.group_id).order_by(W2PDocument.sort_index).all())
            new_ids = [d.promotion_document_id for d in docs if _normalize_formaat(d.formaat) == r.formaat]
            if new_ids and new_ids != json.loads(r.doc_ids or '[]'):
                r.doc_ids = json.dumps(new_ids)
        db.session.commit()
        set_setting('w2p_dagdeal_docids_fix', '1')
    except Exception:
        db.session.rollback()

def _seed_filialen():
    """Maak Filiaal-records voor elk filiaalnummer dat al bij gebruikers voorkomt."""
    existing = {f.nummer for f in Filiaal.query.all()}
    for u in User.query.all():
        if u.filiaal is not None and u.filiaal not in existing:
            db.session.add(Filiaal(nummer=u.filiaal, naam=u.filiaal_naam))
            existing.add(u.filiaal)
    db.session.commit()

def _seed_roles():
    """Maak de 3 basisrollen aan (idempotent). Rechten blijven daarna bewerkbaar via de UI."""
    defs = [
        ('admin',           'Beheerder',       list(_ASSIGNABLE_KEYS), True, False),
        ('ondernemer',      'Ondernemer',      ['labels_make', 'labels_history', 'products', 'team', 'view_audit'], True, True),
        ('medewerker',      'Medewerker',      ['labels_make', 'labels_history', 'products'], True, True),
        ('service_kantoor', 'Service Kantoor', ['w2p_sync'], True, False),
    ]
    for name, label, perms, sysrole, scoped in defs:
        if not Role.query.filter_by(name=name).first():
            db.session.add(Role(name=name, label=label, permissions=json.dumps(perms),
                                is_system=sysrole, store_scoped=scoped))
    db.session.commit()

def _seed_demo():
    """Zorg voor de demo-winkel + het demo-account (idempotent). Standaard ingeschakeld."""
    f = Filiaal.query.filter_by(nummer=DEMO_FILIAAL).first()
    if not f:
        f = Filiaal(nummer=DEMO_FILIAAL, naam='Demo')
        db.session.add(f)
    # dummy-printer zodat de print-UI een printernaam toont; echt printen wordt gesimuleerd
    if not f.doc_printer_ip:
        f.doc_printer_ip = '0.0.0.0'
    f.doc_printer_name = DEMO_PRINTER_NAAM
    db.session.commit()
    if not User.query.filter_by(username='demo').first():
        db.session.add(User(username='demo', password=hash_password('demo'),
                            role='medewerker', filiaal=DEMO_FILIAAL, filiaal_naam='Demo',
                            email='demo@pluslokaal.local', approved=True, must_change_password=False))
        db.session.commit()
    if get_setting('demo_enabled', '') == '':
        set_setting('demo_enabled', '1')

def _seed_kb():
    """Zet de start-artikelen van de kennisbank klaar (idempotent: alleen nog niet
    bestaande slugs worden toegevoegd; bestaande blijven ongemoeid zodat handmatige
    aanpassingen niet overschreven worden)."""
    try:
        from kb_seed import ARTICLES
    except Exception:
        return
    existing = {a.slug for a in KbArticle.query.all()}
    added = False
    for a in ARTICLES:
        if a['slug'] in existing:
            continue
        db.session.add(KbArticle(
            slug=a['slug'], title=a['title'], category=a.get('category', 'Overig'),
            icon=a.get('icon', 'fa-book'), summary=a.get('summary', ''),
            body=a.get('body', ''), sort_index=a.get('sort_index', 0),
            updated_by='systeem',
        ))
        added = True
    if added:
        db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        _migrate_db()
        sharedstate.init(os.path.join(app.instance_path, 'pluslokaal.db'))
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin',
                                password=hash_password('admin'),
                                role='admin', filiaal=1))
            db.session.commit()
            print("✅ Admin aangemaakt (wachtwoord: admin)")
        _seed_filialen()
        _seed_roles()
        _seed_demo()
        _seed_kb()
        _w2p_nightly_scheduler()
    # threaded=True → requests worden concurrent afgehandeld i.p.v. één voor één. Essentieel voor de
    # portaal-proxy (browser haalt tientallen assets parallel op) en voorkomt dat een trage upstream-
    # fetch de hele app blokkeert voor andere gebruikers.
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=True)
else:
    # Gestart via gunicorn/wsgi
    with app.app_context():
        db.create_all()
        _migrate_db()
        sharedstate.init(os.path.join(app.instance_path, 'pluslokaal.db'))
        if not User.query.filter_by(username='admin').first():
            db.session.add(User(username='admin',
                                password=hash_password('admin'),
                                role='admin', filiaal=1))
            db.session.commit()
        _seed_filialen()
        _seed_roles()
        _seed_demo()
        _seed_kb()
        _w2p_nightly_scheduler()
