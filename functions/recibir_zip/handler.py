"""
recibir_zip — Lambda de entrada de FacturaFlow
Patrón: Queue-Based Load Leveling (CONTEXT.md)
  1. Recibe ZIP vía API Gateway
  2. Extrae PDFs en memoria
  3. Sube cada PDF a S3 cifrado AES-256
  4. Encola cada PDF en SQS como mensaje independiente
  5. Devuelve lote_id en < 2 segundos → el usuario puede cerrar el navegador
"""
import base64
import io
import json
import os
import re
import uuid
import zipfile

import boto3

# ── Variables de entorno ──────────────────────────────────────────────────────
# Configuraciones que viven fuera del código para poder cambiarlas sin tocar el programa.
# Si el bucket de S3 cambia en producción, solo se actualiza la variable, no el código.
# También evita que datos sensibles queden expuestos en el repositorio.
BUCKET_FACTURAS = os.environ.get("BUCKET_FACTURAS", "facturaflow-facturas")
SQS_URL         = os.environ.get("SQS_URL", "")

# ── Singletons (reutilizados en invocaciones warm) ────────────────────────────
# Las conexiones a S3 y SQS se crean una sola vez y se reutilizan entre llamadas.
# Lambda puede procesar miles de facturas por hora: reconectar en cada llamada
# sería lento y costoso. Al guardarlas aquí, las llamadas siguientes las reutilizan
# instantáneamente (esto se llama "warm start").
_s3  = None
_sqs = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _subir_pdf_s3(lote_id: str, nombre: str, contenido: bytes) -> str:
    """Sube un PDF a S3 con cifrado AES-256 del lado del servidor."""
    # Los PDFs pueden pesar megabytes y SQS solo admite mensajes pequeños (máx. 256 KB).
    # La solución es guardar el archivo en S3 y poner solo su dirección en la cola.
    # AES-256 cifra el archivo en disco: aunque alguien accediera al almacenamiento
    # físico de Amazon, no podría leer el contenido sin la clave de cifrado.
    clave = f"facturas/{lote_id}/{nombre}"
    _get_s3().put_object(
        Bucket=BUCKET_FACTURAS,
        Key=clave,
        Body=contenido,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )
    return clave


def _encolar_pdfs(mensajes: list) -> None:
    """
    Envía mensajes SQS en lotes de 10 (máximo de la API).
    Cada mensaje representa UNA factura para procesar de forma independiente.
    """
    # SQS es una "fila de espera" en la nube: cada PDF espera su turno para ser
    # procesado por otra Lambda, sin que el usuario tenga que seguir conectado.
    # Mensajes independientes por PDF significan que si uno falla, los demás no se ven afectados.
    # La API de SQS solo acepta 10 mensajes por llamada, de ahí el bucle de a 10.
    for i in range(0, len(mensajes), 10):
        lote = mensajes[i : i + 10]
        entries = [
            {"Id": str(idx), "MessageBody": json.dumps(msg)}
            for idx, msg in enumerate(lote)
        ]
        _get_sqs().send_message_batch(QueueUrl=SQS_URL, Entries=entries)


def _respuesta(status: int, body: dict) -> dict:
    # Construye el formato exacto que API Gateway espera devolver al navegador.
    # Los headers CORS son obligatorios: sin ellos el navegador bloquea la respuesta
    # por seguridad e impide que el frontend reciba los datos.
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


# ── Handler principal ─────────────────────────────────────────────────────────
# Función que AWS Lambda invoca automáticamente con cada petición HTTP.
# Orquesta los 7 pasos del flujo: validar → extraer → subir a S3 → encolar → responder.

def handler(event, _context):
    # Respuesta al preflight CORS — el navegador envía OPTIONS antes del POST
    # para preguntar si tiene permiso de hacer la petición real. Sin esto, el
    # frontend nunca llega a enviar el ZIP.
    if event.get("httpMethod") == "OPTIONS":
        return _respuesta(200, {})

    # 1. Decodificar cuerpo (API Gateway envía binarios en base64)
    # Los ZIPs son archivos binarios. API Gateway los convierte a base64 (texto)
    # antes de pasárnoslos; aquí hacemos el camino inverso para recuperar los bytes reales.
    cuerpo = event.get("body") or ""
    if event.get("isBase64Encoded", False):
        bytes_zip = base64.b64decode(cuerpo)
    elif isinstance(cuerpo, str):
        bytes_zip = cuerpo.encode("latin-1")
    else:
        bytes_zip = cuerpo

    if not bytes_zip:
        return _respuesta(400, {"error": "Cuerpo vacío. Se esperaba un archivo ZIP."})

    # 2. Leer email_analista — viene como query parameter porque el body es ZIP binario
    # El body ya está ocupado por el ZIP, así que los datos extra viajan en la URL:
    # ?email_analista=ana@empresa.com. Lo necesitamos para notificar cuando termine el lote.
    params         = event.get("queryStringParameters") or {}
    email_analista = params.get("email_analista", "")

    if not email_analista:
        return _respuesta(400, {"error": "Falta el parámetro email_analista."})

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_analista):
        return _respuesta(400, {"error": f"El email_analista '{email_analista}' no tiene un formato válido."})

    # 3. Generar ID de seguimiento del lote — se devuelve al usuario
    # UUID es un número aleatorio prácticamente irrepetible. Con él el usuario puede
    # consultar el estado de su lote más tarde, aunque ya haya cerrado el navegador.
    lote_id = str(uuid.uuid4())

    # 4. Abrir ZIP en memoria y filtrar PDFs válidos
    # io.BytesIO hace pasar los bytes en RAM como si fueran un archivo en disco,
    # evitando escribir nada al sistema de archivos. Filtramos metadatos de macOS
    # y archivos ocultos que no son facturas reales.
    try:
        with zipfile.ZipFile(io.BytesIO(bytes_zip)) as zf:
            pdfs = [
                nombre for nombre in zf.namelist()
                if nombre.lower().endswith(".pdf")
                and not nombre.startswith("__MACOSX")        # metadatos macOS
                and not os.path.basename(nombre).startswith(".")  # archivos ocultos
            ]

            if not pdfs:
                return _respuesta(400, {
                    "error": "El ZIP no contiene archivos PDF válidos."
                })

            # 5. Subir cada PDF a S3 (AES-256) y preparar mensajes SQS
            mensajes = []
            for nombre_pdf in pdfs:
                contenido   = zf.read(nombre_pdf)
                nombre_base = os.path.basename(nombre_pdf)
                s3_key      = _subir_pdf_s3(lote_id, nombre_base, contenido)
                mensajes.append({
                    "lote_id":        lote_id,
                    "s3_key":         s3_key,
                    "nombre_archivo": nombre_base,
                    "email_analista": email_analista,
                })

    except zipfile.BadZipFile:
        return _respuesta(400, {"error": "El archivo enviado no es un ZIP válido."})

    # 6. Encolar cada PDF como mensaje independiente en SQS
    _encolar_pdfs(mensajes)

    # 7. Respuesta 202 Accepted — procesamiento ocurre en segundo plano.
    #    El navegador puede cerrarse; el lote_id permite consultar el estado luego.
    #    202 (en lugar de 200) es honesto: las facturas están en cola, no procesadas aún.
    return _respuesta(202, {
        "lote_id":        lote_id,
        "total_facturas": len(mensajes),
        "mensaje":        f"{len(mensajes)} factura(s) recibidas y en cola de procesamiento.",
    })
