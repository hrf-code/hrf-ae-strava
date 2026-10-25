#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_osm_fetch.py  —  pobiera z OpenStreetMap (Overpass API) otoczenie trasy
dla mini mapy 3D w panelu After Effects (AE/hrf_ae_panel.jsx).

Użycie (potrzebny Python 3 i internet, bez dodatkowych bibliotek):
    py tools/hrf_osm_fetch.py data/aktywnosc_hrf.json data/hrf_osm.json
    py tools/hrf_osm_fetch.py data/aktywnosc_hrf.json data/hrf_osm.json --radius 150 --yes

Pobiera tylko: drogi i ścieżki (z nazwami), budynki, wodę (rzeki, jeziora),
tereny zielone (lasy, łąki, parki, pola – uproszczone).

Co widać w trakcie:
  - plan przed startem (ile odcinków, ile z pamięci podręcznej, ile do pobrania) i pytanie o zgodę,
  - na żywo: który odcinek, próba, serwer, upływający czas, pasek postępu i szacowany czas do końca,
  - każdy błąd osobno (kod HTTP / timeout) i odliczanie do ponowienia,
  - przetwarzanie z postępem i podsumowanie (liczba obiektów, rozmiar pliku, czas).

Przerwanie: Ctrl+C w dowolnej chwili. Odcinki już pobrane zostają w pamięci podręcznej
(<plik wyjściowy>.cache) – ponowne uruchomienie dociąga tylko brakujące.

Opcje:
  --radius M     pas wokół trasy w metrach (domyślnie 150)
  --chunk M      długość odcinka trasy na jedno zapytanie, m (domyślnie 3000)
  --tries N      liczba prób na odcinek (domyślnie 6, serwery używane naprzemiennie)
  --timeout S    limit czasu jednego zapytania w sekundach (domyślnie 90)
  --server URL   własny serwer Overpass (zamiast domyślnych)
  --force        ignoruj pamięć podręczną i pobierz wszystko od nowa
  --yes          nie pytaj o zgodę przed pobieraniem

Dane © współtwórcy OpenStreetMap (ODbL) – przy publikacji podaj to źródło.
Klucze wynikowego JSON: radius_m, source, road_major, road_minor, path, river,
water, green, building, names.
"""
import hashlib, json, math, os, sys, threading, time, argparse
import urllib.request, urllib.parse, urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")   # polskie znaki w terminalu Windows
except Exception:
    pass

SERVERS = [
    "https://overpass.openstreetmap.fr/api/interpreter",   # działał najszybciej w testach
    "https://overpass-api.de/api/interpreter",             # główny, często przeciążony (502)
    "https://overpass.kumi.systems/api/interpreter",
]
EARTH_R = 6371000.0
QUERY_EPS_M = 15.0   # tolerancja uproszczenia linii w zapytaniu, m
W = 64
TTY = sys.stdout.isatty()
SPIN = "|/-\\"


# ---------------------------------------------------------------- interfejs
def ui_box(lines):
    print("+" + "-" * (W - 2) + "+")
    for l in lines:
        print("| " + l.ljust(W - 4) + " |")
    print("+" + "-" * (W - 2) + "+")


def ui_head(text):
    print("")
    print("== " + text + " " + "=" * max(3, W - 4 - len(text)))


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


def live(text):
    """Linia stanu nadpisywana w miejscu (tylko w terminalu)."""
    if TTY:
        sys.stdout.write("\r  " + text.ljust(W + 18)[:W + 18])
        sys.stdout.flush()


def live_clear():
    if TTY:
        sys.stdout.write("\r" + " " * (W + 22) + "\r")
        sys.stdout.flush()


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


def host(url):
    return urllib.parse.urlparse(url).netloc or url


STATE = {"total": 0, "cached": 0}   # ile odcinków łącznie i ile już w pamięci podręcznej (do komunikatów po przerwaniu)


def rerun_command():
    """Dokładnie ta sama komenda, którą uruchomiono – do skopiowania przy wznawianiu."""
    parts = ["py"] + sys.argv
    return " ".join('"%s"' % x if (" " in x) else x for x in parts)


def print_resume_hint(after_fix=False):
    ui_info("Odcinki w pamięci podręcznej: %d z %d (nie trzeba ich pobierać ponownie)." % (STATE["cached"], STATE["total"]))
    if after_fix:
        ui_info("Pobrane dane są bezpieczne. Gdy błąd zostanie naprawiony (np. nowsza wersja narzędzia),")
        ui_info("uruchom TĘ SAMĄ komendę jeszcze raz (bez --force) – bez ponownego pobierania:")
    else:
        ui_info("Aby dokończyć, uruchom TĘ SAMĄ komendę jeszcze raz (bez --force):")
    print("")
    print("       " + rerun_command())
    print("")


# ---------------------------------------------------------------- geometria
def dist_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def densify_route(pts, step=10.0):
    """Trasa co ok. step metrów (do liczenia odległości od trasy)."""
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        d = dist_m(a, b)
        n = int(d // step)
        for k in range(1, n + 1):
            f = k / (n + 1.0)
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        out.append(b)
    return out


class RouteIndex:
    """Siatka do szybkiego pytania „jak daleko do trasy”."""

    def __init__(self, pts, cell_m=200.0):
        self.lat0 = pts[0][0]
        self.my = 111320.0
        self.mx = 111320.0 * math.cos(math.radians(self.lat0))
        self.cell = cell_m
        self.g = {}
        for p in pts:
            self.g.setdefault(self.key(p), []).append(p)

    def key(self, p):
        return (int(p[1] * self.mx // self.cell), int(p[0] * self.my // self.cell))

    def dist(self, p, limit):
        kx, ky = self.key(p)
        best = 1e18
        rng = int(limit // self.cell) + 1
        for dx in range(-rng, rng + 1):
            for dy in range(-rng, rng + 1):
                for q in self.g.get((kx + dx, ky + dy), ()):
                    d = math.hypot((p[1] - q[1]) * self.mx, (p[0] - q[0]) * self.my)
                    if d < best:
                        best = d
        return best


def rdp(pts, eps_m, lat0):
    """Uproszczenie łamanej (Douglas–Peucker), eps w metrach."""
    if len(pts) < 3 or eps_m <= 0:
        return pts
    my = 111320.0
    mx = my * math.cos(math.radians(lat0))
    xy = [((p[1]) * mx, (p[0]) * my) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        dm, im = -1, -1
        for i in range(a + 1, b):
            d = abs(dy * xy[i][0] - dx * xy[i][1] + bx * ay - by * ax) / L
            if d > dm:
                dm, im = d, i
        if dm > eps_m:
            keep[im] = True
            stack.append((a, im))
            stack.append((im, b))
    return [p for p, k in zip(pts, keep) if k]


def simplify_ring(pts, eps_m, lat0):
    """Uproszczenie zamkniętego obrysu. Zwykły Douglas–Peucker na obrysie, w którym pierwszy punkt = ostatni,
    gubi wszystko (odcinek startowy ma długość 0), więc obrys rozcinamy w punkcie najdalszym od pierwszego
    i upraszczamy obie połowy osobno. Zwraca obrys bez powtórzonego punktu końcowego."""
    ring = pts[:-1] if len(pts) > 1 and pts[0] == pts[-1] else list(pts)
    if len(ring) < 3 or eps_m <= 0:
        return ring
    cl = math.cos(math.radians(lat0))
    far = max(range(1, len(ring)), key=lambda i: (ring[i][0] - ring[0][0]) ** 2 + ((ring[i][1] - ring[0][1]) * cl) ** 2)
    a = rdp(ring[:far + 1], eps_m, lat0)
    b = rdp(ring[far:] + [ring[0]], eps_m, lat0)
    return a[:-1] + b[:-1]


# ---------------------------------------------------------------- Overpass
def build_query(chunk, r, timeout=90):
    poly = ",".join("%.6f,%.6f" % (p[0], p[1]) for p in chunk)
    ar = "(around:%d,%s)" % (r, poly)
    return (
        "[out:json][timeout:%d];(" % timeout +
        'way["highway"]["highway"!~"proposed|construction|bus_stop|platform|elevator"]%s;'
        'way["building"]%s;'
        'way["waterway"~"^(river|stream|canal|drain|ditch)$"]%s;'
        'way["natural"~"^(water|wood|scrub|grassland|wetland)$"]%s;'
        'way["landuse"~"^(forest|meadow|grass|farmland|orchard|vineyard|allotments|cemetery|recreation_ground|village_green)$"]%s;'
        'way["leisure"~"^(park|garden|pitch|playground)$"]%s;'
        'relation["natural"~"^(water|wood)$"]["type"="multipolygon"]%s;'
        'relation["landuse"~"^(forest|meadow|grass|farmland)$"]["type"="multipolygon"]%s;'
        ");out geom;" % ((ar,) * 8)
    )


def _request(server, query, timeout, box):
    """Jedno zapytanie w osobnym wątku (żeby Ctrl+C działał od razu)."""
    try:
        payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request(server, data=payload, headers={"User-Agent": "hrf-ae-overlay/1.0 (osobiste grafiki ze Stravy)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        data = json.loads(raw.decode("utf-8"))
        remark = str(data.get("remark", ""))
        if remark and "error" in remark.lower():
            raise RuntimeError("serwer zwrócił błąd: " + remark[:120])
        box["data"], box["bytes"] = data, len(raw)
    except urllib.error.HTTPError as e:
        box["error"] = "HTTP %d%s" % (e.code, " (serwer zajęty)" if e.code in (429, 503, 504) else "")
        box["code"] = e.code
    except Exception as e:  # sieć, timeout, JSON
        box["error"] = (type(e).__name__ + ": " + str(e))[:140]


def fetch_overpass(query, label, servers, tries, timeout):
    """Pobiera jedno zapytanie z ponawianiem. Zwraca (dane, bajty, serwer, czas) albo None."""
    for attempt in range(tries):
        server = servers[attempt % len(servers)]
        box = {}
        t = threading.Thread(target=_request, args=(server, query, timeout, box), daemon=True)
        t0 = time.time()
        t.start()
        spin = 0
        while t.is_alive():
            time.sleep(0.2)
            spin += 1
            live("%s  próba %d/%d  %s  %s %s" % (label, attempt + 1, tries, host(server), fmt_time(time.time() - t0), SPIN[spin % 4]))
        elapsed = time.time() - t0
        if "data" in box:
            return box["data"], box["bytes"], server, elapsed
        wait = 15 if box.get("code") in (429, 503, 504) else (5 if "code" in box else 8)
        ui_warn("%s  próba %d/%d nieudana po %s (%s): %s" % (label, attempt + 1, tries, fmt_time(elapsed), host(server), box.get("error", "?")))
        if attempt == tries - 1:
            break
        end = time.time() + wait
        while time.time() < end:
            live("%s  ponawiam za %d s (Ctrl+C = anuluj)" % (label, int(math.ceil(end - time.time()))))
            time.sleep(0.25)
    return None


# ---------------------------------------------------------------- klasyfikacja
def road_class(tags):
    h = tags.get("highway", "")
    if h in ("motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"):
        return "road_major"
    if h in ("tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "road"):
        return "road_minor"
    return "path"


def area_class(tags):
    if "building" in tags:
        return "building"
    if tags.get("natural") == "water" or tags.get("water") or tags.get("landuse") == "reservoir":
        return "water"
    if "waterway" in tags:
        return "river"
    if tags.get("natural") in ("wood", "scrub", "grassland", "wetland") or tags.get("landuse") or tags.get("leisure"):
        return "green"
    return None


def join_rings(members):
    """Skleja segmenty 'outer' relacji w zamknięte pierścienie."""
    segs = [list(m) for m in members if len(m) >= 2]
    rings = []
    while segs:
        cur = segs.pop()
        changed = True
        while changed and cur[0] != cur[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += list(reversed(s))[1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = list(reversed(s))[:-1] + cur
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def chunk_cache_name(i, chunk, radius):
    """Nazwa pliku cache zależy od geometrii odcinka i promienia – zmiana --chunk/--radius nie użyje cudzych danych."""
    h = hashlib.md5(("%d|" % radius + ";".join("%.5f,%.5f" % (p[0], p[1]) for p in chunk)).encode("ascii")).hexdigest()[:8]
    return "k%03d_r%d_%s.json" % (i, radius, h)


# ---------------------------------------------------------------- program
def main():
    ap = argparse.ArgumentParser(description="Pobiera otoczenie trasy z OpenStreetMap (Overpass API).")
    ap.add_argument("data", help="plik *_hrf.json z konwertera")
    ap.add_argument("output", help="plik wyjściowy, np. data/hrf_osm.json")
    ap.add_argument("--radius", type=int, default=150, help="pas wokół trasy w metrach (domyślnie 150)")
    ap.add_argument("--chunk", type=float, default=3000.0, help="długość odcinka trasy na jedno zapytanie, m")
    ap.add_argument("--tries", type=int, default=6, help="liczba prób na odcinek")
    ap.add_argument("--timeout", type=int, default=90, help="limit czasu jednego zapytania, s (potem następna próba na drugim serwerze)")
    ap.add_argument("--server", help="własny serwer Overpass (adres interpretera)")
    ap.add_argument("--force", action="store_true", help="ignoruj pamięć podręczną")
    ap.add_argument("--yes", action="store_true", help="nie pytaj o zgodę")
    a = ap.parse_args()
    servers = [a.server] if a.server else SERVERS
    t_all = time.time()

    ui_box(["HRF  -  pobieranie otoczenia trasy z OpenStreetMap",
            "Plik wejściowy: " + os.path.basename(a.data),
            "Wynik:          " + a.output,
            "Ctrl+C = anuluj w dowolnej chwili (pobrane odcinki zostają)."])

    # ----- 1. trasa
    ui_head("1. Wczytywanie trasy")
    if not os.path.isfile(a.data):
        ui_warn("Nie ma pliku: " + a.data)
        ui_info("Folder, w którym jesteś: " + os.getcwd())
        ui_info("Podaj ścieżkę względem tego folderu, np. ..\\data\\<nazwa>_hrf.json (z folderu tools)")
        ui_info("albo data/<nazwa>_hrf.json (z głównego folderu repo).")
        return 1
    with open(a.data, encoding="utf-8-sig") as f:
        D = json.load(f)
    S = D.get("samples") or D
    lat, lng = S["lat"], S["lng"]
    route = [(la, lo) for la, lo in zip(lat, lng)]
    ui_ok("Wczytano %d punktów (%s)" % (len(route), str(D.get("name", ""))[:40] or "bez nazwy"))
    sparse = [route[0]]
    for p in route[1:]:
        if dist_m(sparse[-1], p) >= 40:
            sparse.append(p)
    if sparse[-1] != route[-1]:
        sparse.append(route[-1])
    total_m = sum(dist_m(sparse[i - 1], sparse[i]) for i in range(1, len(sparse)))
    ui_info("Długość trasy: %.1f km, obszar: %.4f..%.4f N, %.4f..%.4f E" % (total_m / 1000.0, min(lat), max(lat), min(lng), max(lng)))

    chunks, cur, length = [], [sparse[0]], 0.0
    for p in sparse[1:]:
        length += dist_m(cur[-1], p)
        cur.append(p)
        if length >= a.chunk:
            chunks.append(cur)
            cur, length = [p], 0.0
    if len(cur) > 1:
        chunks.append(cur)

    # ----- 2. plan
    ui_head("2. Plan pobierania")
    cache_dir = a.output + ".cache"
    os.makedirs(cache_dir, exist_ok=True)
    files = [os.path.join(cache_dir, chunk_cache_name(i, ch, a.radius)) for i, ch in enumerate(chunks)]
    cached = [os.path.exists(fp) and not a.force for fp in files]
    todo = [i for i in range(len(chunks)) if not cached[i]]
    ui_info("Odcinków: %d  (pas %d m, po ok. %.1f km)" % (len(chunks), a.radius, a.chunk / 1000.0))
    if len(chunks) <= 40:
        for i, ch in enumerate(chunks):
            seg = sum(dist_m(ch[j - 1], ch[j]) for j in range(1, len(ch)))
            ui_info("odcinek %2d/%d  %.1f km  %s" % (i + 1, len(chunks), seg / 1000.0, "z pamięci podręcznej" if cached[i] else "DO POBRANIA"))
    STATE["total"] = len(chunks)
    STATE["cached"] = len(chunks) - len(todo)
    ui_info("Z pamięci podręcznej: %d,  do pobrania: %d" % (len(chunks) - len(todo), len(todo)))
    if todo and STATE["cached"]:
        ui_info("To jest WZNOWIENIE: odcinki z pamięci podręcznej nie będą pobierane ponownie.")
    if todo:
        ui_info("Szacunkowo: %s (zależy od obciążenia serwerów Overpass)" % fmt_time(len(todo) * 17))
        ui_info("Wysyłane są współrzędne trasy do: " + ", ".join(host(s) for s in servers))
        if not a.yes and sys.stdin.isatty() and not ask_yes("Zacząć pobieranie?"):
            ui_warn("Anulowano przed pobieraniem. Nic nie zmieniono.")
            return 130

    # ----- 3. pobieranie
    elements = {}
    failed = []
    done_times = []
    dl_bytes = 0
    if todo:
        ui_head("3. Pobieranie")
    n_todo = len(todo)
    k_done = 0
    for i, ch in enumerate(chunks):
        label = "odcinek %d/%d" % (i + 1, len(chunks))
        if cached[i]:
            with open(files[i], encoding="utf-8") as f:
                response = json.load(f)
        else:
            frac = k_done / float(n_todo)
            eta = (sum(done_times) / len(done_times) + 2) * (n_todo - k_done) if done_times else None
            ui_info("%s  %s  %s" % (bar(frac), "pobrano %d z %d" % (k_done, n_todo), ("pozostało ok. " + fmt_time(eta)) if eta else "szacuję czas..."))
            # linia w zapytaniu uproszczona (mniej wierzchołków = szybsze zapytanie); pas poszerzony o tolerancję uproszczenia
            qline = rdp(ch, QUERY_EPS_M, ch[0][0])
            res = fetch_overpass(build_query(qline, a.radius + int(QUERY_EPS_M), a.timeout), label, servers, a.tries, a.timeout)
            if res is None:
                ui_warn("%s: NIE POBRANO po %d próbach – pomijam, spróbuj ponownie później." % (label, a.tries))
                failed.append(i)
                k_done += 1
                continue
            response, nbytes, server, elapsed = res
            with open(files[i], "w", encoding="utf-8") as f:
                json.dump(response, f)
            dl_bytes += nbytes
            STATE["cached"] += 1
            done_times.append(elapsed)
            k_done += 1
            ui_ok("%s pobrany: %d elementów, %s, %s (%s)" % (label, len(response.get("elements", [])), fmt_size(nbytes), fmt_time(elapsed), host(server)))
            if k_done < n_todo:
                for _ in range(8):   # krótka przerwa dla serwera, przerywalna
                    time.sleep(0.25)
        for el in response.get("elements", []):
            elements[(el["type"], el["id"])] = el

    if failed:
        live_clear()
        ui_head("Pobieranie niekompletne")
        ui_warn("Brakuje %d z %d odcinków: %s" % (len(failed), len(chunks), ", ".join(str(i + 1) for i in failed)))
        ui_info("Plik wynikowy NIE został zapisany (byłby niepełny).")
        ui_info("Pobrane odcinki są w pamięci podręcznej, dociągnięte zostaną tylko brakujące.")
        print_resume_hint()
        return 2
    live_clear()
    if todo:
        ui_ok("Pobrano wszystko: %d odcinków, %s, w %s" % (n_todo, fmt_size(dl_bytes), fmt_time(sum(done_times))))

    # ----- 4. przetwarzanie
    ui_head("4. Przetwarzanie")
    ui_info("Unikalnych obiektów OSM: %d" % len(elements))
    dense = densify_route(route, 10.0)
    idx = RouteIndex(dense)
    lat0 = route[0][0]
    limit = a.radius + 40.0
    ui_ok("Indeks trasy gotowy (%d punktów pomocniczych)" % len(dense))

    def near(p):
        return idx.dist(p, limit) <= limit

    result = {"radius_m": a.radius, "source": "© współtwórcy OpenStreetMap (ODbL)",
              "road_major": [], "road_minor": [], "path": [], "river": [], "water": [], "green": [], "building": [], "names": []}
    seen_names = set()

    def round_pts(pts, eps=0.0):
        if eps > 0:
            pts = rdp(pts, eps, lat0)
        return [[round(p[0], 6), round(p[1], 6)] for p in pts]

    def add_line(cls, pts, name=None):
        # tnie na ciągi wierzchołków w pobliżu trasy
        run = []
        for p in pts:
            if near(p):
                run.append(p)
            else:
                if len(run) >= 2:
                    result[cls].append(round_pts(run, 2.0))
                run = []
        if len(run) >= 2:
            result[cls].append(round_pts(run, 2.0))
        if name and cls in ("road_major", "road_minor", "path"):
            # etykieta: środek najdłuższego odcinka w pobliżu trasy
            best, bl = None, 0
            for j in range(1, len(pts)):
                if near(pts[j - 1]) and near(pts[j]):
                    l = dist_m(pts[j - 1], pts[j])
                    if l > bl:
                        bl, best = l, (pts[j - 1], pts[j])
            if best and bl >= 15:
                key = (name, round(best[0][0], 3), round(best[0][1], 3))
                if key not in seen_names:
                    seen_names.add(key)
                    result["names"].append({"n": name, "lat": round((best[0][0] + best[1][0]) / 2, 6),
                                            "lng": round((best[0][1] + best[1][1]) / 2, 6),
                                            "a": [round(best[0][0], 6), round(best[0][1], 6), round(best[1][0], 6), round(best[1][1], 6)]})

    def add_ring(cls, pts):
        if not any(near(p) for p in pts):
            return
        # tereny zielone upraszczamy mocniej, budynki prawie wcale
        ring = simplify_ring(pts, {"green": 5.0, "water": 1.5, "building": 0.8}.get(cls, 0.0), lat0)
        if len(ring) >= 3:
            result[cls].append(round_pts(ring))

    total_el = len(elements)
    t_proc = time.time()
    last_ui = 0.0
    for n_el, el in enumerate(elements.values()):
        now = time.time()
        if now - last_ui > 0.2:
            last_ui = now
            live("klasyfikacja %s  %d/%d obiektów" % (bar(n_el / float(max(1, total_el))), n_el, total_el))
        tags = el.get("tags", {})
        if el["type"] == "way":
            pts = [(g["lat"], g["lon"]) for g in (el.get("geometry") or []) if g]   # Overpass bywa daje null dla brakujących punktów
            if len(pts) < 2:
                continue
            if "highway" in tags:
                add_line(road_class(tags), pts, tags.get("name"))
                continue
            k = area_class(tags)
            if k == "river":
                add_line("river", pts)
            elif k:
                if pts[0] == pts[-1] and len(pts) >= 4:
                    add_ring(k, pts)
        elif el["type"] == "relation":
            k = area_class(tags)
            if not k or k == "river":
                continue
            outer = []
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    seg = [(g["lat"], g["lon"]) for g in m["geometry"] if g]
                    if len(seg) >= 2:
                        outer.append(seg)
            for ring in join_rings(outer):
                add_ring(k, ring)
    live_clear()
    ui_ok("Sklasyfikowano %d obiektów w %s" % (total_el, fmt_time(time.time() - t_proc)))

    # ----- 5. zapis
    ui_head("5. Zapis")
    out_dir = os.path.dirname(os.path.abspath(a.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(a.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(a.output)
    ui_ok("Zapisano: " + a.output + "  (" + fmt_size(size) + ")")
    labels = [("road_major", "drogi główne"), ("road_minor", "drogi"), ("path", "ścieżki"), ("river", "rzeki"),
              ("water", "woda"), ("green", "zieleń"), ("building", "budynki"), ("names", "nazwy ulic")]
    for key, lab in labels:
        ui_info("%-14s %6d" % (lab, len(result[key])))
    print("")
    ui_box(["Gotowe w %s." % fmt_time(time.time() - t_all),
            "W panelu AE: Mapa trasy + Mini mapa 3D + Otoczenie z OSM,",
            "potem 'Dodaj klip' i wskaż ten plik.",
            "Dane © współtwórcy OpenStreetMap (ODbL)."])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        live_clear()
        print("")
        print("  [!]  PRZERWANO (Ctrl+C). Plik wynikowy nie został zmieniony.")
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
