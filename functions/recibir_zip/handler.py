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
BUCKET_FACTURAS = os.environ.get("BUCKET_FACTURAS", "facturaflow-facturas")
SQS_URL         = os.environ.get("SQS_URL", "")

# ── Singletons (reutilizados en invocaciones warm) ────────────────────────────
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
    for i in range(0, len(mensajes), 10):
        lote = mensajes[i : i + 10]
        entries = [
            {"Id": str(idx), "MessageBody": json.dumps(msg)}
            for idx, msg in enumerate(lote)
        ]
        _get_sqs().send_message_batch(QueueUrl=SQS_URL, Entries=entries)


def _respuesta(status: int, body: dict) -> dict:
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

def handler(event, _context):
    # Respuesta al preflight CORS — el navegador envía OPTIONS antes del POST
    if event.get("httpMethod") == "OPTIONS":
        return _respuesta(200, {})

    # 1. Decodificar cuerpo (API Gateway envía binarios en base64)
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
    params         = event.get("queryStringParameters") or {}
    email_analista = params.get("email_analista", "")

    if not email_analista:
        return _respuesta(400, {"error": "Falta el parámetro email_analista."})

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_analista):
        return _respuesta(400, {"error": f"El email_analista '{email_analista}' no tiene un formato válido."})

    # 3. Generar ID de seguimiento del lote — se devuelve al usuario
    lote_id = str(uuid.uuid4())

    # 4. Abrir ZIP en memoria y filtrar PDFs válidos
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
    return _respuesta(202, {
        "lote_id":        lote_id,
        "total_facturas": len(mensajes),
        "mensaje":        f"{len(mensajes)} factura(s) recibidas y en cola de procesamiento.",
    })
