#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_osm_fetch.py  —  pobiera z OpenStreetMap (Overpass API) otoczenie trasy
dla mini mapy 3D w panelu After Effects (AE/hrf_ae_panel.jsx).

Użycie (u siebie, potrzebny Python 3 i internet, bez dodatkowych bibliotek):
    py tools/hrf_osm_fetch.py data/aktywnosc_hrf.json data/hrf_osm.json
    py tools/hrf_osm_fetch.py data/aktywnosc_hrf.json data/hrf_osm.json --radius 150

Pobiera tylko: drogi i ścieżki (z nazwami), budynki, wodę (rzeki, jeziora),
tereny zielone (lasy, łąki, parki, pola – uproszczone). Wynik jest zapisywany
do pliku; ponowne uruchomienie dociąga tylko brakujące odcinki (cache obok pliku).
Dane © współtwórcy OpenStreetMap (ODbL) – przy publikacji podaj to źródło.

Klucze wynikowego JSON: radius_m, source, road_major, road_minor, path, river,
water, green, building, names.
"""
import json, math, os, sys, time, argparse, urllib.request, urllib.parse, urllib.error

SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
EARTH_R = 6371000.0


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


def build_query(chunk, r):
    poly = ",".join("%.6f,%.6f" % (p[0], p[1]) for p in chunk)
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


def fetch_overpass(query, tries=6):
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = None
    for attempt in range(tries):
        server = SERVERS[attempt % len(SERVERS)]
        try:
            req = urllib.request.Request(server, data=payload, headers={"User-Agent": "hrf-ae-overlay/1.0 (osobiste grafiki ze Stravy)"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = "HTTP %d" % e.code
            wait = 15 if e.code in (429, 504, 503) else 5
        except Exception as e:  # sieć, timeout, JSON
            last_error = str(e)
            wait = 8
        print("   próba %d nieudana (%s) – czekam %d s" % (attempt + 1, last_error, wait))
        time.sleep(wait)
    raise SystemExit("Nie udało się pobrać danych z Overpass: %s" % last_error)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("output")
    ap.add_argument("--radius", type=int, default=150, help="pas wokół trasy w metrach (domyślnie 150)")
    ap.add_argument("--chunk", type=float, default=3000.0, help="długość odcinka trasy na jedno zapytanie, m")
    a = ap.parse_args()

    with open(a.data, encoding="utf-8-sig") as f:
        D = json.load(f)
    S = D.get("samples") or D
    lat, lng = S["lat"], S["lng"]
    route = [(la, lo) for la, lo in zip(lat, lng)]
    # rzadsza trasa do zapytań
    sparse = [route[0]]
    for p in route[1:]:
        if dist_m(sparse[-1], p) >= 40:
            sparse.append(p)
    if sparse[-1] != route[-1]:
        sparse.append(route[-1])
    # odcinki
    chunks, cur, length = [], [sparse[0]], 0.0
    for p in sparse[1:]:
        length += dist_m(cur[-1], p)
        cur.append(p)
        if length >= a.chunk:
            chunks.append(cur)
            cur, length = [p], 0.0
    if len(cur) > 1:
        chunks.append(cur)
    print("Trasa: %d punktów, %d zapytań (pas %d m)" % (len(route), len(chunks), a.radius))

    cache_dir = a.output + ".cache"
    os.makedirs(cache_dir, exist_ok=True)
    elements = {}
    for i, ch in enumerate(chunks):
        cache_file = os.path.join(cache_dir, "k%03d_r%d.json" % (i, a.radius))
        if os.path.exists(cache_file):
            with open(cache_file, encoding="utf-8") as f:
                response = json.load(f)
            print("  odcinek %d/%d – z pamięci podręcznej" % (i + 1, len(chunks)))
        else:
            print("  odcinek %d/%d – pobieram..." % (i + 1, len(chunks)))
            response = fetch_overpass(build_query(ch, a.radius))
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(response, f)
            time.sleep(2)
        for el in response.get("elements", []):
            elements[(el["type"], el["id"])] = el

    dense = densify_route(route, 10.0)
    idx = RouteIndex(dense)
    lat0 = route[0][0]
    limit = a.radius + 40.0

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
        if cls == "green":
            pts = rdp(pts, 5.0, lat0)  # tereny zielone upraszczamy
        elif cls == "water":
            pts = rdp(pts, 1.5, lat0)
        if len(pts) >= 3:
            result[cls].append(round_pts(pts, 0.8 if cls == 'building' else 0.0))

    for el in elements.values():
        tags = el.get("tags", {})
        if el["type"] == "way":
            pts = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
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
                    outer.append([(g["lat"], g["lon"]) for g in m["geometry"]])
            for ring in join_rings(outer):
                add_ring(k, ring)

    with open(a.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print("Zapisano %s: %s" % (a.output, ", ".join("%s=%d" % (k, len(v)) for k, v in result.items() if isinstance(v, list))))
    print("Rozmiar: %.1f MB" % (os.path.getsize(a.output) / 1e6))


if __name__ == "__main__":
    main()
