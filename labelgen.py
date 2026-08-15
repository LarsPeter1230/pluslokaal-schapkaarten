"""Genereert printer-native labelcommando's (ZPL / EPL / TSPL) voor directe
netwerkprint op poort 9100. Tekst + echte barcode; logo wordt hierbij weggelaten
(raster-logo per taal is te printerspecifiek - gebruik daarvoor de browserprint)."""


def _digits(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def _is_ean13(bc):
    d = _digits(bc)
    if len(d) != 13:
        return False
    s = sum(int(d[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
    return (10 - s % 10) % 10 == int(d[12])


def _clean(s, maxlen=40):
    # strip tekens die de commandotalen kunnen breken
    out = str(s or '').replace('"', "'").replace('^', ' ').replace('~', ' ')
    out = out.replace('\r', ' ').replace('\n', ' ')
    return out[:maxlen]


def _price(item, unit):
    if item.get('price') is None:
        return ''
    return 'EUR %s per %s' % (('%.2f' % item['price']).replace('.', ','), _clean(unit, 12))


def build_label(protocol, item, opts, Lw, Lh, dpi=203, copies=1):
    """Geef bytes terug voor één label (met `copies` native kopieën), of None."""
    copies = max(1, int(copies))
    name = _clean(item.get('name'), 32)
    barcode = _digits(item.get('barcode'))
    ean = _is_ean13(barcode)
    price = _price(item, opts.get('price_unit', 'stuk'))
    extras = [_clean(x, 32) for x in (opts.get('extra_line1'), opts.get('extra_line2')) if x]
    if opts.get('show_date') and opts.get('today'):
        extras.insert(0, _clean(opts['today'], 20))

    d = lambda mm: int(round(mm * dpi / 25.4))   # mm -> dots
    W, H = d(Lw), d(Lh)

    if protocol == 'zpl':
        L = ['^XA', '^CI28', '^PW%d' % W, '^LL%d' % H, '^LH0,0']
        y = d(2)
        L.append('^FO%d,%d^A0N,%d,%d^FB%d,1,0,C^FD%s^FS' % (0, y, d(3.5), d(3.5), W, name)); y += d(5)
        if price:
            L.append('^FO%d,%d^A0N,%d,%d^FB%d,1,0,C^FD%s^FS' % (0, y, d(4.5), d(4.5), W, price)); y += d(7)
        bcH = d(11)
        x = d(5)
        if ean:
            L.append('^FO%d,%d^BY2^BEN,%d,Y,N^FD%s^FS' % (x, y, bcH, barcode[:12]))
        else:
            L.append('^FO%d,%d^BY2^BCN,%d,Y,N,N^FD%s^FS' % (x, y, bcH, barcode or name))
        y += bcH + d(4)
        for ex in extras:
            L.append('^FO%d,%d^A0N,%d,%d^FB%d,1,0,C^FD%s^FS' % (0, y, d(2.6), d(2.6), W, ex)); y += d(3)
        L.append('^PQ%d,0,0,N' % copies)
        L.append('^XZ')
        return ('\n'.join(L) + '\n').encode('utf-8', 'replace')

    if protocol == 'epl':
        L = ['N', 'q%d' % W, 'Q%d,24' % H]
        y = d(2)
        L.append('A%d,%d,0,3,1,1,N,"%s"' % (d(2), y, name)); y += d(5)
        if price:
            L.append('A%d,%d,0,4,1,1,N,"%s"' % (d(2), y, price)); y += d(7)
        bcH = d(11)
        if ean:
            L.append('B%d,%d,0,E30,2,4,%d,N,"%s"' % (d(5), y, bcH, barcode[:12]))
        else:
            L.append('B%d,%d,0,1,2,4,%d,N,"%s"' % (d(5), y, bcH, barcode or name))
        y += bcH + d(4)
        for ex in extras:
            L.append('A%d,%d,0,2,1,1,N,"%s"' % (d(2), y, ex)); y += d(3)
        L.append('P%d' % copies)
        return ('\n'.join(L) + '\n').encode('latin-1', 'replace')

    if protocol == 'tspl':
        L = ['SIZE %g mm, %g mm' % (Lw, Lh), 'GAP 3 mm, 0 mm', 'DIRECTION 1', 'CLS']
        y = d(2)
        L.append('TEXT %d,%d,"3",0,1,1,"%s"' % (d(2), y, name)); y += d(5)
        if price:
            L.append('TEXT %d,%d,"4",0,1,1,"%s"' % (d(2), y, price)); y += d(7)
        bcH = d(11)
        btype = 'EAN13' if ean else '128'
        val = barcode[:12] if ean else (barcode or name)
        L.append('BARCODE %d,%d,"%s",%d,1,0,2,4,"%s"' % (d(5), y, btype, bcH, val))
        y += bcH + d(4)
        for ex in extras:
            L.append('TEXT %d,%d,"2",0,1,1,"%s"' % (d(2), y, ex)); y += d(3)
        L.append('PRINT 1,%d' % copies)
        return ('\n'.join(L) + '\n').encode('latin-1', 'replace')

    if protocol == 'text':
        lines = [name]
        if price:
            lines.append(price)
        lines.append(barcode)
        lines += extras
        one = ('\n'.join(lines) + '\n\x0c').encode('utf-8', 'replace')
        return one * copies   # platte tekst kent geen kopie-commando

    return None
