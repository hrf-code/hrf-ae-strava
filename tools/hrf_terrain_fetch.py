#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_terrain_fetch.py  —  pobiera wysokości terenu wokół trasy (otwarte kafelki wysokości "Terrarium" na AWS)
i zapisuje regularną siatkę wysokości do pliku dla skryptu Blendera (Blender/hrf_blender_route.py).

Użycie (potrzebny Python 3 i internet, bez dodatkowych bibliotek):
    py tools/hrf_terrain_fetch.py data/aktywnosc_hrf.json data/hrf_terrain.json
    py tools/hrf_terrain_fetch.py data/aktywnosc_hrf.json data/hrf_terrain.json --zoom 13 --step 25 --margin 1500
    py tools/hrf_terrain_fetch.py - data/hrf_local_terrain.json --bbox 49.55,49.58,20.60,20.65 --zoom 14 --step 10
      (sam wycinek: --bbox LAT_POŁUDNIE,LAT_PÓŁNOC,LNG_ZACHÓD,LNG_WSCHÓD, zamiast pliku trasy podaj "-")

Co widać w trakcie: plan (obszar, liczba kafelków, rozmiar siatki), pytanie o zgodę, pasek postępu pobierania
z szacowanym czasem, błędy i ponowienia, postęp próbkowania siatki, podsumowanie.
Przerwanie: Ctrl+C w dowolnej chwili – pobrane kafelki zostają w pamięci podręcznej (<plik wyjściowy>.cache),
ponowne uruchomienie dociąga tylko brakujące.

Do serwera trafiają numery kafelków (z/x/y) pokrywających obszar trasy, bez samej trasy.

Opcje:
  --zoom N       poziom kafelków (domyślnie 13: ok. 12 m/piksel na szerokości 50°; 12 = 25 m, 14 = 6 m)
  --step M       oczko siatki wyjściowej w metrach (domyślnie 25)
  --margin M     margines terenu wokół trasy w metrach (domyślnie 1500)
  --bbox A,B,C,D wycinek zamiast obszaru trasy: lat_południe,lat_północ,lng_zachód,lng_wschód (plik trasy: "-")
  --tries N      liczba prób na kafelek (domyślnie 5)
  --timeout S    limit czasu jednego kafelka w sekundach (domyślnie 30)
  --url SZABLON  własny szablon adresu kafelków, np. https://host/{z}/{x}/{y}.png (format Terrarium)
  --force        ignoruj pamięć podręczną
  --yes          nie pytaj o zgodę

Dane wysokości: Mapzen/AWS Terrain Tiles (SRTM, NED, GMTED i inne) – przy publikacji podaj źródło:
https://github.com/tilezen/joerd/blob/master/docs/attribution.md
Format wyniku (JSON): lat_s, lat_n, lng_w, lng_e, rows, cols, step_m, source, z_b64
(z_b64 = float32 w metrach n.p.m., zlib + base64, wiersze od południa do północy, kolumny od zachodu).
"""
import array, base64, json, math, os, struct, sys, threading, time, zlib, argparse
import urllib.request, urllib.parse, urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")   # polskie znaki w terminalu Windows
except Exception:
    pass

URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
SOURCE = "Mapzen/AWS Terrain Tiles (SRTM, NED, GMTED i inne), https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
EARTH_R = 6371008.8
W = 64
TTY = sys.stdout.isatty()
SPIN = "|/-\\"
STATE = {"total": 0, "cached": 0}


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


def ask_yes(question, default=True):
    suffix = " [T/n] " if default else " [t/N] "
    try:
        ans = input("  ?    " + question + suffix).strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans[0] in ("t", "y")


def bar(frac, width=24):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "[" + "#" * n + "-" * (width - n) + "] %3d%%" % int(round(frac * 100))


def fmt_time(sec):
    sec = int(round(sec))
    if sec < 60:
        return "%d s" % sec
    if sec < 3600:
        return "%d min %02d s" % (sec // 60, sec % 60)
    return "%d h %02d min" % (sec // 3600, (sec % 3600) // 60)


def fmt_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f kB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def rerun_command():
    parts = ["py"] + sys.argv
    return " ".join('"%s"' % x if (" " in x) else x for x in parts)


def print_resume_hint(after_fix=False):
    ui_info("Kafelki w pamięci podręcznej: %d z %d (nie trzeba ich pobierać ponownie)." % (STATE["cached"], STATE["total"]))
    if after_fix:
        ui_info("Pobrane dane są bezpieczne. Gdy błąd zostanie naprawiony, uruchom TĘ SAMĄ komendę (bez --force):")
    else:
        ui_info("Aby dokończyć, uruchom TĘ SAMĄ komendę jeszcze raz (bez --force):")
    print("")
    print("       " + rerun_command())
    print("")


# ---------------------------------------------------------------- PNG (bez bibliotek)
def decode_png(data):
    """Dekoder PNG 8-bit RGB/RGBA bez przeplotu (taki jest format kafelków). Zwraca (w, h, bpp, bajty)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("to nie jest PNG")
    pos, idat, hdr = 8, [], None
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat.append(body)
        elif typ == b"IEND":
            break
        pos += 12 + ln
    w, h, bd, ct, _, _, interlace = hdr
    if bd != 8 or ct not in (2, 6) or interlace != 0:
        raise ValueError("nieobsługiwany PNG (bd=%d ct=%d interlace=%d)" % (bd, ct, interlace))
    bpp = 3 if ct == 2 else 4
    stride = w * bpp
    raw = zlib.decompress(b"".join(idat))
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif ft != 0:
            raise ValueError("nieznany filtr PNG %d" % ft)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, bpp, out


def tile_elevation(png):
    """Kafelek Terrarium -> array('f') 256*256 z wysokościami w metrach."""
    w, h, bpp, px = decode_png(png)
    el = array.array("f", [0.0]) * (w * h)
    for i in range(w * h):
        j = i * bpp
        el[i] = px[j] * 256.0 + px[j + 1] + px[j + 2] / 256.0 - 32768.0
    return w, el


# ---------------------------------------------------------------- pobieranie
def _get(url, timeout, box):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hrf-ae-terrain/1.0 (osobiste grafiki ze Stravy)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            box["data"] = r.read()
    except urllib.error.HTTPError as e:
        box["error"] = "HTTP %d" % e.code
        box["code"] = e.code
    except Exception as e:
        box["error"] = (type(e).__name__ + ": " + str(e))[:140]


def fetch_tile(url, label, tries, timeout):
    """Zwraca bajty PNG albo None. 404 = brak kafelka (poza zasięgiem danych) -> b''."""
    for attempt in range(tries):
        box = {}
        t = threading.Thread(target=_get, args=(url, timeout, box), daemon=True)
        t0 = time.time()
        t.start()
        spin = 0
        while t.is_alive():
            time.sleep(0.1)
            spin += 1
            live("%s  próba %d/%d  %s %s" % (label, attempt + 1, tries, fmt_time(time.time() - t0), SPIN[spin % 4]))
        if "data" in box:
            return box["data"]
        if box.get("code") == 404:
            return b""
        ui_warn("%s  próba %d/%d nieudana (%s)" % (label, attempt + 1, tries, box.get("error", "?")))
        if attempt < tries - 1:
            end = time.time() + 2 + 2 * attempt
            while time.time() < end:
                live("%s  ponawiam za %d s (Ctrl+C = anuluj)" % (label, int(math.ceil(end - time.time()))))
                time.sleep(0.2)
    return None


def tile_frac(lat, lng, z):
    n = 2.0 ** z
    x = (lng + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


# ---------------------------------------------------------------- program
def main():
    ap = argparse.ArgumentParser(description="Pobiera wysokości terenu wokół trasy (kafelki Terrarium) dla Blendera.")
    ap.add_argument("data", help="plik *_hrf.json z konwertera (przy --bbox wpisz -)")
    ap.add_argument("output", help="plik wyjściowy, np. data/hrf_terrain.json")
    ap.add_argument("--zoom", type=int, default=13)
    ap.add_argument("--step", type=float, default=25.0)
    ap.add_argument("--margin", type=float, default=1500.0)
    ap.add_argument("--bbox", help="wycinek: lat_s,lat_n,lng_w,lng_e (zamiast obszaru wokół trasy)")
    ap.add_argument("--tries", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--url", default=URL)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    t_all = time.time()

    ui_box(["HRF  -  wysokości terenu wokół trasy (dla Blendera)",
            "Plik wejściowy: " + os.path.basename(a.data),
            "Wynik:          " + a.output,
            "Ctrl+C = anuluj w dowolnej chwili (pobrane kafelki zostają)."])

    # ----- 1. obszar
    ui_head("1. Obszar")
    dlat_m = math.radians(1.0) * EARTH_R
    if a.bbox:
        try:
            lat_s, lat_n, lng_w, lng_e = [float(v) for v in a.bbox.split(",")]
            assert lat_n > lat_s and lng_e > lng_w
        except Exception:
            ui_warn("--bbox podaj jako lat_południe,lat_północ,lng_zachód,lng_wschód (cztery liczby, północ > południe).")
            return 1
        latc = (lat_s + lat_n) / 2.0
        dlng_m = dlat_m * math.cos(math.radians(latc))
        ui_ok("Wycinek z --bbox (bez pliku trasy)")
        w_km, h_km = (lng_e - lng_w) * dlng_m / 1000.0, (lat_n - lat_s) * dlat_m / 1000.0
    else:
        if not os.path.isfile(a.data):
            ui_warn("Nie ma pliku: " + a.data)
            ui_info("Folder, w którym jesteś: " + os.getcwd())
            return 1
        with open(a.data, encoding="utf-8-sig") as f:
            D = json.load(f)
        S = D.get("samples") or D
        lat, lng = S["lat"], S["lng"]
        ui_ok("Wczytano %d punktów trasy" % len(lat))
        latc = (min(lat) + max(lat)) / 2.0
        dlng_m = dlat_m * math.cos(math.radians(latc))
        lat_s, lat_n = min(lat) - a.margin / dlat_m, max(lat) + a.margin / dlat_m
        lng_w, lng_e = min(lng) - a.margin / dlng_m, max(lng) + a.margin / dlng_m
        w_km, h_km = (lng_e - lng_w) * dlng_m / 1000.0, (lat_n - lat_s) * dlat_m / 1000.0
    rows = int((lat_n - lat_s) * dlat_m / a.step) + 1
    cols = int((lng_e - lng_w) * dlng_m / a.step) + 1
    ui_info("Obszar%s: %.1f x %.1f km" % ("" if a.bbox else " z marginesem %.0f m" % a.margin, w_km, h_km))
    ui_info("Siatka wyjściowa: %d x %d (oczko %.0f m) = %d wierzchołków" % (cols, rows, a.step, rows * cols))
    if rows * cols > 1500000:
        ui_warn("To dużo wierzchołków dla Blendera. Rozważ większe --step (np. %d)." % int(a.step * 1.5))

    # ----- 2. plan
    ui_head("2. Plan pobierania")
    z = a.zoom
    x0, y1 = tile_frac(lat_s, lng_w, z)
    x1, y0 = tile_frac(lat_n, lng_e, z)
    tx0, tx1, ty0, ty1 = int(x0), int(x1), int(y0), int(y1)
    tiles = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    cache_dir = a.output + ".cache"
    os.makedirs(cache_dir, exist_ok=True)
    files = {t: os.path.join(cache_dir, "%d_%d_%d.png" % (z, t[0], t[1])) for t in tiles}
    cached = {t: (os.path.exists(files[t]) and not a.force) for t in tiles}
    todo = [t for t in tiles if not cached[t]]
    m_px = 156543.03 * math.cos(math.radians(latc)) / (2 ** z)
    STATE["total"], STATE["cached"] = len(tiles), len(tiles) - len(todo)
    ui_info("Poziom %d: kafelki %dx%d = %d (ok. %.1f m na piksel)" % (z, tx1 - tx0 + 1, ty1 - ty0 + 1, len(tiles), m_px))
    ui_info("Z pamięci podręcznej: %d,  do pobrania: %d" % (len(tiles) - len(todo), len(todo)))
    if todo:
        ui_info("Szacunkowo: %s" % fmt_time(len(todo) * 1.5))
        ui_info("Do serwera (%s) trafią tylko numery kafelków obejmujących obszar trasy." % urllib.parse.urlparse(a.url).netloc)
        if STATE["cached"]:
            ui_info("To jest WZNOWIENIE: kafelki z pamięci podręcznej nie będą pobierane ponownie.")
        if not a.yes and sys.stdin.isatty() and not ask_yes("Zacząć pobieranie?"):
            ui_warn("Anulowano przed pobieraniem. Nic nie zmieniono.")
            return 130

    # ----- 3. pobieranie
    missing = []
    times = []
    nbytes = 0
    if todo:
        ui_head("3. Pobieranie kafelków")
    for k, t in enumerate(todo):
        eta = (sum(times) / len(times)) * (len(todo) - k) if times else None
        live("%s  kafelek %d/%d%s" % (bar(k / float(len(todo))), k + 1, len(todo), ("  pozostało ok. " + fmt_time(eta)) if eta else ""))
        t0 = time.time()
        png = fetch_tile(a.url.format(z=z, x=t[0], y=t[1]), "kafelek %d/%d" % (k + 1, len(todo)), a.tries, a.timeout)
        if png is None:
            ui_warn("kafelek %d/%d (%d/%d) NIE POBRANY po %d próbach" % (k + 1, len(todo), t[0], t[1], a.tries))
            missing.append(t)
            continue
        with open(files[t], "wb") as f:
            f.write(png)
        nbytes += len(png)
        STATE["cached"] += 1
        times.append(time.time() - t0)
    live_clear()
    if missing:
        ui_head("Pobieranie niekompletne")
        ui_warn("Brakuje %d z %d kafelków." % (len(missing), len(tiles)))
        ui_info("Plik wynikowy NIE został zapisany (byłby z dziurami).")
        print_resume_hint()
        return 2
    if todo:
        ui_ok("Pobrano %d kafelków, %s, w %s" % (len(todo), fmt_size(nbytes), fmt_time(sum(times))))

    # ----- 4. dekodowanie
    ui_head("4. Dekodowanie kafelków")
    elev = {}
    t_dec = time.time()
    for k, t in enumerate(tiles):
        live("dekodowanie %s  %d/%d" % (bar(k / float(len(tiles))), k + 1, len(tiles)))
        with open(files[t], "rb") as f:
            png = f.read()
        if not png:
            elev[t] = None
            continue
        try:
            elev[t] = tile_elevation(png)[1]
        except Exception as e:
            raise RuntimeError("kafelek %d/%d jest uszkodzony (%s) – usuń go z %s i uruchom ponownie" % (t[0], t[1], e, cache_dir))
    live_clear()
    ui_ok("Zdekodowano %d kafelków w %s" % (len(tiles), fmt_time(time.time() - t_dec)))

    gx0, gx1 = tx0 * 256, (tx1 + 1) * 256 - 1
    gy0, gy1 = ty0 * 256, (ty1 + 1) * 256 - 1

    def px(ix, iy):
        ix = gx0 if ix < gx0 else (gx1 if ix > gx1 else ix)
        iy = gy0 if iy < gy0 else (gy1 if iy > gy1 else iy)
        tl = elev.get((ix >> 8, iy >> 8))
        if tl is None:
            return 0.0
        return tl[(iy & 255) * 256 + (ix & 255)]

    # ----- 5. siatka wyjściowa
    ui_head("5. Próbkowanie siatki")
    grid = array.array("f", [0.0]) * (rows * cols)
    dlat = (lat_n - lat_s) / (rows - 1) if rows > 1 else 0.0
    dlng = (lng_e - lng_w) / (cols - 1) if cols > 1 else 0.0
    t_s = time.time()
    last_ui = 0.0
    for r in range(rows):
        la = lat_s + r * dlat
        lr = math.radians(la)
        fy = ((1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * (2 ** z)) * 256.0 - 0.5
        iy = int(math.floor(fy))
        wy = fy - iy
        base = r * cols
        for c in range(cols):
            fx = (((lng_w + c * dlng) + 180.0) / 360.0 * (2 ** z)) * 256.0 - 0.5
            ix = int(math.floor(fx))
            wx = fx - ix
            v = (px(ix, iy) * (1 - wx) + px(ix + 1, iy) * wx) * (1 - wy) + (px(ix, iy + 1) * (1 - wx) + px(ix + 1, iy + 1) * wx) * wy
            grid[base + c] = v
        now = time.time()
        if now - last_ui > 0.25:
            last_ui = now
            live("próbkowanie %s  wiersz %d/%d" % (bar(r / float(rows)), r, rows))
    live_clear()
    zmin, zmax = min(grid), max(grid)
    ui_ok("Próbkowanie gotowe w %s: wysokości %.0f .. %.0f m n.p.m." % (fmt_time(time.time() - t_s), zmin, zmax))

    # ----- 6. zapis
    ui_head("6. Zapis")
    out_dir = os.path.dirname(os.path.abspath(a.output))
    os.makedirs(out_dir, exist_ok=True)
    blob = base64.b64encode(zlib.compress(grid.tobytes(), 6)).decode("ascii")
    with open(a.output, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "zoom": z, "lat_s": lat_s, "lat_n": lat_n, "lng_w": lng_w, "lng_e": lng_e,
                   "rows": rows, "cols": cols, "step_m": a.step, "source": SOURCE, "z_b64": blob}, f, separators=(",", ":"))
    ui_ok("Zapisano: %s  (%s)" % (a.output, fmt_size(os.path.getsize(a.output))))
    print("")
    ui_box(["Gotowe w %s." % fmt_time(time.time() - t_all),
            "Skrypt Blendera znajdzie ten plik sam (data/hrf_terrain.json),",
            "jeśli leży obok danych trasy.",
            "Źródło wysokości: Mapzen/AWS Terrain Tiles (podaj przy publikacji)."])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        live_clear()
        print("")
        print("  [!]  PRZERWANO (Ctrl+C). Plik wynikowy nie został zmieniony.")
        if STATE["total"]:
            print_resume_hint()
        sys.exit(130)
    except Exception:
        live_clear()
        import traceback
        traceback.print_exc()
        print("")
        print("  [!]  Nieoczekiwany błąd (szczegóły wyżej).")
        if STATE["total"]:
            print_resume_hint(after_fix=True)
        sys.exit(1)
