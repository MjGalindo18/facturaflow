"""
procesar_factura — Lambda orquestadora del pipeline de FacturaFlow.
Disparada por SQS; procesa UNA factura por mensaje:
  motor_ia_mock → validar → guardar DynamoDB → ERP (si APROBADO)

Usa batchItemFailures para que solo los mensajes fallidos vayan a la DLQ,
sin detener el procesamiento del resto del lote.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal

import boto3

from shared.db import guardar_factura
from shared.models import EstadoFactura, Factura, Proveedor
from shared.validators import validar_factura

# ── Variables de entorno ──────────────────────────────────────────────────────
MOTOR_IA_FUNCTION  = os.environ.get("MOTOR_IA_FUNCTION", "facturaflow-motor-ia-mock")
NOTIFICAR_FUNCTION = os.environ.get("NOTIFICAR_FUNCTION", "")
ERP_URL            = os.environ.get("ERP_URL", "")
# 5 req/seg máximo al ERP del cliente (CONTEXT.md) → 200 ms entre llamadas
_ERP_PAUSA_SEG     = float(os.environ.get("ERP_PAUSA_SEG", "0.2"))

# ── Singleton Lambda client ───────────────────────────────────────────────────
_lambda_client = None


def _get_lambda():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda")
    return _lambda_client


# ── Paso 1: invocar motor IA ──────────────────────────────────────────────────

def _invocar_motor_ia(s3_key: str) -> dict:
    """
    Llama a motor_ia_mock de forma síncrona (Lambda-to-Lambda).
    Lanza RuntimeError si la función remota falla o devuelve status != 200.
    """
    response = _get_lambda().invoke(
        FunctionName=MOTOR_IA_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"s3_key": s3_key}).encode(),
    )

    # FunctionError se activa cuando la Lambda remota lanza una excepción
    if response.get("FunctionError"):
        detalle = response["Payload"].read().decode()
        raise RuntimeError(f"motor_ia_mock FunctionError: {detalle}")

    resultado = json.loads(response["Payload"].read())

    if resultado.get("statusCode") != 200:
        raise RuntimeError(f"motor_ia_mock respondió con error: {resultado}")

    return resultado["extraccion"]


# ── Paso 2: construir Factura ─────────────────────────────────────────────────

def _construir_factura(ext: dict) -> Factura:
    """
    Transforma el dict de extracción del motor IA en un objeto Factura.
    El estado se inicializa en REQUIERE_REVISION; validar_factura lo corregirá.
    """
    return Factura(
        id=ext["id_factura"],
        fecha_emision=date.fromisoformat(ext["fecha_emision"]),
        proveedor=Proveedor(
            nombre=ext["proveedor"]["nombre"],
            nit=ext["proveedor"]["nit"],
        ),
        subtotal=Decimal(ext["subtotal"]),
        porcentaje_impuesto=Decimal(ext["porcentaje_impuesto"]),
        valor_impuesto=Decimal(ext["valor_impuesto"]),
        gran_total=Decimal(ext["gran_total"]),
        nivel_confianza=float(ext["nivel_confianza"]),
        estado=EstadoFactura.REQUIERE_REVISION,   # sobreescrito por validar_factura
    )


# ── Paso 5: enviar al ERP ─────────────────────────────────────────────────────

def _enviar_a_erp(factura: Factura) -> None:
    """
    Envía una factura APROBADA al ERP del cliente.
    Respeta el límite de 5 req/seg durmiendo 200 ms antes de cada llamada.
    Si ERP_URL no está configurada (desarrollo local) la llamada se omite.
    """
    if not ERP_URL:
        print(f"[ERP] URL no configurada — factura {factura.id} no enviada (entorno dev).")
        return

    time.sleep(_ERP_PAUSA_SEG)   # cumplir restricción: máx 5 req/seg

    payload = json.dumps(factura.to_dict(), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ERP_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201, 202):
                raise RuntimeError(f"ERP respondió con status inesperado: {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ERP HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ERP no alcanzable: {exc.reason}") from exc


# ── Orquestación de un único registro SQS ─────────────────────────────────────

def _procesar_registro(record: dict) -> None:
    cuerpo   = json.loads(record["body"])
    s3_key   = cuerpo["s3_key"]
    lote_id  = cuerpo.get("lote_id", "sin-lote")

    print(f"[{lote_id}] Procesando: {s3_key}")

    # 1. Extraer datos con motor IA
    extraccion = _invocar_motor_ia(s3_key)

    # 2. Construir objeto Factura
    factura = _construir_factura(extraccion)

    # 3. Validar → asigna factura.estado (APROBADO o REQUIERE_REVISION)
    resultado = validar_factura(factura)
    print(
        f"[{lote_id}] Factura {factura.id} → {factura.estado.value} "
        f"(confianza={resultado.confianza_valida}, totales={resultado.totales_validos})"
    )

    # 4. Guardar en DynamoDB independientemente del estado
    guardar_factura(factura)

    # 5. Notificar al analista de forma asíncrona (fire-and-forget)
    email_analista = cuerpo.get("email_analista", "")
    if NOTIFICAR_FUNCTION and email_analista:
        _get_lambda().invoke(
            FunctionName=NOTIFICAR_FUNCTION,
            InvocationType="Event",          # asíncrono: no bloquea el pipeline
            Payload=json.dumps({
                "lote_id":       lote_id,
                "email_analista": email_analista,
                "factura_ids":   [factura.id],
            }).encode(),
        )

    # 7. Si APROBADO → enviar al ERP; si REQUIERE_REVISION → solo queda en DynamoDB
    if factura.estado == EstadoFactura.APROBADO:
        _enviar_a_erp(factura)
        print(f"[{lote_id}] Factura {factura.id} enviada al ERP.")
    else:
        print(f"[{lote_id}] Factura {factura.id} requiere revisión manual.")


# ── Handler principal ─────────────────────────────────────────────────────────

def handler(event, _context):
    """
    Procesa el lote de mensajes SQS.
    batchItemFailures devuelve al queue (→ DLQ) solo los mensajes que fallaron,
    sin bloquear el resto del lote.
    """
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            _procesar_registro(record)
        except Exception as exc:
            print(f"ERROR [{message_id}]: {type(exc).__name__}: {exc}")
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
