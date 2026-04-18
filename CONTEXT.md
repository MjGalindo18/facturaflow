# FacturaFlow — Contexto Arquitectónico

## ¿Qué es FacturaFlow?
Sistema SaaS B2B que automatiza la extracción, validación e integración 
de facturas en PDF hacia los sistemas ERP de clientes corporativos.

## Patrón Arquitectónico
Serverless / FaaS (Function-as-a-Service) sobre AWS Free Tier

## Restricciones de TI innegociables
- Arquitectura 100% Serverless (Free Tier AWS)
- Presupuesto infraestructura: $0
- API IA: máximo 10 peticiones concurrentes
- API ERP cliente: máximo 5 peticiones por segundo
- PDFs cifrados con AES-256
- Retención de documentos: mínimo 5 años

## Atributo dominante
DISPONIBILIDAD — 99.9% uptime en horario laboral durante cierre de mes

## Atributos de calidad priorizados (Utility Tree)
1. Disponibilidad (H,H) — 99.9% uptime en cierre de mes
2. Escalabilidad (H,H) — 60% del volumen en 3 días hábiles
3. Seguridad (H,M) — AES-256 + no repudio + auditoría inmutable
4. Rendimiento (H,M) — ID en < 2 segundos, procesamiento < 15 seg/factura

## Táctica arquitectónica principal
Queue-Based Load Leveling con AWS SQS
- El analista sube ZIP → recibe ID en < 2 segundos
- Procesamiento ocurre en segundo plano
- Dead Letter Queue para facturas fallidas

## Reglas de negocio críticas
- nivel_confianza > 0.85 Y subtotal + valor_impuesto == gran_total → APROBADO
- Cualquier otra condición → REQUIERE_REVISION
- Cada modificación manual genera registro inmutable en auditoría
- Registro debe guardar: quién, qué campo, valor anterior, valor nuevo, cuándo

## Servicios AWS utilizados
- Lambda: 4 funciones (recibir_zip, procesar_factura, notificar_analista, motor_ia_mock)
- SQS: cola de mensajes principal + Dead Letter Queue
- S3: almacenamiento PDFs cifrados AES-256 + frontend estático
- DynamoDB: estado de facturas + auditoría
- API Gateway: puerta de entrada HTTP
- SES: envío de correos de notificación

## Funciones Lambda
1. recibir_zip → recibe ZIP, descomprime, encola facturas, devuelve ID en < 2s
2. procesar_factura → procesa UNA factura: IA → validar → guardar → ERP
3. notificar_analista → envía correo cuando termina el lote
4. motor_ia_mock → simula IA con pausa de 3-5 segundos

## Contexto de negocio
- Cliente piloto: Constructora Andina S.A.
- Volumen: 3.300 facturas diarias
- Pico: 1.980 facturas en un solo día hábil (cierre de mes)
- MVP en producción: 12 semanas
- Expansión: Colombia → México → Chile en 6 meses
- Reglas fiscales configurables por país sin recompilar

## Diagrama de Clases
ArchivoZIP → contiene → ArchivoPDF
LoteFacturas → contiene → Factura
Factura → tiene → Proveedor
Factura → tiene → Auditoria
SistemaFacturaFlow → usa → ProcesadorFacturas
ProcesadorFacturas → usa → MotorIAService
ProcesadorFacturas → usa → ERPService
ProcesadorFacturas → usa → NotificacionService
ProcesadorFacturas → usa → ValidadorFactura

## Diagrama de Componentes
- API Gateway
- Servicio de recepción de facturas
- Servicio de extracción con motor IA
- Servicio de validación matemáticas
- Servicio de repositorio de facturas
- Servicio de auditoría
- Servicio de almacenamiento de facturas
- Servicio de gestión de colas
- Servicio ERP Cliente
- Servicio de notificación SMTP

## Diagrama de Despliegue — Nodos AWS
- Servidor distribución contenido
- Servidor enrutamiento API
- Entorno ejecución Python 3.11 Serverless FaaS
- Queue Server (SQS)
- Servidor OCR IA (motor_ia_mock)
- Servidor Base de Datos NoSQL (DynamoDB)
- Servidor almacenamiento documentos (S3)
- Servidor notificaciones (SES)
- Servidor ERP Constructora Andina
- Servidor Monitoreo