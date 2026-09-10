# Klaviyo ↔ BigQuery pipeline blueprint (vendored snapshot)

> Provenance: distilled from a production Dagster/dbt Klaviyo↔BigQuery implementation (2026-08), scrubbed of brand identifiers. Cod simplified but faithful to the real logic; where it cuts corners, it says so.
> Reference material for a future Klaviyo ingestion pipeline in this repo — read-only snapshot, not implemented here.

Blueprint de referencia: pipelines Klaviyo ↔ BigQuery de una implementación productiva. Código simplificado pero fiel a la lógica real; donde recorta, lo dice.

---

# Blueprint: pipelines Klaviyo ↔ BigQuery

## Componentes y dirección del dato

```
─────────── Klaviyo → BigQuery ───────────
Klaviyo API → events (6 metricas x 4 cuentas) → silver-project.klaviyo.events_{inc,us,uk,australia}
Klaviyo API → google_rating (segmento NPS)     → silver-project.klaviyo.google_rating_{inc,us,uk,aus}
Klaviyo API → campaigns (one-off, ids fijos)   → silver-project.klaviyo.campaigns_inc

─────────── BigQuery → Klaviyo ───────────
gold-project.analytics.xa_user_traits_{market} → Klaviyo Profile Bulk Import
Klaviyo segmento "sin email"                  → Klaviyo Data Privacy Deletion Jobs

─────────── dbt (gold-project) ───────────
events_* → src_klaviyo_events → fct_marketing_email → xa_marketing_email_action
                              → xa_crm_email_attribution → xi_marketing_crm_performance
google_rating_* → src_klaviyo_short_nps → xi_cx_nps
```

## Convenciones comunes a todos los jobs Dagster

- Un `@graph` por caso de uso, convertido a 4 jobs con `.to_job(name=..., config=yaml_por_cuenta)`. Las 4 cuentas (INC, US, UK, AUS) son 4 API keys distintas en Klaviyo.
- Secretos siempre via `SecretManager().get_secret(id)`. Dos tipos: API key de Klaviyo (`klaviyo-api-key-{inc,us,uk,aus}`) y service account de BigQuery (`silver-project-dagster-klaviyo-service-account`; en dev, la de `dev-project`).
- Header fijo de Klaviyo: `revision: 2025-07-15`, `Authorization: Klaviyo-API-Key <key>`, `accept: application/vnd.api+json`.
- Paginacion siempre por cursor: seguir `links.next` hasta que sea null. Los `params` van solo en la primera request; `next` ya los trae.
- `IS_DAGSTER_AGENT_MODE == 'True'` (cloud) expone solo jobs prod; local expone tambien los `_dev_job` que escriben a `dev-project`.
- Slack hook `slack_message_on_failure` a `#data-alerts` solo en events y google_rating.

```python
# esqueleto que repiten todos los jobs
import yaml, os
from dagster import graph
base_dir = os.path.dirname(__file__)
cfg_us = yaml.load(open(f"{base_dir}/../configs/load_klaviyo_events_us_job.yaml"),
    Loader=yaml.FullLoader)

@graph
def klaviyo_events_job():
    df = list_klaviyo_metric_events()     # op 1: API → DataFrame
    load_events_to_bigquery(df)           # op 2: DataFrame → BQ

klaviyo_events_us_job = klaviyo_events_job.to_job(
    name="klaviyo_events_us_job",
    config={'ops': cfg_us['ops'], 'resources': {...slack...}},
    resource_defs={'slack': slack_resource, 'environment': make_values_resource()},
    hooks={slack_message_on_failure},
)
```

## 1. Klaviyo → BigQuery

### 1.1 Events (el pipeline central)

**Schedule.** INC `15 */1 * * *`, US `0 */1 * * *`, UK `0 */3 * * *`, AUS `15 */3 * * *`, todos `America/Toronto`, `dagster/max_runtime = 7200s`.

**Config por cuenta** (US como ejemplo; los `metric_id` cambian por cuenta):

```yaml
ops:
  list_klaviyo_metric_events:
    config:
      klaviyo_secret_id: "klaviyo-api-key-us"
      metric_ids_to_keep:        # ids REALES por cuenta; ES UNA LISTA DE PRIORIDAD, no un set
        - <us_send>   # emailReceived (send)  -- primero: es el denominador
        - <us_open>   # emailOpen              -- mas volumen, mas barato de perder
        - <us_click>   # emailClick
        - <us_sub>   # emailSubscribe
        - <us_unsub>   # emailUnSubscribe
        - <us_bounce>   # emailBounce            -- ultimo
      max_pages: 50000           # guard por slice
      hours_back: 1              # = cadencia del cron
      overlap_hours: 3           # INC/US 3, UK/AUS 5
      slice_minutes: 15
      max_workers: 8
  load_events_to_bigquery:
    config:
      secret_id: "silver-project-dagster-klaviyo-service-account"
      dataset_id: klaviyo
      table_id: events_us
```

**Paso 1: ventana.** Regla de sizing `W >= G + L` con W = hours_back + overlap, G = peor gap entre corridas (un tick salteado + hora DST de Toronto), L = lag con que Klaviyo publica el evento (>=1h en blasts). INC/US: G=3, L=1 → W=4. UK/AUS: G=7, L=1 → W=8. Backfill manual: `since_dt` + `until_dt` juntos, sin overlap.

```python
if since_dt_cfg and until_dt_cfg:
    since_dt, until_dt = _naive_utc(since_dt_cfg), _naive_utc(until_dt_cfg)
elif since_dt_cfg or until_dt_cfg:
    raise ValueError("since_dt y until_dt van juntos")
else:
    until_dt = datetime.utcnow().replace(microsecond=0)
    since_dt = until_dt - timedelta(hours=hours_back + overlap_hours)
```

**Paso 2:** un stream por metrica, filtrado server-side. Antes se paginaba el firehose entero y se filtraba en Python. Ahora `equals(metric_id, ...)` en la API. Los streams corren uno detras de otro en el orden del YAML compartiendo un presupuesto de 90 min (`MAX_FETCH_MINUTES`); si se agota, el stream actual corta y los siguientes no arrancan. El orden decide quien absorbe la perdida.

**Paso 3:** time slicing dentro de cada metrica. La ventana se parte en slices de 15 min (half-open salvo el ultimo, que incluye el borde derecho) y se paginan concurrentemente con `ThreadPoolExecutor(8)`. Cada thread tiene su propia `requests.Session`. Un slice que supera 200 paginas devuelve `[start, oldest_visto]` para que se re-encole partido en dos; se parte hasta 2 segundos (resolucion del filtro de Klaviyo).

```python
def _fetch_slice(metric_id, sl, allow_split):
    upper = "less-or-equal" if sl.inclusive else "less-than"
    params = {
        "page[size]": 200,
        "include": "profile",            # solo profile: email + location
        "sort": "-datetime",             # newest → oldest
        "filter": (f"greater-or-equal(datetime,{sl.start.isoformat()}),"
                   f"{upper}(datetime,{sl.end.isoformat()}),"
                   f'equals(metric_id,"{metric_id}")'),
    }
    next_url, page_count, oldest = "https://a.klaviyo.com/api/events", 0, None
    while next_url and page_count < max_pages:
        resp = get_with_retries(next_url, params if page_count == 0 else None)
        payload = resp.json()
        data, included = payload["data"], payload.get("included", [])
        profiles = {p["id"]: p for p in included if p["type"] == "profile"}
        batch = [flatten(e, profiles) for e in data
                 if e["relationships"]["metric"]["data"]["id"] == metric_id]  # defensa
        _record(batch)                    # buffer thread-safe, flush CSV cada 20k
        if data:
            oldest = min(oldest or datetime.max, _naive_utc(data[-1]["attributes"]["datetime"]))
        next_url = payload.get("links", {}).get("next")
        page_count += 1
        if time.monotonic() >= deadline:
            return SliceResult(out_of_time=True, oldest=oldest)
        if allow_split and next_url and page_count >= 200:
            return SliceResult(split_here=True, oldest=oldest)
        if not data and (stagnant := stagnant + 1) >= 3:
            break
    return SliceResult(truncated=bool(next_url) and page_count >= max_pages, oldest=oldest)
```

**Reglas de red dentro de `get_with_retries`:**

- 429 no es error: leer `Retry-After`, y frenar a los 8 workers (throttle compartido, Klaviyo limita por cuenta). No consume retries.
- Timeout / ConnectionError / 5xx: backoff exponencial `min(5·2^n, 120)`, max 5 intentos, recicla la session.
- Cada 100 paginas se recicla la conexion TCP.
- Guard de filtro: si tras 500 filas menos del 50% son de la metrica pedida, `raise` (la API esta ignorando el filtro; mejor morir en segundos que a los 2h).

**Paso 4: flatten.** Record base + todas las `event_properties` como columnas, keys normalizadas.

```python
def flatten(e, profiles):
    a = e["attributes"]; props = a.get("event_properties") or {}
    pid = (e["relationships"].get("profile", {}).get("data") or {}).get("id")
    prof = profiles.get(pid, {}).get("attributes", {}); loc = prof.get("location") or {}
    rec = {
        "event_id": e["id"], "event_type": e["type"],
        "metric_id": e["relationships"]["metric"]["data"]["id"],
        "profile_id": pid, "email": prof.get("email"),
        "datetime": a["datetime"], "timestamp": a["timestamp"], "uuid": a["uuid"],
        "flow_id": props.get("$flow"),
        "country": loc.get("country"), "region": loc.get("region"), "city": loc.get("city"),
    }
    for k, v in props.items():
        ck = k.strip().replace("$", "").replace(" ", "_").lower()
        rec.setdefault(ck, v)   # ej: "$message" → message, "Campaign Name" → campaign_name
    return rec
```

Todo se castea a STRING (`_normalize_value`: dict/list → json, bool → "True"/"False", ""/"nan"/"None" → NULL). Los CSVs de 20k filas van a un temp dir que se borra en `finally` (salvo `chunks_folder` configurado, que es lo que consume `upload_local_chunks_job` para recovery).

**Paso 5: load con MERGE.** Una staging table por run, un solo MERGE.

```python
def merge_events_by_event_id(bq, dataset, table, frames, run_id):
    types = _target_column_types(bq, dataset, table)  # INFORMATION_SCHEMA.COLUMNS, is_hidden='NO'
    if not types: raise ValueError("no pude leer schema, no armo un MERGE a ciegas")
    cols = list(types)
    stg = f"stg_{table}_{re.sub(r'[^0-9A-Za-z_]', '_', run_id)}"
    bq.execute_command(f"""
        CREATE OR REPLACE TABLE `{p}.{dataset}.{stg}` ({', '.join(f'`{c}` STRING' for c in cols)})
        OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR))""")
    try:
        for f in frames:
            f = f.reindex(columns=cols)          # columnas nuevas se DESCARTAN, faltantes → NULL
            f = f[f["event_id"].notna()]          # sin id no se puede dedupear
            bq.insert_rows_from_df(dataset, stg, f, replace_existing=False)
        values = ", ".join(f"s.`{c}`" if t == "STRING" else f"CAST(s.`{c}` AS {t})" for c, t in types.items())
        bq.execute_command(f"""
            MERGE `{p}.{dataset}.{table}` t
            USING (SELECT * FROM `{p}.{dataset}.{stg}`
                   QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id) = 1) s
            ON t.event_id = s.event_id
            WHEN NOT MATCHED THEN INSERT ({', '.join(cols)}) VALUES ({values})""")
    finally:
        bq.delete_table(dataset, stg)
```

Por que MERGE y no SELECT-then-append: `events_us` (~225M filas, 188 GB) no esta particionada ni clusterizada, asi que cada `WHERE event_id IN (...)` era un full scan (~$14/dia). MERGE es un scan por run (~$0.5/dia) y es atomico, lo que cierra la race entre dos runs que se pisan por el overlap. Solo `WHEN NOT MATCHED`: los eventos son inmutables.

Recovery manual: `upload_local_chunks_job` lee CSVs de un `chunks_folder` y los pasa como generador a la misma `merge_events_by_event_id`.

### 1.2 Google rating (short NPS)

Diario 06:00–07:30 Toronto, escalonado por cuenta. Lee un segmento de Klaviyo que el equipo de CRM mantiene con "perfiles con `google_rating` seteado".

```python
url = f"https://a.klaviyo.com/api/segments/{segment_id}/profiles"
params = {"page[size]": 100, "fields[profile]": "email,properties"}
while url:
    data = get_with_429_backoff(url, params if "?" not in url else None).json()
    for p in data["data"]:
        email = p["attributes"].get("email")
        rating = (p["attributes"].get("properties") or {}).get("google_rating")
        if email and rating is not None:
            rows.append({"email": email, "google_rating": str(rating)})
    url = data.get("links", {}).get("next")
```

Load = upsert por email, solo si cambio el rating:

```sql
MERGE `klaviyo.google_rating_us` AS target
USING `klaviyo.google_rating_us_tmp` AS source   -- replace_existing=True, + ingested_at = now()
ON target.email = source.email
WHEN MATCHED AND IFNULL(CAST(target.google_rating AS STRING), '')
            != IFNULL(CAST(source.google_rating AS STRING), '')
  THEN UPDATE SET google_rating = source.google_rating, ingested_at = source.ingested_at
WHEN NOT MATCHED THEN INSERT (email, google_rating, ingested_at)
  VALUES (source.email, source.google_rating, source.ingested_at)
```

`ingested_at` es la unica fecha disponible y downstream se usa como `response_dt`. Es la fecha en que el rating cambio en BQ, no en que el cliente respondio.

### 1.3 Campaigns (one-off)

Sin schedule. `GET /api/campaigns/{id}` para ids hardcodeados en el YAML; extrae `definition.content.{subject, preview_text, from_email, ...}` y appendea a `campaigns_inc`. 8 filas, ultima carga 2025-09-11. No lo consume nadie en dbt.

## 2. BigQuery → Klaviyo

### 2.1 User traits (profile enrichment)

Schedule 2x/dia en UTC: INC 09/21, US 08/20, UK 05/17, AUS 03/15.

Fuente: `gold-project.analytics.xa_user_traits_{market}`, un modelo dbt (table, grano email, una tienda por modelo). Logica:

```sql
-- xa_user_traits_us.sql (esquema)
WITH customers AS (              -- base: Shopify customers de ESA tienda
  SELECT id, email, first_name, last_name, phone, city, country, orders_count,
         json_extract_scalar(email_marketing_consent_state) AS email_marketing_consent,
         json_extract_scalar(sms_marketing_consent_state)   AS sms_marketing_consent,
         shop_name AS shop_url, updated_at
  FROM {{ ref('src_shopify_customers') }}
),
xa AS (                          -- enriquecimiento de xa_user_email: RFM, LTV, acquisition
  SELECT email, is_member AS has_brand_account, user_type, last_order_dt,
         aov_usd_lt, lt_order_count, lt_order_revenue,
         segment_l1_status, segment_l3_status, segment_l4_status, ...
  FROM {{ ref('xa_user_email') }}
),
promo AS (                       -- promo_type / gifting_type por mayoria de ordenes (xa_order)
  ...
),
multi_store_customer AS (        -- primary_market: si esta en 1 tienda → esa;
                                  -- si suscripto en 1 → esa;
                                  -- sino tienda con mas ordenes en 365d; sino US > INC > UK > AUS
  ...
),
product_ranking AS (             -- recommended_products_array desde fct_candidates_ranking_category_summary
  ...
),
store_assignment AS (            -- assigned_store + store_assignment_type
  (xf_user_email_store_assignment)
  ...
)
SELECT c.*, x.* EXCEPT (email, segment_l1_status, segment_l3_status, segment_l4_status),
       cat_conf.* EXCEPT (email), p.promo_type, p.gifting_type, msc.primary_market,
       pr.subject_line_tag, pr.recommended_products_array,
       COALESCE(CASE WHEN x.segment_l1_status='Active' AND x.segment_l3_status='Champions'
                       AND x.segment_l4_status='VIP'
                     THEN 'Active Champions VIP'
                     ELSE CONCAT(x.segment_l1_status, ' ', x.segment_l3_status) END,
                'To be classified') AS segment_current,
       sa.assigned_store, sa.store_assignment_type
FROM customers c
LEFT JOIN xa x                                          ON c.email = x.email
LEFT JOIN {{ ref('fct_user_cat_confidence') }} cat_conf  ON c.email = cat_conf.email
LEFT JOIN promo p                                        ON p.order_email = c.email
LEFT JOIN multi_store_customer msc                       ON c.email = msc.email
LEFT JOIN product_ranking pr                             ON c.email = pr.email
LEFT JOIN store_assignment sa                            ON LOWER(c.email) = sa.email
WHERE LOWER(c.shop_url) = '<market>_shop'
QUALIFY ROW_NUMBER() OVER (PARTITION BY c.email ORDER BY c.updated_at) = 1
```

Op 1 `fetch_user_traits`: incremental por `updated_at` (viene de Shopify customers, o sea "cliente tocado en Shopify en las ultimas N horas"), ordenado por email para permitir resume por cursor.

```python
where = (f"WHERE updated_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {lookback_hours} HOUR) "
         f"AND updated_at <= CURRENT_TIMESTAMP()") if lookback_hours > 0 else ""  # 0 = backfill full
df = bq.select_rows(f"SELECT * FROM `{bq_table}` {where} ORDER BY email")
df["recommended_products_array"] = df["recommended_products_array"].apply(lambda x: json.dumps(list(x)))
return df.to_dict(orient="records")
```

Op 2 `send_user_traits`: arma perfiles y los manda al Bulk Import.

```python
CORE_PROFILE_FIELDS = {"email","phone_number","external_id","first_name","last_name",
                        "organization","title","image","locale"}
CORE_LOCATION_FIELDS = {"address1","address2","city","country","region","zip","timezone",
                         "ip","latitude","longitude"}

def clean_user_for_klaviyo(row):
    attrs, loc, props = {}, {}, {}
    for k, v in row.items():
        if k in {"user_type", "created_ts"}: continue
        v = safe_value(v)                    # dates → isoformat, NaN/inf → None, resto str
        if v is None: continue
        (attrs if k in CORE_PROFILE_FIELDS else loc if k in CORE_LOCATION_FIELDS else props)[k] = v
    if loc:   attrs["location"] = loc
    if props: attrs["properties"] = props    # todo lo demas → custom properties del perfil
    return {"type": "profile", "attributes": attrs}

for i in range(0, len(profiles), batch_size):     # batch_size 1000
    payload = {"data": {"type": "profile-bulk-import-job",
                         "attributes": {"profiles": {"data": profiles[i:i+batch_size]}}}}
    ok, invalid_idx, err = post_with_retries("https://a.klaviyo.com/api/profile-bulk-import-jobs", payload)
    if invalid_idx:                                # 400 con indices de perfiles invalidos
        # drop esos indices, reintentar el batch sin ellos, loguear cada uno
    # 429/5xx: backoff; otros errores: se loguea y se sigue con el siguiente batch (no raise)
```

Emite en el output el ultimo email enviado como cursor (`start_after_email`) para reanudar un backfill largo con `WHERE email >= cursor`.

### 2.2 Delete profiles without email

Diario 04:00–05:30 Toronto. Segmento de Klaviyo con perfiles sin email (ruido de POS/SMS). Se usa deletion y no suppression porque el bulk suppression exige email como identificador.

```python
# op 1: ids del segmento
url = f"{BASE}/segments/{segment_id}/relationships/profiles"     # solo ids, page[size]=100
# op 2: un deletion job por perfil
for pid in profile_ids:
    payload = {"data": {"type": "data-privacy-deletion-job",
                         "attributes": {"profile": {"data": {"type": "profile", "id": pid}}}}}
    r = requests.post(f"{BASE}/data-privacy-deletion-jobs", json=payload, headers=headers)
    if r.status_code == 429:
        wait = int(re.search(r"available in (\d+) second", r.json()["errors"][0]["detail"]).group(1))
        time.sleep(wait); retry (max 5)
    time.sleep(0.2)
```

## 3. Capa dbt (gold-project)

### 3.1 `src_klaviyo_events` (view, dataset `source`)

Union de las 4 tablas silver, tipado minimo, mapeo `metric_id` → `event_type` y `account`, dedup por `uuid`. Este es el unico lugar donde vive el diccionario de metricas por cuenta.

```sql
WITH all_events AS (
  SELECT event_id, event_type, metric_id, profile_id, email,
         CAST(TRIM(REPLACE(datetime, '+00:00', '')) AS DATETIME) AS created_ts,
         DATE(datetime) AS created_dt, timestamp, uuid,
         flow_id AS journey_id, country, region, city,
         subject AS email_subject, message AS message_id, campaign AS campaign_id,
         campaign_name, message_name AS message_type_name, flow,
         method, list_ids AS email_list_ids, ...   -- ~45 columnas STRING
  FROM `silver-project.klaviyo.events_inc`
  UNION ALL ... events_us  UNION ALL ... events_uk  UNION ALL ... events_australia
)
SELECT event_id,
  CASE                                             -- orden dentro de cada IN: UK, AUS, INC, US
    WHEN metric_id IN ('<uk_sub>','<aus_sub>','<inc_sub>','<us_sub>') THEN 'emailSubscribe'
    WHEN metric_id IN ('<uk_open>','<aus_open>','<inc_open>','<us_open>') THEN 'emailOpen'
    WHEN metric_id IN ('<uk_click>','<aus_click>','<inc_click>','<us_click>') THEN 'emailClick'
    WHEN metric_id IN ('<uk_unsub>','<aus_unsub>','<inc_unsub>','<us_unsub>') THEN 'emailUnSubscribe'
    WHEN metric_id IN ('<uk_send>','<aus_send>','<inc_send>','<us_send>') THEN 'emailReceived'
    WHEN metric_id IN ('<uk_bounce>','<aus_bounce>','<inc_bounce>','<us_bounce>') THEN 'emailBounce'
  END AS event_type,
  metric_id,
  CASE WHEN metric_id IN ('<uk_sub>','<aus_sub>','<inc_sub>','<us_sub>') THEN method END AS signup_source,
  CASE WHEN metric_id IN ('<uk_unsub>','<aus_unsub>','<inc_unsub>','<us_unsub>') THEN method END AS unsub_source,
  CASE
    WHEN metric_id IN ('<uk_sub>','<uk_open>','<uk_click>','<uk_unsub>','<uk_send>','<uk_bounce>') THEN 'Market UK'
    WHEN metric_id IN ('<aus_sub>','<aus_open>','<aus_click>','<aus_unsub>','<aus_send>','<aus_bounce>') THEN 'Market AUS'
    WHEN metric_id IN ('<inc_sub>','<inc_open>','<inc_click>','<inc_unsub>','<inc_send>','<inc_bounce>') THEN 'Market INC'
    WHEN metric_id IN ('<us_sub>','<us_open>','<us_click>','<us_unsub>','<us_send>','<us_bounce>') THEN 'Market US'
  END AS account,
  * EXCEPT (event_id, event_type, metric_id)
FROM all_events
QUALIFY ROW_NUMBER() OVER (PARTITION BY uuid ORDER BY created_ts DESC) = 1
```

### 3.2 `fct_marketing_email` (incremental, insert_overwrite, particion `created_ts`)

Union de tres ESPs con corte historico: el ESP anterior `< 2025-09-29`, Klaviyo `>= 2025-09-29`, el proveedor de SMS `>= 2025-09-29`. Todas al mismo schema canonico (~35 columnas). Ventana de reproceso 7 dias via macro `backfill_partitions('marketing_email', 'datetime', 6)`, override con `--vars '{marketing_email_backfill_start: ..., marketing_email_backfill_end: ...}'`.

```sql
{% set partitions_to_replace = backfill_partitions('marketing_email', 'datetime', 6) %}
{{ config(materialized='incremental', incremental_strategy='insert_overwrite',
          partition_by={'field': 'created_ts', 'data_type': 'datetime'},
          partitions=partitions_to_replace,
          cluster_by=['created_ts','email','event_type','event_group']) }}

WITH klaviyo_src AS (
  SELECT
    account,
    CONCAT(CAST(DATETIME(TIMESTAMP(created_ts), "America/Toronto") AS STRING), email,
           CASE WHEN LOWER(event_type)='emailunsubscribe' AND email_list_ids IS NOT NULL
                     AND email_list_ids <> '[]' THEN 'emailAutomationSuppressionRemoved'
                WHEN LOWER(event_type)='emailsubscribe' AND email_list_ids IS NOT NULL
                     AND email_list_ids <> '[]' THEN 'emailAutomationSuppressionAdded'
                WHEN LOWER(event_type)='emailunsubscribe' THEN 'emailUnSubscribe'
                ELSE event_type END,
           'Email',
           COALESCE(TRIM(message_id,''), 'unknown'),
           COALESCE(CAST(campaign_id AS STRING), 'unknown'),
           COALESCE(CAST(journey_id AS STRING), 'unknown')) AS event_key,
    event_type, 'Email' AS event_group, email, event_id, created_ts, created_dt AS created_at,
    CAST(NULL AS INT64) AS content_id, campaign_id, campaign_name,
    CAST(NULL AS INT64) AS template_id, CAST(NULL AS STRING) AS template_name,
    message_id, email_subject, journey_id, CAST(NULL AS STRING) AS journey_name,
    CAST(NULL AS INT64) AS channel_id,
    CASE WHEN campaign_name IS NOT NULL OR flow IS NOT NULL THEN 'Marketing' END AS channel_name,
    CAST(NULL AS STRING) AS message_type_id, message_type_name,
    (campaign_name IS NOT NULL OR flow IS NOT NULL) AS is_marketing_message_id,
    CAST(NULL AS STRING) AS experiment_id, country, city, region, signup_source, unsub_source,
    CAST(NULL AS STRING) AS channel_ids, email_list_ids, CAST(NULL AS STRING) AS message_type_ids
  FROM {{ ref('src_klaviyo_events') }}
  WHERE 1=1
    {% if is_incremental() %} AND created_dt IN ({{ partitions_to_replace | join(', ') }}) {% endif %}
    AND created_ts >= DATETIME('2025-09-29')          -- migration date
),
prior_esp_src AS ( ... FROM src_prior_esp_events ... WHERE created_ts < DATETIME('2025-09-29') ),
sms_src AS ( ... FROM src_sms_events ... 'SMS' AS event_group ... ),
all_events AS (SELECT * FROM klaviyo_src UNION ALL SELECT * FROM prior_esp_src UNION ALL SELECT * FROM sms_src)
SELECT * REPLACE (
  CASE WHEN country IN ('US','USA') THEN 'United States' WHEN country IN ('PR','PRI') THEN 'Puerto Rico'
       WHEN country='CA' THEN 'Canada' WHEN country='GB' THEN 'United Kingdom' WHEN country='AU' THEN 'Australia'
       WHEN country='None' OR TRIM(country)='' THEN NULL ELSE country END AS country)  -- + ~10 normalizaciones mas
FROM all_events
```

Detalle clave: en Klaviyo el evento "send" es `emailReceived`; en el ESP anterior era `emailSend`. Downstream se tratan ambos como send.

### 3.3 `xa_marketing_email_action` (incremental, particion `action_dt`, misma ventana de 7 dias)

Agregado a grano `analytics_key × hora × message_type × campaign × template × event_group × journey`. Joinea `fct_marketing_email` con `xa_user_email` (user_type, order_market, sales_channel) y deriva la taxonomia vieja por regex sobre `campaign_name`:

```sql
SELECT
  u.analytics_key, e.account, DATE(e.created_ts) AS action_dt,
  TIMESTAMP_TRUNC(e.created_ts, HOUR) AS action_hr,
  e.email, u.user_type, COALESCE(u.order_market, e.country) AS order_market,
  u.order_sales_channel,
  e.channel_name, e.message_id, e.message_type_name, e.journey_id, e.campaign_name, e.event_group,
  CASE WHEN LOWER(e.campaign_name) LIKE '%survey%' OR LOWER(e.campaign_name) LIKE '%nps%' THEN 'Survey'
       WHEN e.journey_id IS NOT NULL THEN 'Automation'
       WHEN LOWER(e.campaign_name) LIKE '%cart%' OR ... LIKE '%welcome%' OR ... LIKE '%abandon%' THEN 'Automation'
       WHEN LOWER(e.campaign_name) LIKE '%retail%' THEN 'Retail'
       WHEN LOWER(e.campaign_name) LIKE '%bfcm%' THEN 'BFCM'
       WHEN LOWER(e.campaign_name) LIKE '%blast%' OR e.campaign_name LIKE '%BAU%' THEN 'Blast'
       ELSE 'Other' END AS campaign_type,
  CASE WHEN e.event_group='SMS'
       THEN REGEXP_REPLACE(REPLACE(REPLACE(LOWER(e.campaign_id),'|','%7c'),'/','%2f'), '[+]|%20', ' ')
       ELSE CONCAT('campaign_', e.campaign_id) END AS campaign_id,   -- matchea dim_digital_session.campaign
  COUNT(DISTINCT e.email) AS unique_email_count,
  COUNTIF(e.event_type IN ('emailSend','pushSend','smsSend','emailReceived')) AS send_count,
  COUNTIF(e.event_type IN ('emailOpen','pushOpen','smsOpen')) AS open_count,
  COUNTIF(f.event_type IN ('emailOpen','pushOpen','smsOpen')) AS unique_open_count,  -- f = first event per email/day
  COUNTIF(e.event_type IN ('emailClick','pushClick','smsClick')) AS click_count,
  COUNTIF(e.event_type IN ('emailBounce','pushBounce','smsBounce')) AS bounce_count,
  COUNTIF(e.event_type IN ('emailUnSubscribe','pushUnSubscribe','smsUnSubscribe')) AS unsubscribe_count,
  ...
FROM stg_email_marketing e
LEFT JOIN xa_user_email u ON u.email = e.email
LEFT JOIN first_event_per_email f ON ...
GROUP BY ...
```

### 3.4 `xa_crm_email_attribution` (table, rebuild completo, ~120 dias + LY)

Atribuye ordenes de `xa_order` a sends por email con 3 logicas. El "send" es `emailReceived` para Klaviyo y `emailSend` para el ESP anterior; `smsSend` para el proveedor de SMS.

```sql
stg_klaviyo_emails AS (
  SELECT event_id, email, created_ts, campaign_name, campaign_id, event_group, country, journey_id
  FROM {{ ref('fct_marketing_email') }}, date_ranges dr
  WHERE event_type = 'emailReceived' AND event_group = 'Email'
    AND DATE(created_ts) >= '2025-09-29'
    AND (DATE(created_ts) >= dr.current_start OR DATE(created_ts) BETWEEN dr.ly_start AND dr.ly_end)
),
-- 3 atribuciones, todas por email:
--   gmv_6hr       : orden dentro de 6h del SEND (3 a 360 min)
--   gmv_ct_6h     : orden dentro de 6h del CLICK
--   gmv_ct_session: orden cuya sesion (dim_digital_session) tiene campaign = concat('campaign_', campaign_id)
-- Top 50 campañas por semana por sends; el resto se colapsa en 'Other'.
-- Segmento RFM del usuario desde xa_user_email al momento del rebuild.
```

### 3.5 `xi_marketing_crm_performance` (table, grano semana × event_group × market × user_type × campaign)

Junta engagement (3.3) con revenue (3.4) por `campaign_name`, `market` del lado engagement = cuenta emisora (`Market US` → United States, `Market INC` → Canada, etc.), del lado revenue = `order_market` del comprador. L1/L2/send_type via macro `get_crm_taxonomy` (flow si `journey_id IS NOT NULL`). Unsubs (que llegan con `campaign_name` NULL) se prorratean por sends dentro de cada celda. Target NMV semanal de `xi_crm_targets` repartido 85/10/5 Commercial/Brand/Service.

### 3.6 `src_klaviyo_short_nps` → `xi_cx_nps`

```sql
SELECT email, SAFE_CAST(google_rating AS INT64) AS nps_score, CAST(ingested_at AS DATE) AS response_dt,
       'United States' AS market
FROM `silver-project.klaviyo.google_rating_us` WHERE google_rating IS NOT NULL
UNION ALL ... google_rating_inc ('Canada') ... google_rating_uk ('United Kingdom') ...
google_rating_aus ('Australia')
```

`xi_cx_nps` cruza esto con los sends del flow de NPS en `src_klaviyo_events` para encontrar la orden de contexto.

### 3.7 Otros consumidores

- `xf_marketing_email_sign_up`: primer `emailSubscribe` marketing por email, excluyendo emails que ya existian en Klaviyo (migrados del ESP anterior).
- `stg_digital_identity_enhanced`: usa `emailClick` (email + IP) y `profile_id` → email como anchors de identidad, mas las tablas `silver-project.klaviyo.identity.*` (pipeline `klaviyo_identity`, cuyo codigo no esta en `<dagster-repo>`).
- `get_attr_channel`: `utm_source LIKE '%klaviyo%'` → canal `email`.

## 4. Invariantes y trampas para quien lo reconstruya

1. **Idempotencia por doble dedup:** `event_id` en el MERGE de Dagster, `uuid` en `src_klaviyo_events`. El overlap depende de esto.
2. **Todo STRING en silver.** El tipado ocurre en dbt. Una property nueva de Klaviyo no aparece sola: el `reindex` la descarta hasta que alguien agregue la columna al target.
3. **Tablas sin particion ni cluster.** Cualquier query directa a `events_us` es full scan. Filtrar por `datetime` no prunea.
4. `metric_id` es por cuenta y el mapeo vive en dbt, no en Dagster. Agregar una cuenta o metrica implica tocar YAML + `src_klaviyo_events`.
5. **Perdida silenciosa:** si los 90 min se agotan, el run termina verde con `log.error`. La cobertura incompleta solo se detecta con tests dbt (`assert_klaviyo_campaign_sends_vs_openers`, `assert_fct_marketing_email_volume_drop`).
6. **Corte historico fijo `2025-09-29`** del ESP anterior → Klaviyo/SMS, en `fct_marketing_email`, `xa_crm_email_attribution`, `xf_marketing_email_sign_up`.
7. **Send = `emailReceived`** en Klaviyo. Cualquier metrica de rate lo usa como denominador.
8. `google_rating.ingested_at` no es fecha de respuesta, es fecha de cambio en BQ.
9. **Traits:** `updated_at` es de Shopify, no de dbt. Un rebuild del modelo no re-manda a nadie; solo se re-manda quien fue tocado en Shopify en la ventana.
10. **El readme del code location esta desactualizado** (dice 6h/12h Toronto). La fuente de verdad es `scheduler.py`.
11. **La propagación GDPR Shopify→Klaviyo NO existe en este blueprint ni en la integración nativa.** Klaviyo documenta que el borrado de perfiles no se sincroniza entre Shopify y Klaviyo en ninguna dirección. El job de §2.2 solo hace higiene interna (borra perfiles de Klaviyo sin email, ruido POS/SMS, vía un segmento de Klaviyo). Para compliance GDPR real habría que detectar los erasure de Shopify (clientes borrados/redacted) y emitir un `data-privacy-deletion-job` por `email` o `profile_id`; este pipeline no lo hace.
