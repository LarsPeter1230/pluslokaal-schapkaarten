"""Gedeeld wachtwoordbeleid + tijdelijk-wachtwoordgenerator."""
import re
import secrets
import string

# Eis: minimaal 10 tekens, een hoofdletter, een cijfer en een leesteken.
PASSWORD_HINT = 'Minimaal 10 tekens, met een hoofdletter, een cijfer en een leesteken.'
_PUNCT = '!@#$%^&*?-_=+.'


def validate_password(pw):
    """Geeft een foutmelding terug, of None als het wachtwoord voldoet."""
    if not pw or len(pw) < 10:
        return 'Wachtwoord moet minstens 10 tekens bevatten.'
    if not re.search(r'[A-Z]', pw):
        return 'Wachtwoord moet minstens één hoofdletter bevatten.'
    if not re.search(r'\d', pw):
        return 'Wachtwoord moet minstens één cijfer bevatten.'
    if not re.search(r'[^A-Za-z0-9]', pw):
        return 'Wachtwoord moet minstens één leesteken bevatten.'
    return None


def generate_temp_password(length=12):
    """Genereer een tijdelijk wachtwoord dat aan het beleid voldoet."""
    length = max(length, 10)
    picks = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(_PUNCT),
    ]
    pool = string.ascii_letters + string.digits + _PUNCT
    picks += [secrets.choice(pool) for _ in range(length - len(picks))]
    secrets.SystemRandom().shuffle(picks)
    return ''.join(picks)
