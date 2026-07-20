"""
carga_inicial.py
═══════════════════════════════════════════════════════════════════════════════
Ejecutar UNA SOLA VEZ para poblar la base de datos con el histórico completo
de precios y dividendos desde los CSVs exportados del modelo Excel.

Después de esto, usar actualizar.py para el día a día.

Uso:
    python carga_inicial.py [--repo RUTA] [--db RUTA]

    --repo  Carpeta con los CSVs  (default: ./REPOSITORIO_GRAFICOS)
    --db    Fichero SQLite        (default: ./data/graficos.db)
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

# ── Posición de columnas en el CSV (0-indexed) ────────────────────────────────
COL_DATE   = 19
COL_PRICE  = 31
COL_ANIO   = 47
COL_DIV    = 48
DATA_START  = 6
ENCODING    = "cp1250"


# ══════════════════════════════════════════════════════════════════════════════
#  DDL
# ══════════════════════════════════════════════════════════════════════════════
DDL_TABLAS = """
CREATE TABLE IF NOT EXISTS precios (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT    NOT NULL,
    fecha   DATE    NOT NULL,
    precio  REAL    NOT NULL,
    UNIQUE (ticker, fecha)
);
CREATE TABLE IF NOT EXISTS dividendos (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT    NOT NULL,
    anio    INTEGER NOT NULL,
    dps     REAL    NOT NULL,
    UNIQUE (ticker, anio)
);
CREATE TABLE IF NOT EXISTS umbrales_yield (
    ticker            TEXT PRIMARY KEY,
    yield_overvalued  REAL,
    yield_undervalued REAL,
    yield_mediana     REAL,
    yield_medio       REAL
);
CREATE INDEX IF NOT EXISTS idx_precios_ticker_fecha   ON precios   (ticker, fecha);
CREATE INDEX IF NOT EXISTS idx_dividendos_ticker_anio ON dividendos (ticker, anio);
"""

DDL_VISTAS = """
DROP VIEW IF EXISTS yields_base;
CREATE VIEW yields_base AS
SELECT p.ticker, p.fecha,
    CAST(strftime('%Y', p.fecha) AS INTEGER) AS anio,
    p.precio, d.dps,
    CAST(d.dps AS REAL) / NULLIF(p.precio, 0) AS yield
FROM precios p
JOIN dividendos d
    ON  p.ticker = d.ticker
    AND CAST(strftime('%Y', p.fecha) AS INTEGER) = d.anio
WHERE d.dps > 0;

DROP VIEW IF EXISTS metricas_diarias;
CREATE VIEW metricas_diarias AS
SELECT b.ticker, b.fecha, b.anio, b.precio, b.dps, b.yield,
    b.dps / NULLIF(u.yield_overvalued,  0) AS canal_superior,
    b.dps / NULLIF(u.yield_undervalued, 0) AS canal_inferior,
    b.dps / NULLIF(u.yield_mediana,     0) AS canal_mediana,
    CASE
        WHEN (b.dps/NULLIF(u.yield_overvalued,0) - b.dps/NULLIF(u.yield_undervalued,0)) <> 0
        THEN (b.precio - b.dps/NULLIF(u.yield_undervalued,0))
           / (b.dps/NULLIF(u.yield_overvalued,0) - b.dps/NULLIF(u.yield_undervalued,0))
        ELSE NULL
    END AS rating,
    b.dps / NULLIF(u.yield_overvalued, 0) / NULLIF(b.precio, 0) - 1 AS upside,
    u.yield_overvalued, u.yield_undervalued, u.yield_mediana, u.yield_medio,
    b.precio / NULLIF(
        MAX(b.precio) OVER (
            PARTITION BY b.ticker ORDER BY b.fecha
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ), 0
    ) - 1 AS max_drawdown
FROM yields_base b
JOIN umbrales_yield u ON b.ticker = u.ticker;

DROP VIEW IF EXISTS metricas_completas;
CREATE VIEW metricas_completas AS
SELECT m.*,
    COALESCE((
        SELECT SUM(d2.dps) FROM dividendos d2
        WHERE d2.ticker = m.ticker AND d2.dps > 0 AND d2.anio < m.anio
    ), 0) AS dps_acumulado
FROM metricas_diarias m;

DROP VIEW IF EXISTS resumen_tickers;
CREATE VIEW resumen_tickers AS
WITH ultima AS (SELECT ticker, MAX(fecha) AS fecha_max FROM metricas_completas GROUP BY ticker)
SELECT mc.ticker, mc.fecha AS ultima_fecha,
    mc.precio AS precio_actual, mc.dps AS dps_actual,
    ROUND(mc.yield          * 100, 2) AS yield_pct,
    ROUND(mc.rating         * 100, 1) AS rating_pct,
    ROUND(mc.upside         * 100, 1) AS upside_pct,
    ROUND(mc.canal_inferior,       2) AS canal_inferior,
    ROUND(mc.canal_mediana,        2) AS canal_mediana,
    ROUND(mc.canal_superior,       2) AS canal_superior,
    ROUND(mc.yield_overvalued  * 100, 2) AS yield_overvalued_pct,
    ROUND(mc.yield_mediana     * 100, 2) AS yield_mediana_pct,
    ROUND(mc.yield_undervalued * 100, 2) AS yield_undervalued_pct,
    ROUND(mc.max_drawdown      * 100, 2) AS max_drawdown_pct
FROM metricas_completas mc
JOIN ultima ON mc.ticker = ultima.ticker AND mc.fecha = ultima.fecha_max;
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Percentil (equivale a PERCENTILE.INC de Excel)
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
#  Limpieza CSV
# ══════════════════════════════════════════════════════════════════════════════
def limpiar_precio(raw):
    s = raw.strip().replace("$", "").replace(",", "").replace(" ", "")
    try:
        v = float(s); return v if v > 0 else None
    except: return None

def limpiar_fecha(raw):
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try: return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except: continue
    return None

def limpiar_anio(raw):
    try: return int(float(raw.strip()))
    except: return None

def limpiar_dps(raw):
    try: return float(raw.strip().replace(",", "."))
    except: return None

def ticker_desde_nombre(path):
    return path.stem.replace("_", " ")


# ══════════════════════════════════════════════════════════════════════════════
#  Lectura CSV
# ══════════════════════════════════════════════════════════════════════════════
def leer_csv(path):
    """
    Lee precios y dividendos del CSV historico.
    El año en curso se excluye de dividendos: el valor del CSV es parcial
    y lo sobreescribira actualizar.py con el valor anualizado desde Yahoo.
    """
    from datetime import datetime
    anio_actual = datetime.now().year
    ticker = ticker_desde_nombre(path)
    precios, dividendos, visto = [], [], set()
    text = path.read_bytes().decode(ENCODING, errors="replace")
    for idx, line in enumerate(text.splitlines()):
        if idx < DATA_START: continue
        cols = line.split(",")
        if len(cols) > COL_PRICE:
            f = limpiar_fecha(cols[COL_DATE] if len(cols) > COL_DATE else "")
            p = limpiar_precio(cols[COL_PRICE])
            if f and p: precios.append((ticker, f, p))
        if len(cols) > COL_DIV:
            a = limpiar_anio(cols[COL_ANIO])
            d = limpiar_dps(cols[COL_DIV])
            if a and d is not None and a not in visto and a < anio_actual:
                visto.add(a); dividendos.append((ticker, a, d))
    return precios, dividendos


# ══════════════════════════════════════════════════════════════════════════════
#  Base de datos
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
#  Main
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="./REPOSITORIO_GRAFICOS")
    parser.add_argument("--db",   default="./data/graficos.db")
    args = parser.parse_args()

    repo = Path(args.repo)
    if not repo.exists():
        print(f"ERROR: '{repo}' no existe."); return
    csvs = sorted(repo.glob("*.csv"))
    if not csvs:
        print(f"ERROR: No hay CSVs en '{repo}'."); return

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    sep = "─" * 60
    print(f"\n{sep}")
    print(f"  Repositorio : {repo.resolve()}")
    print(f"  Base datos  : {Path(args.db).resolve()}")
    print(f"  Ficheros CSV: {len(csvs)}")
    print(f"{sep}\n")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(DDL_TABLAS)
    conn.commit()

    errores = []
    print("[ PASO 1 ] Cargando histórico desde CSVs…\n")
    for csv_path in csvs:
        ticker = ticker_desde_nombre(csv_path)
        try:
            precios, dividendos = leer_csv(csv_path)
            cur = conn.cursor()
            cur.executemany("INSERT OR IGNORE INTO precios    VALUES (NULL,?,?,?)", precios)
            cur.executemany("INSERT OR IGNORE INTO dividendos VALUES (NULL,?,?,?)", dividendos)
            conn.commit()
            p = conn.execute("SELECT COUNT(*) FROM precios    WHERE ticker=?", (ticker,)).fetchone()[0]
            d = conn.execute("SELECT COUNT(*) FROM dividendos WHERE ticker=?", (ticker,)).fetchone()[0]
            print(f"  ✓  {csv_path.name:<28}  precios={len(precios):>4}  divs={len(dividendos):>2}  (DB: {p}/{d})")
        except Exception as e:
            errores.append((csv_path.name, str(e)))
            print(f"  ✗  {csv_path.name:<28}  ERROR: {e}")

    print(f"\n[ PASO 2 ] Calculando umbrales de yield (percentiles)…\n")
    calcular_umbrales(conn)
    rows = conn.execute("SELECT ticker, yield_overvalued, yield_undervalued, yield_mediana FROM umbrales_yield ORDER BY ticker").fetchall()
    for r in rows:
        print(f"  {r[0]:<20}  over={r[1]:.4%}  under={r[2]:.4%}  med={r[3]:.4%}")

    print(f"\n[ PASO 3 ] Creando vistas SQL…\n")
    conn.executescript(DDL_VISTAS)
    conn.commit()
    for v in ["yields_base", "metricas_diarias", "metricas_completas", "resumen_tickers"]:
        n = conn.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
        print(f"  ✓  {v:<30} {n:>6} filas")

    conn.close()

    print(f"\n{sep}")
    print(f"  Carga inicial completada.")
    print(f"  Tickers OK : {len(csvs) - len(errores)}")
    print(f"  Errores    : {len(errores)}")
    if errores:
        for n, m in errores: print(f"    ✗ {n}: {m}")
    print(f"\n  → Ahora ejecuta: python actualizar.py")
    print(f"{sep}\n")


if __name__ == "__main__":
    main()