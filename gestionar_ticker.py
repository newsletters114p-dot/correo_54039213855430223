"""
gestionar_ticker.py
═══════════════════════════════════════════════════════════════════════════════
Gestiona la lista de tickers en seguimiento.
Ejecutado por el workflow gestionar_ticker.yml via GitHub Actions.

Variables de entorno:
    ACCION  → añadir | eliminar
    TICKER  → ej: NEE US
    NOMBRE  → ej: NextEra Energy Inc
    SECTOR  → ej: Utilities
    TIPO    → ej: Titular
"""

import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf

# ══════════════════════════════════════════════════════════════════════════════
#  Configuración
# ══════════════════════════════════════════════════════════════════════════════
CSV_ACTIVOS  = "./tickers_activos.csv"
REPO_GRAFICOS = "./REPOSITORIO_GRAFICOS"

COL_DATE   = 19
COL_PRICE  = 31
COL_ANIO   = 47
COL_DIV    = 48
DATA_START  = 6

SUFIJOS_YAHOO = {
    "US": "", "CN": ".TO", "NA": ".AS", "LN": ".L", "GY": ".DE",
    "FP": ".PA", "PA": ".PA", "SM": ".MC", "MC": ".MC", "HK": ".HK",
    "AU": ".AX", "JP": ".T", "SW": ".SW", "IT": ".MI", "BB": ".BR",
    "SE": ".ST", "DC": ".CO", "NO": ".OL", "FH": ".HE",
}

# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════
def normalizar_ticker(tk):
    """Mayúsculas y espacio limpio."""
    return " ".join(tk.strip().upper().split())


def ticker_a_yahoo(tk):
    partes = tk.strip().upper().split()
    if len(partes) < 2:
        return tk
    simbolo = partes[0].replace("/", "-")
    mercado = partes[-1]
    sufijo  = SUFIJOS_YAHOO.get(mercado, f".{mercado}")
    if mercado == "HK" and simbolo.isdigit():
        simbolo = simbolo.zfill(4)
    return simbolo + sufijo


def csv_path_para_ticker(tk):
    """Devuelve la ruta del CSV en REPOSITORIO_GRAFICOS si existe."""
    repo = Path(REPO_GRAFICOS)
    # Intentar variantes: NEE_US.csv, NEE US.csv, nee_us.csv
    base = tk.replace(" ", "_")
    variantes = [
        base + ".csv",
        base.lower() + ".csv",
        tk + ".csv",
        tk.lower() + ".csv",
    ]
    for v in variantes:
        p = repo / v
        if p.exists():
            return p
    return None


def generar_csv_desde_yahoo(tk, output_path):
    """Genera CSV compatible con carga_inicial.py desde Yahoo Finance."""
    yahoo = ticker_a_yahoo(tk)
    print(f"  Descargando histórico desde Yahoo: {yahoo}")
    t    = yf.Ticker(yahoo)
    hist = t.history(period="max", auto_adjust=True)
    divs = t.dividends

    if hist.empty:
        print(f"  ✗  No hay datos de precio para {yahoo}")
        return False

    anio_actual = datetime.now().year
    por_anio = {}
    for f, v in divs.items():
        y = f.year
        if y < anio_actual:
            por_anio.setdefault(y, []).append(float(v))
    dps_anual = {a: round(sum(v), 4) for a, v in por_anio.items()}

    precios = []
    for fecha, row in hist.iterrows():
        f = fecha.strftime("%m/%d/%Y")
        p = round(float(row["Close"]), 4)
        if p > 0:
            precios.append((f, p))

    lines = []
    for _ in range(DATA_START):
        lines.append(",".join([""] * 49))

    anios_procesados = set()
    for fecha_str, precio in precios:
        anio = int(fecha_str.split("/")[2])
        cols = [""] * 49
        cols[COL_DATE]  = fecha_str
        cols[COL_PRICE] = str(precio)
        if anio in dps_anual and anio not in anios_procesados:
            cols[COL_ANIO] = str(anio)
            cols[COL_DIV]  = str(dps_anual[anio])
            anios_procesados.add(anio)
        lines.append(",".join(cols))

    Path(output_path).write_text("\n".join(lines), encoding="cp1250")
    print(f"  ✓  CSV generado: {output_path} ({len(precios)} precios, {len(dps_anual)} años dividendos)")
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  Leer y escribir tickers_activos.csv
# ══════════════════════════════════════════════════════════════════════════════
def leer_activos():
    if not Path(CSV_ACTIVOS).exists():
        return [], ["ticker", "sector", "tipo"]
    with open(CSV_ACTIVOS, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), reader.fieldnames


def escribir_activos(filas, fieldnames):
    with open(CSV_ACTIVOS, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filas)


# ══════════════════════════════════════════════════════════════════════════════
#  Acciones
# ══════════════════════════════════════════════════════════════════════════════
def accion_añadir(ticker, nombre, sector, tipo):
    print(f"\n{'='*60}")
    print(f"  AÑADIR: {ticker}")
    print(f"  Nombre: {nombre}  Sector: {sector}  Tipo: {tipo}")
    print(f"{'='*60}\n")

    filas, fieldnames = leer_activos()
    existe = any(r["ticker"].strip() == ticker for r in filas)

    if existe:
        # Sobreescribir sector y tipo
        for r in filas:
            if r["ticker"].strip() == ticker:
                r["sector"] = sector
                r["tipo"]   = tipo
                print(f"  ↺  Ticker ya existía — sector/tipo actualizados")
                break
    else:
        filas.append({"ticker": ticker, "sector": sector, "tipo": tipo})
        print(f"  ✓  Ticker añadido a tickers_activos.csv")

    escribir_activos(filas, fieldnames)

    # Buscar o generar CSV histórico
    csv_existente = csv_path_para_ticker(ticker)
    if csv_existente:
        print(f"  ✓  CSV histórico encontrado: {csv_existente.name}")
    else:
        print(f"  ⚠  CSV no encontrado en REPOSITORIO_GRAFICOS — generando desde Yahoo…")
        Path(REPO_GRAFICOS).mkdir(exist_ok=True)
        nuevo_csv = Path(REPO_GRAFICOS) / f"{ticker.replace(' ', '_')}.csv"
        ok = generar_csv_desde_yahoo(ticker, nuevo_csv)
        if not ok:
            print(f"  ✗  No se pudo generar el CSV para {ticker}")
            print(f"     El ticker se añadió a tickers_activos.csv pero sin datos históricos")
            return

    # Carga inicial y actualización
    print(f"\n  Ejecutando carga_inicial.py…")
    subprocess.run([sys.executable, "carga_inicial.py"], check=True)

    print(f"\n  Ejecutando actualizar.py…")
    subprocess.run([sys.executable, "actualizar.py"], check=True)

    print(f"\n  ✓  {ticker} añadido y procesado correctamente")


def accion_eliminar(ticker):
    print(f"\n{'='*60}")
    print(f"  ELIMINAR: {ticker}")
    print(f"{'='*60}\n")

    filas, fieldnames = leer_activos()
    antes = len(filas)
    filas = [r for r in filas if r["ticker"].strip() != ticker]

    if len(filas) == antes:
        print(f"  ⚠  Ticker '{ticker}' no encontrado en tickers_activos.csv")
        print(f"     No hay nada que eliminar")
        return

    escribir_activos(filas, fieldnames)
    print(f"  ✓  Ticker eliminado de tickers_activos.csv")

    # Regenerar HTML sin el ticker
    print(f"\n  Ejecutando actualizar.py…")
    subprocess.run([sys.executable, "actualizar.py"], check=True)

    print(f"\n  ✓  {ticker} eliminado y HTML actualizado")


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    accion = os.environ.get("ACCION", "").strip().lower()
    ticker = normalizar_ticker(os.environ.get("TICKER", ""))
    nombre = os.environ.get("NOMBRE", "").strip()
    sector = os.environ.get("SECTOR", "").strip()
    tipo   = os.environ.get("TIPO",   "").strip()

    if not ticker:
        print("ERROR: TICKER es obligatorio")
        sys.exit(1)

    if accion == "añadir":
        if not sector or not tipo:
            print("ERROR: sector y tipo son obligatorios para añadir")
            sys.exit(1)
        accion_añadir(ticker, nombre, sector, tipo)

    elif accion == "eliminar":
        accion_eliminar(ticker)

    else:
        print(f"ERROR: accion '{accion}' no reconocida. Usa 'añadir' o 'eliminar'")
        sys.exit(1)


if __name__ == "__main__":
    main()
