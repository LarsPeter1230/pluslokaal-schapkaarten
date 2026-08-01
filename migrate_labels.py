"""Eenmalige migratie: PLUS Label Manager (pluslabels.db) -> pluslokaal-DB.
Idempotent: mergt op natuurlijke sleutels, slaat botsende gebruikersnamen over,
en markeert zichzelf met Setting('labels_migrated'). Herbruikbaar zonder duplicaten."""
import sqlite3, os, json, shutil
from datetime import datetime
import app as m

SRC = '/root/labelmgr_ref/pluslabels.db'
UPLOADS = '/root/labelmgr_ref/app/static/uploads'
ROLE_MAP = {'superadmin': 'admin', 'owner': 'ondernemer', 'user': 'medewerker'}

def parse_dt(s):
    if not s: return datetime.now()
    for f in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try: return datetime.strptime(s, f)
        except ValueError: pass
    return datetime.now()

def run(force=False):
    src = sqlite3.connect(SRC); src.row_factory = sqlite3.Row
    with m.app.app_context():
        if m.get_setting('labels_migrated', '') == '1' and not force:
            print('Al gemigreerd (Setting labels_migrated=1). Gebruik force=True om te forceren.')
            return
        report = {'filialen': 0, 'users_new': 0, 'users_skip': [], 'products': 0,
                  'formats': 0, 'jobs': 0, 'audit': 0}

        # 1) stores -> Filiaal (store_number = nummer)
        store_to_fil = {}
        for s in src.execute('select * from stores'):
            nr = int(s['store_number']) if s['store_number'] else s['id']
            store_to_fil[s['id']] = nr
            f = m.Filiaal.query.filter_by(nummer=nr).first()
            if not f:
                f = m.Filiaal(nummer=nr, naam=(s['name'] or '').replace('PLUS ', '', 1) or None)
                m.db.session.add(f); report['filialen'] += 1
            # printerconfig altijd (bij)werken vanuit de bron
            f.printer_name = s['printer_name']; f.printer_ip = s['printer_ip']
            f.printer_port = s['printer_port'] or 9100; f.printer_dpi = s['printer_dpi'] or 300
            f.printer_protocol = (s['printer_protocol'] or 'tspl'); f.printer_label_w = s['printer_label_w'] or 45.0
            f.printer_label_h = s['printer_label_h'] or 40.0; f.printer_offset_x = s['printer_offset_x'] or 0
            f.printer_offset_y = s['printer_offset_y'] or 0; f.printer_rotation = s['printer_rotation'] or 0
            f.allowed_ips = s['allowed_ips']
        m.db.session.commit()

        # 2) users
        uid_to_name = {}
        for u in src.execute('select * from users'):
            uid_to_name[u['id']] = u['username']
            existing = m.User.query.filter((m.User.username == u['username'])).first()
            if existing:
                report['users_skip'].append(u['username']); continue
            fil = store_to_fil.get(u['store_id']) or 1
            fobj = m.Filiaal.query.filter_by(nummer=fil).first()
            nu = m.User(
                username=u['username'], password=u['password_hash'],
                role=ROLE_MAP.get(u['role'], 'medewerker'), filiaal=fil,
                filiaal_naam=(fobj.naam if fobj else None), email=u['email'],
                access_policy=u['access_policy'] or 'anywhere', allowed_ips=u['allowed_ips'],
                approved=bool(u['approved']), must_change_password=bool(u['must_change_password']))
            m.db.session.add(nu); report['users_new'] += 1
        m.db.session.commit()

        # 3) products (upsert op filiaal+barcode)
        for p in src.execute('select * from products'):
            fil = store_to_fil.get(p['store_id']) or 1
            q = m.Product.query.filter_by(filiaal=fil, barcode=p['barcode'])
            if p['barcode'] and q.first():
                continue
            m.db.session.add(m.Product(
                filiaal=fil, name=p['name'], barcode=p['barcode'],
                barcode_type=(p['barcode_type'] or 'ean13').lower(), price=p['price'],
                sku=p['sku'], category=p['category'], active=bool(p['active'])))
            report['products'] += 1
        m.db.session.commit()

        # 4) label_formats (upsert op naam+filiaal)
        for lf in src.execute('select * from label_formats'):
            fil = store_to_fil.get(lf['store_id']) if lf['store_id'] else None
            if m.LabelFormat.query.filter_by(name=lf['name'], filiaal=fil).first():
                continue
            m.db.session.add(m.LabelFormat(name=lf['name'], width_mm=lf['width_mm'],
                height_mm=lf['height_mm'], is_default=bool(lf['is_default']), filiaal=fil))
            report['formats'] += 1
        m.db.session.commit()

        # 5) label_jobs
        for j in src.execute('select * from label_jobs'):
            fil = store_to_fil.get(j['store_id']) or 1
            m.db.session.add(m.LabelJob(
                filiaal=fil, created_by=uid_to_name.get(j['created_by']),
                format_id=j['format_id'], name=j['name'], status=j['status'] or 'concept',
                items_json=j['items_json'] or '[]', created_at=parse_dt(j['created_at']),
                printed_at=parse_dt(j['printed_at']) if j['printed_at'] else None,
                price_unit=j['price_unit'] or 'stuk', extra_line1=j['extra_line1'],
                extra_line2=j['extra_line2'], show_date=bool(j['show_date']),
                show_logo=bool(j['show_logo'])))
            report['jobs'] += 1
        m.db.session.commit()

        # 6) audit_logs
        for a in src.execute('select * from audit_logs'):
            fil = store_to_fil.get(a['store_id']) if a['store_id'] else None
            m.db.session.add(m.AuditLog(created_at=parse_dt(a['created_at']), user_id=None,
                username=a['username'], filiaal=fil, action=a['action'],
                detail=(a['detail'] or '')[:500], ip=a['ip']))
            report['audit'] += 1
        m.db.session.commit()

        # 7) logo overnemen
        srclogo = os.path.join(UPLOADS, 'app_logo.png')
        if os.path.exists(srclogo):
            dst = os.path.join(os.path.dirname(__file__), 'static', 'img', 'label-logo.png')
            shutil.copy(srclogo, dst)
            m.set_setting('label_logo', 'static/img/label-logo.png')

        m.set_setting('labels_migrated', '1')
        print('MIGRATIE KLAAR:'); print(json.dumps(report, indent=2, default=str))
    src.close()

if __name__ == '__main__':
    import sys
    run(force=('--force' in sys.argv))
