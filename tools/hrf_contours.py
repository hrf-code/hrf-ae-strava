#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_contours.py  —  rysuje warstwice (izohipsy) z pliku terenu (data/hrf_terrain.json) jako czarną mapę z białymi liniami.

Wyniki (w tych samych proporcjach, północ u góry, zakres = cały teren):
  <wyjście>.pdf   wektor do edycji (Illustrator, Inkscape, Affinity): czarne tło + białe ścieżki
  <wyjście>.svg   to samo jako SVG z nazwanymi grupami (warstwice-pomocnicze, warstwice-glowne) – wygodniej w edycji
  <wyjście>.png   raster (skala szarości) używany jako tekstura terenu w Blenderze

Blender nie wczytuje PDF jako obrazu. Przepływ: edytujesz PDF/SVG, eksportujesz PNG o tych samych proporcjach
(ta sama plansza), a w panelu HRF wskazujesz ten PNG jako teksturę terenu.

Użycie (bez internetu, bez dodatkowych bibliotek):
    py tools/hrf_contours.py data/hrf_terrain.json data/hrf_contours
    py tools/hrf_contours.py data/hrf_terrain.json data/hrf_contours --minor 20 --major 100 --png-width 6144

Opcje:
  --minor M        odstęp warstwic pomocniczych w metrach (domyślnie 25)
  --major M        odstęp warstwic głównych, grubszych (domyślnie 100; musi być wielokrotnością --minor)
  --minor-px X     grubość pomocniczych w pikselach PNG o szerokości 4096 (domyślnie 1.1; skaluje się z szerokością)
  --major-px X     grubość głównych (domyślnie 2.6)
  --png-width N    szerokość PNG w pikselach (domyślnie 4096; 0 = bez PNG)
  --page-width PT  szerokość strony PDF/SVG (domyślnie 1000; wysokość wynika z proporcji terenu)
  --simplify C     uproszczenie linii w oczkach siatki (domyślnie 0.3; 0 = bez)
  --no-pdf / --no-svg   pomiń dany plik
  --yes            nie pytaj o zgodę
Przerwanie: Ctrl+C w dowolnej chwili.
"""
import array, base64, json, math, os, struct, sys, time, zlib, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")   # polskie znaki w terminalu Windows
except Exception:
    pass

W = 64
TTY = sys.stdout.isatty()


# ---------------------------------------------------------------- interfejs
def ui_box(lines):
    print("+" + "-" * (W - 2) + "+")
    for l in lines:
        print("| " + l.ljust(W - 4) + " |")
    print("+" + "-" * (W - 2) + "+")


def ui_head(text):
    print("")
    print("== " + text + " " + "=" * max(3, W - 4 - len(text)))


def live(text):
    if TTY:
        sys.stdout.write("\r  " + text.ljust(W + 18)[:W + 18])
        sys.stdout.flush()


def live_clear():
    if TTY:
        sys.stdout.write("\r" + " " * (W + 22) + "\r")
        sys.stdout.flush()


def ui_ok(text):
    live_clear()
    print("  [OK] " + text)


def ui_warn(text):
    live_clear()
    print("  [!]  " + text)


def ui_info(text):
    live_clear()
    print("       " + text)


def bar(frac, width=24):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "[" + "#" * n + "-" * (width - n) + "] %3d%%" % int(round(frac * 100))


def fmt_time(sec):
    sec = int(round(sec))
    if sec < 60:
        return "%d s" % sec
    return "%d min %02d s" % (sec // 60, sec % 60)


def fmt_size(n):
    if n < 1024 * 1024:
        return "%.1f kB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


# ---------------------------------------------------------------- warstwice (marching squares)
# krawędzie komórki (r,c): B = dolna (r,c)-(r,c+1), R = prawa (r,c+1)-(r+1,c+1), T = górna (r+1,c)-(r+1,c+1), Lf = lewa (r,c)-(r+1,c)
# numeracja krawędzi: poziome 2*(r*C+c), pionowe 2*(r*C+c)+1
def trace_contours(g, R, C, levels, progress):
    """Zwraca {poziom: [polilinie]}; polilinia = lista (x, y) w jednostkach siatki (x = kolumna, y = wiersz)."""
    segs = {L: [] for L in levels}
    lo_lvl = levels[0]
    step = levels[1] - levels[0] if len(levels) > 1 else 1.0
    nlev = len(levels)
    for r in range(R - 1):
        row0, row1 = r * C, (r + 1) * C
        for c in range(C - 1):
            v00, v10, v11, v01 = g[row0 + c], g[row0 + c + 1], g[row1 + c + 1], g[row1 + c]
            mn = min(v00, v10, v11, v01)
            mx = max(v00, v10, v11, v01)
            i0 = int(math.ceil((mn - lo_lvl) / step))
            i1 = int(math.floor((mx - lo_lvl) / step))
            if i1 < 0 or i0 >= nlev:
                continue
            if i0 < 0:
                i0 = 0
            if i1 >= nlev:
                i1 = nlev - 1
            eB, eR, eT, eL = 2 * (row0 + c), 2 * (row0 + c + 1) + 1, 2 * (row1 + c), 2 * (row0 + c) + 1
            for li in range(i0, i1 + 1):
                L = levels[li] + 1e-3
                idx = (1 if v00 >= L else 0) | (2 if v10 >= L else 0) | (4 if v11 >= L else 0) | (8 if v01 >= L else 0)
                if idx == 0 or idx == 15:
                    continue
                lst = segs[levels[li]]
                if idx in (1, 14):
                    lst.append((eL, eB))
                elif idx in (2, 13):
                    lst.append((eB, eR))
                elif idx in (3, 12):
                    lst.append((eL, eR))
                elif idx in (4, 11):
                    lst.append((eR, eT))
                elif idx in (6, 9):
                    lst.append((eB, eT))
                elif idx in (7, 8):
                    lst.append((eL, eT))
                else:   # siodło (5 lub 10): rozstrzygamy średnią z rogów
                    hi = (v00 + v10 + v11 + v01) / 4.0 >= L
                    if (idx == 5) == hi:
                        lst.append((eB, eR))
                        lst.append((eT, eL))
                    else:
                        lst.append((eL, eB))
                        lst.append((eR, eT))
        if r % 8 == 0:
            progress(r / float(R - 1))
    out = {}
    for L, sg in segs.items():
        if not sg:
            continue
        out[L] = join_segments(sg, g, R, C, L + 1e-3)
    return out


def join_segments(sg, g, R, C, L):
    adj = {}
    for a, b in sg:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    def pos(e):
        cell, vert = divmod(e, 2)
        r, c = divmod(cell // 1, C)
        if vert == 0:      # krawędź pozioma: (r,c)-(r,c+1)
            v0, v1 = g[r * C + c], g[r * C + c + 1]
            return (c + (L - v0) / (v1 - v0), float(r))
        v0, v1 = g[r * C + c], g[(r + 1) * C + c]          # pionowa: (r,c)-(r+1,c)
        return (float(c), r + (L - v0) / (v1 - v0))

    seen = set()
    lines = []

    def walk(start):
        path = [start]
        seen.add(start)
        cur = start
        while True:
            nxt = None
            for n in adj[cur]:
                if n not in seen:
                    nxt = n
                    break
            if nxt is None:
                break
            path.append(nxt)
            seen.add(nxt)
            cur = nxt
        return path

    for e in adj:                               # najpierw linie otwarte (końce o stopniu 1)
        if e not in seen and len(adj[e]) == 1:
            lines.append([pos(x) for x in walk(e)])
    for e in adj:                               # potem zamknięte pętle
        if e not in seen:
            p = [pos(x) for x in walk(e)]
            p.append(p[0])
            lines.append(p)
    return lines


def rdp(pts, eps):
    if len(pts) < 3 or eps <= 0:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        dm, im = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / L
            if d > dm:
                dm, im = d, i
        if dm > eps:
            keep[im] = True
            stack.append((a, im))
            stack.append((im, b))
    return [p for p, k in zip(pts, keep) if k]


def simplify_line(ln, eps):
    """Uproszczenie linii. Zamknięte pętle (pierwszy punkt = ostatni, np. warstwice wokół szczytów) zwinęłyby się przy
    zwykłym Douglasie–Peuckerze do jednego punktu, więc pętlę rozcinamy w punkcie najdalszym od pierwszego
    i upraszczamy obie połowy osobno."""
    if eps <= 0 or len(ln) < 4:
        return ln
    if ln[0] == ln[-1]:
        ring = ln[:-1]
        if len(ring) < 3:
            return ln
        far = max(range(1, len(ring)), key=lambda i: (ring[i][0] - ring[0][0]) ** 2 + (ring[i][1] - ring[0][1]) ** 2)
        a = rdp(ring[:far + 1], eps)
        b = rdp(ring[far:] + [ring[0]], eps)
        return a[:-1] + b
    return rdp(ln, eps)


# ---------------------------------------------------------------- zapis: PDF, SVG, PNG
def write_pdf(path, pw, ph, minor_lines, major_lines, wminor, wmajor):
    def paths(lines):
        out = []
        for ln in lines:
            out.append("%.2f %.2f m" % ln[0])
            out.extend("%.2f %.2f l" % p for p in ln[1:])
            out.append("S")
        return "\n".join(out)
    content = ("0 0 0 rg\n0 0 %.2f %.2f re f\n1 1 1 RG\n1 J 1 j\n%.3f w\n%s\n%.3f w\n%s\n"
               % (pw, ph, wminor, paths(minor_lines), wmajor, paths(major_lines))).encode("ascii")
    comp = zlib.compress(content, 6)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R /Resources << >> >>" % (pw, ph)).encode("ascii"),
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream"]
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, o in enumerate(objs):
        offs.append(len(buf))
        buf += b"%d 0 obj\n" % (i + 1) + o + b"\nendobj\n"
    xref = len(buf)
    buf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        buf += b"%010d 00000 n \n" % o
    buf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as f:
        f.write(buf)


def write_svg(path, pw, ph, minor_lines, major_lines, wminor, wmajor):
    def d_attr(lines):
        parts = []
        for ln in lines:
            parts.append("M%.2f %.2f" % (ln[0][0], ph - ln[0][1]) + "".join("L%.2f %.2f" % (x, ph - y) for x, y in ln[1:]))
        return "".join(parts)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="%.2f" height="%.2f" viewBox="0 0 %.2f %.2f">\n' % (pw, ph, pw, ph))
        f.write('<rect id="tlo" width="%.2f" height="%.2f" fill="#000000"/>\n' % (pw, ph))
        f.write('<g id="warstwice-pomocnicze" fill="none" stroke="#ffffff" stroke-width="%.3f" stroke-linecap="round" stroke-linejoin="round">\n' % wminor)
        f.write('<path d="%s"/>\n</g>\n' % d_attr(minor_lines))
        f.write('<g id="warstwice-glowne" fill="none" stroke="#ffffff" stroke-width="%.3f" stroke-linecap="round" stroke-linejoin="round">\n' % wmajor)
        f.write('<path d="%s"/>\n</g>\n</svg>\n' % d_attr(major_lines))


def write_png_gray(path, w, h, pix):
    raw = bytearray()
    for y in range(h):
        raw += b"\x00"
        raw += pix[y * w:(y + 1) * w]
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def raster_lines(pix, w, h, lines, half_w, progress=None):
    """Antyaliasowane grube odcinki do bufora 8-bit (biały na czarnym); lines w pikselach, y od góry."""
    n = len(lines)
    for li, ln in enumerate(lines):
        for k in range(len(ln) - 1):
            x0, y0 = ln[k]
            x1, y1 = ln[k + 1]
            dx, dy = x1 - x0, y1 - y0
            ll = dx * dx + dy * dy
            xa = max(0, int(min(x0, x1) - half_w - 1))
            xb = min(w - 1, int(max(x0, x1) + half_w + 1))
            ya = max(0, int(min(y0, y1) - half_w - 1))
            yb = min(h - 1, int(max(y0, y1) + half_w + 1))
            for yy in range(ya, yb + 1):
                base = yy * w
                py = yy + 0.5
                for xx in range(xa, xb + 1):
                    px = xx + 0.5
                    if ll > 0:
                        t = ((px - x0) * dx + (py - y0) * dy) / ll
                        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                    else:
                        t = 0.0
                    ex, ey = px - (x0 + t * dx), py - (y0 + t * dy)
                    d = math.sqrt(ex * ex + ey * ey)
                    cov = half_w + 0.5 - d
                    if cov > 0:
                        v = 255 if cov >= 1 else int(cov * 255)
                        if v > pix[base + xx]:
                            pix[base + xx] = v
        if progress and li % 200 == 0:
            progress(li / float(n))


# ---------------------------------------------------------------- program
def main():
    ap = argparse.ArgumentParser(description="Warstwice z pliku terenu: PDF, SVG i PNG (czarne tło, białe linie).")
    ap.add_argument("terrain", help="plik terenu data/hrf_terrain.json")
    ap.add_argument("output", help="ścieżka wyjściowa bez rozszerzenia, np. data/hrf_contours")
    ap.add_argument("--minor", type=float, default=25.0)
    ap.add_argument("--major", type=float, default=100.0)
    ap.add_argument("--minor-px", type=float, default=1.1)
    ap.add_argument("--major-px", type=float, default=2.6)
    ap.add_argument("--png-width", type=int, default=4096)
    ap.add_argument("--page-width", type=float, default=1000.0)
    ap.add_argument("--simplify", type=float, default=0.3)
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    t_all = time.time()

    ui_box(["HRF  -  warstwice z terenu: PDF, SVG i PNG",
            "Wejście: " + os.path.basename(a.terrain),
            "Wyjście: " + a.output + ".pdf / .svg / .png",
            "Ctrl+C = anuluj w dowolnej chwili."])

    ui_head("1. Teren")
    if not os.path.isfile(a.terrain):
        ui_warn("Nie ma pliku: " + a.terrain)
        ui_info("Folder, w którym jesteś: " + os.getcwd())
        return 1
    if a.major % a.minor > 1e-6:
        ui_warn("--major (%g) powinno być wielokrotnością --minor (%g)." % (a.major, a.minor))
        return 1
    with open(a.terrain, encoding="utf-8") as f:
        d = json.load(f)
    g = array.array("f")
    g.frombytes(zlib.decompress(base64.b64decode(d["z_b64"])))
    R, C = d["rows"], d["cols"]
    zmin, zmax = min(g), max(g)
    earth = 6371008.8
    latc = (d["lat_s"] + d["lat_n"]) / 2.0
    wm = math.radians(d["lng_e"] - d["lng_w"]) * earth * math.cos(math.radians(latc))
    hm = math.radians(d["lat_n"] - d["lat_s"]) * earth
    ui_ok("Wczytano siatkę %d x %d (oczko %.0f m), wysokości %.0f .. %.0f m n.p.m." % (C, R, d["step_m"], zmin, zmax))
    ui_info("Obszar: %.1f x %.1f km" % (wm / 1000.0, hm / 1000.0))

    lo = math.ceil(zmin / a.minor) * a.minor
    levels = []
    v = lo
    while v <= zmax:
        levels.append(v)
        v += a.minor
    n_major = sum(1 for L in levels if abs(L / a.major - round(L / a.major)) < 1e-6)
    ui_head("2. Plan")
    ui_info("Warstwice co %g m (głównych co %g m): %d poziomów, w tym %d głównych" % (a.minor, a.major, len(levels), n_major))
    pw = a.page_width
    ph = pw * hm / wm
    pxw = a.png_width
    pxh = int(round(pxw * hm / wm)) if pxw else 0
    ui_info("Strona PDF/SVG: %.0f x %.0f pt;  PNG: %s" % (pw, ph, ("%d x %d px" % (pxw, pxh)) if pxw else "pominięty"))
    if not a.yes and sys.stdin.isatty():
        try:
            ans = input("  ?    Zacząć? [T/n] ").strip().lower()
        except EOFError:
            ans = ""
        if ans and ans[0] not in ("t", "y"):
            ui_warn("Anulowano.")
            return 130

    ui_head("3. Liczenie warstwic")
    t0 = time.time()
    contours = trace_contours(g, R, C, levels, lambda f: live("liczenie %s" % bar(f)))
    live_clear()
    # jednostki siatki -> strona (y w górę, północ u góry) i piksele PNG (y w dół)
    sx, sy = pw / (C - 1.0), ph / (R - 1.0)
    minor_lines, major_lines = [], []
    npts = 0
    for L, lines in contours.items():
        is_major = abs(L / a.major - round(L / a.major)) < 1e-6
        for ln in lines:
            ln = simplify_line(ln, a.simplify)
            if len(ln) < 2:
                continue
            page = [(x * sx, y * sy) for x, y in ln]
            npts += len(page)
            (major_lines if is_major else minor_lines).append(page)
    ui_ok("Warstwice policzone w %s: %d linii pomocniczych, %d głównych, %d punktów" % (fmt_time(time.time() - t0), len(minor_lines), len(major_lines), npts))

    ui_head("4. Zapis")
    wmin = a.minor_px / 4096.0 * pw
    wmaj = a.major_px / 4096.0 * pw
    out_dir = os.path.dirname(os.path.abspath(a.output))
    os.makedirs(out_dir, exist_ok=True)
    if not a.no_pdf:
        write_pdf(a.output + ".pdf", pw, ph, minor_lines, major_lines, wmin, wmaj)
        ui_ok("PDF: %s.pdf  (%s)" % (a.output, fmt_size(os.path.getsize(a.output + ".pdf"))))
    if not a.no_svg:
        write_svg(a.output + ".svg", pw, ph, minor_lines, major_lines, wmin, wmaj)
        ui_ok("SVG: %s.svg  (%s)" % (a.output, fmt_size(os.path.getsize(a.output + ".svg"))))
    if pxw:
        t1 = time.time()
        k = pxw / pw
        pix = bytearray(pxw * pxh)
        for lines, wpx, label in ((minor_lines, a.minor_px * pxw / 4096.0, "pomocnicze"), (major_lines, a.major_px * pxw / 4096.0, "główne")):
            px_lines = [[(x * k, (ph - y) * k) for x, y in ln] for ln in lines]
            raster_lines(pix, pxw, pxh, px_lines, wpx / 2.0, lambda f, label=label: live("PNG, %s %s" % (label, bar(f))))
            live_clear()
        write_png_gray(a.output + ".png", pxw, pxh, pix)
        ui_ok("PNG: %s.png  (%d x %d, %s) w %s" % (a.output, pxw, pxh, fmt_size(os.path.getsize(a.output + ".png")), fmt_time(time.time() - t1)))
    print("")
    ui_box(["Gotowe w %s." % fmt_time(time.time() - t_all),
            "Edytuj PDF/SVG (np. w Illustratorze), potem wyeksportuj PNG",
            "o tych samych proporcjach i wskaż go w panelu HRF (Teren > Styl)."])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        live_clear()
        print("")
        print("  [!]  PRZERWANO (Ctrl+C). Pliki wynikowe mogą być niekompletne – uruchom ponownie.")
        sys.exit(130)
