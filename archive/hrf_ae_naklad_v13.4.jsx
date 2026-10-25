/*
  HRF — nakładka danych Strava dla After Effects (wersja testowa v13)
  ------------------------------------------------------------------
  Użycie:
    1. Zaimportuj wideo do projektu i ZAZNACZ je w panelu Projekt.
    2. Plik > Skrypt > Uruchom plik skryptu... > hrf_ae_naklad.jsx
    3. Wskaż plik hrf_ae_dane_v2.json (dane aktywności, próbki co ok. 1 s).
    4. Skrypt bierze z wideo rozmiar, fps i długość, a datę i godzinę nagrania
       czyta z metadanych (XMP; gdy ich brak — data modyfikacji pliku minus
       długość, wartość przybliżona). Układ napisów, mapy i profilu jest liczony
       z rozmiaru wideo (pion/poziom). Wartości możesz poprawić w oknie.
    5. Powstają kompozycje:
         HRF_DATA     — dane jako suwaki z klatkami kluczowymi (nie ruszaj)
         HRF_MAPA     — prekomp: duża, szczegółowa mapa trasy (1080x1080)
         HRF_PROFIL   — prekomp: duży profil wysokości z osią kilometrów
         HRF_NAKLAD   — wideo (opcjonalnie) + liczniki + prekompy pomniejszone
         HRF_MAPA3D   — okrągła mini mapa nawigacyjna w prawdziwym 3D: wycinek
                        ok. 100 m wokół jadącego, kamera 3D pochylona pod
                        lekkim kątem, strzałka jadącego pośrodku, mapa obraca
                        się tak, że kierunek jazdy jest w górę
         HRF_MAPA3D_PLASKA — pomocniczo: płaska mapa (trasa + siatka co 50 m)
                        w rozmiarze 1 px = ok. 0,2 m, warstwa 3D w HRF_MAPA3D
       W HRF_NAKLAD warstwa "MAPA 3D" ma wyłączone oko — włącz ją (leży w tym
       samym miejscu co MAPA; wyłącz wtedy płaską MAPĘ).
       Prekompy możesz otworzyć i oglądać w pełnym rozmiarze albo w HRF_NAKLAD
       powiększyć dowolnie (Scale/Position warstw "MAPA" i "PROFIL").
       Nazwy dostają przyrostek " 2", " 3"... przy kolejnych uruchomieniach.
       Przed renderem MOV z alfą wyłącz widoczność warstwy VIDEO.
    6. Dopasowanie: suwak "Shift (s)" na warstwie CONTROLS (w HRF_NAKLAD),
       zakres +/- 30 s. Dodatnia wartość = dane z późniejszego momentu jazdy.
  Dane w nakładce: prędkość, tętno (bieżące/średnie/maks./strefa), wysokość,
  nachylenie, VAM, podjazdy, zjazdy, dystans, pozostało, postęp, średnia i
  maks. prędkość, czas ruchu, czas aktywności, godzina, kierunek jazdy
  (azymut), szerokość i długość geograficzna, mapa trasy ze strzałką kierunku
  i profil wysokości z kropką.
  Wszystko białe, font Arial (na czas testów).
  Nie testowane w prawdziwym AE — w razie błędu podeślij komunikat.
*/

var HRF_PAD = 30; // zapas w sekundach przed i po klipie na dopasowanie suwakiem

var HRF_REQUIRED = ["speed_kmh", "hr", "elev_m", "dist_km", "grade_pct", "gain_m", "loss_m",
    "avg_speed_kmh", "max_speed_kmh", "avg_hr", "max_hr", "vam_mh", "moving_s", "hr_zone",
    "lat", "lng", "progress_pct", "remain_km", "heading_unw"];

// ---------- funkcje pomocnicze ----------
function hrfParseClock(s) {
    var m = /(\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(s);
    if (!m) return null;
    return parseInt(m[1], 10) * 3600 + parseInt(m[2], 10) * 60 + (m[3] ? parseInt(m[3], 10) : 0);
}

function hrfParseOffset(text, activityStart) {
    text = String(text).replace(/^\s+|\s+$/g, "");
    if (text.indexOf(":") >= 0) {
        var v = hrfParseClock(text);
        var a = hrfParseClock(String(activityStart));
        if (v === null || a === null) return null;
        var diff = v - a;
        if (diff < 0) diff += 86400;
        return diff;
    }
    var n = parseFloat(text.replace(",", "."));
    return isNaN(n) ? null : n;
}

function hrfWindow(times, values, off, dur, pad) {
    // zwraca {t:[czasy w kompozycji danych], v:[wartości]}
    var t0 = off - pad, t1 = off + dur + pad;
    var tt = [], vv = [];
    for (var i = 0; i < times.length; i++) {
        if (times[i] < t0) continue;
        if (times[i] > t1) break;
        tt.push(times[i] - off + pad);
        vv.push(values[i]);
    }
    return { t: tt, v: vv };
}

// Start aktywności jako "czas ścienny" (ms), tak jak podaje Strava (lokalny).
function hrfActivityStartMs(str) {
    var m = /(\d{4})[.\-\/](\d{2})[.\-\/](\d{2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(String(str));
    if (!m) return null;
    return Date.UTC(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10),
        parseInt(m[4], 10), parseInt(m[5], 10), m[6] ? parseInt(m[6], 10) : 0);
}

function hrfWallFromDate(d) {
    return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds());
}

// Data ISO z metadanych wideo -> czas ścienny (ms) w strefie tego komputera.
function hrfIsoToWallMs(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/.exec(String(s));
    if (!m) return null;
    var ms = Date.UTC(parseInt(m[1], 10), parseInt(m[2], 10) - 1, parseInt(m[3], 10),
        parseInt(m[4], 10), parseInt(m[5], 10), parseInt(m[6], 10));
    var tz = m[7];
    if (tz && tz != "Z") return ms; // kamera podała czas lokalny z przesunięciem
    return hrfWallFromDate(new Date(ms)); // Z albo brak strefy: UTC -> lokalny czas komputera
}

// Zwraca {wall: ms|null, how: opis}
function hrfVideoStart(item) {
    var res = { wall: null, how: "" };
    var file = null;
    try { file = item.mainSource.file; } catch (e0) { file = null; }
    if (!file || !file.exists) { res.how = "brak pliku źródłowego"; return res; }
    try {
        if (ExternalObject.AdobeXMPScript == undefined) {
            ExternalObject.AdobeXMPScript = new ExternalObject("lib:AdobeXMPScript");
        }
        var xf = new XMPFile(file.fsName, XMPConst.UNKNOWN, XMPConst.OPEN_FOR_READ);
        var xmp = xf.getXMP();
        xf.closeFile();
        var prop = xmp.getProperty(XMPConst.NS_XMP, "CreateDate");
        if (prop && prop.value) {
            var w = hrfIsoToWallMs(prop.value);
            if (w !== null) { res.wall = w; res.how = "metadane pliku (CreateDate " + prop.value + ")"; return res; }
        }
    } catch (e1) { /* przejdź do wartości zastępczej */ }
    try {
        var mod = file.modified;
        if (mod) {
            res.wall = hrfWallFromDate(mod) - Math.round(item.duration * 1000);
            res.how = "data modyfikacji pliku minus długość (PRZYBLIŻONE)";
        }
    } catch (e2) {}
    if (res.wall === null) res.how = "nie udało się odczytać daty";
    return res;
}

// Napis (punkt zakotwiczenia = linia bazowa po lewej)
function hrfText(comp, name, x, y, size, expr, staticText) {
    var L = comp.layers.addText(staticText || "0");
    L.name = name;
    var prop = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = prop.value;
    td.resetCharStyle();
    td.fontSize = size;
    td.fillColor = [1, 1, 1];
    td.applyFill = true;
    td.applyStroke = false;
    try { td.font = "ArialMT"; } catch (e1) {}
    try { td.justification = ParagraphJustification.LEFT_JUSTIFY; } catch (e2) {}
    prop.setValue(td);
    if (expr) prop.expression = expr;
    L.property("ADBE Transform Group").property("ADBE Position").setValue([x, y]);
    return L;
}

// Łamana (otwarta lub zamknięta) jako warstwa kształtu
function hrfPoly(comp, name, pts, x, y, width, opacity, closed) {
    var sl = comp.layers.addShape();
    sl.name = name;
    var grp = sl.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
    var vecs = grp.property("ADBE Vectors Group");
    var sp = vecs.addProperty("ADBE Vector Shape - Group");
    var shp = new Shape();
    shp.vertices = pts;
    shp.closed = !!closed;
    sp.property("ADBE Vector Shape").setValue(shp);
    var st = vecs.addProperty("ADBE Vector Graphic - Stroke");
    st.property("ADBE Vector Stroke Color").setValue([1, 1, 1]);
    st.property("ADBE Vector Stroke Width").setValue(width);
    try {
        st.property("ADBE Vector Stroke Line Cap").setValue(2);
        st.property("ADBE Vector Stroke Line Join").setValue(2);
    } catch (e) {}
    var tr = sl.property("ADBE Transform Group");
    tr.property("ADBE Anchor Point").setValue([0, 0]);
    tr.property("ADBE Position").setValue([x, y]);
    tr.property("ADBE Opacity").setValue(opacity);
    return { layer: sl, vecs: vecs };
}

// Wypełniony kształt (elipsa albo wielokąt)
function hrfFilled(comp, name, pts, radius) {
    var sl = comp.layers.addShape();
    sl.name = name;
    var grp = sl.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
    var vecs = grp.property("ADBE Vectors Group");
    if (pts) {
        var sp = vecs.addProperty("ADBE Vector Shape - Group");
        var shp = new Shape();
        shp.vertices = pts;
        shp.closed = true;
        sp.property("ADBE Vector Shape").setValue(shp);
    } else {
        var el = vecs.addProperty("ADBE Vector Shape - Ellipse");
        el.property("ADBE Vector Ellipse Size").setValue([radius * 2, radius * 2]);
    }
    var fl = vecs.addProperty("ADBE Vector Graphic - Fill");
    fl.property("ADBE Vector Fill Color").setValue([1, 1, 1]);
    sl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
    return sl;
}

function hrfRound(x, n) {
    var k = Math.pow(10, n);
    return Math.round(x * k) / k;
}

// ---------- główny program ----------
function hrfMain() {
    var f = File.openDialog("Wskaż plik JSON z danymi (hrf_ae_dane_v2.json)", "JSON:*.json,Wszystkie:*.*");
    if (!f) return;
    f.encoding = "UTF-8";
    f.open("r");
    var txt = f.read();
    f.close();

    var D;
    try {
        D = eval("(" + txt + ")");
    } catch (e) {
        alert("Nie można odczytać pliku JSON:\n" + e);
        return;
    }
    var S = D.samples;
    if (!S || !S.time_s || !S.time_s.length) {
        alert("W pliku brak pola samples.time_s.");
        return;
    }
    var missing = [];
    for (var q = 0; q < HRF_REQUIRED.length; q++) {
        if (!S[HRF_REQUIRED[q]] || S[HRF_REQUIRED[q]].length != S.time_s.length) missing.push(HRF_REQUIRED[q]);
    }
    if (missing.length) {
        alert("Ten plik JSON nie ma wszystkich serii (brakuje: " + missing.join(", ") + ").\nUżyj pliku hrf_ae_dane_v2.json.");
        return;
    }

    // --- wykrycie zaznaczonego wideo ---
    var vid = null;
    for (var si = 0; si < app.project.selection.length; si++) {
        var it = app.project.selection[si];
        if (it instanceof FootageItem && it.hasVideo) { vid = it; break; }
    }
    var defSize = "1080x1920", defFps = "30", defDur = "60", defOff = "0", info = "";
    if (vid) {
        defSize = vid.width + "x" + vid.height;
        defFps = String(vid.frameRate);
        defDur = String(Math.round(vid.duration * 100) / 100);
        var vs0 = hrfVideoStart(vid);
        var as0 = hrfActivityStartMs(D.start_date_local);
        if (vs0.wall !== null && as0 !== null) {
            defOff = String(Math.round((vs0.wall - as0) / 100) / 10);
            info = "Wykryto: " + vs0.how;
        } else {
            info = "Nie wykryto początku wideo (" + vs0.how + ") — wpisz ręcznie.";
        }
    } else {
        info = "Brak zaznaczonego wideo w panelu Projekt — wpisz wartości ręcznie.";
    }

    // --- okno ustawień ---
    var w = new Window("dialog", "HRF nakładka Strava");
    w.orientation = "column";
    w.alignChildren = "fill";
    w.add("statictext", undefined, String(D.name || "Aktywność") + "\nStart aktywności: " + String(D.start_date_local || "?"), { multiline: true });
    w.add("statictext", undefined, info, { multiline: true });
    var g1 = w.add("group");
    g1.add("statictext", undefined, "Rozmiar (SxW):");
    var eSize = g1.add("edittext", undefined, defSize);
    eSize.characters = 12;
    var g2 = w.add("group");
    g2.add("statictext", undefined, "FPS:");
    var eFps = g2.add("edittext", undefined, defFps);
    eFps.characters = 6;
    var g3 = w.add("group");
    g3.add("statictext", undefined, "Długość klipu (s):");
    var eDur = g3.add("edittext", undefined, defDur);
    eDur.characters = 8;
    var g4 = w.add("group");
    g4.add("statictext", undefined, "Początek wideo (sek. lub GG:MM:SS):");
    var eOff = g4.add("edittext", undefined, defOff);
    eOff.characters = 12;
    var cbVid = null;
    if (vid) {
        cbVid = w.add("checkbox", undefined, "Dodaj wideo do kompozycji HRF_NAKLAD");
        cbVid.value = true;
    }
    var cbMap = w.add("checkbox", undefined, "Mapa trasy z kierunkiem jazdy");
    cbMap.value = true;
    var cbProf = w.add("checkbox", undefined, "Profil wysokości");
    cbProf.value = true;
    var cb3d = w.add("checkbox", undefined, "Mini mapa 3D zza pleców (wymaga mapy)");
    cb3d.value = true;
    var cbOsm = w.add("checkbox", undefined, "Otoczenie z OSM w mini mapie (plik hrf_osm.json)");
    cbOsm.value = false;
    var gb = w.add("group");
    gb.add("button", undefined, "Utwórz", { name: "ok" });
    gb.add("button", undefined, "Anuluj", { name: "cancel" });
    if (w.show() != 1) return;

    var sm = /^\s*(\d+)\s*[xX]\s*(\d+)\s*$/.exec(eSize.text);
    if (!sm) { alert("Rozmiar podaj jako 1080x1920."); return; }
    var W = parseInt(sm[1], 10), H = parseInt(sm[2], 10);
    var fps = parseFloat(eFps.text.replace(",", "."));
    var dur = parseFloat(eDur.text.replace(",", "."));
    var off = hrfParseOffset(eOff.text, D.start_date_local);
    if (isNaN(fps) || fps <= 0 || isNaN(dur) || dur <= 0 || off === null) {
        alert("Sprawdź FPS, długość i początek wideo.");
        return;
    }
    var total = S.time_s[S.time_s.length - 1];
    if (off > total || off + dur < 0) {
        alert("Wideo nie mieści się w czasie aktywności (początek " + off + " s od startu, aktywność trwa " + total +
            " s). Sprawdź, czy to wideo z tego samego dnia, albo wpisz przesunięcie ręcznie.");
        return;
    }
    var showMap = cbMap.value, showProf = cbProf.value;
    var show3d = showMap && cb3d.value;
    var OSM = null;
    if (show3d && cbOsm.value) {
        var fo = File.openDialog("Wskaż plik hrf_osm.json (z hrf_osm_pobierz.py)", "JSON:*.json,Wszystkie:*.*");
        if (fo) {
            try {
                fo.encoding = "UTF-8";
                fo.open("r");
                var otxt = fo.read();
                fo.close();
                OSM = eval("(" + otxt + ")");
            } catch (eo) {
                alert("Nie udało się wczytać pliku OSM: " + eo + "\nMapa powstanie bez otoczenia.");
                OSM = null;
            }
        }
    }

    app.beginUndoGroup("HRF nakładka Strava");
    try {
        var N = S.time_s.length;
        var PAD = HRF_PAD;

        // unikalne nazwy kompozycji (wyrażenia odwołują się do nich po nazwie)
        var suffix = "", sn = 1;
        var nameTaken = function (nm) {
            for (var ci = 1; ci <= app.project.numItems; ci++) {
                if (app.project.item(ci).name == nm) return true;
            }
            return false;
        };
        while (nameTaken("HRF_DATA" + suffix) || nameTaken("HRF_NAKLAD" + suffix) ||
               nameTaken("HRF_MAPA" + suffix) || nameTaken("HRF_PROFIL" + suffix) ||
               nameTaken("HRF_MAPA3D" + suffix) || nameTaken("HRF_MAPA3D_PLASKA" + suffix)) {
            sn++;
            suffix = " " + sn;
        }
        var dataName = "HRF_DATA" + suffix, mainName = "HRF_NAKLAD" + suffix;
        var mapName = "HRF_MAPA" + suffix, profName = "HRF_PROFIL" + suffix;
        var map3dName = "HRF_MAPA3D" + suffix;
        var flatName = "HRF_MAPA3D_PLASKA" + suffix;

        var base =
            'var d=comp("' + dataName + '").layer("DATA");' +
            'var C=comp("' + mainName + '").layer("CONTROLS");' +
            'var s=C.effect("Shift (s)")(1).value;' +
            'var o=C.effect("Start offset (s)")(1).value;' +
            'var c0=C.effect("Start clock (s of day)")(1).value;' +
            'var tt=time+' + PAD + '+s;' +
            'function val(n){return d.effect(n)(1).valueAtTime(tt);}' +
            'function p(n){return (n<10?"0":"")+n;}' +
            'function hms(t){t=Math.max(0,Math.round(t));return p(Math.floor(t/3600))+":"+p(Math.floor(t%3600/60))+":"+p(t%60);}' +
            'function f1(x){return (Math.round(x*10)/10).toFixed(1).replace(".",",");}';

        // ===== kompozycja z danymi =====
        var dcomp = app.project.items.addComp(dataName, 640, 360, 1, dur + 2 * PAD, fps);
        var dl = dcomp.layers.addNull();
        dl.name = "DATA";
        var key;
        for (key in S) {
            if (key == "time_s" || key == "heading_deg") continue;
            if (!S[key] || S[key].length != N) continue;
            var eff = dl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
            eff.name = key;
            var win = hrfWindow(S.time_s, S[key], off, dur, PAD);
            if (win.t.length) eff.property(1).setValuesAtTimes(win.t, win.v);
        }

        // ===== układ liczony z rozmiaru wideo =====
        var items = [
            ["TETNO  bpm", 'Math.round(val("hr")).toString();'],
            ["TETNO SR.  bpm", 'Math.round(val("avg_hr")).toString();'],
            ["TETNO MAKS.  bpm", 'Math.round(val("max_hr")).toString();'],
            ["STREFA TETNA", '"Z"+Math.round(val("hr_zone"));'],
            ["WYSOKOSC  m", 'Math.round(val("elev_m")).toString();'],
            ["NACHYLENIE  %", 'f1(val("grade_pct"));'],
            ["VAM  m/h", 'Math.round(val("vam_mh")).toString();'],
            ["PODJAZDY  m", 'Math.round(val("gain_m")).toString();'],
            ["ZJAZDY  m", 'Math.round(val("loss_m")).toString();'],
            ["DYSTANS  km", 'f1(val("dist_km"));'],
            ["POZOSTALO  km", 'f1(val("remain_km"));'],
            ["POSTEP  %", 'Math.round(val("progress_pct")).toString();'],
            ["SR. PREDKOSC  km/h", 'f1(val("avg_speed_kmh"));'],
            ["MAKS. PREDKOSC  km/h", 'f1(val("max_speed_kmh"));'],
            ["CZAS RUCHU", 'hms(val("moving_s"));'],
            ["CZAS AKTYWNOSCI", 'hms(o+time+s);'],
            ["GODZINA", 'hms(((Math.round(c0+o+time+s)%86400)+86400)%86400);'],
            ["KIERUNEK", 'var h=((Math.round(val("heading_unw"))%360)+360)%360;' +
                'var nm=["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];' +
                'h+String.fromCharCode(176)+" "+nm[Math.round(h/22.5)%16];'],
            ["SZEROKOSC GPS", 'val("lat").toFixed(5);'],
            ["DLUGOSC GPS", 'val("lng").toFixed(5);']
        ];
        var landscape = W >= H;
        var mx = W * 0.04, innerW = W - 2 * mx;
        var cols = landscape ? 6 : 3;
        var cw = innerW / cols;
        var vsz = cw * 0.19, lsz = cw * 0.085;
        var rows = Math.ceil(items.length / cols);
        var cellH = lsz * 1.5 + vsz * 1.35;
        var bigS = vsz * 2.6;
        var headerH = lsz * 1.5 + bigS * 0.8 + vsz * 0.3;
        var g = H * 0.015;
        var topY = H * 0.03;
        var hasTop = showMap || showProf;
        var hTop = hasTop ? (landscape ? 0.22 : 0.15) * H : 0;
        var avail = H - 2 * topY - (hasTop ? hTop + g : 0);
        var need = headerH + g + rows * cellH;
        var k = Math.min(1, avail / need);
        vsz *= k; lsz *= k; bigS *= k; cellH *= k; headerH *= k;
        var blockH = headerH + g + rows * cellH;
        var yBlock = H - topY - blockH;
        var profX = showMap ? mx + hTop + g : mx;
        var profW = innerW - (showMap ? hTop + g : 0);

        // ===== geometria mapy i profilu (wspólna dla prekompów) =====
        var MS = 1080;                 // bok prekompu z mapą
        var PW = 1920;                 // szerokość prekompu z profilem
        var PH = Math.max(240, Math.round(PW * (hTop > 0 ? hTop : 288) / Math.max(1, profW))); // proporcje jak w kadrze
        var latMin = 999, latMax = -999, lngMin = 999, lngMax = -999, i;
        for (i = 0; i < N; i++) {
            if (S.lat[i] < latMin) latMin = S.lat[i];
            if (S.lat[i] > latMax) latMax = S.lat[i];
            if (S.lng[i] < lngMin) lngMin = S.lng[i];
            if (S.lng[i] > lngMax) lngMax = S.lng[i];
        }
        var latMid = (latMin + latMax) / 2, lngMid = (lngMin + lngMax) / 2;
        var cosLat = Math.cos(latMid * Math.PI / 180);
        var mapW = (lngMax - lngMin) * cosLat, mapH = latMax - latMin;
        var mapPad = 0.08;
        var mapSc = Math.min((1 - 2 * mapPad) / (mapW || 1e-9), (1 - 2 * mapPad) / (mapH || 1e-9)); // jednostki ramki na stopień
        var eMin = 1e9, eMax = -1e9;
        for (i = 0; i < N; i++) {
            if (S.elev_m[i] < eMin) eMin = S.elev_m[i];
            if (S.elev_m[i] > eMax) eMax = S.elev_m[i];
        }
        var distTotal = S.dist_km[N - 1];
        if (distTotal <= 0) distTotal = 1;
        if (eMax - eMin < 1) eMax = eMin + 1;

        var mapPts = [], profPts = [];
        for (i = 0; i < N; i++) {
            mapPts.push([0.5 + (S.lng[i] - lngMid) * cosLat * mapSc, 0.5 - (S.lat[i] - latMid) * mapSc]);
            profPts.push([S.dist_km[i] / distTotal, 1 - (S.elev_m[i] - eMin) / (eMax - eMin)]);
        }
        var PROF_Y0 = 0.10, PROF_K = 0.70; // pionowy zakres linii profilu w prekompie

        // kropki w kompozycji danych (pozycje znormalizowane)
        var mdot = dcomp.layers.addNull();
        mdot.name = "MAPDOT";
        var wm = hrfWindow(S.time_s, mapPts, off, dur, PAD);
        if (wm.t.length) mdot.property("ADBE Transform Group").property("ADBE Position").setValuesAtTimes(wm.t, wm.v);
        var pdot = dcomp.layers.addNull();
        pdot.name = "PROFDOT";
        var wp = hrfWindow(S.time_s, profPts, off, dur, PAD);
        if (wp.t.length) pdot.property("ADBE Transform Group").property("ADBE Position").setValuesAtTimes(wp.t, wp.v);

        // postęp przycinania linii liczony po DŁUGOŚCI łamanej (a nie po dystansie),
        // dzięki temu jasna linia dochodzi dokładnie do kropki
        var mapLen = [0], profLen = [0], ddx, ddy;
        for (i = 1; i < N; i++) {
            ddx = (mapPts[i][0] - mapPts[i - 1][0]) * MS;
            ddy = (mapPts[i][1] - mapPts[i - 1][1]) * MS;
            mapLen.push(mapLen[i - 1] + Math.sqrt(ddx * ddx + ddy * ddy));
            ddx = (profPts[i][0] - profPts[i - 1][0]) * PW;
            ddy = (profPts[i][1] - profPts[i - 1][1]) * PH * PROF_K;
            profLen.push(profLen[i - 1] + Math.sqrt(ddx * ddx + ddy * ddy));
        }
        var mapTrim = [], profTrim = [];
        for (i = 0; i < N; i++) {
            mapTrim.push(mapLen[N - 1] > 0 ? mapLen[i] / mapLen[N - 1] * 100 : 0);
            profTrim.push(profLen[N - 1] > 0 ? profLen[i] / profLen[N - 1] * 100 : 0);
        }
        var idxs = [];
        for (i = 0; i < N; i++) idxs.push(i);
        var extra = [["map_trim", mapTrim], ["prof_trim", profTrim], ["idx", idxs]];
        for (var xi = 0; xi < extra.length; xi++) {
            var xe = dl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
            xe.name = extra[xi][0];
            var xw = hrfWindow(S.time_s, extra[xi][1], off, dur, PAD);
            if (xw.t.length) xe.property(1).setValuesAtTimes(xw.t, xw.v);
        }

        // ===== kompozycja nakładki (rozmiar = rozmiar wideo) — tworzona przed prekompami =====
        var comp = app.project.items.addComp(mainName, W, H, 1, dur, fps);
        if (vid && cbVid && cbVid.value) {
            var vl = comp.layers.add(vid);
            vl.name = "VIDEO";
        }
        var ctrl = comp.layers.addNull();
        ctrl.name = "CONTROLS";
        var sh = ctrl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
        sh.name = "Shift (s)";
        sh.property(1).setValue(0);
        var so = ctrl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
        so.name = "Start offset (s)";
        so.property(1).setValue(off);
        var sc0 = ctrl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
        sc0.name = "Start clock (s of day)";
        var asMs = hrfActivityStartMs(D.start_date_local);
        sc0.property(1).setValue(asMs === null ? 0 : Math.round((asMs % 86400000) / 1000));

        // ===== prekomp: duża mapa =====
        var mapComp = null, profComp = null, step = Math.max(1, Math.ceil(N / 900));
        if (showMap) {
            mapComp = app.project.items.addComp(mapName, MS, MS, 1, dur, fps);
            var lwM = MS * 0.008;
            hrfPoly(mapComp, "Map frame", [[0, 0], [MS, 0], [MS, MS], [0, MS]], 0, 0, lwM * 0.6, 40, true);
            var rp = [];
            for (i = 0; i < N; i += step) rp.push([mapPts[i][0] * MS, mapPts[i][1] * MS]);
            rp.push([mapPts[N - 1][0] * MS, mapPts[N - 1][1] * MS]);
            hrfPoly(mapComp, "Map route", rp, 0, 0, lwM, 40, false);
            var done = hrfPoly(mapComp, "Map route done", rp, 0, 0, lwM * 1.7, 100, false);
            var trim = done.vecs.addProperty("ADBE Vector Filter - Trim");
            trim.property("ADBE Vector Trim End").expression = base + 'val("map_trim");';
            // start i meta
            var sx = mapPts[0][0] * MS, sy = mapPts[0][1] * MS, ex = mapPts[N - 1][0] * MS, ey = mapPts[N - 1][1] * MS;
            var sd = hrfFilled(mapComp, "Start dot", null, MS * 0.012);
            sd.property("ADBE Transform Group").property("ADBE Position").setValue([sx, sy]);
            var ed = hrfFilled(mapComp, "Finish dot", null, MS * 0.012);
            ed.property("ADBE Transform Group").property("ADBE Position").setValue([ex, ey]);
            hrfText(mapComp, "Start label", sx + MS * 0.02, sy - MS * 0.015, MS * 0.04, null, String(D.start_code || "START"));
            hrfText(mapComp, "Finish label", ex + MS * 0.02, ey - MS * 0.015, MS * 0.04, null, String(D.end_code || "META"));
            // strzałka kierunku jazdy
            var ar = MS * 0.035;
            var arrow = hrfFilled(mapComp, "Heading arrow", [[0, -1.3 * ar], [0.9 * ar, ar], [0, 0.45 * ar], [-0.9 * ar, ar]], 0);
            arrow.property("ADBE Transform Group").property("ADBE Position").expression = base +
                'var q=comp("' + dataName + '").layer("MAPDOT").transform.position.valueAtTime(tt);[q[0]*' + MS + ',q[1]*' + MS + '];';
            arrow.property("ADBE Transform Group").property("ADBE Rotate Z").expression = base + 'val("heading_unw");';
            // północ i podziałka
            hrfText(mapComp, "North label", MS * 0.93, MS * 0.075, MS * 0.055, null, "N");
            var pxPerKm = mapSc * MS / 111.32; // ramka na stopień szerokości -> piksele na km
            var nice = [0.5, 1, 2, 5, 10, 20], barKm = nice[0], ni;
            for (ni = 0; ni < nice.length; ni++) { if (nice[ni] * pxPerKm <= MS * 0.3) barKm = nice[ni]; }
            var barPx = barKm * pxPerKm;
            hrfPoly(mapComp, "Scale bar", [[0, 0], [barPx, 0]], MS * 0.05, MS * 0.94, lwM, 100, false);
            hrfText(mapComp, "Scale label", MS * 0.05, MS * 0.92, MS * 0.04, null, barKm + " km");
        }
        // ===== prekomp: duży profil =====
        if (showProf) {
            profComp = app.project.items.addComp(profName, PW, PH, 1, dur, fps);
            var lwP = PH * 0.012;
            hrfPoly(profComp, "Profile frame", [[0, 0], [PW, 0], [PW, PH], [0, PH]], 0, 0, lwP * 0.6, 40, true);
            // oś kilometrów
            var tickStep = distTotal > 40 ? 10 : (distTotal > 15 ? 5 : 2), tk;
            for (tk = 0; tk <= distTotal; tk += tickStep) {
                var tx = tk / distTotal * PW;
                hrfPoly(profComp, "Tick " + tk, [[0, PH * 0.06], [0, PH * 0.90]], tx, 0, lwP * 0.4, 15, false);
                hrfText(profComp, "Tick label " + tk, tx + PH * 0.012, PH * 0.975, PH * 0.055, null, tk + " km");
            }
            var pp = [];
            for (i = 0; i < N; i += step) pp.push([profPts[i][0] * PW, (PROF_Y0 + PROF_K * profPts[i][1]) * PH]);
            pp.push([profPts[N - 1][0] * PW, (PROF_Y0 + PROF_K * profPts[N - 1][1]) * PH]);
            hrfPoly(profComp, "Profile line", pp, 0, 0, lwP, 40, false);
            var pdone = hrfPoly(profComp, "Profile line done", pp, 0, 0, lwP * 1.7, 100, false);
            var ptrim = pdone.vecs.addProperty("ADBE Vector Filter - Trim");
            ptrim.property("ADBE Vector Trim End").expression = base + 'val("prof_trim");';
            hrfText(profComp, "Profile max", PH * 0.02, PH * 0.075, PH * 0.06, null, Math.round(eMax) + " m");
            hrfText(profComp, "Profile min", PH * 0.02, PH * 0.90 - PH * 0.012, PH * 0.06, null, Math.round(eMin) + " m");
            var pd = hrfFilled(profComp, "Profile dot", null, PH * 0.025);
            var posExpr = base + 'var q=comp("' + dataName + '").layer("PROFDOT").transform.position.valueAtTime(tt);';
            pd.property("ADBE Transform Group").property("ADBE Position").expression = posExpr +
                '[q[0]*' + PW + ',(' + PROF_Y0 + '+' + PROF_K + '*q[1])*' + PH + '];';
            var pl = hrfText(profComp, "Profile elev now", 0, 0, PH * 0.07, base + 'Math.round(val("elev_m"))+" m";', "0 m");
            pl.property("ADBE Transform Group").property("ADBE Position").expression = posExpr +
                '[Math.min(q[0]*' + PW + '+' + (PH * 0.04) + ',' + (PW - PH * 0.4) + '),(' + PROF_Y0 + '+' + PROF_K + '*q[1])*' + PH + '-' + (PH * 0.05) + '];';
        }

        // ===== okrągła mini mapa nawigacyjna w prawdziwym 3D =====
        // Płaska mapa (trasa i siatka rysowane od razu w docelowej skali) jest warstwą 3D bez obrotów;
        // kamera stoi przed nią i poniżej środka, patrząc w górę na jadącego — dzięki temu "dalsza"
        // część mapy jest naturalnie węższa (perspektywa). Kierunek jazdy w górę zapewnia obrót mapy.
        var map3d = null;
        var VIEW_M = 100;                  // promień widocznego wycinka w metrach
        var GRID_M = 50;                   // odstęp siatki w metrach
        var RING_R = MS * 0.46;            // promień okrągłego wycinka na ekranie (px)
        var TILT_DEG = 50;                 // pochylenie kamery od normalnej mapy (0 = z góry, 60 = prawie z boku)
        var CAM_D = 1500;                  // odległość kamery od jadącego (mniejsza = silniejsza perspektywa)
        var ROUTE_PTS = 6000;              // liczba punktów trasy w mapie (wycinek 100 m wymaga gęstej trasy)
        var WKM = RING_R / (VIEW_M / 1000);          // px świata na kilometr
        var KW = 111.32 / mapSc * WKM;               // px świata na jednostkę znormalizowaną (0..1) mapy

        // rozmiar płaskiej mapy: tyle, żeby perspektywa nie odsłoniła jej krawędzi w obrębie koła
        var rS = RING_R * 1.05, sT, cT, den, FL, tH, yHit;
        var TILT_USED = TILT_DEG;
        for (;;) {
            sT = Math.sin(TILT_USED * Math.PI / 180);
            cT = Math.cos(TILT_USED * Math.PI / 180);
            den = cT * CAM_D - sT * rS;
            FL = 1e9;
            if (den >= 50) {
                tH = CAM_D * cT / den;
                yHit = Math.abs(CAM_D * sT + tH * (-sT * CAM_D - cT * rS));
                FL = 2 * Math.ceil(Math.max(yHit, tH * rS, MS / 2) * 1.1);
            }
            // przy zbyt dużym pochyleniu horyzont wchodzi w kadr – zmniejszamy kąt, żeby płaszczyzna mapy go wypełniała
            if (FL <= 12000 || TILT_USED <= 20) break;
            TILT_USED -= 1;
        }
        if (FL > 30000) FL = 30000;

        var build3D = function () {
            // --- płaska mapa: kierunek jazdy w górę, jadący pośrodku ---
            var flat = app.project.items.addComp(flatName, FL, FL, 1, dur, fps);
            // warstwa sterująca: położenie jadącego (lekko uśrednione), kierunek (uśredniony dłużej) i postęp linii
            var ctl = flat.layers.addNull();
            ctl.name = "CAMCTL";
            var addSl = function (nm, expr) {
                var ef = ctl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
                ef.name = nm;
                ef.property(1).expression = expr;
            };
            var dm = 'comp("' + dataName + '").layer("MAPDOT").transform.position';
            addSl("rx", base + 'var a=0;for(var i=-1;i<=1;i++){a+=' + dm + '.valueAtTime(tt+i)[0];}(a/3-0.5)*' + KW + ';');
            addSl("ry", base + 'var a=0;for(var i=-1;i<=1;i++){a+=' + dm + '.valueAtTime(tt+i)[1];}(a/3-0.5)*' + KW + ';');
            addSl("rot", base + 'var a=0;for(var i=-3;i<=3;i++){a+=d.effect("heading_unw")(1).valueAtTime(tt+i);}-a/7;');
            addSl("trim", base + 'var a=0;for(var i=-1;i<=1;i++){a+=d.effect("map_trim")(1).valueAtTime(tt+i);}Math.max(0,Math.min(100,a/3));');
            addSl("idx", base + 'val("idx");');
            var cx = 'var C2=thisComp.layer("CAMCTL");var RX=C2.effect("rx")(1),RY=C2.effect("ry")(1),RO=C2.effect("rot")(1);';
            // każda warstwa świata (bez rodzica): Rotation = obrót mapy (+ kąt własny), Position tak, by jadący był w środku
            var place = function (lay, ox, oy, ang) {
                var tg = lay.property("ADBE Transform Group");
                tg.property("ADBE Anchor Point").setValue([0, 0]);
                tg.property("ADBE Rotate Z").expression = cx + 'RO+(' + (ang || 0) + ');';
                tg.property("ADBE Position").expression = cx +
                    'var r=RO*Math.PI/180,cs=Math.cos(r),sn=Math.sin(r);var rx=RX-(' + ox + '),ry=RY-(' + oy + ');' +
                    '[' + (FL / 2) + '-(rx*cs-ry*sn),' + (FL / 2) + '-(rx*sn+ry*cs)];';
            };

            // ----- otoczenie z OSM (opcjonalnie): zieleń, woda, budynki, drogi, nazwy ulic -----
            if (OSM) {
                var PXM = WKM / 1000;  // px świata na metr
                // tylko otoczenie trasy z okna klipu (+/- 90 s) – reszta nie jest rysowana
                var wb = [1e9, 1e9, -1e9, -1e9], wi, wcnt = 0;
                for (wi = 0; wi < N; wi++) {
                    if (S.time_s[wi] >= off - 90 && S.time_s[wi] <= off + dur + 90) {
                        wcnt++;
                        if (S.lat[wi] < wb[0]) wb[0] = S.lat[wi];
                        if (S.lng[wi] < wb[1]) wb[1] = S.lng[wi];
                        if (S.lat[wi] > wb[2]) wb[2] = S.lat[wi];
                        if (S.lng[wi] > wb[3]) wb[3] = S.lng[wi];
                    }
                }
                if (!wcnt) { wb = [-90, -180, 90, 180]; }
                wb[0] -= 0.0025; wb[1] -= 0.004; wb[2] += 0.0025; wb[3] += 0.004;
                var inWin = function (ll) {
                    return ll[0] >= wb[0] && ll[0] <= wb[2] && ll[1] >= wb[1] && ll[1] <= wb[3];
                };
                var inWinPath = function (path) {
                    var k;
                    for (k = 0; k < path.length; k++) if (inWin(path[k])) return true;
                    return false;
                };
                var filt = function (list) {
                    var out = [], k;
                    for (k = 0; list && k < list.length; k++) if (inWinPath(list[k])) out.push(list[k]);
                    return out;
                };
                var cvt = function (ll) {
                    return [(ll[1] - lngMid) * cosLat * mapSc * KW, -(ll[0] - latMid) * mapSc * KW];
                };
                var osmLayer = function (nm, list, closed, filled, wpx, op) {
                    list = filt(list);
                    if (!list.length) return;
                    var sl = flat.layers.addShape();
                    sl.name = nm;
                    var og = sl.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
                    var ov = og.property("ADBE Vectors Group");
                    var oi, oj, pts, shp, sg;
                    for (oi = 0; oi < list.length; oi++) {
                        pts = [];
                        for (oj = 0; oj < list[oi].length; oj++) pts.push(cvt(list[oi][oj]));
                        shp = new Shape();
                        shp.vertices = pts;
                        shp.closed = closed;
                        sg = ov.addProperty("ADBE Vector Shape - Group");
                        sg.property("ADBE Vector Shape").setValue(shp);
                    }
                    if (filled) {
                        ov.addProperty("ADBE Vector Graphic - Fill").property("ADBE Vector Fill Color").setValue([1, 1, 1]);
                    } else {
                        var os2 = ov.addProperty("ADBE Vector Graphic - Stroke");
                        os2.property("ADBE Vector Stroke Color").setValue([1, 1, 1]);
                        os2.property("ADBE Vector Stroke Width").setValue(wpx);
                        try {
                            os2.property("ADBE Vector Stroke Line Cap").setValue(2);
                            os2.property("ADBE Vector Stroke Line Join").setValue(2);
                        } catch (ec) {}
                    }
                    sl.property("ADBE Transform Group").property("ADBE Opacity").setValue(op);
                    place(sl, 0, 0, 0);
                };
                osmLayer("OSM zielen", OSM.zielen, true, true, 0, 12);
                osmLayer("OSM woda", OSM.woda, true, true, 0, 28);
                osmLayer("OSM rzeki", OSM.rzeka, false, false, 5 * PXM, 35);
                osmLayer("OSM budynki", OSM.budynek, true, true, 0, 38);
                osmLayer("OSM sciezki", OSM.sciezka, false, false, 2.5 * PXM, 28);
                osmLayer("OSM drogi", OSM.droga2, false, false, 6 * PXM, 32);
                osmLayer("OSM drogi glowne", OSM.droga1, false, false, 9 * PXM, 42);
                // nazwy ulic (każda jako osobny napis ustawiony wzdłuż odcinka)
                var nm = OSM.nazwy || [], ni, nl, rc, p1, p2, an, MAXN = 120, nmade = 0;
                for (ni = 0; ni < nm.length && nmade < MAXN; ni++) {
                    if (!inWin([nm[ni].lat, nm[ni].lng])) continue;
                    nmade++;
                    p1 = cvt([nm[ni].a[0], nm[ni].a[1]]);
                    p2 = cvt([nm[ni].a[2], nm[ni].a[3]]);
                    an = Math.atan2(p2[1] - p1[1], p2[0] - p1[0]) * 180 / Math.PI;
                    if (an > 90) an -= 180;
                    if (an <= -90) an += 180;
                    nl = hrfText(flat, "Ulica: " + nm[ni].n, 0, 0, 7 * PXM, null, String(nm[ni].n));
                    rc = nl.sourceRectAtTime(0, false);
                    nl.property("ADBE Transform Group").property("ADBE Opacity").setValue(80);
                    place(nl, cvt([nm[ni].lat, nm[ni].lng])[0], cvt([nm[ni].lat, nm[ni].lng])[1], an);
                    nl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([rc.left + rc.width / 2, rc.top + rc.height / 2]);
                }
            }

            // trasa i siatka we współrzędnych świata (px), dzieci węzła MAPROOT
            var rp = [], gstep = Math.max(1, Math.ceil(N / ROUTE_PTS));
            var bx0 = 1e9, by0 = 1e9, bx1 = -1e9, by1 = -1e9, wx, wy;
            for (i = 0; i < N; i += gstep) {
                wx = (mapPts[i][0] - 0.5) * KW; wy = (mapPts[i][1] - 0.5) * KW;
                rp.push([wx, wy]);
                if (wx < bx0) bx0 = wx; if (wx > bx1) bx1 = wx;
                if (wy < by0) by0 = wy; if (wy > by1) by1 = wy;
            }
            rp.push([(mapPts[N - 1][0] - 0.5) * KW, (mapPts[N - 1][1] - 0.5) * KW]);

            var gspx = WKM * GRID_M / 1000;  // odstęp siatki w px
            var gpad = gspx * 3;
            var gx0 = Math.floor((bx0 - gpad) / gspx) * gspx, gy0 = Math.floor((by0 - gpad) / gspx) * gspx;
            var gW = bx1 - gx0 + gpad, gH = by1 - gy0 + gpad;
            try {
                var gv;
                for (gv = 0; gv < 2; gv++) {
                    var gl2 = flat.layers.addShape();
                    gl2.name = gv == 0 ? "Grid V" : "Grid H";
                    var gg = gl2.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
                    var gvv = gg.property("ADBE Vectors Group");
                    var gsh = gvv.addProperty("ADBE Vector Shape - Group");
                    var gsp2 = new Shape();
                    gsp2.vertices = gv == 0 ? [[0, 0], [0, gH]] : [[0, 0], [gW, 0]];
                    gsp2.closed = false;
                    gsh.property("ADBE Vector Shape").setValue(gsp2);
                    var gst = gvv.addProperty("ADBE Vector Graphic - Stroke");
                    gst.property("ADBE Vector Stroke Color").setValue([1, 1, 1]);
                    gst.property("ADBE Vector Stroke Width").setValue(MS * 0.003);
                    var grp = gvv.addProperty("ADBE Vector Filter - Repeater");
                    grp.property("ADBE Vector Repeater Copies").setValue(Math.ceil((gv == 0 ? gW : gH) / gspx) + 1);
                    grp.property("ADBE Vector Repeater Transform").property("ADBE Vector Repeater Position").setValue(gv == 0 ? [gspx, 0] : [0, gspx]);
                    gl2.property("ADBE Transform Group").property("ADBE Opacity").setValue(18);
                    place(gl2, gx0, gy0);
                }
            } catch (eg) {}
            var lw = MS * 0.03; // szerokość linii trasy (ok. 6 m)
            var r1 = hrfPoly(flat, "Route", rp, 0, 0, lw, 55, false);
            place(r1.layer, 0, 0);
            // "Route done": ślad za jadącym budowany wyrażeniem (bez Trim Paths) – kończy się dokładnie w punkcie strzałki
            var r2 = hrfPoly(flat, "Route done", [[0, 0], [1, 1]], 0, 0, lw * 1.4, 100, false);
            var pj = [], pi2;
            for (pi2 = 0; pi2 < rp.length; pi2++) pj.push("[" + Math.round(rp[pi2][0] * 10) / 10 + "," + Math.round(rp[pi2][1] * 10) / 10 + "]");
            var WINPTS = Math.ceil(400 / gstep);   // ile ostatnich próbek (ok. 400 s jazdy) rysować za jadącym
            r2.vecs.property("ADBE Vector Shape - Group").property("ADBE Vector Shape").expression =
                'var C2=thisComp.layer("CAMCTL");var P=[' + pj.join(",") + '];' +
                'var k=Math.max(1,Math.min(P.length-1,Math.floor(C2.effect("idx")(1)/' + gstep + ')));' +
                'var Q=P.slice(Math.max(0,k-' + WINPTS + '),k+1);Q.push([C2.effect("rx")(1),C2.effect("ry")(1)]);' +
                'createPath(Q,[],[],false);';
            place(r2.layer, 0, 0);
            // strzałka jadącego pośrodku (leży na mapie, nie obraca się razem z nią)
            var ar = MS * 0.04;
            var arw = hrfFilled(flat, "Rider arrow", [[0, -1.3 * ar], [0.9 * ar, ar], [0, 0.45 * ar], [-0.9 * ar, ar]], 0);
            arw.property("ADBE Transform Group").property("ADBE Position").setValue([FL / 2, FL / 2]);

            // --- wersja zewnętrzna: tło, mapa jako warstwa 3D, kamera, pierścień ---
            var c3 = app.project.items.addComp(map3dName, MS, MS, 1, dur, fps);
            var bgPts = [], rk;
            for (rk = 0; rk < 72; rk++) {
                bgPts.push([MS / 2 + Math.cos(rk / 72 * 2 * Math.PI) * RING_R, MS / 2 + Math.sin(rk / 72 * 2 * Math.PI) * RING_R]);
            }
            var disc = hrfFilled(c3, "Backdrop", bgPts, 0);
            disc.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
            disc.property("ADBE Transform Group").property("ADBE Opacity").setValue(40);
            try {
                disc.property("ADBE Root Vectors Group").property(1).property("ADBE Vectors Group")
                    .property("ADBE Vector Graphic - Fill").property("ADBE Vector Fill Color").setValue([0, 0, 0]);
            } catch (ed) {}
            var fl = c3.layers.add(flat);
            fl.name = "MAP";
            fl.threeDLayer = true;
            fl.property("ADBE Transform Group").property("ADBE Position").setValue([MS / 2, MS / 2, 0]);
            // kamera: przed mapą i poniżej środka, patrzy w górę na jadącego (środek mapy)
            var cam = c3.layers.addCamera("Camera", [MS / 2, MS / 2]);
            cam.property("ADBE Camera Options Group").property("ADBE Camera Zoom").setValue(CAM_D);
            var camTr = cam.property("ADBE Transform Group");
            var camPoi = camTr.property("ADBE Point of Interest") || camTr.property("ADBE Anchor Point");
            camPoi.setValue([MS / 2, MS / 2, 0]);
            camTr.property("ADBE Position").setValue([MS / 2, MS / 2 + CAM_D * sT, -CAM_D * cT]);
            var ringPts = [];
            for (rk = 0; rk < 72; rk++) {
                ringPts.push([Math.cos(rk / 72 * 2 * Math.PI) * RING_R, Math.sin(rk / 72 * 2 * Math.PI) * RING_R]);
            }
            hrfPoly(c3, "Ring", ringPts, MS / 2, MS / 2, MS * 0.008, 80, true);
            return c3;
        };
        if (show3d && mapComp) {
            map3d = build3D();
        }

        // prekompy w kadrze głównym (pomniejszone; w razie potrzeby powiększ Scale/Position)
        if (mapComp) {
            var ml = comp.layers.add(mapComp);
            ml.name = "MAPA";
            ml.property("ADBE Transform Group").property("ADBE Position").setValue([mx + hTop / 2, topY + hTop / 2]);
            ml.property("ADBE Transform Group").property("ADBE Scale").setValue([hTop / MS * 100, hTop / MS * 100]);
            if (map3d) ml.enabled = false; // mapa 2D ukryta, gdy jest mini mapa 3D (włącz okiem)
        }
        var add3dLayer = function (c3, nm) {
            var l3 = comp.layers.add(c3);
            l3.name = nm;
            l3.property("ADBE Transform Group").property("ADBE Position").setValue([mx + hTop / 2, topY + hTop / 2]);
            l3.property("ADBE Transform Group").property("ADBE Scale").setValue([hTop / MS * 100, hTop / MS * 100]);
            l3.enabled = true;
            // maska okrągła z miękką krawędzią
            try {
                var mk = l3.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");
                var rr = RING_R, kk = 0.5523 * rr, cc = MS / 2;
                var mshape = new Shape();
                mshape.vertices = [[cc, cc - rr], [cc + rr, cc], [cc, cc + rr], [cc - rr, cc]];
                mshape.outTangents = [[kk, 0], [0, kk], [-kk, 0], [0, -kk]];
                mshape.inTangents = [[-kk, 0], [0, -kk], [kk, 0], [0, kk]];
                mshape.closed = true;
                mk.property("ADBE Mask Shape").setValue(mshape);
                mk.property("ADBE Mask Feather").setValue([MS * 0.01, MS * 0.01]);
            } catch (em) {}
            return l3;
        };
        if (map3d) add3dLayer(map3d, "MAPA 3D (zza plecow)");
        if (profComp) {
            var pl2 = comp.layers.add(profComp);
            pl2.name = "PROFIL";
            pl2.property("ADBE Transform Group").property("ADBE Position").setValue([profX + profW / 2, topY + hTop / 2]);
            pl2.property("ADBE Transform Group").property("ADBE Scale").setValue([profW / PW * 100, profW / PW * 100]);
        }

        // ===== liczniki =====
        hrfText(comp, "Speed label", mx, yBlock + lsz, lsz, null, "PREDKOSC  km/h");
        hrfText(comp, "Speed value", mx, yBlock + lsz * 1.5 + bigS * 0.8, bigS,
            base +
            'var v=val("speed_kmh");' +
            'if(v<0.5){var dd=d.effect("dist_km")(1);var kk=(dd.valueAtTime(tt+2)-dd.valueAtTime(tt-2))/4*3600;if(kk>v)v=kk;}' +
            'Math.round(v).toString();', "0");
        for (var r = 0; r < items.length; r++) {
            var col = r % cols, row = Math.floor(r / cols);
            var cx = mx + col * cw;
            var cy = yBlock + headerH + g + row * cellH;
            hrfText(comp, "Label " + (r + 1), cx, cy + lsz, lsz, null, items[r][0]);
            hrfText(comp, "Value " + (r + 1), cx, cy + lsz * 1.5 + vsz, vsz, base + items[r][1], "0");
        }

        ctrl.moveToEnd();
        comp.openInViewer();
    } catch (err) {
        alert("Błąd skryptu:\n" + err.toString() + "\nlinia: " + err.line);
    }
    app.endUndoGroup();
}

hrfMain();
