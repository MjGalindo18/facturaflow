# FacturaFlow

Sistema SaaS B2B que automatiza la extracción, validación e integración de facturas en PDF hacia los sistemas ERP de clientes corporativos. Construido 100% sobre AWS Serverless (Free Tier) con Python 3.11.

---

## Tabla de contenidos

- [Descripción del proyecto](#descripción-del-proyecto)
- [Arquitectura](#arquitectura)
- [Estructura de carpetas](#estructura-de-carpetas)
- [Tecnologías utilizadas](#tecnologías-utilizadas)
- [Cómo desplegar](#cómo-desplegar)
- [Cómo contribuir](#cómo-contribuir)

---

## Descripción del proyecto

FacturaFlow recibe lotes de facturas en formato ZIP, extrae los PDFs, los procesa mediante un motor de IA (OCR), valida las reglas matemáticas y fiscales, y los integra automáticamente al ERP del cliente. El analista recibe confirmación de recepción en menos de 2 segundos y una notificación por correo cuando el lote finaliza.

### Contexto de negocio

| Parámetro | Valor |
|---|---|
| Cliente piloto | Constructora Andina S.A. |
| Volumen diario | 3.300 facturas |
| Pico de cierre de mes | 1.980 facturas en un día hábil |
| MVP en producción | 12 semanas |
| Expansión planeada | Colombia → México → Chile |
| Presupuesto de infraestructura | $0 (AWS Free Tier) |

### Reglas de validación

- `nivel_confianza > 0.85` **Y** `subtotal + valor_impuesto == gran_total` → **APROBADO**
- Cualquier otra condición → **REQUIERE_REVISIÓN**
- Toda modificación manual genera un registro inmutable en la tabla de auditoría (quién, qué campo, valor anterior, valor nuevo, cuándo).

---

## Arquitectura

### Patrón: Serverless / FaaS con Queue-Based Load Leveling

El atributo dominante del sistema es la **disponibilidad (99.9% uptime en horario laboral durante cierre de mes)**. Para cumplirlo, la recepción del lote está completamente desacoplada del procesamiento mediante AWS SQS.

```
Analista
   │
   ▼
[API Gateway]
   │
   ▼
[Lambda: recibir_zip]
   │  ① Descomprime ZIP
   │  ② Sube PDFs a S3 (AES-256)
   │  ③ Encola un mensaje SQS por factura
   │  ④ Devuelve ID de lote en < 2 s  ──────────────────► Analista
   │
   ▼
[SQS: FacturasQueue]  ←── fallos ──► [DLQ: FacturasDLQ]
   │
   ▼  (hasta 10 invocaciones paralelas)
[Lambda: procesar_factura]
   │  ① Invoca motor_ia_mock (3-5 s simulados)
   │  ② Valida reglas matemáticas
   │  ③ Guarda estado en DynamoDB
   │  ④ Integra con ERP (máx. 5 req/s)
   │
   ▼  (cuando termina el lote)
[Lambda: notificar_analista]
   │
   ▼
[SES] ──────────────────────────────────────────────────► Analista (correo)


[Lambda: motor_ia_mock]
   └── Simula OCR con nivel_confianza aleatorio [0.70 – 0.99]
```

### SLAs garantizados

| Métrica | Valor |
|---|---|
| Tiempo de respuesta (ID de lote) | < 2 segundos |
| Tiempo de procesamiento por factura | < 15 segundos |
| Uptime en cierre de mes | 99.9% |
| Concurrencia máxima motor IA | 10 invocaciones simultáneas |
| Límite API ERP | 5 peticiones por segundo |

### Decisiones arquitectónicas (ADRs)

| ADR | Decisión | Estado |
|---|---|---|
| ADR-001 | Patrón Serverless / FaaS sobre AWS | Aceptado |
| ADR-002 | Queue-Based Load Leveling con SQS | Aceptado |
| ADR-003 | DynamoDB sobre base de datos SQL tradicional | Aceptado |
| ADR-004 | Procesamiento completamente asíncrono | Aceptado |
| ADR-005 | Motor IA Mock para el MVP | Aceptado |
| ADR-006 | AES-256 (SSE-S3) para almacenamiento de PDFs | Aceptado |

Ver [ADR.md](ADR.md) para el detalle completo de cada decisión.

---

## Estructura de carpetas

```
facturaflow/
├── functions/                        # Funciones Lambda
│   ├── recibir_zip/
│   │   ├── handler.py                # Punto de entrada: recibe ZIP, encola en SQS
│   │   └── requirements.txt
│   ├── procesar_factura/
│   │   ├── handler.py                # Orquesta IA → validación → DynamoDB → ERP
│   │   └── requirements.txt
│   ├── notificar_analista/
│   │   ├── handler.py                # Envía resumen del lote por SES
│   │   └── requirements.txt
│   └── motor_ia_mock/
│       ├── handler.py                # Simula OCR con latencia 3-5 s
│       └── requirements.txt
├── shared/                           # Módulos compartidos (copiados en el build)
│   ├── db.py                         # Operaciones DynamoDB (facturas + auditoría)
│   ├── models.py                     # Modelos de datos
│   └── validators.py                 # Reglas de validación de facturas
├── frontend/                         # Aplicación web estática (S3)
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── tests/                            # Suite de pruebas con pytest
├── template.yaml                     # Infraestructura como código (AWS SAM)
├── samconfig.toml                    # Configuración de despliegue SAM
├── conftest.py                       # Fixtures globales de pytest
├── CONTEXT.md                        # Contexto arquitectónico del sistema
└── ADR.md                            # Architecture Decision Records
```

---

## Tecnologías utilizadas

### Lenguaje y runtime

| Tecnología | Versión | Uso |
|---|---|---|
| Python | 3.11 | Runtime de todas las funciones Lambda |
| boto3 | ≥ 1.34 | SDK de AWS (S3, SQS, DynamoDB, SES, Lambda) |

### Servicios AWS

| Servicio | Rol en el sistema |
|---|---|
| **API Gateway** | Puerta de entrada HTTP REST; expone `POST /facturas/lote` |
| **Lambda** | 4 funciones FaaS: recibir_zip, procesar_factura, notificar_analista, motor_ia_mock |
| **SQS** | Cola principal (FacturasQueue) + Dead Letter Queue (FacturasDLQ, 14 días, 3 reintentos) |
| **S3** | Almacenamiento de PDFs cifrados (AES-256, versionado, Glacier a los 90 días) y frontend estático |
| **DynamoDB** | Tabla `FacturasTable` (estado de facturas) + `AuditoriaTable` (log inmutable) |
| **SES** | Envío de correos de notificación al analista |
| **CloudFormation / SAM** | Infraestructura como código; despliegue reproducible |
| **X-Ray** | Trazabilidad distribuida de invocaciones Lambda |

### Seguridad

| Control | Implementación |
|---|---|
| Cifrado en reposo | SSE-S3 (AES-256) obligatorio vía bucket policy |
| Acceso temporal | Presigned URLs con expiración de 15 minutos |
| Mínimo privilegio | Roles IAM individuales por función Lambda |
| Auditoría inmutable | Tabla DynamoDB append-only con escrituras atómicas |
| Retención de documentos | S3 Lifecycle Policy → 5 años mínimo |

---

## Cómo desplegar

### Prerrequisitos

- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) configurado con credenciales válidas
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) ≥ 1.100
- Python 3.11
- Una dirección de correo verificada en Amazon SES (región `us-east-1`)

### Variables de configuración

Antes de desplegar, edita los parámetros en `samconfig.toml` o pásalos como overrides:

| Parámetro | Descripción | Ejemplo |
|---|---|---|
| `Entorno` | Nombre del entorno (`dev`, `prod`) | `dev` |
| `RemitenteEmail` | Email verificado en SES para notificaciones | `analista@empresa.com` |
| `ERPUrl` | Endpoint del ERP del cliente (vacío en dev) | `https://erp.empresa.com/api` |

### Pasos de despliegue

```bash
# 1. Clonar el repositorio
git clone <url-del-repositorio>
cd facturaflow

# 2. Compilar el proyecto (empaqueta dependencias por función)
sam build

# 3. Desplegar por primera vez (modo guiado)
sam deploy --guided

# 4. Despliegues posteriores (usa samconfig.toml)
sam deploy
```

El comando `sam deploy --guided` solicita los parámetros interactivamente y guarda la configuración en `samconfig.toml`. En despliegues posteriores, `sam deploy` reutiliza esa configuración.

### Verificar el despliegue

```bash
# Ver los outputs del stack (URL de API Gateway, nombres de recursos)
aws cloudformation describe-stacks \
  --stack-name facturaflow \
  --query "Stacks[0].Outputs"
```

### Ejecutar pruebas

```bash
# Instalar dependencias de desarrollo
pip install pytest boto3

# Ejecutar la suite completa
pytest tests/ -v
```

### Eliminar el stack

```bash
sam delete --stack-name facturaflow
```

> **Nota:** El bucket S3 con los PDFs no se elimina automáticamente si contiene objetos (por diseño, para preservar la retención de 5 años). Vacíalo manualmente antes de eliminar el stack si lo necesitas.

---

## Cómo contribuir

### Flujo de trabajo

1. **Crea una rama** desde `main` con el formato `feature/<descripción>` o `fix/<descripción>`.
2. **Implementa** el cambio siguiendo las convenciones del proyecto.
3. **Escribe pruebas** en `tests/` para los nuevos comportamientos.
4. **Ejecuta la suite** localmente: `pytest tests/ -v`
5. **Abre un Pull Request** hacia `main` describiendo el cambio y su motivación.

### Convenciones

- **Idioma del código:** español para nombres de dominio (`factura`, `proveedor`, `lote`), inglés para patrones técnicos estándar.
- **Funciones Lambda:** cada función es independiente y stateless; el estado persiste exclusivamente en DynamoDB.
- **Módulos compartidos:** los cambios en `shared/` afectan a todas las funciones; asegúrate de que los tests de todas ellas siguen pasando.
- **Reglas de negocio:** cualquier cambio en las reglas de validación debe documentarse en `CONTEXT.md` y, si implica una decisión arquitectónica, en un nuevo ADR en `ADR.md`.
- **Seguridad:** ningún objeto debe subirse a S3 sin cifrado AES-256. La bucket policy lo rechaza a nivel de infraestructura, pero el código debe enviar el header correcto.

### Agregar soporte para un nuevo país

Las reglas fiscales están diseñadas para ser configurables por país sin recompilar:

1. Agrega las constantes del país en `shared/validators.py`.
2. Actualiza los tests en `tests/` para los nuevos escenarios fiscales.
3. Documenta las diferencias normativas en `CONTEXT.md`.

---

*FacturaFlow MVP v1.0 — 2026*
