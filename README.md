# Auto-Geraldine-Weiss

Herramienta de valoración de acciones de dividendo basada en la metodología de **Geraldine Weiss**, publicada automáticamente en GitHub Pages y actualizada cada noche sin intervención manual.

**→ [Ver el gráfico en vivo](https://newsletters114p-dot.github.io/correo_54039213855430223/)**

---

## ¿Por qué el repo es público?

Este repositorio es público por una razón técnica concreta: **GitHub Pages gratuito solo funciona con repos públicos**.

GitHub Pages es el servicio que publica el HTML del gráfico en una URL accesible para cualquier persona. Con un repo privado, GitHub exige un plan de pago (Team o superior) para activar Pages. Durante el desarrollo se probó Netlify como alternativa gratuita para repos privados, pero su sistema de caché en el plan gratuito causó problemas persistentes — el HTML actualizado no se reflejaba en la URL pública a pesar de que el fichero en el repo era correcto. Tras varias iteraciones sin solución, se tomó la decisión de hacer el repo público y usar GitHub Pages directamente.

**¿Qué implica que sea público?**
- El código fuente (scripts Python, workflows) es visible para cualquiera
- El HTML generado con los datos de valoración es visible para cualquiera
- Los datos que contiene son **precios históricos y dividendos públicos** obtenidos de Yahoo Finance — no hay información confidencial ni propietaria en el repo
- Las credenciales de Gmail y los destinatarios de la newsletter están almacenados como **GitHub Secrets** (encriptados) y nunca aparecen en el código

---

## ¿Qué hace este proyecto?

Toma el histórico de precios y dividendos de una lista de empresas, calcula los canales de valoración Geraldine Weiss y los presenta en un gráfico interactivo. Cada semana envía automáticamente una newsletter por email con el resumen de señales de compra y venta.

---

## Arquitectura

```
FUENTES DE DATOS
│
├── CSVs históricos (REPOSITORIO_GRAFICOS/)   ← carga única inicial, solo local
│   Exportaciones del modelo Excel
│   Nombre del fichero = ticker: ENB_CN.csv → "ENB CN"
│
└── Yahoo Finance (yfinance)                  ← actualización diaria automática
    Precios de cierre ajustados
    Dividendos → anualizados → DPS anual corregido

        │
        ▼
SQLite (data/graficos.db)

  TABLAS BASE:
  ┌──────────┐   ┌─────────────┐   ┌─────────────────┐
  │ precios  │   │ dividendos  │   │ umbrales_yield  │
  │──────────│   │─────────────│   │─────────────────│
  │ ticker   │   │ ticker      │   │ ticker          │
  │ fecha    │   │ anio        │   │ yield_over (5%) │
  │ precio   │   │ dps         │   │ yield_under(95%)│
  └──────────┘   └─────────────┘   │ yield_mediana   │
                                   └─────────────────┘

  VISTAS SQL:
  yields_base → metricas_diarias → metricas_completas → resumen_tickers

        │
        ▼
docs/index.html  ← HTML estático con todos los datos embebidos
                    Publicado por GitHub Pages
                    URL: https://newsletters114p-dot.github.io/correo_54039213855430223/
```

---

## Lógica de valoración (Geraldine Weiss)

Los únicos inputs son **precios históricos** y **dividendos anuales (DPS)**. Todo lo demás se calcula:

```
yield_diario     = DPS_anual / Precio_diario

canal_superior   = DPS / percentil_5%(yield histórico)   → precio "caro"
canal_mediana    = DPS / percentil_50%(yield histórico)  → precio "justo"
canal_inferior   = DPS / percentil_95%(yield histórico)  → precio "barato"

rating [0-1]     = (precio - canal_inferior) / (canal_superior - canal_inferior)
upside           = canal_superior / precio - 1
```

**Nota sobre el DPS del año en curso:** Yahoo Finance solo devuelve los pagos realizados hasta la fecha. Para evitar que el año en curso aparezca con el dividendo parcial (distorsionando los canales), el script detecta la frecuencia histórica de pago de cada empresa y extrapola al año completo. Adicionalmente, el DPS de cualquier año nunca puede ser menor que el del año anterior — si la extrapolación da un valor inferior, se usa el del año anterior. Los CSVs históricos excluyen el año en curso por el mismo motivo.

**Frecuencias fijas:** Algunas empresas tienen historial irregular en Yahoo Finance y requieren frecuencia de pago hardcodeada en `FRECUENCIAS_FIJAS` dentro de `actualizar.py`:

| Ticker   | Frecuencia | Motivo |
|----------|-----------|--------|
| ACN US   | 4 (trimestral) | Yahoo tiene historial incompleto |
| ADC US   | 12 (mensual) | Yahoo tiene años con frecuencia distinta |
| BATS LN  | 4 (trimestral) | — |
| RIO LN   | 2 (semestral) | Dividendo variable ligado a beneficio |
| ULVR LN  | 4 (trimestral) | — |

**Nota sobre tickers LN (Londres):** Los precios históricos de los CSVs están en peniques (GBX). Yahoo Finance también devuelve precios y dividendos en peniques para tickers `.L`. Si al cargar un CSV nuevo de un ticker LN los gráficos se ven distorsionados, verificar que los precios históricos estén en peniques y no en libras — si están en libras, multiplicar por 100 en la DB.

---

## Scripts

### `carga_inicial.py`
Ejecutar **una sola vez** al arrancar el proyecto o al añadir tickers nuevos.

- Lee todos los CSVs de `REPOSITORIO_GRAFICOS/`
- Carga precios y dividendos históricos en SQLite
- Excluye el año en curso del CSV (valor parcial)
- Calcula los umbrales de yield (percentiles en Python)
- Crea las vistas SQL

```bash
python carga_inicial.py
# Opcionales:
python carga_inicial.py --repo /ruta/csvs --db /ruta/graficos.db
```

### `actualizar.py`
Ejecutado **cada noche** por GitHub Actions (también manualmente).

- Lee los tickers que hay en la DB
- Descarga precios nuevos desde Yahoo Finance
- Actualiza dividendos anualizados con extrapolación robusta:
  - Detecta frecuencia de pago por mediana de años con ≥ 3 pagos
  - Usa frecuencia fija para los tickers en `FRECUENCIAS_FIJAS`
  - El DPS de cualquier año nunca cae por debajo del año anterior
- Recalcula umbrales de yield
- Regenera `docs/index.html` con todos los datos frescos

```bash
python actualizar.py
```

### `generar_newsletter.py`
Ejecutado **cada lunes** por GitHub Actions (también manualmente).

- Lee `tickers_maestro.csv` para obtener nombre, sector y tipo (Titular / Banquillo / Cantera / No info)
- Lee la DB para obtener precios, yields, rating, upside
- Incluye BRK/B US (sin dividendo) con precio actual descargado de Yahoo
- Descarga rendimiento semanal desde Yahoo Finance
- Genera HTML compatible con Outlook Classic (tablas, inline CSS)
- Envía por Gmail usando contraseña de aplicación

```bash
python generar_newsletter.py                          # envía el email
python generar_newsletter.py --solo-html --out preview.html  # previsualizar sin enviar
```

---

## Automatización (GitHub Actions)

### Workflow 1: `actualizar.yml`
**Cuándo:** Lunes a viernes a las 22:30 UTC (00:30 CET)
**Qué hace:**
1. Descarga precios y dividendos del día desde Yahoo Finance
2. Recalcula todos los indicadores
3. Regenera `docs/index.html`
4. Hace commit y push automático (con stash + rebase para evitar conflictos)
5. GitHub Pages publica el HTML actualizado en segundos

### Workflow 2: `newsletter_semanal.yml`
**Cuándo:** Lunes a las 08:00 UTC (10:00 CET)
**Qué hace:**
1. Lee los datos de la DB (actualizados la noche anterior)
2. Descarga el rendimiento semanal de Yahoo Finance
3. Genera la newsletter HTML compatible con Outlook
4. La envía por Gmail a los destinatarios configurados

Ambos workflows se pueden lanzar manualmente desde **GitHub → Actions → Run workflow**.

---

## Configuración de Secrets (GitHub)

En **Settings → Secrets and variables → Actions**:

| Secret | Descripción |
|---|---|
| `GMAIL_USER` | Email de Gmail remitente |
| `GMAIL_APP_PASS` | Contraseña de aplicación de 16 caracteres |
| `DESTINATARIOS` | Emails separados por coma |

---

## Ficheros del repo

```
correo_54039213855430223/
├── .github/
│   └── workflows/
│       ├── actualizar.yml            ← actualización nocturna
│       └── newsletter_semanal.yml    ← newsletter semanal
├── docs/
│   └── index.html                   ← HTML publicado por GitHub Pages (generado)
├── data/
│   └── graficos.db                  ← base de datos SQLite (generada y commiteada)
├── carga_inicial.py                 ← carga histórico desde CSVs (una vez)
├── actualizar.py                    ← actualización diaria desde Yahoo
├── generar_newsletter.py            ← newsletter semanal por email
├── tickers_maestro.csv              ← metadatos: nombre, sector, índice, tipo
├── requirements.txt                 ← dependencias Python (yfinance)
├── README.md                        ← este fichero
└── REPOSITORIO_GRAFICOS/            ← CSVs históricos (en .gitignore, solo local)
```

---

## Columna "tipo" en tickers_maestro.csv

La columna `tipo` clasifica cada empresa según su rol en la estrategia:

| Valor | Significado |
|---|---|
| `Titular` | Posición activa en cartera |
| `Banquillo` | Candidata en seguimiento activo |
| `Cantera` | En análisis preliminar |
| `No info` | Sin clasificar (por defecto para tickers nuevos) |

La clasificación se gestiona manualmente a través de un Excel externo. Al añadir un ticker nuevo, el tipo por defecto es `No info` hasta que se actualice el Excel y se regenere el CSV.

---

## Añadir un ticker nuevo

1. Conseguir el CSV histórico del modelo Excel y nombrarlo `TICKER_MERCADO.csv` (ej: `JNJ_US.csv`)
2. Abrir el Codespace del repo en GitHub
3. Subir el CSV a la carpeta `REPOSITORIO_GRAFICOS/`
4. Añadir una fila en `tickers_maestro.csv` con nombre, sector, índice y tipo (`No info` si no está clasificado aún)
5. Si el ticker es LN, verificar que los precios del CSV estén en peniques (no en libras)
6. En el terminal:

```bash
python carga_inicial.py
python actualizar.py
git add data/graficos.db docs/index.html tickers_maestro.csv
git commit -m "Añadir NUEVO TICKER"
git push
```

Si el ticker no paga dividendos (como BRK/B), no aparecerá en los gráficos GW pero sí en la newsletter con precio actual. Añadirlo a `TICKERS_SIN_DIV` en `generar_newsletter.py` si se quiere incluir explícitamente.

---

## Mapeo de mercados → Yahoo Finance

| Sufijo CSV | Yahoo | Bolsa |
|---|---|---|
| US | — | NYSE / NASDAQ |
| CN | .TO | Toronto |
| NA | .AS | Amsterdam |
| LN | .L | Londres (LSE) — precios en peniques (GBX) |
| GY | .DE | Xetra |
| FP / PA | .PA | París |
| SM / MC | .MC | Madrid |
| HK | .HK | Hong Kong |
| AU | .AX | Australia |
| JP | .T | Tokio |
| SW | .SW | Suiza |
| IT | .MI | Milán |
| BB | .BR | Bruselas |
| SE | .ST | Estocolmo |
| DC | .CO | Copenhague |
| NO | .OL | Oslo |
| FH | .HE | Helsinki |

Para añadir un mercado no listado, editar `SUFIJOS_YAHOO` en `actualizar.py` y `generar_newsletter.py`.

---

## Tickers con "/" en el símbolo

Yahoo Finance no acepta `/` en los tickers. El script convierte automáticamente `/` a `-`:
- `BRK/B US` → `BRK-B`
- `RCI/B CN` → `RCI-B.TO`
- `QBR/B CN` → `QBR-B.TO`
