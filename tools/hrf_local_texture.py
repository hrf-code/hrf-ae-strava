#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hrf_local_texture.py  —  tekstura warstwic w wysokiej jakości tylko dla wycinka (np. fragmentu trasy, który pokazujesz z bliska).

Globalna tekstura (hrf_contours.png) ma jedną rozdzielczość na cały teren, więc z bliska linie są grube i poszarpane.
To narzędzie pobiera dokładniejsze wysokości tylko dla wskazanego wycinka (kafelki w większym zbliżeniu, drobniejsze
oczko), liczy z nich gładkie warstwice i rysuje PNG o dużej rozdzielczości wraz z plikiem zasięgu (.meta.json).
Skrypt Blendera (panel HRF → Teren → Tekstura lokalna) sam nałoży ją na globalną w obrębie wycinka.

Komendę z właściwym --bbox podaje panel w Blenderze (przycisk „Kopiuj komendę” albo „Wygeneruj teraz”).

Użycie (potrzebny Python 3 i internet, bez dodatkowych bibliotek):
    py tools/hrf_local_texture.py --bbox 49.55,49.58,20.60,20.65 --out data/hrf_local

Opcje:
  --bbox A,B,C,D   lat_południe,lat_północ,lng_zachód,lng_wschód (wymagane)
  --out ŚCIEŻKA    prefiks plików wyjściowych (domyślnie data/hrf_local): <out>_terrain.json, <out>.png, <out>.meta.json
  --zoom N         poziom kafelków wysokości (domyślnie 14 = ok. 6 m na piksel; 15 = 3 m)
  --step M         oczko siatki wysokości w metrach (domyślnie 10)
  --minor M, --major M   odstępy warstwic (domyślnie 10 i 50 m)
  --png-width N    szerokość PNG w pikselach (domyślnie 6144)
  --vector         zapisz też PDF i SVG tego wycinka (do edycji)
  --yes            nie pytaj o zgodę
Przerwanie: Ctrl+C w dowolnej chwili (pobrane kafelki zostają w pamięci podręcznej).
"""
import argparse, importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(encoding="utf-8")   # polskie znaki w terminalu Windows
except Exception:
    pass


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(mod, argv):
    old = sys.argv
    sys.argv = [os.path.basename(mod.__file__)] + argv
    try:
        return mod.main()
    finally:
        sys.argv = old


def main():
    ap = argparse.ArgumentParser(description="Tekstura warstwic w wysokiej jakości dla wycinka (--bbox).")
    ap.add_argument("--bbox", required=True, help="lat_południe,lat_północ,lng_zachód,lng_wschód")
    ap.add_argument("--out", default="data/hrf_local")
    ap.add_argument("--zoom", type=int, default=14)
    ap.add_argument("--step", type=float, default=10.0)
    ap.add_argument("--minor", type=float, default=10.0)
    ap.add_argument("--major", type=float, default=50.0)
    ap.add_argument("--png-width", type=int, default=6144)
    ap.add_argument("--vector", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()

    terrain_file = a.out + "_terrain.json"
    tf_args = ["-", terrain_file, "--bbox", a.bbox, "--zoom", str(a.zoom), "--step", str(a.step)]
    ct_args = [terrain_file, a.out, "--minor", str(a.minor), "--major", str(a.major), "--png-width", str(a.png_width)]
    if not a.vector:
        ct_args += ["--no-pdf", "--no-svg"]
    if a.yes:
        tf_args.append("--yes")
        ct_args.append("--yes")

    print("")
    print("=== KROK 1/2: wysokości wycinka (%s) ===" % a.bbox)
    rc = call(load("hrf_terrain_fetch"), tf_args)
    if rc:
        return rc
    print("")
    print("=== KROK 2/2: warstwice i tekstura ===")
    rc = call(load("hrf_contours"), ct_args)
    if rc:
        return rc
    print("")
    print("Gotowe. W Blenderze: Teren → Tekstura lokalna → „Wczytaj teksturę ponownie” (albo uruchom skrypt od nowa).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("")
        print("  [!]  PRZERWANO (Ctrl+C). Pobrane kafelki zostały w pamięci podręcznej – uruchom tę samą komendę ponownie.")
        sys.exit(130)
