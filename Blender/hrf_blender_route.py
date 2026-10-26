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
import json, math, os, sys, glob, zlib, base64, array, traceback
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from mathutils import Vector

CONFIG = {
    "DATA_PATH": "",          # plik *_hrf.json; puste = najnowszy *_hrf.json w folderze data/ obok repo
    "MODE": "full",           # "full" = cała jazda skompresowana do FULL_SECONDS, "clip" = okno klipu w czasie rzeczywistym
    "START_S": 0.0,           # clip: sekunda aktywności, od której zaczyna się klip
    "DURATION_S": 60.0,       # clip: długość klipu w sekundach
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
    "TERRAIN_DRAPE": True,    # trasa przyklejona do terenu (wysokość z mapy terenu zamiast z GPS)
    "TERRAIN_SMOOTH_S": 2.0,  # wygładzanie wysokości trasy na terenie (połowa okna, s)
    "ROUTE_COLOR": (0.35, 0.65, 1.0),
    "DONE_COLOR": (1.0, 1.0, 1.0),
    "ARROW_COLOR": (1.0, 0.38, 0.05),
    "SUN_AZIMUTH_DEG": 135.0, # kierunek, z którego świeci słońce (stopnie od osi X; 135 = północny zachód)
    "SUN_ELEV_DEG": 40.0,
    "SUN_STRENGTH": 3.0,
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
            cands = glob.glob(os.path.join(sub, "*_hrf.json"))
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
    ("MODE", "mode"), ("FULL_SECONDS", "full_seconds"), ("START_S", "start_s"), ("DURATION_S", "duration_s"),
    ("FPS", "fps"), ("RES_X", "res_x"), ("RES_Y", "res_y"), ("EXAG", "exag"), ("CAM_MODE", "cam_mode"),
    ("ORBIT_ELEV_DEG", "orbit_elev"), ("ORBIT_MARGIN", "orbit_margin"), ("ORBIT_TURNS", "orbit_turns"),
    ("ORBIT_START_DEG", "orbit_start"), ("CAM_LENS_MM", "lens"), ("CAM_BACK_M", "chase_back"),
    ("CAM_UP_M", "chase_up"), ("CAM_LOOK_AHEAD_M", "chase_ahead"), ("TERRAIN_DRAPE", "drape"),
    ("TERRAIN_SKIP", "terrain_skip"), ("TERRAIN_COLOR", "terrain_color"), ("ROUTE_COLOR", "route_color"),
    ("DONE_COLOR", "done_color"), ("ARROW_COLOR", "arrow_color"), ("SUN_AZIMUTH_DEG", "sun_azimuth"),
    ("SUN_ELEV_DEG", "sun_elev"), ("SUN_STRENGTH", "sun_strength"), ("DONE_SCALE", "done_scale"),
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
    s = cfg["FULL_SCALE"] if (full and not orbit) else 1.0
    px_per_rad = lens / 36.0 * max(rx, ry)                                     # sensor 36 mm na dłuższym boku kadru
    half_short = (36.0 * min(rx, ry) / float(max(rx, ry)) / 2.0) / lens       # tan(pół kąta widzenia) na krótszym boku
    out = {"s": s, "orbit": orbit, "orbit_dist": 0.0}
    if orbit:
        span = math.sqrt(E["w"] ** 2 + E["h"] ** 2 + E["zr"] ** 2) / 2.0
        d = span / half_short * cfg["ORBIT_MARGIN"]
        out["orbit_dist"] = d
        radius = cfg["ORBIT_TUBE_PX"] * d / px_per_rad
        arrow_len = cfg["ORBIT_ARROW_PX"] * d / px_per_rad
    else:
        radius = cfg["TUBE_RADIUS_M"] * s
        arrow_len = cfg["ARROW_LEN_M"] * s
    out["radius"] = radius * p.line_scale
    out["arrow_len"] = arrow_len * p.arrow_scale
    return out


def time_map(cfg, T):
    fps = int(cfg["FPS"])
    if cfg["MODE"] == "full":
        t0, nsec = 0.0, cfg["FULL_SECONDS"]
        speed = (T["n"] - 1) / float(nsec)
    else:
        t0, nsec, speed = cfg["START_S"], cfg["DURATION_S"], 1.0
    return t0, speed, max(2, int(round(nsec * fps))), fps


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
    me.materials.append(diffuse_material("HRF_terrain", cfg["TERRAIN_COLOR"]))
    ob = bpy.data.objects.new("HRF_Terrain", me)
    col.objects.link(ob)
    return ob, len(verts)


def make_light(col, scene):
    """Słońce + słabe światło otoczenia, żeby bryła terenu była czytelna."""
    li = bpy.data.lights.new("HRF_Sun", "SUN")
    ob = bpy.data.objects.new("HRF_Sun", li)
    col.objects.link(ob)
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


def key_camera(scene, cfg, T, sz):
    """Kamera: orbit (krąży wokół całej trasy) albo chase (za strzałką, z bezwładnością)."""
    cam = STATE["objs"]["cam"]
    cam.animation_data_clear()
    t0, speed, nf, fps = time_map(cfg, T)
    orbit, s = sz["orbit"], sz["s"]
    E = T["ext"]
    cd = cam.data
    cd.lens = cfg["CAM_LENS_MM"]
    cd.clip_start = 0.5 * s if not orbit else max(1.0, sz["orbit_dist"] * 0.01)
    cd.clip_end = 400000.0 if not orbit else sz["orbit_dist"] * 4.0
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
    el = math.radians(cfg["ORBIT_ELEV_DEG"])
    az0 = math.radians(cfg["ORBIT_START_DEG"])
    pivot = Vector((E["cx"], E["cy"], E["cz"]))
    cam_pos = cam_phi = prev_eul = None
    for fi in range(nf):
        fr = 1 + fi
        t = max(0.0, min(T["n"] - 1.0, t0 + fi / float(fps) * speed))
        P = Vector((at(T["x"], t), at(T["y"], t), at(T["z"], t)))
        if orbit:
            az = az0 + 2.0 * math.pi * cfg["ORBIT_TURNS"] * fi / float(nf)
            cam_pos = pivot + Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))) * sz["orbit_dist"]
            look = pivot
        else:
            target_phi = at(T["phi_cam"], t)
            cam_phi = target_phi if cam_phi is None else cam_phi + (target_phi - cam_phi) * a_yaw
            fwd = Vector((math.cos(cam_phi), math.sin(cam_phi), 0.0))
            want = P - fwd * back + Vector((0.0, 0.0, up))
            cam_pos = want if cam_pos is None else cam_pos + (want - cam_pos) * a_pos
            look = P + fwd * ahead
        cam_use = cam_pos.copy()
        if T["terrain"] and not orbit:
            cam_use.z = max(cam_use.z, terr_z(cam_use.x, cam_use.y) + clearance)    # kamera nie wchodzi pod teren
        cq = (look - cam_use).to_track_quat("-Z", "Y")
        cam.location = cam_use
        cam.rotation_euler = cq.to_euler("XYZ", prev_eul) if prev_eul else cq.to_euler("XYZ")
        prev_eul = cam.rotation_euler.copy()
        cam.keyframe_insert("location", frame=fr)
        cam.keyframe_insert("rotation_euler", frame=fr)
    prefs.keyframe_new_interpolation_type = old


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
    """Grubość linii, rozmiar strzałki (na żywo, bez przeliczania animacji)."""
    o, T = STATE["objs"], STATE["T"]
    if not o or not T:
        return
    cfg = cfg_from_props(scene)
    p = scene.hrf
    sz = calc_sizes(cfg, T, p)
    r = sz["radius"]
    lift = r * 1.6 if T["terrain"] else 0.0
    o["route"].data.bevel_depth = r
    o["done"].data.bevel_depth = r * cfg["DONE_SCALE"]
    for key in ("route", "done"):                       # rura leży na terenie, nie w nim
        sp = o[key].data.splines[0]
        for i, bp in enumerate(STATE["base_pts"]):
            sp.points[i].co = (bp[0], bp[1], bp[2] + lift, 1.0)
    o["arrow"].scale = (sz["arrow_len"],) * 3
    z0 = 0.15 + (lift / sz["arrow_len"] if sz["arrow_len"] > 0 else 0.0)
    for v, nv in zip(o["arrow"].data.vertices, arrow_verts(z0)):
        v.co = nv
    o["arrow"].data.update()


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
        o["sun"] = make_light(col, scene)
        terrain_info = {"file": T["terrain_path"], "vertices": nv, "draped": bool(cfg["TERRAIN_DRAPE"])}
    STATE["base_pts"] = T["pts"]
    o["route"] = make_curve("HRF_Route", T["pts"], sz["radius"], mat_route, col)
    o["done"] = make_curve("HRF_RouteDone", T["pts"], sz["radius"] * cfg["DONE_SCALE"], mat_done, col)
    o["arrow"] = make_arrow("HRF_Arrow", mat_arrow, col)
    cam = bpy.data.objects.new("HRF_Camera", bpy.data.cameras.new("HRF_Camera"))
    col.objects.link(cam)
    o["cam"] = cam
    STATE["objs"], STATE["T"] = o, T
    apply_look(scene)
    apply_sun(scene)
    apply_colors(scene)
    key_objects(scene, cfg, T)
    key_camera(scene, cfg, T, sz)
    t0, speed, nf, fps = time_map(cfg, T)
    engine = apply_render_settings(scene, cfg, nf)
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
    sz = calc_sizes(cfg, T, scene.hrf)
    apply_look(scene)
    key_objects(scene, cfg, T)
    key_camera(scene, cfg, T, sz)
    t0, speed, nf, fps = time_map(cfg, T)
    apply_render_settings(scene, cfg, nf)
    scene.frame_set(1)
    scene.hrf.info = "%d klatek | %.1f km" % (nf, T["length_m"] / 1000.0)
    return nf


# ---------------------------------------------------------------- panel (właściwości, operatory, UI)
def _upd_look(self, context):
    if STATE["busy"]:
        return
    try:
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


class HRFProps(bpy.types.PropertyGroup):
    info: StringProperty(name="Stan", default="")
    data_path: StringProperty(name="Plik danych", subtype="FILE_PATH", default="",
                              description="Plik *_hrf.json; puste = najnowszy w folderze data/ obok repo")
    mode: EnumProperty(name="Tryb", default="full", items=[
        ("full", "Cała jazda (przelot)", "Cała aktywność skompresowana do podanej liczby sekund"),
        ("clip", "Klip (czas rzeczywisty)", "Okno klipu: początek + długość w sekundach aktywności")])
    full_seconds: FloatProperty(name="Długość przelotu (s)", default=40.0, min=1.0, soft_max=300.0)
    start_s: FloatProperty(name="Początek klipu (s)", default=0.0, min=0.0)
    duration_s: FloatProperty(name="Długość klipu (s)", default=60.0, min=0.1, soft_max=600.0)
    fps: IntProperty(name="FPS", default=30, min=1, max=120)
    res_x: IntProperty(name="Szerokość", default=1920, min=16, max=16384)
    res_y: IntProperty(name="Wysokość", default=1080, min=16, max=16384)
    cam_mode: EnumProperty(name="Kamera", default="orbit", items=[
        ("orbit", "Orbita (cała trasa)", "Cała trasa w kadrze, kamera krąży wokół"),
        ("chase", "Za strzałką", "Kamera jedzie za strzałką")])
    orbit_elev: FloatProperty(name="Wysokość kamery (°)", default=40.0, min=5.0, max=89.0)
    orbit_margin: FloatProperty(name="Zapas w kadrze", default=0.9, min=0.3, max=3.0,
                                description="Mniej = kamera bliżej; poniżej ok. 0.8 trasa może wychodzić poza kadr")
    orbit_turns: FloatProperty(name="Okrążenia", default=1.0, min=-10.0, max=10.0)
    orbit_start: FloatProperty(name="Start orbity (°)", default=-90.0)
    lens: FloatProperty(name="Ogniskowa (mm)", default=30.0, min=8.0, max=200.0)
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
    terrain_color: FloatVectorProperty(name="Teren", subtype="COLOR", size=3, min=0.0, max=1.0,
                                       default=(0.17, 0.19, 0.22), update=_upd_colors)
    sun_azimuth: FloatProperty(name="Słońce: azymut (°)", default=135.0, update=_upd_sun)
    sun_elev: FloatProperty(name="Słońce: wysokość (°)", default=40.0, min=1.0, max=90.0, update=_upd_sun)
    sun_strength: FloatProperty(name="Słońce: siła", default=3.0, min=0.0, soft_max=10.0, update=_upd_sun)
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
    bl_label = "Zastosuj czas i kamerę"
    bl_description = "Szybko przelicza animację (czas, kamera, strzałka, przejechany odcinek) bez przebudowy terenu"

    def execute(self, context):
        return _run_op(self, lambda: apply_time_camera(context.scene), lambda n: "Animacja: %d klatek" % n)


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
        lay.prop(p, "mode")
        if p.mode == "full":
            lay.prop(p, "full_seconds")
        else:
            lay.prop(p, "start_s")
            lay.prop(p, "duration_s")
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
            lay.prop(p, "orbit_elev")
            lay.prop(p, "orbit_margin")
            lay.prop(p, "orbit_turns")
            lay.prop(p, "orbit_start")
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
        lay.operator("hrf.rebuild", icon="FILE_REFRESH")


class HRF_PT_render(_Sub, bpy.types.Panel):
    bl_label = "Render"
    bl_idname = "HRF_PT_render"

    def draw(self, context):
        p = context.scene.hrf
        lay = self.layout
        lay.prop(p, "output_dir")
        lay.operator("hrf.render_frame", icon="RENDER_STILL")
        lay.operator("hrf.render_anim", icon="RENDER_ANIMATION")


CLASSES = [HRFProps, HRF_OT_rebuild, HRF_OT_apply, HRF_OT_render_frame, HRF_OT_render_anim,
           HRF_PT_main, HRF_PT_time, HRF_PT_camera, HRF_PT_look, HRF_PT_terrain, HRF_PT_render]


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


# ---------------------------------------------------------------- uruchomienie z wiersza poleceń
def _cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ov, extra, i = {}, {}, 0
    keymap = {"--data": ("DATA_PATH", str), "--mode": ("MODE", str), "--start": ("START_S", float),
              "--duration": ("DURATION_S", float), "--exag": ("EXAG", float), "--full-seconds": ("FULL_SECONDS", float),
              "--fps": ("FPS", int), "--cam": ("CAM_MODE", str), "--turns": ("ORBIT_TURNS", float),
              "--orbit-margin": ("ORBIT_MARGIN", float), "--orbit-elev": ("ORBIT_ELEV_DEG", float),
              "--res-x": ("RES_X", int), "--res-y": ("RES_Y", int)}
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


if __name__ == "__main__":
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
