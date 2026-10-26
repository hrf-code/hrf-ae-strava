# data/ – dane aktywności (prywatne)

Ten folder zawiera ślad GPS i pochodne pliki, więc **nie trafia do GitHuba** (`.gitignore`: `data/*`, poza tym opisem).

**Jeden projekt = jedna aktywność = jeden podfolder.** Podfolder tworzy program `HRF.pyw` (przycisk
„Nowy projekt z pliku FIT/GPX…”) albo konwerter z terminala.

```
data/
└─ <projekt>/
   ├─ zrodlo/<plik>.fit|.gpx          # oryginał ze Stravy (kopia)
   ├─ <projekt>_hrf.json              # dane aktywności (konwerter) – wczytuje je AE i Blender
   ├─ hrf_terrain.json                # wysokości terenu wokół trasy (hrf_terrain_fetch)
   ├─ hrf_contours.png / .pdf / .svg  # warstwice całego terenu (+ .meta.json z zasięgiem)
   ├─ hrf_streets.png / .pdf / .svg   # mapa ulic, rzek, wody i granic miasta (kontury; osobno od warstwic, ta sama plansza)
   ├─ hrf_local.png / .meta.json      # tekstura lokalna wycinka (hrf_local_texture)
   ├─ hrf_local_terrain.json          # wysokości wycinka (pośredni, do ponownego liczenia warstwic)
   ├─ hrf_osm.json                    # drogi/budynki/woda/zieleń z OpenStreetMap (opcjonalnie)
   ├─ *.bak                           # kopie poprzednich wersji przed nadpisaniem (np. edytowany PDF)
   └─ cache/                          # pobrane kafelki, odcinki OSM i logi – można usunąć, zostanie pobrane od nowa
      ├─ hrf_terrain/  hrf_local_terrain/  hrf_osm/
      └─ hrf_local.log
```

Zasady:
- Projekt nie musi leżeć w `data/`: w programie `HRF.pyw` możesz wskazać dowolny folder na dysku (układ w środku jest taki sam).
- Skrypt Blendera i narzędzia szukają plików **w folderze projektu**; wystarczy, że wskażesz `<projekt>_hrf.json`.
- Starsze pamięci podręczne `<plik>.cache` są przenoszone do `cache/` automatycznie przy pierwszym użyciu narzędzia.
- Kopię projektu (np. na dysk zewnętrzny) robisz kopiując cały podfolder; usunięcie `cache/` odzyskuje miejsce
  (ponowne pobranie trwa tylko dla brakujących kafelków).
