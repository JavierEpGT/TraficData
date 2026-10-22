# Traffic Ingestion Service (Grupo 2)

Microservicio de ingesta de **tráfico vehicular real** para el proyecto *Monitoreo Urbano e Inteligencia Ambiental* (Universidad San Pablo de Guatemala).

Dado un punto GPS real (`lat`/`lon`):

1. Consulta Mapbox Directions (`driving-traffic` + `driving`)
2. Calcula el Índice de Congestión ∈ [0, 1]
3. Arma el Data Contract estándar y lo envía (o imprime en `DRY_RUN`) al Data Warehouse del G4
4. Guarda métricas crudas en SQLite (o Postgres si hay `DATABASE_URL`) para Looker Studio

**Regla:** sin datos simulados en producción. Si Mapbox falla o la lectura es inválida, se descarta y se registra el error.

## Requisitos

- Python 3.11+
- Token de Mapbox con acceso a Directions API

## Instalación

```bash
cd traffic-service
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Editar .env con MAPBOX_ACCESS_TOKEN, STUDENT_ID y SECRET_KEY
```

**Importante:** copia `.env.example` → `.env` y pon el token ahí. `.env` está en `.gitignore` — **nunca** lo subas a GitHub. Si el token se filtró (chat, screenshot, commit), regenerarlo en Mapbox y reemplazarlo en `.env`.

CSV de puntos reales de ejemplo: `data/points.csv`.

```bash
# Smoke test local (DRY_RUN, Mapbox real)
python -m app.cli run --csv data/points.csv
```

## Variables de entorno

| Variable | Requerida | Default | Descripción |
| --- | --- | --- | --- |
| `MAPBOX_ACCESS_TOKEN` | sí | — | Token Mapbox |
| `STUDENT_ID` | sí | — | Identificador del estudiante/grupo |
| `SECRET_KEY` | sí | — | Clave compartida para `auth_hash` |
| `DW_ENDPOINT_URL` | no | — | URL REST del DW (G4) |
| `DW_AUTH_TOKEN` | no | — | Token Bearer opcional |
| `DRY_RUN` | no | `true` | Si `true` o no hay DW URL: no envía, imprime payload |
| `SEGMENT_LENGTH_M` | no | `400` | Longitud del tramo sintético (m) |
| `SEGMENT_BEARING_DEG` | no | `0` | Rumbo del segundo punto (°) |
| `DB_PATH` | no | `traffic.db` | SQLite local (si `DATABASE_URL` está vacío) |
| `DATABASE_URL` | no | — | URL de Postgres/Neon. Si está vacío, se usa SQLite + `DB_PATH` |
| `GROUP1_API_URL` | no | — | Feed G1 (`https://api.quick-convertor.com/api/records`) |
| `GROUP1_API_TOKEN` | no | — | API key G1 enviada como header `x-api-key` |
| `G4_FEED_TOKEN` | no* | — | API key del feed para Prefect G4 (`x-api-key`) |

Los secretos **nunca** se hardcodean ni se loguean.

### Nota Mapbox

`annotations` requiere `overview=full` (docs Directions v5). El perfil `driving-traffic` usa `overview=full` + `annotations=duration,distance,congestion_numeric`. El perfil `driving` usa `overview=false`.

## API FastAPI

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- `GET /health` → `{"status":"ok"}`
- `POST /traffic/reading` body: `{"latitude": 14.6349, "longitude": -90.5069}`
- `GET /traffic/readings` — feed autenticado para Prefect G4

Respuesta de `POST /traffic/reading` incluye `status` (`SENT` | `DRY_RUN` | `FAILED` | `INVALID`), `payload` (si aplica) y `error`.

### Feed Prefect (G4)

G4 **tira** lecturas válidas (Data Contract). Header `x-api-key` = `G4_FEED_TOKEN`. Query: `since` (ISO-8601 `...Z`, inclusivo) y `limit` (1–500, default 100).

```bash
curl -H "x-api-key: $G4_FEED_TOKEN" \
  "https://<host-g2>/traffic/readings?limit=100"

curl -H "x-api-key: $G4_FEED_TOKEN" \
  "https://<host-g2>/traffic/readings?since=2026-10-10T22:30:00Z&limit=100"
```

Solo `DRY_RUN` / `SENT` con `traffic_congestion_index`. El host debe ser público (no `localhost`).

## CLI

```bash
# Ingestar puntos reales desde CSV (columnas lat,lon o latitude,longitude)
python -m app.cli run --csv points.csv

# Ingestar puntos GPS reales desde la API del G1
python -m app.cli run --from-g1

# Bucle cada 5 minutos
python -m app.cli run --csv points.csv --loop --interval 300
python -m app.cli run --from-g1 --loop --interval 300

# Exportar lecturas (SQLite o Postgres) → CSV (fuente Looker Studio)
python -m app.cli export-csv --out readings.csv
```

## Batch local G2 (sin otros grupos)

Un solo comando: cobertura Mapbox + ingesta DRY_RUN + export para Looker.

```bash
cd traffic-service
python scripts/g2_local_batch.py --csv data/points.csv
```

Genera / actualiza:

- `coverage_log.csv` — evidencia append-only (gitignored)
- `traffic.db` — lecturas crudas (gitignored)
- `readings.csv` — export para Looker Studio (gitignored)

### Checklist hora pico / valle (informe)

1. Correr el batch en **hora pico** (p.ej. 7:30–9:00 o 17:00–19:00).
2. Correr el **mismo** comando en **hora valle** (p.ej. 22:00–23:00 o domingo temprano).
3. Comparar en `coverage_log.csv` las columnas `t_traffic_s` / `traffic_congestion_index` entre ambas corridas (mismo `lat,lon`).
4. Guardar capturas o copias fechadas del log para el informe.

Si aparecen muchos `INVALID` con `SEGMENT_MISMATCH`, el snap de Mapbox alargó la ruta fuera de `[0.5×, 3×]` de `SEGMENT_LENGTH_M`. Ajusta puntos a vías continuas o prueba otro `SEGMENT_BEARING_DEG` / `SEGMENT_LENGTH_M` en `.env`.

### Looker Studio (tablero G2)

1. Ejecutar el batch (o `python -m app.cli export-csv --out readings.csv`).
2. En [Looker Studio](https://lookerstudio.google.com/): **Crear → Fuente de datos → Subir archivo** → seleccionar `readings.csv`.
3. Campos útiles: `timestamp`, `lat`, `lon`, `traffic_congestion_index`, `status`, `t_traffic_s`, `t_free_s`.
4. Gráficos sugeridos: serie temporal del índice, mapa por lat/lon, tabla filtrada por `status=DRY_RUN` (o `SENT` cuando exista DW).

El envío al Data Warehouse del G4 sigue pendiente (`DRY_RUN=false` + `DW_ENDPOINT_URL`).

## Script de cobertura (evidencia hora pico / valle)

```bash
python scripts/coverage_check.py --csv data/points.csv --log coverage_log.csv
```

Imprime una tabla por punto (`t_traffic`, `t_free`, distancia, índice, congestion promedio) y agrega cada corrida con timestamp a `coverage_log.csv`.

## Tests

Los mocks de Mapbox viven **solo** en `tests/` (vía `respx`).

```bash
pytest -q
```

## Data Contract (enviado al DW)

Campos: `reading_id`, `timestamp`, `latitude`, `longitude`, `student_id`, `auth_hash`, `domain_data` (`noise_db`, `traffic_congestion_index`, `air_quality_aqi`). Sin campos extra.

Índice: `(t_traffic - t_free) / t_traffic`, clamp [0, 1], 4 decimales.

## TODO(G2)

- Confirmar formato exacto de `auth_hash` con G4
- Confirmar endpoint y autenticación del DW
- Confirmar aceptación de `null` en `noise_db` / `air_quality_aqi`
- `Group1ApiSource` ya consume `/api/records` (auth `x-api-key`, paginación)
- Feed Prefect G4: `GET /traffic/readings` (header `x-api-key`)
