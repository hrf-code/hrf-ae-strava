#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_osm_pobierz.py  —  pobiera z OpenStreetMap (Overpass API) otoczenie trasy
dla mini mapy 3D w skrypcie After Effects (hrf_ae_naklad.jsx).

Użycie (u siebie, potrzebny Python 3 i internet, bez dodatkowych bibliotek):
    python3 hrf_osm_pobierz.py hrf_ae_dane_v2.json hrf_osm.json
    python3 hrf_osm_pobierz.py hrf_ae_dane_v2.json hrf_osm.json --promien 150

Pobiera tylko: drogi i ścieżki (z nazwami), budynki, wodę (rzeki, jeziora),
tereny zielone (lasy, łąki, parki, pola – uproszczone). Wynik jest zapisywany
do pliku; ponowne uruchomienie dociąga tylko brakujące odcinki (cache obok pliku).
Dane © współtwórcy OpenStreetMap (ODbL) – przy publikacji podaj to źródło.
"""
import json, math, os, sys, time, argparse, urllib.request, urllib.parse, urllib.error

SERWERY = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
R_ZIEMI = 6371000.0


def dist_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_ZIEMI * math.asin(math.sqrt(h))


def zageszczona_trasa(pts, krok=10.0):
    """Trasa co ok. krok metrów (do liczenia odległości od trasy)."""
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        d = dist_m(a, b)
        n = int(d // krok)
        for k in range(1, n + 1):
            f = k / (n + 1.0)
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        out.append(b)
    return out


class Indeks:
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


def zapytanie(kawalek, r):
    poly = ",".join("%.6f,%.6f" % (p[0], p[1]) for p in kawalek)
    ar = "(around:%d,%s)" % (r, poly)
    return (
        "[out:json][timeout:120];("
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


def pobierz(zap, proby=6):
    dane = urllib.parse.urlencode({"data": zap}).encode("utf-8")
    ostatni = None
    for proba in range(proby):
        serwer = SERWERY[proba % len(SERWERY)]
        try:
            req = urllib.request.Request(serwer, data=dane, headers={"User-Agent": "hrf-ae-naklad/1.0 (osobiste grafiki ze Stravy)"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            ostatni = "HTTP %d" % e.code
            czekaj = 15 if e.code in (429, 504, 503) else 5
        except Exception as e:  # sieć, timeout, JSON
            ostatni = str(e)
            czekaj = 8
        print("   próba %d nieudana (%s) – czekam %d s" % (proba + 1, ostatni, czekaj))
        time.sleep(czekaj)
    raise SystemExit("Nie udało się pobrać danych z Overpass: %s" % ostatni)


def klasa_drogi(tags):
    h = tags.get("highway", "")
    if h in ("motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link", "primary_link", "secondary_link"):
        return "droga1"
    if h in ("tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "road"):
        return "droga2"
    return "sciezka"


def klasa_obszaru(tags):
    if "building" in tags:
        return "budynek"
    if tags.get("natural") == "water" or tags.get("water") or tags.get("landuse") == "reservoir":
        return "woda"
    if "waterway" in tags:
        return "rzeka"
    if tags.get("natural") in ("wood", "scrub", "grassland", "wetland") or tags.get("landuse") or tags.get("leisure"):
        return "zielen"
    return None


def pierscienie(czlonkowie):
    """Skleja segmenty 'outer' relacji w zamknięte pierścienie."""
    segs = [list(m) for m in czlonkowie if len(m) >= 2]
    ringi = []
    while segs:
        cur = segs.pop()
        zmiana = True
        while zmiana and cur[0] != cur[-1]:
            zmiana = False
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
                zmiana = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            ringi.append(cur)
    return ringi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dane")
    ap.add_argument("wyjscie")
    ap.add_argument("--promien", type=int, default=150, help="pas wokół trasy w metrach (domyślnie 150)")
    ap.add_argument("--kawalek", type=float, default=3000.0, help="długość odcinka trasy na jedno zapytanie, m")
    a = ap.parse_args()

    with open(a.dane, encoding="utf-8-sig") as f:
        D = json.load(f)
    S = D.get("samples") or D
    lat, lng = S["lat"], S["lng"]
    trasa = [(la, lo) for la, lo in zip(lat, lng)]
    # rzadsza trasa do zapytań
    rzadka = [trasa[0]]
    for p in trasa[1:]:
        if dist_m(rzadka[-1], p) >= 40:
            rzadka.append(p)
    if rzadka[-1] != trasa[-1]:
        rzadka.append(trasa[-1])
    # odcinki
    kawalki, cur, dl = [], [rzadka[0]], 0.0
    for p in rzadka[1:]:
        dl += dist_m(cur[-1], p)
        cur.append(p)
        if dl >= a.kawalek:
            kawalki.append(cur)
            cur, dl = [p], 0.0
    if len(cur) > 1:
        kawalki.append(cur)
    print("Trasa: %d punktów, %d zapytań (pas %d m)" % (len(trasa), len(kawalki), a.promien))

    cache_dir = a.wyjscie + ".cache"
    os.makedirs(cache_dir, exist_ok=True)
    elementy = {}
    for i, kw in enumerate(kawalki):
        plik = os.path.join(cache_dir, "k%03d_r%d.json" % (i, a.promien))
        if os.path.exists(plik):
            with open(plik, encoding="utf-8") as f:
                dane = json.load(f)
            print("  odcinek %d/%d – z pamięci podręcznej" % (i + 1, len(kawalki)))
        else:
            print("  odcinek %d/%d – pobieram..." % (i + 1, len(kawalki)))
            dane = pobierz(zapytanie(kw, a.promien))
            with open(plik, "w", encoding="utf-8") as f:
                json.dump(dane, f)
            time.sleep(2)
        for el in dane.get("elements", []):
            elementy[(el["type"], el["id"])] = el

    gesta = zageszczona_trasa(trasa, 10.0)
    idx = Indeks(gesta)
    lat0 = trasa[0][0]
    limit = a.promien + 40.0

    def blisko(p):
        return idx.dist(p, limit) <= limit

    wynik = {"promien_m": a.promien, "zrodlo": "© współtwórcy OpenStreetMap (ODbL)",
             "droga1": [], "droga2": [], "sciezka": [], "rzeka": [], "woda": [], "zielen": [], "budynek": [], "nazwy": []}
    nazwy_widziane = set()

    def zaokr(pts, eps=0.0):
        if eps > 0:
            pts = rdp(pts, eps, lat0)
        return [[round(p[0], 6), round(p[1], 6)] for p in pts]

    def dodaj_linie(klasa, pts, nazwa=None):
        # tnie na ciągi wierzchołków w pobliżu trasy
        ciag = []
        for p in pts:
            if blisko(p):
                ciag.append(p)
            else:
                if len(ciag) >= 2:
                    wynik[klasa].append(zaokr(ciag, 2.0))
                ciag = []
        if len(ciag) >= 2:
            wynik[klasa].append(zaokr(ciag, 2.0))
        if nazwa and klasa in ("droga1", "droga2", "sciezka"):
            # etykieta: środek najdłuższego odcinka w pobliżu trasy
            best, bl = None, 0
            for j in range(1, len(pts)):
                if blisko(pts[j - 1]) and blisko(pts[j]):
                    l = dist_m(pts[j - 1], pts[j])
                    if l > bl:
                        bl, best = l, (pts[j - 1], pts[j])
            if best and bl >= 15:
                key = (nazwa, round(best[0][0], 3), round(best[0][1], 3))
                if key not in nazwy_widziane:
                    nazwy_widziane.add(key)
                    wynik["nazwy"].append({"n": nazwa, "lat": round((best[0][0] + best[1][0]) / 2, 6),
                                           "lng": round((best[0][1] + best[1][1]) / 2, 6),
                                           "a": [round(best[0][0], 6), round(best[0][1], 6), round(best[1][0], 6), round(best[1][1], 6)]})

    def dodaj_pierscien(klasa, pts):
        if not any(blisko(p) for p in pts):
            return
        if klasa == "zielen":
            pts = rdp(pts, 5.0, lat0)  # tereny zielone upraszczamy
        elif klasa == "woda":
            pts = rdp(pts, 1.5, lat0)
        if len(pts) >= 3:
            wynik[klasa].append(zaokr(pts, 0.8 if klasa == 'budynek' else 0.0))

    for el in elementy.values():
        tags = el.get("tags", {})
        if el["type"] == "way":
            pts = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
            if len(pts) < 2:
                continue
            if "highway" in tags:
                dodaj_linie(klasa_drogi(tags), pts, tags.get("name"))
                continue
            k = klasa_obszaru(tags)
            if k == "rzeka":
                dodaj_linie("rzeka", pts)
            elif k:
                if pts[0] == pts[-1] and len(pts) >= 4:
                    dodaj_pierscien(k, pts)
        elif el["type"] == "relation":
            k = klasa_obszaru(tags)
            if not k or k == "rzeka":
                continue
            outer = []
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    outer.append([(g["lat"], g["lon"]) for g in m["geometry"]])
            for ring in pierscienie(outer):
                dodaj_pierscien(k, ring)

    with open(a.wyjscie, "w", encoding="utf-8") as f:
        json.dump(wynik, f, ensure_ascii=False, separators=(",", ":"))
    print("Zapisano %s: %s" % (a.wyjscie, ", ".join("%s=%d" % (k, len(v)) for k, v in wynik.items() if isinstance(v, list))))
    print("Rozmiar: %.1f MB" % (os.path.getsize(a.wyjscie) / 1e6))


if __name__ == "__main__":
    main()
