# -*- coding: utf-8 -*-
"""
hrf_blender_route.py  —  buduje w Blenderze scenę z trasy jazdy: ścieżka z prawdziwą wysokością (oś Z),
teren z wysokości, strzałka jadąca po trasie i kamera (orbitująca wokół całej trasy albo jadąca za strzałką).
Dane z tego samego pliku *_hrf.json co panel AE. Po uruchomieniu skrypt dodaje do okna 3D panel sterowania
(klawisz N -> zakładka "HRF"), którym zmieniasz parametry bez edytowania kodu.

Uruchomienie w Blenderze:  zakładka Scripting -> Open -> ten plik -> Run Script.
Scena powstaje w kolekcji "HRF" (kolejne uruchomienie odtwarza ją od zera). Wartości z CONFIG poniżej są
punktem startowym; później zmieniasz je w panelu (wartości zapisują się w scenie .blend).
Żeby panel wracał po ponownym otwarciu pliku .blend: w edytorze tekstu zaznacz "Register" przy tym skrypcie.

Co robi panel:
  - "Przebuduj scenę"  - całość od zera (potrzebne po zmianie przewyższenia, terenu, pliku danych),
  - "Zastosuj czas i kamerę" - przelicza samą animację (szybko: kamera, strzałka, przejechany odcinek),
  - kolory, grubość linii, rozmiar strzałki, światło - zmieniają się na żywo,
  - przyciski renderu klatki i animacji.

Z wiersza poleceń (test bez okna):
    blender -b --python Blender/hrf_blender_route.py -- --exag 1.5 --render-frame 100 --render-out test.png --dark

Dane z GPS są prywatne: plik sceny (.blend) z trasą nie powinien trafiać do publicznego repo.
"""
import bpy
import json, math, os, sys, glob, zlib, base64, array, traceback, time, re, subprocess
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from mathutils import Vector, Matrix

CONFIG = {
    "DATA_PATH": "",          # plik *_hrf.json; puste = najnowszy *_hrf.json w folderze data/ obok repo
    "MODE": "full",           # "full" = okno w FULL_SECONDS s; "speed" = okno z przyspieszeniem SPEED x; "clip" = okno w czasie rzeczywistym (1:1)
    "START_MIN": 0.0,         # początek klipu w minutach liczonych od startu całej aktywności
    "END_MIN": 0.0,           # koniec klipu w minutach od startu aktywności (0 = do końca jazdy)
    "SPEED": 10.0,            # speed: przyspieszenie (1 = czas rzeczywisty 1:1, 10 = 10x szybciej)
    "FULL_SECONDS": 40.0,     # full: długość przelotu w sekundach (klatki = FULL_SECONDS * FPS)
    "FULL_SCALE": 50.0,       # full + chase: mnożnik grubości linii, strzałki i odległości kamery
    "FPS": 30,
    "RES_X": 1920,            # kadr 16:9 (poziomy); dla pionowego ustaw RES_X 1080, RES_Y 1920
    "RES_Y": 1080,
    "EXAG": 1.5,              # przewyższenie (1 = skala rzeczywista, 1.5 = czytelne i naturalne, 3 = mocno przesadzone)
    "POINT_MIN_M": 4.0,       # minimalny odstęp punktów krzywej trasy, m
    "SMOOTH_XY_S": 4.0,       # wygładzanie pozycji (połowa okna, s)
    "SMOOTH_Z_S": 12.0,       # wygładzanie wysokości z GPS (połowa okna, s)
    "CAM_MODE": "orbit",      # "orbit" = cała trasa w kadrze, kamera krąży wokół; "chase" = kamera za strzałką
    "TUBE_RADIUS_M": 1.2,     # chase: promień rury trasy, m
    "DONE_SCALE": 1.5,        # o ile grubsza jest przejechana część
    "ARROW_LEN_M": 9.0,       # chase: długość strzałki, m
    "CAM_BACK_M": 60.0,       # chase: kamera za strzałką, m
    "CAM_UP_M": 45.0,         # chase: kamera nad strzałką, m
    "CAM_LOOK_AHEAD_M": 20.0, # chase: kamera patrzy na punkt przed strzałką, m
    "CAM_LENS_MM": 30.0,
    "ORBIT_TARGET": "null",   # orbit: kamera krąży wokół "null" (obiekt HRF_Pivot, który możesz przesuwać) albo wokół "arrow" (strzałki)
    "ORBIT_DISTANCE_M": 0.0,  # orbit: odległość kamery od celu w metrach; 0 = automatycznie (cała trasa w kadrze; dla strzałki bliżej)
    "ORBIT_TURNS": 1.0,       # orbit: ile pełnych okrążeń w czasie animacji
    "ORBIT_ELEV_DEG": 40.0,   # orbit: wysokość kamery nad horyzontem (stopnie)
    "ORBIT_START_DEG": -90.0, # orbit: początkowy azymut kamery (stopnie, -90 = od południa)
    "ORBIT_MARGIN": 0.9,      # orbit: zapas wokół trasy w kadrze (mniej = bliżej; poniżej ok. 0.8 trasa wychodzi poza kadr)
    "ORBIT_TUBE_PX": 3.0,     # orbit: promień linii trasy w pikselach
    "ORBIT_ARROW_PX": 70.0,   # orbit: długość strzałki w pikselach
    "CAM_POS_TAU_S": 0.6,     # chase: bezwładność pozycji kamery (s)
    "CAM_YAW_TAU_S": 1.8,     # chase: bezwładność obrotu kamery (s)
    "TERRAIN": "auto",        # "auto" = data/hrf_terrain.json obok danych trasy, "off" = bez terenu, albo pełna ścieżka pliku
    "TERRAIN_SKIP": 1,        # użyj co N-tego oczka siatki terenu (1 = wszystkie; większe = lżejsza siatka)
    "TERRAIN_COLOR": (0.17, 0.19, 0.22),
    "TERRAIN_STYLE": "gray",  # "gray" = gładki, oświetlony; "contours" = warstwice z tekstury PNG (czarne tło, białe linie, bez oświetlenia)
    "TERRAIN_TEXTURE": "",    # PNG warstwic z tools/hrf_contours.py; puste = hrf_contours.png obok pliku terenu
    "TERRAIN_DRAPE": True,    # trasa przyklejona do terenu (wysokość z mapy terenu zamiast z GPS)
    "TERRAIN_SMOOTH_S": 2.0,  # wygładzanie wysokości trasy na terenie (połowa okna, s)
    "ROUTE_COLOR": (0.35, 0.65, 1.0),
    "DONE_COLOR": (1.0, 1.0, 1.0),
    "ARROW_COLOR": (1.0, 0.38, 0.05),
    "SUN_AZIMUTH_DEG": 135.0, # kierunek, z którego świeci słońce (stopnie od osi X; 135 = północny zachód)
    "SUN_ELEV_DEG": 40.0,
    "SUN_STRENGTH": 3.0,
    "OWN_WORLD": True,        # True = nie zmieniaj środowiska (World) sceny, zostaje Twoje (np. domyślne z Blendera); False = skrypt ustawia własne
    "OUTPUT_DIR": "//hrf_render/",
}

EARTH_R = 6371008.8
STATE = {"raw": None, "raw_key": None, "T": None, "objs": {}, "base_pts": None, "busy": False}


# ---------------------------------------------------------------- dane
def script_dirs():
    """Foldery, w których leży ten skrypt (w edytorze tekstu Blendera __file__ bywa nieprawdziwe)."""
    cands = []
    try:
        t = bpy.context.space_data.text
        if t and t.filepath:
            cands.append(bpy.path.abspath(t.filepath))
    except Exception:
        pass
    for t in bpy.data.texts:
        if t.filepath and os.path.basename(t.filepath).startswith("hrf_blender_route"):
            cands.append(bpy.path.abspath(t.filepath))
    if "__file__" in globals():
        cands.append(os.path.abspath(__file__))
    out = []
    for c in cands:
        d = os.path.dirname(os.path.abspath(c))
        if d not in out:
            out.append(d)
    return out


def find_data(path):
    """Plik danych aktywności: z pola/CONFIG, ze zmiennej środowiskowej HRF_DATA (ustawia ją program HRF.pyw)
    albo najnowszy *_hrf.json w data/<projekt>/ (lub bezpośrednio w data/)."""
    path = path or os.environ.get("HRF_DATA", "")
    if path:
        path = bpy.path.abspath(path)
        if not os.path.isfile(path):
            raise RuntimeError("Nie ma pliku danych: " + path)
        return path
    searched = []
    for d in script_dirs() + [os.path.dirname(bpy.path.abspath(bpy.data.filepath)) if bpy.data.filepath else ""]:
        if not d:
            continue
        for sub in (os.path.join(d, "..", "data"), os.path.join(d, "data"), d):
            sub = os.path.normpath(sub)
            searched.append(sub)
            cands = glob.glob(os.path.join(sub, "*_hrf.json")) + glob.glob(os.path.join(sub, "*", "*_hrf.json"))
            if cands:
                return max(cands, key=os.path.getmtime)
    raise RuntimeError("Nie znaleziono pliku *_hrf.json (szukano w: %s). Wskaż plik w panelu (Plik danych) albo w CONFIG['DATA_PATH']."
                       % (", ".join(searched) or "brak folderów"))


def find_terrain(cfg, data_path):
    t = cfg["TERRAIN"]
    if not t or t == "off":
        return None
    if t != "auto":
        t = bpy.path.abspath(t)
        if not os.path.isfile(t):
            raise RuntimeError("Nie ma pliku terenu: " + t)
        return t
    cand = os.path.join(os.path.dirname(os.path.abspath(data_path)), "hrf_terrain.json")
    return cand if os.path.isfile(cand) else None


def load_terrain(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    g = array.array("f")
    g.frombytes(zlib.decompress(base64.b64decode(d["z_b64"])))
    if len(g) != d["rows"] * d["cols"]:
        raise RuntimeError("Plik terenu jest uszkodzony (zły rozmiar siatki).")
    d["grid"] = g
    del d["z_b64"]
    return d


def terrain_at(tr, lat, lng):
    """Wysokość terenu (m n.p.m.) w punkcie, interpolacja dwuliniowa; poza siatką = najbliższy brzeg."""
    R, C = tr["rows"], tr["cols"]
    fr = (lat - tr["lat_s"]) / (tr["lat_n"] - tr["lat_s"]) * (R - 1)
    fc = (lng - tr["lng_w"]) / (tr["lng_e"] - tr["lng_w"]) * (C - 1)
    fr = 0.0 if fr < 0 else (R - 1.0 if fr > R - 1 else fr)
    fc = 0.0 if fc < 0 else (C - 1.0 if fc > C - 1 else fc)
    r0, c0 = int(fr), int(fc)
    r1, c1 = min(r0 + 1, R - 1), min(c0 + 1, C - 1)
    wr, wc = fr - r0, fc - c0
    g = tr["grid"]
    a = g[r0 * C + c0] * (1 - wc) + g[r0 * C + c1] * wc
    b = g[r1 * C + c0] * (1 - wc) + g[r1 * C + c1] * wc
    return a * (1 - wr) + b * wr


def interp_series(ts, vs, n):
    """Próbki nieregularne -> siatka co 1 s (indeks = sekunda aktywności)."""
    out = [0.0] * n
    j, m = 0, len(ts)
    for k in range(n):
        t = float(k)
        while j + 1 < m and ts[j + 1] <= t:
            j += 1
        if t <= ts[0]:
            out[k] = vs[0]
        elif j + 1 >= m:
            out[k] = vs[-1]
        else:
            t0, t1 = ts[j], ts[j + 1]
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            out[k] = vs[j] + (vs[j + 1] - vs[j]) * f
    return out


def smooth(v, half):
    h = int(round(half))
    if h <= 0:
        return list(v)
    pre = [0.0]
    for x in v:
        pre.append(pre[-1] + x)
    n = len(v)
    out = []
    for i in range(n):
        a, b = max(0, i - h), min(n - 1, i + h)
        out.append((pre[b + 1] - pre[a]) / (b - a + 1))
    return out


def at(arr, t):
    if t <= 0:
        return arr[0]
    if t >= len(arr) - 1:
        return arr[-1]
    i = int(t)
    f = t - i
    return arr[i] + (arr[i + 1] - arr[i]) * f


def load_raw(cfg):
    """Dane z plików (trasa + teren), z pamięcią podręczną: ponowne budowanie sceny nie czyta ich od nowa."""
    path = find_data(cfg["DATA_PATH"])
    tpath = find_terrain(cfg, path)
    key = (path, os.path.getmtime(path), tpath, os.path.getmtime(tpath) if tpath else 0)
    if STATE["raw"] is not None and STATE["raw_key"] == key:
        return STATE["raw"]
    with open(path, encoding="utf-8-sig") as f:
        D = json.load(f)
    S = D["samples"]
    raw = {"path": path, "name": D.get("name", ""), "ts": S["time_s"], "lat": S["lat"], "lng": S["lng"],
           "elev": S["elev_m"], "terrain": load_terrain(tpath) if tpath else None, "terrain_path": tpath}
    STATE["raw"], STATE["raw_key"] = raw, key
    return raw


def build_track(cfg, raw):
    ts, lat, lng, elev = raw["ts"], raw["lat"], raw["lng"], raw["elev"]
    n = int(ts[-1]) + 1
    lat0 = (min(lat) + max(lat)) / 2.0
    lng0 = (min(lng) + max(lng)) / 2.0
    kx = math.radians(1.0) * EARTH_R * math.cos(math.radians(lat0))
    ky = math.radians(1.0) * EARTH_R
    xs = interp_series(ts, [(v - lng0) * kx for v in lng], n)
    ys = interp_series(ts, [(v - lat0) * ky for v in lat], n)
    xs, ys = smooth(xs, cfg["SMOOTH_XY_S"]), smooth(ys, cfg["SMOOTH_XY_S"])
    terr = raw["terrain"]
    drape = bool(terr and cfg["TERRAIN_DRAPE"])
    if drape:
        # wysokość trasy z mapy terenu (zamiast barometru GPS), żeby trasa leżała na powierzchni terenu
        zs = [terrain_at(terr, lat0 + ys[i] / ky, lng0 + xs[i] / kx) for i in range(n)]
    else:
        zs = interp_series(ts, elev, n)
    zs = smooth(zs, cfg["TERRAIN_SMOOTH_S"] if drape else cfg["SMOOTH_Z_S"])
    zmin = min(zs)
    zs = [(v - zmin) * cfg["EXAG"] for v in zs]

    # kierunek: yaw (w płaszczyźnie XY) i pitch (nachylenie) z okna +/- 6 s; w postoju zostaje poprzedni
    phi, theta = [0.0] * n, [0.0] * n
    last = (0.0, 0.0)
    H = 6
    for i in range(n):
        a, b = max(0, i - H), min(n - 1, i + H)
        dx, dy, dz = xs[b] - xs[a], ys[b] - ys[a], zs[b] - zs[a]
        hd = math.hypot(dx, dy)
        if hd > 1.0:
            last = (math.atan2(dy, dx), math.atan2(dz, hd))
        phi[i], theta[i] = last
    for i in range(1, n):   # odwinięcie kąta (ciągłość)
        while phi[i] - phi[i - 1] > math.pi:
            phi[i] -= 2 * math.pi
        while phi[i] - phi[i - 1] < -math.pi:
            phi[i] += 2 * math.pi
    phi_arrow, theta_arrow = smooth(phi, 3), smooth(theta, 5)
    phi_cam = smooth(phi, 8)

    # punkty krzywej: co najmniej POINT_MIN_M od poprzedniego; postęp wzdłuż krzywej dla każdej sekundy
    kept = [0]
    cum = [0.0]
    for i in range(1, n):
        p = kept[-1]
        d = math.sqrt((xs[i] - xs[p]) ** 2 + (ys[i] - ys[p]) ** 2 + (zs[i] - zs[p]) ** 2)
        if d >= cfg["POINT_MIN_M"]:
            kept.append(i)
            cum.append(cum[-1] + d)
    if kept[-1] != n - 1:
        p = kept[-1]
        d = math.sqrt((xs[-1] - xs[p]) ** 2 + (ys[-1] - ys[p]) ** 2 + (zs[-1] - zs[p]) ** 2)
        kept.append(n - 1)
        cum.append(cum[-1] + d)
    total = cum[-1] or 1.0
    prog = [0.0] * n
    k = 0
    for i in range(n):
        while k + 1 < len(kept) - 1 and kept[k + 1] <= i:
            k += 1
        i0, i1 = kept[k], kept[k + 1] if k + 1 < len(kept) else kept[k]
        f = (i - i0) / float(i1 - i0) if i1 > i0 else 0.0
        prog[i] = (cum[k] + (cum[min(k + 1, len(cum) - 1)] - cum[k]) * max(0.0, min(1.0, f))) / total
    pts = [(xs[i], ys[i], zs[i]) for i in kept]
    ext = {"cx": (min(xs) + max(xs)) / 2.0, "cy": (min(ys) + max(ys)) / 2.0, "cz": max(zs) * 0.35,
           "w": max(xs) - min(xs), "h": max(ys) - min(ys), "zr": max(zs) - min(zs)}
    return {"path": raw["path"], "name": raw["name"], "n": n, "x": xs, "y": ys, "z": zs, "ext": ext,
            "phi_arrow": phi_arrow, "theta_arrow": theta_arrow, "phi_cam": phi_cam,
            "pts": pts, "prog": prog, "length_m": total, "zmax": max(zs),
            "terrain": terr, "terrain_path": raw["terrain_path"], "zmin_ref": zmin, "proj": (lat0, lng0, kx, ky)}


# ---------------------------------------------------------------- parametry z panelu
# CONFIG <-> właściwości panelu (scene.hrf)
PROP_MAP = [
    ("MODE", "mode"), ("SPEED", "speed"), ("FULL_SECONDS", "full_seconds"),
    ("FPS", "fps"), ("RES_X", "res_x"), ("RES_Y", "res_y"), ("EXAG", "exag"), ("CAM_MODE", "cam_mode"),
    ("ORBIT_ELEV_DEG", "orbit_elev"), ("ORBIT_MARGIN", "orbit_margin"), ("ORBIT_TURNS", "orbit_turns"),
    ("ORBIT_START_DEG", "orbit_start"), ("ORBIT_TARGET", "orbit_target"), ("CAM_LENS_MM", "lens"), ("CAM_BACK_M", "chase_back"),
    ("CAM_UP_M", "chase_up"), ("CAM_LOOK_AHEAD_M", "chase_ahead"), ("TERRAIN_DRAPE", "drape"),
    ("TERRAIN_SKIP", "terrain_skip"), ("TERRAIN_COLOR", "terrain_color"), ("TERRAIN_STYLE", "terrain_style"),
    ("TERRAIN_TEXTURE", "terrain_texture"), ("ROUTE_COLOR", "route_color"),
    ("DONE_COLOR", "done_color"), ("ARROW_COLOR", "arrow_color"), ("SUN_AZIMUTH_DEG", "sun_azimuth"),
    ("SUN_ELEV_DEG", "sun_elev"), ("SUN_STRENGTH", "sun_strength"), ("DONE_SCALE", "done_scale"),
    ("OWN_WORLD", "own_world"),
]
DEFAULT_OUT = "//hrf_render/"


def props_from_config(scene, overrides=None):
    cfg = dict(CONFIG)
    if overrides:
        cfg.update(overrides)
    p = scene.hrf
    for ckey, pname in PROP_MAP:
        try:
            setattr(p, pname, cfg[ckey])
        except Exception:
            pass
    ov = overrides or {}
    start_min, end_min = cfg["START_MIN"], cfg["END_MIN"]
    if "START_S" in ov:                       # stare klucze w sekundach (flagi --start / --duration)
        start_min = ov["START_S"] / 60.0
    if "DURATION_S" in ov:
        end_min = start_min + ov["DURATION_S"] / 60.0
    p.start_min, p.end_min = start_min, end_min
    p.orbit_distance_manual = cfg["ORBIT_DISTANCE_M"] > 0
    if cfg["ORBIT_DISTANCE_M"] > 0:
        p.orbit_distance = cfg["ORBIT_DISTANCE_M"]
    p.data_path = cfg["DATA_PATH"]
    p.output_dir = "" if cfg["OUTPUT_DIR"] == DEFAULT_OUT else cfg["OUTPUT_DIR"]
    p.use_terrain = cfg["TERRAIN"] != "off"
    p.terrain_path = "" if cfg["TERRAIN"] in ("auto", "off") else cfg["TERRAIN"]
    p.line_scale, p.arrow_scale = 1.0, 1.0


def cfg_from_props(scene):
    cfg = dict(CONFIG)
    p = scene.hrf
    for ckey, pname in PROP_MAP:
        v = getattr(p, pname)
        cfg[ckey] = tuple(v) if ckey.endswith("_COLOR") else v
    cfg["START_MIN"], cfg["END_MIN"] = p.start_min, p.end_min
    cfg["ORBIT_DISTANCE_M"] = p.orbit_distance if p.orbit_distance_manual else 0.0     # 0 = automatycznie
    cfg["DATA_PATH"] = p.data_path
    cfg["OUTPUT_DIR"] = p.output_dir or DEFAULT_OUT
    cfg["TERRAIN"] = ("auto" if not p.terrain_path else p.terrain_path) if p.use_terrain else "off"
    return cfg


def calc_sizes(cfg, T, p):
    """Odległość kamery (orbit), grubość linii i długość strzałki."""
    E = T["ext"]
    rx, ry = int(cfg["RES_X"]), int(cfg["RES_Y"])
    lens = cfg["CAM_LENS_MM"]
    orbit = cfg["CAM_MODE"] == "orbit"
    full = cfg["MODE"] == "full"
    if orbit:
        s = 1.0
    elif full:
        s = cfg["FULL_SCALE"]
    elif cfg["MODE"] == "speed":
        s = max(1.0, min(cfg["FULL_SCALE"], cfg["SPEED"] / 5.0))      # kamera za strzałką przy dużym przyspieszeniu stoi dalej
    else:
        s = 1.0
    px_per_rad = lens / 36.0 * max(rx, ry)                                     # sensor 36 mm na dłuższym boku kadru
    half_short = (36.0 * min(rx, ry) / float(max(rx, ry)) / 2.0) / lens       # tan(pół kąta widzenia) na krótszym boku
    out = {"s": s, "orbit": orbit, "orbit_dist": 0.0}
    if orbit:
        span = math.sqrt(E["w"] ** 2 + E["h"] ** 2 + E["zr"] ** 2) / 2.0
        fit = span / half_short * cfg["ORBIT_MARGIN"]                 # cała trasa w kadrze
        if cfg["ORBIT_DISTANCE_M"] > 0:
            d = cfg["ORBIT_DISTANCE_M"]
        else:
            d = fit * 0.06 if cfg["ORBIT_TARGET"] == "arrow" else fit   # wokół strzałki domyślnie dużo bliżej
        d = max(d, 5.0)
        out["orbit_dist"] = d
        out["fit_dist"] = fit
        radius = cfg["ORBIT_TUBE_PX"] * d / px_per_rad
        arrow_len = cfg["ORBIT_ARROW_PX"] * d / px_per_rad
    else:
        radius = cfg["TUBE_RADIUS_M"] * s
        arrow_len = cfg["ARROW_LEN_M"] * s
    out["radius"] = radius * p.line_scale
    out["arrow_len"] = arrow_len * p.arrow_scale
    return out


MAX_FRAMES = 36000     # 20 min przy 30 fps; więcej to godziny zapisywania kluczy i gigantyczny render


def window_bounds(cfg, T):
    """Okno aktywności (sekundy od startu) z pól początek/koniec w minutach; koniec 0 = do końca jazdy."""
    end = T["n"] - 1.0
    t0 = min(max(0.0, cfg["START_MIN"] * 60.0), max(0.0, end - 1.0))
    t1 = cfg["END_MIN"] * 60.0 if cfg["END_MIN"] > 0 else end
    t1 = min(t1, end)
    if t1 - t0 < 1.0:
        raise RuntimeError("Koniec klipu musi być co najmniej sekundę po początku (cała aktywność trwa %.1f min)." % (end / 60.0))
    return t0, t1


def time_map(cfg, T):
    """(t0, speed, liczba klatek, fps): od której sekundy aktywności, ile sekund aktywności na sekundę animacji."""
    fps = int(cfg["FPS"])
    mode = cfg["MODE"]
    t0, t1 = window_bounds(cfg, T)
    win = t1 - t0
    if mode == "full":
        nsec = cfg["FULL_SECONDS"]
        speed = win / float(nsec)
    elif mode == "speed":
        speed = max(0.01, cfg["SPEED"])
        nsec = win / speed
    else:
        speed, nsec = 1.0, win
    nf = max(2, int(round(nsec * fps)))
    if nf > MAX_FRAMES:
        raise RuntimeError("Animacja miałaby %d klatek (limit %d, ok. %.0f min). Zmniejsz okno albo zwiększ przyspieszenie."
                           % (nf, MAX_FRAMES, MAX_FRAMES / float(fps) / 60.0))
    return t0, speed, nf, fps


def _hms(sec):
    sec = int(round(sec))
    return "%d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)


def time_summary(scene):
    """Jedna linia do panelu: ile klatek, jak długo i jaką część aktywności pokazuje animacja."""
    T = STATE.get("T")
    if not T:
        return ""
    try:
        t0, speed, nf, fps = time_map(cfg_from_props(scene), T)
    except RuntimeError as e:
        return str(e)
    t1 = min(T["n"] - 1.0, t0 + (nf - 1) / float(fps) * speed)
    return "%d klatek = %.1f s | %s× | %.1f–%.1f min z %.1f" % (nf, nf / float(fps), ("%.3g" % speed), t0 / 60.0, t1 / 60.0, (T["n"] - 1.0) / 60.0)


# ---------------------------------------------------------------- obiekty sceny
def clean_scene():
    col = bpy.data.collections.get("HRF")
    if col:
        for o in list(col.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.curves, bpy.data.meshes, bpy.data.cameras, bpy.data.materials, bpy.data.lights):
        for d in list(coll):
            if d.name.startswith("HRF_") and d.users == 0:
                coll.remove(d)
    if col is None:
        col = bpy.data.collections.new("HRF")
        bpy.context.scene.collection.children.link(col)
    elif col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    return col


def emission_material(name, rgb, strength=1.0):
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    em.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(em.outputs[0], out.inputs[0])
    mat.diffuse_color = (rgb[0], rgb[1], rgb[2], 1.0)
    return mat


def diffuse_material(name, rgb):
    mat = bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    bsdf.inputs["Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    mat.diffuse_color = (rgb[0], rgb[1], rgb[2], 1.0)
    return mat


def set_material_color(name, rgb):
    mat = bpy.data.materials.get(name)
    if not mat or not mat.node_tree:
        return
    for nd in mat.node_tree.nodes:
        if nd.type in ("EMISSION", "BSDF_DIFFUSE"):
            nd.inputs["Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    mat.diffuse_color = (rgb[0], rgb[1], rgb[2], 1.0)


def make_curve(name, pts, radius, mat, col):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = 2
    cu.use_fill_caps = True
    try:
        cu.bevel_factor_mapping_start = "SPLINE"
        cu.bevel_factor_mapping_end = "SPLINE"
    except Exception:
        pass
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for i, p in enumerate(pts):
        sp.points[i].co = (p[0], p[1], p[2], 1.0)
    cu.materials.append(mat)
    ob = bpy.data.objects.new(name, cu)
    col.objects.link(ob)
    return ob


ARROW_TOP = [(0, 0.5), (0.35, -0.5), (0, -0.2), (-0.35, -0.5)]


def arrow_verts(z0):
    """Strzałka o długości 1 (skalowana obiektem), leży na wysokości z0 (w jednostkach strzałki)."""
    h = 0.14
    return [(x, y, z0) for x, y in ARROW_TOP] + [(x, y, z0 + h) for x, y in ARROW_TOP]


def make_arrow(name, mat, col):
    faces = [(4, 5, 6), (4, 6, 7), (0, 2, 1), (0, 3, 2)]
    for i in range(4):
        j = (i + 1) % 4
        faces.append((i, j, 4 + j, 4 + i))
    me = bpy.data.meshes.new(name)
    me.from_pydata(arrow_verts(0.15), [], faces)
    me.update()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    col.objects.link(ob)
    return ob


def add_terrain_uv(me, ri, ci, R, C):
    """UV = położenie w siatce terenu (0..1, południe u dołu, północ u góry) – pasuje do PNG z hrf_contours.py."""
    uvl = me.uv_layers.new(name="HRF_UV")
    nc = len(ci)
    try:
        import numpy as np
        us = np.array([c / (C - 1.0) for c in ci], dtype=np.float32)
        vs = np.array([r / (R - 1.0) for r in ri], dtype=np.float32)
        vi = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", vi)
        uv = np.empty((len(vi), 2), dtype=np.float32)
        uv[:, 0] = us[vi % nc]
        uv[:, 1] = vs[vi // nc]
        uvl.data.foreach_set("uv", uv.ravel())
    except ImportError:
        for li, lp in enumerate(me.loops):
            vi = lp.vertex_index
            uvl.data[li].uv = (ci[vi % nc] / (C - 1.0), ri[vi // nc] / (R - 1.0))


def find_contour_texture(p, T):
    t = bpy.path.abspath(p.terrain_texture) if p.terrain_texture else ""
    if not t and T.get("terrain_path"):
        t = os.path.join(os.path.dirname(os.path.abspath(T["terrain_path"])), "hrf_contours.png")
    return t if t and os.path.isfile(t) else ""


def find_streets_texture(p, T):
    t = bpy.path.abspath(p.streets_texture) if p.streets_texture else ""
    if not t and T.get("terrain_path"):
        t = os.path.join(os.path.dirname(os.path.abspath(T["terrain_path"])), "hrf_streets.png")
    return t if t and os.path.isfile(t) else ""


def find_local_texture(p, T):
    """PNG lokalnej tekstury warstwic (hrf_local.png obok danych albo wskazany w panelu) i jego zasięg z .meta.json."""
    png = bpy.path.abspath(p.local_texture) if p.local_texture else ""
    if not png and T.get("path"):
        png = os.path.join(os.path.dirname(os.path.abspath(T["path"])), "hrf_local.png")
    if not png or not os.path.isfile(png):
        return None, None
    meta_path = os.path.splitext(png)[0] + ".meta.json"
    if not os.path.isfile(meta_path):
        return None, None
    try:
        with open(meta_path, encoding="utf-8") as f:
            return png, json.load(f)
    except Exception:
        return None, None


def _image_node(nt, path, extension):
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.reload()                                  # po ponownym wygenerowaniu/wyeksportowaniu pokaże się nowa wersja
    except Exception:
        pass
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    tex.extension = extension
    return tex


def apply_terrain_style(scene):
    """Materiał terenu: gładki szary albo warstwice z tekstury (czarne tło, białe linie, bez oświetlenia).
    Gdy istnieje lokalna tekstura wycinka (hrf_local.png), jest nakładana na globalną w obrębie wycinka."""
    o, T = STATE["objs"], STATE["T"]
    if not o.get("terrain") or not T:
        return
    p = scene.hrf
    mat = bpy.data.materials.get("HRF_terrain")
    if mat is None or not mat.node_tree:
        return
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    if p.terrain_style in ("contours", "streets"):
        path = find_contour_texture(p, T) if p.terrain_style == "contours" else find_streets_texture(p, T)
        if path:
            tc = nt.nodes.new("ShaderNodeTexCoord")
            tex_g = _image_node(nt, path, "EXTEND")
            nt.links.new(tc.outputs["UV"], tex_g.inputs["Vector"])
            color = tex_g.outputs["Color"]
            lpath, meta = find_local_texture(p, T) if (p.local_use and p.terrain_style == "contours") else (None, None)
            if lpath:
                tr = T["terrain"]
                wg, hg = tr["lng_e"] - tr["lng_w"], tr["lat_n"] - tr["lat_s"]
                wl, hl = meta["lng_e"] - meta["lng_w"], meta["lat_n"] - meta["lat_s"]
                mp = nt.nodes.new("ShaderNodeMapping")
                mp.vector_type = "POINT"
                mp.inputs["Location"].default_value = ((tr["lng_w"] - meta["lng_w"]) / wl, (tr["lat_s"] - meta["lat_s"]) / hl, 0.0)
                mp.inputs["Scale"].default_value = (wg / wl, hg / hl, 1.0)
                nt.links.new(tc.outputs["UV"], mp.inputs["Vector"])
                tex_l = _image_node(nt, lpath, "CLIP")
                nt.links.new(mp.outputs["Vector"], tex_l.inputs["Vector"])
                sep = nt.nodes.new("ShaderNodeSeparateXYZ")
                nt.links.new(mp.outputs["Vector"], sep.inputs["Vector"])

                def cmp(op, src, val):
                    n = nt.nodes.new("ShaderNodeMath")
                    n.operation = op
                    nt.links.new(src, n.inputs[0])
                    n.inputs[1].default_value = val
                    return n.outputs[0]

                def mul(a_, b_):
                    n = nt.nodes.new("ShaderNodeMath")
                    n.operation = "MULTIPLY"
                    nt.links.new(a_, n.inputs[0])
                    nt.links.new(b_, n.inputs[1])
                    return n.outputs[0]
                mask = mul(mul(cmp("GREATER_THAN", sep.outputs["X"], 0.0), cmp("LESS_THAN", sep.outputs["X"], 1.0)),
                           mul(cmp("GREATER_THAN", sep.outputs["Y"], 0.0), cmp("LESS_THAN", sep.outputs["Y"], 1.0)))
                mix = nt.nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                cols_in = [i for i in mix.inputs if i.type == "RGBA"]
                nt.links.new(mask, mix.inputs[0])
                nt.links.new(color, cols_in[0])
                nt.links.new(tex_l.outputs["Color"], cols_in[1])
                color = [i for i in mix.outputs if i.type == "RGBA"][0]
            em = nt.nodes.new("ShaderNodeEmission")
            nt.links.new(color, em.inputs["Color"])
            nt.links.new(em.outputs[0], out.inputs[0])
            mat.diffuse_color = (0.0, 0.0, 0.0, 1.0)
            return
        p.info = ("Brak PNG mapy ulic (uruchom tools/hrf_streetmap.py albo wskaż plik) – teren szary" if p.terrain_style == "streets"
                  else "Brak PNG warstwic (uruchom tools/hrf_contours.py albo wskaż plik) – teren szary")
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    c = p.terrain_color
    bsdf.inputs["Color"].default_value = (c[0], c[1], c[2], 1.0)
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    mat.diffuse_color = (c[0], c[1], c[2], 1.0)


# ---------------------------------------------------------------- tekstura lokalna (wycinek z okna czasu)
def region_bbox(scene):
    """Prostokąt (lat/lng) obejmujący trasę pokazaną w bieżącym oknie czasu plus margines (panel: Margines wycinka)."""
    T = STATE["T"]
    if not T:
        raise RuntimeError("Scena nie jest zbudowana – kliknij 'Przebuduj scenę'.")
    cfg = cfg_from_props(scene)
    p = scene.hrf
    t0, speed, nf, fps = time_map(cfg, T)
    xs, ys = [], []
    step = max(1, nf // 400)
    for fi in list(range(0, nf, step)) + [nf - 1]:
        t = max(0.0, min(T["n"] - 1.0, t0 + fi / float(fps) * speed))
        xs.append(at(T["x"], t))
        ys.append(at(T["y"], t))
    m = max(50.0, p.local_margin)
    lat0, lng0, kx, ky = T["proj"]
    lat_s, lat_n = lat0 + (min(ys) - m) / ky, lat0 + (max(ys) + m) / ky
    lng_w, lng_e = lng0 + (min(xs) - m) / kx, lng0 + (max(xs) + m) / kx
    tr = T["terrain"]
    if tr:                                   # tekstura ma sens tylko na terenie
        lat_s, lat_n = max(lat_s, tr["lat_s"]), min(lat_n, tr["lat_n"])
        lng_w, lng_e = max(lng_w, tr["lng_w"]), min(lng_e, tr["lng_e"])
    return lat_s, lat_n, lng_w, lng_e, (lng_e - lng_w) * kx, (lat_n - lat_s) * ky


def local_paths(T):
    here = script_dirs()[0] if script_dirs() else os.getcwd()
    tool = os.path.normpath(os.path.join(here, "..", "tools", "hrf_local_texture.py"))
    out = os.path.join(os.path.dirname(os.path.abspath(T["path"])), "hrf_local")
    return tool, out


def local_params(wm, hm):
    """Parametry generatora dobrane do rozmiaru wycinka: duży wycinek = mniej szczegółów, żeby nie ściągać setek kafelków."""
    size = max(wm, hm)
    zoom = 14 if size <= 8000 else (13 if size <= 20000 else 12)
    step = int(max(5, min(40, round(size / 700.0))))
    minor = 10 if size <= 8000 else (20 if size <= 20000 else 25)
    width = int(min(8192, max(2048, round(wm * 1.25 / 256.0) * 256)))      # ok. 0,8 m na piksel (do 8192 px)
    return {"zoom": zoom, "step": step, "minor": minor, "major": minor * 5, "width": width}


def local_command(scene, python=None, bbox=None, out=None):
    """Gotowa komenda generatora tekstury dla bieżącego wycinka (do wklejenia w terminalu).
    bbox (lat_s, lat_n, lng_w, lng_e, szerokość_m, wysokość_m) i out można podać jawnie (np. w testach)."""
    T = STATE["T"]
    lat_s, lat_n, lng_w, lng_e, wm, hm = bbox if bbox else region_bbox(scene)
    tool, out_default = local_paths(T)
    out = out or out_default
    prm = local_params(wm, hm)
    exe = python or ("py" if os.name == "nt" else "python3")
    quote = (lambda x: '"%s"' % x) if " " in exe else (lambda x: x)
    bb = "%.6f,%.6f,%.6f,%.6f" % (lat_s, lat_n, lng_w, lng_e)
    p = scene.hrf
    if not p.local_save_png and not p.local_save_vector:
        raise RuntimeError("Zaznacz co najmniej jedno: Zapisz PNG albo Zapisz PDF/SVG.")
    cmd = '%s "%s" --bbox %s --out "%s" --zoom %d --step %d --minor %d --major %d --png-width %d' % (
        quote(exe), tool, bb, out, prm["zoom"], prm["step"], prm["minor"], prm["major"], prm["width"])
    if not p.local_save_png:
        cmd += " --no-png"
    if p.local_save_vector:
        cmd += " --vector"
    cmd += " --yes"
    return cmd, (tool, out, bb, prm, wm, hm)


LOCAL = {"proc": None, "scene": None, "log": None, "prop": "local_status", "ok": ""}


def _backup_existing(out, exts):
    """Istniejące pliki, które zostaną nadpisane (np. PDF/SVG edytowany w Illustratorze), dostają kopię .bak ze znacznikiem czasu."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved = []
    for ext in exts:
        f = out + ext
        if os.path.isfile(f):
            bak = "%s%s.%s.bak" % (out, ext, stamp)
            try:
                os.replace(f, bak)
                saved.append(os.path.basename(bak))
            except OSError:
                pass
    return saved


def _local_timer():
    pr = LOCAL["proc"]
    if pr is None:
        return None
    sc = bpy.data.scenes.get(LOCAL["scene"] or "")
    prop = LOCAL["prop"]
    last = ""
    try:
        with open(LOCAL["log"], "rb") as f:
            f.seek(max(0, os.path.getsize(LOCAL["log"]) - 600))
            for ln in f.read().decode("utf-8", "replace").replace("\r", "\n").split("\n"):
                if ln.strip():
                    last = ln.strip()
    except Exception:
        pass
    if pr.poll() is None:
        if sc is not None:
            setattr(sc.hrf, prop, "Generuję... " + last[:70])
        return 1.0
    rc = pr.returncode
    LOCAL["proc"] = None
    if sc is not None:
        if rc == 0:
            setattr(sc.hrf, prop, LOCAL["ok"])
            try:
                apply_terrain_style(sc)
            except Exception:
                traceback.print_exc()
        else:
            setattr(sc.hrf, prop, "Błąd generatora (kod %s): %s" % (rc, last[:80]))
    return None


def _log_path(out):
    """Log generatora w folderze cache/ projektu (bez zaśmiecania folderu z wynikami)."""
    d = os.path.join(os.path.dirname(os.path.abspath(out)), "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, os.path.basename(out) + ".log")


def _start_job(scene, args, log, prop, ok_msg, start_msg):
    if LOCAL["proc"] is not None and LOCAL["proc"].poll() is None:
        raise RuntimeError("Generator już działa – poczekaj na koniec.")
    fh = open(log, "wb")
    LOCAL["proc"] = subprocess.Popen(args, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    LOCAL["scene"], LOCAL["log"], LOCAL["prop"], LOCAL["ok"] = scene.name, log, prop, ok_msg
    setattr(scene.hrf, prop, start_msg)
    bpy.app.timers.register(_local_timer, first_interval=1.0)


def _python_exe():
    return sys.executable if sys.executable and os.path.isfile(sys.executable) else ("py" if os.name == "nt" else "python3")


def start_local_generation(scene, bbox=None, out=None):
    cmd_text, (tool, out, bb, prm, wm, hm) = local_command(scene, bbox=bbox, out=out)
    if not os.path.isfile(tool):
        raise RuntimeError("Nie znaleziono narzędzia: " + tool)
    p = scene.hrf
    args = [_python_exe(), tool, "--bbox", bb, "--out", out, "--zoom", str(prm["zoom"]), "--step", str(prm["step"]),
            "--minor", str(prm["minor"]), "--major", str(prm["major"]), "--png-width", str(prm["width"])]
    if not p.local_save_png:
        args.append("--no-png")
    if p.local_save_vector:
        args.append("--vector")
    args.append("--yes")
    if LOCAL["proc"] is not None and LOCAL["proc"].poll() is None:
        raise RuntimeError("Generator już działa – poczekaj na koniec.")
    saved = _backup_existing(out, [".png", ".pdf", ".svg", ".meta.json"])
    ok = ("Gotowe – tekstura lokalna wczytana." if p.local_save_png else "Gotowe – zapisano PDF/SVG wycinka (bez PNG).")
    if saved:
        ok += " Poprzednie pliki: *.bak."
    _start_job(scene, args, _log_path(out), "local_status", ok,
               "Generuję teksturę lokalną (%.1f x %.1f km, PNG %d px)..." % (wm / 1000.0, hm / 1000.0, prm["width"]))
    return _log_path(out)


def global_command(scene, python=None, out=None):
    """Komenda generatora GLOBALNEJ tekstury warstwic (cały teren) z ustawień panelu."""
    T = STATE["T"]
    p = scene.hrf
    if not T or not T.get("terrain_path"):
        raise RuntimeError("Brak pliku terenu – zbuduj scenę z terenem.")
    if not p.global_save_png and not p.global_save_vector:
        raise RuntimeError("Zaznacz co najmniej jedno: Zapisz PNG albo Zapisz PDF/SVG.")
    here = script_dirs()[0] if script_dirs() else os.getcwd()
    tool = os.path.normpath(os.path.join(here, "..", "tools", "hrf_contours.py"))
    out = out or os.path.join(os.path.dirname(os.path.abspath(T["path"])), "hrf_contours")
    exe = python or ("py" if os.name == "nt" else "python3")
    quote = (lambda x: '"%s"' % x) if " " in exe else (lambda x: x)
    args = ["--minor", "%g" % p.global_minor, "--major", "%g" % p.global_major,
            "--png-width", str(p.global_width if p.global_save_png else 0)]
    if not p.global_save_vector:
        args += ["--no-pdf", "--no-svg"]
    args.append("--yes")
    cmd = '%s "%s" "%s" "%s" %s' % (quote(exe), tool, T["terrain_path"], out, " ".join(args))
    return cmd, (tool, out, args)


def start_global_generation(scene, out=None):
    cmd, (tool, out, args) = global_command(scene, out=out)
    if not os.path.isfile(tool):
        raise RuntimeError("Nie znaleziono narzędzia: " + tool)
    if p_major_invalid(scene.hrf):
        raise RuntimeError("Odstęp głównych warstwic musi być wielokrotnością odstępu pomocniczych.")
    if LOCAL["proc"] is not None and LOCAL["proc"].poll() is None:
        raise RuntimeError("Generator już działa – poczekaj na koniec.")
    saved = _backup_existing(out, [".png", ".pdf", ".svg", ".meta.json"])
    ok = "Gotowe – tekstura globalna wczytana." + (" Poprzednie pliki: *.bak." if saved else "")
    _start_job(scene, [_python_exe(), tool, STATE["T"]["terrain_path"], out] + args, _log_path(out), "global_status", ok,
               "Generuję teksturę globalną...")
    return _log_path(out)


def streets_command(scene, python=None, out=None):
    """Komenda generatora mapy ulic (hrf_streetmap.py) dla całego terenu z ustawień panelu."""
    T = STATE["T"]
    p = scene.hrf
    if not T or not T.get("terrain_path"):
        raise RuntimeError("Brak pliku terenu – zbuduj scenę z terenem.")
    layers = [k for k, on in (("roads", p.st_roads), ("cycleways", p.st_cycle), ("streets", p.st_small), ("rivers", p.st_rivers),
                              ("water", p.st_water), ("boundaries", p.st_bound)) if on]
    if not layers:
        raise RuntimeError("Zaznacz co najmniej jedną warstwę mapy ulic.")
    if not p.streets_save_png and not p.streets_save_vector:
        raise RuntimeError("Zaznacz co najmniej jedno: Zapisz PNG albo Zapisz PDF/SVG.")
    here = script_dirs()[0] if script_dirs() else os.getcwd()
    tool = os.path.normpath(os.path.join(here, "..", "tools", "hrf_streetmap.py"))
    out = out or os.path.join(os.path.dirname(os.path.abspath(T["path"])), "hrf_streets")
    exe = python or ("py" if os.name == "nt" else "python3")
    quote = (lambda x: '"%s"' % x) if " " in exe else (lambda x: x)
    args = ["--layers", ",".join(layers), "--png-width", str(p.streets_width if p.streets_save_png else 0)]
    if not p.streets_save_vector:
        args += ["--no-pdf", "--no-svg"]
    args.append("--yes")
    cmd = '%s "%s" "%s" "%s" %s' % (quote(exe), tool, T["terrain_path"], out, " ".join(args))
    return cmd, (tool, out, args)


def start_streets_generation(scene, out=None):
    cmd, (tool, out, args) = streets_command(scene, out=out)
    if not os.path.isfile(tool):
        raise RuntimeError("Nie znaleziono narzędzia: " + tool)
    if LOCAL["proc"] is not None and LOCAL["proc"].poll() is None:
        raise RuntimeError("Generator już działa – poczekaj na koniec.")
    saved = _backup_existing(out, [".png", ".pdf", ".svg", ".meta.json"])
    ok = "Gotowe – mapa ulic zapisana." + (" Poprzednie pliki: *.bak." if saved else "")
    _start_job(scene, [_python_exe(), tool, STATE["T"]["terrain_path"], out] + args, _log_path(out), "streets_status", ok,
               "Generuję mapę ulic (pobieranie z internetu)...")
    return _log_path(out)


def p_major_invalid(p):
    return p.global_minor <= 0 or abs(p.global_major / p.global_minor - round(p.global_major / p.global_minor)) > 1e-6


def make_terrain(T, cfg, col):
    tr = T["terrain"]
    g, R, C = tr["grid"], tr["rows"], tr["cols"]
    lat0, lng0, kx, ky = T["proj"]
    zmin, ex = T["zmin_ref"], cfg["EXAG"]
    sk = max(1, int(cfg["TERRAIN_SKIP"]))
    ri = list(range(0, R, sk))
    ci = list(range(0, C, sk))
    if ri[-1] != R - 1:
        ri.append(R - 1)
    if ci[-1] != C - 1:
        ci.append(C - 1)
    xs = [(tr["lng_w"] + (tr["lng_e"] - tr["lng_w"]) * c / (C - 1.0) - lng0) * kx for c in ci]
    verts = []
    for r in ri:
        y = (tr["lat_s"] + (tr["lat_n"] - tr["lat_s"]) * r / (R - 1.0) - lat0) * ky
        base = r * C
        for j, c in enumerate(ci):
            verts.append((xs[j], y, (g[base + c] - zmin) * ex))
    nc = len(ci)
    faces = []
    for i in range(len(ri) - 1):
        for j in range(nc - 1):
            a = i * nc + j
            faces.append((a, a + 1, a + nc + 1, a + nc))
    me = bpy.data.meshes.new("HRF_Terrain")
    me.from_pydata(verts, [], faces)
    me.update()
    try:
        me.shade_smooth()
    except Exception:
        for p in me.polygons:
            p.use_smooth = True
    add_terrain_uv(me, ri, ci, R, C)
    me.materials.append(diffuse_material("HRF_terrain", cfg["TERRAIN_COLOR"]))
    ob = bpy.data.objects.new("HRF_Terrain", me)
    col.objects.link(ob)
    return ob, len(verts)


def make_light(col, scene, own_world=True):
    """Słońce + światło otoczenia, żeby bryła terenu była czytelna.
    own_world=True: środowisko (World) sceny zostaje takie, jakie ustawiłeś (np. domyślne z pliku startowego);
    własny „HRF_World” powstaje tylko wtedy, gdy scena nie ma żadnego World."""
    li = bpy.data.lights.new("HRF_Sun", "SUN")
    ob = bpy.data.objects.new("HRF_Sun", li)
    col.objects.link(ob)
    cur = scene.world
    is_hrf = cur is not None and cur.name.startswith("HRF_World")
    if own_world:
        prev = bpy.data.worlds.get(scene.get("hrf_prev_world", "")) or bpy.data.worlds.get("World")
        if is_hrf and prev is not None:          # wcześniej podmienione przez skrypt – wracamy do Twojego środowiska
            scene.world = prev
        if scene.world is not None:
            return ob
    elif cur is not None and not is_hrf:
        scene["hrf_prev_world"] = cur.name       # zapamiętaj, żeby dało się wrócić
    w = bpy.data.worlds.get("HRF_World") or bpy.data.worlds.new("HRF_World")
    try:
        w.use_nodes = True
        bg = w.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value = (0.6, 0.65, 0.72, 1.0)
            bg.inputs["Strength"].default_value = 0.6
    except Exception:
        pass
    scene.world = w
    return ob


def apply_sun(scene):
    sun = STATE["objs"].get("sun")
    if not sun:
        return
    p = scene.hrf
    el, az = math.radians(p.sun_elev), math.radians(p.sun_azimuth)
    to_sun = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    sun.rotation_euler = (-to_sun).to_track_quat("-Z", "Y").to_euler()
    sun.data.energy = p.sun_strength


def set_engine(scene):
    ids = [e.identifier for e in type(scene.render).bl_rna.properties["engine"].enum_items]
    for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        if cand in ids:
            scene.render.engine = cand
            return cand
    return scene.render.engine


# ---------------------------------------------------------------- animacja
def key_objects(scene, cfg, T):
    """Strzałka (pozycja, kierunek) i przejechany odcinek – klatka po klatce."""
    o = STATE["objs"]
    arrow, done = o["arrow"], o["done"]
    t0, speed, nf, fps = time_map(cfg, T)
    arrow.animation_data_clear()
    done.data.animation_data_clear()
    prefs = bpy.context.preferences.edit
    old = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"
    prev = None
    done.data.bevel_factor_start = 0.0
    for fi in range(nf):
        fr = 1 + fi
        t = max(0.0, min(T["n"] - 1.0, t0 + fi / float(fps) * speed))
        P = Vector((at(T["x"], t), at(T["y"], t), at(T["z"], t)))
        ph, th = at(T["phi_arrow"], t), at(T["theta_arrow"], t)
        d = Vector((math.cos(th) * math.cos(ph), math.cos(th) * math.sin(ph), math.sin(th)))
        arrow.location = P
        q = d.to_track_quat("Y", "Z")
        arrow.rotation_euler = q.to_euler("XYZ", prev) if prev else q.to_euler("XYZ")
        prev = arrow.rotation_euler.copy()
        arrow.keyframe_insert("location", frame=fr)
        arrow.keyframe_insert("rotation_euler", frame=fr)
        done.data.bevel_factor_end = max(1e-4, min(1.0, at(T["prog"], t)))
        done.data.keyframe_insert("bevel_factor_end", frame=fr)
    prefs.keyframe_new_interpolation_type = old


def key_chase_camera(scene, cfg, T, sz):
    """Kamera za strzałką (z bezwładnością), pozycje zapisane klatka po klatce."""
    cam = STATE["objs"]["cam"]
    t0, speed, nf, fps = time_map(cfg, T)
    s = sz["s"]
    cd = cam.data
    cd.lens = cfg["CAM_LENS_MM"]
    cd.clip_start = 0.5 * s
    cd.clip_end = 400000.0
    prefs = bpy.context.preferences.edit
    old = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"
    back, up, ahead = cfg["CAM_BACK_M"] * s, cfg["CAM_UP_M"] * s, cfg["CAM_LOOK_AHEAD_M"] * s
    dt = 1.0 / fps
    a_pos = 1.0 - math.exp(-dt / max(1e-3, cfg["CAM_POS_TAU_S"]))
    a_yaw = 1.0 - math.exp(-dt / max(1e-3, cfg["CAM_YAW_TAU_S"]))
    lat0, lng0, kx, ky = T["proj"]

    def terr_z(x, y):
        return (terrain_at(T["terrain"], lat0 + y / ky, lng0 + x / kx) - T["zmin_ref"]) * cfg["EXAG"]
    clearance = max(10.0 * s, 0.4 * up)
    cam_pos = cam_phi = prev_eul = None
    for fi in range(nf):
        fr = 1 + fi
        t = max(0.0, min(T["n"] - 1.0, t0 + fi / float(fps) * speed))
        P = Vector((at(T["x"], t), at(T["y"], t), at(T["z"], t)))
        target_phi = at(T["phi_cam"], t)
        cam_phi = target_phi if cam_phi is None else cam_phi + (target_phi - cam_phi) * a_yaw
        fwd = Vector((math.cos(cam_phi), math.sin(cam_phi), 0.0))
        want = P - fwd * back + Vector((0.0, 0.0, up))
        cam_pos = want if cam_pos is None else cam_pos + (want - cam_pos) * a_pos
        look = P + fwd * ahead
        cam_use = cam_pos.copy()
        if T["terrain"]:
            cam_use.z = max(cam_use.z, terr_z(cam_use.x, cam_use.y) + clearance)    # kamera nie wchodzi pod teren
        cq = (look - cam_use).to_track_quat("-Z", "Y")
        cam.location = cam_use
        cam.rotation_euler = cq.to_euler("XYZ", prev_eul) if prev_eul else cq.to_euler("XYZ")
        prev_eul = cam.rotation_euler.copy()
        cam.keyframe_insert("location", frame=fr)
        cam.keyframe_insert("rotation_euler", frame=fr)
    prefs.keyframe_new_interpolation_type = old


# Orbita i rozmiary są sterowane DRIVERAMI czytającymi pola panelu (scene.hrf). Dzięki temu pola można animować
# (najedź na pole i wciśnij I), a przeliczanie sceny nie kasuje ani nie nadpisuje klatek kluczowych.
DRIVER_VARS = {
    "vA": "hrf.orbit_auto", "vD": "hrf.orbit_distance", "vM": "hrf.orbit_distance_manual",
    "vE": "hrf.orbit_elev", "vS": "hrf.orbit_start", "vT": "hrf.orbit_turns",
    "vL": "hrf.line_scale", "vDs": "hrf.done_scale", "vAs": "hrf.arrow_scale", "vN": "frame_end",
}
DEFF = "max(5, vA + (vD - vA) * vM)"      # odległość kamery od celu: automatyczna albo ręczna (animowalna)


def set_driver(owner, path, index, expr, scene):
    """Driver skryptowy z prostym wyrażeniem (nie wymaga zgody na uruchamianie skryptów Pythona)."""
    try:
        owner.driver_remove(path, index)
    except Exception:
        pass
    fc = owner.driver_add(path, index)
    for m in list(fc.modifiers):
        fc.modifiers.remove(m)
    drv = fc.driver
    drv.type = "SCRIPTED"
    drv.expression = expr
    for name, dp in DRIVER_VARS.items():
        if re.search(r"\b%s\b" % name, expr):
            v = drv.variables.new()
            v.name = name
            v.type = "SINGLE_PROP"
            tg = v.targets[0]
            tg.id_type = "SCENE"
            tg.id = scene
            tg.data_path = dp
    return fc


def remove_orbit_drivers():
    o = STATE["objs"]
    pairs = [(o["cam"], "location"), (o["cam"].data, "clip_start"), (o["rig"], "rotation_euler"),
             (o["route"].data, "bevel_depth"), (o["done"].data, "bevel_depth"), (o["arrow"], "scale"),
             (o["route"], "location"), (o["done"], "location"), (o["arrow"], "delta_location")]
    for owner, path in pairs:
        try:
            owner.driver_remove(path)
        except Exception:
            pass


def install_orbit_drivers(scene, cfg):
    """Pozycja kamery (odległość, wysokość), obrót rigu, grubość linii, rozmiar strzałki i podniesienie nad teren."""
    o, T = STATE["objs"], STATE["T"]
    cam, rig = o["cam"], o["rig"]
    cam.location = (0.0, 0.0, 0.0)
    set_driver(cam, "location", 0, "cos(radians(vE)) * " + DEFF, scene)
    set_driver(cam, "location", 2, "sin(radians(vE)) * " + DEFF, scene)
    set_driver(cam.data, "clip_start", -1, "0.002 * " + DEFF, scene)
    set_driver(rig, "rotation_euler", 2, "radians(vS) + 2 * pi * vT * (frame - 1) / vN", scene)
    rx, ry = int(cfg["RES_X"]), int(cfg["RES_Y"])
    ppr = cfg["CAM_LENS_MM"] / 36.0 * max(rx, ry)                    # piksele na radian
    kr, ka = cfg["ORBIT_TUBE_PX"] / ppr, cfg["ORBIT_ARROW_PX"] / ppr
    radius = "%.9g * %s * vL" % (kr, DEFF)                           # promień linii w pikselach ekranu
    set_driver(o["route"].data, "bevel_depth", -1, radius, scene)
    set_driver(o["done"].data, "bevel_depth", -1, radius + " * vDs", scene)
    for i in range(3):
        set_driver(o["arrow"], "scale", i, "%.9g * %s * vAs" % (ka, DEFF), scene)
    if T["terrain"]:                                                 # rura leży na terenie, nie w nim
        lift = "1.6 * " + radius
        set_driver(o["route"], "location", 2, lift, scene)
        set_driver(o["done"], "location", 2, lift, scene)
        set_driver(o["arrow"], "delta_location", 2, lift, scene)


def refresh_orbit(scene):
    """Cel (null albo strzałka), ogniskowa i drivery orbity; wołane przy zmianie celu, ogniskowej, rozdzielczości, trybu."""
    o, T = STATE["objs"], STATE["T"]
    if not o or not T or "rig" not in o:
        return
    cfg = cfg_from_props(scene)
    p = scene.hrf
    if cfg["CAM_MODE"] != "orbit":
        return
    cam, rig, pivot = o["cam"], o["rig"], o["pivot"]
    tgt = o["arrow"] if p.orbit_target == "arrow" else pivot
    for c in rig.constraints:
        if c.type == "COPY_LOCATION":
            c.target = tgt
    for c in cam.constraints:
        if c.type == "TRACK_TO":
            c.target = tgt
    cfg_auto = dict(cfg)
    cfg_auto["ORBIT_DISTANCE_M"] = 0.0
    auto = calc_sizes(cfg_auto, T, p)["orbit_dist"]
    old_busy = STATE["busy"]
    STATE["busy"] = True
    try:
        p.orbit_auto = auto
    finally:
        STATE["busy"] = old_busy
    cam.data.lens = p.lens
    cam.data.clip_end = 400000.0
    pivot.empty_display_size = max(10.0, auto * 0.03)
    install_orbit_drivers(scene, cfg)


def setup_camera(scene):
    """Ustawia kamerę wg trybu: orbita (rig + constraints, na żywo) albo za strzałką (zapisane klatki)."""
    o, T = STATE["objs"], STATE["T"]
    cfg = cfg_from_props(scene)
    sz = calc_sizes(cfg, T, scene.hrf)
    cam, rig, pivot = o["cam"], o["rig"], o["pivot"]
    remove_orbit_drivers()
    cam.animation_data_clear()
    rig.animation_data_clear()
    for c in list(cam.constraints):
        cam.constraints.remove(c)
    for c in list(rig.constraints):
        rig.constraints.remove(c)
    cam.parent = None
    cam.matrix_parent_inverse = Matrix.Identity(4)
    cam.location = (0.0, 0.0, 0.0)
    cam.rotation_euler = (0.0, 0.0, 0.0)
    if sz["orbit"]:
        cam.parent = rig
        rig.constraints.new("COPY_LOCATION")
        tt = cam.constraints.new("TRACK_TO")
        tt.track_axis = "TRACK_NEGATIVE_Z"
        tt.up_axis = "UP_Y"
        pivot.hide_viewport = False
        refresh_orbit(scene)
    else:
        pivot.hide_viewport = True
        key_chase_camera(scene, cfg, T, sz)


def apply_render_settings(scene, cfg, nf):
    scene.camera = STATE["objs"]["cam"]
    scene.frame_start, scene.frame_end = 1, nf
    scene.render.fps = int(cfg["FPS"])
    scene.render.resolution_x, scene.render.resolution_y = int(cfg["RES_X"]), int(cfg["RES_Y"])
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = cfg["OUTPUT_DIR"] + "frame_"
    try:
        scene.view_settings.view_transform = "Standard"
    except Exception:
        pass
    return set_engine(scene)


def apply_look(scene):
    """Grubość linii i rozmiar strzałki. W orbicie robią to drivery (refresh_orbit); przy kamerze za strzałką – wartości stałe."""
    o, T = STATE["objs"], STATE["T"]
    if not o or not T:
        return
    cfg = cfg_from_props(scene)
    if cfg["CAM_MODE"] == "orbit":
        return
    sz = calc_sizes(cfg, T, scene.hrf)
    r = sz["radius"]
    lift = r * 1.6 if T["terrain"] else 0.0
    o["route"].data.bevel_depth = r
    o["done"].data.bevel_depth = r * cfg["DONE_SCALE"]
    o["route"].location.z = lift                         # rura leży na terenie, nie w nim
    o["done"].location.z = lift
    o["arrow"].scale = (sz["arrow_len"],) * 3
    o["arrow"].delta_location.z = lift


def apply_colors(scene):
    p = scene.hrf
    set_material_color("HRF_route", p.route_color)
    set_material_color("HRF_done", p.done_color)
    set_material_color("HRF_arrow", p.arrow_color)
    set_material_color("HRF_terrain", p.terrain_color)


# ---------------------------------------------------------------- budowa i przeliczanie
def build_scene(scene):
    """Pełna budowa sceny z bieżących wartości panelu."""
    cfg = cfg_from_props(scene)
    p = scene.hrf
    raw = load_raw(cfg)
    T = build_track(cfg, raw)
    old_pivot = bpy.data.objects.get("HRF_Pivot")        # przesunięty ręcznie null zostaje po przebudowie
    keep_loc = old_pivot.location.copy() if old_pivot else None
    keep_exag = STATE.get("exag_built") or cfg["EXAG"]
    STATE["objs"], STATE["T"] = {}, None      # stare obiekty zostaną usunięte
    col = clean_scene()
    sz = calc_sizes(cfg, T, p)
    o = {}
    mat_route = emission_material("HRF_route", cfg["ROUTE_COLOR"])
    mat_done = emission_material("HRF_done", cfg["DONE_COLOR"])
    mat_arrow = emission_material("HRF_arrow", cfg["ARROW_COLOR"])
    terrain_info = None
    if T["terrain"]:
        o["terrain"], nv = make_terrain(T, cfg, col)
        o["sun"] = make_light(col, scene, cfg["OWN_WORLD"])
        terrain_info = {"file": T["terrain_path"], "vertices": nv, "draped": bool(cfg["TERRAIN_DRAPE"])}
    o["route"] = make_curve("HRF_Route", T["pts"], sz["radius"], mat_route, col)
    o["done"] = make_curve("HRF_RouteDone", T["pts"], sz["radius"] * cfg["DONE_SCALE"], mat_done, col)
    o["arrow"] = make_arrow("HRF_Arrow", mat_arrow, col)
    cam = bpy.data.objects.new("HRF_Camera", bpy.data.cameras.new("HRF_Camera"))
    col.objects.link(cam)
    o["cam"] = cam
    E = T["ext"]
    pivot = bpy.data.objects.new("HRF_Pivot", None)      # null: cel orbity kamery, przesuwaj go (G) albo animuj
    pivot.empty_display_type = "SPHERE"
    pivot.location = (E["cx"], E["cy"], E["cz"]) if keep_loc is None else (keep_loc.x, keep_loc.y, keep_loc.z * cfg["EXAG"] / max(1e-6, keep_exag))
    col.objects.link(pivot)
    rig = bpy.data.objects.new("HRF_Rig", None)          # obraca się wokół celu; kamera jest jego dzieckiem
    rig.empty_display_type = "PLAIN_AXES"
    rig.empty_display_size = 1.0
    rig.hide_select = True
    col.objects.link(rig)
    o["pivot"], o["rig"] = pivot, rig
    STATE["exag_built"] = cfg["EXAG"]
    scene["hrf_exag_built"] = cfg["EXAG"]
    STATE["objs"], STATE["T"] = o, T
    apply_look(scene)
    apply_sun(scene)
    apply_colors(scene)
    apply_terrain_style(scene)
    key_objects(scene, cfg, T)
    setup_camera(scene)
    t0, speed, nf, fps = time_map(cfg, T)
    engine = apply_render_settings(scene, cfg, nf)
    replace_nulls(scene)
    scene.frame_set(1)
    info = {"data": T["path"], "name": T["name"], "length_km": round(T["length_m"] / 1000.0, 2),
            "curve_points": len(T["pts"]), "frames": nf, "fps": fps, "mode": cfg["MODE"], "cam": cfg["CAM_MODE"],
            "engine": engine, "z_max_m": round(T["zmax"], 1), "exag": cfg["EXAG"], "terrain": terrain_info,
            "blender": bpy.app.version_string}
    p.info = "%d klatek | %.1f km | teren: %s" % (nf, T["length_m"] / 1000.0, ("%d tys. wierzch." % (terrain_info["vertices"] // 1000)) if terrain_info else "brak")
    print("[HRF] " + json.dumps(info, ensure_ascii=False))
    print("[HRF] Panel sterowania: w oknie 3D wciśnij N i wybierz zakładkę HRF (prawa krawędź paska bocznego).")
    return info


def apply_time_camera(scene):
    """Szybko: przelicza animację (czas, kamera, strzałka, przejechany odcinek) bez przebudowy obiektów."""
    T = STATE["T"]
    if not T or not STATE["objs"]:
        raise RuntimeError("Scena nie jest zbudowana – kliknij 'Przebuduj scenę'.")
    cfg = cfg_from_props(scene)
    apply_look(scene)
    key_objects(scene, cfg, T)
    setup_camera(scene)
    t0, speed, nf, fps = time_map(cfg, T)
    apply_render_settings(scene, cfg, nf)
    scene.frame_set(1)
    scene.hrf.info = "%d klatek | %.1f km" % (nf, T["length_m"] / 1000.0)
    return nf


# ---------------------------------------------------------------- nulle w podanych współrzędnych
def parse_coords(text):
    """„49.5791, 20.0501” = szerokość, długość geograficzna (jak w Mapach Google); opcjonalna trzecia liczba to wysokość m n.p.m.
    Przecinek dziesiętny też działa („49,5791 20,0501”)."""
    t = (text or "").replace(";", " ").replace("\u00b0", " ")
    if "." not in t:
        t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", t)]
    if len(nums) < 2:
        raise RuntimeError("Podaj współrzędne jako: szerokość, długość (np. 49.5791, 20.0501) – opcjonalnie trzecia liczba to wysokość m n.p.m.")
    lat, lng = nums[0], nums[1]
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        raise RuntimeError("Współrzędne poza zakresem: kolejność to szerokość (−90..90), potem długość (−180..180).")
    return lat, lng, (nums[2] if len(nums) > 2 else None)


def coords_to_xyz(lat, lng, alt=None):
    """Pozycja w scenie dla współrzędnych geograficznych; wysokość z terenu (albo z podanej wysokości n.p.m., albo z najbliższego punktu trasy)."""
    T = STATE["T"]
    if not T:
        raise RuntimeError("Scena nie jest zbudowana – kliknij 'Przebuduj scenę'.")
    lat0, lng0, kx, ky = T["proj"]
    exag = STATE.get("exag_built") or 1.0
    x, y = (lng - lng0) * kx, (lat - lat0) * ky
    note = ""
    tr = T["terrain"]
    if alt is not None:
        z = (alt - T["zmin_ref"]) * exag
    elif tr:
        z = (terrain_at(tr, lat, lng) - T["zmin_ref"]) * exag
        if not (tr["lat_s"] <= lat <= tr["lat_n"] and tr["lng_w"] <= lng <= tr["lng_e"]):
            note = " (poza pobranym terenem – wysokość z jego brzegu)"
    else:
        i = min(range(T["n"]), key=lambda k: (T["x"][k] - x) ** 2 + (T["y"][k] - y) ** 2)
        z, note = T["z"][i], " (brak terenu – wysokość z najbliższego punktu trasy)"
    return x, y, z, note


def _null_collection(scene):
    col = bpy.data.collections.get("HRF_Nulls")
    if col is None:
        col = bpy.data.collections.new("HRF_Nulls")
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    return col


def add_null(scene, text, name="HRF_Null", to_pivot=False):
    """Dodaje null (Empty) w podanych współrzędnych albo przenosi tam HRF_Pivot (cel orbity)."""
    lat, lng, alt = parse_coords(text)
    x, y, z, note = coords_to_xyz(lat, lng, alt)
    if to_pivot:
        pv = STATE["objs"].get("pivot")
        if pv is None:
            raise RuntimeError("Scena nie jest zbudowana – kliknij 'Przebuduj scenę'.")
        pv.hide_viewport = False
        pv.location = (x, y, z)
        return "HRF_Pivot przeniesiony na %.5f, %.5f%s" % (lat, lng, note)
    E = STATE["T"]["ext"]
    ob = bpy.data.objects.new(name or "HRF_Null", None)
    ob.empty_display_type = "PLAIN_AXES"
    ob.empty_display_size = max(5.0, 0.01 * max(E["w"], E["h"]))
    ob["hrf_lat"], ob["hrf_lng"] = lat, lng
    if alt is not None:
        ob["hrf_alt"] = alt
    ob.location = (x, y, z)
    _null_collection(scene).objects.link(ob)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    return "Dodano null „%s” w %.5f, %.5f%s" % (ob.name, lat, lng, note)


def replace_nulls(scene):
    """Po przebudowie sceny nulle wracają na swoje współrzędne (zmienia się np. przewyższenie, więc wysokość trzeba policzyć od nowa)."""
    col = bpy.data.collections.get("HRF_Nulls")
    if not col or not STATE["T"]:
        return
    for ob in col.objects:
        if "hrf_lat" in ob:
            try:
                x, y, z, _ = coords_to_xyz(ob["hrf_lat"], ob["hrf_lng"], ob.get("hrf_alt"))
                ob.location = (x, y, z)
            except Exception:
                traceback.print_exc()


def _bridge_timer():
    """Odbiera polecenia z programu HRF.pyw (plik cache/hrf_commands.json obok danych) i odpowiada plikiem hrf_commands.ack."""
    try:
        T = STATE["T"]
        if T and STATE["objs"]:
            d = os.path.join(os.path.dirname(os.path.abspath(T["path"])), "cache")
            f = os.path.join(d, "hrf_commands.json")
            if os.path.isfile(f):
                with open(f, encoding="utf-8") as fh:
                    cmds = json.load(fh)
                os.remove(f)
                if isinstance(cmds, dict):
                    cmds = [cmds]
                acks = []
                for c in cmds:
                    try:
                        if c.get("cmd") not in ("null", "pivot"):
                            raise RuntimeError("Nieznane polecenie: %s" % c.get("cmd"))
                        msg = add_null(bpy.context.scene, c.get("text", ""), c.get("name") or "HRF_Null", to_pivot=(c["cmd"] == "pivot"))
                        acks.append({"id": c.get("id"), "ok": True, "msg": msg})
                    except Exception as e:
                        traceback.print_exc()
                        acks.append({"id": c.get("id"), "ok": False, "msg": str(e)})
                with open(os.path.join(d, "hrf_commands.ack"), "w", encoding="utf-8") as fh:
                    json.dump(acks, fh, ensure_ascii=False)
                for area in bpy.context.window_manager.windows[0].screen.areas:
                    area.tag_redraw()
    except Exception:
        traceback.print_exc()
    return 1.0


# ---------------------------------------------------------------- panel (właściwości, operatory, UI)
def _upd_look(self, context):
    if STATE["busy"] or not STATE["objs"]:
        return
    if context.scene.hrf.cam_mode == "orbit":      # w orbicie grubość i rozmiar liczą drivery (można je animować)
        return
    try:
        apply_look(context.scene)
    except Exception:
        traceback.print_exc()


_PENDING = {"t": 0.0, "scene": None, "scheduled": False}


def _apply_timer():
    """Po ostatniej zmianie czasu (ok. 0,7 s ciszy) przelicza animację: długość, klatki, klucze strzałki i kamery."""
    if time.time() - _PENDING["t"] < 0.7:
        return 0.3
    _PENDING["scheduled"] = False
    sc = bpy.data.scenes.get(_PENDING["scene"] or "")
    if sc is None or not STATE["objs"]:
        return None
    try:
        apply_time_camera(sc)
    except RuntimeError as e:          # np. za dużo klatek – komunikat w panelu zamiast wyjątku
        sc.hrf.info = str(e)
        print("[HRF] " + str(e))
    except Exception:
        traceback.print_exc()
    return None


def _upd_time(self, context):
    """Zmiana czasu/trybu/przyspieszenia/FPS/rozdzielczości: animacja przelicza się sama po chwili."""
    if STATE["busy"] or not STATE["objs"]:
        return
    sc = context.scene
    _PENDING["t"] = time.time()
    _PENDING["scene"] = sc.name
    try:
        sc.hrf.info = "Zmieniono czas – przeliczam animację..."
    except Exception:
        pass
    if not _PENDING["scheduled"]:
        _PENDING["scheduled"] = True
        bpy.app.timers.register(_apply_timer, first_interval=0.8)


def _upd_orbit(self, context):
    """Zmiana celu, odległości, wysokości, ogniskowej albo obrotów orbity – na żywo."""
    if STATE["busy"] or not STATE["objs"]:
        return
    try:
        if context.scene.hrf.cam_mode == "orbit":
            refresh_orbit(context.scene)
        else:
            STATE["objs"]["cam"].data.lens = context.scene.hrf.lens
    except Exception:
        traceback.print_exc()


def _upd_dist_manual(self, context):
    """Włączenie 'Odległość ręcznie' ustawia pole na bieżącą odległość automatyczną, żeby kamera nie skoczyła."""
    if STATE["busy"] or not STATE["objs"] or not STATE["T"]:
        return
    try:
        sc = context.scene
        p = sc.hrf
        if p.orbit_distance_manual:
            cfg = cfg_from_props(sc)
            cfg["ORBIT_DISTANCE_M"] = 0.0
            auto = calc_sizes(cfg, STATE["T"], p)["orbit_dist"]
            STATE["busy"] = True
            try:
                p.orbit_distance = float(round(auto))
            finally:
                STATE["busy"] = False
        if p.cam_mode == "orbit":
            refresh_orbit(sc)
    except Exception:
        traceback.print_exc()


def _upd_cam_mode(self, context):
    if STATE["busy"] or not STATE["objs"]:
        return
    try:
        setup_camera(context.scene)
        apply_look(context.scene)
    except Exception:
        traceback.print_exc()


def _upd_colors(self, context):
    if STATE["busy"]:
        return
    try:
        apply_colors(context.scene)
    except Exception:
        traceback.print_exc()


def _upd_sun(self, context):
    if STATE["busy"]:
        return
    try:
        apply_sun(context.scene)
    except Exception:
        traceback.print_exc()


def _upd_terrain_style(self, context):
    if STATE["busy"]:
        return
    try:
        apply_terrain_style(context.scene)
    except Exception:
        traceback.print_exc()


class HRFProps(bpy.types.PropertyGroup):
    info: StringProperty(name="Stan", default="")
    data_path: StringProperty(name="Plik danych", subtype="FILE_PATH", default="",
                              description="Plik *_hrf.json; puste = najnowszy w folderze data/ obok repo")
    mode: EnumProperty(name="Tryb", default="full", update=_upd_time, items=[
        ("full", "Cała jazda w N sekund", "Cała aktywność skompresowana do podanej liczby sekund"),
        ("speed", "Przyspieszenie (×)", "Czas rzeczywisty ×N: 1 = 1:1, 10 = dziesięć razy szybciej; długość animacji wynika z okna i przyspieszenia"),
        ("clip", "Klip 1:1 (czas rzeczywisty)", "Okno od początku do końca w czasie rzeczywistym, bez przyspieszenia")])
    speed: FloatProperty(name="Przyspieszenie ×", default=10.0, min=0.1, soft_max=300.0, precision=1, update=_upd_time,
                         description="1 = czas rzeczywisty (1:1), 10 = dziesięć razy szybciej")
    start_min: FloatProperty(name="Początek (min)", default=0.0, min=0.0, soft_max=600.0, precision=2, update=_upd_time,
                             description="Początek klipu w minutach od startu całej aktywności")
    end_min: FloatProperty(name="Koniec (min)", default=0.0, min=0.0, soft_max=600.0, precision=2, update=_upd_time,
                           description="Koniec klipu w minutach od startu całej aktywności; 0 = do końca jazdy")
    full_seconds: FloatProperty(name="Długość przelotu (s)", default=40.0, min=1.0, soft_max=300.0, update=_upd_time)
    fps: IntProperty(name="FPS", default=30, min=1, max=120, update=_upd_time)
    res_x: IntProperty(name="Szerokość", default=1920, min=16, max=16384, update=_upd_time)
    res_y: IntProperty(name="Wysokość", default=1080, min=16, max=16384, update=_upd_time)
    cam_mode: EnumProperty(name="Kamera", default="orbit", update=_upd_cam_mode, items=[
        ("orbit", "Orbita (cała trasa)", "Cała trasa w kadrze, kamera krąży wokół"),
        ("chase", "Za strzałką", "Kamera jedzie za strzałką")])
    orbit_target: EnumProperty(name="Cel orbity", default="null", update=_upd_orbit, items=[
        ("null", "Null (przesuwalny punkt)", "Kamera krąży wokół obiektu HRF_Pivot; przesuń go (G) lub animuj"),
        ("arrow", "Strzałka", "Kamera krąży wokół strzałki i jedzie razem z nią")])
    orbit_distance_manual: BoolProperty(name="Odległość ręcznie", default=False, update=_upd_dist_manual,
                                        description="Wyłączone = automatycznie (null: cała trasa w kadrze, strzałka: bliżej)")
    orbit_distance: FloatProperty(name="Odległość od celu (m)", default=1000.0, min=5.0, soft_min=50.0, soft_max=100000.0,
                                  step=10000, precision=0)
    orbit_auto: FloatProperty(name="Odległość automatyczna (m)", default=1000.0, min=5.0, precision=0,
                              description="Wyliczana przez skrypt (cała trasa w kadrze albo bliżej strzałki)")
    orbit_elev: FloatProperty(name="Wysokość kamery (°)", default=40.0, min=5.0, max=89.0)
    orbit_margin: FloatProperty(name="Zapas w kadrze", default=0.9, min=0.3, max=3.0, update=_upd_orbit,
                                description="Mniej = kamera bliżej; poniżej ok. 0.8 trasa może wychodzić poza kadr")
    orbit_turns: FloatProperty(name="Okrążenia", default=1.0, min=-10.0, max=10.0)
    orbit_start: FloatProperty(name="Start orbity (°)", default=-90.0)
    lens: FloatProperty(name="Ogniskowa (mm)", default=30.0, min=8.0, max=200.0, update=_upd_orbit)
    chase_back: FloatProperty(name="Za strzałką (m)", default=60.0, min=1.0)
    chase_up: FloatProperty(name="Nad strzałką (m)", default=45.0, min=1.0)
    chase_ahead: FloatProperty(name="Patrzy przed siebie (m)", default=20.0, min=0.0)
    exag: FloatProperty(name="Przewyższenie", default=1.5, min=0.0, soft_max=6.0,
                        description="1 = skala rzeczywista; zmiana wymaga przebudowy sceny")
    use_terrain: BoolProperty(name="Teren", default=True)
    terrain_path: StringProperty(name="Plik terenu", subtype="FILE_PATH", default="",
                                 description="Puste = data/hrf_terrain.json obok danych trasy")
    drape: BoolProperty(name="Trasa na terenie", default=True)
    terrain_skip: IntProperty(name="Oczko co N", default=1, min=1, max=8,
                              description="Rzadsza siatka terenu = lżejsza scena")
    line_scale: FloatProperty(name="Grubość linii", default=1.0, min=0.1, max=10.0, update=_upd_look)
    arrow_scale: FloatProperty(name="Rozmiar strzałki", default=1.0, min=0.1, max=10.0, update=_upd_look)
    done_scale: FloatProperty(name="Przejechana grubsza ×", default=1.5, min=1.0, max=5.0, update=_upd_look)
    route_color: FloatVectorProperty(name="Trasa przed strzałką", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=(0.35, 0.65, 1.0), update=_upd_colors)
    done_color: FloatVectorProperty(name="Przejechana", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(1.0, 1.0, 1.0), update=_upd_colors)
    arrow_color: FloatVectorProperty(name="Strzałka", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=(1.0, 0.38, 0.05), update=_upd_colors)
    terrain_style: EnumProperty(name="Styl terenu", default="gray", update=_upd_terrain_style, items=[
        ("gray", "Gładki (oświetlony)", "Szary teren z cieniowaniem"),
        ("contours", "Warstwice (tekstura)", "Czarne tło, białe warstwice z pliku PNG (tools/hrf_contours.py)"),
        ("streets", "Mapa ulic (tekstura)", "Czarne tło, białe kontury ulic, rzek, wody i granic miasta z pliku PNG (tools/hrf_streetmap.py)")])
    terrain_texture: StringProperty(name="PNG warstwic", subtype="FILE_PATH", default="", update=_upd_terrain_style,
                                    description="Puste = hrf_contours.png obok pliku terenu; po edycji w Illustratorze wyeksportuj PNG o tych samych proporcjach")
    streets_width: IntProperty(name="PNG szerokość (px)", default=4096, min=512, max=16384,
                               description="Szerokość PNG mapy ulic w pikselach (metry na piksel = szerokość obszaru / ta wartość; 8192 = ostrzejsze cienkie linie, ale dłużej)")
    st_roads: BoolProperty(name="Główne drogi", default=True, description="Autostrady, drogi krajowe, wojewódzkie, powiatowe i trzeciorzędne")
    st_cycle: BoolProperty(name="Ścieżki rowerowe", default=True)
    st_small: BoolProperty(name="Drobne uliczki", default=False, description="Ulice osiedlowe, boczne i serwisowe – bardzo dużo linii")
    st_rivers: BoolProperty(name="Rzeki", default=True)
    st_water: BoolProperty(name="Jeziora (obrys)", default=True)
    st_bound: BoolProperty(name="Granice miasta", default=True)
    streets_save_png: BoolProperty(name="Zapisz PNG", default=True)
    streets_save_vector: BoolProperty(name="Zapisz PDF/SVG", default=False, description="Zapisuje też wektorowy PDF i SVG mapy ulic do edycji")
    streets_cmd: StringProperty(name="Komenda mapy ulic", default="")
    streets_status: StringProperty(name="Stan mapy ulic", default="")
    streets_texture: StringProperty(name="PNG mapy ulic", subtype="FILE_PATH", default="", update=_upd_terrain_style,
                                    description="Puste = hrf_streets.png obok pliku terenu (tools/hrf_streetmap.py)")
    local_use: BoolProperty(name="Użyj tekstury lokalnej", default=True, update=_upd_terrain_style,
                            description="Nakłada hrf_local.png (wycinek w wysokiej jakości) na globalną teksturę warstwic")
    local_margin: FloatProperty(name="Margines wycinka (m)", default=1500.0, min=50.0, soft_max=10000.0,
                                description="O ile szerszy od pokazywanej trasy jest wycinek tekstury lokalnej")
    local_texture: StringProperty(name="PNG lokalny", subtype="FILE_PATH", default="", update=_upd_terrain_style,
                                  description="Puste = hrf_local.png obok danych trasy (wraz z hrf_local.meta.json)")
    local_save_png: BoolProperty(name="Zapisz PNG", default=True,
                                 description="Rysuje PNG wycinka (tekstura, którą Blender nakłada na teren)")
    local_save_vector: BoolProperty(name="Zapisz PDF/SVG", default=False,
                                    description="Zapisuje też wektorowy PDF i SVG wycinka do edycji (Illustrator, Inkscape)")
    global_minor: FloatProperty(name="Warstwice co (m)", default=25.0, min=1.0, soft_max=100.0,
                                description="Odstęp warstwic pomocniczych w teksturze globalnej")
    global_major: FloatProperty(name="Główne co (m)", default=100.0, min=1.0, soft_max=500.0,
                                description="Odstęp warstwic głównych (wielokrotność odstępu pomocniczych)")
    global_width: IntProperty(name="PNG szerokość (px)", default=4096, min=512, max=16384,
                              description="Szerokość PNG całego terenu w pikselach")
    global_save_png: BoolProperty(name="Zapisz PNG", default=True)
    global_save_vector: BoolProperty(name="Zapisz PDF/SVG", default=False,
                                     description="Zapisuje też wektorowy PDF i SVG całego terenu do edycji")
    global_cmd: StringProperty(name="Komenda globalna", default="")
    global_status: StringProperty(name="Stan tekstury globalnej", default="")
    local_cmd: StringProperty(name="Komenda", default="")
    local_status: StringProperty(name="Stan tekstury lokalnej", default="")
    terrain_color: FloatVectorProperty(name="Teren", subtype="COLOR", size=3, min=0.0, max=1.0,
                                       default=(0.17, 0.19, 0.22), update=_upd_colors)
    sun_azimuth: FloatProperty(name="Słońce: azymut (°)", default=135.0, update=_upd_sun)
    sun_elev: FloatProperty(name="Słońce: wysokość (°)", default=40.0, min=1.0, max=90.0, update=_upd_sun)
    sun_strength: FloatProperty(name="Słońce: siła", default=3.0, min=0.0, soft_max=10.0, update=_upd_sun)
    own_world: BoolProperty(name="Zostaw moje środowisko (World)", default=True,
                            description="Włączone: skrypt nie zmienia środowiska (World) sceny, zostaje Twoje. Wyłączone: ustawia własne, jasne tło "
                                        "(działa po „Przebuduj scenę”). Siła słońca 0 wyłącza też słońce HRF")
    null_coords: StringProperty(name="Współrzędne", default="",
                                description="Szerokość, długość geograficzna jak w Mapach Google, np. 49.5791, 20.0501. Trzecia liczba (opcjonalnie) = wysokość m n.p.m.; "
                                            "bez niej null leży na terenie")
    null_name: StringProperty(name="Nazwa nulla", default="HRF_Null")
    output_dir: StringProperty(name="Folder renderu", subtype="DIR_PATH", default="",
                               description="PNG z przezroczystością; puste = folder hrf_render obok pliku .blend")


def _run_op(op, fn, ok_msg=None):
    try:
        res = fn()
        if ok_msg:
            op.report({"INFO"}, ok_msg(res) if callable(ok_msg) else ok_msg)
        return {"FINISHED"}
    except Exception as e:
        traceback.print_exc()
        op.report({"ERROR"}, str(e))
        return {"CANCELLED"}


class HRF_OT_rebuild(bpy.types.Operator):
    bl_idname = "hrf.rebuild"
    bl_label = "Przebuduj scenę"
    bl_description = "Buduje całą scenę od zera z wartości w panelu (po zmianie przewyższenia, terenu, pliku danych)"

    def execute(self, context):
        return _run_op(self, lambda: build_scene(context.scene), "Scena zbudowana")


class HRF_OT_apply(bpy.types.Operator):
    bl_idname = "hrf.apply"
    bl_label = "Przelicz animację teraz"
    bl_description = "Zmiany czasu przeliczają się same po chwili; ten przycisk robi to od razu (czas, kamera, strzałka, przejechany odcinek)"

    def execute(self, context):
        return _run_op(self, lambda: apply_time_camera(context.scene), lambda n: "Animacja: %d klatek" % n)


class HRF_OT_set_speed(bpy.types.Operator):
    bl_idname = "hrf.set_speed"
    bl_label = "Ustaw przyspieszenie"
    bl_description = "Ustawia tryb przyspieszenia i podaną wartość (1 = czas rzeczywisty 1:1)"
    value: FloatProperty(default=1.0)

    def execute(self, context):
        p = context.scene.hrf
        STATE["busy"] = True               # wartości ustawiamy tu, a przeliczenie robi operator (bez opóźnionego duplikatu)
        try:
            p.mode = "speed"
            p.speed = self.value
        finally:
            STATE["busy"] = False
        return _run_op(self, lambda: apply_time_camera(context.scene), lambda n: "Przyspieszenie %g×: %d klatek" % (self.value, n))


class HRF_OT_select_pivot(bpy.types.Operator):
    bl_idname = "hrf.select_pivot"
    bl_label = "Zaznacz null (pivot)"
    bl_description = "Zaznacza obiekt HRF_Pivot w oknie 3D – przesuń go klawiszem G, ustaw w Item (N) albo animuj"

    def execute(self, context):
        def go():
            pv = STATE["objs"].get("pivot")
            if pv is None:
                raise RuntimeError("Scena nie jest zbudowana – kliknij 'Przebuduj scenę'.")
            pv.hide_viewport = False
            for ob in context.view_layer.objects:
                ob.select_set(False)
            pv.select_set(True)
            context.view_layer.objects.active = pv
        return _run_op(self, go, "Zaznaczono HRF_Pivot")


class HRF_OT_center_pivot(bpy.types.Operator):
    bl_idname = "hrf.center_pivot"
    bl_label = "Null na środek trasy"
    bl_description = "Przywraca null (pivot) na środek trasy"

    def execute(self, context):
        def go():
            pv, T = STATE["objs"].get("pivot"), STATE["T"]
            if pv is None or not T:
                raise RuntimeError("Scena nie jest zbudowana – kliknij 'Przebuduj scenę'.")
            E = T["ext"]
            pv.location = (E["cx"], E["cy"], E["cz"])
        return _run_op(self, go, "Null przywrócony na środek trasy")


class HRF_OT_add_null(bpy.types.Operator):
    bl_idname = "hrf.add_null"
    bl_label = "Dodaj null"
    bl_description = ("Dodaje null (Empty) w podanych współrzędnych, na wysokości terenu. Leży w kolekcji HRF_Nulls i po 'Przebuduj scenę' wraca na te współrzędne")

    def execute(self, context):
        p = context.scene.hrf
        return _run_op(self, lambda: add_null(context.scene, p.null_coords, p.null_name), lambda m: m)


class HRF_OT_pivot_to_coords(bpy.types.Operator):
    bl_idname = "hrf.pivot_to_coords"
    bl_label = "Pivot tutaj"
    bl_description = "Przenosi HRF_Pivot (cel orbity kamery) na podane współrzędne"

    def execute(self, context):
        p = context.scene.hrf
        return _run_op(self, lambda: add_null(context.scene, p.null_coords, p.null_name, to_pivot=True), lambda m: m)


class HRF_OT_local_command(bpy.types.Operator):
    bl_idname = "hrf.local_command"
    bl_label = "Kopiuj komendę (wycinek)"
    bl_description = ("Liczy wycinek z bieżącego okna czasu, kopiuje do schowka gotową komendę generatora tekstury lokalnej "
                      "(wklej w terminalu); wynik wczyta się po 'Wczytaj teksturę ponownie'")

    def execute(self, context):
        def go():
            cmd, info = local_command(context.scene)
            context.scene.hrf.local_cmd = cmd
            context.window_manager.clipboard = cmd
            return info
        return _run_op(self, go, lambda i: "Komenda w schowku: wycinek %.1f x %.1f km, PNG %d px, kafelki z=%d" % (i[4] / 1000.0, i[5] / 1000.0, i[3]["width"], i[3]["zoom"]))


class HRF_OT_local_run(bpy.types.Operator):
    bl_idname = "hrf.local_run"
    bl_label = "Wygeneruj teraz"
    bl_description = ("Uruchamia generator tekstury lokalnej w tle (potrzebny internet na drobniejsze kafelki wysokości); "
                      "po zakończeniu materiał terenu odświeży się sam")

    def execute(self, context):
        return _run_op(self, lambda: start_local_generation(context.scene), lambda log: "Uruchomiono generator (log: %s)" % log)


class HRF_OT_global_command(bpy.types.Operator):
    bl_idname = "hrf.global_command"
    bl_label = "Kopiuj komendę (cały teren)"
    bl_description = "Kopiuje do schowka komendę generatora globalnej tekstury warstwic (wklej w terminalu)"

    def execute(self, context):
        def go():
            cmd, _ = global_command(context.scene)
            context.scene.hrf.global_cmd = cmd
            context.window_manager.clipboard = cmd
        return _run_op(self, go, "Komenda w schowku")


class HRF_OT_global_run(bpy.types.Operator):
    bl_idname = "hrf.global_run"
    bl_label = "Wygeneruj teraz (cały teren)"
    bl_description = ("Rysuje globalną teksturę warstwic całego terenu w tle (bez internetu); istniejące pliki "
                      "hrf_contours.* dostają kopię .bak")

    def execute(self, context):
        return _run_op(self, lambda: start_global_generation(context.scene), lambda log: "Uruchomiono generator (log: %s)" % log)


class HRF_OT_streets_command(bpy.types.Operator):
    bl_idname = "hrf.streets_command"
    bl_label = "Kopiuj komendę (mapa ulic)"
    bl_description = "Kopiuje do schowka komendę generatora mapy ulic (wklej w terminalu)"

    def execute(self, context):
        def go():
            cmd, _ = streets_command(context.scene)
            context.scene.hrf.streets_cmd = cmd
            context.window_manager.clipboard = cmd
        return _run_op(self, go, "Komenda w schowku")


class HRF_OT_streets_run(bpy.types.Operator):
    bl_idname = "hrf.streets_run"
    bl_label = "Wygeneruj teraz (mapa ulic)"
    bl_description = ("Pobiera z OpenStreetMap (internet) drogi, rzeki, wodę i granice dla obszaru terenu i rysuje hrf_streets.* w tle; "
                      "do serwera trafiają tylko rogi prostokątów obszaru, nie trasa. Istniejące pliki dostają kopię .bak")

    def execute(self, context):
        return _run_op(self, lambda: start_streets_generation(context.scene), lambda log: "Uruchomiono generator (log: %s)" % log)


class HRF_OT_reload_texture(bpy.types.Operator):
    bl_idname = "hrf.reload_texture"
    bl_label = "Wczytaj teksturę ponownie"
    bl_description = "Po ponownym wyeksportowaniu PNG warstwic (np. z Illustratora) odświeża materiał terenu"

    def execute(self, context):
        return _run_op(self, lambda: apply_terrain_style(context.scene), "Tekstura odświeżona")


class HRF_OT_render_frame(bpy.types.Operator):
    bl_idname = "hrf.render_frame"
    bl_label = "Renderuj bieżącą klatkę"

    def execute(self, context):
        bpy.ops.render.render("INVOKE_DEFAULT")
        return {"FINISHED"}


class HRF_OT_render_anim(bpy.types.Operator):
    bl_idname = "hrf.render_anim"
    bl_label = "Renderuj animację"
    bl_description = "PNG z przezroczystością do folderu renderu (najpierw zapisz plik .blend)"

    def execute(self, context):
        out = context.scene.hrf.output_dir or DEFAULT_OUT
        if out.startswith("//") and not bpy.data.filepath:
            self.report({"ERROR"}, "Najpierw zapisz plik .blend (File > Save As) albo podaj pełną ścieżkę folderu renderu")
            return {"CANCELLED"}
        context.scene.render.filepath = out + "frame_"
        bpy.ops.render.render("INVOKE_DEFAULT", animation=True)
        return {"FINISHED"}


class HRF_PT_main(bpy.types.Panel):
    bl_label = "HRF – trasa 3D"
    bl_idname = "HRF_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HRF"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.label(text=p.info or "Scena nie zbudowana", icon="INFO")
        lay.prop(p, "data_path")
        lay.operator("hrf.rebuild", icon="FILE_REFRESH")


class _Sub:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HRF"
    bl_parent_id = "HRF_PT_main"


class HRF_PT_time(_Sub, bpy.types.Panel):
    bl_label = "Czas i kadr"
    bl_idname = "HRF_PT_time"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        T = STATE.get("T")
        if T:
            lay.label(text="Cała aktywność: %.1f min (%s)" % ((T["n"] - 1.0) / 60.0, _hms(T["n"] - 1.0)), icon="TIME")
        row = lay.row(align=True)
        row.prop(p, "start_min")
        row.prop(p, "end_min")
        lay.prop(p, "mode")
        if p.mode == "full":
            lay.prop(p, "full_seconds")
        elif p.mode == "speed":
            lay.prop(p, "speed")
            row = lay.row(align=True)
            for lab, val in (("1:1", 1.0), ("5×", 5.0), ("10×", 10.0), ("30×", 30.0), ("60×", 60.0), ("120×", 120.0)):
                row.operator("hrf.set_speed", text=lab).value = val
        lay.label(text=time_summary(context.scene))
        row = lay.row(align=True)
        row.prop(p, "res_x")
        row.prop(p, "res_y")
        lay.prop(p, "fps")
        lay.operator("hrf.apply", icon="FILE_REFRESH")


class HRF_PT_camera(_Sub, bpy.types.Panel):
    bl_label = "Kamera"
    bl_idname = "HRF_PT_camera"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.prop(p, "cam_mode", text="")
        lay.prop(p, "lens")
        if p.cam_mode == "orbit":
            lay.prop(p, "orbit_target")
            lay.prop(p, "orbit_distance_manual")
            row = lay.row()
            row.active = p.orbit_distance_manual            # wyszarzone, ale nadal można animować
            row.prop(p, "orbit_distance")
            lay.label(text="Automatycznie: %.0f m" % p.orbit_auto)
            if p.orbit_target == "null":
                row = lay.row(align=True)
                row.operator("hrf.select_pivot", icon="EMPTY_AXIS")
                row.operator("hrf.center_pivot", icon="PIVOT_BOUNDBOX")
            lay.prop(p, "orbit_elev")
            if not p.orbit_distance_manual and p.orbit_target == "null":
                lay.prop(p, "orbit_margin")
            lay.prop(p, "orbit_turns")
            lay.prop(p, "orbit_start")
            box = lay.box()
            box.label(text="Animacja: najedź na pole (odległość, wysokość,", icon="KEYFRAME")
            box.label(text="okrążenia, grubość linii...) i wciśnij I.")
            box.label(text="Odległość animuje się przy włączonym 'ręcznie'.")
        else:
            lay.prop(p, "chase_back")
            lay.prop(p, "chase_up")
            lay.prop(p, "chase_ahead")
        lay.operator("hrf.apply", icon="FILE_REFRESH")


class HRF_PT_look(_Sub, bpy.types.Panel):
    bl_label = "Wygląd (na żywo)"
    bl_idname = "HRF_PT_look"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.prop(p, "line_scale")
        lay.prop(p, "done_scale")
        lay.prop(p, "arrow_scale")
        lay.prop(p, "route_color")
        lay.prop(p, "done_color")
        lay.prop(p, "arrow_color")
        lay.prop(p, "terrain_color")
        lay.prop(p, "own_world")
        lay.prop(p, "sun_azimuth")
        lay.prop(p, "sun_elev")
        lay.prop(p, "sun_strength")


class HRF_PT_terrain(_Sub, bpy.types.Panel):
    bl_label = "Wysokość i teren"
    bl_idname = "HRF_PT_terrain"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.prop(p, "exag")
        lay.prop(p, "use_terrain")
        if p.use_terrain:
            lay.prop(p, "terrain_path")
            lay.prop(p, "drape")
            lay.prop(p, "terrain_skip")
            lay.prop(p, "terrain_style")
            if p.terrain_style == "streets":
                lay.prop(p, "streets_texture")
                lay.operator("hrf.reload_texture", icon="FILE_REFRESH")
                sb = lay.box()
                sb.label(text="Mapa ulic (cały teren, internet)", icon="WORLD")
                col2 = sb.column(align=True)
                row = col2.row(align=True)
                row.prop(p, "st_roads")
                row.prop(p, "st_cycle")
                row = col2.row(align=True)
                row.prop(p, "st_rivers")
                row.prop(p, "st_water")
                row = col2.row(align=True)
                row.prop(p, "st_bound")
                row.prop(p, "st_small")
                sb.prop(p, "streets_width")
                row = sb.row(align=True)
                row.prop(p, "streets_save_png")
                row.prop(p, "streets_save_vector")
                row = sb.row(align=True)
                row.operator("hrf.streets_command", icon="COPYDOWN")
                row.operator("hrf.streets_run", icon="PLAY")
                if p.streets_cmd:
                    sb.prop(p, "streets_cmd", text="")
                if p.streets_status:
                    sb.label(text=p.streets_status)
            if p.terrain_style == "contours":
                lay.prop(p, "terrain_texture")
                lay.operator("hrf.reload_texture", icon="FILE_REFRESH")
                gb = lay.box()
                gb.label(text="Tekstura globalna (cały teren)", icon="WORLD")
                row = gb.row(align=True)
                row.prop(p, "global_minor")
                row.prop(p, "global_major")
                gb.prop(p, "global_width")
                row = gb.row(align=True)
                row.prop(p, "global_save_png")
                row.prop(p, "global_save_vector")
                row = gb.row(align=True)
                row.operator("hrf.global_command", icon="COPYDOWN")
                row.operator("hrf.global_run", icon="PLAY")
                if p.global_cmd:
                    gb.prop(p, "global_cmd", text="")
                if p.global_status:
                    gb.label(text=p.global_status)
                box = lay.box()
                box.label(text="Tekstura lokalna (wycinek z okna czasu)", icon="TEXTURE")
                box.prop(p, "local_use")
                box.prop(p, "local_margin")
                box.prop(p, "local_texture")
                row = box.row(align=True)
                row.prop(p, "local_save_png")
                row.prop(p, "local_save_vector")
                row = box.row(align=True)
                row.operator("hrf.local_command", icon="COPYDOWN")
                row.operator("hrf.local_run", icon="PLAY")
                if p.local_cmd:
                    box.prop(p, "local_cmd", text="")
                if p.local_status:
                    box.label(text=p.local_status)
        lay.operator("hrf.rebuild", icon="FILE_REFRESH")


class HRF_PT_nulls(_Sub, bpy.types.Panel):
    bl_label = "Null w współrzędnych"
    bl_idname = "HRF_PT_nulls"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.prop(p, "null_coords", text="")
        lay.prop(p, "null_name", text="Nazwa")
        row = lay.row(align=True)
        row.operator("hrf.add_null", icon="EMPTY_AXIS")
        row.operator("hrf.pivot_to_coords", icon="PIVOT_BOUNDBOX")
        lay.label(text="np. 49.5791, 20.0501  (+ ewent. wysokość m)")


class HRF_PT_render(_Sub, bpy.types.Panel):
    bl_label = "Render"
    bl_idname = "HRF_PT_render"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.prop(p, "output_dir")
        lay.operator("hrf.render_frame", icon="RENDER_STILL")
        lay.operator("hrf.render_anim", icon="RENDER_ANIMATION")


CLASSES = [HRFProps, HRF_OT_rebuild, HRF_OT_apply, HRF_OT_set_speed, HRF_OT_select_pivot, HRF_OT_center_pivot, HRF_OT_add_null, HRF_OT_pivot_to_coords,
           HRF_OT_local_command, HRF_OT_local_run, HRF_OT_global_command, HRF_OT_global_run, HRF_OT_streets_command, HRF_OT_streets_run, HRF_OT_reload_texture, HRF_OT_render_frame, HRF_OT_render_anim,
           HRF_PT_main, HRF_PT_time, HRF_PT_camera, HRF_PT_look, HRF_PT_terrain, HRF_PT_nulls, HRF_PT_render]


def unregister():
    if hasattr(bpy.types.Scene, "hrf"):
        try:
            del bpy.types.Scene.hrf
        except Exception:
            pass
    for cls in reversed(CLASSES):
        old = getattr(bpy.types, cls.__name__, None)
        for c in (old, cls):
            if c is None:
                continue
            try:
                bpy.utils.unregister_class(c)
            except Exception:
                pass


def register():
    unregister()      # ponowne uruchomienie skryptu odświeża panel zamiast go dublować
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hrf = PointerProperty(type=HRFProps)
    old = bpy.app.driver_namespace.get("hrf_bridge_timer")
    if old is not None:
        try:
            bpy.app.timers.unregister(old)
        except Exception:
            pass
    bpy.app.driver_namespace["hrf_bridge_timer"] = _bridge_timer
    bpy.app.timers.register(_bridge_timer, first_interval=1.0, persistent=True)


def show_sidebar():
    """Otwiera pasek boczny (klawisz N) we wszystkich oknach 3D, żeby panel HRF od razu był widoczny."""
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    for sp in area.spaces:
                        if sp.type == "VIEW_3D":
                            sp.show_region_ui = True
    except Exception:
        pass


def run(overrides=None):
    """Rejestruje panel, ustawia wartości z CONFIG (+ overrides) i buduje scenę."""
    register()
    show_sidebar()
    scene = bpy.context.scene
    STATE["busy"] = True
    try:
        props_from_config(scene, overrides)
    finally:
        STATE["busy"] = False
    return build_scene(scene)


def attach(scene=None):
    """Podłącza panel HRF i most z programem HRF.pyw do sceny, która już jest zbudowana (otwarty zapisany .blend), BEZ przebudowy obiektów.
    Odtwarza z pliku danych tylko obliczenia (trasa, teren), a obiekty bierze ze sceny po nazwach."""
    scene = scene or bpy.context.scene
    register()
    show_sidebar()
    names = {"route": "HRF_Route", "done": "HRF_RouteDone", "arrow": "HRF_Arrow", "cam": "HRF_Camera", "pivot": "HRF_Pivot",
             "rig": "HRF_Rig", "terrain": "HRF_Terrain", "sun": "HRF_Sun"}
    objs = {k: bpy.data.objects.get(v) for k, v in names.items()}
    objs = {k: v for k, v in objs.items() if v is not None}
    if "route" not in objs:
        raise RuntimeError("To nie jest scena HRF (brak obiektu HRF_Route) – uruchom skrypt bez HRF_ATTACH, żeby ją zbudować.")
    STATE["busy"] = True
    try:
        cfg = cfg_from_props(scene)
        env_data = os.environ.get("HRF_DATA", "")
        dp = bpy.path.abspath(scene.hrf.data_path) if scene.hrf.data_path else ""
        if not (dp and os.path.isfile(dp)) and env_data and os.path.isfile(env_data):
            cfg["DATA_PATH"] = env_data
        T = build_track(cfg, load_raw(cfg))
    finally:
        STATE["busy"] = False
    STATE["objs"], STATE["T"] = objs, T
    STATE["exag_built"] = scene.get("hrf_exag_built", cfg["EXAG"])
    print("[HRF] Dołączono do istniejącej sceny (bez przebudowy). Dane: %s" % T["path"])
    return T


def _attach_on_load():
    try:
        attach(bpy.context.scene)
    except Exception:
        traceback.print_exc()
    return None


# ---------------------------------------------------------------- uruchomienie z wiersza poleceń
def _cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ov, extra, i = {}, {}, 0
    keymap = {"--data": ("DATA_PATH", str), "--mode": ("MODE", str), "--start": ("START_S", float),
              "--duration": ("DURATION_S", float), "--exag": ("EXAG", float), "--full-seconds": ("FULL_SECONDS", float),
              "--fps": ("FPS", int), "--cam": ("CAM_MODE", str), "--turns": ("ORBIT_TURNS", float),
              "--orbit-margin": ("ORBIT_MARGIN", float), "--orbit-elev": ("ORBIT_ELEV_DEG", float),
              "--res-x": ("RES_X", int), "--res-y": ("RES_Y", int), "--terrain-style": ("TERRAIN_STYLE", str), "--orbit-target": ("ORBIT_TARGET", str), "--speed": ("SPEED", float),
              "--start-min": ("START_MIN", float), "--end-min": ("END_MIN", float),
              "--orbit-distance": ("ORBIT_DISTANCE_M", float),
              "--terrain-texture": ("TERRAIN_TEXTURE", str)}
    while i < len(argv):
        a = argv[i]
        if a in keymap and i + 1 < len(argv):
            ov[keymap[a][0]] = keymap[a][1](argv[i + 1])
            i += 2
        elif a in ("--render-frame", "--render-out", "--save", "--set") and i + 1 < len(argv):
            extra.setdefault(a, []).append(argv[i + 1])
            i += 2
        elif a in ("--dark", "--quick-test"):
            extra[a] = ["1"]
            i += 1
        else:
            i += 1
    return ov, extra


if __name__ == "__main__" and os.environ.get("HRF_ATTACH") == "1" and bpy.data.filepath and bpy.data.objects.get("HRF_Route"):
    attach(bpy.context.scene)
elif __name__ == "__main__":
    ov, extra = _cli() if "--" in sys.argv else ({}, {})
    run(ov)
    if "--dark" in extra:
        sc = bpy.context.scene
        sc.render.film_transparent = False
        w = bpy.data.worlds.new("HRF_dark")
        try:
            w.use_nodes = True
        except Exception:
            pass
        bg = w.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value = (0.04, 0.045, 0.06, 1.0)
        sc.world = w
    if "--render-out" in extra:
        sc = bpy.context.scene
        sc.frame_set(int(extra.get("--render-frame", [sc.frame_start])[0]))
        sc.render.filepath = extra["--render-out"][0]
        bpy.ops.render.render(write_still=True)
    if "--save" in extra:
        bpy.ops.wm.save_as_mainfile(filepath=extra["--save"][0])

if __name__ != "__main__" and bpy.data.filepath and bpy.data.objects.get("HRF_Route"):
    # skrypt z zaznaczonym „Register” wczytany razem z zapisanym .blend: panel i most wracają bez przebudowy sceny
    bpy.app.timers.register(_attach_on_load, first_interval=0.5)
