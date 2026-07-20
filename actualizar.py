"""
actualizar.py  v2
═══════════════════════════════════════════════════════════════════════════════
Ejecución diaria (por GitHub Actions o manualmente):
  1. Lee los tickers de la DB
  2. Descarga precios recientes desde Yahoo Finance
  3. Actualiza dividendos anuales con anualización robusta del año en curso
  4. Recalcula umbrales de yield
  5. Regenera docs/index.html

Correcciones v2:
  - Anualización robusta del año en curso: usa la mediana de los últimos
    N años completos para detectar la frecuencia real de pago por empresa,
    filtrando splits de dividendos y pagos especiales.
  - Tickers sin dividendos (BRK/B, etc.) se omiten silenciosamente sin error.
  - TGT US: disclaimer eliminado del HTML.
  - Nombre de empresa siempre leído desde tickers_maestro.csv (BATS, ULVR, etc.)

Uso:
    python actualizar.py [--db RUTA] [--html RUTA]
"""

import argparse
import json
import sqlite3
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from statistics import median

import yfinance as yf


# ── Mapeo de sufijos de mercado → sufijo Yahoo Finance ────────────────────────
SUFIJOS_YAHOO = {
    "US": "",      # NYSE / NASDAQ
    "CN": ".TO",   # Toronto
    "NA": ".AS",   # Amsterdam (Euronext)
    "LN": ".L",    # Londres (LSE)
    "GY": ".DE",   # Alemania (Xetra)
    "FP": ".PA",   # París (Euronext)
    "PA": ".PA",
    "SM": ".MC",   # Madrid (BME)
    "MC": ".MC",
    "HK": ".HK",   # Hong Kong
    "AU": ".AX",   # Australia (ASX)
    "JP": ".T",    # Tokio
    "SW": ".SW",   # Suiza (SIX)
    "IT": ".MI",   # Milán
    "BB": ".BR",   # Bruselas
    "SE": ".ST",   # Estocolmo
    "DC": ".CO",   # Copenhague
    "NO": ".OL",   # Oslo
    "FH": ".HE",   # Helsinki
}


def ticker_a_yahoo(ticker_modelo: str) -> str:
    partes  = ticker_modelo.strip().upper().split()
    if len(partes) < 2:
        return ticker_modelo
    simbolo = partes[0]
    mercado = partes[-1]
    sufijo  = SUFIJOS_YAHOO.get(mercado, f".{mercado}")
    if mercado == "HK" and simbolo.isdigit():
        simbolo = simbolo.zfill(4)
    return simbolo + sufijo


# ══════════════════════════════════════════════════════════════════════════════
#  Percentil
# ══════════════════════════════════════════════════════════════════════════════
def percentil(valores, p):
    s = sorted(v for v in valores if v is not None)
    if not s: return None
    n   = len(s)
    idx = p * (n - 1)
    lo  = int(idx)
    hi  = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


# ══════════════════════════════════════════════════════════════════════════════
#  Descarga desde Yahoo Finance
# ══════════════════════════════════════════════════════════════════════════════
def descargar_precios(ticker_modelo: str, fecha_desde: str) -> list:
    yahoo = ticker_a_yahoo(ticker_modelo)
    try:
        hist = yf.Ticker(yahoo).history(start=fecha_desde, auto_adjust=True)
        if hist.empty:
            return []
        resultado = []
        for fecha, row in hist.iterrows():
            fecha_iso = fecha.strftime("%Y-%m-%d")
            precio    = round(float(row["Close"]), 4)
            if precio > 0:
                resultado.append((ticker_modelo, fecha_iso, precio))
        return resultado
    except Exception as e:
        print(f"    ⚠  Yahoo precio error para {yahoo}: {e}")
        return []


def detectar_frecuencia_pago(pagos_historicos: list) -> int:
    """
    Detecta la frecuencia anual de pago de dividendos usando años completos previos.

    Estrategia robusta:
      1. Agrupa los pagos por año para obtener el número de pagos por año.
      2. Toma la mediana de los recuentos anuales como frecuencia base.
      3. Redondea al valor estándar más cercano (1, 2, 4, 12).

    Esto filtra automáticamente pagos especiales y variaciones puntuales
    (ej: HPQ, MET, MSFT, PRU, UNH que tienen historial irregular).

    Retorna: 1 (anual), 2 (semestral), 4 (trimestral), 12 (mensual).
    """
    anio_actual = datetime.now().year

    # Solo años completos
    por_anio = {}
    for f, v in pagos_historicos:
        if f.year < anio_actual and v > 0:
            por_anio.setdefault(f.year, []).append(v)

    if not por_anio:
        return 4  # trimestral por defecto

    # Recuentos de pagos por año (filtrar años con solo 1 pago especial si hay más datos)
    conteos = [len(pagos) for pagos in por_anio.values()]

    # Mediana de conteos anuales (más robusta que la media ante outliers)
    med = median(conteos)

    # Redondear al valor estándar más cercano
    if med <= 1.5:   return 1
    if med <= 3.0:   return 2
    if med <= 8.0:   return 4
    return 12


def descargar_dividendos_anuales(ticker_modelo: str) -> list:
    """
    Descarga el historial completo de dividendos de Yahoo,
    los agrupa por año y devuelve lista de (ticker_modelo, anio, dps_anual).

    Para el año en curso (incompleto), extrapola usando la frecuencia
    histórica detectada por mediana de conteos anuales.

    Casos especiales cubiertos:
      - HPQ, MET, MSFT, PRU, UNH: frecuencia irregular en el pasado
        → la mediana evita que un año con más pagos distorsione la frecuencia
      - Empresas con un solo año de historial → default 4 (trimestral)
      - Empresas sin dividendos (BRK/B) → lista vacía sin error
    """
    yahoo = ticker_a_yahoo(ticker_modelo)
    try:
        divs = yf.Ticker(yahoo).dividends
        if divs.empty:
            return []

        anio_actual = datetime.now().year

        # Convertir a lista de (datetime_naive, importe)
        pagos = []
        for fecha, importe in divs.items():
            try:
                f = fecha.to_pydatetime()
                if f.tzinfo:
                    f = f.replace(tzinfo=None)
                if float(importe) > 0:
                    pagos.append((f, float(importe)))
            except Exception:
                continue

        if not pagos:
            return []

        # Detectar frecuencia usando solo años históricos completos
        pagos_hist = [(f, v) for f, v in pagos if f.year < anio_actual]
        frecuencia = detectar_frecuencia_pago(pagos_hist)

        # Agrupar por año
        por_anio = {}
        for f, v in pagos:
            por_anio.setdefault(f.year, []).append((f, v))

        resultado = []
        for anio, lista_pagos in sorted(por_anio.items()):
            total   = sum(v for _, v in lista_pagos)
            n_pagos = len(lista_pagos)

            if anio == anio_actual and n_pagos < frecuencia:
                # Año en curso incompleto → extrapolar al año completo
                total_anualizado = total * (frecuencia / n_pagos)
                resultado.append((ticker_modelo, anio, round(total_anualizado, 4)))
            else:
                resultado.append((ticker_modelo, anio, round(total, 4)))

        return resultado

    except Exception as e:
        print(f"    ⚠  Dividendos Yahoo error para {yahoo}: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  Cálculo de umbrales
# ══════════════════════════════════════════════════════════════════════════════
def calcular_umbrales(conn):
    conn.execute("DELETE FROM umbrales_yield")
    rows = conn.execute(
        "SELECT p.ticker, CAST(d.dps AS REAL)/NULLIF(p.precio,0) "
        "FROM precios p JOIN dividendos d "
        "ON p.ticker=d.ticker "
        "AND CAST(strftime('%Y',p.fecha) AS INTEGER)=d.anio "
        "WHERE d.dps > 0"
    ).fetchall()
    cache = {}
    for ticker, y in rows:
        if y: cache.setdefault(ticker, []).append(y)
    registros = [(t,
        percentil(v, 0.05), percentil(v, 0.95),
        percentil(v, 0.50), sum(v)/len(v))
        for t, v in cache.items()]
    conn.executemany("INSERT OR REPLACE INTO umbrales_yield VALUES (?,?,?,?,?)", registros)
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
#  Generación del objeto GW para el HTML
# ══════════════════════════════════════════════════════════════════════════════
def cagr(dps_dict, anios_back):
    anios = sorted(dps_dict.keys())
    if not anios: return None
    ultimo = anios[-1]
    inicio = ultimo - anios_back
    if inicio not in dps_dict: return None
    v0, v1 = dps_dict[inicio], dps_dict[ultimo]
    if not v0 or not v1 or v0 <= 0 or v1 <= 0: return None
    return (v1 / v0) ** (1 / anios_back) - 1


def generar_gw(conn, csv_maestro="./tickers_maestro.csv"):
    import csv as _csv

    # Leer nombres del CSV maestro (punto 10: nombres siempre desde aquí)
    nombres = {}
    if Path(csv_maestro).exists():
        with open(csv_maestro, encoding="utf-8-sig", newline="") as f:
            for row in _csv.DictReader(f):
                tk = row.get("ticker", "").strip()
                nm = row.get("nombre", "").strip()
                if tk and nm:
                    nombres[tk] = nm

    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM metricas_completas ORDER BY ticker"
    ).fetchall()]

    gw = {}
    for ticker in tickers:
        # Serie semanal (último día de cada semana ISO)
        series_rows = conn.execute("""
            SELECT fecha, precio,
                   ROUND(canal_superior, 2),
                   ROUND(canal_mediana,  2),
                   ROUND(canal_inferior, 2)
            FROM metricas_completas
            WHERE ticker = ?
            ORDER BY fecha
        """, (ticker,)).fetchall()

        seen_weeks = {}
        for row in series_rows:
            dt  = datetime.strptime(row[0], "%Y-%m-%d")
            key = dt.isocalendar()[:2]
            seen_weeks[key] = list(row)
        series = list(seen_weeks.values())

        # Resumen
        r = conn.execute("""
            SELECT precio_actual, yield_pct, rating_pct, upside_pct,
                   canal_superior, canal_inferior, canal_mediana, ultima_fecha
            FROM resumen_tickers WHERE ticker = ?
        """, (ticker,)).fetchone()
        if not r: continue

        precio_actual = r[0]
        yield_actual  = (r[1] or 0) / 100
        rating        = (r[2] or 0) / 100
        upside        = (r[3] or 0) / 100
        p_over        = r[4]
        p_under       = r[5]
        p_mediana     = r[6]

        # Min/max 52 semanas
        mm = conn.execute("""
            SELECT ROUND(MIN(precio),2), ROUND(MAX(precio),2)
            FROM precios
            WHERE ticker = ? AND fecha >= date('now', '-1 year')
        """, (ticker,)).fetchone()

        # Año inicio
        anio_inicio = conn.execute(
            "SELECT CAST(strftime('%Y', MIN(fecha)) AS INTEGER) FROM precios WHERE ticker=?",
            (ticker,)
        ).fetchone()[0]

        # Dividendos históricos y CAGR
        div_rows = conn.execute(
            "SELECT anio, dps FROM dividendos WHERE ticker=? AND dps > 0 ORDER BY anio",
            (ticker,)
        ).fetchall()
        divhist  = [[r[0], r[1]] for r in div_rows]
        dps_dict = {r[0]: r[1] for r in div_rows}

        # Nombre: siempre desde CSV maestro si existe; si no, el ticker
        nombre_display = nombres.get(ticker) or ticker

        gw[ticker] = {
            "summary": {
                "name":          nombre_display,
                "ticker":        ticker,
                "anio_inicio":   anio_inicio,
                "precio_actual": precio_actual,
                "yield_actual":  round(yield_actual, 6),
                "dg3":           round(cagr(dps_dict, 3),  6) if cagr(dps_dict, 3)  else None,
                "dg5":           round(cagr(dps_dict, 5),  6) if cagr(dps_dict, 5)  else None,
                "dg10":          round(cagr(dps_dict, 10), 6) if cagr(dps_dict, 10) else None,
                "rating":        round(rating, 6),
                "upside":        round(upside, 6),
                "p_over":        round(p_over,    2) if p_over    else None,
                "p_under":       round(p_under,   2) if p_under   else None,
                "p_mediana":     round(p_mediana, 2) if p_mediana else None,
                "min52":         mm[0] if mm else None,
                "max52":         mm[1] if mm else None,
            },
            "series":  series,
            "divhist": divhist,
        }
    return gw


# ══════════════════════════════════════════════════════════════════════════════
#  Generación HTML  (punto 3: TGT US sin disclaimer)
# ══════════════════════════════════════════════════════════════════════════════
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Geraldine Weiss</title>
<style>
:root{--navy:#073763;--blue:#5B9BD5;--blue2:#9DC3E6;--orange:#ED7D31;--gray:#808080;--bd:#d8d8d8}
*{box-sizing:border-box}
body{font-family:Calibri,-apple-system,'Segoe UI',sans-serif;color:#1a1a1a;margin:0;padding:18px;background:#fff}
h1{font-size:18px;color:var(--navy);margin:0 0 14px}
.bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
label.fld{font-size:10.5px;color:#555;text-transform:uppercase;letter-spacing:.03em;display:flex;flex-direction:column;gap:3px}
select{padding:5px 9px;border:1px solid var(--bd);border-radius:6px;font-size:12px;background:#fff}
.tot{font-size:12px;color:#555;font-style:italic}
.gwgrid{display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start}
.card{border:1px solid var(--bd);border-radius:8px;padding:10px 12px}
.card h4{margin:0 0 6px;font-size:12.5px;color:var(--navy)}
.gwtitle{font-size:14px;font-weight:700;color:var(--navy);margin:0 0 6px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:700;font-size:11px;color:#fff}
.leg{font-size:10.5px;color:#555;display:flex;gap:12px;flex-wrap:wrap;margin-top:6px}
.sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:middle}
.tip{position:absolute;pointer-events:none;background:var(--navy);color:#fff;font-size:10px;line-height:1.5;padding:5px 8px;border-radius:5px;white-space:nowrap;transform:translate(-50%,-112%);opacity:0;transition:opacity .07s;z-index:9;box-shadow:0 2px 6px rgba(0,0,0,.25)}
.tip b{color:#9DC3E6}.tip .k{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}
.chartbox{position:relative}
table.corp{border-collapse:collapse;font-size:11px;width:100%;margin-top:6px}
table.corp th{background:var(--navy);color:#fff;padding:4px 8px;font-weight:600;text-align:right}
table.corp th:first-child,table.corp td:first-child{text-align:left}
table.corp td{padding:3px 8px;text-align:right;border-bottom:1px solid #eee;font-variant-numeric:tabular-nums}
table.corp tr:nth-child(even) td{background:#F3F7FC}
.ts{font-size:10px;color:#aaa;margin-top:12px;text-align:right}
.empty{padding:30px;text-align:center;color:#888}
@media(max-width:760px){.gwgrid{grid-template-columns:1fr}}
</style></head>
<body>
<h1>Geraldine Weiss &mdash; Valoración por Yield Histórico</h1>
<div class="bar">
  <label class="fld">Empresa<select id="gwSel"></select></label>
  <div class="tot">Canal superior = DPS / Yield mínimo histórico &nbsp;·&nbsp; Canal inferior = DPS / Yield máximo histórico</div>
</div>
<div id="gwEmpty" class="card empty" style="display:none">Esta empresa no tiene serie de precios cargada.</div>
<div class="gwgrid" id="gwBody">
  <div class="card">
    <div class="gwtitle" id="gwTitle"></div>
    <div class="chartbox" id="gwchart"></div>
    <div class="leg">
      <span><span class="sw" style="background:#1F3864"></span>Precio</span>
      <span><span class="sw" style="background:#FF0000"></span>Sobrevalorado</span>
      <span><span class="sw" style="background:#9DC3E6"></span>Mediana</span>
      <span><span class="sw" style="background:#00B050"></span>Infravalorado</span>
    </div>
  </div>
  <div class="card">
    <h4>Datos actuales</h4>
    <table class="corp" id="gwkv"></table>
    <h4 style="margin-top:14px">Historial de dividendos</h4>
    <table class="corp" id="gwdivtable"></table>
  </div>
</div>
<div class="ts">Actualizado: __TIMESTAMP__</div>

<script>
const PAL={navy:'#1F3864',red:'#FF0000',green:'#00B050',blueL:'#9DC3E6'};
const $=s=>document.querySelector(s);
const GW=__GW_DATA__;

function renderGW(tk){
  const G=GW[tk];
  if(!G){$('#gwBody').style.display='none';$('#gwEmpty').style.display='';return;}
  $('#gwBody').style.display='';$('#gwEmpty').style.display='none';
  const s=G.series,sm=G.summary;
  const rPc=v=>v==null?'—':(v*100).toFixed(0)+'%';
  const d2=v=>v==null?'—':'$'+v.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const ratingColor=sm.rating>=0.66?'#1f9d55':sm.rating>=0.4?'#d99100':'#d64545';

  // Sin disclaimer TGT (punto 3)
  $('#gwTitle').innerHTML=`<div>${sm.name}</div>
    <div style="font-size:13px">Rating: ${rPc(sm.rating)} &nbsp;·&nbsp; Upside: ${rPc(sm.upside)}</div>`;

  // ── SVG ──────────────────────────────────────────────────────────────────
  const W=640,H=340,P={l:62,r:14,t:10,b:24};
  const n=s.length;
  const closes=s.map(d=>d[1]),over=s.map(d=>d[2]),med=s.map(d=>d[3]),und=s.map(d=>d[4]);
  const all=[].concat(closes,over,und).filter(v=>v!=null);
  const ymax=Math.ceil(Math.max(...all)/50)*50,ymin=0;
  const X=i=>P.l+(W-P.l-P.r)*i/(n-1);
  const Y=v=>P.t+(H-P.t-P.b)*(1-(v-ymin)/(ymax-ymin));
  const poly=(arr,col,wd,dash)=>{
    const pts=arr.map((v,i)=>v==null?null:X(i).toFixed(1)+','+Y(v).toFixed(1)).filter(Boolean);
    return `<polyline points="${pts.join(' ')}" fill="none" stroke="${col}" stroke-width="${wd}" ${dash?`stroke-dasharray="${dash}"`:''}/>`;
  };
  const dlr=v=>'$'+v.toLocaleString('en-US',{maximumFractionDigits:0});
  let svg=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">`;
  for(let k=0;k<=8;k++){
    const v=ymax/8*k,yy=Y(v);
    svg+=`<line x1="${P.l}" y1="${yy.toFixed(1)}" x2="${W-P.r}" y2="${yy.toFixed(1)}" stroke="#d9d9d9" stroke-dasharray="3 3"/>`;
    svg+=`<text x="${P.l-5}" y="${(yy+3).toFixed(1)}" text-anchor="end" font-size="9" fill="#808080">${dlr(v)}</text>`;
  }
  svg+=poly(over,PAL.red,1.6)+poly(und,PAL.green,1.6)+poly(med,PAL.blueL,1.3,'6 4')+poly(closes,PAL.navy,1.6);
  svg+=`<line x1="${P.l}" y1="${H-P.b}" x2="${W-P.r}" y2="${H-P.b}" stroke="#808080"/>`;
  let prev=null;
  s.forEach((d,i)=>{
    const y=(''+d[0]).slice(0,4);
    if(y!==prev){
      const x=X(i).toFixed(1);
      svg+=`<line x1="${x}" y1="${H-P.b}" x2="${x}" y2="${H-P.b+4}" stroke="#808080"/>`;
      svg+=`<text x="${x}" y="${H-8}" text-anchor="middle" font-size="10" fill="#595959">${y}</text>`;
      prev=y;
    }
  });
  svg+=`<line id="gwgd" x1="-9" y1="${P.t}" x2="-9" y2="${H-P.b}" stroke="${PAL.navy}" stroke-width="1" opacity="0"/>`;
  svg+=`<circle id="gwdot" r="3.2" fill="${PAL.navy}" opacity="0"/>`;
  svg+='</svg>';

  const box=$('#gwchart');box.innerHTML=svg;
  const tip=document.createElement('div');tip.className='tip';box.appendChild(tip);
  const el=box.querySelector('svg'),gd=el.querySelector('#gwgd'),dot=el.querySelector('#gwdot');
  el.addEventListener('mousemove',e=>{
    const pt=el.createSVGPoint();pt.x=e.clientX;pt.y=e.clientY;
    const loc=pt.matrixTransform(el.getScreenCTM().inverse());
    let i=Math.round((loc.x-P.l)/(W-P.l-P.r)*(n-1));
    i=Math.max(0,Math.min(n-1,i));
    const d=s[i];
    gd.setAttribute('x1',X(i));gd.setAttribute('x2',X(i));gd.setAttribute('opacity','0.35');
    dot.setAttribute('cx',X(i));dot.setAttribute('cy',Y(d[1]));dot.setAttribute('opacity','1');
    tip.innerHTML=`<div style="font-weight:700;margin-bottom:2px">${d[0]}</div>
      <div><span class="k" style="background:${PAL.navy}"></span>Precio: <b>$${d[1]}</b></div>
      <div><span class="k" style="background:${PAL.red}"></span>Sobreval.: <b>${d[2]?'$'+d[2].toFixed(0):'—'}</b></div>
      <div><span class="k" style="background:${PAL.green}"></span>Infraval.: <b>${d[4]?'$'+d[4].toFixed(0):'—'}</b></div>`;
    const sc=el.clientWidth/W;
    tip.style.left=(X(i)*sc)+'px';tip.style.top=((Y(d[1])-6)*sc)+'px';tip.style.opacity='1';
  });
  el.addEventListener('mouseleave',()=>{
    tip.style.opacity='0';
    gd.setAttribute('opacity','0');dot.setAttribute('opacity','0');
  });

  // ── KV ───────────────────────────────────────────────────────────────────
  $('#gwkv').innerHTML=[
    ['Precio actual',        d2(sm.precio_actual)],
    ['Yield actual',         rPc(sm.yield_actual)],
    ['Rating',               `<span class="badge" style="background:${ratingColor}">${rPc(sm.rating)}</span>`],
    ['Upside',               `<b style="color:${sm.upside>=0?'#1f9d55':'#d64545'}">${rPc(sm.upside)}</b>`],
    ['Canal sobrevalorado',  d2(sm.p_over)],
    ['Canal mediana',        d2(sm.p_mediana)],
    ['Canal infravalorado',  d2(sm.p_under)],
    ['Div. Growth 3y',       rPc(sm.dg3)],
    ['Div. Growth 5y',       rPc(sm.dg5)],
    ['Div. Growth 10y',      rPc(sm.dg10)],
    ['Mínimo 52W',           d2(sm.min52)],
    ['Máximo 52W',           d2(sm.max52)],
  ].map(([k,v])=>`<tr><td>${k}</td><td>${v}</td></tr>`).join('');

  // ── Dividendos ───────────────────────────────────────────────────────────
  $('#gwdivtable').innerHTML='<tr><th>Año</th><th>DPS</th></tr>'+
    G.divhist.map(([y,v])=>`<tr><td>${y}</td><td>$${(+v).toFixed(2)}</td></tr>`).join('');
}

// Sin advertencia TGT (punto 3)
Object.keys(GW).sort().forEach(tk=>{
  const nm=GW[tk]&&GW[tk].summary&&GW[tk].summary.name&&GW[tk].summary.name!==tk?GW[tk].summary.name:'';
  const lbl=nm?tk+' — '+nm:tk;
  const o=new Option(lbl,tk);
  $('#gwSel').add(o);
});
$('#gwSel').addEventListener('change',()=>renderGW($('#gwSel').value));
const _hash=window.location.hash.replace('#ticker=','');
const _query=new URLSearchParams(window.location.search).get('ticker');
const _urlTk=decodeURIComponent(_hash||_query||'');
const _initTk=(_urlTk&&GW[_urlTk])?_urlTk:Object.keys(GW).sort()[0];
if(_initTk){$('#gwSel').value=_initTk;renderGW(_initTk);}
</script>
</body></html>
"""


def generar_html(gw_data, html_path, timestamp):
    gw_json = json.dumps(gw_data, ensure_ascii=False, separators=(',', ':'))
    html    = HTML_TEMPLATE.replace("__GW_DATA__",  gw_json)
    html    = html.replace("__TIMESTAMP__", timestamp)
    Path(html_path).write_text(html, encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",   default="./data/graficos.db")
    parser.add_argument("--html", default="./docs/index.html")
    args = parser.parse_args()

    db_path   = Path(args.db)
    html_path = Path(args.html)

    if not db_path.exists():
        print(f"ERROR: DB no encontrada en '{db_path}'.")
        print("  → Ejecuta primero: python carga_inicial.py")
        return

    html_path.parent.mkdir(parents=True, exist_ok=True)

    sep = "─" * 60
    ts  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{sep}")
    print(f"  Base datos : {db_path.resolve()}")
    print(f"  HTML output: {html_path.resolve()}")
    print(f"  Timestamp  : {ts}")
    print(f"{sep}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # ── PASO 1: Obtener tickers de la DB ──────────────────────────────────────
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM precios ORDER BY ticker"
    ).fetchall()]
    print(f"[ PASO 1 ] {len(tickers)} tickers en DB: {', '.join(tickers)}\n")

    # ── PASO 2: Descargar precios nuevos desde Yahoo ──────────────────────────
    print("[ PASO 2 ] Descargando precios desde Yahoo Finance…\n")
    nuevos_total = 0
    for ticker in tickers:
        ultima = conn.execute(
            "SELECT MAX(fecha) FROM precios WHERE ticker=?", (ticker,)
        ).fetchone()[0]
        if ultima:
            dt_ultima   = datetime.strptime(ultima, "%Y-%m-%d").date()
            fecha_desde = (dt_ultima + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            fecha_desde = "2016-01-01"

        yahoo  = ticker_a_yahoo(ticker)
        nuevos = descargar_precios(ticker, fecha_desde)

        if nuevos:
            cur = conn.cursor()
            cur.executemany(
                "INSERT OR IGNORE INTO precios VALUES (NULL,?,?,?)", nuevos
            )
            conn.commit()
            nuevos_total += cur.rowcount
            print(f"  ✓  {ticker:<20} ({yahoo:<10})  +{len(nuevos)} precios  (desde {fecha_desde})")
        else:
            print(f"  –  {ticker:<20} ({yahoo:<10})  sin datos nuevos desde {fecha_desde}")

        time.sleep(0.3)

    print(f"\n  Total filas nuevas de precio: {nuevos_total}")

    # ── PASO 3: Actualizar dividendos anuales desde Yahoo ─────────────────────
    print(f"\n[ PASO 3 ] Actualizando dividendos anuales…\n")
    for ticker in tickers:
        divs = descargar_dividendos_anuales(ticker)
        if divs:
            conn.executemany(
                "INSERT OR REPLACE INTO dividendos VALUES (NULL,?,?,?)", divs
            )
            conn.commit()
            anios = [str(d[1]) for d in divs]
            print(f"  ✓  {ticker:<20}  {len(divs)} años → {', '.join(anios[-4:])}")
        else:
            print(f"  –  {ticker:<20}  sin dividendos en Yahoo (se omite)")
        time.sleep(0.3)

    # ── PASO 4: Recalcular umbrales ───────────────────────────────────────────
    print(f"\n[ PASO 4 ] Recalculando umbrales de yield…\n")
    calcular_umbrales(conn)
    rows = conn.execute(
        "SELECT ticker, yield_overvalued, yield_undervalued, yield_mediana "
        "FROM umbrales_yield ORDER BY ticker"
    ).fetchall()
    for r in rows:
        print(f"  {r[0]:<20}  over={r[1]:.4%}  under={r[2]:.4%}  med={r[3]:.4%}")

    # ── PASO 5: Regenerar HTML ────────────────────────────────────────────────
    print(f"\n[ PASO 5 ] Generando HTML…\n")
    gw_data = generar_gw(conn)
    generar_html(gw_data, str(html_path), ts)
    print(f"  ✓  {len(gw_data)} tickers en docs/index.html")
    print(f"  ✓  → {html_path.resolve()}")

    conn.close()
    print(f"\n{sep}")
    print(f"  Actualización completada: {ts}")
    print(f"{sep}\n")


if __name__ == "__main__":
    main()
