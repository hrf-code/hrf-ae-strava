# -*- coding: utf-8 -*-
"""
hrf_blender_route.py  —  buduje w Blenderze scenę z trasy jazdy: ścieżka z prawdziwą wysokością (oś Z),
strzałka jadąca po trasie i kamera podążająca za strzałką. Dane z tego samego pliku *_hrf.json co panel AE.

Uruchomienie w Blenderze:  zakładka Scripting -> Open -> ten plik -> ustaw DATA_PATH (albo zostaw puste)
-> Run Script.  Scena powstaje w kolekcji "HRF" (kolejne uruchomienie ją odtwarza od zera).
Render nakładki:  Render -> Render Animation (PNG z przezroczystością w folderze OUTPUT_DIR), potem w AE
jako warstwa nad wideo.

Z wiersza poleceń (test bez okna):
    blender -b --python Blender/hrf_blender_route.py -- --start 3600 --duration 20 --render-frame 100 --render-out test.png

Tryby czasu:
  clip  - okno klipu w czasie rzeczywistym (START_S + DURATION_S, jak w panelu AE),
  full  - cała jazda skompresowana do FULL_SECONDS sekund (przelot; rozmiary i kamera skalowane FULL_SCALE).

Dane z GPS są prywatne: plik sceny (.blend) z trasą nie powinien trafiać do publicznego repo.
"""
import bpy
import json, math, os, sys, glob, zlib, base64, array
from mathutils import Vector

CONFIG = {
    "DATA_PATH": "",          # plik *_hrf.json; puste = najnowszy *_hrf.json w folderze data/ obok repo
    "MODE": "full",           # "full" = cała jazda skompresowana do FULL_SECONDS, "clip" = okno klipu w czasie rzeczywistym
    "START_S": 0.0,           # clip: sekunda aktywności, od której zaczyna się klip
    "DURATION_S": 60.0,       # clip: długość klipu w sekundach
    "FULL_SECONDS": 40.0,     # full: długość przelotu w sekundach (klatki = FULL_SECONDS * FPS)
    "FULL_SCALE": 50.0,       # full: mnożnik grubości linii, strzałki i odległości kamery
    "FPS": 30,
    "RES_X": 1080,
    "RES_Y": 1920,
    "EXAG": 3.0,              # przewyższenie (1 = skala rzeczywista, 3 = wyraźnie w widoku z daleka)
    "POINT_MIN_M": 4.0,       # minimalny odstęp punktów krzywej trasy, m
    "SMOOTH_XY_S": 4.0,       # wygładzanie pozycji (połowa okna, s)
    "SMOOTH_Z_S": 12.0,       # wygładzanie wysokości (połowa okna, s)
    "CAM_MODE": "orbit",      # "orbit" = cała trasa w kadrze, kamera krąży wokół; "chase" = kamera za strzałką
    "TUBE_RADIUS_M": 1.2,     # chase: promień rury trasy, m
    "DONE_SCALE": 1.5,        # o ile grubsza jest przejechana część
    "ARROW_LEN_M": 9.0,       # chase: długość strzałki, m
    "ARROW_LIFT_M": 2.0,      # chase: strzałka nad trasą, m
    "CAM_BACK_M": 60.0,       # chase: kamera za strzałką, m
    "CAM_UP_M": 45.0,         # chase: kamera nad strzałką, m
    "CAM_LOOK_AHEAD_M": 20.0, # chase: kamera patrzy na punkt przed strzałką, m
    "CAM_LENS_MM": 30.0,
    "ORBIT_TURNS": 1.0,       # orbit: ile pełnych okrążeń w czasie animacji
    "ORBIT_ELEV_DEG": 40.0,   # orbit: wysokość kamery nad horyzontem (stopnie)
    "ORBIT_START_DEG": -90.0, # orbit: początkowy azymut kamery (stopnie, -90 = od południa)
    "ORBIT_MARGIN": 1.15,     # orbit: zapas wokół trasy w kadrze
    "ORBIT_TUBE_PX": 3.0,     # orbit: promień linii trasy w pikselach
    "ORBIT_ARROW_PX": 70.0,   # orbit: długość strzałki w pikselach
    "CAM_POS_TAU_S": 0.6,     # bezwładność pozycji kamery (s)
    "CAM_YAW_TAU_S": 1.8,     # bezwładność obrotu kamery (s)
    "TERRAIN": "auto",        # "auto" = data/hrf_terrain.json obok danych trasy, "off" = bez terenu, albo pełna ścieżka pliku
    "TERRAIN_SKIP": 1,        # użyj co N-tego oczka siatki terenu (1 = wszystkie; większe = lżejsza siatka)
    "TERRAIN_COLOR": (0.17, 0.19, 0.22),
    "TERRAIN_DRAPE": True,    # trasa przyklejona do terenu (wysokość z mapy terenu zamiast z GPS)
    "TERRAIN_SMOOTH_S": 2.0,  # wygładzanie wysokości trasy na terenie (połowa okna, s) – małe, żeby trasa nie odpływała od powierzchni
    "OUTPUT_DIR": "//hrf_render/",
}

EARTH_R = 6371008.8


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
        if not os.path.isfile(path):
            raise RuntimeError("Nie ma pliku DATA_PATH: " + path)
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
    raise RuntimeError("Nie znaleziono pliku *_hrf.json (szukano w: %s). Ustaw CONFIG['DATA_PATH'] na pełną ścieżkę pliku."
                       % (", ".join(searched) or "brak folderów"))


def find_terrain(cfg, data_path):
    t = cfg["TERRAIN"]
    if not t or t == "off":
        return None
    if t != "auto":
        if not os.path.isfile(t):
            raise RuntimeError("Nie ma pliku TERRAIN: " + t)
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


def build_track(cfg):
    path = find_data(cfg["DATA_PATH"])
    with open(path, encoding="utf-8-sig") as f:
        D = json.load(f)
    S = D["samples"]
    ts, lat, lng, elev = S["time_s"], S["lat"], S["lng"], S["elev_m"]
    n = int(ts[-1]) + 1
    lat0 = (min(lat) + max(lat)) / 2.0
    lng0 = (min(lng) + max(lng)) / 2.0
    kx = math.radians(1.0) * EARTH_R * math.cos(math.radians(lat0))
    ky = math.radians(1.0) * EARTH_R
    xs = interp_series(ts, [(v - lng0) * kx for v in lng], n)
    ys = interp_series(ts, [(v - lat0) * ky for v in lat], n)
    xs, ys = smooth(xs, cfg["SMOOTH_XY_S"]), smooth(ys, cfg["SMOOTH_XY_S"])
    tpath = find_terrain(cfg, path)
    terr = load_terrain(tpath) if tpath else None
    if terr and cfg["TERRAIN_DRAPE"]:
        # wysokość trasy z mapy terenu (zamiast barometru GPS), żeby trasa leżała na powierzchni terenu
        zs = [terrain_at(terr, lat0 + ys[i] / ky, lng0 + xs[i] / kx) for i in range(n)]
    else:
        zs = interp_series(ts, elev, n)
    zs = smooth(zs, cfg["TERRAIN_SMOOTH_S"] if (terr and cfg["TERRAIN_DRAPE"]) else cfg["SMOOTH_Z_S"])
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
    return {"path": path, "name": D.get("name", ""), "n": n, "x": xs, "y": ys, "z": zs, "ext": ext,
            "phi_arrow": phi_arrow, "theta_arrow": theta_arrow, "phi_cam": phi_cam,
            "pts": pts, "prog": prog, "length_m": total, "zmax": max(zs),
            "terrain": terr, "terrain_path": tpath, "zmin_ref": zmin, "proj": (lat0, lng0, kx, ky)}


# ---------------------------------------------------------------- scena
def clean_scene():
    col = bpy.data.collections.get("HRF")
    if col:
        for o in list(col.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.curves, bpy.data.meshes, bpy.data.cameras, bpy.data.materials):
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


def make_arrow(name, length, mat, col):
    L = length
    W = L * 0.7
    h = L * 0.14
    top = [(0, L * 0.5), (W * 0.5, -L * 0.5), (0, -L * 0.2), (-W * 0.5, -L * 0.5)]
    verts = [(x, y, 0.0) for x, y in top] + [(x, y, h) for x, y in top]
    faces = [(4, 5, 6), (4, 6, 7),            # góra
             (0, 2, 1), (0, 3, 2)]            # dół
    for i in range(4):                        # boki
        j = (i + 1) % 4
        faces.append((i, j, 4 + j, 4 + i))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
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
    mat = bpy.data.materials.new("HRF_terrain")
    try:
        mat.use_nodes = True
    except Exception:
        pass
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    c = cfg["TERRAIN_COLOR"]
    bsdf.inputs["Color"].default_value = (c[0], c[1], c[2], 1.0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    mat.diffuse_color = (c[0], c[1], c[2], 1.0)
    me.materials.append(mat)
    ob = bpy.data.objects.new("HRF_Terrain", me)
    col.objects.link(ob)
    return ob, len(verts)


def make_light(col, scene):
    """Słońce z północnego zachodu (jak w kartografii) + słabe światło otoczenia, żeby bryła terenu była czytelna."""
    li = bpy.data.lights.new("HRF_Sun", "SUN")
    li.energy = 3.0
    ob = bpy.data.objects.new("HRF_Sun", li)
    el, az = math.radians(40.0), math.radians(135.0)
    to_sun = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    ob.rotation_euler = (-to_sun).to_track_quat("-Z", "Y").to_euler()
    col.objects.link(ob)
    w = bpy.data.worlds.new("HRF_World")
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


def set_engine(scene):
    ids = [e.identifier for e in type(scene.render).bl_rna.properties["engine"].enum_items]
    for cand in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        if cand in ids:
            scene.render.engine = cand
            return cand
    return scene.render.engine


def run(overrides=None):
    cfg = dict(CONFIG)
    if overrides:
        cfg.update(overrides)
    T = build_track(cfg)
    scene = bpy.context.scene
    col = clean_scene()

    full = cfg["MODE"] == "full"
    orbit = cfg["CAM_MODE"] == "orbit"
    s = cfg["FULL_SCALE"] if (full and not orbit) else 1.0         # skala obiektów i kamery (chase)
    fps = int(cfg["FPS"])
    if full:
        t0, nsec = 0.0, cfg["FULL_SECONDS"]
        speed = (T["n"] - 1) / float(nsec)
    else:
        t0, nsec, speed = cfg["START_S"], cfg["DURATION_S"], 1.0
    nf = max(2, int(round(nsec * fps)))
    f0 = 1

    mat_route = emission_material("HRF_route", (0.35, 0.65, 1.0), 1.0)
    mat_done = emission_material("HRF_done", (1.0, 1.0, 1.0), 1.0)
    mat_arrow = emission_material("HRF_arrow", (1.0, 0.38, 0.05), 1.0)

    # orbit: odległość kamery dobrana tak, by cała trasa mieściła się w kadrze; grubości linii w pikselach
    E = T["ext"]
    rx, ry = int(cfg["RES_X"]), int(cfg["RES_Y"])
    lens = cfg["CAM_LENS_MM"]
    px_per_rad = lens / 36.0 * max(rx, ry)                      # sensor 36 mm na dłuższym boku kadru
    half_short = (36.0 * min(rx, ry) / float(max(rx, ry)) / 2.0) / lens   # tan(pół kąta widzenia) na krótszym boku
    if orbit:
        span = math.sqrt(E["w"] ** 2 + E["h"] ** 2 + (E["zr"] * 1.0) ** 2) / 2.0
        orbit_dist = span / half_short * cfg["ORBIT_MARGIN"]
        radius = cfg["ORBIT_TUBE_PX"] * orbit_dist / px_per_rad
        arrow_len = cfg["ORBIT_ARROW_PX"] * orbit_dist / px_per_rad
        lift = arrow_len * 0.15
    else:
        radius = cfg["TUBE_RADIUS_M"] * s
        arrow_len = cfg["ARROW_LEN_M"] * s
        lift = cfg["ARROW_LIFT_M"] * s
    pts = T["pts"]
    terrain_info = None
    if T["terrain"]:
        pts = [(p[0], p[1], p[2] + radius * 1.6) for p in pts]      # rura leży na terenie, nie w nim
        lift += radius * 1.6
        tob, nv = make_terrain(T, cfg, col)
        make_light(col, scene)
        terrain_info = {"file": T["terrain_path"], "vertices": nv, "draped": bool(cfg["TERRAIN_DRAPE"])}
    route = make_curve("HRF_Route", pts, radius, mat_route, col)
    done = make_curve("HRF_RouteDone", pts, radius * cfg["DONE_SCALE"], mat_done, col)
    arrow = make_arrow("HRF_Arrow", arrow_len, mat_arrow, col)
    camdata = bpy.data.cameras.new("HRF_Camera")
    camdata.lens = cfg["CAM_LENS_MM"]
    camdata.clip_start = 0.5 * s if not orbit else max(1.0, orbit_dist * 0.01)
    camdata.clip_end = 400000.0 if not orbit else orbit_dist * 4.0
    cam = bpy.data.objects.new("HRF_Camera", camdata)
    col.objects.link(cam)

    # ----- animacja (klatka po klatce; interpolacja liniowa dla nowych kluczy)
    prefs = bpy.context.preferences.edit
    old_interp = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = "LINEAR"
    back, up, ahead = cfg["CAM_BACK_M"] * s, cfg["CAM_UP_M"] * s, cfg["CAM_LOOK_AHEAD_M"] * s
    dt = 1.0 / fps
    a_pos = 1.0 - math.exp(-dt / max(1e-3, cfg["CAM_POS_TAU_S"]))
    a_yaw = 1.0 - math.exp(-dt / max(1e-3, cfg["CAM_YAW_TAU_S"]))
    cam_pos = None
    cam_phi = None
    prev_cam_eul = None
    prev_arrow_eul = None
    done.data.bevel_factor_start = 0.0
    lat0_, lng0_, kx_, ky_ = T["proj"]

    def terr_z(x, y):
        """Wysokość powierzchni terenu (po przewyższeniu) pod punktem sceny (x, y)."""
        return (terrain_at(T["terrain"], lat0_ + y / ky_, lng0_ + x / kx_) - T["zmin_ref"]) * cfg["EXAG"]
    clearance = max(10.0 * s, 0.4 * up)
    el = math.radians(cfg["ORBIT_ELEV_DEG"])
    az0 = math.radians(cfg["ORBIT_START_DEG"])
    pivot = Vector((E["cx"], E["cy"], E["cz"]))
    for fi in range(nf):
        fr = f0 + fi
        t = max(0.0, min(T["n"] - 1.0, t0 + fi / float(fps) * speed))
        P = Vector((at(T["x"], t), at(T["y"], t), at(T["z"], t)))

        # strzałka: pozycja i kierunek wzdłuż trasy (z nachyleniem)
        ph, th = at(T["phi_arrow"], t), at(T["theta_arrow"], t)
        d = Vector((math.cos(th) * math.cos(ph), math.cos(th) * math.sin(ph), math.sin(th)))
        arrow.location = P + Vector((0.0, 0.0, lift))
        q = d.to_track_quat("Y", "Z")
        arrow.rotation_euler = q.to_euler("XYZ", prev_arrow_eul) if prev_arrow_eul else q.to_euler("XYZ")
        prev_arrow_eul = arrow.rotation_euler.copy()
        arrow.keyframe_insert("location", frame=fr)
        arrow.keyframe_insert("rotation_euler", frame=fr)

        # przejechana część trasy rośnie za strzałką
        done.data.bevel_factor_end = max(1e-4, min(1.0, at(T["prog"], t)))
        done.data.keyframe_insert("bevel_factor_end", frame=fr)

        if orbit:
            # kamera krąży wokół całej trasy i patrzy na środek
            az = az0 + 2.0 * math.pi * cfg["ORBIT_TURNS"] * fi / float(nf)
            cam_pos = pivot + Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))) * orbit_dist
            look = pivot
        else:
            # kamera: za strzałką i nad nią, z bezwładnością
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
        cam.rotation_euler = cq.to_euler("XYZ", prev_cam_eul) if prev_cam_eul else cq.to_euler("XYZ")
        prev_cam_eul = cam.rotation_euler.copy()
        cam.keyframe_insert("location", frame=fr)
        cam.keyframe_insert("rotation_euler", frame=fr)
    prefs.keyframe_new_interpolation_type = old_interp

    # ----- ustawienia sceny i renderu
    scene.camera = cam
    scene.frame_start, scene.frame_end = f0, f0 + nf - 1
    scene.frame_set(f0)
    scene.render.fps = fps
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
    engine = set_engine(scene)
    info = {"data": T["path"], "name": T["name"], "length_km": round(T["length_m"] / 1000.0, 2),
            "curve_points": len(T["pts"]), "frames": nf, "fps": fps, "mode": cfg["MODE"], "cam": cfg["CAM_MODE"], "engine": engine,
            "z_max_m": round(T["zmax"], 1), "exag": cfg["EXAG"], "terrain": terrain_info,
            "blender": bpy.app.version_string}
    print("[HRF] " + json.dumps(info, ensure_ascii=False))
    return info


# ---------------------------------------------------------------- uruchomienie z wiersza poleceń
def _cli():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ov, extra, i = {}, {}, 0
    keymap = {"--data": ("DATA_PATH", str), "--mode": ("MODE", str), "--start": ("START_S", float),
              "--duration": ("DURATION_S", float), "--exag": ("EXAG", float), "--full-seconds": ("FULL_SECONDS", float),
              "--fps": ("FPS", int), "--cam": ("CAM_MODE", str), "--turns": ("ORBIT_TURNS", float), "--res-x": ("RES_X", int), "--res-y": ("RES_Y", int)}
    while i < len(argv):
        a = argv[i]
        if a in keymap and i + 1 < len(argv):
            ov[keymap[a][0]] = keymap[a][1](argv[i + 1])
            i += 2
        elif a in ("--render-frame", "--render-out", "--save") and i + 1 < len(argv):
            extra[a] = argv[i + 1]
            i += 2
        elif a == "--dark":      # podgląd na ciemnym tle (zamiast przezroczystego)
            extra[a] = "1"
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
        sc.frame_set(int(extra.get("--render-frame", sc.frame_start)))
        sc.render.filepath = extra["--render-out"]
        bpy.ops.render.render(write_still=True)
    if "--save" in extra:
        bpy.ops.wm.save_as_mainfile(filepath=extra["--save"])
