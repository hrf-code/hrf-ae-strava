# HRF – dane ze Stravy w After Effects

Nakładka danych (prędkość, tętno, wysokość, mapa, mini mapa 3D, profil…) do wideo z jazdy, sterowana plikiem FIT/GPX.

## Instalacja
- **After Effects** – panel nie wymaga instalacji: Plik → Skrypt → Uruchom plik skryptu → `AE/hrf_ae_panel.jsx`.
  Na stałe: skopiuj plik do `…/Adobe After Effects <wersja>/Support Files/Scripts/ScriptUI Panels/`
  (pojawi się w menu Okno).
- **Python 3** (Windows: `py`) do konwertera. Dla plików FIT potrzebny `fitparse`
  (`py -m pip install fitparse`; skrypt sam zapyta o instalację).

## Przepływ pracy
1. Pobierz ze Stravy `.fit` / `.gpx` (aktywność → „⋯” → Eksportuj oryginał / Eksportuj GPX)
   i wrzuć do `data/`.
2. `py tools/hrf_converter.py` – przeszukuje folder `data/`, tworzy `<nazwa>_hrf.json`
   (można też podać plik lub folder jako argument).
   Sumy można wyrównać do Stravy: `--ascent 902 --moving-time 12216 --distance 59.6`.
3. W After Effects uruchom `AE/hrf_ae_panel.jsx`.
   „Wczytaj dane…” → wskaż `*_hrf.json`; zaznacz wideo w panelu Projekt → „Dodaj klip…”.
   „Odśwież dane” podmienia wartości we wszystkich klipach.
4. (opcjonalnie) otoczenie z OSM: `py tools/hrf_osm_fetch.py data/<nazwa>_hrf.json data/hrf_osm.json`,
   potem w panelu zaznacz „Otoczenie z OSM”. Narzędzie pokazuje plan (ile odcinków z pamięci podręcznej,
   ile do pobrania) i pyta o zgodę, a w trakcie postęp, próby, błędy i szacowany czas.
   **Ctrl+C przerywa w dowolnej chwili**, a ponowne uruchomienie dociąga tylko brakujące odcinki
   (`--yes` pomija pytanie, `--force` pobiera od nowa).

## Blender: trasa 3D ze strzałką i kamerą (opcjonalnie)
`Blender/hrf_blender_route.py` buduje z tego samego pliku `*_hrf.json` scenę: ścieżka z prawdziwą wysokością (oś Z),
strzałka jadąca po trasie i kamera. W Blenderze: zakładka Scripting → Open → ten plik → (opcjonalnie ustaw `DATA_PATH`
w `CONFIG`) → Run Script. Potem Render → Render Animation: PNG z przezroczystością w `hrf_render/`, do nałożenia w AE.
**Panel w Blenderze:** po uruchomieniu skrypt dodaje do okna 3D panel (klawisz `N` → zakładka **HRF**):
„Przebuduj scenę" (po zmianie przewyższenia, terenu, pliku), „Zastosuj czas i kamerę" (szybko, ok. 0,1 s),
kolory, grubość linii, rozmiar strzałki i słońce zmieniają się na żywo, są też przyciski renderu klatki i animacji.
Wartości zapisują się w scenie `.blend`; żeby panel wracał po otwarciu pliku, zaznacz „Register" przy skrypcie
w edytorze tekstu. Wartości w `CONFIG` na górze skryptu są punktem startowym przy każdym uruchomieniu skryptu.
- `CAM_MODE = "chase"` – kamera za strzałką i nad nią (okno klipu z `START_MIN` i `END_MIN`).
- `CAM_MODE = "orbit"` + `MODE = "full"` – kamera krąży wokół celu (`ORBIT_TURNS`), strzałka jedzie po trasie; przelot skompresowany
  do `FULL_SECONDS` sekund. **Cel orbity** (`ORBIT_TARGET`): `null` = obiekt `HRF_Pivot` (domyślnie na środku trasy; zaznacz go
  przyciskiem w panelu i przesuwaj klawiszem G, ustawiaj w Item (N) albo animuj – kamera zawsze patrzy na niego i krąży wokół)
  albo `arrow` = strzałka (kamera jedzie razem z nią). `ORBIT_DISTANCE_M` – odległość kamery od celu w metrach (0 = auto:
  przy nullu cała trasa w kadrze, przy strzałce bliżej; w panelu przełącznik „Odległość ręcznie” startuje od bieżącej odległości). Orbita jest układem w scenie (`HRF_Pivot` → `HRF_Rig` → `HRF_Camera`
  z constraintami), więc cel, odległość, wysokość i obroty zmieniasz w panelu na żywo; null przetrwa przebudowę sceny.
- **Czas animacji** (panel: Czas i kadr): **początek i koniec klipu w minutach od startu całej aktywności** (`START_MIN`, `END_MIN`;
  koniec 0 = do końca jazdy), a pod nimi tryb (`MODE`): `full` = to okno w `FULL_SECONDS` s; `speed` = okno z przyspieszeniem `SPEED`×
  (presety 1:1, 5×, 10×, 30×, 60×, 120×); `clip` = okno 1:1 (czas rzeczywisty). Panel pokazuje długość całej aktywności i podpis
  „N klatek = X s | prędkość | okno od–do min”. Zmiana czasu przelicza animację sama po ok. 0,7 s. Limit 36000 klatek (20 min
  przy 30 fps): np. cała 4-godzinna jazda w 1:1 to ponad 400 tys. klatek, więc zwiększ przyspieszenie lub zawęź okno.
- `EXAG` – przewyższenie terenu i trasy (1 = skala rzeczywista, domyślnie 1,5 = czytelne i naturalne, 3 = mocno przesadzone).
- **Teren (sama geometria, bez tekstur):** `py tools/hrf_terrain_fetch.py data/<nazwa>_hrf.json data/hrf_terrain.json`
  pobiera wysokości z otwartych kafelków (Mapzen/AWS Terrain Tiles) z planem, postępem i Ctrl+C jak narzędzie OSM
  (`--zoom 13`, `--step 25`, `--margin 1500`). Skrypt Blendera sam znajdzie `data/hrf_terrain.json`, zbuduje siatkę terenu,
  przyklei do niej trasę i doda światło. `TERRAIN = "off"` wyłącza teren, `TERRAIN_SKIP` odchudza siatkę.
  Do serwera trafiają tylko numery kafelków obejmujących obszar trasy. Źródło wysokości podaj przy publikacji.
- **Warstwice na terenie (edytowalne):** `py tools/hrf_contours.py data/hrf_terrain.json data/hrf_contours` tworzy
  `hrf_contours.pdf` i `.svg` (wektor: czarne tło, białe ścieżki; SVG ma nazwane grupy warstwic pomocniczych i głównych)
  oraz `.png` (tekstura). Edytujesz PDF/SVG (Illustrator, Inkscape), eksportujesz PNG **o tych samych proporcjach
  i planszy**, a w panelu Blendera wybierasz Styl terenu → „Warstwice (tekstura)” i wskazujesz PNG („Wczytaj teksturę ponownie”
  po każdej zmianie). Blender nie wczytuje PDF jako obrazu – dlatego PNG. Opcje: `--minor 25 --major 100 --png-width 4096`.
- **Tekstura lokalna (z bliska):** globalny PNG warstwic ma jedną rozdzielczość na cały teren, więc przy kamerze blisko trasy linie są
  grube i poszarpane. W panelu (Teren → styl „Warstwice” → „Tekstura lokalna”) przycisk **Kopiuj komendę** liczy wycinek z bieżącego
  okna czasu (trasa pokazana w animacji + „Margines wycinka”) i kopiuje do schowka gotową komendę; **Wygeneruj teraz** uruchamia ją
  w tle. Komenda `py tools/hrf_local_texture.py --bbox LAT_S,LAT_N,LNG_W,LNG_E --out data/hrf_local ...` pobiera drobniejsze wysokości
  tylko dla wycinka (kafelki z=14, oczko do 5 m), rysuje gładkie warstwice (co 10 m) i PNG do 8192 px z plikiem zasięgu
  `hrf_local.meta.json`. Skrypt Blendera sam nakłada ją na globalną teksturę w obrębie wycinka (przycisk „Wczytaj teksturę ponownie”).
  Wycinek `--bbox` przyjmuje też `tools/hrf_terrain_fetch.py` (`-` zamiast pliku trasy). Pliki `data/hrf_local*` są poza gitem.
- **Animowanie parametrów:** pola panelu (odległość od celu, wysokość kamery, okrążenia, start orbity, grubość linii, rozmiar strzałki…)
  animujesz normalnie: najedź na pole i wciśnij `I` (klatki widać w Dope Sheet → Scene). Orbita, grubość linii i rozmiar strzałki
  liczą **drivery** czytające te pola, więc zmiana czasu, „Przelicz animację” ani przebudowa sceny nie kasują kluczy.
- Test bez okna: `blender -b --python Blender/hrf_blender_route.py -- --start-min 60 --end-min 61 --mode clip --render-frame 100 --render-out test.png --dark`
  (flagi: `--mode full --cam orbit --exag 4 --full-seconds 20`). Sprawdzone na Blenderze 4.4, 4.5, 5.0 i 5.2.

## Struktura
```
hrf-ae-strava/
├─ AE/hrf_ae_panel.jsx           # aktualny panel (v14.x), ExtendScript (ES3: bez let/const/=>/JSON)
├─ tools/
│  ├─ hrf_converter.py           # FIT/GPX → JSON
│  ├─ hrf_osm_fetch.py           # drogi/budynki/woda/zieleń z Overpass API
│  ├─ hrf_terrain_fetch.py       # wysokości terenu wokół trasy (dla Blendera)
│  └─ hrf_contours.py            # warstwice z terenu: PDF/SVG (edycja) + PNG (tekstura)
├─ Blender/hrf_blender_route.py   # scena 3D z trasy: wysokość, strzałka, kamera (chase / orbit)
├─ archive/                      # starszy skrypt jednorazowy (bez panelu)
└─ data/                         # pliki z danymi (poza gitem: zawierają ślad GPS)
```

## Kluczowe decyzje
- Jeden projekt AE = jedna aktywność (jazda). Panel nie obsługuje kilku jazd naraz; klipów z tej jazdy może być wiele.
  Wczytanie innej trasy podmienia `HRF_DATA`, a wspólna mapa/profil/mapa 3D są budowane od nowa (stare dostają „(old)”).
- Dane aktywności w jednej kompozycji `HRF_DATA` (czas = sekundy od startu × K; K<1 dla jazd dłuższych niż ~2,7 h – limit AE 3 h).
- Wspólne kompozycje: `HRF_MAP`, `HRF_MAP3D` (+`_FLAT`), `HRF_PROFILE <PWxPH>`, `HRF_COUNTERS <WxH>`; klip = wideo + warstwy z Time Remap (Start offset + Shift na warstwie CONTROLS).
- Mini mapa 3D: warstwa 3D + kamera (bez Corner Pin); `TILT_DEG`, `CAM_D`, `VIEW_M` na górze bloku mapy.
- „Route done” to ścieżka z wyrażenia (Trim Paths nie działał w warstwie 3D).
- Na czas testów wszystko białe, font Arial.

## Format danych (`samples`)
time_s, speed_kmh, hr, elev_m, dist_km, grade_pct, gain_m, loss_m, avg_speed_kmh, max_speed_kmh,
avg_hr, max_hr, vam_mh, moving_s, hr_zone, lat, lng, progress_pct, remain_km, heading_deg, heading_unw
oraz pola: name, activity_id, start_date_local, start_code, end_code, total_dist_km, hr_zones.

## Do zrobienia
- Kolory i fonty po zakończeniu testów (teraz białe, Arial).
- Wariant układu pod poziome wideo; opcjonalnie wybór pól do pokazania w panelu.
- Rysowanie otoczenia OSM – sprawdzić pobieranie Overpass (błędy 502).
