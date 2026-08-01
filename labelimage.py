"""Rendert een label als monochrome afbeelding (zoals het ontwerp in de app) en
zet dat om naar een TSPL BITMAP-opdracht, zodat de winkelprinter het échte
ontwerp print: logo, streep, naam, prijsband en barcode."""
from io import BytesIO

_FONT_REG = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
_FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def _font(bold, size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REG, size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def _centered(draw, cx, y, text, font, fill=0):
    w, h = _text_w(draw, text, font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)
    return h


def _wrap(draw, text, font, maxw):
    """Breek tekst af op woorden zodat elke regel binnen maxw past."""
    words = str(text or '').split()
    if not words:
        return ['']
    lines, cur = [], words[0]
    for w in words[1:]:
        if _text_w(draw, cur + ' ' + w, font)[0] <= maxw:
            cur += ' ' + w
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def render_label(item, opts, Lw, Lh, dpi=300, logo_path=None, show_logo=False):
    """Rendert het labelontwerp adaptief: tekst breekt af, alles schaalt zodat
    het altijd binnen het label past — met of zonder prijs."""
    from PIL import Image, ImageDraw

    px = lambda mm: max(1, int(round(mm * dpi / 25.4)))
    W, H = px(Lw), px(Lh)
    pad = px(2)
    avail_w = W - 2 * pad
    cx = W / 2
    line_w = max(1, px(0.3))
    gap = px(1.2)

    measure = ImageDraw.Draw(Image.new('1', (1, 1)))

    # Logo voorbereiden (op basisgrootte)
    logo_im = None
    if show_logo and logo_path:
        try:
            lg = Image.open(logo_path).convert('L')
            logo_im = lg
        except Exception:
            logo_im = None

    name = str(item.get('name') or '')
    has_price = item.get('price') is not None
    price = ('€ ' + ('%.2f' % item['price']).replace('.', ',')) if has_price else ''
    unit = ('per ' + str(opts.get('price_unit') or 'stuk')) if has_price else ''
    old_price = item.get('old_price')
    old_txt = ('€ ' + ('%.2f' % old_price).replace('.', ',')) if (has_price and old_price) else ''
    digits = ''.join(ch for ch in str(item.get('barcode') or '') if ch.isdigit()) \
             or str(item.get('barcode') or '')
    extras = []
    if opts.get('show_date') and opts.get('today'):
        extras.append(str(opts['today']))
    if opts.get('extra_line1'):
        extras.append(str(opts['extra_line1']))
    if opts.get('extra_line2'):
        extras.append(str(opts['extra_line2']))

    def build(scale):
        """Bereken alle elementen + totale hoogte voor een gegeven schaal."""
        s = scale
        f_name = _font(True, max(8, int(px(Lh * 0.10) * s)))
        f_price = _font(True, max(9, int(px(Lh * 0.17) * s)))
        f_unit = _font(True, max(6, int(px(Lh * 0.05) * s)))
        f_dig = _font(False, max(6, int(px(Lh * 0.055) * s)))
        f_ex = _font(False, max(6, int(px(Lh * 0.045) * s)))
        els = []          # (kind, payload, height)
        total = pad

        if logo_im is not None:
            maxh = int(px(Lh * 0.15) * s)
            ratio = min(avail_w / logo_im.width, maxh / logo_im.height)
            lw, lh = max(1, int(logo_im.width * ratio)), max(1, int(logo_im.height * ratio))
            els.append(('logo', (lw, lh), lh)); total += lh + gap
            els.append(('line', None, line_w)); total += line_w + gap

        name_lines = _wrap(measure, name, f_name, avail_w)[:3]
        nh = _text_w(measure, 'Hg', f_name)[1]
        for ln in name_lines:
            els.append(('text', (ln, f_name), nh)); total += nh + px(0.6)

        if has_price:
            if old_txt:
                f_old = _font(True, max(7, int(px(Lh * 0.075) * s)))
                oh = _text_w(measure, old_txt, f_old)[1]
                els.append(('strike', (old_txt, f_old), oh)); total += oh + px(0.5)
            ph = _text_w(measure, price, f_price)[1]
            els.append(('text', (price, f_price), ph)); total += ph + px(0.4)
            uh = _text_w(measure, unit, f_unit)[1]
            els.append(('text', (unit, f_unit), uh)); total += uh + gap

        els.append(('line', None, line_w)); total += line_w + px(1.2)

        bc = _barcode_image(item.get('barcode'), target_w=avail_w, dpi=dpi,
                            module_height=max(5.0, Lh * 0.22 * s))
        if bc is not None:
            if bc.width > avail_w:
                r = avail_w / bc.width
                bc = bc.resize((int(bc.width * r), int(bc.height * r)))
            els.append(('img', bc, bc.height)); total += bc.height + px(0.8)
        if digits:
            dh = _text_w(measure, digits, f_dig)[1]
            els.append(('text', (digits, f_dig), dh)); total += dh + px(0.8)

        for ex in extras:
            eh = _text_w(measure, ex, f_ex)[1]
            els.append(('text', (ex, f_ex), eh)); total += eh + px(0.4)

        total += pad
        return els, total

    els, total = build(1.0)
    if total > H:                       # schaal terug tot het past
        s = max(0.45, (H - 2 * pad) / float(total - 2 * pad))
        els, total = build(s)

    # Tekenen
    img = Image.new('1', (W, H), 1)
    draw = ImageDraw.Draw(img)
    y = pad
    for kind, payload, h in els:
        if kind == 'logo':
            lw, lh = payload
            lg = logo_im.resize((lw, lh)).point(lambda p: 0 if p < 128 else 1, mode='1')
            img.paste(lg, (int(cx - lw / 2), int(y)))
            y += lh + gap
        elif kind == 'line':
            draw.line([(pad, y), (W - pad, y)], fill=0, width=line_w)
            y += line_w + gap
        elif kind == 'img':
            img.paste(payload, (int(cx - payload.width / 2), int(y)))
            y += payload.height + px(0.8)
        elif kind == 'text':
            text, font = payload
            w, th = _text_w(draw, text, font)
            draw.text((cx - w / 2, y), text, font=font, fill=0)
            y += h + px(0.5)
        elif kind == 'strike':
            text, font = payload
            w, th = _text_w(draw, text, font)
            draw.text((cx - w / 2, y), text, font=font, fill=0)
            ly = int(y + h * 0.55)
            draw.line([(cx - w / 2 - px(0.5), ly), (cx + w / 2 + px(0.5), ly)],
                      fill=0, width=max(1, px(0.35)))
            y += h + px(0.5)
    return img


def _barcode_image(code, target_w, dpi=300, module_height=9.0):
    """Genereer een scanbare EAN13/Code128-afbeelding (PIL '1')."""
    digits = ''.join(ch for ch in str(code or '') if ch.isdigit())
    try:
        import barcode
        from barcode.writer import ImageWriter
        # Geen ingebouwde tekst: cijfers worden apart onder de bars getekend.
        opts = {'module_height': float(module_height), 'quiet_zone': 2.0, 'dpi': dpi, 'write_text': False}
        if len(digits) in (12, 13):
            obj = barcode.get('ean13', digits[:12], writer=ImageWriter())
        else:
            obj = barcode.get('code128', str(code or ''), writer=ImageWriter())
        bio = BytesIO()
        obj.write(bio, options=opts)
        bio.seek(0)
        from PIL import Image
        im = Image.open(bio).convert('L')
        if im.width > target_w:
            r = target_w / im.width
            im = im.resize((target_w, int(im.height * r)))
        return im.point(lambda p: 0 if p < 128 else 1, mode='1')
    except Exception:
        return None


def image_to_tspl(img, Lw, Lh, dpi=300, density=10, copies=1, gap=3.0):
    """Bouw een volledige TSPL-opdracht met het label als BITMAP.
    `copies` = aantal kopieën dat de printer zelf afdrukt (betrouwbaarder dan herhalen)."""
    W, H = img.size
    width_bytes = (W + 7) // 8
    px = img.load()
    data = bytearray()
    for row in range(H):
        for byte_i in range(width_bytes):
            b = 0
            for bit in range(8):
                x = byte_i * 8 + bit
                # TSPL BITMAP: bit 1 = wit (niet printen), 0 = zwart (printen)
                white = 1
                if x < W:
                    white = 1 if px[x, row] else 0  # '1'-mode: 0=zwart,255->1=wit
                if white:
                    b |= (0x80 >> bit)
            data.append(b)

    head = 'SIZE %g mm, %g mm\r\n' % (Lw, Lh)
    head += 'GAP %g mm, 0 mm\r\n' % gap
    head += 'DIRECTION 1\r\nREFERENCE 0,0\r\nDENSITY %d\r\nCLS\r\n' % density
    cmd = bytearray(head.encode('ascii', 'replace'))
    cmd += ('BITMAP 0,0,%d,%d,0,' % (width_bytes, H)).encode('ascii')
    cmd += data
    cmd += ('\r\nPRINT 1,%d\r\n' % max(1, int(copies))).encode('ascii')
    return bytes(cmd)


def build_tspl_graphic(item, opts, Lw, Lh, dpi=300, logo_path=None, show_logo=False,
                       density=10, copies=1, gap=3.0):
    """Volledig pad: render ontwerp -> TSPL BITMAP. None bij fout (val terug op tekst)."""
    try:
        img = render_label(item, opts, Lw, Lh, dpi=dpi, logo_path=logo_path, show_logo=show_logo)
        return image_to_tspl(img, Lw, Lh, dpi=dpi, density=density, copies=copies, gap=gap)
    except Exception:
        return None
