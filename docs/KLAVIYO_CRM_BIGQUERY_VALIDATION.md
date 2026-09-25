# Validación CRM en BigQuery — 2026-09-24

Validación inicial: `87abd9b`; revalidación con cruce por email sin condición de tienda, PR #4. Proyecto `commerce-agents-dev`, dataset
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
| Órdenes atribuidas por entrega / clic | 6 / 4 (reglas alternativas) |
| Valor atribuido por entrega / clic | USD 1.279,10 / USD 632,10 |
| Identidades CRM del snapshot | 4.731 Customer + 6.401 Prospect + 3 Unresolved |

El mart de campañas coincide con los contadores recalculados desde la tabla de
engagement. Activity cuenta eventos por fecha del evento; engagement sólo asocia
interacciones a una entrega precedente disponible. No deben igualarse a la fuerza:
la captura parcial puede omitir entregas anteriores.

## Cruce entre CRM y Shopify

La primera ejecución no encontró compras porque CRM usa `klaviyo-main` y Shopify
`gid://shopify/Shop/12345794`. Por instrucción del usuario se eliminó la igualdad
de tienda de los cruces entre proveedores. Se conserva el email normalizado e
inequívoco; las compras/refunds se agregan por identidad antes del join para evitar
multiplicar filas. Los joins internos entre órdenes y refunds conservan su tienda.

La revalidación encuentra 6 órdenes / USD 1.279,10 bajo `delivery_6h`, y 4 órdenes /
USD 632,10 bajo `click_6h`; no se suman. Cada regla conserva las 276 órdenes de
la semana. No hay importes incompletos entre las atribuidas. El snapshot reconoce
4.731 identidades con 8.590 órdenes históricas. La atribución positiva quedó
verificada con datos reales, además de los límites temporales de los fixtures.

Revalidación de BigQuery: 4 vistas reconstruidas y 14 tests de los modelos
modificados y descendientes. Las pruebas locales también verifican coincidencias
entre tiendas distintas y una sola fila por identidad CRM al agregar compras.

Jobs de revalidación semántica:
- Atribución: `2c33d158-cd34-44c2-835f-896624c9d8a3`.
- Clientes: `4ccf30eb-92ff-4db4-bcba-6a8f05534fa6`.

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
