# Validación CRM en BigQuery — 2026-09-24

Código probado: `87abd9b`, PR #4. Proyecto `commerce-agents-dev`, dataset
`analytics`, región `us-central1`. No se ejecutó ingesta ni backfill.

## Ejecución

```bash
.venv-platform/bin/dbt run --project-dir dbt --profiles-dir dbt --select tag:crm
.venv-platform/bin/dbt test --project-dir dbt --profiles-dir dbt --select tag:crm
```

- 12/12 vistas creadas correctamente; CREATE VIEW procesó cero bytes.
- 26/26 tests pasaron contra los datos existentes: claves, nulos, cobertura de
  órdenes por regla, ventanas de atribución, contadores e identidad ambigua.
- Límite de facturación por consulta: 1 GiB.
- Agregaciones temporales: `[2026-09-04 00:00:00 UTC, 2026-09-11 00:00:00 UTC)`.
  La semana 17–24 no tiene eventos. La fuente cargada contiene eventos desde
  septiembre 8 a las 14:00 hasta septiembre 10 a las 13:58:57 UTC: no es una
  semana completa ni permite inferir la performance de todo septiembre.
- Los tests de integridad y el snapshot de clientes usan las relaciones actuales
  completas; no se limitan artificialmente a una semana.

## Resultados y reconciliación

| Control | Resultado |
| --- | --- |
| Observaciones fuente / eventos únicos del proveedor / eventos en activity | 20.163 / 20.163 / 20.163 |
| Entregas / entregas con apertura / entregas con clic | 10.618 / 4.115 / 629 |
| Open rate / CTR / CTOR | 38,7549% / 5,9239% / 15,2855% |
| Entregas no vinculables | 0 |
| Eventos de apertura / clic asociados a entregas | 4.947 / 1.276 |
| Eventos de apertura / clic en activity | 6.530 / 1.295 |
| Órdenes en la semana por regla de atribución | 276 |
| Órdenes atribuidas por regla | 0 |
| Identidades CRM del snapshot | 11.132 Prospect + 3 Unresolved |

El mart de campañas coincide con los contadores recalculados desde la tabla de
engagement. Activity cuenta eventos por fecha del evento; engagement sólo asocia
interacciones a una entrega precedente disponible. No deben igualarse a la fuerza:
la captura parcial puede omitir entregas anteriores.

## Bloqueo funcional de atribución e identificación de clientes

La fuente CRM usa `shop_key = klaviyo-main`; Shopify usa
`shop_key = gid://shopify/Shop/12345794`. No hay identidades que coincidan en
**tienda e identidad**. Por eso las órdenes quedan `no_eligible_touch` y las
identidades resueltas aparecen como Prospect. Estos resultados NO validan que
el negocio tenga cero conversiones ni que esas personas nunca hayan comprado.
Se requiere confirmar la relación cuenta Klaviyo–tienda Shopify y configurar
una clave canónica antes de validar conversiones positivas con datos reales.
No se eliminó el filtro por tienda ni se asumió esa relación automáticamente.
Los fixtures locales cubren atribuciones positivas y límites temporales.

## Alcance de Cube

Se ejecutaron contra BigQuery las expresiones de agregación obtenidas de los
bindings generados de las cuatro vistas CRM. Las seis consultas de métricas y
reconciliación procesaron 385.012.774 bytes en total. Jobs de referencia:

- Campañas: `a6bb3dac-44c2-4a72-a5fa-35e842903a2b`.
- Activity: `1346da3a-7e75-415e-846b-4fa456b8a5b9`.
- Atribución: `185990cd-bbc1-4422-8873-6e261306ac0b`.
- Clientes: `79253e8d-fdcb-4531-82c8-a1b7fcb2668d`.
- Reconciliación entregas: `0c42ae15-0eb2-4e3f-9ec0-1b96f9a2d31e`.
- Reconciliación fuente: `c2cf6b16-5b18-490a-ac60-c8f601444e8f`.

Esto valida SQL y datos en BigQuery; no una petición HTTP JSON al runtime de Cube.
Docker no está disponible en esta máquina. Compilación/runtime, autenticación
y aislamiento por tienda del servicio Cube siguen pendientes.
