/*
  HRF — panel danych Strava dla After Effects (v14.6, wersja testowa)
  -----------------------------------------------------------------
  Uruchomienie: Plik > Skrypt > Uruchom plik skryptu... > hrf_ae_panel.jsx
  (otwiera się małe okno-panel, które zostaje na wierzchu; wymaga zezwolenia
  skryptom na zapis plików: Preferencje > Scripting & Expressions).

  Jak to działa:
    * Dane aktywności leżą w JEDNEJ kompozycji "HRF_DATA" (cała jazda, czas =
      sekundy od startu aktywności). Wszystkie klipy czytają z niej.
    * Każdy klip (wideo z tej samej jazdy) dostaje własne kompozycje
      "HRF_OVERLAY - <nazwa wideo>", mapę, profil i mini mapę 3D. Na warstwie
      CONTROLS klipu jest "Start offset (s)" = sekundy od startu aktywności
      do pierwszej klatki klipu oraz "Shift (s)" do ręcznego dopasowania.
    * "Wczytaj dane" – wskaż JSON (<nazwa>_hrf.json). "Odśwież dane" –
      czyta ten sam plik ponownie i podmienia wartości w HRF_DATA, więc
      wszystkie klipy odświeżają się od razu. (Gdy zmieni się sama trasa,
      usuń stare klipy i dodaj je jeszcze raz.)
    * "Dodaj klip" – zaznacz wideo w panelu Projekt (albo wpisz wartości),
      panel odczyta rozmiar, fps, długość i godzinę nagrania z metadanych.
  Wszystko białe, Arial (na czas testów). Nie testowane w prawdziwym AE.
*/

var HRF_REQUIRED = ["speed_kmh", "hr", "elev_m", "dist_km", "grade_pct", "gain_m", "loss_m",
    "avg_speed_kmh", "max_speed_kmh", "avg_hr", "max_hr", "vam_mh", "moving_s", "hr_zone",
    "lat", "lng", "progress_pct", "remain_km", "heading_unw"];
var HRF_MS = 1080;   // bok prekompu z mapą
var hrfStale = {};   // nazwa -> nieaktualny prekomp (zmieniony na "(old)"), czeka na podmianę w klipach

// Pola licznika: [klucz, etykieta, wyrażenie]. "speed" to duży napis prędkości (bez wyrażenia tutaj).
// Kolejność = kolejność w siatce licznika.
var HRF_FIELDS = [
    ["speed", "PREDKOSC  km/h (duzy napis)", null],
    ["hr", "TETNO  bpm", 'Math.round(val("hr")).toString();'],
    ["hr_avg", "TETNO SR.  bpm", 'Math.round(val("avg_hr")).toString();'],
    ["hr_max", "TETNO MAKS.  bpm", 'Math.round(val("max_hr")).toString();'],
    ["hr_zone", "STREFA TETNA", '"Z"+Math.round(val("hr_zone"));'],
    ["elev", "WYSOKOSC  m", 'Math.round(val("elev_m")).toString();'],
    ["grade", "NACHYLENIE  %", 'f1(val("grade_pct"));'],
    ["vam", "VAM  m/h", 'Math.round(val("vam_mh")).toString();'],
    ["gain", "PODJAZDY  m", 'Math.round(val("gain_m")).toString();'],
    ["loss", "ZJAZDY  m", 'Math.round(val("loss_m")).toString();'],
    ["dist", "DYSTANS  km", 'f1(val("dist_km"));'],
    ["remain", "POZOSTALO  km", 'f1(val("remain_km"));'],
    ["progress", "POSTEP  %", 'Math.round(val("progress_pct")).toString();'],
    ["speed_avg", "SR. PREDKOSC  km/h", 'f1(val("avg_speed_kmh"));'],
    ["speed_max", "MAKS. PREDKOSC  km/h", 'f1(val("max_speed_kmh"));'],
    ["moving", "CZAS RUCHU", 'hms(val("moving_s"));'],
    ["elapsed", "CZAS AKTYWNOSCI", 'hms(time/K);'],
    ["clock", "GODZINA", 'hms(((Math.round(c0+time/K)%86400)+86400)%86400);'],
    ["heading", "KIERUNEK", 'var h=((Math.round(val("heading_unw"))%360)+360)%360;' +
        'var nm=["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];' +
        'h+String.fromCharCode(176)+" "+nm[Math.round(h/22.5)%16];'],
    ["lat", "SZEROKOSC GPS", 'val("lat").toFixed(5);'],
    ["lng", "DLUGOSC GPS", 'val("lng").toFixed(5);']
];
var HRF_VERSION = "v14.6";   // podnoś przy zmianach widocznych dla użytkownika; czas buildu panel bierze z daty modyfikacji pliku

// średnia wartości tablicy w oknie +/- w próbek wokół indeksu i (wygładzanie wysokości)
function hrfAvgAround(arr, i, w) {
    var a = Math.max(0, i - w), b = Math.min(arr.length - 1, i + w), sum = 0, j;
    for (j = a; j <= b; j++) sum += arr[j];
    return sum / (b - a + 1);
}

function hrfPad2(n) { return (n < 10 ? "0" : "") + n; }

// Data i godzina ostatniej zmiany pliku skryptu (= czas builda) – widoczna w panelu, żeby sprawdzić, czy działa najnowsza wersja
function hrfBuildStamp() {
    try {
        var m = new File($.fileName).modified;
        if (!m) return "?";
        return m.getFullYear() + "-" + hrfPad2(m.getMonth() + 1) + "-" + hrfPad2(m.getDate()) + " " +
            hrfPad2(m.getHours()) + ":" + hrfPad2(m.getMinutes()) + ":" + hrfPad2(m.getSeconds());
    } catch (e) { return "?"; }
}

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


function hrfSafe(s) {
    s = String(s).replace(/\.[A-Za-z0-9]{2,4}$/, "").replace(/[^A-Za-z0-9 _\-]/g, "_");
    return s.length ? s : "klip";
}

function hrfFindComp(name) {
    for (var i = 1; i <= app.project.numItems; i++) {
        var it = app.project.item(i);
        if (it instanceof CompItem && it.name == name) return it;
    }
    return null;
}

// czasy rosnące (AE wymaga) – pomija powtórzenia
function hrfIncreasing(times, values, K) {
    var t = [], v = [], last = -1e9, kk = K || 1;
    for (var i = 0; i < times.length; i++) {
        if (times[i] > last) { t.push(times[i] * kk); v.push(values[i]); last = times[i]; }
    }
    return { t: t, v: v };
}

function hrfReadJson(file) {
    file.encoding = "UTF-8";
    if (!file.open("r")) throw new Error("Nie można otworzyć pliku.");
    var txt = file.read();
    file.close();
    var D = eval("(" + txt + ")");
    var S = D.samples;
    if (!S || !S.time_s || !S.time_s.length) throw new Error("W pliku brak pola samples.time_s.");
    var missing = [];
    for (var q = 0; q < HRF_REQUIRED.length; q++) {
        if (!S[HRF_REQUIRED[q]] || S[HRF_REQUIRED[q]].length != S.time_s.length) missing.push(HRF_REQUIRED[q]);
    }
    if (missing.length) throw new Error("Brakuje serii: " + missing.join(", ") + ". Użyj pliku <nazwa>_hrf.json.");
    return D;
}

// wspólna geometria trasy (taka sama dla danych i dla klipów)
function hrfGeom(S) {
    var N = S.time_s.length, i;
    var latMin = 999, latMax = -999, lngMin = 999, lngMax = -999;
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
    var mapSc = Math.min((1 - 2 * mapPad) / (mapW || 1e-9), (1 - 2 * mapPad) / (mapH || 1e-9));
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
    return { latMid: latMid, lngMid: lngMid, cosLat: cosLat, mapSc: mapSc, eMin: eMin, eMax: eMax,
             distTotal: distTotal, mapPts: mapPts, profPts: profPts };
}

// Zapisuje / odświeża dane w kompozycji HRF_DATA (czas = sekundy od startu aktywności).
function hrfWriteData(D, filePath, progress) {
    var S = D.samples, N = S.time_s.length, i, key;
    var total = S.time_s[N - 1];
    // kompozycja w AE ma limit 3 h (10800 s) – dłuższe aktywności zapisujemy w skróconej skali czasu (K)
    var K = 1;
    while ((total + 60) * K > 10000) K = K / 2;
    var dcomp = hrfFindComp("HRF_DATA");
    var isNew = !dcomp;
    if (isNew) {
        dcomp = app.project.items.addComp("HRF_DATA", 640, 360, 1, (total + 60) * K, 30);
    } else {
        try { dcomp.duration = (total + 60) * K; } catch (ed) {}
    }
    var dl = null, li;
    for (li = 1; li <= dcomp.numLayers; li++) { if (dcomp.layer(li).name == "DATA") dl = dcomp.layer(li); }
    if (!dl) { dl = dcomp.layers.addNull(); dl.name = "DATA"; }
    var fx = dl.property("ADBE Effect Parade");
    var G = hrfGeom(S);
    // długość łamanej mapy -> postęp linii na mapie
    var mapLen = [0], ddx, ddy, mapTrim = [];
    for (i = 1; i < N; i++) {
        ddx = (G.mapPts[i][0] - G.mapPts[i - 1][0]) * HRF_MS;
        ddy = (G.mapPts[i][1] - G.mapPts[i - 1][1]) * HRF_MS;
        mapLen.push(mapLen[i - 1] + Math.sqrt(ddx * ddx + ddy * ddy));
    }
    for (i = 0; i < N; i++) mapTrim.push(mapLen[N - 1] > 0 ? mapLen[i] / mapLen[N - 1] * 100 : 0);

    var series = [], names = [];
    for (key in S) {
        if (key == "time_s" || key == "heading_deg") continue;
        if (!S[key] || S[key].length != N) continue;
        names.push(key);
        series.push(S[key]);
    }
    names.push("map_trim"); series.push(mapTrim);
    var idxs = [];
    for (i = 0; i < N; i++) idxs.push(i);
    names.push("idx"); series.push(idxs);
    var si, inc, ef, old;
    for (si = 0; si < names.length; si++) {
        if (progress) progress("Zapis danych: " + names[si] + " (" + (si + 1) + "/" + names.length + ")");
        old = fx.property(names[si]);
        if (old) old.remove();
        ef = fx.addProperty("ADBE Slider Control");
        ef.name = names[si];
        inc = hrfIncreasing(S.time_s, series[si], K);
        ef.property(1).setValuesAtTimes(inc.t, inc.v);
    }
    // kropki: pozycje znormalizowane
    var dots = [["MAPDOT", G.mapPts], ["PROFDOT", G.profPts]], dn, dd2;
    for (dn = 0; dn < dots.length; dn++) {
        for (li = dcomp.numLayers; li >= 1; li--) { if (dcomp.layer(li).name == dots[dn][0]) dcomp.layer(li).remove(); }
        dd2 = dcomp.layers.addNull();
        dd2.name = dots[dn][0];
        inc = hrfIncreasing(S.time_s, dots[dn][1], K);
        dd2.property("ADBE Transform Group").property("ADBE Position").setValuesAtTimes(inc.t, inc.v);
    }
    dcomp.comment = "id=" + String(D.activity_id || "") + "|start=" + String(D.start_date_local || "") + "|n=" + N + "|k=" + K + "|sig=" + hrfGeomSig(G) + "|file=" + String(filePath || "");
    return dcomp;
}

// Podpis geometrii trasy i profilu. Wspólne prekompy (mapa, profil, mini mapa 3D) mają trasę wpisaną na stałe,
// więc zapisujemy w ich komentarzu, dla jakiej trasy powstały.
function hrfGeomSig(G) {
    // ostatni człon = wersja rysowania prekompów; po zmianie kodu mapy/profilu podnieś go, a stare prekomp zbudują się od nowa
    return [G.latMid.toFixed(5), G.lngMid.toFixed(5), G.mapSc.toFixed(3), Math.round(G.eMin), Math.round(G.eMax), G.distTotal.toFixed(2), "r5"].join("/");
}

// Zwraca wspólny prekomp tylko wtedy, gdy powstał dla tej samej trasy. Nieaktualny dostaje " (old)",
// dzięki czemu klip zbuduje go od nowa; potem hrfAdopt podmienia go w klipach zrobionych wcześniej.
function hrfFreshComp(name, sig) {
    var c = hrfFindComp(name);
    if (!c) return null;
    if (String(c.comment) == "sig=" + sig) return c;
    c.name = name + " (old)";
    hrfStale[name] = c;
    return null;
}

// Nowy prekomp zastępuje stary we wszystkich istniejących klipach (ułożenie i Time Remap warstwy zostają).
function hrfAdopt(name, newComp) {
    var oldComp = hrfStale[name];
    if (!oldComp) return;
    delete hrfStale[name];
    var i, j, it, lay;
    for (i = 1; i <= app.project.numItems; i++) {
        it = app.project.item(i);
        if (!(it instanceof CompItem) || it.id == newComp.id || it.id == oldComp.id) continue;
        for (j = 1; j <= it.numLayers; j++) {
            lay = it.layer(j);
            try { if (lay.source && lay.source.id == oldComp.id) lay.replaceSource(newComp, false); } catch (er) {}
        }
    }
}

// Czy wczytywana trasa różni się od tej, dla której zapisano HRF_DATA?
function hrfRouteChanged(D) {
    var inf = hrfDataInfo();
    if (!inf) return false;
    if (inf.sig) return inf.sig != hrfGeomSig(hrfGeom(D.samples));
    return String(inf.id) != String(D.activity_id || "") || String(inf.n) != String(D.samples.time_s.length);
}

function hrfHasClips() {
    for (var i = 1; i <= app.project.numItems; i++) {
        var it = app.project.item(i);
        if (it instanceof CompItem && it.name.indexOf("HRF_OVERLAY") == 0) return true;
    }
    return false;
}

function hrfDataInfo() {
    var dc = hrfFindComp("HRF_DATA");
    if (!dc || !dc.comment) return null;
    var parts = String(dc.comment).split("|"), res = {}, k, kv;
    for (k = 0; k < parts.length; k++) {
        kv = parts[k].indexOf("=");
        if (kv > 0) res[parts[k].substring(0, kv)] = parts[k].substring(kv + 1);
    }
    return res;
}

// ---------- budowa jednego klipu ----------
// o: {W,H,fps,dur,off,vid,addVid,showMap,showProf,show3d,OSM,key,fields}
function hrfBuildClip(D, o) {
    var S = D.samples;
    var W = o.W, H = o.H, fps = o.fps, dur = o.dur, off = o.off, vid = o.vid;
    var cbVid = { value: o.addVid };
    var showMap = o.showMap, showProf = o.showProf, show3d = o.showMap && o.show3d;
    var OSM = o.OSM;
    var comp = null;
    app.beginUndoGroup("HRF klip");
    try {
        var N = S.time_s.length;

        // Wspólne kompozycje (mapa, mini mapa 3D, profil, liczniki) są tworzone raz i używane przez wszystkie klipy.
        // Czas w nich = sekundy od startu aktywności; każdy klip przesuwa go przez Time Remap.
        var keyName = hrfSafe(o.key || (vid ? vid.name : "klip"));
        var suffix = " - " + keyName, sn = 1;
        while (hrfFindComp("HRF_OVERLAY" + suffix)) {
            sn++;
            suffix = " - " + keyName + " " + sn;
        }
        var inf0 = hrfDataInfo();
        var KT = (inf0 && inf0.k) ? parseFloat(inf0.k) : 1;
        if (isNaN(KT) || KT <= 0) KT = 1;
        var durAll = (S.time_s[N - 1] + 60) * KT;
        var dataName = "HRF_DATA", mainName = "HRF_OVERLAY" + suffix;
        var mapName = "HRF_MAP";
        var profName = "HRF_PROFILE", trimName = "TRIM";   // dopełniane po obliczeniu proporcji (PH)
        var map3dName = o.OSM ? "HRF_MAP3D" + suffix : "HRF_MAP3D";
        var flatName = o.OSM ? "HRF_MAP3D_FLAT" + suffix : "HRF_MAP3D_FLAT";
        var cntName = "HRF_COUNTERS " + W + "x" + H + "-" + (showMap ? 1 : 0) + (showProf ? 1 : 0);
        var asMs0 = hrfActivityStartMs(D.start_date_local);
        var clock0 = asMs0 === null ? 0 : Math.round((asMs0 % 86400000) / 1000);

        var base =
            'var d=comp("' + dataName + '").layer("DATA");' +
            'var c0=' + clock0 + ';' +
            'var K=' + KT + ';' +
            'var tt=time;' +
            'function val(n){return d.effect(n)(1).valueAtTime(tt);}' +
            'function p(n){return (n<10?"0":"")+n;}' +
            'function hms(t){t=Math.max(0,Math.round(t));return p(Math.floor(t/3600))+":"+p(Math.floor(t%3600/60))+":"+p(t%60);}' +
            'function f1(x){return (Math.round(x*10)/10).toFixed(1).replace(".",",");}';

        // ===== wspólna kompozycja z danymi (zapisana wcześniej przez hrfWriteData) =====
        var dcomp = hrfFindComp("HRF_DATA");
        if (!dcomp) throw new Error("Brak kompozycji HRF_DATA – najpierw wczytaj dane.");

        // ===== układ liczony z rozmiaru wideo =====
        var fsel = o.fields;   // mapa klucz -> true (null = wszystkie pola)
        var showSpeed = !fsel || !!fsel.speed;
        var items = [], fi, fmask = 0;
        for (fi = 0; fi < HRF_FIELDS.length; fi++) {
            if (fsel && !fsel[HRF_FIELDS[fi][0]]) continue;
            fmask += Math.pow(2, fi);
            if (HRF_FIELDS[fi][2]) items.push([HRF_FIELDS[fi][1], HRF_FIELDS[fi][2]]);
        }
        cntName += fsel ? "-" + fmask.toString(16) : "-all";
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
        var need = (showSpeed ? headerH + g : 0) + rows * cellH;
        var k = Math.min(1, avail / need);
        vsz *= k; lsz *= k; bigS *= k; cellH *= k; headerH *= k;
        var headTotal = showSpeed ? headerH + g : 0;
        var blockH = headTotal + rows * cellH;
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
        profName = "HRF_PROFILE " + PW + "x" + PH;
        trimName = "TRIM " + PH;
        var PROF_Y0 = 0.10, PROF_K = 0.70; // pionowy zakres linii profilu w prekompie

        var geoSig = hrfGeomSig(hrfGeom(S));
        var profIsNew = showProf && !hrfFreshComp(profName, geoSig);
        if (profIsNew) {
            // postęp przycinania linii liczony po DŁUGOŚCI łamanej (a nie po dystansie),
            // dzięki temu jasna linia dochodzi dokładnie do kropki
            var profLen = [0], profTrim = [], ddx, ddy;
            for (i = 1; i < N; i++) {
                ddx = (profPts[i][0] - profPts[i - 1][0]) * PW;
                ddy = (profPts[i][1] - profPts[i - 1][1]) * PH * PROF_K;
                profLen.push(profLen[i - 1] + Math.sqrt(ddx * ddx + ddy * ddy));
            }
            for (i = 0; i < N; i++) profTrim.push(profLen[N - 1] > 0 ? profLen[i] / profLen[N - 1] * 100 : 0);
            // postęp profilu zależy od proporcji prekompu, więc jest liczony osobno dla każdego klipu
            for (var tli = dcomp.numLayers; tli >= 1; tli--) { if (dcomp.layer(tli).name == trimName) dcomp.layer(tli).remove(); }
            var tl = dcomp.layers.addNull();
            tl.name = trimName;
            var te = tl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
            te.name = "prof_trim";
            var tk2 = hrfIncreasing(S.time_s, profTrim, KT);
            te.property(1).setValuesAtTimes(tk2.t, tk2.v);
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

        // ===== prekomp: duża mapa =====
        var mapComp = null, profComp = null, step = Math.max(1, Math.ceil(N / 900));
        mapComp = showMap ? hrfFreshComp(mapName, geoSig) : null;
        if (showMap && !mapComp) {
            mapComp = app.project.items.addComp(mapName, MS, MS, 1, durAll, 30);
            mapComp.comment = "sig=" + geoSig;
            hrfAdopt(mapName, mapComp);
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
        if (showProf && profIsNew) {
            profComp = app.project.items.addComp(profName, PW, PH, 1, durAll, 30);
            profComp.comment = "sig=" + geoSig;
            hrfAdopt(profName, profComp);
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
            ptrim.property("ADBE Vector Trim End").expression = base + 'comp("HRF_DATA").layer("' + trimName + '").effect("prof_trim")(1).valueAtTime(tt);';
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

        if (showProf && !profComp) profComp = hrfFindComp(profName);

        // ===== okrągła mini mapa nawigacyjna w prawdziwym 3D =====
        // Płaska mapa (trasa i siatka rysowane od razu w docelowej skali) jest warstwą 3D bez obrotów;
        // kamera stoi przed nią i poniżej środka, patrząc w górę na jadącego — dzięki temu "dalsza"
        // część mapy jest naturalnie węższa (perspektywa). Kierunek jazdy w górę zapewnia obrót mapy.
        var map3d = null;
        var VIEW_M = 100;                  // promień widocznego wycinka w metrach
        var GRID_M = 50;                   // odstęp siatki w metrach
        var RING_R = MS * 0.46;            // promień okrągłego wycinka na ekranie (px)
        var TILT_DEG = 50;                 // pochylenie kamery od normalnej mapy (0 = z góry, 60 = prawie z boku)
        var ELEV_EXAG = 0;                 // domyślne przewyższenie trasy na mapie 3D (0 = płasko, 1 = skala rzeczywista); potem do zmiany suwakiem na CAMCTL
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
            var flat = app.project.items.addComp(flatName, FL, FL, 1, durAll, 30);
            // warstwa sterująca: położenie jadącego (lekko uśrednione), kierunek (uśredniony dłużej) i postęp linii
            var ctl = flat.layers.addNull();
            ctl.name = "CAMCTL";
            var addSl = function (nm, expr) {
                var ef = ctl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
                ef.name = nm;
                ef.property(1).expression = expr;
            };
            var dm = 'comp("' + dataName + '").layer("MAPDOT").transform.position';
            addSl("Rider X (map px)", base + 'var a=0;for(var i=-1;i<=1;i++){a+=' + dm + '.valueAtTime(tt+i*K)[0];}(a/3-0.5)*' + KW + ';');
            addSl("Rider Y (map px)", base + 'var a=0;for(var i=-1;i<=1;i++){a+=' + dm + '.valueAtTime(tt+i*K)[1];}(a/3-0.5)*' + KW + ';');
            addSl("Map rotation (deg)", base + 'var a=0;for(var i=-3;i<=3;i++){a+=d.effect("heading_unw")(1).valueAtTime(tt+i*K);}-a/7;');
            addSl("Route progress (%)", base + 'var a=0;for(var i=-1;i<=1;i++){a+=d.effect("map_trim")(1).valueAtTime(tt+i*K);}Math.max(0,Math.min(100,a/3));');
            addSl("Sample index (data row)", base + 'val("idx");');
            var exSl = ctl.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
            exSl.name = "Elevation exaggeration (0 = off)";
            exSl.property(1).setValue(ELEV_EXAG);
            try {
                ctl.comment = "STEROWANIE MAPA 3D (wartosci poza pierwszym suwakiem sa liczone z danych - nie zmieniaj)\n" +
                    "Elevation exaggeration (0 = off): JEDYNY suwak do recznej zmiany. 0 = trasa plaska, 1 = wysokosc w skali rzeczywistej, 3 = wyraznie.\n" +
                    "Rider X / Rider Y (map px): pozycja jadacego na mapie w pikselach swiata (z HRF_DATA).\n" +
                    "Map rotation (deg): obrot mapy tak, by kierunek jazdy byl w gore (z kierunku jazdy).\n" +
                    "Route progress (%): odcinek trasy za jadacym liczony po dlugosci (z danych).\n" +
                    "Sample index (data row): numer probki z pliku danych w tej chwili.";
            } catch (ec) {}
            var cx = 'var C2=thisComp.layer("CAMCTL");var RX=C2.effect("Rider X (map px)")(1),RY=C2.effect("Rider Y (map px)")(1),RO=C2.effect("Map rotation (deg)")(1);';
            // każda warstwa świata (bez rodzica): Rotation = obrót mapy (+ kąt własny), Position tak, by jadący był w środku
            var place = function (lay, ox, oy, ang, sc) {
                var tg = lay.property("ADBE Transform Group");
                tg.property("ADBE Anchor Point").setValue([0, 0]);
                if (sc) tg.property("ADBE Scale").setValue([sc * 100, sc * 100]);
                tg.property("ADBE Rotate Z").expression = cx + 'RO+(' + (ang || 0) + ');';
                tg.property("ADBE Position").expression = cx +
                    'var r=RO*Math.PI/180,cs=Math.cos(r),sn=Math.sin(r);var rx=RX-(' + ox + '),ry=RY-(' + oy + ');' +
                    '[' + (FL / 2) + '-(rx*cs-ry*sn),' + (FL / 2) + '-(rx*sn+ry*cs)];';
            };

            var SC = KW / MS;                  // powiększenie małego rysunku do px świata (kształty rysujemy w skali ramki MS x MS)
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
                // kształty OSM w małej skali (jak trasa); napisy mają pozycję w px świata (cvt)
                var cvtS = function (ll) {
                    var c = cvt(ll);
                    return [c[0] / SC, c[1] / SC];
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
                        for (oj = 0; oj < list[oi].length; oj++) pts.push(cvtS(list[oi][oj]));
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
                        os2.property("ADBE Vector Stroke Width").setValue(wpx / SC);
                        try {
                            os2.property("ADBE Vector Stroke Line Cap").setValue(2);
                            os2.property("ADBE Vector Stroke Line Join").setValue(2);
                        } catch (ec) {}
                    }
                    sl.property("ADBE Transform Group").property("ADBE Opacity").setValue(op);
                    place(sl, 0, 0, 0, SC);
                };
                osmLayer("OSM green", OSM.green, true, true, 0, 12);
                osmLayer("OSM water", OSM.water, true, true, 0, 28);
                osmLayer("OSM rivers", OSM.river, false, false, 5 * PXM, 35);
                osmLayer("OSM buildings", OSM.building, true, true, 0, 38);
                osmLayer("OSM paths", OSM.path, false, false, 2.5 * PXM, 28);
                osmLayer("OSM roads", OSM.road_minor, false, false, 6 * PXM, 32);
                osmLayer("OSM main roads", OSM.road_major, false, false, 9 * PXM, 42);
                // nazwy ulic (każda jako osobny napis ustawiony wzdłuż odcinka)
                var nm = OSM.names || [], ni, nl, rc, p1, p2, an, MAXN = 120, nmade = 0;
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

            // trasa, siatka i ślad są rysowane w małej skali (jak mapa 2D: ramka MS x MS), a warstwy dostają Scale = SC.
            // Ścieżki w pikselach świata (do +/-40 tys. px) rozjeżdżały się w AE (skoki trasy w obie strony).
            var rp = [], rz = [], gstep = Math.max(1, Math.ceil(N / ROUTE_PTS));
            var bx0 = 1e9, by0 = 1e9, bx1 = -1e9, by1 = -1e9, wx, wy;
            for (i = 0; i < N; i += gstep) {
                wx = (mapPts[i][0] - 0.5) * MS; wy = (mapPts[i][1] - 0.5) * MS;
                rp.push([wx, wy]);
                rz.push(hrfAvgAround(S.elev_m, i, 5));
                if (wx < bx0) bx0 = wx; if (wx > bx1) bx1 = wx;
                if (wy < by0) by0 = wy; if (wy > by1) by1 = wy;
            }
            rp.push([(mapPts[N - 1][0] - 0.5) * MS, (mapPts[N - 1][1] - 0.5) * MS]);
            rz.push(hrfAvgAround(S.elev_m, N - 1, 5));

            var gspx = WKM * GRID_M / 1000 / SC;  // odstęp siatki (jednostki małego rysunku)
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
                    gst.property("ADBE Vector Stroke Width").setValue(MS * 0.003 / SC);
                    var grp = gvv.addProperty("ADBE Vector Filter - Repeater");
                    grp.property("ADBE Vector Repeater Copies").setValue(Math.ceil((gv == 0 ? gW : gH) / gspx) + 1);
                    grp.property("ADBE Vector Repeater Transform").property("ADBE Vector Repeater Position").setValue(gv == 0 ? [gspx, 0] : [0, gspx]);
                    gl2.property("ADBE Transform Group").property("ADBE Opacity").setValue(18);
                    place(gl2, gx0 * SC, gy0 * SC, 0, SC);
                }
            } catch (eg) {}
            var lw = MS * 0.03; // szerokość linii trasy w px świata (ok. 6 m)
            // Trasa i ślad liczone wyrażeniem: punkty są od razu obrócone (kierunek jazdy w górę) i uniesione o wysokość
            // względem jadącego, więc warstwy stoją w środku bez obrotu. Wysokość z = przesunięcie w górę ekranu
            // o z * tan(pochylenie kamery) (tak wygląda wysokość pod pochyloną kamerą) razy ELEV_EXAG.
            var r1 = hrfPoly(flat, "Route", [[0, 0], [1, 1]], 0, 0, lw / SC, 55, false);
            var r2 = hrfPoly(flat, "Route done", [[0, 0], [1, 1]], 0, 0, lw * 1.4 / SC, 100, false);
            var pj = [], pi2;
            for (pi2 = 0; pi2 < rp.length; pi2++) {
                pj.push("[" + Math.round(rp[pi2][0] * 1000) / 1000 + "," + Math.round(rp[pi2][1] * 1000) / 1000 + "," + Math.round(rz[pi2] * 10) / 10 + "]");
            }
            var WINPTS = Math.ceil(400 / gstep);   // ile ostatnich próbek (ok. 400 s jazdy) rysować za jadącym
            var LIFT = (WKM / 1000) * Math.tan(TILT_USED * Math.PI / 180) / SC;   // jednostki małego rysunku na metr wysokości (bez przewyższenia)
            var geoExpr =
                'var C2=thisComp.layer("CAMCTL");var P=[' + pj.join(",") + '];' +
                'var kf=Math.max(1,Math.min(P.length-1,C2.effect("Sample index (data row)")(1)/' + gstep + '));var k=Math.floor(kf),fr=kf-k;' +
                'var k1=Math.min(P.length-1,k+1);var RZ=P[k][2]*(1-fr)+P[k1][2]*fr;' +
                'var RX=C2.effect("Rider X (map px)")(1)/' + SC + ',RY=C2.effect("Rider Y (map px)")(1)/' + SC + ',RO=C2.effect("Map rotation (deg)")(1)*Math.PI/180;' +
                'var cs=Math.cos(RO),sn=Math.sin(RO),EX=' + LIFT + '*C2.effect("Elevation exaggeration (0 = off)")(1);' +
                'function q(p){var x=p[0]-RX,y=p[1]-RY;return [x*cs-y*sn,x*sn+y*cs-(p[2]-RZ)*EX];}' +
                'var Q=[],i;';
            r1.vecs.property("ADBE Vector Shape - Group").property("ADBE Vector Shape").expression =
                geoExpr + 'for(i=0;i<P.length;i++)Q.push(q(P[i]));createPath(Q,[],[],false);';
            r2.vecs.property("ADBE Vector Shape - Group").property("ADBE Vector Shape").expression =
                geoExpr + 'for(i=Math.max(0,k-' + WINPTS + ');i<=k;i++)Q.push(q(P[i]));Q.push([0,0]);createPath(Q,[],[],false);';
            var centerLayer = function (lay) {
                var tg = lay.property("ADBE Transform Group");
                tg.property("ADBE Anchor Point").setValue([0, 0]);
                tg.property("ADBE Position").setValue([FL / 2, FL / 2]);
                tg.property("ADBE Scale").setValue([SC * 100, SC * 100]);
            };
            centerLayer(r1.layer);
            centerLayer(r2.layer);
            // strzałka jadącego pośrodku (leży na mapie, nie obraca się razem z nią)
            var ar = MS * 0.04;
            var arw = hrfFilled(flat, "Rider arrow", [[0, -1.3 * ar], [0.9 * ar, ar], [0, 0.45 * ar], [-0.9 * ar, ar]], 0);
            arw.property("ADBE Transform Group").property("ADBE Position").setValue([FL / 2, FL / 2]);

            // --- wersja zewnętrzna: tło, mapa jako warstwa 3D, kamera, pierścień ---
            var c3 = app.project.items.addComp(map3dName, MS, MS, 1, durAll, 30);
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
            map3d = hrfFreshComp(map3dName, geoSig);
            if (!map3d) {
                var oldFlat = hrfFindComp(flatName);
                if (oldFlat) oldFlat.name = flatName + " (old)";
                map3d = build3D();
                map3d.comment = "sig=" + geoSig;
                hrfAdopt(map3dName, map3d);
            }
        }
        // przesunięcie czasu klipu: czas w prekompach = sekundy od startu aktywności
        var remap = function (lay) {
            try {
                lay.timeRemapEnabled = true;
                lay.property("ADBE Time Remapping").expression =
                    'var C=thisComp.layer("CONTROLS");(time+C.effect("Start offset (s)")(1)+C.effect("Shift (s)")(1))*' + KT + ';';
            } catch (er) {}
        };

        // prekompy w kadrze głównym (pomniejszone; w razie potrzeby powiększ Scale/Position)
        if (mapComp) {
            var ml = comp.layers.add(mapComp);
            ml.name = "MAP";
            ml.property("ADBE Transform Group").property("ADBE Position").setValue([mx + hTop / 2, topY + hTop / 2]);
            ml.property("ADBE Transform Group").property("ADBE Scale").setValue([hTop / MS * 100, hTop / MS * 100]);
            if (map3d) ml.enabled = false; // mapa 2D ukryta, gdy jest mini mapa 3D (włącz okiem)
            remap(ml);
        }
        var add3dLayer = function (c3, nm) {
            var l3 = comp.layers.add(c3);
            l3.name = nm;
            l3.property("ADBE Transform Group").property("ADBE Position").setValue([mx + hTop / 2, topY + hTop / 2]);
            l3.property("ADBE Transform Group").property("ADBE Scale").setValue([hTop / MS * 100, hTop / MS * 100]);
            l3.enabled = true;
            remap(l3);
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
        if (map3d) add3dLayer(map3d, "MAP 3D (from behind)");
        if (profComp) {
            var pl2 = comp.layers.add(profComp);
            pl2.name = "PROFILE";
            pl2.property("ADBE Transform Group").property("ADBE Position").setValue([profX + profW / 2, topY + hTop / 2]);
            pl2.property("ADBE Transform Group").property("ADBE Scale").setValue([profW / PW * 100, profW / PW * 100]);
            remap(pl2);
        }

        // ===== liczniki (wspólna kompozycja dla danego rozmiaru kadru) =====
        var cnt = hrfFindComp(cntName);
        if (!cnt) {
            cnt = app.project.items.addComp(cntName, W, H, 1, durAll, 30);
            if (showSpeed) {
                hrfText(cnt, "Speed label", mx, yBlock + lsz, lsz, null, "PREDKOSC  km/h");
                hrfText(cnt, "Speed value", mx, yBlock + lsz * 1.5 + bigS * 0.8, bigS,
                    base +
                    'var v=val("speed_kmh");' +
                    'if(v<0.5){var dd=d.effect("dist_km")(1);var kk=(dd.valueAtTime(tt+2*K)-dd.valueAtTime(tt-2*K))/4*3600;if(kk>v)v=kk;}' +
                    'Math.round(v).toString();', "0");
            }
            for (var r = 0; r < items.length; r++) {
                var col = r % cols, row = Math.floor(r / cols);
                var cx = mx + col * cw;
                var cy = yBlock + headTotal + row * cellH;
                hrfText(cnt, "Label " + (r + 1), cx, cy + lsz, lsz, null, items[r][0]);
                hrfText(cnt, "Value " + (r + 1), cx, cy + lsz * 1.5 + vsz, vsz, base + items[r][1], "0");
            }
        }
        var cl = comp.layers.add(cnt);
        cl.name = "COUNTERS";
        remap(cl);

        ctrl.moveToEnd();
        comp.openInViewer();
    } catch (err) {
        alert("Błąd skryptu:\n" + err.toString() + "\nlinia: " + err.line);
    }
    app.endUndoGroup();
    return comp;
}

// ---------- panel ----------
function hrfPanel() {
    var D = null, filePath = "", lastPickId = -1;
    var w = new Window("palette", "HRF - dane Strava " + HRF_VERSION, undefined, { resizeable: false });
    w.orientation = "column";
    w.alignChildren = "fill";
    w.margins = 12;
    var stVer = w.add("statictext", undefined, "Wersja " + HRF_VERSION + "  |  build " + hrfBuildStamp());
    try {
        stVer.helpTip = String($.fileName);
        stVer.graphics.font = ScriptUI.newFont("Arial", "BOLD", 11);
    } catch (ev) {}
    var stData = w.add("statictext", undefined, "Dane: nie wczytano", { multiline: true });
    stData.preferredSize.width = 330;
    stData.preferredSize.height = 42;
    var gb1 = w.add("group");
    var bLoad = gb1.add("button", undefined, "Wczytaj dane...");
    var bRef = gb1.add("button", undefined, "Odswiez dane");
    var pn = w.add("panel", undefined, "Nowy klip");
    pn.orientation = "column";
    pn.alignChildren = "fill";
    pn.margins = 12;
    var gA = pn.add("group");
    gA.add("statictext", undefined, "Rozmiar:");
    var eSize = gA.add("edittext", undefined, "1080x1920"); eSize.characters = 10;
    gA.add("statictext", undefined, "FPS:");
    var eFps = gA.add("edittext", undefined, "30"); eFps.characters = 5;
    gA.add("statictext", undefined, "Dlugosc (s):");
    var eDur = gA.add("edittext", undefined, "60"); eDur.characters = 6;
    var gB = pn.add("group");
    gB.add("statictext", undefined, "Poczatek (s lub GG:MM:SS):");
    var eOff = gB.add("edittext", undefined, "0"); eOff.characters = 10;
    var bPick = pn.add("button", undefined, "Pobierz z zaznaczonego wideo");
    var stInfo = pn.add("statictext", undefined, "", { multiline: true });
    stInfo.preferredSize.width = 320;
    stInfo.preferredSize.height = 30;
    var cV = pn.add("checkbox", undefined, "Dodaj wideo do kompozycji klipu"); cV.value = true;
    var cM = pn.add("checkbox", undefined, "Mapa trasy z kierunkiem jazdy"); cM.value = true;
    var cP = pn.add("checkbox", undefined, "Profil wysokosci"); cP.value = true;
    var c3 = pn.add("checkbox", undefined, "Mini mapa 3D zza plecow"); c3.value = true;
    var cO = pn.add("checkbox", undefined, "Otoczenie z OSM (plik hrf_osm.json)"); cO.value = false;
    var gF = pn.add("group");
    var bFields = gF.add("button", undefined, "Pola licznika...");
    var stFields = gF.add("statictext", undefined, "");
    stFields.preferredSize.width = 150;
    var bAdd = pn.add("button", undefined, "Dodaj klip z zaznaczonego wideo");
    var stStat = w.add("statictext", undefined, "");
    var bClose = w.add("button", undefined, "Zamknij");

    // wybór pól licznika (zapamiętywany między uruchomieniami)
    var sel = {}, fk;
    for (fk = 0; fk < HRF_FIELDS.length; fk++) sel[HRF_FIELDS[fk][0]] = true;
    try {
        if (app.settings.haveSetting("HRF", "fields")) {
            var saved = String(app.settings.getSetting("HRF", "fields")).split(","), sj;
            for (fk = 0; fk < HRF_FIELDS.length; fk++) sel[HRF_FIELDS[fk][0]] = false;
            for (sj = 0; sj < saved.length; sj++) if (saved[sj] in sel) sel[saved[sj]] = true;
        }
    } catch (es) {}
    var countSel = function () {
        var c = 0, q;
        for (q = 0; q < HRF_FIELDS.length; q++) if (sel[HRF_FIELDS[q][0]]) c++;
        return c;
    };
    var showFields = function () { stFields.text = "Pola: " + countSel() + " z " + HRF_FIELDS.length; };
    var fieldsOpt = function () { return countSel() == HRF_FIELDS.length ? null : sel; };   // null = wszystkie
    var pickFields = function () {
        var dlg = new Window("dialog", "Pola licznika");
        dlg.orientation = "column";
        dlg.alignChildren = "fill";
        dlg.margins = 12;
        var gc = dlg.add("group");
        gc.orientation = "row";
        gc.alignChildren = "top";
        var colG = [], cn, cbs = [], q;
        for (cn = 0; cn < 3; cn++) {
            colG.push(gc.add("group"));
            colG[cn].orientation = "column";
            colG[cn].alignChildren = "left";
        }
        var perCol = Math.ceil(HRF_FIELDS.length / 3);
        for (q = 0; q < HRF_FIELDS.length; q++) {
            var cb = colG[Math.floor(q / perCol)].add("checkbox", undefined, HRF_FIELDS[q][1]);
            cb.value = !!sel[HRF_FIELDS[q][0]];
            cbs.push(cb);
        }
        var gq = dlg.add("group");
        var bAll = gq.add("button", undefined, "Wszystkie");
        var bNone = gq.add("button", undefined, "Zadne");
        gq.add("button", undefined, "OK", { name: "ok" });
        gq.add("button", undefined, "Anuluj", { name: "cancel" });
        bAll.onClick = function () { for (var z = 0; z < cbs.length; z++) cbs[z].value = true; };
        bNone.onClick = function () { for (var z = 0; z < cbs.length; z++) cbs[z].value = false; };
        if (dlg.show() == 1) {
            var keep = [];
            for (q = 0; q < HRF_FIELDS.length; q++) {
                sel[HRF_FIELDS[q][0]] = cbs[q].value;
                if (cbs[q].value) keep.push(HRF_FIELDS[q][0]);
            }
            try { app.settings.saveSetting("HRF", "fields", keep.join(",")); } catch (e2) {}
            showFields();
        }
    };
    bFields.onClick = pickFields;
    showFields();

    var selectedVideo = function () {
        for (var si = 0; si < app.project.selection.length; si++) {
            var it = app.project.selection[si];
            if (it instanceof FootageItem && it.hasVideo) return it;
        }
        return null;
    };
    var setStatus = function (t) { stStat.text = t; w.update(); };
    var showData = function () {
        var inf = hrfDataInfo();
        if (D) {
            stData.text = "Dane: " + String(D.name || "?") + "\nStart: " + String(D.start_date_local || "?") + "  |  " +
                D.samples.time_s.length + " probek" + (inf ? "  |  HRF_DATA w projekcie" : "");
        } else if (inf) {
            stData.text = "W projekcie jest HRF_DATA (aktywnosc " + inf.id + ", start " + inf.start + ").\nWczytaj plik, aby dodawac klipy.";
        } else {
            stData.text = "Dane: nie wczytano";
        }
    };
    var load = function (f, refresh) {
        try {
            var nd = hrfReadJson(f);
            var inf = hrfDataInfo();
            if (hrfRouteChanged(nd) && hrfHasClips()) {
                if (!confirm("To inna trasa niz w projekcie" + (inf && inf.id ? " (aktywnosc " + inf.id + ")" : "") + ". Mapa i profil w istniejacych klipach sa ze starej trasy.\nZostana podmienione na nowe przy nastepnym dodaniu klipu.\nKontynuowac?")) return;
            }
            D = nd;
            filePath = f.fsName;
            app.beginUndoGroup("HRF dane");
            try {
                hrfWriteData(D, filePath, setStatus);
            } finally {
                app.endUndoGroup();
            }
            setStatus("Dane zapisane w HRF_DATA – klipy odswiezone.");
            showData();
        } catch (e) {
            alert("Blad danych:\n" + e.toString() + (e.line ? "\nlinia: " + e.line : ""));
        }
    };
    var pick = function () {
        var vid = selectedVideo();
        if (!vid) { stInfo.text = "Zaznacz wideo w panelu Projekt."; return null; }
        lastPickId = vid.id;
        eSize.text = vid.width + "x" + vid.height;
        eFps.text = String(vid.frameRate);
        eDur.text = String(Math.round(vid.duration * 100) / 100);
        if (D) {
            var vs0 = hrfVideoStart(vid), as0 = hrfActivityStartMs(D.start_date_local);
            if (vs0.wall !== null && as0 !== null) {
                eOff.text = String(Math.round((vs0.wall - as0) / 100) / 10);
                stInfo.text = "Poczatek wykryty: " + vs0.how;
            } else {
                stInfo.text = "Nie wykryto poczatku (" + vs0.how + ") – wpisz recznie.";
            }
        } else {
            stInfo.text = "Wczytaj dane, aby wykryc poczatek wideo.";
        }
        return vid;
    };
    bLoad.onClick = function () {
        var f = File.openDialog("Wskaz plik JSON z danymi (<nazwa>_hrf.json)", "JSON:*.json,Wszystkie:*.*");
        if (f) load(f, false);
    };
    bRef.onClick = function () {
        var f = filePath ? new File(filePath) : null;
        if (!f || !f.exists) {
            var inf = hrfDataInfo();
            f = (inf && inf.file) ? new File(inf.file) : null;
        }
        if (!f || !f.exists) {
            f = File.openDialog("Wskaz plik JSON z danymi", "JSON:*.json,Wszystkie:*.*");
            if (!f) return;
        }
        load(f, true);
    };
    bPick.onClick = function () { pick(); };
    bAdd.onClick = function () {
        if (!D) { alert("Najpierw wczytaj dane."); return; }
        var vid = selectedVideo();
        if (vid && vid.id != lastPickId) pick();   // nowe wideo: uzupełnij pola (ręczne zmiany dla tego samego wideo zostają)
        var sm = /^\s*(\d+)\s*[xX]\s*(\d+)\s*$/.exec(eSize.text);
        if (!sm) { alert("Rozmiar podaj jako 1080x1920."); return; }
        var fps = parseFloat(eFps.text.replace(",", ".")), dur = parseFloat(eDur.text.replace(",", "."));
        var off = hrfParseOffset(eOff.text, D.start_date_local);
        if (isNaN(fps) || fps <= 0 || isNaN(dur) || dur <= 0 || off === null) { alert("Sprawdz FPS, dlugosc i poczatek."); return; }
        var total = D.samples.time_s[D.samples.time_s.length - 1];
        if (off > total || off + dur < 0) {
            alert("Klip nie miesci sie w czasie aktywnosci (poczatek " + off + " s, aktywnosc trwa " + total + " s).");
            return;
        }
        var OSM = null;
        if (cM.value && c3.value && cO.value) {
            var fo = File.openDialog("Wskaz plik hrf_osm.json (z hrf_osm_fetch.py)", "JSON:*.json,Wszystkie:*.*");
            if (fo) {
                try {
                    fo.encoding = "UTF-8"; fo.open("r"); var ot = fo.read(); fo.close();
                    OSM = eval("(" + ot + ")");
                } catch (eo) { alert("Nie udalo sie wczytac OSM: " + eo); OSM = null; }
            }
        }
        setStatus("Buduje klip...");
        hrfBuildClip(D, { W: parseInt(sm[1], 10), H: parseInt(sm[2], 10), fps: fps, dur: dur, off: off, vid: vid,
            addVid: cV.value, showMap: cM.value, showProf: cP.value, show3d: c3.value, OSM: OSM, key: vid ? vid.name : "klip", fields: fieldsOpt() });
        setStatus("Klip dodany.");
    };
    bClose.onClick = function () { w.close(); };
    showData();
    var inf0 = hrfDataInfo();
    if (inf0 && inf0.file) {
        var f0 = new File(inf0.file);
        if (f0.exists) {
            try { D = hrfReadJson(f0); filePath = f0.fsName; showData(); } catch (e0) {}
        }
    }
    w.show();
}

hrfPanel();
