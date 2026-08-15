"""Portaal - transparante per-gebruiker proxy naar pluslokaal.nl.

pluslokaal.nl is een ASP.NET-WebForms/Bootstrap-site (geen Cloudflare/JS-gate), dus we kunnen met
een lichte `requests.Session` inloggen en de sessie warm houden. Elke gebruiker koppelt eenmalig zijn
pluslokaal.nl-inloggegevens; wij loggen op de achtergrond in, bewaren de cookies server-side en
proxyen de pagina's door. Loginmechanisme: GET login.aspx → __VIEWSTATE + __VIEWSTATEGENERATOR lezen →
POST username/password. Sessie verlopen ⇒ pluslokaal.nl redirect naar login.aspx; dat detecteren we en
we loggen transparant opnieuw in.

Best-effort: het is een externe site. Als pluslokaal.nl z'n loginformulier wijzigt, pas dan
`_LOGIN_FIELDS`/`_parse_hidden` hieronder aan.
"""
import threading
import requests
from urllib.parse import urlsplit

BASE = "https://www.pluslokaal.nl"
HOST = "www.pluslokaal.nl"
LOGIN_PATH = "/login.aspx"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Per-gebruiker een warme sessie. Beschermd door _lock voor het aanmaken; elke sessie heeft z'n
# eigen lock zodat requests van dezelfde gebruiker geserialiseerd worden (ASP.NET-sessie is stateful).
_sessions = {}          # uid -> {"s": Session, "lock": Lock, "user": str, "pw": str}
_lock = threading.Lock()


def _new_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "nl-NL,nl;q=0.9",
    })
    # Grotere connection-pool zodat statische assets PARALLEL opgehaald kunnen worden (default = 10).
    try:
        from requests.adapters import HTTPAdapter
        ad = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        s.mount("https://", ad)
        s.mount("http://", ad)
    except Exception:
        pass
    return s


def _parse_hidden(html):
    """Haal de ASP.NET hidden-velden (__VIEWSTATE, __VIEWSTATEGENERATOR, evt. __EVENTVALIDATION) uit
    de loginpagina. Regex i.p.v. een parser: robuust en zonder afhankelijkheid, de velden staan als
    <input type="hidden" name=".." value=".." />."""
    import re
    out = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        m = re.search(
            r'<input[^>]*\bname="' + re.escape(name) + r'"[^>]*\bvalue="([^"]*)"',
            html, re.I)
        if not m:
            m = re.search(
                r'<input[^>]*\bvalue="([^"]*)"[^>]*\bname="' + re.escape(name) + r'"',
                html, re.I)
        if m:
            out[name] = m.group(1)
    return out


def _looks_logged_out(resp):
    """True als de response feitelijk de loginpagina is (sessie verlopen / niet ingelogd)."""
    try:
        if urlsplit(resp.url).path.lower().endswith("login.aspx"):
            return True
    except Exception:
        pass
    return False


def _do_login(s, username, password):
    """Voer de form-login uit op de gedeelde sessie `s`. Return (ok, message)."""
    try:
        r = s.get(BASE + LOGIN_PATH, timeout=25)
    except Exception as e:
        return False, f"Kon loginpagina niet laden: {e}"[:200]
    fields = _parse_hidden(r.text)
    payload = {
        "username": username,
        "password": password,
        "remember": "on",
    }
    payload.update(fields)
    try:
        r2 = s.post(BASE + LOGIN_PATH, data=payload, timeout=25, allow_redirects=True)
    except Exception as e:
        return False, f"Login-verzoek mislukte: {e}"[:200]
    # Succes = we belanden NIET meer op de loginpagina en krijgen geen loginformulier terug.
    if _looks_logged_out(r2):
        return False, "Gebruikersnaam of wachtwoord onjuist."
    # Extra check: een beschermde pagina ophalen; als die weer naar login stuurt → mislukt.
    try:
        home = s.get(BASE + "/", timeout=25, allow_redirects=True)
        if _looks_logged_out(home):
            return False, "Gebruikersnaam of wachtwoord onjuist."
    except Exception:
        pass
    return True, "ok"


def login(uid, username, password):
    """(Her)koppel een gebruiker: maak een verse sessie en log in. Return (ok, message)."""
    s = _new_session()
    ok, msg = _do_login(s, username, password)
    if ok:
        with _lock:
            _sessions[uid] = {"s": s, "lock": threading.Lock(), "user": username, "pw": password}
    return ok, msg


def logout(uid):
    with _lock:
        _sessions.pop(uid, None)


def has_session(uid):
    with _lock:
        return uid in _sessions


def ensure(uid, username, password):
    """Zorg dat er een warme sessie is (bv. na herstart van de app). Return (ok, message)."""
    with _lock:
        have = uid in _sessions
    if have:
        return True, "ok"
    return login(uid, username, password)


def fetch(uid, path, username, password, method="GET", data=None, headers=None,
          content_type=None, stateful=True):
    """Haal `path` (root-relatief, incl. querystring) op namens de gebruiker via diens warme sessie.
    Logt transparant opnieuw in als de sessie verlopen blijkt. Return een requests.Response of None
    bij een harde fout. `data` = raw request body (bytes) voor POST.

    `stateful=False` (voor statische assets: css/js/afbeeldingen/fonts) laat de per-sessie lock LOS,
    zodat de browser die assets PARALLEL kan ophalen i.p.v. één voor één. urllib3's connection-pool is
    thread-safe en statische GET's muteren de ASP.NET-sessie/cookies niet, dus dit is veilig; alleen
    navigatie/POST (stateful) blijft geserialiseerd."""
    ok, _ = ensure(uid, username, password)
    if not ok:
        return None
    with _lock:
        sess = _sessions.get(uid)
    if not sess:
        return None
    url = BASE + (path if path.startswith("/") else "/" + path)
    hdrs = {}
    if content_type:
        hdrs["Content-Type"] = content_type
    if headers:
        hdrs.update(headers)

    def _req():
        if stateful:
            with sess["lock"]:
                return sess["s"].request(
                    method, url, data=data, headers=hdrs or None,
                    timeout=30, allow_redirects=True)
        return sess["s"].request(
            method, url, data=data, headers=hdrs or None,
            timeout=30, allow_redirects=True)

    try:
        r = _req()
    except Exception:
        return None
    # Sessie verlopen? Eén keer opnieuw inloggen en de request herhalen.
    if _looks_logged_out(r) and method.upper() == "GET":
        with sess["lock"]:
            ok2, _ = _do_login(sess["s"], username, password)
        if ok2:
            try:
                r = _req()
            except Exception:
                return None
    return r
