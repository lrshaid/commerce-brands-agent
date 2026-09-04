# Progress — commerce-brands-dev

Fecha de corte documental: **2026-09-04 17:42 UTC** (14:42 Argentina).

Objetivo: implementar la arquitectura acordada **GCP + Dagster OSS + dbt Core**,
desde extracción Shopify hasta modelos analíticos, con observabilidad y control de costos.
**La implementación completa todavía no está terminada.**

Este archivo consolida decisiones, pasos ejecutados, arquitectura del código y pendientes.

> **Estado actual resumido (verificado en vivo antes de este deploy):**
> - Auth GCP OK; VM `dagster-control` RUNNING; Cloud Run Job `dagster-worker` desplegado con digest
>   `sha256:e178bcd6f34a4d3b5f0a60c99c4d81284154e95f60555231d6bdeae7f1265644`.
> - Orders publicado y replay idempotente verificado (309 registros raw).
> - **Refunds YA publicado antes de este deploy**: extracción `refunds-initial-20260904-01`,
>   3 páginas raw, 5 vistas de staging en `analytics` (corrección respecto a cortes anteriores).
> - Returns implementado localmente: 187 tests PASS, `dbt compile` OK, 4 modelos staging
>   (`stg_shopify__return_pages`, `stg_shopify__returns`, `stg_shopify__return_line_items`,
>   `stg_shopify__return_refunds`), job `shopify_returns_ingestion`, 28 assets.
> - **En curso**: build + terraform apply + rollout + aceptación end-to-end de returns.

## 1. Resumen del estado

| Componente | Estado al corte | Evidencia / límite |
|---|---|---|
| Proyecto, facturación e infraestructura base | Desplegados, documentados | Proyecto `commerce-agents-dev`; Terraform |
| Dagster en VM + workers Cloud Run | Operativos, documentados | Pruebas sintéticas, orders y captura de refunds |
| Orders → GCS → raw → dbt | Verificado con la tienda disponible | 101 órdenes + 208 líneas; 17 checks; replay sin duplicados |
| Refunds → captura GCS | Verificado para órdenes sin refunds | 3 páginas: 50/50/1; 0 refunds |
| Refunds → raw BigQuery | **Publicado** | Extracción `refunds-initial-20260904-01`; 3 raw pages |
| Refunds → dbt staging | **5 vistas en analytics** | Desplegado y verificado |
| Returns / exchanges | **En deploy end-to-end** | Código local listo: 187 tests, 28 assets, 4 staging models |
| Intermediate / marts / reports comerciales | Pendientes | No confundir inventario SQL con modelos implementados |
| GA4 / sesiones | Pendientes | Sin export GA4 configurado según el último contexto documentado |
| Alertas de presupuesto | Configuradas | No equivalen a un tope de gasto; entrega de emails no probada |
| Mail semanal de gastos | Parcial | SQL/CLI preparado; export, remitente y delivery pendientes |
| Schedules de datos | Deshabilitados | Falta aceptación y frecuencia acordada |

## 2. Decisiones de arquitectura

1. **Dagster OSS reemplaza a Airflow.** No se utiliza Cloud Composer ni Dagster+.
2. **Dagster vive en una VM de GCP**, dentro de Docker, con PostgreSQL persistente.
3. **Python y dbt corren en Cloud Run Jobs**; las consultas SQL las ejecuta BigQuery.
4. **dbt Core**, no Dataform, administra modelos, dependencias SQL, tests y documentación.
5. Convención: **source → staging → intermediate → marts → reports**.
6. `source()` es la definición de una fuente física, no un modelo `src_`.
7. Los modelos de normalización usan `stg_`. **Raw** es la persistencia original anterior a dbt.
8. Elegir estas capas **no aprobó MetricFlow** ni una suscripción al Semantic Layer hospedado.
9. La VM elegida es **e2-medium, 4 GiB**, no e2-small.
10. Región de implementación: **us-central1**. Un futuro export de GA4 deberá coordinar su ubicación.
11. Presupuesto: **USD 100 mensuales antes de créditos promocionales**, no antes de impuestos.
12. No agregar reglas financieras, matching de eventos o deduplicación de negocio para hacer pasar tests.

## 3. Arquitectura GCP

```text
Administración por IAP / SSH
        │
        ▼
Compute Engine: dagster-control (Docker)
  ├─ webserver — interfaz y API de Dagster
  ├─ daemon — cola, monitoreo y futura programación
  ├─ code-location — definiciones de assets/jobs
  └─ PostgreSQL — historial, eventos y estado persistente
        │ lanza y sigue ejecuciones
        ▼
Cloud Run Jobs: dagster-worker
  ├─ Python → Shopify Admin GraphQL
  ├─ GCS → respuestas/archivos originales inmutables
  ├─ BigQuery → carga raw + manifiesto de publicación
  └─ dbt Core → modelos SQL y checks nativos en Dagster
                   │
                   ▼
               BigQuery

Servicios transversales:
  Cloud Build → Artifact Registry → imágenes fijadas por digest
  Secret Manager + service accounts → credenciales / permisos
  Cloud Logging + Monitoring → logs, heartbeat y alertas
  GCS → artefactos dbt, backups y estado Terraform
```

### Recursos y límites

| Recurso | Configuración |
|---|---|
| Proyecto | `commerce-agents-dev`, número `448325654721`, organización `clicar.studio` |
| Proyecto anterior | `commerce-agentes-dev`, marcado para eliminación por pedido del usuario |
| VM | `dagster-control`, `us-central1-a`, e2-medium |
| PostgreSQL | Puerto privado `10.42.0.10:5432`; disco persistente de 30 GiB |
| UI | Bind a `127.0.0.1:3000`; acceso administrativo por túnel IAP |
| Red | VPC privada; PostgreSQL permitido solo desde tag `dagster-worker` |
| Worker | `dagster-worker`, 2 CPU, 4 GiB, una tarea, paralelismo 1 |
| Red del worker | Direct VPC egress, `PRIVATE_RANGES_ONLY` |
| Concurrencia Dagster | Una ejecución activa en la cola configurada |
| Límite del worker | 1.800 segundos; sin retries automáticos de Cloud Run |
| Arranque Dagster | 600 segundos después del ajuste por conexión privada |
| Readiness PostgreSQL | Intentos de conexión de 5 segundos, espera acotada a 180 segundos |
| Conexión DB de Dagster | `connect_timeout: 10` |
| RAM Docker | PostgreSQL 768 MiB; code-location 900 MiB; webserver 800 MiB; daemon 750 MiB |
| CPU Docker | 0,5 CPU por contenedor configurado |
| Datasets | `raw_shopify`, `cfg`, `analytics`, `platform_smoke`, `billing_export` |
| Buckets | Prefijo `commerce-agents-dev-`: landing, artifacts, backups, builds, tfstate |
| Terraform remoto | `gs://commerce-agents-dev-tfstate/platform`, con versionado |

Identidades separadas para control, worker, builds y reporte de costos.
El token Shopify se inyecta desde Secret Manager en el worker; no se copia al repositorio.
El acceso de un plugin interactivo no sustituye una credencial de ejecución remota.

### Imágenes y precisión del estado

Última imagen con captura de refunds exitosa documentada:

`refunds-20260904-02` — `sha256:9e679356288fae92f0475d5d288388fc90087006de137792d769b13d38f67366`.

Al leer `infra/terraform/deployment.auto.tfvars`, la imagen deseada ya era:

`sha256:9ddbfbc0c7060b6f421df659c252b52eaf1424194d7e61cd15aea2107c8dc4a5`.

Esto señala trabajo posterior/en curso: **un digest en tfvars no prueba que el despliegue,
la publicación raw ni la ejecución dbt hayan terminado**. Actualizar esta sección únicamente
con evidencia del build, apply, rollout y ejecución correspondientes.

## 4. Arquitectura del código

```text
queries/shopify/                  Operaciones GraphQL originales
agent/warehouse/                 Transporte, captura, validación y publicación
orchestration/                   Assets, jobs y puente Dagster ↔ dbt
dbt/
  models/staging/shopify/        Orders y colecciones hijas
  models/staging/refunds/        Páginas, refunds, líneas, transacciones y ajustes
  models/staging/platform/       Fixtures de aceptación técnica
  models/reports/platform/       Reporte sintético, no reporte comercial
  macros/                       Convenciones de schema
  tests/                        Tests SQL de publicación y relaciones
warehouse/contracts/            Granos, envelopes, transportes y decisiones abiertas
config/                         Esquemas y templates de configuración
infra/
  terraform/                    Recursos, permisos y referencias a imágenes
  runtime/                      Docker, Compose, Dagster, readiness, health y backups
  scripts/                      Lanzamiento, inspección, probes y verificadores
  cost_report/                  Consulta y CLI del reporte de gastos
tests/                          Tests Python y de configuración
docs/                           Diseño, conexión, contrato y evidencia de despliegue
semantic/                       Catálogo heredado; no es implementación MetricFlow
```

Las carpetas comerciales `intermediate`, `marts` y `reports` son parte del diseño objetivo;
no se consideran implementadas por existir SQL de referencia bajo `warehouse/`.

### Responsabilidad de módulos

| Módulo | Responsabilidad |
|---|---|
| `shopify_bulk.py` | Identidad de tienda, consulta orders, envío Bulk con intención durable y operación exacta |
| `shopify_export.py` | Espera, descarga acotada, validación de conteos y parentesco del archivo Bulk |
| `raw_records.py` | Envelope JSONL; texto original, hash, IDs opcionales, validación estricta |
| `raw_landing.py` | GCS inmutable; verifica contenido y generación en reejecuciones |
| `raw_publication.py` | Carga temporal y transacción BigQuery para raw + manifiesto |
| `refund_queries.py` | Divide la proyección original en consultas paginadas independientes |
| `refund_capture.py` | Captura exacta, cursores, límites, checksums, reanudación y sello final |
| `refund_raw.py` | Revalida la captura sin llamadas API/escrituras GCS antes de exponer filas raw |
| `orchestration/definitions.py` | Ensambla assets, recursos, jobs y límites de ejecución |
| `orchestration/shopify_dbt.py` | Eventos nativos de modelos/tests y conservación de artefactos, aun ante fallos |
| `wait_for_postgres.py` | Readiness del worker con reintentos acotados y logs sin credenciales |

### Flujo de orders

1. Validar `extraction_id`, shop esperado y ventana temporal explícita.
2. Autenticar la tienda y verificar su GID/dominio.
3. Registrar intención durable y enviar la operación Bulk una sola vez.
4. Consultar esa operación exacta hasta su finalización.
5. Descargar sin exponer URLs firmadas ni enviar credenciales al host de descarga.
6. Validar archivo completo, conteos del proveedor, IDs y relaciones padre/hijo.
7. Conservar JSONL original en GCS con generación y checksum.
8. Cargar registros a una tabla temporal BigQuery.
9. Publicar raw y manifiesto atómicamente; rechazar contenido conflictivo en replay.
10. Ejecutar cinco modelos staging y sus 17 checks desde Dagster.
11. Conservar logs, resultados y manifest de dbt por ejecución.

Modelos: `stg_shopify__order_records`, `orders`, `order_line_items`,
`order_shipping_lines`, `order_discount_applications` (todos con prefijo `stg_shopify__`).
Son observaciones versionadas; todavía no una reconstrucción de estado actual ni métricas financieras.

### Flujo de refunds

1. Usar ventana explícita, identidad estable y tienda autenticada.
2. Recorrer órdenes con páginas de **50**; leer la lista de refunds de cada orden.
3. Para cada refund, recorrer por separado `refundLineItems`, `transactions` y `orderAdjustments`.
4. Continuar con `endCursor` mientras `hasNextPage=true`.
5. Rechazar conexiones faltantes, errores GraphQL, cursores repetidos/no válidos y propietarios incorrectos.
6. Guardar cada respuesta HTTP original en GCS, con consulta/variables/hash/timestamp asociados.
7. Escribir `complete.json` únicamente al terminar todos los recorridos.
8. Antes de publicar, reproducir la validación sobre los archivos guardados en modo solo lectura.
9. Representar **una respuesta HTTP completa por fila raw**, no inventar un objeto refund reconstruido.
10. Conservar en el manifiesto la operación, parámetros, cursor, archivo, generación y checksum de cada página.
11. Publicar raw + manifiesto mediante la transacción compartida.
12. Ejecutar los cinco modelos staging y sus tests nativos.

**Verificado remotamente hasta el paso 7; paso 8 verificado en lectura sobre la captura real.
Pasos 9–12 presentes en el código, sin aceptación remota confirmada en este corte.**

Modelos presentes: `stg_shopify__refund_pages`, `stg_shopify__refunds`,
`stg_shopify__refund_line_items`, `stg_shopify__refund_transactions`,
`stg_shopify__refund_adjustments`.

Reglas de interpretación:

- Tres páginas raw pueden contener 101 órdenes y cero refunds: son conteos de granos diferentes.
- `complete.json` acredita el recorrido de la consulta accesible; no garantiza permisos sobre toda la tienda.
- Varias respuestas no forman una instantánea transaccional única de Shopify.
- Los IDs físicos de observación no sustituyen IDs de negocio faltantes.
- La proyección actual tiene campos financieros/identificadores incompletos; no se inventan moneda, impuestos o enlaces.
- Se rechazan colisiones de generaciones bajo la clave física actual antes de publicar.

### Jobs presentes en el código

| Job | Selección |
|---|---|
| `platform_acceptance` | Captura/publicación sintética + modelos y checks de prueba |
| `shopify_orders_ingestion` | Orders raw/manifiesto + staging orders |
| `shopify_refunds_capture` | Solo captura GCS de refunds |
| `shopify_refunds_ingestion` | Captura + raw/manifiesto + staging refunds; aceptación remota pendiente de confirmar |

## 5. Registro de pasos realizados

Los resultados detallados, correcciones y handles completos permanecen en
[docs/DEPLOYMENT_STATUS.md](docs/DEPLOYMENT_STATUS.md). Una entrada histórica STARTING
queda superada solo por un resultado posterior de esa misma ejecución.

| Paso | Trabajo | Resultado documentado |
|---|---|---|
| 1 | Corregir ID del proyecto y vincular facturación | `commerce-agents-dev` activo; anterior marcado para eliminación |
| 2 | Definir región, VM, presupuesto y arquitectura | us-central1, e2-medium, Dagster + Cloud Run + dbt |
| 3 | Desplegar red, storage, datasets, identidades, secretos y registry | Fundación Terraform desplegada |
| 4 | Migrar estado Terraform a GCS versionado | Backend remoto configurado |
| 5 | Construir runtime y corregir requirements/log routing | Build exitoso después de fallos iniciales documentados |
| 6 | Levantar VM, PostgreSQL, servicios Dagster y Cloud Run | Contenedores y acceso IAP verificados |
| 7 | Corregir permiso de metadatos del bucket para compute logs | Fallo inicial detectado; permiso acotado al bucket |
| 8 | Probar ejecución sintética exitosa | 3 materializaciones y 5 checks |
| 9 | Inyectar fallo dbt intencional | Fallo/test visibles y artefactos conservados |
| 10 | Respaldar y restaurar PostgreSQL en DB aislada | Conteos originales/restaurados: 1 run, 21 eventos |
| 11 | Probar cancelación | Worker cancelado durante pausa pre-dbt; no prueba SQL activo |
| 12 | Reiniciar servicios de control durante un worker | Continuidad exitosa; no prueba caída de VM/DB |
| 13 | Implementar parser raw y landing inmutable | Reutilización del archivo y rechazo de contenido distinto |
| 14 | Probar publicación atómica y replay | Sin duplicados; conflicto rechazado; rollback preservado |
| 15 | Integrar aceptación raw → dbt bajo identidad del worker | 6 assets, 8 checks y artefactos verificados |
| 16 | Evitar reemplazo accidental de VM en Terraform | Reimport de la misma VM, state backup y `prevent_destroy` |
| 17 | Probar extracción vacía | Cero filas, un manifiesto; replay sin duplicados |
| 18 | Probar timeout del job | Fallo/cancelación en pausa controlada; no prueba límite de SQL activo |
| 19 | Conectar secreto Shopify y verificar tienda/scopes | Worker autenticado; token no expuesto |
| 20 | Implementar y desplegar orders completo | 101 órdenes + 208 líneas = 309 registros |
| 21 | Exponer los dos tests SQL como checks nativos | 17 checks visibles en Dagster |
| 22 | Repetir la extracción orders | Misma operación Bulk, 309 registros y un manifiesto |
| 23 | Implementar paginación/captura refunds | Tests simulados y validación de consultas |
| 24 | Desplegar captura refunds; primer intento | START_TIMEOUT; sin archivos GCS |
| 25 | Agregar readiness PostgreSQL y ajustar margen de arranque | Reintentos acotados, timeout DB y logs explícitos |
| 26 | Reintentar captura refunds | SUCCESS, 101 órdenes en 50/50/1, cero refunds |
| 27 | Revalidar captura real y preparar adaptador raw | 3 páginas / 12.605 bytes, sin escrituras BigQuery en esa prueba |
| 28 | Implementar asset de publicación y staging refunds | Código presente; cierre remoto pendiente de confirmar |
| 29 | Preparar consulta de costos y validación sintética | SQL/CLI existente; entrega semanal aún no operativa |
| 30 | Guardar checkpoint Git | `d336f66` pusheado; hay cambios posteriores sin commit |

## 6. Evidencia de las ejecuciones principales

| Prueba | Dagster run | Resultado |
|---|---|---|
| Orders inicial | `35cfc82f-5bfc-4a54-a2d2-d21e0fdf2e6c` | SUCCESS; 101 órdenes, 208 líneas |
| Orders replay | `b4d68d1e-a449-45a5-b812-007b37a6426e` | SUCCESS; 17 checks; 309 registros sin duplicar |
| Refunds primer intento | `91df7779-48a7-4f24-98ca-507f23a599ce` | FAILURE / START_TIMEOUT; Cloud Run exit 0 no fue éxito del pipeline |
| Refunds captura tras ajuste | `47a88b5a-f6a0-485f-b59d-e4dcffc2fa41` | SUCCESS; worker `dagster-worker-sqw5w` |

Orders: extracción `orders-initial-20260904-01`, operación
`gid://shopify/BulkOperation/8002564980949`, ventana hasta `2026-09-04T11:50:00Z` desde 1970.

Refunds: extracción `refunds-initial-20260904-01`, ventana
`[1970-01-01T00:00:00Z, 2026-09-04T13:05:00Z)`.
Sello GCS generación `1788528413896780` bajo:

`gs://commerce-agents-dev-landing/pages/v1/order_refunds/d4e3a81c5ad0ea0363c6e66d0128d7e5b2c1ba4a7c80872f60ddd0ee7870eb7b/complete.json`.

La conexión PostgreSQL del reintento respondió en el intento 14, a los 130,1 segundos.
Cloud Run terminó exitosamente a `2026-09-04T13:27:01.136942Z`.
Eso demuestra tolerancia de la demora observada, no su eliminación.

Los tests locales fueron creciendo por hitos: 90 con conexión, 113 con orders local,
128 con captura local, 136 con hardening y 141 con preparación raw.
No se volvió a ejecutar la suite para escribir este documento; no usar ese último número
como evidencia de cambios posteriores. Los 23 checks de staging refunds pertenecen a
la implementación local hasta que una ejecución desplegada los confirme.

## 7. Observabilidad, seguridad y recuperación

- Resultado autoritativo: estado del run, pasos y checks de Dagster más verificación de datos.
  Un build exitoso, un envío aceptado o un exit 0 de Cloud Run no bastan.
- Trazabilidad: job/run/step/attempt, extracción, tienda, ventana, ejecución Cloud Run,
  versión de código, archivos y jobs BigQuery.
- Artefactos: `manifest.json`, `run_results.json`, `dbt.log` y otros resultados disponibles,
  bajo `gs://commerce-agents-dev-artifacts/dbt/<run>/...`.
- Logs de cómputo en GCS; logs de infraestructura en Cloud Logging.
- Healthchecks/heartbeat y políticas de alerta configurados; recepción de alertas aún no probada.
- Datos originales y credenciales nunca en logs o fixtures versionados.
- Raw solo visible a consumidores con manifiesto publicado; no avanzar watermark ante captura parcial.
- Reintentar la misma extracción conserva identidad; una nueva exportación no es un replay de la anterior.
- No relanzar por un timeout de observación: consultar el handle existente y confirmar terminalidad.
- Backups PostgreSQL y restore aislado probados; falta recuperación integral ante pérdida de VM/DB.
- Faltan pruebas de contención entre publicadores concurrentes y cancelación de consultas BigQuery activas.

## 8. Costos y mail semanal

- Presupuesto mensual: USD 100, proyecto específico, umbrales 50/80/100%.
- Destino: `lauti@clicar.studio`.
- Base presupuestaria excluye PROMOTION e incluye free tier y descuentos ordinarios configurados.
- No es un mecanismo de corte automático del gasto.
- Mail acordado: lunes 09:00, `America/Argentina/Buenos_Aires`.
- Reporte separado de Dagster: diseño con Cloud Run + Cloud Scheduler.
- SQL/CLI en `infra/cost_report/`: mes acumulado, semana anterior, costo antes de promoción y neto.
- Aritmética Decimal/NUMERIC; créditos sin multiplicar costos; datos faltantes no se presentan como gasto cero.
- Pendiente: habilitar/verificar export real de Billing, autorizar remitente, deduplicar envíos y probar delivery.
- Crear el dataset `billing_export` no habilita por sí solo la exportación.

## 9. Próximos pasos y criterios de cierre

1. **Refunds raw + staging:** confirmar build/apply/rollout; publicar captura existente;
   verificar 3 páginas raw, un manifiesto, modelos/checks y artefactos. Repetir sin duplicados.
2. **Fixtures no vacíos:** validar líneas, transacciones, ajustes y enlaces; distinguir
   SQL probado con datos sintéticos de API real probada con esos casos.
3. **Returns:** transporte, paginación, IDs, carga y staging con pruebas de completitud.
4. **Exchanges:** reemplazar consulta incompatible `exchangeV2s` y definir enlaces auténticos.
5. **Actualización incremental:** cobertura de cambios en hijos, ventanas/checkpoints,
   reintentos, cuarentena y recuperación de archivos cuando expire una URL firmada.
6. **Modelos de negocio:** acordar matching parcial/repetido, reconocimiento temporal,
   ajustes, exclusiones, monedas, taxonomías y scope de mappings. Implementar `int_`, facts/dims y reports.
7. **Clientes / inventario / otros streams:** confirmar claves, historia, grain y política de replay.
8. **GA4 / sesiones:** configurar export futuro y contrato de eventos/identidad; los extractos SQL
   de contexto no equivalen a un pipeline GA4 implementado.
9. **Operación:** completar recuperación, concurrencia, alertas y cancellation tests faltantes.
10. **Gastos:** export + remitente + delivery probado; recién después activar mail semanal.
11. **Schedules:** acordar cadencia y habilitar solo después de aceptación; cuatro veces por día
    fue una propuesta, no una frecuencia aprobada.

Las decisiones abiertas completas están en [warehouse/contracts/decisions.yaml](warehouse/contracts/decisions.yaml).
El cierre del objetivo requiere evidencia de estos componentes, no solo infraestructura o tests locales verdes.

## 10. Procedimiento de continuidad

1. Leer este resumen, `GAPS.md` y la última evidencia de despliegue.
2. Revisar el worktree y distinguir cambios locales de la imagen efectivamente desplegada.
3. Identificar builds/runs existentes; no lanzar duplicados mientras sigan activos.
4. Probar código/configuración y validar SQL con alcance y costo acotados.
5. Construir imagen versionada; registrar build y digest.
6. Revisar plan Terraform antes de aplicar; no aceptar reemplazo de VM/discos por rutina.
7. Respaldar PostgreSQL, desplegar contenedores y verificar salud y ausencia de drift.
8. Ejecutar una extracción explícita; registrar run y worker.
9. Verificar datos, checks, artefactos y replay, no solo estado de proceso.
10. Actualizar este documento con fecha, evidencia y pendientes. Commit/push solo por instrucción explícita.

### Documentos de referencia

- [Arquitectura](docs/ARCHITECTURE.md)
- [Evidencia cronológica de despliegue](docs/DEPLOYMENT_STATUS.md)
- [Gaps y limitaciones](GAPS.md)
- [Conexión Shopify](docs/SHOPIFY_CONNECTION.md)
- [Contrato raw Shopify](docs/SHOPIFY_RAW_CONTRACT.md)
- [Envelope raw](warehouse/contracts/shopify_raw_v1.yaml)
- [Contrato de páginas refunds](warehouse/contracts/refund_pages_v1.yaml)
- [Reporte de gastos](infra/cost_report/README.md)

Algunas referencias contienen frases históricas como «no desplegado» o «en ejecución».
Resolverlas por fecha y evidencia posterior, no por la primera coincidencia textual.
