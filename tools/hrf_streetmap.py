#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_streetmap.py  —  mapa ulic, rzek, wody, zieleni i granic miasta jako czarno-białe KONTURY (linie, bez wypełnień). Zieleń jest opcjonalna (domyślnie wyłączona).

Obszar mapy jest taki sam jak obszar terenu (data/<projekt>/hrf_terrain.json), więc plik PNG można nałożyć na teren w Blenderze
dokładnie tak jak warstwice (Styl terenu → „Mapa ulic”). Dane z OpenStreetMap pobierane są po PROSTOKĄTACH obszaru terenu
(do serwera Overpass trafiają tylko rogi prostokątów, nie trasa).

Wyniki (czarne tło, białe linie, północ u góry):
  <wyjście>.png         raster (skala szarości; grubość linii zależy od rodzaju: główne drogi najgrubsze, ścieżki najcieńsze)
  <wyjście>.pdf         wektor do edycji
  <wyjście>.svg         to samo z nazwanymi grupami (drogi-glowne, drogi-drugorzedne, rowery, ulice-boczne, sciezki, rzeki, strumienie, woda, zielen, granice)
  <wyjście>.meta.json   zasięg geograficzny planszy

Użycie (potrzebny Python 3 i internet, bez dodatkowych bibliotek):
    py tools/hrf_streetmap.py data/<projekt>/hrf_terrain.json data/<projekt>/hrf_streets
    py tools/hrf_streetmap.py - data/<projekt>/hrf_streets --bbox 49.40,49.70,20.40,20.90 --layers roads,rivers,boundaries

Opcje:
  --bbox A,B,C,D    lat_południe,lat_północ,lng_zachód,lng_wschód (zamiast pliku terenu; wtedy pierwszy argument to „-”)
  --layers LISTA    co rysować, po przecinku:
                    roads (główne arterie: autostrady, drogi krajowe, wojewódzkie i trzeciorzędne), cycleways (ścieżki rowerowe),
                    streets (drobne uliczki: osiedlowe, boczne, serwisowe), paths (chodniki, ścieżki, drogi leśne),
                    rivers (rzeki i kanały), streams (potoki), water (jeziora i zbiorniki), green (lasy, parki, łąki),
                    boundaries (granice miasta/gminy)
                    domyślnie: roads,cycleways,rivers,water,boundaries (drobne uliczki i zieleń tylko na życzenie)
  --admin-levels L  poziomy granic administracyjnych OSM, po przecinku (domyślnie 8 = gmina/miasto w Polsce; 6 = powiat, 4 = województwo)
  --tile-km K       bok prostokąta jednego zapytania w km (domyślnie 10; w gęstych miastach zmniejsz do 5)
  --png-width N     szerokość PNG w pikselach (domyślnie 4096; 0 = bez PNG)
  --page-width PT   szerokość strony PDF/SVG (domyślnie 1000)
  --scale X         mnożnik grubości linii (domyślnie 1.0); rzeki są grubsze niż ulice
  --tries N --timeout S --server URL --force --yes   jak w hrf_osm_fetch.py
  --no-pdf / --no-svg   pomiń dany plik
Przerwanie: Ctrl+C w dowolnej chwili; pobrane prostokąty zostają w pamięci podręcznej (cache/hrf_streets/).

Dane © współtwórcy OpenStreetMap (ODbL) – przy publikacji podaj to źródło.
"""
import argparse, hashlib, importlib.util, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")   # polskie znaki w terminalu Windows
except Exception:
    pass


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


osm = load("hrf_osm_fetch")        # Overpass: serwery, ponawianie, upraszczanie, interfejs
con = load("hrf_contours")         # rysowanie linii do PNG, zapis PNG
ui_box, ui_head, ui_ok, ui_warn, ui_info, live, live_clear, bar, fmt_time, fmt_size = (
    osm.ui_box, osm.ui_head, osm.ui_ok, osm.ui_warn, osm.ui_info, osm.live, osm.live_clear, osm.bar, osm.fmt_time, osm.fmt_size)

ALL_LAYERS = ("roads", "cycleways", "streets", "paths", "rivers", "streams", "water", "green", "boundaries")
DEFAULT_LAYERS = "roads,cycleways,rivers,water,boundaries"
# klasa -> (nazwa grupy w SVG, grubość w px przy PNG 4096, tolerancja uproszczenia w metrach)
CLASSES = [
    ("boundary",   "granice",         2.4, 5.0),
    ("green",      "zielen",          1.0, 4.0),
    ("water",      "woda",            2.0, 2.0),
    ("stream",     "strumienie",      1.6, 2.0),
    ("river",      "rzeki",           4.0, 2.0),
    ("path",       "sciezki",         0.8, 2.0),
    ("road_small", "ulice-boczne",     0.9, 1.5),
    ("cycle",      "rowery",          1.4, 2.0),
    ("road_minor", "drogi-drugorzedne", 1.5, 1.5),
    ("road_major", "drogi-glowne",    2.4, 1.2),
]
ROAD_MAJOR = "motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link"
ROAD_MINOR = "tertiary|tertiary_link"
ROAD_SMALL = "unclassified|residential|living_street|service|road"
PATHS = "footway|path|cycleway|track|pedestrian|steps|bridleway"
GREEN_LANDUSE = "forest|meadow|grass|village_green|recreation_ground"


# ---------------------------------------------------------------- zapytanie
def build_query(bb, layers, admin_levels, timeout):
    box = "(%.6f,%.6f,%.6f,%.6f)" % bb
    parts = []
    if "roads" in layers:
        parts.append('way["highway"~"^(%s|%s)$"]%s;' % (ROAD_MAJOR, ROAD_MINOR, box))
    if "streets" in layers:
        parts.append('way["highway"~"^(%s)$"]%s;' % (ROAD_SMALL, box))
    if "cycleways" in layers:
        parts.append('way["highway"="cycleway"]%s;way["highway"~"^(path|footway)$"]["bicycle"="designated"]%s;' % (box, box))
    if "paths" in layers:
        parts.append('way["highway"~"^(%s)$"]%s;' % (PATHS, box))
    if "rivers" in layers:
        parts.append('way["waterway"~"^(river|canal)$"]%s;' % box)
    if "streams" in layers:
        parts.append('way["waterway"="stream"]%s;' % box)
    if "water" in layers:
        parts.append('way["natural"="water"]%s;way["waterway"="riverbank"]%s;way["landuse"="reservoir"]%s;'
                     'relation["natural"="water"]["type"="multipolygon"]%s;' % ((box,) * 4))
    if "green" in layers:
        parts.append('way["natural"="wood"]%s;way["landuse"~"^(%s)$"]%s;way["leisure"="park"]%s;'
                     'relation["natural"="wood"]["type"="multipolygon"]%s;relation["landuse"~"^(forest|meadow|grass)$"]["type"="multipolygon"]%s;'
                     'relation["leisure"="park"]["type"="multipolygon"]%s;' % (box, GREEN_LANDUSE, box, box, box, box, box))
    if "boundaries" in layers:
        parts.append('relation["boundary"="administrative"]["admin_level"~"^(%s)$"]%s;' % ("|".join(admin_levels), box))
    return "[out:json][timeout:%d];(%s);out geom(%.6f,%.6f,%.6f,%.6f);" % ((timeout, "".join(parts)) + bb)


def tile_cache_name(i, j, bb, layers, admin_levels):
    key = "%.6f,%.6f,%.6f,%.6f|%s|%s" % (bb + (",".join(sorted(layers)), ",".join(admin_levels)))
    return "t%02d_%02d_%s.json" % (i, j, hashlib.md5(key.encode("ascii")).hexdigest()[:8])


# ---------------------------------------------------------------- klasyfikacja i geometria
def runs_of(geometry):
    """Ciągi punktów (lat, lng); brakujące punkty (null poza prostokątem) przerywają linię."""
    runs, cur = [], []
    for g in geometry or []:
        if g:
            cur.append((g["lat"], g["lon"]))
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def classify(el, layers):
    """Zwraca klasę elementu OSM albo None."""
    t = el.get("tags", {})
    if el["type"] == "relation":
        if t.get("boundary") == "administrative":
            return "boundary" if "boundaries" in layers else None
        if t.get("natural") == "water":
            return "water" if "water" in layers else None
        if t.get("natural") == "wood" or t.get("landuse") or t.get("leisure") == "park":
            return "green" if "green" in layers else None
        return None
    if "highway" in t:
        h = t["highway"]
        if h in ROAD_MAJOR.split("|"):
            return "road_major" if "roads" in layers else None
        if h in ROAD_MINOR.split("|"):
            return "road_minor" if "roads" in layers else None
        if h in ROAD_SMALL.split("|"):
            return "road_small" if "streets" in layers else None
        if h == "cycleway" or (t.get("bicycle") == "designated" and h in ("path", "footway")):
            return "cycle" if "cycleways" in layers else None
        return "path" if "paths" in layers else None
    w = t.get("waterway")
    if w in ("river", "canal"):
        return "river" if "rivers" in layers else None
    if w == "stream":
        return "stream" if "streams" in layers else None
    if t.get("natural") == "water" or w == "riverbank" or t.get("landuse") == "reservoir":
        return "water" if "water" in layers else None
    if t.get("natural") == "wood" or t.get("landuse") in GREEN_LANDUSE.split("|") or t.get("leisure") == "park":
        return "green" if "green" in layers else None
    return None


def element_runs(el):
    if el["type"] == "way":
        return runs_of(el.get("geometry"))
    out = []
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("geometry"):
            out += runs_of(m["geometry"])
    return out


def simplify_run(run, eps_m, lat0):
    if len(run) >= 4 and run[0] == run[-1]:
        ring = osm.simplify_ring(run, eps_m, lat0)
        return ring + [ring[0]] if len(ring) >= 3 else []
    return osm.rdp(run, eps_m, lat0)


# ---------------------------------------------------------------- zapis: PDF, SVG
def write_pdf(path, pw, ph, layers):
    """layers = [(nazwa, grubość_pt, [linie])]; linie w punktach strony (y w górę)."""
    out = ["0 0 0 rg", "0 0 %.2f %.2f re f" % (pw, ph), "1 1 1 RG", "1 J 1 j"]
    for _name, width, lines in layers:
        out.append("%.3f w" % width)
        for ln in lines:
            out.append("%.2f %.2f m" % ln[0])
            out.extend("%.2f %.2f l" % p for p in ln[1:])
            out.append("S")
    import zlib
    comp = zlib.compress("\n".join(out).encode("ascii"), 6)
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


def write_svg(path, pw, ph, layers):
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="%.2f" height="%.2f" viewBox="0 0 %.2f %.2f">\n' % (pw, ph, pw, ph))
        f.write('<rect id="tlo" width="%.2f" height="%.2f" fill="#000000"/>\n' % (pw, ph))
        for name, width, lines in layers:
            d = "".join("M%.2f %.2f" % (ln[0][0], ph - ln[0][1]) + "".join("L%.2f %.2f" % (x, ph - y) for x, y in ln[1:]) for ln in lines)
            f.write('<g id="%s" fill="none" stroke="#ffffff" stroke-width="%.3f" stroke-linecap="round" stroke-linejoin="round">\n<path d="%s"/>\n</g>\n' % (name, width, d))
        f.write("</svg>\n")


def split_long(lines, maxlen):
    """Dzieli długie odcinki na krótkie (szybsze rysowanie: obszar sprawdzany dla odcinka jest mały)."""
    out = []
    for ln in lines:
        res = [ln[0]]
        for k in range(1, len(ln)):
            x0, y0 = res[-1]
            x1, y1 = ln[k]
            d = math.hypot(x1 - x0, y1 - y0)
            n = int(d // maxlen)
            for s in range(1, n + 1):
                f = s / float(n + 1)
                res.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
            res.append((x1, y1))
        out.append(res)
    return out


# ---------------------------------------------------------------- program
def main():
    ap = argparse.ArgumentParser(description="Mapa ulic, rzek, wody i granic jako czarno-białe kontury (PNG, PDF, SVG).")
    ap.add_argument("terrain", help="plik terenu data/<projekt>/hrf_terrain.json albo „-” razem z --bbox")
    ap.add_argument("output", help="ścieżka wyjściowa bez rozszerzenia, np. data/<projekt>/hrf_streets")
    ap.add_argument("--bbox")
    ap.add_argument("--layers", default=DEFAULT_LAYERS)
    ap.add_argument("--admin-levels", default="8")
    ap.add_argument("--tile-km", type=float, default=10.0)
    ap.add_argument("--png-width", type=int, default=4096)
    ap.add_argument("--page-width", type=float, default=1000.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--tries", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--server")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--no-svg", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    t_all = time.time()
    servers = [a.server] if a.server else osm.SERVERS
    layers = [x.strip() for x in a.layers.split(",") if x.strip()]
    bad = [x for x in layers if x not in ALL_LAYERS]
    if bad or not layers:
        ui_warn("Nieznane warstwy: %s. Dozwolone: %s" % (", ".join(bad) or "(pusto)", ", ".join(ALL_LAYERS)))
        return 1
    admin = [x.strip() for x in a.admin_levels.split(",") if x.strip().isdigit()] or ["8"]

    ui_box(["HRF  -  mapa ulic, rzek, wody i granic (kontury)",
            "Teren:  " + (os.path.basename(a.terrain) if a.bbox is None else "wycinek --bbox"),
            "Wyjście: " + a.output + ".png / .pdf / .svg",
            "Ctrl+C = anuluj w dowolnej chwili (pobrane prostokąty zostają)."])

    # ----- 1. obszar
    ui_head("1. Obszar")
    if a.bbox:
        try:
            s_, n_, w_, e_ = [float(x) for x in a.bbox.split(",")]
        except ValueError:
            ui_warn("--bbox: podaj cztery liczby lat_S,lat_N,lng_W,lng_E")
            return 1
        ext = {"lat_s": s_, "lat_n": n_, "lng_w": w_, "lng_e": e_}
    else:
        if not os.path.isfile(a.terrain):
            ui_warn("Nie ma pliku terenu: " + a.terrain)
            ui_info("Najpierw pobierz teren (hrf_terrain_fetch.py) albo podaj --bbox z „-” zamiast pliku.")
            return 1
        with open(a.terrain, encoding="utf-8") as f:
            d = json.load(f)
        ext = {k: d[k] for k in ("lat_s", "lat_n", "lng_w", "lng_e")}
    if ext["lat_n"] <= ext["lat_s"] or ext["lng_e"] <= ext["lng_w"]:
        ui_warn("Zły obszar: północ musi być większa od południa, a wschód od zachodu.")
        return 1
    earth = 6371008.8
    latc = (ext["lat_s"] + ext["lat_n"]) / 2.0
    wm = math.radians(ext["lng_e"] - ext["lng_w"]) * earth * math.cos(math.radians(latc))
    hm = math.radians(ext["lat_n"] - ext["lat_s"]) * earth
    ui_ok("Obszar %.1f x %.1f km  (%.4f..%.4f N, %.4f..%.4f E)" % (wm / 1000.0, hm / 1000.0, ext["lat_s"], ext["lat_n"], ext["lng_w"], ext["lng_e"]))
    ui_info("Warstwy: " + ", ".join(layers) + ("   (granice: admin_level %s)" % ",".join(admin) if "boundaries" in layers else ""))

    # ----- 2. plan prostokątów
    ui_head("2. Plan pobierania")
    nlat = max(1, int(math.ceil(hm / 1000.0 / a.tile_km)))
    nlng = max(1, int(math.ceil(wm / 1000.0 / a.tile_km)))
    tiles = []
    for i in range(nlat):
        for j in range(nlng):
            bb = (round(ext["lat_s"] + (ext["lat_n"] - ext["lat_s"]) * i / nlat, 6), round(ext["lng_w"] + (ext["lng_e"] - ext["lng_w"]) * j / nlng, 6),
                  round(ext["lat_s"] + (ext["lat_n"] - ext["lat_s"]) * (i + 1) / nlat, 6), round(ext["lng_w"] + (ext["lng_e"] - ext["lng_w"]) * (j + 1) / nlng, 6))
            tiles.append((i, j, bb))
    cache_dir = osm.cache_dir_for(a.output)
    os.makedirs(cache_dir, exist_ok=True)
    files = [os.path.join(cache_dir, tile_cache_name(i, j, bb, layers, admin)) for i, j, bb in tiles]
    cached = [os.path.exists(fp) and not a.force for fp in files]
    todo = [k for k in range(len(tiles)) if not cached[k]]
    ui_info("Prostokątów: %d (%d x %d, po ok. %.0f km)   z pamięci podręcznej: %d, do pobrania: %d" % (len(tiles), nlng, nlat, a.tile_km, len(tiles) - len(todo), len(todo)))
    if todo and len(todo) < len(tiles):
        ui_info("To jest WZNOWIENIE: prostokąty z pamięci podręcznej nie będą pobierane ponownie.")
    if todo:
        ui_info("Szacunkowo: %s (zależy od gęstości zabudowy i obciążenia serwerów Overpass)" % fmt_time(len(todo) * 40))
        ui_info("Wysyłane są tylko współrzędne rogów prostokątów (nie trasa) do: " + ", ".join(osm.host(s) for s in servers))
        if not a.yes and sys.stdin.isatty() and not osm.ask_yes("Zacząć pobieranie?"):
            ui_warn("Anulowano przed pobieraniem. Nic nie zmieniono.")
            return 130

    # ----- 3. pobieranie
    responses = [None] * len(tiles)
    failed, done_times, dl_bytes, k_done = [], [], 0, 0
    if todo:
        ui_head("3. Pobieranie")
    for k, (i, j, bb) in enumerate(tiles):
        label = "prostokąt %d/%d" % (k + 1, len(tiles))
        if cached[k]:
            with open(files[k], encoding="utf-8") as f:
                responses[k] = json.load(f)
            continue
        eta = (sum(done_times) / len(done_times) + 2) * (len(todo) - k_done) if done_times else None
        ui_info("%s  pobrano %d z %d  %s" % (bar(k_done / float(len(todo))), k_done, len(todo), ("pozostało ok. " + fmt_time(eta)) if eta else "szacuję czas..."))
        res = osm.fetch_overpass(build_query(bb, layers, admin, a.timeout), label, servers, a.tries, a.timeout)
        k_done += 1
        if res is None:
            ui_warn("%s: NIE POBRANO po %d próbach – pomijam, spróbuj ponownie później." % (label, a.tries))
            failed.append(k)
            continue
        response, nbytes, server, elapsed = res
        with open(files[k], "w", encoding="utf-8") as f:
            json.dump(response, f)
        responses[k] = response
        dl_bytes += nbytes
        done_times.append(elapsed)
        ui_ok("%s pobrany: %d elementów, %s, %s (%s)" % (label, len(response.get("elements", [])), fmt_size(nbytes), fmt_time(elapsed), osm.host(server)))
        if k_done < len(todo):
            for _ in range(8):
                time.sleep(0.25)
    live_clear()
    if failed:
        ui_head("Pobieranie niekompletne")
        ui_warn("Brakuje %d z %d prostokątów: %s" % (len(failed), len(tiles), ", ".join(str(k + 1) for k in failed)))
        ui_info("Pliki wynikowe NIE zostały zapisane (mapa byłaby dziurawa). Uruchom tę samą komendę ponownie – dociągnie tylko brakujące.")
        return 2
    if todo:
        ui_ok("Pobrano wszystko: %d prostokątów, %s, w %s" % (len(todo), fmt_size(dl_bytes), fmt_time(sum(done_times))))

    # ----- 4. przetwarzanie
    ui_head("4. Przetwarzanie")
    by_class = {c[0]: [] for c in CLASSES}
    eps = {c[0]: c[3] for c in CLASSES}
    total_el = sum(len(r.get("elements", [])) for r in responses)
    px_m = wm / float(a.png_width or 4096)          # metrów na piksel PNG
    min_blob = 4.0 * px_m                           # drobne obrysy zieleni i wody (kępki trawy, oczka) poniżej ok. 4 px zostają pominięte
    seen = 0
    skipped = 0
    for resp in responses:
        for el in resp.get("elements", []):
            seen += 1
            if seen % 500 == 0:
                live("klasyfikacja %s  %d/%d" % (bar(seen / float(max(1, total_el))), seen, total_el))
            cls = classify(el, layers)
            if not cls:
                continue
            for run in element_runs(el):
                if cls in ("green", "water") and run[0] == run[-1]:
                    lats, lngs = [p[0] for p in run], [p[1] for p in run]
                    size = math.hypot((max(lats) - min(lats)) * 111320.0, (max(lngs) - min(lngs)) * 111320.0 * math.cos(math.radians(latc)))
                    if size < min_blob:
                        skipped += 1
                        continue
                run = simplify_run(run, eps[cls], latc)
                if len(run) >= 2:
                    by_class[cls].append(run)
    live_clear()
    ui_ok("Sklasyfikowano %d obiektów (pominięto %d drobnych obrysów zieleni/wody poniżej %.0f m)" % (total_el, skipped, min_blob))

    pw = a.page_width
    ph = pw * hm / wm
    dlat, dlng = ext["lat_n"] - ext["lat_s"], ext["lng_e"] - ext["lng_w"]

    def to_page(run):
        return [((lng - ext["lng_w"]) / dlng * pw, (lat - ext["lat_s"]) / dlat * ph) for lat, lng in run]
    page_layers, counts = [], {}
    for key, name, wpx, _eps in CLASSES:
        lines = [to_page(r) for r in by_class[key]]
        counts[name] = len(lines)
        if lines:
            page_layers.append((name, wpx * a.scale / 4096.0 * pw, lines, wpx * a.scale))
    for key, name, *_ in CLASSES:
        if counts.get(name):
            ui_info("%-16s %7d linii" % (name, counts[name]))
    if not page_layers:
        ui_warn("W tym obszarze nie znaleziono żadnych obiektów wybranych warstw.")

    # ----- 5. zapis
    ui_head("5. Zapis")
    out_dir = os.path.dirname(os.path.abspath(a.output))
    os.makedirs(out_dir, exist_ok=True)
    pdf_layers = [(n, w, ls) for n, w, ls, _ in page_layers]
    if not a.no_pdf:
        write_pdf(a.output + ".pdf", pw, ph, pdf_layers)
        ui_ok("PDF: %s.pdf  (%s)" % (a.output, fmt_size(os.path.getsize(a.output + ".pdf"))))
    if not a.no_svg:
        write_svg(a.output + ".svg", pw, ph, pdf_layers)
        ui_ok("SVG: %s.svg  (%s)" % (a.output, fmt_size(os.path.getsize(a.output + ".svg"))))
    pxw = a.png_width
    pxh = int(round(pxw * hm / wm)) if pxw else 0
    if pxw:
        t1 = time.time()
        k = pxw / pw
        pix = bytearray(pxw * pxh)
        for name, _w, lines, wpx in page_layers:
            px_lines = split_long([[(x * k, (ph - y) * k) for x, y in ln] for ln in lines], 10.0)
            con.raster_lines(pix, pxw, pxh, px_lines, wpx * pxw / 4096.0 / 2.0, lambda f, name=name: live("PNG, %s %s" % (name, bar(f))))
            live_clear()
        con.write_png_gray(a.output + ".png", pxw, pxh, pix)
        ui_ok("PNG: %s.png  (%d x %d, %s) w %s" % (a.output, pxw, pxh, fmt_size(os.path.getsize(a.output + ".png")), fmt_time(time.time() - t1)))
    with open(a.output + ".meta.json", "w", encoding="utf-8") as f:
        json.dump({"lat_s": ext["lat_s"], "lat_n": ext["lat_n"], "lng_w": ext["lng_w"], "lng_e": ext["lng_e"], "png": os.path.basename(a.output) + ".png" if pxw else None,
                   "png_width": pxw, "png_height": pxh, "page_pt": [pw, ph], "layers": layers, "counts": counts,
                   "source": "© współtwórcy OpenStreetMap (ODbL)"}, f, indent=1, ensure_ascii=False)
    ui_ok("Zasięg planszy: %s.meta.json" % a.output)
    print("")
    ui_box(["Gotowe w %s." % fmt_time(time.time() - t_all),
            "Blender: Styl terenu → Mapa ulic (tekstura), potem „Wczytaj teksturę ponownie”.",
            "Dane © współtwórcy OpenStreetMap (ODbL)."])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        live_clear()
        print("")
        print("  [!]  PRZERWANO (Ctrl+C). Pobrane prostokąty zostały w pamięci podręcznej – uruchom tę samą komendę ponownie.")
        sys.exit(130)
