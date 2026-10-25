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
4. (opcjonalnie) otoczenie z OSM: `py tools/hrf_osm_fetch.py <dane>.json hrf_osm.json`,
   potem w panelu zaznacz „Otoczenie z OSM”.

## Struktura
```
hrf-ae-strava/
├─ AE/hrf_ae_panel.jsx           # aktualny panel (v14.x), ExtendScript (ES3: bez let/const/=>/JSON)
├─ tools/
│  ├─ hrf_converter.py           # FIT/GPX → JSON
│  └─ hrf_osm_fetch.py           # drogi/budynki/woda/zieleń z Overpass API
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
