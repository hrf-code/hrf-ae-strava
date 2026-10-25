#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_converter.py  —  zamienia plik FIT albo GPX (np. pobrany ze Stravy) na <nazwa>_hrf.json
dla panelu After Effects (AE/hrf_ae_panel.jsx).

Pobieranie pliku ze Stravy: aktywność -> menu "..." -> "Eksportuj oryginał" (FIT/GPX z urządzenia)
albo "Eksportuj GPX".

Najprościej: wrzuć plik .fit/.gpx do folderu data/ i uruchom bez argumentów:
    py tools/hrf_converter.py
Skrypt przeszuka folder data/, a dla każdego pliku utworzy obok <nazwa>_hrf.json
(gotowe pliki pomija; --force przelicza ponownie). Działa też na .gpx.gz / .fit.gz
z eksportu całego konta Stravy.

Pojedynczy plik / opcje (Windows: zamiast python3 wpisz py):
    py hrf_converter.py aktywnosc.gpx
    py hrf_converter.py aktywnosc.fit --name "Przehyba" --start NS --meta PRZ
    py hrf_converter.py aktywnosc.gpx --ascent 902 --moving-time 12216 --distance 59.6

Opcje:
  -o, --output PLIK  plik wyjściowy (tylko dla pojedynczego pliku; domyślnie <nazwa>_hrf.json)
  --name TEKST       nazwa aktywności (domyślnie z GPX albo z nazwy pliku)
  --start KOD --meta KOD   skróty miejscowości na mapie (domyślnie START / META)
  --id NUMER         numer aktywności (domyślnie cyfry z nazwy pliku)
  --zones A,B,C,D    górne granice stref tętna Z1-Z4 (domyślnie 116,145,159,174)
  --zone NAZWA       strefa czasowa lokalnego czasu startu, np. Europe/Warsaw
                     (domyślnie strefa tego komputera)
  --ascent M --moving-time S --distance KM
                     dopasowanie sum do wartości ze strony Stravy (opcjonalne);
                     bez tego sumy liczą się z samego pliku i mogą się nieco różnić.

FIT wymaga biblioteki:  py -m pip install fitparse     (GPX działa bez niczego).
"""
import argparse, datetime, json, math, os, re, sys
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(encoding="utf-8")   # polskie znaki w terminalu Windows
except Exception:
    pass

W = 64


def ui_box(lines):
    print("+" + "-" * (W - 2) + "+")
    for l in lines:
        print("| " + l.ljust(W - 4) + " |")
    print("+" + "-" * (W - 2) + "+")


def ui_head(text):
    print("")
    print("== " + text + " " + "=" * max(3, W - 4 - len(text)))


def ui_ok(text):
    print("  [OK] " + text)


def ui_warn(text):
    print("  [!]  " + text)


def ui_info(text):
    print("       " + text)


def ask_yes(question, default=True):
    suffix = " [T/n] " if default else " [t/N] "
    try:
        ans = input("  ?    " + question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        return default
    return ans[0] in ("t", "y")


def have_fitparse():
    try:
        import fitparse  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_fitparse():
    """Sprawdza bibliotekę do plików FIT; w razie braku proponuje instalację."""
    if have_fitparse():
        return True
    ui_head("Brakuje biblioteki do plików FIT")
    ui_info("Pliki .fit czyta biblioteka 'fitparse' (jednorazowa, darmowa instalacja).")
    ui_info("Komenda ręcznie:  py -m pip install fitparse")
    if not ask_yes("Zainstalować teraz?"):
        ui_warn("Pomijam pliki FIT. Pliki .gpx działają bez instalowania czegokolwiek.")
        return False
    import subprocess
    print("")
    code = subprocess.call([sys.executable, "-m", "pip", "install", "fitparse"])
    print("")
    if code == 0 and have_fitparse():
        ui_ok("Zainstalowano fitparse.")
        return True
    ui_warn("Instalacja się nie udała. Spróbuj ręcznie:  py -m pip install fitparse")
    return False


# ---------------------------------------------------------------- wczytywanie
def parse_iso(s):
    s = s.strip().replace("Z", "+00:00")
    d = datetime.datetime.fromisoformat(s) if "." not in s else datetime.datetime.fromisoformat(re.sub(r"(\.\d{1,6})\d*", r"\1", s))
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def local(tag):
    return tag.rsplit("}", 1)[-1]


def read_gpx(path):
    root = ET.parse(path).getroot()
    name = None
    for el in root.iter():
        if local(el.tag) == "name" and el.text and el.text.strip():
            name = el.text.strip()
            break
    pts = []
    for tp in root.iter():
        if local(tp.tag) != "trkpt":
            continue
        try:
            lat, lon = float(tp.get("lat")), float(tp.get("lon"))
        except (TypeError, ValueError):
            continue
        ele = t = hr = None
        for ch in tp.iter():
            ln = local(ch.tag)
            if ln == "ele" and ch.text:
                ele = float(ch.text)
            elif ln == "time" and ch.text:
                t = parse_iso(ch.text)
            elif ln == "hr" and ch.text:
                hr = float(ch.text)
        if t is None:
            continue
        pts.append({"t": t, "lat": lat, "lon": lon, "ele": ele, "hr": hr, "dist": None})
    return name, pts


def read_fit(path):
    try:
        import fitparse
    except ImportError:
        raise SystemExit("brak biblioteki fitparse (py -m pip install fitparse)")
    ff = fitparse.FitFile(path)
    pts = []
    for msg in ff.get_messages("record"):
        v = {f.name: f.value for f in msg}
        lat, lon = v.get("position_lat"), v.get("position_long")
        t = v.get("timestamp")
        if lat is None or lon is None or t is None:
            continue
        lat, lon = lat * 180.0 / 2 ** 31, lon * 180.0 / 2 ** 31
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        ele = v.get("enhanced_altitude", v.get("altitude"))
        d = v.get("distance")
        pts.append({"t": t, "lat": lat, "lon": lon, "ele": ele,
                    "hr": v.get("heart_rate"), "dist": d})
    return None, pts


# ---------------------------------------------------------------- matematyka
def hav(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    dp = p2 - p1
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000.0 * math.asin(math.sqrt(h))


def bearing(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def smooth(vals, half):
    n = len(vals)
    out = []
    for i in range(n):
        a, b = max(0, i - half), min(n, i + half + 1)
        out.append(sum(vals[a:b]) / (b - a))
    return out


def ascent_descent(elev, thr=0.7):
    """Suma podjazdów i zjazdów z histerezą (drobny szum poniżej progu nie liczy się)."""
    gain, loss = [0.0], [0.0]
    ref = elev[0]
    g = l = 0.0
    for e in elev[1:]:
        if e - ref >= thr:
            g += e - ref
            ref = e
        elif ref - e >= thr:
            l += ref - e
            ref = e
        gain.append(g)
        loss.append(l)
    return gain, loss


def build(pts, args, name_from_file):
    pts.sort(key=lambda p: p["t"])
    # czasy rosnące, bez powtórzeń
    clean, last = [], None
    for p in pts:
        sec = int(round(p["t"].timestamp()))
        if last is not None and sec <= last:
            continue
        p["sec"] = sec
        clean.append(p)
        last = sec
    pts = clean
    if len(pts) < 10:
        sys.exit("Za mało punktów w pliku.")
    t0 = pts[0]["sec"]
    n = len(pts)
    time_s = [p["sec"] - t0 for p in pts]
    lat = [p["lat"] for p in pts]
    lon = [p["lon"] for p in pts]

    # wysokość: brakujące uzupełnij, lekko wygładź
    ele = []
    lastv = None
    for p in pts:
        if p["ele"] is not None:
            lastv = p["ele"]
        ele.append(lastv)
    first = next((e for e in ele if e is not None), 0.0)
    ele = [first if e is None else e for e in ele]
    elev = smooth(ele, 2)

    # dystans narastająco
    dist = [0.0]
    use_fit_dist = all(p["dist"] is not None for p in pts)
    for i in range(1, n):
        if use_fit_dist:
            d = max(0.0, pts[i]["dist"] - pts[i - 1]["dist"])
        else:
            d = hav(lat[i - 1], lon[i - 1], lat[i], lon[i])
        dist.append(dist[-1] + d)
    dist_scale = 1.0
    if args.distance:
        dist_scale = args.distance * 1000.0 / dist[-1]
    dist = [d * dist_scale for d in dist]
    total_km = dist[-1] / 1000.0

    # prędkość (km/h): różnica dystansu w oknie ok. 5 s, przerwy w nagraniu = postój
    speed = []
    for i in range(n):
        a = max(0, i - 2)
        b = min(n - 1, i + 2)
        dt = time_s[b] - time_s[a]
        if dt <= 0 or dt > 30:
            speed.append(0.0)
        else:
            speed.append((dist[b] - dist[a]) / dt * 3.6)
    speed = [0.0 if s < 1.0 else s for s in speed]          # poniżej 1 km/h = stoi
    moving = [1 if s > 0 else 0 for s in speed]

    # czas ruchu (narastająco), każda próbka liczy się do następnej
    mov = [0.0]
    for i in range(1, n):
        dt = min(time_s[i] - time_s[i - 1], 5)             # przerwy >5 s to pauza
        mov.append(mov[-1] + (dt if moving[i] else 0))
    moving_scale = 1.0
    if args.moving_time and mov[-1] > 0:
        moving_scale = args.moving_time / mov[-1]
    mov = [m * moving_scale for m in mov]

    gain, loss = ascent_descent(elev)
    gain_scale = 1.0
    if args.ascent and gain[-1] > 0:
        gain_scale = args.ascent / gain[-1]
    gain = [g * gain_scale for g in gain]
    loss = [l * gain_scale for l in loss]

    # nachylenie (%): różnica wysokości na ok. 25 m dystansu
    grade = []
    j0 = 0
    for i in range(n):
        while j0 < i and dist[i] - dist[j0] > 25:
            j0 += 1
        dd = dist[i] - dist[j0]
        if dd < 8 or speed[i] == 0:
            grade.append(0.0)
        else:
            gr = (elev[i] - elev[j0]) / dd * 100.0
            grade.append(max(-30.0, min(30.0, gr)))
    grade = smooth(grade, 2)

    # VAM: tempo wznoszenia (m/h) z ostatnich ok. 90 s
    vam = []
    j0 = 0
    for i in range(n):
        while j0 < i and time_s[i] - time_s[j0] > 90:
            j0 += 1
        dt = time_s[i] - time_s[j0]
        v = (elev[i] - elev[j0]) / dt * 3600 if dt >= 20 else 0.0
        vam.append(max(0.0, v))

    # tętno
    hr_raw = [p["hr"] for p in pts]
    has_hr = any(h is not None for h in hr_raw)
    hr, lasth = [], None
    for h in hr_raw:
        if h is not None:
            lasth = h
        hr.append(lasth)
    firsth = next((h for h in hr if h is not None), 0)
    hr = [int(round(firsth if h is None else h)) for h in hr]
    zones = [0] + args.zones + [999]
    zlist = [[zones[k] if k == 0 else zones[k] + 1, zones[k + 1]] for k in range(5)]
    zlist[0] = [0, zones[1]]
    hr_zone = []
    for h in hr:
        z = 1
        for k in range(1, 5):
            if h > zones[k]:
                z = k + 1
        hr_zone.append(z if has_hr else 1)

    avg_hr, max_hr, s_hr, mx = [], [], 0.0, 0
    for i in range(n):
        s_hr += hr[i]
        mx = max(mx, hr[i])
        avg_hr.append(round(s_hr / (i + 1), 1))
        max_hr.append(mx)

    avg_speed, max_speed, mxs = [], [], 0.0
    for i in range(n):
        mxs = max(mxs, speed[i])
        max_speed.append(round(mxs, 1))
        avg_speed.append(round(dist[i] / 1000.0 / (mov[i] / 3600.0), 1) if mov[i] > 0 else 0)

    # kierunek jazdy: azymut na odcinku ok. 20 m, w postoju zostaje ostatni
    head, lasth_deg = [], None
    unw = []
    j0 = 0
    for i in range(n):
        while j0 < i and dist[i] - dist[j0] > 12:
            j0 += 1
        if dist[i] - dist[j0] >= 10:
            lasth_deg = bearing(lat[j0], lon[j0], lat[i], lon[i])
        if lasth_deg is None:
            k = min(n - 1, 30)
            lasth_deg = bearing(lat[0], lon[0], lat[k], lon[k])
        head.append(lasth_deg)
    # wygładzenie kątów i rozwinięcie (bez skoków 359->0)
    prev = None
    for h in head:
        if prev is None:
            u = h
        else:
            d = ((h - prev + 540.0) % 360.0) - 180.0
            # delikatne wygładzanie, żeby mapa nie drżała
            u = prev + d * 0.5
        unw.append(u)
        prev = u
    heading_deg = [int(round(u % 360.0)) % 360 for u in unw]

    progress = [round(d / dist[-1] * 100.0, 2) if dist[-1] > 0 else 0.0 for d in dist]
    remain = [round(max(0.0, total_km - d / 1000.0), 3) for d in dist]

    # czas startu: lokalny zegar
    st = pts[0]["t"]
    if args.zone:
        try:
            from zoneinfo import ZoneInfo
            stl = st.astimezone(ZoneInfo(args.zone))
        except Exception as e:
            sys.exit("Nieznana strefa czasowa %s (%s). Na Windowsie: py -m pip install tzdata" % (args.zone, e))
    else:
        stl = st.astimezone()
    start_local = stl.strftime("%Y-%m-%dT%H:%M:%S")

    name = args.name or name_from_file
    act_id = args.id or "".join(re.findall(r"\d+", os.path.basename(args.file)))[:20] or "0"

    r1 = lambda a: [round(x, 1) for x in a]
    D = {
        "name": name,
        "activity_id": act_id,
        "start_date_local": start_local,
        "start_code": args.start or "START",
        "end_code": args.meta or "META",
        "total_dist_km": round(total_km, 3),
        "hr_zones": zlist,
        "samples": {
            "time_s": time_s,
            "speed_kmh": r1(speed),
            "hr": hr,
            "elev_m": r1(elev),
            "dist_km": [round(d / 1000.0, 3) for d in dist],
            "grade_pct": r1(grade),
            "gain_m": r1(gain),
            "loss_m": r1(loss),
            "avg_speed_kmh": avg_speed,
            "max_speed_kmh": max_speed,
            "avg_hr": avg_hr,
            "max_hr": max_hr,
            "vam_mh": [round(v) for v in vam],
            "moving_s": [int(round(m)) for m in mov],
            "hr_zone": hr_zone,
            "lat": [round(x, 6) for x in lat],
            "lng": [round(x, 6) for x in lon],
            "progress_pct": progress,
            "remain_km": remain,
            "heading_deg": heading_deg,
            "heading_unw": r1(unw),
        },
        "calibration": {"moving_s_scale": round(moving_scale, 4), "gain_scale": round(gain_scale, 4),
                        "dist_scale": round(dist_scale, 4),
                        "note": "sumy liczone z pliku; skale != 1 oznaczają dopasowanie do wartości podanych w opcjach"},
    }
    if not has_hr:
        D["calibration"]["uwaga"] = "brak tętna w pliku – serie tętna są zerowe"
    return D


def convert_one(path, out, args):
    low = path.lower()
    gz = low.endswith(".gz")
    if gz:
        import gzip, shutil, tempfile
        base = path[:-3]
        ext = os.path.splitext(base)[1].lower()
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        with gzip.open(path, "rb") as fi:
            shutil.copyfileobj(fi, tmp)
        tmp.close()
        src = tmp.name
    else:
        ext = os.path.splitext(path)[1].lower()
        src = path
    try:
        if ext == ".fit":
            name, pts = read_fit(src)
        elif ext in (".gpx", ".xml"):
            name, pts = read_gpx(src)
        else:
            raise SystemExit("Obsługiwane pliki: .fit, .gpx (także .gz)")
    finally:
        if gz:
            os.unlink(src)
    if not name:
        name = os.path.splitext(os.path.basename(path[:-3] if gz else path))[0]
    args.file = path
    D = build(pts, args, name)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(D, f, ensure_ascii=False, separators=(",", ":"))
    s = D["samples"]
    ui_ok("Zapisano: " + os.path.basename(out))
    ui_info("%s  |  start %s" % (D["name"], D["start_date_local"]))
    ui_info("%d próbek, %.1f km, czas całkowity %d s, czas ruchu %d s" % (
        len(s["time_s"]), D["total_dist_km"], s["time_s"][-1], s["moving_s"][-1]))
    ui_info("podjazdy %.0f m, zjazdy %.0f m, śr. %.1f km/h, maks. %.1f km/h" % (
        s["gain_m"][-1], s["loss_m"][-1], s["avg_speed_kmh"][-1], s["max_speed_kmh"][-1]))
    if s["max_hr"][-1]:
        ui_info("tętno śr. %.0f, maks. %d" % (s["avg_hr"][-1], s["max_hr"][-1]))
    else:
        ui_warn("brak tętna w pliku")


def main():
    ap = argparse.ArgumentParser(description="FIT/GPX -> JSON dla panelu AE (bez argumentów: przeszukuje folder data/)")
    ap.add_argument("file", nargs="?", help="plik .fit/.gpx albo folder; bez tego – folder data/ obok tools/")
    ap.add_argument("-o", "--output", help="plik wyjściowy (tylko dla pojedynczego pliku)")
    ap.add_argument("--name")
    ap.add_argument("--start")
    ap.add_argument("--meta")
    ap.add_argument("--id")
    ap.add_argument("--zones", default="116,145,159,174")
    ap.add_argument("--zone")
    ap.add_argument("--ascent", type=float)
    ap.add_argument("--moving-time", dest="moving_time", type=float)
    ap.add_argument("--distance", type=float)
    ap.add_argument("--force", action="store_true", help="przelicz także pliki, które mają już gotowy JSON")
    args = ap.parse_args()
    try:
        args.zones = [int(x) for x in args.zones.split(",")]
        assert len(args.zones) == 4
    except Exception:
        sys.exit("--zones podaj jako cztery liczby: 116,145,159,174")

    ui_box(["HRF  -  konwerter FIT/GPX na dane dla After Effects",
            "Wrzuć plik .fit lub .gpx do folderu data/ i uruchom skrypt."])
    target = args.file
    if target and os.path.isfile(target):
        if target.lower().endswith((".fit", ".fit.gz")) and not ensure_fitparse():
            return
        ui_head(os.path.basename(target))
        out = args.output or os.path.splitext(target if not target.lower().endswith(".gz") else target[:-3])[0] + "_hrf.json"
        convert_one(target, out, args)
        return

    if target:
        folder = target
    else:
        # domyślnie folder data/ obok tools/; gdy go nie ma – folder skryptu
        folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        if not os.path.isdir(folder):
            folder = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(folder):
        sys.exit("Nie ma takiego pliku ani folderu: %s" % folder)
    exts = (".fit", ".gpx", ".fit.gz", ".gpx.gz")
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(exts))
    if not files:
        ui_head("Folder: " + folder)
        ui_warn("Nie ma tu plików .fit ani .gpx.")
        ui_info("Pobierz je ze Stravy: aktywność -> menu '...' -> 'Eksportuj oryginał'")
        ui_info("albo 'Eksportuj GPX', wrzuć do tego folderu i uruchom skrypt ponownie.")
        return
    ui_head("Folder: " + folder)
    ui_info("Znaleziono plików: %d" % len(files))
    for f in files:
        ui_info("- " + f)
    fit_ok = None
    done = skipped = failed = 0
    for f in files:
        path = os.path.join(folder, f)
        base = path[:-3] if path.lower().endswith(".gz") else path
        out = os.path.splitext(base)[0] + "_hrf.json"
        ui_head(f)
        if os.path.exists(out) and not args.force and os.path.getmtime(out) >= os.path.getmtime(path):
            ui_info("Pomijam – JSON już jest (opcja --force przelicza ponownie).")
            skipped += 1
            continue
        if f.lower().endswith((".fit", ".fit.gz")):
            if fit_ok is None:
                fit_ok = ensure_fitparse()
            if not fit_ok:
                ui_warn("Pominięto plik FIT (brak biblioteki).")
                failed += 1
                continue
        try:
            convert_one(path, out, args)
            done += 1
        except SystemExit as e:
            ui_warn("Pominięto: %s" % e)
            failed += 1
        except Exception as e:
            ui_warn("Błąd: %s" % e)
            failed += 1
    ui_head("Podsumowanie")
    ui_info("Przeliczone: %d,  pominięte (już gotowe): %d,  błędy: %d" % (done, skipped, failed))
    if done:
        ui_info("W panelu AE kliknij 'Wczytaj dane...' i wskaż plik *_hrf.json.")
        ui_info("Sumy możesz wyrównać do Stravy: --ascent / --moving-time / --distance")
        ui_info("(dla pojedynczego pliku, np.: py hrf_converter.py plik.gpx --ascent 902)")


if __name__ == "__main__":
    main()
