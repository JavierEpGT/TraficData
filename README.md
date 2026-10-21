# TraficData — Grupo 2: Tráfico Vehicular

Microservicio de ingesta de **tráfico vehicular en tiempo real** para el proyecto integrador:

**Monitoreo Urbano e Inteligencia Ambiental**  
*Introducción a Business Analytics — Universidad San Pablo de Guatemala*

Repositorio: [JaviGodGt/TraficData](https://github.com/JaviGodGt/TraficData)

---

## Contexto del proyecto

El sistema urbano se construye con **4 equipos**:

| Grupo | Dominio | Rol |
| --- | --- | --- |
| **G1** | Ruido | Captura con celulares (GPS real) |
| **G2 (nosotros)** | Tráfico vehicular | Consulta Mapbox, índice de congestión, envío al DW |
| **G3** | Calidad del aire | Sensor / API de AQI |
| **G4** | Integración | Data Warehouse + Master Dashboard |

Todos los grupos producen lecturas con el **mismo Data Contract** (JSON). G4 las recibe por REST, las almacena y las visualiza en el dashboard maestro. G2 además guarda métricas crudas locales para su tablero en **Looker Studio**.

---

## Qué hace este microservicio

Dado un punto GPS real (`latitude`, `longitude`):

1. Calcula un segundo punto a `SEGMENT_LENGTH_M` metros (rumbo `SEGMENT_BEARING_DEG`) para formar un tramo consultable en Mapbox Directions.
2. Consulta **dos perfiles** sobre el mismo tramo:
   - `mapbox/driving-traffic` → tiempo con tráfico en vivo
   - `mapbox/driving` → tiempo de flujo libre (sin tráfico)
3. Calcula el **Índice de Congestión** ∈ [0, 1]:
   - `0` = fluido
   - `1` = colapsado
   - Fórmula: `(t_con_trafico - t_libre) / t_con_trafico`, limitado a `[0, 1]`, 4 decimales
4. Arma el payload del Data Contract y lo envía por REST al Data Warehouse del G4 (o lo imprime si `DRY_RUN=true`).
5. Persiste métricas crudas en SQLite para exportar a CSV / Looker Studio.

### Regla innegociable: datos reales

- Nada simulado ni generado por IA en código de producción.
- Los mocks existen **solo** en `tests/` y están aislados.
- Si Mapbox falla o la lectura es inválida → se **descarta**, se registra el error y **nunca** se inventa un valor.

---

## Stack

- Python 3.11+
- FastAPI + uvicorn
- httpx
- pydantic v2 + pydantic-settings
- sqlite3 (stdlib)
- pytest + respx
- Type hints en todo + logging estándar

---

## Estructura del repositorio

```
TraficData/
├── README.md                 ← este archivo (contexto GitHub)
├── .gitignore
└── traffic-service/
    ├── app/
    │   ├── config.py         # variables de entorno
    │   ├── models.py         # Data Contract (Pydantic)
    │   ├── geo.py            # punto destino geodésico
    │   ├── mapbox_client.py  # Directions v5 (doble perfil)
    │   ├── congestion.py     # índice puro
    │   ├── contract.py       # payload + auth_hash
    │   ├── dw_client.py      # POST al Data Warehouse G4
    │   ├── storage.py        # SQLite + export CSV
    │   ├── sources.py        # CSV / API G1 (records)
    │   ├── main.py           # pipeline + FastAPI
    │   └── cli.py            # run / export-csv
    ├── scripts/
    │   └── coverage_check.py # evidencia hora pico / valle
    ├── tests/                # mocks Mapbox solo aquí
    ├── .env.example
    ├── requirements.txt
    └── README.md             # guía operativa del servicio
```

---

## Flujo de una lectura

```
GPS real (lat, lon)
    → geo: segundo punto del tramo
    → Mapbox driving-traffic + driving
    → validaciones (ROUTE_MISMATCH, SEGMENT_MISMATCH, duraciones)
    → índice de congestión
    → Data Contract (auth_hash)
    → DRY_RUN? imprimir : POST al DW G4
    → SQLite readings_raw (SENT | DRY_RUN | FAILED | INVALID)
```

### Validaciones (lectura inválida → no se envía)

| Código | Condición |
| --- | --- |
| `ROUTE_MISMATCH` | Distancias traffic vs free difieren > 10 % |
| `SEGMENT_MISMATCH` | Distancia de ruta fuera de `[0.5×, 3×]` de `SEGMENT_LENGTH_M` |
| `NON_POSITIVE_DURATION` | Alguna duración ≤ 0 |

### Mapbox Directions v5

- Orden de coordenadas: **longitud, latitud**
- `driving-traffic`: `overview=full` + `annotations=duration,distance,congestion_numeric` (requerido por la [documentación oficial](https://docs.mapbox.com/api/navigation/directions/))
- `driving`: `overview=false`
- Timeout 10 s; reintentos con backoff (máx. 3) solo en 429, 5xx y timeouts

---

## Data Contract (compartido por los 4 grupos)

```json
{
  "reading_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-10-10T22:30:00Z",
  "latitude": 14.6349,
  "longitude": -90.5069,
  "student_id": "g2-student-001",
  "auth_hash": "<sha256 hex 64 chars>",
  "domain_data": {
    "noise_db": null,
    "traffic_congestion_index": 0.4321,
    "air_quality_aqi": null
  }
}
```

- `auth_hash` = SHA256 hex minúsculas de `student_id + timestamp + secret_key` (UTF-8, sin separadores), usando **exactamente** el mismo `timestamp` del payload.
- Lat/lon del payload = punto **original** GPS, no el punto calculado del tramo.
- **Sin campos extra** en el JSON del contrato (G4 valida esquema). Métricas adicionales solo en SQLite local.

---

## Inicio rápido

```bash
git clone https://github.com/JaviGodGt/TraficData.git
cd TraficData/traffic-service

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Editar .env: MAPBOX_ACCESS_TOKEN, STUDENT_ID, SECRET_KEY
```

**Secretos / Mapbox:** el token vive **solo** en `traffic-service/.env` (está en `.gitignore`). Nunca lo pegues en README, issues, commits ni chats públicos. Si un token se expuso, rótalo en el [dashboard de Mapbox](https://account.mapbox.com/access-tokens/) (eliminar/regenerar) y actualiza `.env`.

Puntos GPS de ejemplo (Ciudad de Guatemala): `traffic-service/data/points.csv`.

### Avance sin otros grupos (G2 solo)

Con Mapbox y `DRY_RUN=true` pueden avanzar evidencia y Looker sin G1/G4:

```bash
cd traffic-service
python scripts/g2_local_batch.py --csv data/points.csv
```

Eso actualiza `coverage_log.csv`, SQLite y `readings.csv`. Repetir en **hora pico** y **hora valle** para el informe. Detalle Looker Studio y checklist en [`traffic-service/README.md`](traffic-service/README.md).

### Variables de entorno

| Variable | Requerida | Default | Descripción |
| --- | --- | --- | --- |
| `MAPBOX_ACCESS_TOKEN` | sí | — | Token Mapbox |
| `STUDENT_ID` | sí | — | ID del estudiante / grupo |
| `SECRET_KEY` | sí | — | Clave compartida para `auth_hash` |
| `DW_ENDPOINT_URL` | no | — | URL REST del DW (G4) |
| `DW_AUTH_TOKEN` | no | — | Bearer opcional |
| `DRY_RUN` | no | `true` | Si `true` o sin DW URL: no envía, imprime payload |
| `SEGMENT_LENGTH_M` | no | `400` | Longitud del tramo (m) |
| `SEGMENT_BEARING_DEG` | no | `0` | Rumbo del segundo punto (°) |
| `DB_PATH` | no | `traffic.db` | SQLite local |
| `GROUP1_API_URL` | no | — | Feed G1 (`…/api/records`) |
| `GROUP1_API_TOKEN` | no | — | API key G1 (header `x-api-key`) |
| `G4_FEED_TOKEN` | no* | — | API key del feed para Prefect G4 (`x-api-key`) |

Los secretos **nunca** se hardcodean ni se loguean. No subir `.env` al repo.

---

## Cómo correr

### API

```bash
cd traffic-service
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- `GET /health` → `{"status":"ok"}`
- `POST /traffic/reading` con body `{"latitude": 14.6349, "longitude": -90.5069}`
- `GET /traffic/readings` — feed para Prefect G4 (requiere `x-api-key` = `G4_FEED_TOKEN`)

```bash
curl -H "x-api-key: $G4_FEED_TOKEN" \
  "https://<host-g2>/traffic/readings?limit=100"
# Incremental: agregar &since=2026-10-10T22:30:00Z
```

Solo lecturas `DRY_RUN` / `SENT` con índice. Respuesta: `{ "count": N, "items": [Data Contract...] }`. No usar `localhost` para G4: el servicio debe estar publicado.

### CLI

```bash
# CSV de puntos GPS reales (columnas lat,lon o latitude,longitude)
python -m app.cli run --csv points.csv

# Puntos GPS reales desde la API del G1 (Noise Monitor)
python -m app.cli run --from-g1

# Bucle cada 5 minutos
python -m app.cli run --csv points.csv --loop --interval 300
python -m app.cli run --from-g1 --loop --interval 300

# Exportar SQLite → CSV (fuente Looker Studio)
python -m app.cli export-csv --out readings.csv
```

### Evidencia de cobertura (hora pico vs valle)

```bash
python scripts/coverage_check.py --csv points.csv --log coverage_log.csv
```

Consulta ambos perfiles por punto, imprime tabla e append a `coverage_log.csv` con timestamp. Sirve para demostrar que los tiempos con tráfico varían en vivo (no solo histórico) y guardar evidencia para el informe.

### Tests

```bash
pytest -q
```

Mapbox está mockeado solo en `tests/` (respx). No hay llamadas reales a Mapbox en CI/tests.

---

## Almacenamiento local (`readings_raw`)

SQLite con: `reading_id`, `timestamp`, lat/lon originales, destino del tramo, tiempos y distancias (traffic/free), promedio `congestion_numeric`, índice, `status` (`SENT` | `DRY_RUN` | `FAILED` | `INVALID`), `error`, `created_at`.

Ese CSV exportado alimentará el tablero de G2 en Looker Studio.

---

## Pendientes TODO(G2)

Coordinar con los otros grupos:

- [ ] Confirmar formato exacto de `auth_hash` con G4
- [ ] Confirmar endpoint y autenticación del DW
- [ ] Confirmar aceptación de `null` en `noise_db` / `air_quality_aqi`
- [x] Feed pull para Prefect G4 (`GET /traffic/readings`)
- [x] Origen de coordenadas: CSV o API G1 (`python -m app.cli run --from-g1`)

---

## Licencia / uso académico

Proyecto académico — Universidad San Pablo de Guatemala.  
Grupo 2: Tráfico Vehicular.
