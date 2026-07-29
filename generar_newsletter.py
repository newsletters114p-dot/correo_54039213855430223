"""
generar_newsletter.py  v7
═══════════════════════════════════════════════════════════════════════════════
Newsletter semanal — Estrategia Dividendo Creciente / Método Geraldine Weiss

Fuentes:
  tickers_activos.csv → qué aparece + sector + tipo (fuente de verdad)
  tickers_maestro.csv → nombres de empresa
  data/graficos.db    → métricas GW
  Yahoo Finance       → rendimiento semanal y precio sin dividendo
"""

import argparse
import csv
import os
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yfinance as yf

# ══════════════════════════════════════════════════════════════════════════════
#  Configuración
# ══════════════════════════════════════════════════════════════════════════════
GH_PAGES_URL  = "https://newsletters114p-dot.github.io/correo_54039213855430223"
ASUNTO        = "📊 Dividendo Creciente — Seguimiento semanal"
DB_PATH       = "./data/graficos.db"
CSV_MAESTRO   = "./tickers_maestro.csv"
CSV_ACTIVOS   = "./tickers_activos.csv"

TICKERS_SIN_DIV = {"BRK/B US"}

HALL_OF_FAME = {
    "DHLGY US", "BNS US", "ADRNY US", "MSFT US", "TROW US",
    "MAA US", "UL US", "AMZN US", "KO US", "BTI US",
    "AAPL US", "BBY US", "GOOG US", "FRT US", "META US",
    "NVDA US", "BMO US",
}

TICKERS_DATOS_INCORRECTOS = {"BATS LN", "RIO LN", "ULVR LN"}

ORDEN_SECTORES = [
    "Information Technology", "Communication Services", "Health Care",
    "Industrials", "Financials", "Real Estate", "Consumer Staples",
    "Consumer Discretionary", "Energy", "Utilities", "Materials",
]

ORDEN_TIPO = {"Titular": 0, "Banquillo": 1, "Cantera": 2}

NAVY = "#1B3A5C"
BLUE = "#2B6CB0"
FONT = "Calibri,Arial,sans-serif"

SUFIJOS_YAHOO = {
    "US": "", "CN": ".TO", "NA": ".AS", "LN": ".L", "GY": ".DE",
    "FP": ".PA", "PA": ".PA", "SM": ".MC", "MC": ".MC", "HK": ".HK",
    "AU": ".AX", "JP": ".T", "SW": ".SW", "IT": ".MI", "BB": ".BR",
    "SE": ".ST", "DC": ".CO", "NO": ".OL", "FH": ".HE",
}

# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def ticker_a_yahoo(tk):
    partes = tk.strip().upper().split()
    if len(partes) < 2: return tk
    simbolo = partes[0].replace("/", "-")
    mercado = partes[-1]
    sufijo  = SUFIJOS_YAHOO.get(mercado, f".{mercado}")
    if mercado == "HK" and simbolo.isdigit(): simbolo = simbolo.zfill(4)
    return simbolo + sufijo

def estado_desde_precio(precio, canal_inferior, canal_superior):
    if precio is None or canal_inferior is None or canal_superior is None:
        return "Neutral", "#F1F5F9", "#64748B", "#CBD5E1"
    if precio < canal_inferior:
        return "Infraval.",       "#DCFCE7", "#166534", "#BBF7D0"
    if precio < canal_inferior * 1.03:
        return "Cerca infraval.", "#DCFCE7", "#15803D", "#BBF7D0"
    if precio > canal_superior:
        return "Sobreval.",       "#FEE2E2", "#991B1B", "#FECACA"
    if precio > canal_superior * 0.97:
        return "Cerca sobreval.", "#FEF3C7", "#92400E", "#FDE68A"
    return "Neutral",             "#F1F5F9", "#64748B", "#CBD5E1"

def fmt_precio(v, moneda="$"):
    return "—" if v is None else f"{moneda}{v:,.2f}"

def fmt_pct(v, signo=True, decimales=0):
    if v is None: return "—"
    s = f"{v*100:.{decimales}f}%"
    if signo and v > 0: s = "+" + s
    return s

def fmt_semana(v):
    try:
        if v is None or v != v: return "—", "#64748B"
        return fmt_pct(float(v), signo=True, decimales=2), ("#166534" if float(v) >= 0 else "#991B1B")
    except: return "—", "#64748B"

def moneda_ticker(tk):
    partes = tk.strip().upper().split()
    if len(partes) < 2: return "$"
    m = partes[-1]
    if m == "LN": return "p"
    if m in ("FP", "PA"): return "€"
    if m in ("CN", "TO"): return "C$"
    return "$"

# ══════════════════════════════════════════════════════════════════════════════
#  Lectura de datos
# ══════════════════════════════════════════════════════════════════════════════
def leer_maestro(csv_path):
    """Solo nombres. Sector y tipo NO se leen de aquí."""
    nombres = {}
    if not Path(csv_path).exists(): return nombres
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tk = row.get("ticker", "").strip()
            nm = row.get("nombre", "").strip()
            if tk: nombres[tk] = nm or tk
    return nombres

def leer_activos(csv_path):
    """Fuente de verdad: qué tickers aparecen, con qué sector y tipo."""
    activos = {}
    if not Path(csv_path).exists():
        print(f"  ⚠  {csv_path} no encontrado")
        return activos
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tk = row.get("ticker", "").strip()
            if tk:
                activos[tk] = {
                    "sector": row.get("sector", "-").strip() or "-",
                    "tipo":   row.get("tipo",   "No info").strip() or "No info",
                }
    return activos

def leer_db(db_path):
    if not Path(db_path).exists():
        raise FileNotFoundError(f"DB no encontrada: '{db_path}'")
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT ticker, ultima_fecha, precio_actual, dps_actual,
               yield_pct, rating_pct, upside_pct,
               canal_inferior, canal_mediana, canal_superior, max_drawdown_pct
        FROM resumen_tickers ORDER BY ticker
    """).fetchall()
    conn.close()
    datos = {}
    for r in rows:
        datos[r[0]] = {
            "ultima_fecha":   r[1],
            "precio":         r[2],  "dps_db":         r[3],
            "yield_pct":      (r[4] or 0)/100, "rating": (r[5] or 0)/100,
            "upside":         (r[6] or 0)/100,
            "canal_inferior": r[7],  "canal_mediana":  r[8],
            "canal_superior": r[9],  "max_drawdown":   (r[10] or 0)/100,
        }
    return datos

def db_vacio():
    return {"ultima_fecha": None, "precio": None, "dps_db": None,
            "yield_pct": None, "rating": None, "upside": None,
            "canal_inferior": None, "canal_mediana": None,
            "canal_superior": None, "max_drawdown": None}

def descargar_precio_actual(tk):
    yahoo = ticker_a_yahoo(tk)
    try:
        hist = yf.Ticker(yahoo).history(period="5d", auto_adjust=True)
        if not hist.empty:
            closes = hist["Close"].dropna()
            if not closes.empty: return round(float(closes.iloc[-1]), 2)
    except Exception as e:
        print(f"    ⚠  Yahoo precio {yahoo}: {e}")
    return None

def descargar_rendimiento_semanal(tickers):
    rendimientos = {}
    fecha_desde  = (datetime.today() - timedelta(days=12)).strftime("%Y-%m-%d")
    for tk in tickers:
        yahoo = ticker_a_yahoo(tk)
        try:
            hist = yf.Ticker(yahoo).history(start=fecha_desde, auto_adjust=True)
            if not hist.empty and len(hist) >= 2:
                closes = hist["Close"].dropna()
                if closes.empty: rendimientos[tk] = None; continue
                p_actual = float(closes.iloc[-1])
                fechas   = hist.index
                if hasattr(fechas, "tz") and fechas.tz is not None:
                    fechas = fechas.tz_convert(None)
                limite = datetime.today() - timedelta(days=7)
                mask   = fechas <= limite
                p_sem  = float(hist["Close"][mask].iloc[-1]) if mask.any() else float(hist["Close"].iloc[0])
                rendimientos[tk] = (p_actual / p_sem) - 1
            else:
                rendimientos[tk] = None
        except Exception as e:
            print(f"    ⚠  Semana {yahoo}: {e}")
            rendimientos[tk] = None
        time.sleep(0.2)
    return rendimientos

# ══════════════════════════════════════════════════════════════════════════════
#  HTML helpers
# ══════════════════════════════════════════════════════════════════════════════
def td(contenido, align="left", extra="", borde=True):
    b = "border-bottom:1px solid #E2E8F0;" if borde else ""
    return f'<td align="{align}" style="padding:7px 10px;{b}font-family:{FONT};font-size:11.5px;{extra}">{contenido}</td>'

def badge_tipo(tipo):
    tipo = (tipo or "No info").strip()
    if tipo == "No info": tipo = "-"
    estilos = {"Titular": ("#1B3A5C","700"), "Banquillo": ("#64748B","400"),
                "Cantera": ("#9A3412","400"), "-": ("#94A3B8","400")}
    color, weight = estilos.get(tipo, ("#94A3B8","400"))
    return f'<span style="font-size:11px;font-weight:{weight};color:{color};font-family:{FONT}">{tipo}</span>'

def badge_estado(precio, canal_inferior, canal_superior):
    texto, bg, color, borde = estado_desde_precio(precio, canal_inferior, canal_superior)
    return (f'<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:0 auto">'
            f'<tr><td align="center" bgcolor="{bg}" style="padding:3px 10px;border:1px solid {borde};'
            f'font-size:10px;font-weight:700;color:{color};font-family:{FONT};white-space:nowrap">'
            f'{texto}</td></tr></table>')

def _url_ticker(tk):
    return f"{GH_PAGES_URL}/index.html#ticker={tk.replace(' ','%20')}"

def _link_ticker(tk, url):
    disc = (' <span style="color:#92400E;font-size:9px;font-weight:700">⚠ gráfico incompleto</span>'
            if tk in TICKERS_DATOS_INCORRECTOS else "")
    return f'<a href="{url}" style="color:{NAVY};font-weight:700;text-decoration:none;font-family:{FONT}">{tk}</a>{disc}'

def _link_nombre(nombre, url):
    return f'<a href="{url}" style="color:#334155;text-decoration:none;font-family:{FONT}">{nombre}</a>'

def cabecera_tabla(cols, aligns):
    ths = "".join(f'<th align="{a}" style="padding:8px 10px;font-size:10.5px;font-weight:700;'
                  f'color:#ffffff;font-family:{FONT};white-space:nowrap">{h}</th>'
                  for h, a in zip(cols, aligns))
    return f'<tr bgcolor="{NAVY}">{ths}</tr>'

COLS_SENALES = (
    ["Tipo","Ticker","Nombre","Sector","Precio cierre","Var. semana","Infravaloración","Sobrevaloración","Rating","Upside"],
    ["left","left","left","left","right","right","right","right","right","right"],
)
COLS_DETALLE = (
    ["Tipo","Ticker","Nombre","Precio cierre","Var. semana","Infravaloración","Sobrevaloración","Rating","Upside","Estado"],
    ["left","left","left","right","right","right","right","right","right","center"],
)
COLS_HOF = (
    ["Ticker","Nombre","Precio cierre","Var. semana","Infravaloración","Sobrevaloración","Rating","Upside","Estado"],
    ["left","left","right","right","right","right","right","right","center"],
)

def _metricas(f, con_sector=False, con_tipo=True):
    tk     = f["ticker"]
    db     = f["db"]
    meta   = f["meta"]
    moneda = moneda_ticker(tk)
    rv, uv = db.get("rating"), db.get("upside")
    url    = _url_ticker(tk)
    sem_txt, sem_color = fmt_semana(f.get("semana"))
    r_color = "#166534" if (rv or 0) <= 0.5 else "#991B1B"
    u_color = "#166534" if (uv or 0) >= 0   else "#991B1B"
    td_sem  = (f'<td align="right" style="padding:7px 10px;border-bottom:1px solid #E2E8F0;'
               f'font-weight:700;color:{sem_color};font-family:{FONT};font-size:11.5px">{sem_txt}</td>')
    celdas = ""
    if con_tipo:
        celdas += f'<td style="padding:7px 10px;border-bottom:1px solid #E2E8F0">{badge_tipo(meta.get("tipo","No info"))}</td>'
    celdas += td(_link_ticker(tk, url))
    celdas += td(_link_nombre(meta.get("nombre", tk), url))
    if con_sector:
        celdas += td(meta.get("sector","-"), extra="color:#64748B")
    celdas += td(fmt_precio(db.get("precio"), moneda), align="right")
    celdas += td_sem
    celdas += td(fmt_precio(db.get("canal_inferior"), moneda), align="right", extra="color:#166534;font-weight:700")
    celdas += td(fmt_precio(db.get("canal_superior"), moneda), align="right", extra="color:#DC2626;font-weight:700")
    celdas += td(fmt_pct(rv, signo=False, decimales=1) if rv is not None else "—", align="right", extra=f"color:{r_color};font-weight:700")
    celdas += td(fmt_pct(uv, signo=True,  decimales=1) if uv is not None else "—", align="right", extra=f"color:{u_color};font-weight:700")
    return celdas

def fila_senales(f, bg):
    return f'<tr bgcolor="{bg}">{_metricas(f, con_sector=True, con_tipo=True)}</tr>'

def fila_detalle(f, bg):
    db = f["db"]
    celdas = _metricas(f, con_sector=False, con_tipo=True)
    celdas += f'<td align="center" style="padding:7px 10px;border-bottom:1px solid #E2E8F0">{badge_estado(db.get("precio"),db.get("canal_inferior"),db.get("canal_superior"))}</td>'
    return f'<tr bgcolor="{bg}">{celdas}</tr>'

def fila_hof(f, bg):
    db = f["db"]
    celdas = _metricas(f, con_sector=False, con_tipo=False)
    celdas += f'<td align="center" style="padding:7px 10px;border-bottom:1px solid #E2E8F0">{badge_estado(db.get("precio"),db.get("canal_inferior"),db.get("canal_superior"))}</td>'
    return f'<tr bgcolor="{bg}">{celdas}</tr>'

def bloque_senales(filas, titulo, color_titulo):
    if not filas: return ""
    rows = "".join(fila_senales(f,"#FFFFFF" if i%2==0 else "#F8FAFC") for i,f in enumerate(filas))
    return f"""
<tr><td style="padding:0 0 8px 0">
  <div style="font-size:16px;font-weight:700;color:{color_titulo};font-family:{FONT};padding:12px 0 6px 0;border-bottom:2px solid {color_titulo}">{titulo}</div>
</td></tr>
<tr><td style="padding-bottom:20px">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:{FONT};font-size:11.5px">
    {cabecera_tabla(*COLS_SENALES)}{rows}
  </table>
</td></tr>"""

def bloque_detalle(todos):
    por_sector = {}
    for f in todos:
        s = f["meta"].get("sector","-") or "-"
        if s == "-": continue
        por_sector.setdefault(s,[]).append(f)
    sectores = [s for s in ORDEN_SECTORES if s in por_sector]
    sectores += sorted(s for s in por_sector if s not in ORDEN_SECTORES)
    cuerpo = ""
    for sector in sectores:
        cuerpo += (f'<tr><td colspan="10" bgcolor="{NAVY}" style="padding:6px 10px;font-size:10.5px;'
                   f'font-weight:700;color:#FFFFFF;letter-spacing:.8px;text-transform:uppercase;'
                   f'font-family:{FONT}">{sector}</td></tr>')
        cuerpo += cabecera_tabla(*COLS_DETALLE)
        for i, f in enumerate(sorted(por_sector[sector], key=lambda x: ORDEN_TIPO.get(x["meta"].get("tipo",""),3))):
            cuerpo += fila_detalle(f,"#FFFFFF" if i%2==0 else "#F8FAFC")
    return f"""
<tr><td style="padding:0 0 8px 0">
  <div style="font-size:16px;font-weight:700;color:{BLUE};font-family:{FONT};padding:12px 0 6px 0;border-bottom:2px solid {BLUE}">Detalle completo por sector</div>
</td></tr>
<tr><td style="padding-bottom:20px">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:{FONT};font-size:11.5px">
    {cuerpo}
  </table>
</td></tr>"""

def bloque_hall_of_fame(filas):
    if not filas: return ""
    rows = "".join(fila_hof(f,"#FFFFFF" if i%2==0 else "#F8FAFC") for i,f in enumerate(filas))
    return f"""
<tr><td style="padding:0 0 8px 0">
  <div style="font-size:16px;font-weight:700;color:{BLUE};font-family:{FONT};padding:12px 0 6px 0;border-bottom:2px solid {BLUE}">Hall of Fame</div>
</td></tr>
<tr><td style="padding-bottom:20px">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:{FONT};font-size:11.5px">
    {cabecera_tabla(*COLS_HOF)}{rows}
  </table>
</td></tr>"""

def generar_html(todos, fecha_str):
    hof_set = {f["ticker"] for f in todos if f["meta"].get("sector") == "Hall of Fame"}
    con_datos = [f for f in todos if f["ticker"] not in hof_set
                 and f["db"].get("precio") is not None
                 and f["db"].get("canal_inferior") is not None]
    infrav = sorted([f for f in con_datos if f["db"]["precio"] < f["db"]["canal_inferior"]],
                    key=lambda x: (ORDEN_TIPO.get(x["meta"].get("tipo",""),3), -(x["db"]["upside"] or 0)))
    sobrev = sorted([f for f in con_datos if f["db"]["precio"] > f["db"]["canal_superior"]],
                    key=lambda x: (ORDEN_TIPO.get(x["meta"].get("tipo",""),3), (x["db"]["upside"] or 0)))
    n_inf, n_sob, n_total = len(infrav), len(sobrev), len(todos)
    hof_filas = sorted([f for f in todos if f["ticker"] in hof_set], key=lambda x: x["ticker"])
    todos_s   = sorted([f for f in todos if f["ticker"] not in hof_set],
                       key=lambda x: (ORDEN_SECTORES.index(x["meta"].get("sector","")) if x["meta"].get("sector","") in ORDEN_SECTORES else 999, x["ticker"]))

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F1F5F9">
<table role="presentation" width="900" cellpadding="0" cellspacing="0" border="0"
       align="center" style="border-collapse:collapse;font-family:{FONT};background:#ffffff;margin:20px auto">
<tr>
  <td bgcolor="{NAVY}" style="padding:20px 28px;border-bottom:4px solid {BLUE}">
    <div style="font-size:10px;font-weight:700;color:#93C5FD;letter-spacing:2px;text-transform:uppercase;font-family:{FONT};margin-bottom:6px">SEGUIMIENTO SEMANAL</div>
    <div style="font-size:22px;font-weight:700;color:#FFFFFF;font-family:{FONT}">Estrategia Dividendo Creciente &mdash; M&eacute;todo Geraldine Weiss</div>
    <div style="font-size:11px;color:#93C5FD;margin-top:5px;font-family:{FONT}">Precios al &uacute;ltimo cierre &nbsp;&middot;&nbsp; Emitido {fecha_str}</div>
  </td>
</tr>
<tr>
  <td style="padding:20px 28px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">
      <tr>
        <td width="31%" bgcolor="#F0FDF4" style="padding:16px;text-align:center;border:1px solid #BBF7D0">
          <div style="font-size:32px;font-weight:700;color:#166534;font-family:{FONT};line-height:1">{n_inf}</div>
          <div style="font-size:10px;font-weight:700;color:#166534;font-family:{FONT};margin-top:6px;letter-spacing:1px">INFRAVALORADAS</div>
        </td>
        <td width="4%"></td>
        <td width="31%" bgcolor="#FEF2F2" style="padding:16px;text-align:center;border:1px solid #FECACA">
          <div style="font-size:32px;font-weight:700;color:#991B1B;font-family:{FONT};line-height:1">{n_sob}</div>
          <div style="font-size:10px;font-weight:700;color:#991B1B;font-family:{FONT};margin-top:6px;letter-spacing:1px">SOBREVALORADAS</div>
        </td>
        <td width="4%"></td>
        <td width="31%" bgcolor="#EFF6FF" style="padding:16px;text-align:center;border:1px solid #BFDBFE">
          <div style="font-size:32px;font-weight:700;color:{NAVY};font-family:{FONT};line-height:1">{n_total}</div>
          <div style="font-size:10px;font-weight:700;color:{NAVY};font-family:{FONT};margin-top:6px;letter-spacing:1px">ACTIVOS EN SEGUIMIENTO</div>
        </td>
      </tr>
    </table>
  </td>
</tr>
<tr>
  <td style="padding:0 28px 20px 28px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">
      {bloque_senales(infrav,"Infravaloradas","#166534")}
      {bloque_senales(sobrev,"Sobrevaloradas","#991B1B")}
      {bloque_detalle(todos_s)}
      {bloque_hall_of_fame(hof_filas)}
    </table>
  </td>
</tr>
<tr>
  <td style="padding:16px 28px 24px 28px;border-top:1px solid #E2E8F0">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">
      <tr><td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8;padding-bottom:6px">
        <span style="color:#1B3A5C;font-weight:700">Estado</span> comparaci&oacute;n directa precio vs bandas:
        <span style="color:#166534;font-weight:700">Infraval.</span> = debajo del canal &nbsp;|&nbsp;
        <span style="color:#15803D;font-weight:700">Cerca infraval.</span> = dentro del 3% &nbsp;|&nbsp;
        <span style="color:#64748B;font-weight:700">Neutral</span> &nbsp;|&nbsp;
        <span style="color:#92400E;font-weight:700">Cerca sobreval.</span> = dentro del 3% &nbsp;|&nbsp;
        <span style="color:#991B1B;font-weight:700">Sobreval.</span> = encima del canal
      </td></tr>
      <tr><td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8;padding-bottom:6px">
        <span style="color:#1B3A5C;font-weight:700">Tipo</span>:
        <span style="color:#1B3A5C;font-weight:700">Titular</span> = posici&oacute;n activa &nbsp;|&nbsp;
        <span style="color:#64748B;font-weight:700">Banquillo</span> = en seguimiento &nbsp;|&nbsp;
        <span style="color:#9A3412;font-weight:700">Cantera</span> = an&aacute;lisis preliminar
      </td></tr>
      <tr><td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8">
        Precios al cierre. Monedas: &euro; Europa · C$ Canad&aacute; · p peniques Londres.
        Valoraci&oacute;n Geraldine Weiss. Documento interno; no constituye recomendaci&oacute;n de inversi&oacute;n.
        &nbsp;&middot;&nbsp;
        <a href="{GH_PAGES_URL}/index.html" style="color:{BLUE};text-decoration:underline">Ver gr&aacute;ficos &rarr;</a>
      </td></tr>
    </table>
  </td>
</tr>
</table>
</body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
#  Envío Gmail
# ══════════════════════════════════════════════════════════════════════════════
def enviar_gmail(html, fecha_str, gmail_user, gmail_pass, destinatarios):
    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"{ASUNTO} — {fecha_str}"
    msg["From"]    = gmail_user
    msg["To"]      = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(gmail_user, gmail_pass)
        s.sendmail(gmail_user, destinatarios, msg.as_string())
    print(f"  ✓  Enviada a: {', '.join(destinatarios)}")

# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",        default=DB_PATH)
    parser.add_argument("--solo-html", action="store_true")
    parser.add_argument("--out",       default="./newsletter_preview.html")
    args = parser.parse_args()

    fecha_str = datetime.today().strftime("%d/%m/%Y")
    sep = "─" * 60
    print(f"\n{sep}\n  Newsletter GW · {fecha_str}\n{sep}\n")

    print("[ PASO 1 ] Leyendo datos…\n")
    nombres = leer_maestro(CSV_MAESTRO)   # ticker → nombre
    activos = leer_activos(CSV_ACTIVOS)   # ticker → {sector, tipo}  ← FUENTE DE VERDAD
    db_data = leer_db(args.db)

    # Tickers sin dividendo: añadir con precio de Yahoo
    for tk in TICKERS_SIN_DIV:
        if tk not in db_data:
            precio = descargar_precio_actual(tk)
            db_data[tk] = db_vacio()
            db_data[tk]["precio"] = precio
            print(f"  {tk} (sin dividendo) precio={'$'+str(precio) if precio else 'N/D'}")

    # Lista final de tickers: activos + Hall of Fame + sin dividendo
    tickers_visibles = set(activos.keys()) | TICKERS_SIN_DIV
    tickers = sorted(tickers_visibles)
    print(f"  Activos: {len(activos)} · HoF: {len(HALL_OF_FAME)} · Total: {len(tickers)}")

    print(f"\n[ PASO 2 ] Descargando rendimiento semanal…\n")
    rendimientos = descargar_rendimiento_semanal(tickers)
    print(f"  OK: {sum(1 for v in rendimientos.values() if v is not None)}/{len(tickers)}")

    print(f"\n[ PASO 3 ] Ensamblando…\n")
    todos = []
    for tk in tickers:
        # Sector y tipo: SIEMPRE de tickers_activos (fuente de verdad)
        if tk in activos:
            sector = activos[tk]["sector"]
            tipo   = activos[tk]["tipo"]
        else:
            sector, tipo = "-", "No info"

        nombre = nombres.get(tk) or tk
        todos.append({
            "ticker": tk,
            "meta":   {"nombre": nombre, "sector": sector, "tipo": tipo},
            "db":     db_data.get(tk, db_vacio()),
            "semana": rendimientos.get(tk),
        })

    con_datos = [f for f in todos if f["ticker"] not in HALL_OF_FAME
                 and f["db"].get("precio") is not None
                 and f["db"].get("canal_inferior") is not None]
    n_i = sum(1 for f in con_datos if f["db"]["precio"] < f["db"]["canal_inferior"])
    n_s = sum(1 for f in con_datos if f["db"]["precio"] > f["db"]["canal_superior"])
    print(f"  Infraval: {n_i}  Sobreval: {n_s}  Total: {len(todos)}")

    html = generar_html(todos, fecha_str)

    if args.solo_html:
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"\n  ✓  HTML guardado en: {args.out}\n")
    else:
        gmail_user = os.environ.get("GMAIL_USER", "")
        gmail_pass = os.environ.get("GMAIL_APP_PASS", "")
        destinos   = [d.strip() for d in os.environ.get("DESTINATARIOS","").split(",") if d.strip()]
        if not gmail_user or not gmail_pass:
            print("  ERROR: GMAIL_USER y GMAIL_APP_PASS necesarios.\n"); return
        if not destinos:
            print("  ERROR: DESTINATARIOS no configurado.\n"); return
        print(f"\n[ PASO 4 ] Enviando…\n")
        enviar_gmail(html, fecha_str, gmail_user, gmail_pass, destinos)

    print(f"\n{sep}\n  Completado: {fecha_str}\n{sep}\n")

if __name__ == "__main__":
    main()