#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HRF.pyw  —  jedno okno do wszystkich narzędzi: dane z FIT/GPX, teren, warstwice, wycinek lokalny, OSM, Blender, After Effects.

Uruchomienie: dwuklik na HRF.pyw albo  py HRF.pyw  (potrzebny Python 3 z tkinter; nic więcej nie trzeba instalować).
Najedź myszą na dowolne pole lub przycisk – pojawi się podpowiedź, co dokładnie robi.

Program niczego nie liczy sam: uruchamia skrypty z folderu tools/ (te same, które można wołać z terminala)
i pokazuje ich wynik w dolnym logu. Dane każdej aktywności leżą w osobnym folderze data/<projekt>/.
"""
import glob, json, os, queue, re, shutil, subprocess, sys, threading, time, webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")
DATA = os.path.join(ROOT, "data")
BLENDER_SCRIPT = os.path.join(ROOT, "Blender", "hrf_blender_route.py")
AE_PANEL = os.path.join(ROOT, "AE", "hrf_ae_panel.jsx")
SETTINGS = os.path.join(ROOT, "_local", "hrf_gui.json")
VERSION = "1.0"
REPO_URL = "https://github.com/hrf-code/hrf-ae-strava"

NET_INFO = {
    "teren": ("Do serwera kafelków wysokości (s3.amazonaws.com, otwarte dane Terrarium) trafią tylko numery kafelków (z/x/y) "
              "pokrywających obszar trasy. Sama trasa i Twoje dane nie są wysyłane."),
    "wycinek": ("Do serwera kafelków wysokości (s3.amazonaws.com) trafią tylko numery kafelków pokrywających wskazany wycinek. "
                "Sama trasa nie jest wysyłana."),
    "ulice": ("Do serwera Overpass (OpenStreetMap) trafią tylko współrzędne rogów prostokątów pokrywających obszar terenu (po kilka km), a nie Twoja trasa. "
              "Pobierane są drogi, rzeki, woda i granice miasta w tym obszarze; dla dużego terenu to kilkadziesiąt MB danych i kilka minut."),
    "osm": ("Do serwera Overpass (OpenStreetMap) zostaną wysłane współrzędne odcinków Twojej trasy, żeby pobrać drogi, budynki, "
            "wodę i zieleń w pobliżu. To dane o przebiegu trasy – wysyłane są do zewnętrznego serwisu."),
}


# ---------------------------------------------------------------- pomocnicze
def python_exe():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(cand):
            return cand
    return exe


def git_revision():
    """Krótki skrót bieżącego commita (do podpisu wersji); puste, gdy to nie jest kopia z gita."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def fmt_size(n):
    if n < 1024 * 1024:
        return "%.0f kB" % (n / 1024.0)
    return "%.1f MB" % (n / 1048576.0)


def safe_name(s):
    s = re.sub(r"[^\w\-]+", "_", s.strip(), flags=re.UNICODE).strip("_")
    return s or "aktywnosc"


def quote_arg(a):
    return '"%s"' % a if (" " in a or not a) else a


def find_blenders():
    found = []
    pats = [r"C:\Program Files\Blender Foundation\Blender *\blender.exe",
            r"C:\Program Files (x86)\Blender Foundation\Blender *\blender.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Blender Foundation\Blender *\blender.exe")]
    for p in pats:
        found += glob.glob(p)
    env = os.environ.get("HRF_BLENDER")
    if env and os.path.isfile(env):
        found.append(env)

    def ver(path):
        m = re.search(r"Blender (\d+)\.(\d+)", path)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return sorted(set(found), key=ver, reverse=True)


def parse_local_command(text):
    """Wyciąga parametry z komendy skopiowanej w Blenderze (przycisk 'Kopiuj komendę')."""
    out = {}
    m = re.search(r"--bbox\s+([-\d.,]+)", text)
    if m:
        parts = m.group(1).split(",")
        if len(parts) == 4:
            out["bbox"] = parts
    for key in ("zoom", "step", "minor", "major", "png-width"):
        m = re.search(r"--%s\s+([\d.]+)" % key, text)
        if m:
            out[key] = m.group(1)
    out["vector"] = "--vector" in text
    out["no-png"] = "--no-png" in text
    return out


class Tip:
    """Podpowiedź przy najechaniu myszą."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.tw = None
        self.after_id = None
        widget.bind("<Enter>", self.enter, add="+")
        widget.bind("<Leave>", self.leave, add="+")
        widget.bind("<ButtonPress>", self.leave, add="+")

    def enter(self, _e=None):
        self.cancel()
        self.after_id = self.widget.after(self.delay, self.show)

    def leave(self, _e=None):
        self.cancel()
        if self.tw is not None:
            try:
                self.tw.destroy()
            except Exception:
                pass
            self.tw = None

    def cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def show(self):
        if self.tw is not None:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tw, text=self.text, justify="left", background="#fffbe0", foreground="#222", relief="solid", borderwidth=1,
                 wraplength=420, font=("Segoe UI", 9), padx=8, pady=5).pack()


# ---------------------------------------------------------------- aplikacja
class App:
    def __init__(self, root, data_dir=None):
        self.root = root
        self.data_dir = data_dir or DATA
        self.tips = {}
        self.action_buttons = []
        self.job = None
        self.q = queue.Queue()
        self._buf = ""
        self.intro_labels = []
        root.title("HRF – trasa ze Stravy: narzędzia")
        try:
            self.icon = tk.PhotoImage(file=os.path.join(ROOT, "assets", "hrf_icon.png"))
            root.iconphoto(True, self.icon)
        except Exception:
            pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d+20+20" % (min(1080, sw - 60), min(820, sh - 100)))
        root.minsize(760, 560)
        try:
            ttk.Style().theme_use("vista" if os.name == "nt" else "clam")
        except Exception:
            pass
        self.settings = self.load_settings()
        self.extra = [x for x in self.settings.get("extra", []) if isinstance(x, str)]
        self.blends = dict(self.settings.get("blends", {}))     # projekt -> ostatnio otwarty zapisany .blend
        self.proj_map = {}
        self.build_vars()
        self.build_menu()
        self.build_header()
        self.build_status()
        self.build_log()
        self.build_tabs()
        self.refresh_projects(select=self.settings.get("project"))
        root.after(100, self.pump)

    # ---- ustawienia
    def load_settings(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_settings(self):
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump({"project": self.pdir() or "", "extra": self.extra, "blends": self.blends, "blender": self.blender.get()}, f, ensure_ascii=False)
        except Exception:
            pass

    # ---- zmienne
    def build_vars(self):
        V = tk.StringVar
        self.project = V()
        self.src, self.new_name, self.proj_dir = V(), V(), V()
        self.act_name, self.start_code, self.end_code, self.zones, self.tz = V(), V(value="START"), V(value="META"), V(value="116,145,159,174"), V()
        self.ascent, self.moving, self.distance = V(), V(), V()
        self.t_margin, self.t_zoom, self.t_step = V(value="15000"), V(value="12"), V(value="60")
        self.c_minor, self.c_major, self.c_width = V(value="25"), V(value="100"), V(value="4096")
        self.c_png, self.c_pdf, self.c_svg = tk.BooleanVar(value=True), tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        self.l_bbox = [V(), V(), V(), V()]
        self.l_zoom, self.l_step, self.l_minor, self.l_major, self.l_width = V(value="14"), V(value="10"), V(value="10"), V(value="50"), V(value="6144")
        self.l_png, self.l_vec = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self.o_radius, self.o_chunk, self.o_tries, self.o_server = V(value="150"), V(value="3000"), V(value="6"), V(value="domyślne")
        self.o_force = tk.BooleanVar(value=False)
        B = tk.BooleanVar
        self.s_roads, self.s_cycle, self.s_small, self.s_paths, self.s_rivers, self.s_streams, self.s_water, self.s_bound = (
            B(value=True), B(value=True), B(value=False), B(value=False), B(value=True), B(value=False), B(value=True), B(value=True))
        self.s_tile, self.s_width, self.s_admin = V(value="10"), V(value="4096"), V(value="8")
        self.s_png, self.s_pdf, self.s_svg = B(value=True), B(value=False), B(value=False)
        self.blender = V()
        self.null_coords, self.null_name = V(), V(value="HRF_Null")

    # ---- rejestracja podpowiedzi
    def tip(self, widget, text):
        self.tips[str(widget)] = text
        Tip(widget, text)

    def action(self, parent, text, command, tip, **grid):
        b = ttk.Button(parent, text=text, command=command)
        self.tip(b, tip)
        self.action_buttons.append(b)
        return b

    def field(self, parent, row, label, var, tip, width=16, choices=None, unit="", browse=None):
        lab = ttk.Label(parent, text=label)
        lab.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        if choices is not None:
            w = ttk.Combobox(parent, textvariable=var, values=choices, width=width)
        else:
            w = ttk.Entry(parent, textvariable=var, width=width)
        w.grid(row=row, column=1, sticky="w", pady=3)
        if unit:
            ttk.Label(parent, text=unit, foreground="#666").grid(row=row, column=2, sticky="w", padx=4)
        self.tip(lab, tip)
        self.tip(w, tip)
        if browse:
            b = ttk.Button(parent, text="Przeglądaj…", command=browse)
            b.grid(row=row, column=3, sticky="w", padx=6)
            self.tip(b, "Wybierz plik w oknie dialogowym.")
        return w

    def check(self, parent, text, var, tip, **kw):
        c = ttk.Checkbutton(parent, text=text, variable=var, **kw)
        self.tip(c, tip)
        return c

    def intro(self, parent, text):
        lab = ttk.Label(parent, text=text, justify="left", foreground="#333", wraplength=900)
        lab.pack(fill="x", padx=12, pady=(10, 6))
        self.intro_labels.append(lab)
        return lab

    # ---- menu
    def build_menu(self):
        bar = tk.Menu(self.root)
        plik = tk.Menu(bar, tearoff=0)
        plik.add_command(label="Nowy projekt z pliku FIT/GPX…", command=self.new_project_dialog)
        plik.add_command(label="Dodaj istniejący folder projektu…", command=self.add_existing_folder)
        plik.add_command(label="Otwórz folder projektu", command=self.open_project_folder)
        plik.add_command(label="Otwórz folder data/", command=lambda: self.open_folder(self.data_dir))
        plik.add_separator()
        plik.add_command(label="Uruchom Blendera ze skryptem", accelerator="F5", command=self.do_blender)
        plik.add_command(label="Otwórz zapisaną scenę (.blend)…", command=self.open_blend)
        plik.add_separator()
        plik.add_command(label="Zakończ", command=self.quit)
        self.root.bind("<F5>", lambda e: self.do_blender())
        bar.add_cascade(label="Plik", menu=plik)
        pomoc = tk.Menu(bar, tearoff=0)
        pomoc.add_command(label="Jak to działa (przepływ pracy)", command=lambda: self.nb.select(self.tabs["start"]))
        pomoc.add_command(label="O programie", command=lambda: self.nb.select(self.tabs["about"]))
        pomoc.add_separator()
        pomoc.add_command(label="Strona projektu na GitHubie", command=lambda: webbrowser.open(REPO_URL))
        pomoc.add_command(label="Otwórz folder programu", command=lambda: self.open_folder(ROOT))
        bar.add_cascade(label="Pomoc", menu=pomoc)
        self.root.config(menu=bar)

    def open_folder(self, d):
        if d and os.path.isdir(d):
            os.startfile(d) if os.name == "nt" else subprocess.Popen(["xdg-open", d])

    def quit(self):
        self.save_settings()
        self.root.destroy()

    # ---- nagłówek z wyborem projektu
    def build_header(self):
        f = ttk.Frame(self.root, padding=(10, 8, 10, 2))
        f.pack(fill="x")
        lab = ttk.Label(f, text="Projekt (aktywność):", font=("Segoe UI", 10, "bold"))
        lab.pack(side="left")
        self.tip(lab, "Jeden projekt = jedna aktywność = jeden folder (domyślnie data/<projekt>/, ale może leżeć gdziekolwiek na dysku). Wszystkie pliki (dane, teren, "
                      "warstwice, OSM, wycinek, pamięć podręczna) leżą w tym folderze. Wybierz istniejący albo utwórz nowy z pliku FIT/GPX.")
        self.cb = ttk.Combobox(f, textvariable=self.project, state="readonly", width=44)
        self.cb.pack(side="left", padx=8)
        self.cb.bind("<<ComboboxSelected>>", lambda e: self.on_project())
        self.tip(self.cb, "Podfoldery data/ oraz foldery dodane z dysku (pokazane razem ze ścieżką). Zmiana projektu odświeża stan plików poniżej.")
        b = ttk.Button(f, text="Odśwież", command=lambda: self.refresh_projects(select=self.project.get()))
        b.pack(side="left")
        self.tip(b, "Ponownie czyta folder data/ (np. po ręcznym skopiowaniu plików).")
        b = ttk.Button(f, text="Dodaj folder…", command=self.add_existing_folder)
        b.pack(side="left", padx=6)
        self.tip(b, "Dodaje do listy istniejący folder projektu z dowolnego miejsca na dysku (taki, w którym jest plik *_hrf.json). "
                    "Projekty z folderu data/ pojawiają się na liście same.")
        b = ttk.Button(f, text="Otwórz folder projektu", command=self.open_project_folder)
        b.pack(side="left", padx=6)
        self.tip(b, "Otwiera folder data/<projekt>/ w Eksploratorze Windows.")
        b = ttk.Button(f, text="Nowy projekt z pliku FIT/GPX…", command=self.new_project_dialog)
        b.pack(side="left", padx=6)
        self.tip(b, "Okno nowego projektu: wskazujesz plik .fit/.gpx i folder, a program sam konwertuje dane i przygotowuje teren oraz warstwice.")
        b = ttk.Button(f, text="▶ Blender (F5)", command=self.do_blender)
        b.pack(side="right")
        self.tip(b, "Uruchamia Blendera ze skryptem HRF dla wybranego projektu. Działa w każdej chwili, także gdy w tle trwa pobieranie danych. Skrót: F5.")

    # ---- panel stanu plików projektu
    def build_status(self):
        f = ttk.LabelFrame(self.root, text="Pliki projektu", padding=(10, 4, 10, 6))
        f.pack(fill="x", padx=10, pady=(4, 2))
        self.status_labels = {}
        items = [("act", "Dane aktywności", "Plik <nazwa>_hrf.json z konwertera (zakładka Dane). Potrzebny do wszystkiego poniżej."),
                 ("terrain", "Teren", "hrf_terrain.json – wysokości terenu wokół trasy (zakładka Teren)."),
                 ("cont", "Warstwice (cały teren)", "hrf_contours.png/.pdf/.svg – czarno-biała mapa warstwic (zakładka Warstwice)."),
                 ("streets", "Mapa ulic", "hrf_streets.png/.pdf/.svg – czarno-białe kontury ulic, rzek, wody i granic miasta (zakładka Mapa ulic)."),
                 ("local", "Wycinek lokalny", "hrf_local.png + hrf_local.meta.json – tekstura w wysokiej jakości dla fragmentu trasy (zakładka Wycinek)."),
                 ("osm", "OSM", "hrf_osm.json – drogi, budynki, woda i zieleń z OpenStreetMap (zakładka OSM, opcjonalne).")]
        for i, (key, text, tip) in enumerate(items):
            lab = ttk.Label(f, text=text + ": –", width=36)
            lab.grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=1)
            self.status_labels[key] = (lab, text)
            self.tip(lab, tip)

    # ---- zakładki
    def build_tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=6)
        self.tabs = {}
        for key, title in (("start", "Start"), ("dane", "1. Dane"), ("teren", "2. Teren"), ("warstwice", "3. Warstwice"),
                           ("ulice", "4. Mapa ulic"), ("wycinek", "5. Wycinek lokalny"), ("osm", "6. OSM"), ("blender", "7. Blender"), ("ae", "8. After Effects"), ("about", "O programie")):
            fr = ttk.Frame(self.nb)
            self.nb.add(fr, text=title)
            self.tabs[key] = fr
        self.tab_start(self.tabs["start"])
        self.tab_dane(self.tabs["dane"])
        self.tab_teren(self.tabs["teren"])
        self.tab_warstwice(self.tabs["warstwice"])
        self.tab_ulice(self.tabs["ulice"])
        self.tab_wycinek(self.tabs["wycinek"])
        self.tab_osm(self.tabs["osm"])
        self.tab_blender(self.tabs["blender"])
        self.tab_ae(self.tabs["ae"])
        self.tab_about(self.tabs["about"])
        self.root.bind("<Configure>", self.on_resize, add="+")

    def on_resize(self, e):
        if e.widget is self.root:
            for lab in self.intro_labels:
                try:
                    lab.config(wraplength=max(400, e.width - 60))
                except Exception:
                    pass

    def tab_start(self, f):
        self.intro(f, "Tak wygląda cały przepływ. Wymagane są kroki 1–3 (dane, teren, warstwice) i 7 (Blender); reszta jest opcjonalna.")
        txt = ("1. Dane – konwertujesz plik FIT/GPX ze Stravy na plik *_hrf.json (prędkość, tętno, wysokość, trasa…).\n"
               "2. Teren – pobierasz wysokości terenu wokół trasy (internet, kafelki wysokości).\n"
               "3. Warstwice – rysujesz czarno-białą mapę warstwic całego terenu (PNG do Blendera, PDF/SVG do edycji).\n"
               "4. Mapa ulic – osobne czarno-białe kontury dróg, rzek, jezior i granic miasta (ta sama plansza, osobne pliki hrf_streets.*).\n"
               "5. Wycinek lokalny – gdy kamera jest blisko trasy: tekstura warstwic w wysokiej jakości tylko dla tego fragmentu.\n"
               "6. OSM – opcjonalnie drogi, budynki, woda i zieleń z OpenStreetMap (do mini mapy w After Effects).\n"
               "7. Blender – otwiera Blendera z gotową sceną 3D (trasa z wysokością, strzałka, kamera, teren); renderujesz przezroczyste PNG.\n"
               "8. After Effects – panel z nakładką danych nad wideo (liczniki, mapa, profil); nakładasz też render z Blendera.")
        lab = ttk.Label(f, text=txt, justify="left")
        lab.pack(fill="x", padx=14, pady=4)
        self.intro(f, "Gdzie są pliki: folder projektu (domyślnie data/<projekt>/, ale możesz wskazać dowolny folder na dysku) zawiera <nazwa>_hrf.json, hrf_terrain.json, hrf_contours.(png/pdf/svg), hrf_streets.(png/pdf/svg), hrf_local.*, "
                      "hrf_osm.json, folder zrodlo/ (oryginalny FIT/GPX) i folder cache/ (pobrane kafelki, odcinki OSM, logi). "
                      "Folder data/ jest prywatny i nie trafia do GitHuba; własny folder projektu program tylko zapamiętuje, niczego poza nim nie rusza. Najedź myszą na dowolne pole lub przycisk, żeby zobaczyć podpowiedź.")

    def tab_dane(self, f):
        self.intro(f, "Konwertuje plik FIT/GPX na dane dla Blendera i After Effects (<projekt>/<projekt>_hrf.json). Działa bez internetu "
                      "(do plików FIT potrzebna jest biblioteka fitparse – program zapyta o instalację).")
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        self.field(g, 0, "Plik źródłowy (.fit/.gpx):", self.src, "Plik pobrany ze Stravy: aktywność → ‘⋯’ → Eksportuj oryginał (FIT) albo Eksportuj GPX. "
                   "Obsługiwane też .gz. Plik zostanie skopiowany do folderu projektu (zrodlo/), oryginał zostaje nietknięty.",
                   width=52, browse=self.pick_source)
        self.field(g, 1, "Folder projektu:", self.proj_dir, "Dowolny folder na dysku (nowy lub istniejący). Program skopiuje tu plik źródłowy do podfolderu zrodlo/ "
                   "i zapisze tu wszystkie wyniki: dane, teren, warstwice, OSM i pamięć podręczną. Puste = data/<nazwa projektu>/ w folderze programu.",
                   width=52, browse=self.pick_folder)
        self.field(g, 2, "Nazwa projektu:", self.new_name, "Nazwa plików danych (<nazwa>_hrf.json) i, gdy nie wskażesz folderu, nazwa folderu data/<nazwa>/. "
                   "Domyślnie z nazwy pliku. Jeśli dane już tam są, stary plik *_hrf.json zostanie nadpisany.", width=30)
        self.field(g, 3, "Nazwa aktywności:", self.act_name, "Tekst nazwy w danych (np. do napisu). Puste = z pliku GPX albo z nazwy pliku.", width=40)
        self.field(g, 4, "Kod startu / mety:", self.start_code, "Krótki napis przy punkcie startu na mapie (np. NS). Domyślnie START.", width=10)
        ttk.Entry(g, textvariable=self.end_code, width=10).grid(row=4, column=2, sticky="w", padx=4)
        self.tip(g.grid_slaves(row=4, column=2)[0], "Krótki napis przy mecie na mapie (np. PRZ). Domyślnie META.")
        self.field(g, 5, "Strefy tętna (górne granice):", self.zones, "Cztery górne granice stref Z1–Z4 w bpm, oddzielone przecinkami. Domyślnie 116,145,159,174.", width=24)
        self.field(g, 6, "Strefa czasowa:", self.tz, "Do czasu lokalnego startu, np. Europe/Warsaw. Puste = strefa tego komputera. "
                   "Na Windowsie może wymagać: py -m pip install tzdata.", width=24)
        ttk.Separator(g, orient="horizontal").grid(row=7, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(g, text="Wyrównanie sum do Stravy (opcjonalne):").grid(row=8, column=0, columnspan=3, sticky="w")
        self.field(g, 9, "Podjazdy:", self.ascent, "Suma podjazdów ze strony Stravy w metrach. Wtedy podjazdy w danych zostaną przeskalowane do tej wartości "
                   "(bez tego liczą się z samego pliku i mogą się nieco różnić).", width=10, unit="m")
        self.field(g, 10, "Czas ruchu:", self.moving, "Czas ruchu ze Stravy w sekundach (np. 12216 = 3 h 23 min 36 s).", width=10, unit="s")
        self.field(g, 11, "Dystans:", self.distance, "Dystans ze Stravy w kilometrach (np. 59.6).", width=10, unit="km")
        b = self.action(f, "Konwertuj → utwórz projekt", self.do_convert,
                        "Kopiuje plik do <folder projektu>/zrodlo/ i zapisuje tam <nazwa>_hrf.json. Potem wybierz kolejny krok (2. Teren).")
        b.pack(anchor="w", padx=14, pady=10)

    def tab_teren(self, f):
        self.intro(f, "Pobiera wysokości terenu wokół trasy z otwartych kafelków (Mapzen/AWS Terrarium: SRTM i inne). Wynik: hrf_terrain.json. "
                      "Potrzebny internet. Pobrane kafelki zostają w cache/ – ponowne uruchomienie dociąga tylko brakujące.")
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        self.field(g, 0, "Margines wokół trasy:", self.t_margin, "O ile metrów teren wychodzi poza trasę. 1500 = wąski pas wokół trasy; 15000 = teren wypełnia cały kadr "
                   "kamery orbitującej wokół całej trasy.", width=10, unit="m")
        self.field(g, 1, "Poziom kafelków (zoom):", self.t_zoom, "Rozdzielczość źródłowa: 12 ≈ 25 m na piksel, 13 ≈ 12 m, 14 ≈ 6 m. Wyższy = dokładniej, ale dużo więcej kafelków. "
                   "Dla dużego marginesu użyj 12.", width=10, choices=["11", "12", "13", "14"])
        self.field(g, 2, "Oczko siatki:", self.t_step, "Odstęp wierzchołków siatki terenu w Blenderze. Mniejszy = ładniejszy relief, ale więcej wierzchołków "
                   "(40 × 46 km przy 60 m to ok. 0,5 mln, przy 30 m ok. 2 mln).", width=10, unit="m")
        row = ttk.Frame(f)
        row.pack(anchor="w", padx=14, pady=4)
        b = ttk.Button(row, text="Preset: wąski pas", command=lambda: self.set_terrain(1500, 13, 25))
        b.pack(side="left")
        self.tip(b, "Margines 1,5 km, zoom 13, oczko 25 m – lekki teren tylko wokół trasy.")
        b = ttk.Button(row, text="Preset: pełny kadr orbity", command=lambda: self.set_terrain(15000, 12, 60))
        b.pack(side="left", padx=6)
        self.tip(b, "Margines 15 km, zoom 12, oczko 60 m – teren wypełnia cały kadr kamery orbitującej (ok. 0,5 mln wierzchołków).")
        b = self.action(f, "Pobierz teren (internet)", self.do_terrain,
                        "Wysyła numery kafelków (nie trasę) do serwera wysokości i zapisuje data/<projekt>/hrf_terrain.json. Pyta o zgodę przed startem.")
        b.pack(anchor="w", padx=14, pady=10)

    def set_terrain(self, margin, zoom, step):
        self.t_margin.set(str(margin))
        self.t_zoom.set(str(zoom))
        self.t_step.set(str(step))

    def tab_warstwice(self, f):
        self.intro(f, "Rysuje warstwice całego terenu jako czarną mapę z białymi liniami. PNG służy w Blenderze jako tekstura terenu; PDF i SVG to wektory do "
                      "ręcznej edycji (Illustrator, Inkscape) – po edycji wyeksportuj PNG o tych samych proporcjach. Działa bez internetu.")
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        self.field(g, 0, "Warstwice co:", self.c_minor, "Odstęp cienkich warstwic w metrach (np. 25). Mniej = gęściej.", width=10, unit="m")
        self.field(g, 1, "Linie główne co:", self.c_major, "Odstęp grubszych warstwic; musi być wielokrotnością odstępu cienkich (np. 100).", width=10, unit="m")
        self.field(g, 2, "Szerokość PNG:", self.c_width, "Szerokość obrazu w pikselach (np. 4096). Wyższa = ostrzejsze linie z bliska, ale dłuższe liczenie i większy plik. "
                   "Dla zbliżeń lepiej użyć zakładki Wycinek lokalny.", width=10, unit="px")
        r = ttk.Frame(f)
        r.pack(anchor="w", padx=14, pady=6)
        self.check(r, "Zapisz PNG", self.c_png, "Raster (skala szarości) – tekstura terenu w Blenderze.").pack(side="left")
        self.check(r, "Zapisz PDF", self.c_pdf, "Wektorowy PDF do edycji (czarne tło + białe ścieżki).").pack(side="left", padx=12)
        self.check(r, "Zapisz SVG", self.c_svg, "Wektorowy SVG z nazwanymi grupami warstwic (wygodniejszy w edycji niż PDF).").pack(side="left")
        r2 = ttk.Frame(f)
        r2.pack(anchor="w", padx=14, pady=8)
        b = self.action(r2, "Generuj warstwice", self.do_contours, "Liczy warstwice z hrf_terrain.json i zapisuje wybrane pliki hrf_contours.* w folderze projektu. "
                        "Istniejące pliki dostają kopię .bak przed nadpisaniem.")
        b.pack(side="left")
        b = ttk.Button(r2, text="Otwórz PDF", command=lambda: self.open_project_file("hrf_contours.pdf"))
        b.pack(side="left", padx=8)
        self.tip(b, "Otwiera hrf_contours.pdf w domyślnym programie (np. Illustrator).")
        b = ttk.Button(r2, text="Otwórz SVG", command=lambda: self.open_project_file("hrf_contours.svg"))
        b.pack(side="left")
        self.tip(b, "Otwiera hrf_contours.svg w domyślnym programie (np. Inkscape).")

    def tab_ulice(self, f):
        self.intro(f, "Rysuje mapę głównych dróg, ścieżek rowerowych, rzek, jezior i granic miasta jako czarno-białe KONTURY (same linie, bez wypełnień; drogi to linie) na tej samej planszy co teren "
                      "i warstwice, ale w osobnych plikach (hrf_streets.png/.pdf/.svg). Dane z OpenStreetMap, potrzebny internet. W Blenderze: Styl terenu → Mapa ulic.")
        box = ttk.LabelFrame(f, text="Co narysować", padding=(10, 4, 10, 6))
        box.pack(anchor="w", padx=14, pady=4)
        items = ((self.s_roads, "Główne drogi (arterie)", "Tylko główne arterie: autostrady, drogi ekspresowe, krajowe, wojewódzkie, powiatowe i trzeciorzędne (cieńsze). Bez drobnych uliczek."),
                 (self.s_cycle, "Ścieżki rowerowe", "Wydzielone drogi i ścieżki rowerowe (oraz ścieżki oznaczone jako rowerowe)."),
                 (self.s_small, "Drobne uliczki", "Ulice osiedlowe, boczne, serwisowe i dojazdowe – bardzo dużo drobnych linii, dlatego domyślnie wyłączone."),
                 (self.s_paths, "Ścieżki i drogi leśne", "Chodniki, ścieżki, drogi leśne, szlaki rowerowe i schody (najcieńsze linie). W mieście bardzo dużo danych."),
                 (self.s_rivers, "Rzeki i kanały", "Cieki wodne rysowane jako linie."),
                 (self.s_streams, "Potoki", "Małe cieki; w górach jest ich dużo, więc domyślnie wyłączone."),
                 (self.s_water, "Jeziora i zbiorniki (obrys)", "Same obrysy wody, bez wypełnienia."),
                 (self.s_bound, "Granice miasta / gminy", "Granice administracyjne (poziom z pola niżej); grubsza linia."))
        for i, (var, text, tip) in enumerate(items):
            self.check(box, text, var, tip).grid(row=i // 2, column=i % 2, sticky="w", padx=8, pady=2)
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        self.field(g, 0, "Poziom granic (admin_level):", self.s_admin, "Poziom granic administracyjnych OSM, po przecinku. 8 = gmina/miasto w Polsce (domyślnie), 6 = powiat, 4 = województwo. "
                   "Np. 8,9 dodaje też dzielnice dużych miast.", width=10)
        self.field(g, 1, "Bok prostokąta zapytania:", self.s_tile, "Obszar jest pobierany w prostokątach tej wielkości (km). 10 pasuje do terenu i wsi; w gęstym mieście zmniejsz do 5, "
                   "jeśli serwer przekracza limit czasu.", width=10, unit="km")
        self.field(g, 2, "Szerokość PNG:", self.s_width, "Piksele (np. 4096). Przy dużym terenie 8192 daje ostrzejsze cienkie ulice, ale dłużej się liczy.", width=10, unit="px")
        r = ttk.Frame(f)
        r.pack(anchor="w", padx=14, pady=6)
        self.check(r, "Zapisz PNG", self.s_png, "Raster, który Blender nakłada na teren.").pack(side="left")
        self.check(r, "Zapisz PDF", self.s_pdf, "Wektor do edycji (czarne tło + białe ścieżki).").pack(side="left", padx=12)
        self.check(r, "Zapisz SVG", self.s_svg, "Wektor z nazwanymi grupami (drogi-glowne, drogi-poboczne, rzeki, woda, granice…) – wygodny w edycji.").pack(side="left")
        r2 = ttk.Frame(f)
        r2.pack(anchor="w", padx=14, pady=8)
        b = self.action(r2, "Generuj mapę ulic (internet)", self.do_streets,
                        "Pobiera z OpenStreetMap wybrane warstwy dla obszaru terenu i rysuje hrf_streets.* w folderze projektu. Wymaga pobranego terenu. "
                        "Pyta o zgodę; istniejące pliki dostają kopię .bak. Przerwane pobieranie wznawia się z pamięci podręcznej.")
        b.pack(side="left")
        b = ttk.Button(r2, text="Otwórz PDF", command=lambda: self.open_project_file("hrf_streets.pdf"))
        b.pack(side="left", padx=8)
        self.tip(b, "Otwiera hrf_streets.pdf w domyślnym programie (np. Illustrator).")
        b = ttk.Button(r2, text="Otwórz SVG", command=lambda: self.open_project_file("hrf_streets.svg"))
        b.pack(side="left")
        self.tip(b, "Otwiera hrf_streets.svg w domyślnym programie (np. Inkscape).")

    def tab_wycinek(self, f):
        self.intro(f, "Tekstura warstwic w wysokiej jakości tylko dla fragmentu trasy (kamera blisko). Pobiera drobniejsze wysokości dla wycinka i rysuje "
                      "gładkie warstwice. Potrzebny internet. Najłatwiej: w Blenderze (panel HRF → Teren) kliknij ‘Kopiuj komendę’ i wklej tu.")
        r = ttk.Frame(f)
        r.pack(anchor="w", padx=14, pady=(0, 4))
        b = ttk.Button(r, text="Wklej komendę z Blendera…", command=self.paste_command)
        b.pack(side="left")
        self.tip(b, "Wklej tekst skopiowany przyciskiem ‘Kopiuj komendę’ w panelu HRF w Blenderze. Program wyciągnie z niego wycinek i parametry.")
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        names = ("Szerokość południowa (lat S):", "Szerokość północna (lat N):", "Długość zachodnia (lng W):", "Długość wschodnia (lng E):")
        tips = ("Dolna granica wycinka w stopniach (np. 49.45). Musi być mniejsza niż północna.", "Górna granica wycinka w stopniach (np. 49.62).",
                "Lewa (zachodnia) granica w stopniach długości geograficznej (np. 20.52).", "Prawa (wschodnia) granica w stopniach (np. 20.70).")
        for i in range(4):
            self.field(g, i, names[i], self.l_bbox[i], tips[i], width=14, unit="°")
        self.field(g, 4, "Poziom kafelków:", self.l_zoom, "Rozdzielczość wysokości: 14 ≈ 6 m na piksel, 15 ≈ 3 m. Dla dużych wycinków użyj 13.", width=8, choices=["12", "13", "14", "15"])
        self.field(g, 5, "Oczko siatki:", self.l_step, "Odstęp punktów wysokości (m). Mniejszy = gładsze warstwice, ale dłużej.", width=8, unit="m")
        self.field(g, 6, "Warstwice co:", self.l_minor, "Odstęp cienkich warstwic w metrach (dla wycinka zwykle 10).", width=8, unit="m")
        self.field(g, 7, "Linie główne co:", self.l_major, "Odstęp grubszych linii; wielokrotność poprzedniego (np. 50).", width=8, unit="m")
        self.field(g, 8, "Szerokość PNG:", self.l_width, "Piksele. 6144–8192 daje ok. 1 m na piksel dla wycinka 5–8 km.", width=8, unit="px")
        r = ttk.Frame(f)
        r.pack(anchor="w", padx=14, pady=4)
        self.check(r, "Zapisz PNG", self.l_png, "Raster wycinka (hrf_local.png), który Blender nakłada na teren.").pack(side="left")
        self.check(r, "Zapisz PDF/SVG", self.l_vec, "Wektorowy PDF i SVG wycinka do edycji.").pack(side="left", padx=12)
        b = self.action(f, "Generuj teksturę wycinka (internet)", self.do_local,
                        "Pobiera wysokości dla wycinka, rysuje warstwice i zapisuje hrf_local.png (+ .meta.json z zasięgiem) w folderze projektu. "
                        "Pyta o zgodę. Po zakończeniu w Blenderze kliknij ‘Wczytaj teksturę ponownie’.")
        b.pack(anchor="w", padx=14, pady=8)

    def tab_osm(self, f):
        self.intro(f, "Opcjonalnie: pobiera drogi, budynki, wodę i zieleń z OpenStreetMap wzdłuż trasy (Overpass API), do mini mapy 3D w After Effects. "
                      "Potrzebny internet; przy przeciążonych serwerach może trwać długo. Wynik: hrf_osm.json.")
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        self.field(g, 0, "Promień wokół trasy:", self.o_radius, "Szerokość pasa w metrach po każdej stronie trasy (domyślnie 150).", width=8, unit="m")
        self.field(g, 1, "Długość odcinka:", self.o_chunk, "Trasa jest pobierana kawałkami tej długości (domyślnie 3000 m). Krótsze odcinki = mniejsze zapytania.", width=8, unit="m")
        self.field(g, 2, "Liczba prób:", self.o_tries, "Ile razy ponawiać każdy odcinek przy błędzie serwera (domyślnie 6, serwery są zmieniane na przemian).", width=8)
        self.field(g, 3, "Serwer Overpass:", self.o_server, "‘domyślne’ = lista (overpass.openstreetmap.fr, overpass-api.de, kumi.systems) z automatycznym przełączaniem. "
                   "Możesz wskazać jeden, np. https://overpass.openstreetmap.fr/api/interpreter.", width=52,
                   choices=["domyślne", "https://overpass.openstreetmap.fr/api/interpreter", "https://overpass-api.de/api/interpreter",
                            "https://overpass.kumi.systems/api/interpreter"])
        self.check(g, "Pobierz wszystko od nowa (ignoruj cache)", self.o_force, "Ignoruje wcześniej pobrane odcinki z cache/ i ściąga je ponownie.").grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        b = self.action(f, "Pobierz otoczenie z OSM (internet)", self.do_osm,
                        "Wysyła współrzędne odcinków trasy do serwera Overpass. Pyta o zgodę. Można przerwać przyciskiem Anuluj – pobrane odcinki zostają.")
        b.pack(anchor="w", padx=14, pady=10)

    def tab_blender(self, f):
        self.intro(f, "Otwiera Blendera z gotową sceną 3D tego projektu: trasa z wysokością, przejechany odcinek, strzałka, kamera (orbita lub za strzałką) i teren. "
                      "W Blenderze wciśnij N w oknie 3D i wybierz zakładkę HRF – tam jest panel sterowania (czas, kamera, wygląd, tekstury, render).")
        g = ttk.Frame(f)
        g.pack(anchor="w", padx=14, pady=4)
        self.blender_cb = self.field(g, 0, "Wersja Blendera:", self.blender, "Wykryte instalacje w C:\\Program Files\\Blender Foundation. Skrypt był testowany na 4.4, 4.5, 5.0 i 5.2.",
                                     width=60, choices=[])
        self.blender_data = tk.StringVar()
        lab = ttk.Label(f, textvariable=self.blender_data, font=("Segoe UI", 10, "bold"), wraplength=900, justify="left")
        lab.pack(anchor="w", padx=14, pady=(8, 0))
        self.intro_labels.append(lab)
        self.tip(lab, "Plik danych, z którego Blender zbuduje scenę: najnowszy *_hrf.json z folderu projektu wybranego na liście u góry okna. "
                      "Żeby zbudować scenę dla innej jazdy, wybierz jej projekt na liście (albo najpierw ją skonwertuj w zakładce 1. Dane).")
        b = ttk.Button(f, text="Uruchom Blendera ze sceną (F5)", command=self.do_blender)
        self.tip(b, "Startuje Blendera i buduje scenę z pliku *_hrf.json wybranego projektu (a teren, warstwice i OSM bierze z folderu projektu). "
                    "Blender otwiera się w osobnym oknie. Działa w każdej chwili, także podczas pobierania danych; to samo robi przycisk u góry okna, "
                    "wpis w menu Plik i klawisz F5.")
        b.pack(anchor="w", padx=14, pady=(6, 2))
        b = ttk.Button(f, text="Otwórz zapisaną scenę (.blend)…", command=self.open_blend)
        self.tip(b, "Otwiera w Blenderze Twój zapisany plik .blend z wcześniej zbudowaną sceną tego projektu – BEZ jej przebudowy (wszystkie ustawienia, klatki kluczowe "
                    "i ręcznie ustawione obiekty zostają). Skrypt tylko podłącza panel HRF i odbiór poleceń z tego okna, więc potem można dodawać nulle w podanych współrzędnych.")
        b.pack(anchor="w", padx=14, pady=(0, 6))
        nb = ttk.LabelFrame(f, text="Null w Blenderze (gdy scena jest już załadowana)", padding=(10, 6, 10, 8))
        nb.pack(anchor="w", fill="x", padx=14, pady=(10, 4))
        self.field(nb, 0, "Współrzędne:", self.null_coords, "Szerokość, długość geograficzna jak w Mapach Google (prawy klik na mapie → pierwsza linijka), np. 49.5791, 20.0501. "
                   "Trzecia liczba (opcjonalnie) to wysokość m n.p.m., np. 49.5791, 20.0501, 1250. Bez niej null leży na powierzchni terenu.", width=40)
        self.field(nb, 1, "Nazwa nulla:", self.null_name, "Nazwa obiektu (Empty) w Blenderze. Nulle trafiają do kolekcji HRF_Nulls i po „Przebuduj scenę” wracają na swoje współrzędne.", width=24)
        r = ttk.Frame(nb)
        r.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        b = ttk.Button(r, text="Dodaj null w Blenderze", command=lambda: self.send_blender("null"))
        b.pack(side="left")
        self.tip(b, "Wysyła współrzędne do uruchomionego Blendera, który ma załadowaną scenę tego projektu (zbudowaną przyciskiem „Uruchom Blendera ze sceną” albo otwartą "
                    "przyciskiem „Otwórz zapisaną scenę”); tam pojawia się nowy null (Empty) w tym miejscu. Działa w każdej chwili i nie blokuje innych zadań.")
        b = ttk.Button(r, text="Przenieś pivot (cel orbity) tutaj", command=lambda: self.send_blender("pivot"))
        b.pack(side="left", padx=8)
        self.tip(b, "Przenosi istniejący HRF_Pivot, wokół którego orbituje kamera, na podane współrzędne (w trybie celu orbity „null”).")
        b = ttk.Button(f, text="Kopiuj ścieżkę skryptu Blendera", command=lambda: self.copy_text(BLENDER_SCRIPT))
        b.pack(anchor="w", padx=14)
        self.tip(b, "Do ręcznego otwarcia: w Blenderze zakładka Scripting → Open → ten plik → Run Script. Ścieżka do pliku trafia do schowka.")
        self.intro(f, "Render: w panelu HRF (sekcja Render) ‘Renderuj animację’ zapisuje przezroczyste PNG; nałóż je potem w After Effects. "
                      "Plik .blend zapisz przed renderem (domyślny folder wyjściowy leży obok niego).")

    def tab_ae(self, f):
        self.intro(f, "Panel z nakładką danych (liczniki, mapa trasy, profil wysokości, mini mapa 3D) dla After Effects. Używa tego samego pliku *_hrf.json co Blender.")
        steps = ("1. W After Effects: Plik → Skrypt → Uruchom plik skryptu… → wskaż AE\\hrf_ae_panel.jsx (przycisk niżej kopiuje ścieżkę).\n"
                 "2. W panelu: ‘Wczytaj dane…’ i wskaż plik *_hrf.json projektu (przycisk niżej otwiera go w Eksploratorze).\n"
                 "3. Zaznacz wideo w panelu Projekt → ‘Dodaj klip…’. Opcjonalnie wskaż hrf_osm.json (otoczenie z OSM).\n"
                 "4. Render z Blendera (PNG z przezroczystością) importuj jako sekwencję i połóż nad wideo.")
        ttk.Label(f, text=steps, justify="left").pack(anchor="w", padx=14, pady=4)
        r = ttk.Frame(f)
        r.pack(anchor="w", padx=14, pady=8)
        b = ttk.Button(r, text="Kopiuj ścieżkę panelu AE", command=lambda: self.copy_text(AE_PANEL))
        b.pack(side="left")
        self.tip(b, "Kopiuje do schowka pełną ścieżkę do AE\\hrf_ae_panel.jsx.")
        b = ttk.Button(r, text="Pokaż plik danych (*_hrf.json)", command=lambda: self.reveal(self.activity_file()))
        b.pack(side="left", padx=8)
        self.tip(b, "Otwiera Eksplorator z zaznaczonym plikiem *_hrf.json wybranego projektu – wskaż go w panelu AE (‘Wczytaj dane…’).")
        b = ttk.Button(r, text="Pokaż plik OSM", command=lambda: self.reveal(self.pfile("hrf_osm.json")))
        b.pack(side="left")
        self.tip(b, "Otwiera Eksplorator z zaznaczonym hrf_osm.json (opcjonalne otoczenie dla mini mapy 3D w AE).")

    def tab_about(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x", padx=14, pady=(14, 6))
        if getattr(self, "icon", None) is not None:
            small = self.icon.subsample(4, 4)
            self.about_icon = small
            ttk.Label(top, image=small).pack(side="left", padx=(0, 14))
        info = ttk.Frame(top)
        info.pack(side="left")
        ttk.Label(info, text="HRF – dane ze Stravy w After Effects i Blenderze", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        rev = git_revision()
        ttk.Label(info, text="Wersja %s%s  •  autor: HRF (haraf.pl)" % (VERSION, " (" + rev + ")" if rev else ""), foreground="#555").pack(anchor="w")
        link = ttk.Label(info, text=REPO_URL, foreground="#1a5fb4", cursor="hand2")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: webbrowser.open(REPO_URL))
        self.tip(link, "Otwiera stronę projektu na GitHubie (kod, opis, historia zmian) w przeglądarce.")
        sections = [
            ("Co to robi",
             ["Z jednego pliku FIT/GPX ze Stravy robi komplet materiałów do filmu z jazdy: nakładkę z danymi dla After Effects "
              "(prędkość, tętno, wysokość, mapa trasy, profil, mini mapa 3D) oraz animowaną scenę 3D w Blenderze "
              "(trasa z prawdziwą wysokością, strzałka jadąca po trasie, kamera orbitująca albo jadąca za strzałką, teren, warstwice)."]),
            ("Z czego się składa",
             ["•  tools/ – narzędzia Pythona (konwerter, teren, warstwice, tekstura lokalna, OSM); to one wykonują całą pracę,",
              "•  Blender/hrf_blender_route.py – skrypt budujący scenę i panel sterowania HRF w Blenderze,",
              "•  AE/hrf_ae_panel.jsx – panel dla After Effects,",
              "•  HRF.pyw – to okno: wybór projektu, uruchamianie narzędzi, log, podpowiedzi przy każdej kontrolce."]),
            ("Prywatność",
             ["Plik aktywności i ślad GPS zostają na Twoim dysku (folder data/ nie trafia do GitHuba). Z internetu pobierane są tylko: "
              "wysokości terenu (wysyłane są wyłącznie numery kafelków, nie trasa) i – opcjonalnie – dane OpenStreetMap wzdłuż trasy; "
              "przed każdym takim połączeniem program mówi, co zostanie wysłane i pyta o zgodę."]),
            ("Źródła danych (podaj je przy publikacji filmu)",
             ["•  Wysokości terenu: Mapzen / AWS Terrain Tiles (SRTM, NED i inne otwarte zbiory),",
              "•  Drogi, budynki, woda, zieleń: © współtwórcy OpenStreetMap (licencja ODbL), pobierane przez Overpass API."]),
            ("Wymagania",
             ["Python 3 z tkinter (okno i narzędzia), opcjonalnie fitparse dla plików .fit, Blender 4.4–5.2, After Effects."]),
        ]
        body = tk.Text(f, wrap="word", font=("Segoe UI", 10), relief="flat", background=self.root.cget("background"), height=22, borderwidth=0)
        body.pack(fill="both", expand=True, padx=14, pady=6)
        body.tag_config("h", font=("Segoe UI", 10, "bold"), spacing1=4)
        nl = chr(10)
        for head, lines in sections:
            body.insert("end", head + nl, "h")
            body.insert("end", nl.join(lines) + nl + nl)
        body.config(state="disabled")

    # ---- dolny log
    def build_log(self):
        f = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        f.pack(side="bottom", fill="x")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.progress_lbl = ttk.Label(top, text="Gotowe.", foreground="#444")
        self.progress_lbl.pack(side="left")
        self.bar = ttk.Progressbar(top, mode="determinate", length=200)
        self.bar.pack(side="left", padx=10)
        self.cancel_btn = ttk.Button(top, text="Anuluj zadanie", command=self.cancel_job, state="disabled")
        self.cancel_btn.pack(side="right")
        self.tip(self.cancel_btn, "Przerywa uruchomione narzędzie. Pobrane kafelki i odcinki zostają w cache/, więc kolejne uruchomienie dokończy pracę.")
        b = ttk.Button(top, text="Wyczyść log", command=lambda: self.log.delete("1.0", "end"))
        b.pack(side="right", padx=6)
        self.tip(b, "Czyści okno logu poniżej.")
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(box, height=9, wrap="word", font=("Consolas", 9), background="#101418", foreground="#d8dee6", insertbackground="#fff")
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.config(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.tag_config("cmd", foreground="#7fb6ff")
        self.log.tag_config("err", foreground="#ff8080")
        self.log.tag_config("ok", foreground="#8de08d")
        self.tip(self.log, "Wynik uruchomionych narzędzi. Przy błędzie tutaj zobaczysz komunikat i to, co zrobić dalej.")

    # ---- projekt
    def list_projects(self):
        """Etykieta -> ścieżka. Podfoldery data/ pokazują się samą nazwą, foldery z innych miejsc dysku nazwą i ścieżką."""
        found = {}
        if os.path.isdir(self.data_dir):
            for d in sorted(os.listdir(self.data_dir)):
                full = os.path.join(self.data_dir, d)
                if os.path.isdir(full) and not d.startswith(("_", ".")) and (glob.glob(os.path.join(full, "*_hrf.json")) or os.path.isdir(os.path.join(full, "zrodlo"))):
                    found[d] = full
        for path in self.extra:
            if os.path.isdir(path) and not self.in_data(path):
                found["%s   [%s]" % (os.path.basename(path.rstrip("\\/")) or path, path)] = path
        return found

    def in_data(self, path):
        a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(self.data_dir))
        return os.path.dirname(a) == b

    def register_project(self, path):
        path = os.path.abspath(path)
        if not self.in_data(path) and path not in self.extra:
            self.extra.append(path)
        self.save_settings()

    def refresh_projects(self, select=None):
        self.proj_map = self.list_projects()
        labels = list(self.proj_map)
        self.cb["values"] = labels
        want = None
        if select:
            if select in self.proj_map:
                want = select
            else:
                for lab, path in self.proj_map.items():
                    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(select)):
                        want = lab
                        break
        if want:
            self.project.set(want)
        elif self.project.get() not in self.proj_map:
            self.project.set(labels[0] if labels else "")
        blenders = find_blenders()
        self.blender_cb["values"] = blenders
        if blenders and (self.blender.get() not in blenders):
            self.blender.set(self.settings.get("blender") if self.settings.get("blender") in blenders else blenders[0])
        self.on_project()

    def pdir(self):
        return self.proj_map.get(self.project.get())

    def pfile(self, name):
        d = self.pdir()
        return os.path.join(d, name) if d else None

    def activity_file(self):
        d = self.pdir()
        if not d or not os.path.isdir(d):
            return None
        c = glob.glob(os.path.join(d, "*_hrf.json"))
        return max(c, key=os.path.getmtime) if c else None

    def on_project(self):
        d = self.pdir()
        files = {
            "act": [self.activity_file()] if self.activity_file() else [],
            "terrain": [self.pfile("hrf_terrain.json")] if d else [],
            "cont": [self.pfile("hrf_contours." + e) for e in ("png", "pdf", "svg")] if d else [],
            "streets": [self.pfile("hrf_streets." + e) for e in ("png", "pdf", "svg")] if d else [],
            "local": [self.pfile("hrf_local.png")] if d else [],
            "osm": [self.pfile("hrf_osm.json")] if d else [],
        }
        for key, (lab, text) in self.status_labels.items():
            have = [x for x in files.get(key, []) if x and os.path.isfile(x)]
            if have:
                size = sum(os.path.getsize(x) for x in have)
                extra = ""
                if key in ("cont", "streets"):
                    extra = " (" + "/".join(os.path.splitext(x)[1][1:].upper() for x in have) + ")"
                lab.config(text="✔ %s%s: %s" % (text, extra, fmt_size(size)), foreground="#1a7f2e")
            else:
                lab.config(text="✘ %s: brak" % text, foreground="#b03030")
        if hasattr(self, "blender_data"):
            act = self.activity_file()
            if act:
                self.blender_data.set("Scena powstanie z projektu „%s”: %s" % (self.project.get().split("   [")[0], act))
            else:
                self.blender_data.set("Projekt „%s” nie ma jeszcze danych (*_hrf.json) – najpierw skonwertuj plik w zakładce 1. Dane." % (self.project.get() or "—"))
        self.save_settings()

    def open_project_folder(self):
        d = self.pdir()
        if d and os.path.isdir(d):
            os.startfile(d) if os.name == "nt" else subprocess.Popen(["xdg-open", d])
        else:
            messagebox.showinfo("Projekt", "Najpierw wybierz albo utwórz projekt.")

    def open_project_file(self, name):
        f = self.pfile(name)
        if f and os.path.isfile(f):
            os.startfile(f) if os.name == "nt" else subprocess.Popen(["xdg-open", f])
        else:
            messagebox.showinfo("Brak pliku", "Nie ma pliku %s – najpierw go wygeneruj." % name)

    def reveal(self, path):
        if path and os.path.isfile(path):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        else:
            messagebox.showinfo("Brak pliku", "Tego pliku jeszcze nie ma – wykonaj poprzedni krok.")

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.progress_lbl.config(text="Skopiowano do schowka.")

    # ---- dialogi
    def source_stem(self, path):
        stem = os.path.basename(path)
        for ext in (".gz", ".fit", ".gpx"):
            if stem.lower().endswith(ext):
                stem = stem[:-len(ext)]
        return safe_name(stem)

    def pick_source(self):
        p = filedialog.askopenfilename(title="Plik aktywności", filetypes=[("FIT/GPX", "*.fit *.gpx *.fit.gz *.gpx.gz"), ("Wszystkie", "*.*")])
        if p:
            self.src.set(p)
            self.new_name.set(self.source_stem(p))

    def pick_folder(self):
        d = filedialog.askdirectory(title="Folder projektu (może być nowy)", initialdir=self.proj_dir.get() or self.data_dir, mustexist=False)
        if d:
            self.proj_dir.set(os.path.normpath(d))

    def new_project_dialog(self):
        """Jedno okno: plik FIT/GPX + folder projektu + co przygotować. Po „Utwórz projekt” wszystko dzieje się samo."""
        if self.job is not None:
            messagebox.showinfo("Zadanie trwa", "Poczekaj na koniec bieżącego zadania albo je anuluj.")
            return
        win = tk.Toplevel(self.root)
        win.title("Nowy projekt")
        win.transient(self.root)
        win.resizable(False, False)
        g = ttk.Frame(win, padding=14)
        g.pack(fill="both", expand=True)
        ttk.Label(g, text="Wskaż plik z aktywnością i folder, w którym ma powstać projekt. Program skopiuje tam plik, skonwertuje go na dane "
                          "i od razu przygotuje resztę.", wraplength=620, justify="left").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        def browse_src():
            self.pick_source()
            if self.src.get() and not self.proj_dir.get():
                pass
        self.field(g, 1, "Plik FIT/GPX:", self.src, "Plik ze Stravy (.fit, .gpx, także .gz). Zostanie skopiowany do <folder projektu>/zrodlo/, oryginał zostaje nietknięty.",
                   width=54, browse=browse_src)
        self.field(g, 2, "Folder projektu:", self.proj_dir, "Dowolny folder na dysku (nowy lub istniejący): tu powstaną dane, teren, warstwice i pamięć podręczna. "
                   "Puste = data/<nazwa projektu>/ w folderze programu.", width=54, browse=self.pick_folder)
        self.field(g, 3, "Nazwa projektu:", self.new_name, "Nazwa plików danych (<nazwa>_hrf.json). Domyślnie z nazwy pliku.", width=30)
        ttk.Separator(g, orient="horizontal").grid(row=4, column=0, columnspan=4, sticky="ew", pady=8)
        ttk.Label(g, text="Po konwersji przygotuj od razu (reszta ustawień z zakładek 2, 3 i 4):").grid(row=5, column=0, columnspan=4, sticky="w")
        self.want_terrain, self.want_contours, self.want_streets, self.want_osm = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        c = self.check(g, "Teren (internet: numery kafelków wysokości)", self.want_terrain, "Pobiera wysokości terenu wokół trasy (zakładka 2. Teren: margines, zoom, oczko). "
                       "Bez tego nie powstanie teren w Blenderze.")
        c.grid(row=6, column=0, columnspan=4, sticky="w", pady=2)
        c = self.check(g, "Warstwice całego terenu (PNG)", self.want_contours, "Rysuje czarno-białą teksturę warstwic (zakładka 3. Warstwice). Wymaga terenu.")
        c.grid(row=7, column=0, columnspan=4, sticky="w", pady=2)
        c = self.check(g, "Mapa ulic, rzek i granic miasta (kontury; internet)", self.want_streets, "Czarno-białe kontury dróg, rzek, jezior i granic miasta na planszy terenu "
                       "(zakładka 4. Mapa ulic: warstwy i ustawienia). Wymaga terenu. Domyślnie tylko główne drogi i ścieżki rowerowe, bez drobnych uliczek. Dane z OpenStreetMap, wysyłane są tylko rogi prostokątów obszaru, nie trasa.")
        c.grid(row=8, column=0, columnspan=4, sticky="w", pady=2)
        c = self.check(g, "Otoczenie z OpenStreetMap (internet, opcjonalne, wolne)", self.want_osm, "Drogi, budynki, woda i zieleń do mini mapy w After Effects. "
                       "Wysyła współrzędne odcinków trasy do serwera Overpass; może trwać długo – można zrobić później w zakładce 6. OSM.")
        c.grid(row=9, column=0, columnspan=4, sticky="w", pady=2)
        wr = ttk.Frame(g)
        wr.grid(row=10, column=0, columnspan=4, sticky="w", pady=(8, 0))
        l1 = ttk.Label(wr, text="Szerokość PNG:  warstwice")
        l1.pack(side="left")
        e1 = ttk.Entry(wr, textvariable=self.c_width, width=7)
        e1.pack(side="left", padx=(4, 12))
        l2 = ttk.Label(wr, text="mapa ulic")
        l2.pack(side="left")
        e2 = ttk.Entry(wr, textvariable=self.s_width, width=7)
        e2.pack(side="left", padx=4)
        ttk.Label(wr, text="px", foreground="#666").pack(side="left")
        wtip = ("Szerokość obrazu PNG w pikselach (np. 4096; 8192 = ostrzejsze linie, ale dłużej się liczy i zajmuje więcej pamięci). Metry na piksel = szerokość "
                "obszaru / ta liczba. Wysokość wynika z proporcji terenu.")
        for w in (l1, e1, l2, e2):
            self.tip(w, wtip)
        row = ttk.Frame(g)
        row.grid(row=11, column=0, columnspan=4, sticky="e", pady=(12, 0))

        def go():
            if not self.src.get().strip() or not os.path.isfile(self.src.get().strip().strip('"')):
                messagebox.showwarning("Plik", "Wskaż istniejący plik .fit lub .gpx.", parent=win)
                return
            steps = [name for name, on in (("terrain", self.want_terrain.get()), ("contours", self.want_contours.get()), ("streets", self.want_streets.get()), ("osm", self.want_osm.get())) if on]
            net = [k for k, on in (("teren", self.want_terrain.get()), ("ulice", self.want_streets.get()), ("osm", self.want_osm.get())) if on]
            if net and not messagebox.askyesno("Połączenie z internetem", "\n\n".join(NET_INFO[k] for k in net) + "\n\nKontynuować?", parent=win):
                return
            win.destroy()
            self.do_convert(chain=steps)
        b = ttk.Button(row, text="Utwórz projekt", command=go)
        b.pack(side="right")
        self.tip(b, "Kopiuje plik do folderu projektu, konwertuje go i po kolei przygotowuje zaznaczone rzeczy. Postęp widać w logu; Anuluj zadanie przerywa.")
        b2 = ttk.Button(row, text="Anuluj", command=win.destroy)
        b2.pack(side="right", padx=8)
        self.tip(b2, "Zamyka okno bez tworzenia projektu.")
        win.grab_set()

    def add_existing_folder(self):
        d = filedialog.askdirectory(title="Istniejący folder projektu (z plikiem *_hrf.json)", initialdir=self.data_dir)
        if not d:
            return
        if not glob.glob(os.path.join(d, "*_hrf.json")):
            if not messagebox.askyesno("Brak danych", "W tym folderze nie ma jeszcze pliku *_hrf.json (konwersji). Dodać go mimo to jako projekt?"):
                return
        self.register_project(d)
        self.refresh_projects(select=d)

    def paste_command(self):
        win = tk.Toplevel(self.root)
        win.title("Wklej komendę z Blendera")
        win.geometry("640x240")
        ttk.Label(win, text="Wklej tu komendę skopiowaną w Blenderze (panel HRF → Teren → Tekstura lokalna → ‘Kopiuj komendę’):").pack(anchor="w", padx=10, pady=8)
        t = tk.Text(win, height=6, wrap="word")
        t.pack(fill="both", expand=True, padx=10)
        try:
            t.insert("1.0", self.root.clipboard_get())
        except Exception:
            pass

        def ok():
            self.apply_parsed(parse_local_command(t.get("1.0", "end")))
            win.destroy()
        ttk.Button(win, text="Użyj", command=ok).pack(pady=8)

    def apply_parsed(self, d):
        if "bbox" in d:
            for v, x in zip(self.l_bbox, d["bbox"]):
                v.set(x)
        for key, var in (("zoom", self.l_zoom), ("step", self.l_step), ("minor", self.l_minor), ("major", self.l_major), ("png-width", self.l_width)):
            if key in d:
                var.set(d[key])
        self.l_vec.set(bool(d.get("vector")))
        self.l_png.set(not d.get("no-png"))
        if "bbox" not in d:
            messagebox.showwarning("Komenda", "Nie znalazłem w tekście parametru --bbox. Sprawdź, czy skopiowano całą komendę.")

    # ---- uruchamianie zadań
    def set_busy(self, busy):
        for b in self.action_buttons:
            b.config(state="disabled" if busy else "normal")
        self.cancel_btn.config(state="normal" if busy else "disabled")
        if not busy:
            self.bar.config(mode="determinate", value=0)

    def logw(self, text, tag=None):
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")

    def run_job(self, title, args, env=None, after=None, cwd=None):
        if self.job is not None:
            messagebox.showinfo("Zadanie trwa", "Poczekaj na koniec bieżącego zadania albo je anuluj.")
            return False
        self.logw("\n>>> " + title, "cmd")
        self.logw("    " + " ".join(quote_arg(a) for a in args), "cmd")
        envv = dict(os.environ)
        envv.update({"HRF_LIVE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        envv.update(env or {})
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(args, cwd=cwd or ROOT, env=envv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, creationflags=flags)
        except Exception as e:
            self.logw("Nie udało się uruchomić: %s" % e, "err")
            return False
        self.job = {"proc": proc, "title": title, "after": after, "cancelled": False, "t0": time.time()}
        self._buf = ""
        self.set_busy(True)
        self.bar.config(mode="indeterminate")
        self.bar.start(60)
        threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
        return True

    def _reader(self, proc):
        try:
            while True:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                self.q.put(("data", chunk.decode("utf-8", "replace")))
        finally:
            proc.wait()
            self.q.put(("done", proc.returncode))

    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "data":
                    self.feed(val)
                else:
                    self.finish_job(val)
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def feed(self, text):
        self._buf += text.replace("\r\n", "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            seg = [s for s in line.split("\r") if s.strip()]
            if seg:
                s = seg[-1].rstrip()
                self.logw(s, "err" if re.match(r"\s*(\[!\]|Traceback|Error)", s) else ("ok" if s.lstrip().startswith("[OK]") else None))
        tail = [s for s in self._buf.split("\r") if s.strip()]
        if tail:
            s = tail[-1].strip()
            self.progress_lbl.config(text=s[:120])
            m = re.search(r"(\d{1,3})%", s)
            if m:
                self.bar.stop()
                self.bar.config(mode="determinate", value=min(100, int(m.group(1))))

    def finish_job(self, rc):
        job, self.job = self.job, None
        self.bar.stop()
        self.set_busy(False)
        if self._buf.strip():
            self.feed("\n")
        took = time.time() - job["t0"] if job else 0
        if job and job["cancelled"]:
            self.logw("Przerwano (%.0f s). Pobrane dane zostały w cache/ – uruchom ponownie, aby dokończyć." % took, "err")
            self.progress_lbl.config(text="Przerwano.")
        elif rc == 0:
            self.logw("Zakończono pomyślnie (%.0f s)." % took, "ok")
            self.progress_lbl.config(text="Gotowe.")
        else:
            self.logw("Zakończono z błędem (kod %s). Przeczytaj komunikat powyżej." % rc, "err")
            self.progress_lbl.config(text="Błąd.")
        self.on_project()
        if job and rc == 0 and not job["cancelled"] and job["after"]:
            job["after"]()

    def cancel_job(self):
        if self.job:
            self.job["cancelled"] = True
            try:
                self.job["proc"].terminate()
            except Exception:
                pass

    # ---- akcje
    def tool(self, name):
        return os.path.join(TOOLS, name)

    def need_activity(self):
        a = self.activity_file()
        if not a:
            messagebox.showwarning("Brak danych", "Najpierw skonwertuj plik FIT/GPX w zakładce 1. Dane (albo wybierz projekt, który ma plik *_hrf.json).")
        return a

    def num(self, var, name, kind=float, minimum=None):
        try:
            v = kind(str(var.get()).replace(",", "."))
        except ValueError:
            messagebox.showwarning("Błędna wartość", "Pole „%s” musi być liczbą." % name)
            return None
        if minimum is not None and v < minimum:
            messagebox.showwarning("Błędna wartość", "Pole „%s” musi być co najmniej %g." % (name, minimum))
            return None
        return v

    def do_convert(self, chain=None):
        src = self.src.get().strip().strip('"')
        if not src or not os.path.isfile(src):
            messagebox.showwarning("Plik", "Wskaż istniejący plik .fit lub .gpx.")
            return
        name = safe_name(self.new_name.get() or os.path.splitext(os.path.basename(src))[0])
        low = src.lower()
        if (low.endswith(".fit") or low.endswith(".fit.gz")):
            import importlib.util
            if importlib.util.find_spec("fitparse") is None:
                if not messagebox.askyesno("Brak biblioteki fitparse", "Pliki .fit czyta biblioteka fitparse (darmowa, jednorazowa instalacja przez pip z internetu).\n\n"
                                           "Zainstalować teraz? (Dla plików .gpx nie jest potrzebna.)"):
                    return
                self.run_job("Instalacja fitparse", [python_exe(), "-m", "pip", "install", "fitparse"], after=lambda: self.do_convert(chain))
                return
        chosen = self.proj_dir.get().strip().strip('"')
        proj = os.path.abspath(chosen) if chosen else os.path.join(self.data_dir, name)
        try:
            os.makedirs(os.path.join(proj, "zrodlo"), exist_ok=True)
        except OSError as e:
            messagebox.showerror("Folder projektu", "Nie można utworzyć folderu:\n%s" % e)
            return
        dst = os.path.join(proj, "zrodlo", os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
        out = os.path.join(proj, name + "_hrf.json")
        args = [python_exe(), self.tool("hrf_converter.py"), dst, "-o", out, "--force", "--zones", self.zones.get().strip() or "116,145,159,174"]
        for flag, var in (("--name", self.act_name), ("--start", self.start_code), ("--meta", self.end_code), ("--zone", self.tz)):
            if var.get().strip():
                args += [flag, var.get().strip()]
        for flag, var, label in (("--ascent", self.ascent, "Podjazdy"), ("--moving-time", self.moving, "Czas ruchu"), ("--distance", self.distance, "Dystans")):
            if var.get().strip():
                v = self.num(var, label)
                if v is None:
                    return
                args += [flag, "%g" % v]

        def after():
            self.register_project(proj)
            self.refresh_projects(select=proj)
            if chain is not None:
                self.run_chain(list(chain))
            else:
                self.nb.select(self.tabs["teren"])
        self.run_job("Konwersja %s → %s" % (os.path.basename(src), proj), args, after=after)

    def run_chain(self, steps):
        """Kolejne kroki przygotowania projektu (teren, warstwice, OSM) – każdy startuje po poprawnym końcu poprzedniego."""
        if not steps:
            self.logw("\nProjekt gotowy: %s" % (self.pdir() or ""), "ok")
            self.nb.select(self.tabs["blender"])
            return
        step, rest = steps[0], steps[1:]
        nxt = lambda: self.run_chain(rest)
        if step == "terrain":
            self.do_terrain(skip=True, then=nxt)
        elif step == "contours":
            self.do_contours(then=nxt)
        elif step == "streets":
            self.do_streets(skip=True, then=nxt)
        elif step == "osm":
            self.do_osm(skip=True, then=nxt)

    def do_terrain(self, skip=False, then=None):
        act = self.need_activity()
        if not act:
            return
        m, z, s = self.num(self.t_margin, "Margines", float, 0), self.num(self.t_zoom, "Poziom kafelków", int, 8), self.num(self.t_step, "Oczko", float, 2)
        if None in (m, z, s):
            return
        if not skip and not messagebox.askyesno("Połączenie z internetem", NET_INFO["teren"] + "\n\nKontynuować?"):
            return
        self.run_job("Pobieranie terenu", [python_exe(), self.tool("hrf_terrain_fetch.py"), act, self.pfile("hrf_terrain.json"),
                                           "--margin", "%g" % m, "--zoom", str(z), "--step", "%g" % s, "--yes"],
                     after=then or (lambda: self.nb.select(self.tabs["warstwice"])))

    def contours_args(self, terrain, out, minor, major, width, png, pdf, svg):
        a = ["--minor", "%g" % minor, "--major", "%g" % major, "--png-width", str(width if png else 0)]
        if not pdf:
            a.append("--no-pdf")
        if not svg:
            a.append("--no-svg")
        return [python_exe(), self.tool("hrf_contours.py"), terrain, out] + a + ["--yes"]

    def backup(self, base, exts):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        n = 0
        for e in exts:
            f = base + e
            if os.path.isfile(f):
                os.replace(f, "%s%s.%s.bak" % (base, e, stamp))
                n += 1
        if n:
            self.logw("Poprzednie pliki zachowano jako *.bak (%d)." % n)

    def do_contours(self, then=None):
        terrain = self.pfile("hrf_terrain.json")
        if not terrain or not os.path.isfile(terrain):
            messagebox.showwarning("Brak terenu", "Najpierw pobierz teren (zakładka 2. Teren).")
            return
        if not (self.c_png.get() or self.c_pdf.get() or self.c_svg.get()):
            messagebox.showwarning("Nic do zapisania", "Zaznacz co najmniej jedno: PNG, PDF albo SVG.")
            return
        mi, ma, w = self.num(self.c_minor, "Warstwice co", float, 1), self.num(self.c_major, "Linie główne co", float, 1), self.num(self.c_width, "Szerokość PNG", int, 256)
        if None in (mi, ma, w):
            return
        if abs(ma / mi - round(ma / mi)) > 1e-6:
            messagebox.showwarning("Odstępy", "Linie główne muszą być wielokrotnością odstępu cienkich warstwic (np. 25 i 100).")
            return
        base = self.pfile("hrf_contours")
        self.backup(base, [".png", ".pdf", ".svg", ".meta.json"])
        self.run_job("Warstwice całego terenu", self.contours_args(terrain, base, mi, ma, w, self.c_png.get(), self.c_pdf.get(), self.c_svg.get()), after=then)

    def do_streets(self, skip=False, then=None):
        terrain = self.pfile("hrf_terrain.json")
        if not terrain or not os.path.isfile(terrain):
            messagebox.showwarning("Brak terenu", "Najpierw pobierz teren (zakładka 2. Teren) – mapa ulic obejmuje ten sam obszar.")
            return
        layers = [name for name, var in (("roads", self.s_roads), ("cycleways", self.s_cycle), ("streets", self.s_small), ("paths", self.s_paths), ("rivers", self.s_rivers), ("streams", self.s_streams),
                                         ("water", self.s_water), ("boundaries", self.s_bound)) if var.get()]
        if not layers:
            messagebox.showwarning("Nic do narysowania", "Zaznacz co najmniej jedną warstwę.")
            return
        if not (self.s_png.get() or self.s_pdf.get() or self.s_svg.get()):
            messagebox.showwarning("Nic do zapisania", "Zaznacz co najmniej jedno: PNG, PDF albo SVG.")
            return
        tile, w = self.num(self.s_tile, "Bok prostokąta", float, 1), self.num(self.s_width, "Szerokość PNG", int, 256)
        if None in (tile, w):
            return
        if not skip and not messagebox.askyesno("Połączenie z internetem", NET_INFO["ulice"] + "\n\nKontynuować?"):
            return
        base = self.pfile("hrf_streets")
        self.backup(base, [".png", ".pdf", ".svg", ".meta.json"])
        args = [python_exe(), self.tool("hrf_streetmap.py"), terrain, base, "--layers", ",".join(layers), "--admin-levels", self.s_admin.get().strip() or "8",
                "--tile-km", "%g" % tile, "--png-width", str(w if self.s_png.get() else 0), "--yes"]
        if not self.s_pdf.get():
            args.append("--no-pdf")
        if not self.s_svg.get():
            args.append("--no-svg")
        self.run_job("Mapa ulic, rzek i granic", args, after=then)

    def do_local(self):
        try:
            bbox = [float(v.get().replace(",", ".")) for v in self.l_bbox]
        except ValueError:
            messagebox.showwarning("Wycinek", "Podaj cztery liczby wycinka (lub wklej komendę z Blendera).")
            return
        if bbox[1] <= bbox[0] or bbox[3] <= bbox[2]:
            messagebox.showwarning("Wycinek", "Północ musi być większa od południa, a wschód od zachodu.")
            return
        if not self.pdir() or not os.path.isdir(self.pdir()):
            messagebox.showwarning("Projekt", "Najpierw wybierz projekt.")
            return
        z, st = self.num(self.l_zoom, "Poziom kafelków", int, 8), self.num(self.l_step, "Oczko", float, 1)
        mi, ma, w = self.num(self.l_minor, "Warstwice co", float, 1), self.num(self.l_major, "Linie główne co", float, 1), self.num(self.l_width, "Szerokość PNG", int, 256)
        if None in (z, st, mi, ma, w):
            return
        if not (self.l_png.get() or self.l_vec.get()):
            messagebox.showwarning("Nic do zapisania", "Zaznacz PNG i/lub PDF/SVG.")
            return
        if not messagebox.askyesno("Połączenie z internetem", NET_INFO["wycinek"] + "\n\nKontynuować?"):
            return
        base = self.pfile("hrf_local")
        self.backup(base, [".png", ".pdf", ".svg", ".meta.json"])
        args = [python_exe(), self.tool("hrf_local_texture.py"), "--bbox", ",".join("%.6f" % x for x in bbox), "--out", base,
                "--zoom", str(z), "--step", "%g" % st, "--minor", "%g" % mi, "--major", "%g" % ma, "--png-width", str(w)]
        if not self.l_png.get():
            args.append("--no-png")
        if self.l_vec.get():
            args.append("--vector")
        args.append("--yes")
        self.run_job("Tekstura lokalna wycinka", args)

    def do_osm(self, skip=False, then=None):
        act = self.need_activity()
        if not act:
            return
        r, c, t = self.num(self.o_radius, "Promień", int, 20), self.num(self.o_chunk, "Długość odcinka", float, 500), self.num(self.o_tries, "Liczba prób", int, 1)
        if None in (r, c, t):
            return
        if not skip and not messagebox.askyesno("Połączenie z internetem", NET_INFO["osm"] + "\n\nKontynuować?"):
            return
        args = [python_exe(), self.tool("hrf_osm_fetch.py"), act, self.pfile("hrf_osm.json"), "--radius", str(r), "--chunk", "%g" % c, "--tries", str(t), "--yes"]
        srv = self.o_server.get().strip()
        if srv and srv != "domyślne":
            args += ["--server", srv]
        if self.o_force.get():
            args.append("--force")
        self.run_job("Pobieranie otoczenia z OSM", args, after=then)

    def blender_command(self):
        exe, act = self.blender.get().strip(), self.activity_file()
        return exe, act

    def send_blender(self, cmd):
        """Polecenie dla uruchomionego Blendera: plik cache/hrf_commands.json obok danych projektu; Blender odpowiada plikiem hrf_commands.ack."""
        act = self.activity_file()
        if not act:
            messagebox.showwarning("Brak danych", "Projekt nie ma jeszcze danych (*_hrf.json), więc nie ma sceny w Blenderze.")
            return
        text = self.null_coords.get().strip()
        if not text:
            messagebox.showwarning("Współrzędne", "Wpisz współrzędne, np. 49.5791, 20.0501 (szerokość, długość).")
            return
        cdir = os.path.join(os.path.dirname(act), "cache")
        os.makedirs(cdir, exist_ok=True)
        f, ack = os.path.join(cdir, "hrf_commands.json"), os.path.join(cdir, "hrf_commands.ack")
        cid = str(int(time.time() * 1000))
        try:
            pending = json.load(open(f, encoding="utf-8")) if os.path.isfile(f) else []
        except Exception:
            pending = []
        pending.append({"id": cid, "cmd": cmd, "text": text, "name": self.null_name.get().strip() or "HRF_Null"})
        tmp = f + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(pending, fh, ensure_ascii=False)
        os.replace(tmp, f)
        try:
            os.remove(ack)
        except OSError:
            pass
        self.logw("\n>>> Wysłano do Blendera: %s %s" % ("null" if cmd == "null" else "pivot", text), "cmd")
        self.progress_lbl.config(text="Czekam na odpowiedź Blendera…")
        self.poll_ack(f, ack, cid, 0)

    def poll_ack(self, f, ack, cid, waited):
        res = None
        if os.path.isfile(ack):
            try:
                for a in json.load(open(ack, encoding="utf-8")):
                    if a.get("id") == cid:
                        res = a
            except Exception:
                pass
        if res:
            self.logw("    Blender: " + res.get("msg", ""), "ok" if res.get("ok") else "err")
            self.progress_lbl.config(text="Gotowe." if res.get("ok") else "Błąd.")
            return
        if waited >= 12000:
            try:
                os.remove(f)                      # nieodebrane polecenie nie ma się wykonać później, przy przypadkowym starcie Blendera
            except OSError:
                pass
            self.logw("    Blender nie odpowiedział. Uruchom go (F5) i poczekaj, aż scena się zbuduje – musi to być scena tego samego projektu "
                      "(zakładka Blender pokazuje, z jakiego pliku powstaje).", "err")
            self.progress_lbl.config(text="Brak odpowiedzi Blendera.")
            return
        self.root.after(500, lambda: self.poll_ack(f, ack, cid, waited + 500))

    def open_blend(self):
        exe = self.blender.get().strip()
        if not exe or not os.path.isfile(exe):
            messagebox.showwarning("Blender", "Nie znaleziono Blendera. Zainstaluj go albo ustaw zmienną HRF_BLENDER na pełną ścieżkę blender.exe.")
            return
        key = self.pdir() or ""
        last = self.blends.get(key, "")
        path = filedialog.askopenfilename(title="Zapisana scena Blendera (.blend)", filetypes=[("Blender", "*.blend"), ("Wszystkie", "*.*")],
                                          initialdir=os.path.dirname(last) if last and os.path.isdir(os.path.dirname(last)) else (self.pdir() or ROOT),
                                          initialfile=os.path.basename(last) if last else "")
        if not path:
            return
        self.blends[key] = path
        self.save_settings()
        env = dict(os.environ)
        env["HRF_ATTACH"] = "1"
        act = self.activity_file()
        if act:
            env["HRF_DATA"] = act
        try:
            subprocess.Popen([exe, path, "--python", BLENDER_SCRIPT], env=env, cwd=ROOT)
        except Exception as e:
            messagebox.showerror("Blender", "Nie udało się uruchomić: %s" % e)
            return
        self.logw("\n>>> Otwarto zapisaną scenę: %s" % path, "cmd")
        self.logw("    Skrypt dołącza panel HRF bez przebudowy sceny. Dane: %s" % (act or "z pliku sceny"))

    def do_blender(self):
        exe, act = self.blender_command()
        if not exe or not os.path.isfile(exe):
            messagebox.showwarning("Blender", "Nie znaleziono Blendera. Zainstaluj go albo ustaw zmienną HRF_BLENDER na pełną ścieżkę blender.exe.")
            return
        if not act:
            messagebox.showwarning("Brak danych", "Najpierw skonwertuj plik FIT/GPX (zakładka 1. Dane).")
            return
        env = dict(os.environ)
        env["HRF_DATA"] = act
        try:
            subprocess.Popen([exe, "--python", BLENDER_SCRIPT], env=env, cwd=ROOT)
        except Exception as e:
            messagebox.showerror("Blender", "Nie udało się uruchomić: %s" % e)
            return
        self.logw("\n>>> Uruchomiono Blendera: %s" % exe, "cmd")
        self.logw("    dane: %s" % act, "cmd")
        self.logw("    W Blenderze wciśnij N w oknie 3D → zakładka HRF.")


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.quit)
    root.mainloop()


if __name__ == "__main__":
    main()
