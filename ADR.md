# Architecture Decision Records — FacturaFlow

> Registro formal de decisiones arquitectónicas tomadas durante el diseño del sistema SaaS B2B FacturaFlow.
> Cada ADR documenta el contexto, la decisión adoptada, sus consecuencias y su vínculo con el Utility Tree del proyecto.

---

## ADR-001: Patrón Serverless / FaaS sobre AWS

**Estado:** Aceptado

### Contexto

FacturaFlow es un MVP que debe estar en producción en 12 semanas con presupuesto de infraestructura de $0. El equipo no puede asumir costos fijos de servidores, y el volumen de facturas es altamente variable: 3.300 facturas diarias en operación normal, pero concentradas al 60% en solo 3 días hábiles durante el cierre de mes (hasta 1.980 facturas en un día). Una arquitectura basada en servidores permanentes implicaría sobredimensionamiento para cubrir los picos o degradación en momentos críticos.

### Decisión

Adoptar una arquitectura **100% Serverless / FaaS (Function-as-a-Service)** sobre AWS Free Tier, implementando la lógica del sistema en cuatro funciones Lambda independientes: `recibir_zip`, `procesar_factura`, `notificar_analista` y `motor_ia_mock`. La infraestructura se aprovisionará y destruirá automáticamente por AWS en función de la demanda real.

### Consecuencias

**Positivas:**
- Costo operativo de $0 dentro de los límites del Free Tier durante el MVP.
- Escalado automático ante los picos de cierre de mes sin intervención manual.
- Despliegue independiente de cada función, reduciendo el radio de impacto de un fallo.
- Time-to-market acelerado: sin configuración ni mantenimiento de servidores.

**Negativas (trade-offs aceptados):**
- Cold starts de Lambda pueden introducir latencia adicional en la primera invocación.
- Límite de 15 minutos de ejecución por invocación Lambda, que obliga a descomponer tareas largas.
- Debugging y observabilidad más complejos que en un monolito.
- Vendor lock-in con el ecosistema AWS.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Disponibilidad (H,H) | AWS gestiona la alta disponibilidad multi-AZ de Lambda sin configuración adicional. |
| Escalabilidad (H,H) | Lambda escala horizontalmente de forma automática para absorber los picos de cierre de mes. |
| Rendimiento (H,M) | La ejecución bajo demanda elimina cuellos de botella por recursos compartidos. |

---

## ADR-002: Queue-Based Load Leveling con AWS SQS

**Estado:** Aceptado

### Contexto

El atributo de calidad dominante del sistema es la **disponibilidad (99.9% uptime en horario laboral durante cierre de mes)**. El analista espera recibir confirmación de la recepción del ZIP en menos de 2 segundos, pero el procesamiento real de cada factura (OCR + validación + integración ERP) puede tomar hasta 15 segundos. Si el sistema procesara de forma síncrona, cualquier demora del motor IA o del ERP del cliente bloquearía la respuesta al usuario y degradaría la experiencia en los momentos de mayor carga. Adicionalmente, la API del ERP del cliente tiene un límite estricto de 5 peticiones por segundo.

### Decisión

Implementar el patrón **Queue-Based Load Leveling** usando **AWS SQS** como intermediario entre la recepción del ZIP y el procesamiento de cada factura. La función `recibir_zip` encola cada factura individualmente en SQS y retorna un ID de lote al analista en menos de 2 segundos. La función `procesar_factura` consume mensajes de la cola de forma asíncrona. Se configura una **Dead Letter Queue (DLQ)** para capturar mensajes que fallen repetidamente.

### Consecuencias

**Positivas:**
- La respuesta al analista se desacopla del tiempo de procesamiento: siempre < 2 segundos.
- El ritmo de consumo de la cola puede regularse para respetar el límite de 5 req/s del ERP.
- La DLQ garantiza que ninguna factura se pierda silenciosamente ante fallos transitorios.
- El sistema mantiene disponibilidad ante picos al absorber la carga en la cola en lugar de rechazarla.

**Negativas (trade-offs aceptados):**
- El analista no recibe el resultado en tiempo real; debe esperar la notificación por correo.
- Complejidad operacional adicional: monitoreo de profundidad de cola y DLQ.
- El orden de procesamiento de facturas dentro de un lote no está garantizado (cola estándar SQS).
- Latencia final mayor comparada con procesamiento síncrono directo.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Disponibilidad (H,H) | El desacoplamiento evita que fallos en el ERP o en el motor IA afecten la recepción de nuevos lotes. |
| Escalabilidad (H,H) | La cola absorbe los picos de 1.980 facturas/día sin saturar el procesamiento concurrente. |
| Rendimiento (H,M) | Garantiza el SLA de ID en < 2 segundos independientemente de la carga del sistema. |

---

## ADR-003: DynamoDB sobre Base de Datos SQL Tradicional

**Estado:** Aceptado

### Contexto

El sistema necesita persistir dos tipos de datos con patrones de acceso muy diferentes: el **estado de las facturas** (lecturas/escrituras frecuentes por ID de lote y por estado) y el **registro de auditoría** (escrituras inmutables de alta frecuencia, lectura eventual para cumplimiento). El presupuesto es $0 y la arquitectura debe permanecer en el Free Tier de AWS. Las bases de datos SQL gestionadas (RDS) tienen costo fijo mensual y requieren gestión de conexiones que es incompatible con el modelo de ejecución efímera de Lambda (cada invocación es stateless). La retención de documentos de auditoría es de mínimo 5 años.

### Decisión

Utilizar **AWS DynamoDB** como única base de datos del sistema, con dos tablas: una para el estado de facturas y otra para el registro de auditoría inmutable. DynamoDB se integra nativamente con Lambda sin pool de conexiones, opera dentro del Free Tier (25 GB, 25 WCU, 25 RCU), y su modelo de escritura append-only es apropiado para el log de auditoría.

### Consecuencias

**Positivas:**
- Sin costo fijo: DynamoDB cobra por operación, no por instancia activa.
- Sin gestión de conexiones: cada Lambda se conecta de forma independiente y stateless.
- Escalado automático de capacidad ante picos de escritura en cierre de mes.
- TTL nativo de DynamoDB puede gestionar la retención de 5 años sin lógica adicional.
- Escrituras atómicas garantizan la inmutabilidad del registro de auditoría.

**Negativas (trade-offs aceptados):**
- Sin soporte para consultas ad hoc complejas con JOINs; los patrones de acceso deben diseñarse al momento del modelado.
- Limitaciones en la consistencia eventual para lecturas fuera de la región primaria.
- Curva de aprendizaje en el diseño de claves de partición para evitar hot partitions.
- Reporting analítico complejo requeriría exportar datos a otra herramienta.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Disponibilidad (H,H) | DynamoDB ofrece SLA de 99.999% en modo multi-región; sin instancias que reiniciar. |
| Seguridad (H,M) | El modelo append-only de la tabla de auditoría garantiza no repudio e inmutabilidad de registros. |
| Escalabilidad (H,H) | Escala horizontalmente sin cambios de código ante el crecimiento a México y Chile. |

---

## ADR-004: Procesamiento Asíncrono vs. Síncrono

**Estado:** Aceptado

### Contexto

Cada factura requiere tres operaciones de alta latencia en secuencia: (1) extracción por OCR/IA (~3-5 segundos simulados por `motor_ia_mock`), (2) validación matemática, y (3) envío al ERP del cliente (sujeto al límite de 5 req/s). Procesar un lote de 1.980 facturas de forma síncrona y secuencial tomaría horas. El procesamiento síncrono en la misma llamada HTTP que recibe el ZIP violaría el SLA de < 2 segundos y agotaría el timeout de API Gateway (29 segundos). La API del motor IA tiene un límite de 10 peticiones concurrentes.

### Decisión

Adoptar un modelo de **procesamiento completamente asíncrono** para el ciclo de vida de cada factura. La función `recibir_zip` desacopla la recepción del procesamiento encolando cada factura en SQS. La función `procesar_factura` es invocada por SQS de forma independiente por cada mensaje, permitiendo hasta 10 ejecuciones paralelas (respetando el límite del motor IA). La función `notificar_analista` cierra el ciclo enviando el resultado por correo electrónico vía SES cuando termina el lote.

### Consecuencias

**Positivas:**
- El analista nunca espera más de 2 segundos para confirmar la recepción.
- Las 10 facturas se procesan en paralelo, reduciendo el tiempo total del lote.
- El sistema puede regular el ritmo de envío al ERP para cumplir el límite de 5 req/s.
- Un fallo en el procesamiento de una factura no bloquea el resto del lote.

**Negativas (trade-offs aceptados):**
- El analista no tiene visibilidad en tiempo real del progreso; depende del correo de notificación.
- La lógica de seguimiento del estado del lote (¿cuántas facturas han terminado?) requiere un contador en DynamoDB.
- Los errores son más difíciles de correlacionar con la solicitud original sin un trace ID explícito.
- La experiencia del usuario es eventual: el resultado puede tardar minutos en llegar.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Rendimiento (H,M) | Garantiza el SLA de ID < 2 segundos; el paralelismo reduce el tiempo de lote. |
| Disponibilidad (H,H) | El aislamiento por factura evita que un fallo en una propague errores a todo el lote. |
| Escalabilidad (H,H) | El paralelismo controlado por SQS permite absorber picos sin saturar las APIs externas. |

---

## ADR-005: Motor IA Mock para el MVP

**Estado:** Aceptado

### Contexto

La funcionalidad central de FacturaFlow depende de un motor de OCR/IA que extraiga campos estructurados (número de factura, proveedor, subtotal, impuesto, gran total, etc.) de documentos PDF. Integrar un motor de IA real (Amazon Textract, Google Document AI, etc.) en el MVP implica: (1) costos que superan el presupuesto de $0, (2) complejidad de integración que alarga el tiempo de desarrollo más allá de las 12 semanas, y (3) variabilidad en los resultados que dificulta las pruebas de las reglas de validación. La arquitectura debe diseñarse para que el motor real pueda reemplazar al mock sin cambios en el resto del sistema.

### Decisión

Implementar **`motor_ia_mock`** como una función Lambda independiente que simula el comportamiento del motor de IA real: recibe el contenido de un PDF, introduce una pausa aleatoria de 3 a 5 segundos (simulando la latencia de OCR), y retorna campos extraídos con un `nivel_confianza` aleatorio entre 0.70 y 0.99. La interfaz del mock es idéntica a la que tendría el motor real, permitiendo su reemplazo transparente en producción.

### Consecuencias

**Positivas:**
- El MVP puede desplegarse y validarse en producción sin costo de IA.
- Las reglas de validación y el flujo completo pueden probarse con datos controlados.
- La latencia simulada de 3-5 segundos permite probar el comportamiento asíncrono bajo condiciones realistas.
- El `nivel_confianza` aleatorio genera escenarios APROBADO y REQUIERE_REVISION para probar ambos flujos.
- Reemplazo futuro por motor real requiere solo cambiar la implementación de `motor_ia_mock`, sin tocar otras funciones.

**Negativas (trade-offs aceptados):**
- El MVP no valida la extracción real de PDFs; la precisión del OCR queda pendiente para la siguiente iteración.
- Los datos de confianza aleatorios no reflejan la distribución real de errores de OCR.
- Si el motor real tiene una interfaz diferente a la asumida, será necesario un adapter.
- El cliente piloto (Constructora Andina S.A.) debe entender que la extracción automática es simulada en esta fase.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Disponibilidad (H,H) | El mock nunca falla por cuota de API externa; elimina una clase de dependencia externa en el MVP. |
| Rendimiento (H,M) | La latencia simulada de 3-5 s permite validar que el sistema cumple el SLA de < 15 seg/factura. |
| Seguridad (H,M) | No se envían documentos de clientes reales a servicios de terceros durante el MVP. |

---

## ADR-006: AES-256 para Almacenamiento de PDFs en S3

**Estado:** Aceptado

### Contexto

Los PDFs de facturas contienen información financiera y fiscal altamente sensible de Constructora Andina S.A. y sus proveedores (RUC/NIT, montos, datos bancarios implícitos). Las regulaciones fiscales de los países objetivo (Colombia, México, Chile) exigen protección de documentos tributarios. El CONTEXT.md establece como restricción innegociable que los "PDFs cifrados con AES-256" y una retención mínima de 5 años. Un incidente de seguridad que exponga facturas de clientes podría terminar con el negocio en su etapa más temprana.

### Decisión

Almacenar todos los archivos PDF en **AWS S3 con cifrado del lado del servidor SSE-S3 (AES-256)**, activado como política obligatoria del bucket mediante una bucket policy que rechace (`Deny`) cualquier operación `PutObject` que no incluya el header `x-amz-server-side-encryption: AES256`. Las funciones Lambda acceden a S3 mediante roles IAM con principio de mínimo privilegio. Los presigned URLs tienen expiración de 15 minutos para acceso temporal de lectura.

### Consecuencias

**Positivas:**
- Cumplimiento de la restricción de negocio innegociable de AES-256 sin costo adicional (SSE-S3 está incluido en S3).
- La bucket policy garantiza que ningún objeto se almacene sin cifrar, incluso ante un error de código.
- AWS gestiona la rotación de claves de cifrado automáticamente.
- Los presigned URLs permiten acceso temporal sin exponer credenciales permanentes al frontend.
- La retención de 5 años se gestiona con S3 Lifecycle Policies sin lógica adicional.

**Negativas (trade-offs aceptados):**
- SSE-S3 cifra en reposo pero los datos viajan cifrados por HTTPS; si se necesita control total de claves se requeriría SSE-KMS (costo adicional fuera del Free Tier).
- Con SSE-S3 AWS tiene acceso técnico a las claves; para sectores regulados como banca se necesitaría SSE-KMS con CMK.
- Los presigned URLs con 15 minutos de expiración pueden generar fricción de UX si el analista tarda en abrir el documento.
- El cifrado no sustituye controles de acceso: IAM y las políticas de bucket deben estar correctamente configurados.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Seguridad (H,M) | Satisface directamente el requisito de AES-256 y protege documentos durante la retención de 5 años. |
| Disponibilidad (H,H) | SSE-S3 no introduce latencia apreciable; S3 mantiene su SLA de 99.99% con cifrado activo. |
| Escalabilidad (H,H) | S3 soporta el crecimiento a México y Chile sin cambios de configuración de cifrado. |

---

## ADR-007: Simulación de Notificaciones con DynamoDB

**Estado:** Aceptado

### Contexto

AWS SES opera en modo **sandbox** por defecto: solo puede enviar correos a direcciones verificadas manualmente en la consola de AWS. Durante el MVP, el equipo de desarrollo necesita probar el flujo completo de notificaciones —incluyendo el contenido del mensaje y el registro del evento— sin depender de que cada dirección de analista esté verificada en SES. Bloquear las pruebas a la espera de la verificación manual de SES introduciría fricción innecesaria en los sprints y podría enmascarar errores de lógica de notificación que solo se descubrirían en producción.

### Decisión

Introducir la variable de entorno **`MODO_SIMULACION`** en las funciones Lambda de notificación. Cuando `MODO_SIMULACION=true`, la función `notificar_analista` **no llama a SES** y en su lugar guarda un registro completo del correo simulado (destinatario, asunto, cuerpo, timestamp) en la tabla **`facturaflow-notificaciones-dev`** de DynamoDB. Cuando `MODO_SIMULACION=false` (producción), el flujo invoca SES normalmente. El código de negocio es idéntico en ambos casos; solo el canal de entrega cambia.

### Consecuencias

**Positivas:**
- El flujo completo de notificación puede probarse y auditarse en desarrollo sin restricciones de sandbox de SES.
- Los registros en DynamoDB permiten inspeccionar el contenido exacto del correo que se habría enviado.
- El interruptor `MODO_SIMULACION` facilita pruebas de integración automatizadas sin mocks de SES.
- El cambio a producción no requiere modificar lógica de negocio; solo actualizar la variable de entorno en `template.yaml`.

**Negativas (trade-offs aceptados):**
- La ruta de código de SES real no se ejercita hasta el despliegue en producción; errores de formato o de permisos IAM de SES pueden quedar latentes.
- La tabla `facturaflow-notificaciones-dev` acumula registros de simulación que deben purgarse manualmente o mediante TTL.
- El equipo puede desarrollar una falsa confianza si solo prueba en modo simulación sin validar el comportamiento real de SES.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Disponibilidad (H,H) | El modo simulación elimina la dependencia de SES sandbox durante el desarrollo, evitando bloqueos por verificación de correos. |
| Seguridad (H,M) | Los correos simulados no salen del entorno AWS; no se expone información financiera a destinatarios externos durante pruebas. |
| Rendimiento (H,M) | Una escritura en DynamoDB (~1-5 ms) reemplaza una llamada a SES (~100-300 ms), acelerando el ciclo de pruebas. |

---

## ADR-008: Soporte Multi-País con Variable de Entorno PAIS

**Estado:** Aceptado

### Contexto

Daniela (Product Owner) estableció como requisito de negocio la expansión a **México y Chile en un plazo de 6 meses** desde el lanzamiento en Colombia. Cada país tiene una estructura fiscal diferente: Colombia aplica IVA del 19% con NIT como identificador tributario; México usa RFC y tasa de IVA del 16%; Chile usa RUT y un IVA del 19% con reglas de retención distintas. Recompilar y redesplegar el sistema para cada país, o mantener ramas de código separadas por país, generaría deuda técnica inmediata y complejidad operacional que el equipo no puede sostener.

### Decisión

Introducir la variable de entorno **`PAIS`** en `template.yaml` con los valores válidos `colombia`, `mexico` y `chile`. Las funciones Lambda leen `PAIS` al inicializarse y seleccionan las reglas fiscales correspondientes (porcentaje de IVA, formato de identificador tributario, nombre del campo en los documentos). El mismo artefacto Lambda se despliega en todos los países; la configuración por país se inyecta como variable de entorno sin recompilación. La expansión a un nuevo país requiere únicamente agregar sus reglas fiscales al diccionario de configuración y añadir el valor en `template.yaml`.

### Consecuencias

**Positivas:**
- Expansión geográfica sin recompilación ni nuevas ramas de código.
- La variable de entorno es auditable en los logs de CloudWatch y en la configuración de Lambda.
- El mismo pipeline CI/CD despliega a todos los países con parámetros distintos.
- Agregar un cuarto país (ej. Perú) requiere solo extender el diccionario de reglas, no refactorizar.

**Negativas (trade-offs aceptados):**
- Las reglas fiscales están **hardcodeadas en el código fuente**, no en una base de datos; un cambio de tasa de IVA exige un redespliegue (no es configurable en caliente).
- El testing debe cubrir explícitamente las tres variantes de `PAIS`; una regla incorrecta para un país no afecta a los demás pero puede pasar desapercibida.
- Si las diferencias entre países crecen significativamente, el diccionario de configuración puede convertirse en un antipatrón de configuración compleja.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Escalabilidad (H,H) | Habilita la expansión a México y Chile sin cambios de arquitectura, cumpliendo directamente el requisito de Daniela en 6 meses. |
| Disponibilidad (H,H) | Un fallo de configuración en un país no afecta el despliegue de los demás; los entornos son independientes. |
| Seguridad (H,M) | Las reglas fiscales por país se centralizan en el código versionado en Git, lo que garantiza trazabilidad de cada cambio de configuración tributaria. |

---

## ADR-009: Auditoría Automática del Estado de Facturas

**Estado:** Aceptado

### Contexto

Valeria (CISO) estableció como **requisito innegociable** el no repudio de cada modificación de estado en una factura: el sistema debe ser capaz de demostrar, con evidencia inmutable, quién cambió el estado de una factura, cuándo y desde qué estado anterior. Este requisito responde a exigencias regulatorias de los países objetivo (Colombia Decreto 2364 de 2012, México CFDI) y a la necesidad del cliente piloto de mantener una pista de auditoría completa para auditorías fiscales. Sin esta trazabilidad, FacturaFlow no puede ser considerado un sistema confiable para entornos B2B con obligaciones fiscales.

### Decisión

Implementar la función `guardar_auditoria()` que se invoca **dentro de `procesar_factura`** inmediatamente después de cada cambio de estado de factura. Cada llamada escribe un registro inmutable en la tabla de auditoría de DynamoDB con los campos: `id_factura`, `estado_anterior`, `estado_nuevo`, `usuario` (valor fijo `sistema_ia` para cambios automáticos), `timestamp_utc` y `nivel_confianza` en el momento del cambio. Los registros de auditoría son de escritura única (no se actualizan ni eliminan); la tabla usa la clave compuesta `(id_factura, timestamp_utc)` para garantizar unicidad y orden cronológico.

### Consecuencias

**Positivas:**
- Satisface directamente el requisito de no repudio de Valeria con evidencia inmutable y ordenada cronológicamente.
- La pista de auditoría es consultable por `id_factura` para reconstruir el ciclo de vida completo de cualquier factura.
- El valor fijo `sistema_ia` como usuario permite distinguir cambios automáticos de posibles intervenciones manuales futuras.
- La inmutabilidad de los registros es estructural (DynamoDB sin operación de `Delete` en esa tabla), no solo por convención.

**Negativas (trade-offs aceptados):**
- Cada factura procesada genera **al menos un registro de auditoría**, lo que multiplica el volumen de la tabla: 3.300 facturas/día = mínimo 3.300 registros de auditoría diarios.
- `guardar_auditoria()` es una escritura síncrona dentro del flujo de `procesar_factura`; una degradación de DynamoDB en la tabla de auditoría puede bloquear el procesamiento de facturas.
- La tabla de auditoría crecerá sostenidamente durante los 5 años de retención obligatoria; se requiere monitoreo de consumo del Free Tier.
- Las consultas de auditoría para rangos de fechas requieren un GSI (Global Secondary Index) que añade costo de capacidad.

### Conexión con el Utility Tree

| Atributo | Impacto |
|---|---|
| Seguridad (H,M) | Cumple el requisito de no repudio de Valeria CISO; cada cambio de estado queda registrado de forma inmutable con timestamp UTC. |
| Disponibilidad (H,H) | La dependencia síncrona con DynamoDB para auditoría exige que la tabla de auditoría tenga alta disponibilidad; el SLA de DynamoDB (99.999%) lo garantiza. |
| Escalabilidad (H,H) | DynamoDB escala el volumen creciente de registros de auditoría al expandirse a México y Chile sin cambios de esquema ni de código. |

---

*Última actualización: 2026-05-15 — FacturaFlow MVP v1.0*
