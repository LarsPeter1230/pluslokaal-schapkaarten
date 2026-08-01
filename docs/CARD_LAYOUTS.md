# Card layout specs (exact, extracted from the PLUS reference PDFs)

All values are **fractions of the card's own width (W) / height (H)** unless noted. A "card" is either
a full sheet (A3 single) or one **cell** of the SK Maxi 4-up sheet — the same fractions are reused,
so a layout scales. Sizes shown as `pt` are the reference's point size; the fraction of H next to it
is what the renderer uses (`S(fractionH)`), because Montserrat ≈ Gotham at the same point size.

To re-extract from a reference PDF (in `docs/references/`):
```python
import fitz
pg = fitz.open('docs/references/OLD_A3_liggend_actie.pdf')[0]
W, H = pg.rect.width, pg.rect.height
for b in pg.get_text('dict')['blocks']:
    for l in b.get('lines', []):
        for s in l['spans']:
            x0,y0,x1,y1 = s['bbox']; c = s['color']
            print(f"{x0/W*100:.1f}%,{y0/H*100:.1f}% sz={s['size']:.1f} ({s['size']/H*100:.1f}%H) "
                  f"rgb({(c>>16)&255},{(c>>8)&255},{c&255}) {s['text']!r}")
for p in pg.get_drawings():
    if p.get('fill'):
        r = p['rect']; rc = tuple(round(v*255) for v in p['fill'])
        print(f"RECT {r.x0/W*100:.1f}..{r.x1/W*100:.1f} x {r.y0/H*100:.1f}..{r.y1/H*100:.1f} {rc}")
```

Colors seen in refs: red `#ED1C24 (237,28,36)` / sometimes `#E3000A`; green `#82BB22 (130,187,34)`;
near-black text `#231F20 (35,31,32)` / `#181715 (24,23,21)`; white.

---

## NEW layout — actie (ref: `NEW_A3_liggend_actie.pdf`, 420×297)

Left text column (x≈0.03):
- koptekst `Gotham-Black` 65pt = **0.077H**, first line top y≈0.024, line advance ≈0.079H
- subtekst `Gotham-Book` 47pt = **0.056H**, y≈0.186
- vbtekst (prijsvoorbeeld) `Gotham-Book` 27pt = **0.032H**, y≈0.301
- kaartcode `Gotham-Book` 10pt = 0.012H, top-right x≈0.94 y≈0.022
- disclaimers `GothamNarrow-Book` ~11pt bottom; halveprijs adds the "**Voor 2e halve prijs…" line at y≈0.943, "Maximaal…" at y≈0.965

Right action block `x 0.561…0.970`:
- **two-tone** (halveprijs / korting / gratis / halen-betalen): block **y 0.325…0.904**, RED top ⅔
  (y0.325…0.711), GREEN bottom ⅓ (y0.711…0.904). Big white text in red, white label in green.
  - **euro korting** shows the amount in PLUS-notatie **`5.-`** (hele euro's `N.-`, met centen `N.CC`) — **niet** `€5`.
- **prijs**: block **y 0.422…0.904** (bottom-aligned with two-tone). GREEN label bar (verpakking+inhoud,
  bv. "Per pak" / "2 doosjes") top ≈20% (y0.422…0.520), RED block y0.520…0.904, struck was-price
  (vp1 – vp2) near top, big white price with **superscript cents** (`N.` big, cents ≈0.64×).
- **Portrait (A4/A5/A3-staand)**: action box **x 0.186…0.812**; two-tone **y 0.508…0.951**,
  prijs **y 0.582…0.951** (green header ≈20%). Same internal layout as landscape.

Reference reds vary by format/print-profile: A3-liggend & A4-staand refs use `#E3000A (227,0,10)`,
SK-Maxi ref uses `#ED1C24 (237,28,36)` — the renderer's `RED` is the latter (within the ref range).

`NEW_SK_Maxi_actie.pdf` = A4 landscape, 4 cards at the `_SK_CELLS` positions (same per-card layout).

## NEW layout — tip (designed, no official ref)

`_newtip_*`: green **"TIP" pill** top-left, koptekst 0.077H, subtekst 0.056H, then a green label bar
(verpakking+inhoud) + big **black** price with superscript cents (`_prijs_zwart`). Right block x0.561…0.970.

---

## OLD layout — actie (ref: `OLD_A3_liggend_actie.pdf`, 420×297) — matched 1:1

Product text left (x≈0.03): kop 65pt **0.077H** @y0.024; sub 47pt **0.056H** @y0.105;
"Bijvoorbeeld tekst" 27pt **0.032H** @y0.193; "Maximaal…" @y0.965.

**Red "sticker" price** (this is the distinctive bit — reproduce exactly):
- whole `"N."` `Gotham-Black` **415pt = 0.493H** (renderer uses `S(0.50)`); ink-left ≈ **0.461W**,
  ink-bottom ≈ **0.98–1.02H** (bleeds slightly off the bottom in the ref).
- cents `"99"` **241.9pt = 0.287H** (≈ **0.585 × whole**); ink-left ≈0.618W, ink-top ≈0.425H
  (large **superscript**, upper-right, overlapping the "1").
- Both drawn **red fill + thick WHITE outline** (`_outline_text` / PIL `stroke_width`, ow≈0.055×size)
  **+ a black drop shadow** offset ≈0.05×size down-right. → the cut-out sticker look.
- **White label badge** `x0.50…0.94, y0.337…0.465` (rounded, black drop shadow) with **red** text
  (verpakking+inhoud) `Gotham-Black` 44pt = 0.052H — sits **over the top** of the price.
- **Vanprijs strike**: RED bar `x0.03…0.357, y0.689…0.784` (+black shadow) with **white struck** text
  `Gotham-Black` 53.6pt = 0.064H, left-middle.

`OLD_SK_Maxi_actie.pdf` (297×210, 4 cards): per-card whole `"1"` = 133pt = **0.534 of the cell height**
(so essentially the same relative size as A3); cents 77.5pt; white label badge per card. Cells at
`_SK_CELLS`. **Prices must stay inside each cell — do not let them bleed into the neighbour.**

Implementation: `_old_price(canvas, draw, d, X, Y, S, W, H)` uses **fixed** sizes (`S(0.50)` whole,
`0.585×` cents) at fixed anchor fractions, and scales down only if a multi-digit price would exceed
`0.52W`. `_old_label` draws the white badge. Live preview = `.pvc.oldactie2` in plus.css (matching
cqh sizes + `-webkit-text-stroke` + `text-shadow`).

## OLD layout — tip (ref: `OLD_single_tip_137x88.pdf`) — matched

- GREEN left **panel** `x 0…0.279`, full height; **"TIP"** `Gotham-Bold` white **0.19H** @ (0.038,0.038);
  NIX18 logo + legit text bottom of panel (if alcohol).
- Product right (x≈0.305): kop `GothamNarrow-Black` 22pt (0.088H of the 137×88 card) @y0.024;
  sub `GothamNarrow-Book` 14pt (0.056H) @y0.204.
- **Black VERPAKKING label** (white text) + big **BLACK** price with superscript cents, lower-right
  (`_tip_price`). kilo bottom-right, "Land van herkomst" bottom.

---

## Gotchas when matching a layout 1:1
- The **whole number bleeds off the card bottom** in the old-actie ref — that's intentional; keep the
  price big. But on **SK Maxi it must stay within the cell** (contained), so cap the box.
- Reference "labels" like "Verpakking: Inhoud:" in the sample PDFs are just the placeholder text the
  operator typed; the real field content (verpakking + inhoud, uppercased for tip / as-is for actie)
  goes there.
- New vs old differ mainly in the **price treatment** (modern red/green blocks vs the red outlined
  sticker); product text sizes are the **same** (0.077 / 0.056 / 0.032 H).
