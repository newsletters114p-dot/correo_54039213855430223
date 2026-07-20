"""
generar_newsletter.py  v5
═══════════════════════════════════════════════════════════════════════════════
Newsletter semanal — Estrategia Dividendo Creciente / Método Geraldine Weiss
100% compatible con Outlook Classic (tablas, inline CSS, sin JS, sin flex)

La columna "tipo" de tickers_maestro.csv contiene: Titular · Banquillo · Cantera · No info
No hay Core / Candidate / Tactical — esas etiquetas fueron eliminadas.

Variables de entorno (GitHub Secrets):
    GMAIL_USER      → email remitente
    GMAIL_APP_PASS  → contraseña de aplicación de 16 caracteres
    DESTINATARIOS   → emails separados por coma

Uso local:
    python generar_newsletter.py --solo-html --out preview.html
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
GH_PAGES_URL = "https://newsletters114p-dot.github.io/correo_54039213855430223/"
ASUNTO       = "📊 Dividendo Creciente — Seguimiento semanal"
DB_PATH      = "./data/graficos.db"
CSV_PATH     = "./tickers_maestro.csv"

# Umbrales de estado basados en rating
RATING_INFRAVAL       = 0.30
RATING_CERCA_INFRAVAL = 0.40
RATING_CERCA_SOBREVAL = 0.70
RATING_SOBREVAL       = 0.80

# Orden fijo de sectores
ORDEN_SECTORES = [
    "Information Technology",
    "Communication Services",
    "Health Care",
    "Industrials",
    "Financials",
    "Real Estate",
    "Consumer Staples",
    "Consumer Discretionary",
    "Energy",
    "Utilities",
    "Materials",
]


HALL_OF_FAME = {
    "DHLGY US", "BNS US", "ADRNY US", "MSFT US", "TROW US",
    "MAA US", "UL US", "AMZN US", "KO US", "BTI US",
    "AAPL US", "BBY US", "GOOG US", "FRT US", "META US",
    "NVDA US", "BMO US",
}

# Tickers con advertencia de datos incorrectos en newsletter y gráfico
TICKERS_DATOS_INCORRECTOS = {"BATS LN", "RIO LN", "ULVR LN"}

NAVY = "#1B3A5C"
BLUE = "#2B6CB0"
FONT = "Calibri,Arial,sans-serif"

SUFIJOS_YAHOO = {
    "US": "",     "CN": ".TO",  "NA": ".AS",  "LN": ".L",
    "GY": ".DE",  "FP": ".PA",  "PA": ".PA",  "SM": ".MC",
    "MC": ".MC",  "HK": ".HK",  "AU": ".AX",  "JP": ".T",
    "SW": ".SW",  "IT": ".MI",  "BB": ".BR",  "SE": ".ST",
    "DC": ".CO",  "NO": ".OL",  "FH": ".HE",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def ticker_a_yahoo(ticker_modelo: str) -> str:
    partes  = ticker_modelo.strip().upper().split()
    if len(partes) < 2:
        return ticker_modelo
    simbolo = partes[0]
    mercado = partes[-1]
    sufijo  = SUFIJOS_YAHOO.get(mercado, f".{mercado}")
    if mercado == "HK" and simbolo.isdigit():
        simbolo = simbolo.zfill(4)
    # Berkshire y otros tickers con "/" → Yahoo usa "-"
    simbolo = simbolo.replace("/", "-")
    return simbolo + sufijo


def estado_desde_rating(rating):
    """Devuelve (texto, bgcolor, color_texto, color_borde)."""
    if rating is None:
        return "Neutral", "#F1F5F9", "#64748B", "#CBD5E1"
    if rating < RATING_INFRAVAL:
        return "Infraval.",       "#DCFCE7", "#166534", "#BBF7D0"
    if rating < RATING_CERCA_INFRAVAL:
        return "Cerca infraval.", "#DCFCE7", "#15803D", "#BBF7D0"
    if rating < RATING_CERCA_SOBREVAL:
        return "Neutral",         "#F1F5F9", "#64748B", "#CBD5E1"
    if rating < RATING_SOBREVAL:
        return "Cerca sobreval.", "#FEF3C7", "#92400E", "#FDE68A"
    return "Sobreval.",           "#FEE2E2", "#991B1B", "#FECACA"


def fmt_precio(v, moneda="$"):
    if v is None:
        return "—"
    return f"{moneda}{v:,.2f}"


def fmt_pct(v, signo=True):
    if v is None:
        return "—"
    s = f"{v * 100:.0f}%"
    if signo and v > 0:
        s = "+" + s
    return s


def fmt_semana(v):
    try:
        if v is None or (v != v):
            return "—", "#64748B"
        return fmt_pct(float(v), signo=True), ("#166534" if float(v) >= 0 else "#991B1B")
    except Exception:
        return "—", "#64748B"


def moneda_ticker(ticker):
    partes = ticker.strip().upper().split()
    if len(partes) < 2:
        return "$"
    m = partes[-1]
    if m == "LN":
        return "p"        # peniques GBX
    if m in ("FP", "PA"):
        return "€"
    if m in ("CN", "TO"):
        return "C$"
    return "$"


# ══════════════════════════════════════════════════════════════════════════════
#  Lectura de datos
# ══════════════════════════════════════════════════════════════════════════════
def leer_maestro(csv_path):
    """
    Lee tickers_maestro.csv.
    La columna 'tipo' contiene: Titular, Banquillo, Cantera, No info.
    """
    maestro = {}
    if not Path(csv_path).exists():
        print(f"  ⚠  {csv_path} no encontrado")
        return maestro
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            tk = row.get("ticker", "").strip()
            if not tk:
                continue
            maestro[tk] = {
                "nombre": row.get("nombre", tk).strip() or tk,
                "sector": row.get("sector", "-").strip() or "-",
                "indice": row.get("indice", "-").strip() or "-",
                "tipo":   row.get("tipo",   "No info").strip() or "No info",
            }
    return maestro


def leer_db(db_path):
    """Lee resumen_tickers de la DB. Devuelve dict ticker → métricas."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"DB no encontrada: '{db_path}'")
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT ticker, ultima_fecha, precio_actual, dps_actual,
               yield_pct, rating_pct, upside_pct,
               canal_inferior, canal_mediana, canal_superior,
               max_drawdown_pct
        FROM resumen_tickers ORDER BY ticker
    """).fetchall()
    conn.close()
    datos = {}
    for r in rows:
        datos[r[0]] = {
            "ultima_fecha":   r[1],
            "precio":         r[2],
            "dps_db":         r[3],
            "yield_pct":      (r[4] or 0) / 100,
            "rating":         (r[5] or 0) / 100,
            "upside":         (r[6] or 0) / 100,
            "canal_inferior": r[7],
            "canal_mediana":  r[8],
            "canal_superior": r[9],
            "max_drawdown":   (r[10] or 0) / 100,
        }
    return datos


def descargar_precio_actual(ticker_modelo: str):
    """Precio de cierre más reciente para tickers sin datos en DB (ej: BRK/B)."""
    yahoo = ticker_a_yahoo(ticker_modelo)
    try:
        hist = yf.Ticker(yahoo).history(period="5d", auto_adjust=True)
        if not hist.empty:
            closes = hist["Close"].dropna()
            if not closes.empty:
                return round(float(closes.iloc[-1]), 2)
    except Exception as e:
        print(f"    ⚠  Precio Yahoo error para {yahoo}: {e}")
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
                if closes.empty:
                    rendimientos[tk] = None
                    continue
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
#  HTML helpers — Outlook Classic
# ══════════════════════════════════════════════════════════════════════════════
def td(contenido, align="left", extra="", borde=True):
    b = "border-bottom:1px solid #E2E8F0;" if borde else ""
    return (
        f'<td align="{align}" style="padding:7px 10px;{b}'
        f'font-family:{FONT};font-size:11.5px;{extra}">{contenido}</td>'
    )


def badge_tipo(tipo):
    """
    Titular   → azul oscuro / negrita
    Banquillo → gris medio
    Cantera   → terracota
    No info   → gris claro
    """
    tipo = (tipo or "No info").strip()
    estilos = {
        "Titular":   ("#1B3A5C", "700"),
        "Banquillo": ("#64748B", "400"),
        "Cantera":   ("#9A3412", "400"),
        "No info":   ("#94A3B8", "400"),
    }
    color, weight = estilos.get(tipo, ("#94A3B8", "400"))
    return (
        f'<span style="font-size:11px;font-weight:{weight};color:{color};'
        f'font-family:{FONT}">{tipo}</span>'
    )


def badge_estado(rating):
    texto, bg, color, borde = estado_desde_rating(rating)
    return (
        f'<table cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;margin:0 auto">'
        f'<tr><td align="center" bgcolor="{bg}" '
        f'style="padding:3px 10px;border:1px solid {borde};font-size:10px;'
        f'font-weight:700;color:{color};font-family:{FONT};white-space:nowrap">'
        f'{texto}</td></tr></table>'
    )


def _link_ticker(tk, url):
    disc = (
        ' <span style="color:#92400E;font-size:9px;font-weight:700">⚠ datos hist. incorrectos</span>'
        if tk in TICKERS_DATOS_INCORRECTOS else ""
    )
    return (
        f'<a href="{url}" style="color:{NAVY};font-weight:700;'
        f'text-decoration:none;font-family:{FONT}">{tk}</a>{disc}'
    )


def _link_nombre(nombre, url):
    return (
        f'<a href="{url}" style="color:#334155;'
        f'text-decoration:none;font-family:{FONT}">{nombre}</a>'
    )


def _url_ticker(tk):
    return f"{GH_PAGES_URL}/index.html#ticker={tk.replace(' ', '%20')}"


def cabecera_tabla(cols, aligns):
    ths = "".join(
        f'<th align="{a}" style="padding:8px 10px;font-size:10.5px;font-weight:700;'
        f'color:#ffffff;font-family:{FONT};white-space:nowrap">{h}</th>'
        for h, a in zip(cols, aligns)
    )
    return f'<tr bgcolor="{NAVY}">{ths}</tr>'


# ── Columnas de las dos tablas ────────────────────────────────────────────────
COLS_SENALES = (
    ["Tipo", "Ticker", "Nombre", "Sector", "Precio cierre", "Var. semana",
     "Infravaloración", "Sobrevaloración", "Rating", "Upside"],
    ["left", "left", "left", "left", "right", "right", "right", "right", "right", "right"],
)
COLS_DETALLE = (
    ["Tipo", "Ticker", "Nombre", "Precio cierre", "Var. semana",
     "Infravaloración", "Sobrevaloración", "Rating", "Upside", "Estado"],
    ["left", "left", "left", "right", "right", "right", "right", "right", "right", "center"],
)



COLS_HOF = (
    ["Ticker", "Nombre", "Precio cierre", "Var. semana",
     "Infravaloración", "Sobrevaloración", "Rating", "Upside", "Estado"],
    ["left", "left", "right", "right", "right", "right", "right", "right", "center"],
)

def _celdas_comunes(f, con_sector=False):
    """Devuelve las celdas numéricas reutilizables en ambos tipos de fila."""
    tk      = f["ticker"]
    db      = f["db"]
    moneda  = moneda_ticker(tk)
    precio  = fmt_precio(db.get("precio"), moneda)
    canal_i = fmt_precio(db.get("canal_inferior"), moneda)
    canal_s = fmt_precio(db.get("canal_superior"), moneda)
    rv      = db.get("rating")
    uv      = db.get("upside")
    rating_ = fmt_pct(rv, signo=False) if rv is not None else "—"
    upside_ = fmt_pct(uv, signo=True)  if uv is not None else "—"
    sem_txt, sem_color = fmt_semana(f.get("semana"))
    r_color = "#166534" if (rv or 0) <= 0.5 else "#991B1B"
    u_color = "#166534" if (uv or 0) >= 0   else "#991B1B"
    url     = _url_ticker(tk)
    lnk     = _link_ticker(tk, url)
    lnk_nom = _link_nombre(f["meta"].get("nombre", tk), url)

    tipo_badge  = badge_tipo(f["meta"].get("tipo", "No info"))
    td_tipo     = f'<td style="padding:7px 10px;border-bottom:1px solid #E2E8F0">{tipo_badge}</td>'
    td_semana   = (
        f'<td align="right" style="padding:7px 10px;border-bottom:1px solid #E2E8F0;'
        f'font-weight:700;color:{sem_color};font-family:{FONT};font-size:11.5px">'
        f'{sem_txt}</td>'
    )

    celdas = [
        td_tipo,
        td(lnk),
        td(lnk_nom),
    ]
    if con_sector:
        celdas.append(td(f["meta"].get("sector", "-"), extra="color:#64748B"))
    celdas += [
        td(precio,  align="right"),
        td_semana,
        td(canal_i, align="right", extra="color:#166534;font-weight:700"),
        td(canal_s, align="right", extra="color:#DC2626;font-weight:700"),
        td(rating_, align="right", extra=f"color:{r_color};font-weight:700"),
        td(upside_, align="right", extra=f"color:{u_color};font-weight:700"),
    ]
    return "".join(celdas)


def fila_senales(f, bg):
    return f'<tr bgcolor="{bg}">{_celdas_comunes(f, con_sector=True)}</tr>'


def fila_detalle(f, bg):
    celdas = _celdas_comunes(f, con_sector=False)
    rv     = f["db"].get("rating")
    celdas += (
        f'<td align="center" style="padding:7px 10px;'
        f'border-bottom:1px solid #E2E8F0">{badge_estado(rv)}</td>'
    )
    return f'<tr bgcolor="{bg}">{celdas}</tr>'


def bloque_senales(filas, titulo, color_titulo):
    if not filas:
        return ""
    rows = "".join(
        fila_senales(f, "#FFFFFF" if i % 2 == 0 else "#F8FAFC")
        for i, f in enumerate(filas)
    )
    cab = cabecera_tabla(*COLS_SENALES)
    return f"""
<tr>
  <td style="padding:0 0 8px 0">
    <div style="font-size:16px;font-weight:700;color:{color_titulo};
                font-family:{FONT};padding:12px 0 6px 0;
                border-bottom:2px solid {color_titulo}">{titulo}</div>
  </td>
</tr>
<tr>
  <td style="padding-bottom:20px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="border-collapse:collapse;font-family:{FONT};font-size:11.5px">
      {cab}
      {rows}
    </table>
  </td>
</tr>"""


def bloque_detalle(todos):
    """Detalle por sector, en el orden fijo de ORDEN_SECTORES."""
    por_sector = {}
    for f in todos:
        s = f["meta"].get("sector", "-") or "-"
        por_sector.setdefault(s, []).append(f)

    sectores_ordenados  = [s for s in ORDEN_SECTORES if s in por_sector]
    sectores_ordenados += sorted(s for s in por_sector if s not in ORDEN_SECTORES)

    cuerpo = ""
    for sector in sectores_ordenados:
        cuerpo += (
            f'<tr><td colspan="10" bgcolor="{NAVY}" style="padding:6px 10px;'
            f'font-size:10.5px;font-weight:700;color:#FFFFFF;letter-spacing:.8px;'
            f'text-transform:uppercase;font-family:{FONT}">{sector}</td></tr>'
        )
        cuerpo += cabecera_tabla(*COLS_DETALLE)
        for i, f in enumerate(por_sector[sector]):
            cuerpo += fila_detalle(f, "#FFFFFF" if i % 2 == 0 else "#F8FAFC")

    cab_titulo = (
        f'<div style="font-size:16px;font-weight:700;color:{BLUE};'
        f'font-family:{FONT};padding:12px 0 6px 0;'
        f'border-bottom:2px solid {BLUE}">Detalle completo por sector</div>'
    )
    return f"""
<tr>
  <td style="padding:0 0 8px 0">{cab_titulo}</td>
</tr>
<tr>
  <td style="padding-bottom:20px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="border-collapse:collapse;font-family:{FONT};font-size:11.5px">
      {cuerpo}
    </table>
  </td>
</tr>"""



def fila_hof(f, bg):
    """Fila para Hall of Fame: sin columna Tipo, con Estado."""
    tk      = f["ticker"]
    db      = f["db"]
    meta    = f["meta"]
    moneda  = moneda_ticker(tk)
    precio  = fmt_precio(db.get("precio"), moneda)
    canal_i = fmt_precio(db.get("canal_inferior"), moneda)
    canal_s = fmt_precio(db.get("canal_superior"), moneda)
    rv      = db.get("rating")
    uv      = db.get("upside")
    rating_ = fmt_pct(rv, signo=False) if rv is not None else "—"
    upside_ = fmt_pct(uv, signo=True)  if uv is not None else "—"
    sem_txt, sem_color = fmt_semana(f.get("semana"))
    r_color = "#166534" if (rv or 0) <= 0.5 else "#991B1B"
    u_color = "#166534" if (uv or 0) >= 0   else "#991B1B"
    url     = _url_ticker(tk)
    lnk     = _link_ticker(tk, url)
    lnk_nom = _link_nombre(meta.get("nombre", tk), url)
    td_semana = (
        f'<td align="right" style="padding:7px 10px;border-bottom:1px solid #E2E8F0;'
        f'font-weight:700;color:{sem_color};font-family:{FONT};font-size:11.5px">'
        f'{sem_txt}</td>'
    )
    celdas = (
        td(lnk)
        + td(lnk_nom)
        + td(precio,  align="right")
        + td_semana
        + td(canal_i, align="right", extra="color:#166534;font-weight:700")
        + td(canal_s, align="right", extra="color:#DC2626;font-weight:700")
        + td(rating_, align="right", extra=f"color:{r_color};font-weight:700")
        + td(upside_, align="right", extra=f"color:{u_color};font-weight:700")
        + f'<td align="center" style="padding:7px 10px;border-bottom:1px solid #E2E8F0">{badge_estado(rv)}</td>'
    )
    return f'<tr bgcolor="{bg}">{celdas}</tr>'


def bloque_hall_of_fame(filas):
    if not filas:
        return ""
    rows = "".join(
        fila_hof(f, "#FFFFFF" if i % 2 == 0 else "#F8FAFC")
        for i, f in enumerate(filas)
    )
    cab = cabecera_tabla(*COLS_HOF)
    cab_titulo = (
        f'<div style="font-size:16px;font-weight:700;color:{BLUE};'
        f'font-family:{FONT};padding:12px 0 6px 0;'
        f'border-bottom:2px solid {BLUE}">Hall of Fame</div>'
    )
    return f"""
<tr>
  <td style="padding:0 0 8px 0">{cab_titulo}</td>
</tr>
<tr>
  <td style="padding-bottom:20px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0"
           style="border-collapse:collapse;font-family:{FONT};font-size:11.5px">
      {cab}
      {rows}
    </table>
  </td>
</tr>"""


def generar_html(todos, fecha_str):
    con_datos = [f for f in todos if f["db"].get("rating") is not None]
    n_inf   = sum(1 for f in con_datos if (f["db"]["rating"] or 1) < RATING_INFRAVAL)
    n_sob   = sum(1 for f in con_datos if (f["db"]["rating"] or 0) >= RATING_SOBREVAL)
    n_total = len(todos)

    infrav = sorted(
        [f for f in con_datos if (f["db"]["rating"] or 1) < RATING_INFRAVAL],
        key=lambda x: x["db"]["upside"] or 0, reverse=True,
    )
    sobrev = sorted(
        [f for f in con_datos if (f["db"]["rating"] or 0) >= RATING_SOBREVAL],
        key=lambda x: x["db"]["upside"] or 0,
    )
    # Hall of Fame: bloque aparte, excluidos del detalle por sectores
    hof    = sorted(
        [f for f in todos if f["ticker"] in HALL_OF_FAME],
        key=lambda x: x["ticker"],
    )
    hof_tickers = {f["ticker"] for f in hof}
    todos_s = sorted(
        [f for f in todos if f["ticker"] not in hof_tickers],
        key=lambda x: (
            ORDEN_SECTORES.index(x["meta"].get("sector", ""))
            if x["meta"].get("sector", "") in ORDEN_SECTORES else 999,
            x["ticker"],
        ),
    )

    leyenda_tipo = (
        f'<span style="color:#1B3A5C;font-weight:700">Titular</span> = posición activa en cartera'
        f' &nbsp;|&nbsp; '
        f'<span style="color:#64748B;font-weight:700">Banquillo</span> = candidata en seguimiento'
        f' &nbsp;|&nbsp; '
        f'<span style="color:#9A3412;font-weight:700">Cantera</span> = en análisis preliminar'
        f' &nbsp;|&nbsp; '
        f'<span style="color:#94A3B8;font-weight:700">No info</span> = sin clasificar'
    )

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F1F5F9">

<table role="presentation" width="900" cellpadding="0" cellspacing="0" border="0"
       align="center" style="border-collapse:collapse;font-family:{FONT};
       background:#ffffff;margin:20px auto">

<!-- CABECERA -->
<tr>
  <td bgcolor="{NAVY}" style="padding:20px 28px;border-bottom:4px solid {BLUE}">
    <div style="font-size:10px;font-weight:700;color:#93C5FD;letter-spacing:2px;
                text-transform:uppercase;font-family:{FONT};margin-bottom:6px">
      SEGUIMIENTO SEMANAL
    </div>
    <div style="font-size:22px;font-weight:700;color:#FFFFFF;font-family:{FONT}">
      Estrategia Dividendo Creciente &mdash; M&eacute;todo Geraldine Weiss
    </div>
    <div style="font-size:11px;color:#93C5FD;margin-top:5px;font-family:{FONT}">
      Precios al &uacute;ltimo cierre &nbsp;&middot;&nbsp; Emitido {fecha_str}
    </div>
  </td>
</tr>

<!-- KPIs -->
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

<!-- SEÑALES + DETALLE -->
<tr>
  <td style="padding:0 28px 20px 28px">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">
      {bloque_senales(infrav, "Infravaloradas", "#166534")}
      {bloque_senales(sobrev, "Sobrevaloradas", "#991B1B")}
      {bloque_detalle(todos_s)}
      {bloque_hall_of_fame(hof)}
    </table>
  </td>
</tr>

<!-- PIE -->
<tr>
  <td style="padding:16px 28px 24px 28px;border-top:1px solid #E2E8F0">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse">
      <tr>
        <td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8;padding-bottom:6px">
          <span style="color:#1B3A5C;font-weight:700">Estado</span>
          &nbsp;seg&uacute;n la posici&oacute;n del precio frente a las l&iacute;neas de valoraci&oacute;n:
          &nbsp;<span style="color:#166534;font-weight:700">Infraval.</span> = debajo del canal inferior &nbsp;|&nbsp;
          <span style="color:#15803D;font-weight:700">Cerca infraval.</span> = entre canal inferior y 40% del rango &nbsp;|&nbsp;
          <span style="color:#64748B;font-weight:700">Neutral</span> = zona central &nbsp;|&nbsp;
          <span style="color:#92400E;font-weight:700">Cerca sobreval.</span> = entre 70% del rango y canal superior &nbsp;|&nbsp;
          <span style="color:#991B1B;font-weight:700">Sobreval.</span> = encima del canal superior
        </td>
      </tr>
      <tr>
        <td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8;padding-bottom:6px">
          <span style="color:#1B3A5C;font-weight:700">Rating</span>
          &nbsp;posici&oacute;n del precio en la banda (0% = canal infraval., 100% = canal sobreval.).
          Negativo = precio por debajo del canal inferior.
          &nbsp;&nbsp;
          <span style="color:#1B3A5C;font-weight:700">Upside</span>
          &nbsp;recorrido potencial hasta la l&iacute;nea de sobrevaloración.
        </td>
      </tr>
      <tr>
        <td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8;padding-bottom:6px">
          <span style="color:#1B3A5C;font-weight:700">Infravaloración / Sobrevaloración</span>
          &nbsp;precio te&oacute;rico en DPS / percentil&nbsp;95% y DPS / percentil&nbsp;5%
          del yield hist&oacute;rico.
        </td>
      </tr>
      <tr>
        <td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8;padding-bottom:6px">
          <span style="color:#1B3A5C;font-weight:700">Tipo</span>:
          &nbsp;{leyenda_tipo}
        </td>
      </tr>
      <tr>
        <td style="font-size:10px;color:#64748B;font-family:{FONT};line-height:1.8">
          <span style="color:#1B3A5C;font-weight:700">Precios</span>
          &nbsp;al cierre del mercado en EE.UU., salvo indicaci&oacute;n de otra moneda
          (&euro; Europa, C$ Canad&aacute;, p peniques Londres).
          Valoraci&oacute;n Geraldine Weiss.
          &nbsp;&middot;&nbsp;
          Documento de seguimiento interno; no constituye recomendaci&oacute;n de inversi&oacute;n.
          &nbsp;&middot;&nbsp;
          <a href="{GH_PAGES_URL}/index.html"
             style="color:{BLUE};text-decoration:underline">
            Ver gr&aacute;ficos completos &rarr;
          </a>
        </td>
      </tr>
    </table>
  </td>
</tr>

</table>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
#  Envío Gmail
# ══════════════════════════════════════════════════════════════════════════════
def enviar_gmail(html, fecha_str, gmail_user, gmail_pass, destinatarios):
    msg             = MIMEMultipart("alternative")
    msg["Subject"]  = f"{ASUNTO} — {fecha_str}"
    msg["From"]     = gmail_user
    msg["To"]       = ", ".join(destinatarios)
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(gmail_user, gmail_pass)
        s.sendmail(gmail_user, destinatarios, msg.as_string())
    print(f"  ✓  Newsletter enviada a: {', '.join(destinatarios)}")


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",        default=DB_PATH)
    parser.add_argument("--csv",       default=CSV_PATH)
    parser.add_argument("--solo-html", action="store_true")
    parser.add_argument("--out",       default="./newsletter_preview.html")
    args = parser.parse_args()

    fecha_str = datetime.today().strftime("%d/%m/%Y")
    sep = "─" * 60
    print(f"\n{sep}\n  Newsletter GW · {fecha_str}\n{sep}\n")

    # ── Paso 1: Leer datos ────────────────────────────────────────────────────
    print("[ PASO 1 ] Leyendo datos…\n")
    maestro = leer_maestro(args.csv)
    db_data = leer_db(args.db)

    # Tickers sin datos GW en DB: solo incluir los que están en TICKERS_SIN_DIV
    # (empresas sin dividendo como BRK/B que queremos mostrar con precio solamente)
    TICKERS_SIN_DIV = {"BRK/B US"}
    for tk in TICKERS_SIN_DIV:
        if tk not in db_data:
            precio = descargar_precio_actual(tk)
            db_data[tk] = {
                "ultima_fecha":   None,
                "precio":         precio,
                "dps_db":         None,
                "yield_pct":      None,
                "rating":         None,
                "upside":         None,
                "canal_inferior": None,
                "canal_mediana":  None,
                "canal_superior": None,
                "max_drawdown":   None,
            }
            p_str = f"${precio:.2f}" if precio else "N/D"
            print(f"  BRK/B US (sin dividendo)  precio={p_str}")

    # Solo los tickers con datos en DB (+ los explícitos de TICKERS_SIN_DIV)
    tickers = sorted(db_data.keys())
    print(f"\n  Maestro: {len(maestro)} · Total en newsletter: {len(tickers)}")

    # ── Paso 2: Rendimiento semanal ───────────────────────────────────────────
    print(f"\n[ PASO 2 ] Descargando rendimiento semanal…\n")
    rendimientos = descargar_rendimiento_semanal(tickers)
    ok = sum(1 for v in rendimientos.values() if v is not None)
    print(f"  OK: {ok}/{len(tickers)}")

    # ── Paso 3: Ensamblar ─────────────────────────────────────────────────────
    print(f"\n[ PASO 3 ] Ensamblando…\n")
    todos = []
    for tk in tickers:
        meta = maestro.get(tk, {"nombre": tk, "sector": "-", "indice": "-", "tipo": "No info"})
        if not meta.get("nombre"):
            meta["nombre"] = tk
        todos.append({
            "ticker": tk,
            "meta":   meta,
            "db":     db_data[tk],
            "semana": rendimientos.get(tk),
        })

    con_datos = [f for f in todos if f["db"]["rating"] is not None]
    n_i = sum(1 for f in con_datos if (f["db"]["rating"] or 1) < RATING_INFRAVAL)
    n_s = sum(1 for f in con_datos if (f["db"]["rating"] or 0) >= RATING_SOBREVAL)
    print(f"  Infraval: {n_i}  Sobreval: {n_s}  Sin datos GW: {len(todos) - len(con_datos)}")

    html = generar_html(todos, fecha_str)

    if args.solo_html:
        Path(args.out).write_text(html, encoding="utf-8")
        print(f"\n  ✓  HTML guardado en: {args.out}\n")
    else:
        gmail_user = os.environ.get("GMAIL_USER", "")
        gmail_pass = os.environ.get("GMAIL_APP_PASS", "")
        destinos   = [d.strip() for d in os.environ.get("DESTINATARIOS", "").split(",") if d.strip()]
        if not gmail_user or not gmail_pass:
            print("  ERROR: GMAIL_USER y GMAIL_APP_PASS son necesarios.\n")
            return
        if not destinos:
            print("  ERROR: DESTINATARIOS no configurado.\n")
            return
        print(f"\n[ PASO 4 ] Enviando email…\n")
        enviar_gmail(html, fecha_str, gmail_user, gmail_pass, destinos)

    print(f"\n{sep}\n  Completado: {fecha_str}\n{sep}\n")


if __name__ == "__main__":
    main()