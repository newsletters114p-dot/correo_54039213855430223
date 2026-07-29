"""
gestionar_ticker.py  v3
═══════════════════════════════════════════════════════════════════════════════
Gestiona hasta 2 operaciones (añadir/eliminar) en una sola ejecución.
Al final:
  - Añade el ticker a tickers_maestro.csv si no está
  - Ejecuta carga_inicial.py + actualizar.py
  - Genera preview.html
"""

import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

CSV_ACTIVOS   = "./tickers_activos.csv"
CSV_MAESTRO   = "./tickers_maestro.csv"
REPO_GRAFICOS = "./REPOSITORIO_GRAFICOS"

COL_DATE  = 19
COL_PRICE = 31
COL_ANIO  = 47
COL_DIV   = 48
DATA_START = 6

SUFIJOS_YAHOO = {
    "US": "", "CN": ".TO", "NA": ".AS", "LN": ".L", "GY": ".DE",
    "FP": ".PA", "PA": ".PA", "SM": ".MC", "MC": ".MC", "HK": ".HK",
    "AU": ".AX", "JP": ".T", "SW": ".SW", "IT": ".MI", "BB": ".BR",
    "SE": ".ST", "DC": ".CO", "NO": ".OL", "FH": ".HE",
}

def normalizar(tk):
    return " ".join(tk.strip().upper().split())

def ticker_a_yahoo(tk):
    partes = tk.strip().upper().split()
    if len(partes) < 2: return tk
    simbolo = partes[0].replace("/", "-")
    mercado = partes[-1]
    sufijo  = SUFIJOS_YAHOO.get(mercado, f".{mercado}")
    if mercado == "HK" and simbolo.isdigit(): simbolo = simbolo.zfill(4)
    return simbolo + sufijo

def csv_path_para_ticker(tk):
    repo = Path(REPO_GRAFICOS)
    base = tk.replace(" ", "_")
    for v in [base + ".csv", base.lower() + ".csv", tk + ".csv"]:
        p = repo / v
        if p.exists(): return p
    return None

def generar_csv_desde_yahoo(tk, output_path):
    yahoo = ticker_a_yahoo(tk)
    print(f"  Descargando histórico: {yahoo}")
    t    = yf.Ticker(yahoo)
    hist = t.history(period="max", auto_adjust=True)
    divs = t.dividends
    if hist.empty:
        print(f"  ✗  Sin datos para {yahoo}")
        return False
    anio_actual = datetime.now().year
    por_anio = {}
    for f, v in divs.items():
        if f.year < anio_actual:
            por_anio.setdefault(f.year, []).append(float(v))
    dps = {a: round(sum(v), 4) for a, v in por_anio.items()}
    lines = [",".join([""] * 49) for _ in range(DATA_START)]
    visto = set()
    for fecha, row in hist.iterrows():
        f = fecha.strftime("%m/%d/%Y")
        p = round(float(row["Close"]), 4)
        if p <= 0: continue
        a = int(f.split("/")[2])
        cols = [""] * 49
        cols[COL_DATE] = f; cols[COL_PRICE] = str(p)
        if a in dps and a not in visto:
            cols[COL_ANIO] = str(a); cols[COL_DIV] = str(dps[a]); visto.add(a)
        lines.append(",".join(cols))
    Path(output_path).write_text("\n".join(lines), encoding="cp1250")
    print(f"  ✓  CSV: {len(lines)-DATA_START} precios, {len(dps)} años dividendos")
    return True

def leer_activos():
    if not Path(CSV_ACTIVOS).exists():
        return [], ["ticker", "sector", "tipo"]
    with open(CSV_ACTIVOS, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames)

def escribir_activos(filas, fieldnames):
    with open(CSV_ACTIVOS, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filas)

def actualizar_maestro(ticker, nombre, sector):
    """Añade el ticker a tickers_maestro.csv si no está."""
    if not Path(CSV_MAESTRO).exists():
        return
    with open(CSV_MAESTRO, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        filas = list(reader)

    existe = any(r.get("ticker", "").strip() == ticker for r in filas)
    if not existe and nombre:
        filas.append({
            "ticker": ticker,
            "nombre": nombre,
            "sector": sector,
            "indice": "-",
            "tipo":   "No info",
        })
        with open(CSV_MAESTRO, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(filas)
        print(f"  ✓  Añadido a tickers_maestro.csv: {ticker} — {nombre}")
    else:
        print(f"  –  Ya existe en tickers_maestro.csv: {ticker}")

def op_añadir(ticker, nombre, sector, tipo):
    print(f"\n  ── AÑADIR: {ticker} | {sector} | {tipo}")
    filas, fieldnames = leer_activos()
    existe = any(r["ticker"].strip() == ticker for r in filas)
    if existe:
        for r in filas:
            if r["ticker"].strip() == ticker:
                r["sector"] = sector
                r["tipo"]   = tipo
        print(f"  ↺  Ya existía — sector/tipo actualizados")
    else:
        filas.append({"ticker": ticker, "sector": sector, "tipo": tipo})
        print(f"  ✓  Añadido a tickers_activos.csv")
    escribir_activos(filas, fieldnames)

    csv_existente = csv_path_para_ticker(ticker)
    if csv_existente:
        print(f"  ✓  CSV histórico encontrado: {csv_existente.name}")
    else:
        print(f"  ⚠  CSV no encontrado — generando desde Yahoo…")
        Path(REPO_GRAFICOS).mkdir(exist_ok=True)
        nuevo_csv = Path(REPO_GRAFICOS) / f"{ticker.replace(' ', '_')}.csv"
        if not generar_csv_desde_yahoo(ticker, nuevo_csv):
            print(f"  ✗  No se pudo generar el CSV para {ticker}")
            return False

    actualizar_maestro(ticker, nombre, sector)
    return True

def op_eliminar(ticker):
    print(f"\n  ── ELIMINAR: {ticker}")
    filas, fieldnames = leer_activos()
    antes = len(filas)
    filas = [r for r in filas if r["ticker"].strip() != ticker]
    if len(filas) == antes:
        print(f"  ⚠  '{ticker}' no encontrado — nada que hacer")
        return False
    escribir_activos(filas, fieldnames)
    print(f"  ✓  Eliminado de tickers_activos.csv")
    return True

def main():
    ops = []
    for i in [1, 2]:
        accion = os.environ.get(f"ACCION_{i}", "ninguna").strip().lower()
        ticker = normalizar(os.environ.get(f"TICKER_{i}", ""))
        nombre = os.environ.get(f"NOMBRE_{i}", "").strip()
        sector = os.environ.get(f"SECTOR_{i}", "-").strip()
        tipo   = os.environ.get(f"TIPO_{i}",   "-").strip()
        if accion != "ninguna" and ticker:
            ops.append((accion, ticker, nombre, sector, tipo))

    if not ops:
        print("  Sin operaciones que procesar.")
        return

    sep = "═" * 60
    print(f"\n{sep}\n  Gestionar ticker v3 — {len(ops)} operación(es)\n{sep}")

    hay_cambios   = False
    necesita_carga = False

    for accion, ticker, nombre, sector, tipo in ops:
        if accion == "añadir":
            ok = op_añadir(ticker, nombre, sector, tipo)
            if ok:
                hay_cambios    = True
                necesita_carga = True
        elif accion == "eliminar":
            ok = op_eliminar(ticker)
            if ok:
                hay_cambios = True

    if not hay_cambios:
        print(f"\n  Sin cambios efectivos.")
        return

    if necesita_carga:
        print(f"\n  Ejecutando carga_inicial.py…")
        subprocess.run([sys.executable, "carga_inicial.py"], check=True)

    print(f"\n  Ejecutando actualizar.py…")
    subprocess.run([sys.executable, "actualizar.py"], check=True)

    print(f"\n  Generando preview.html…")
    subprocess.run([sys.executable, "generar_newsletter.py",
                    "--solo-html", "--out", "preview.html"], check=True)
    print(f"  ✓  preview.html generado — refleja exactamente lo que saldrá en el correo")

    print(f"\n{sep}\n  Completado\n{sep}")

if __name__ == "__main__":
    main()
