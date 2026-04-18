import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3

from shared.models import Factura

# ── Nombres de tablas ────────────────────────────────────────────────────────
TABLA_FACTURAS   = os.environ.get("FACTURAS_TABLE",  "facturaflow-facturas")
TABLA_AUDITORIA  = os.environ.get("AUDITORIA_TABLE", "facturaflow-auditoria")

# ── Singleton de recurso DynamoDB (reutilizado entre invocaciones warm) ───────
_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _dynamodb


def _tabla_facturas():
    return _get_dynamodb().Table(TABLA_FACTURAS)


def _tabla_auditoria():
    return _get_dynamodb().Table(TABLA_AUDITORIA)


# ── Helpers de serialización para DynamoDB ───────────────────────────────────
# boto3 requiere Decimal para números; float y str numérico deben convertirse.

def _factura_a_item(factura: Factura) -> dict:
    """Convierte Factura a item DynamoDB (numéricos como Decimal)."""
    raw = factura.to_dict()
    return {
        "id":                  raw["id"],
        "fecha_emision":       raw["fecha_emision"],
        "proveedor":           raw["proveedor"],
        "subtotal":            Decimal(raw["subtotal"]),
        "porcentaje_impuesto": Decimal(raw["porcentaje_impuesto"]),
        "valor_impuesto":      Decimal(raw["valor_impuesto"]),
        "gran_total":          Decimal(raw["gran_total"]),
        "nivel_confianza":     Decimal(str(raw["nivel_confianza"])),
        "estado":              raw["estado"],
        "fecha_carga":         raw["fecha_carga"],
    }


def _item_a_factura(item: dict) -> Factura:
    """Convierte item DynamoDB de vuelta a Factura."""
    normalizado = {
        **item,
        "subtotal":            str(item["subtotal"]),
        "porcentaje_impuesto": str(item["porcentaje_impuesto"]),
        "valor_impuesto":      str(item["valor_impuesto"]),
        "gran_total":          str(item["gran_total"]),
        "nivel_confianza":     float(item["nivel_confianza"]),
    }
    return Factura.from_dict(normalizado)


# ── Facturas ─────────────────────────────────────────────────────────────────

def guardar_factura(factura: Factura) -> None:
    """Persiste una factura en DynamoDB (crea o reemplaza)."""
    _tabla_facturas().put_item(Item=_factura_a_item(factura))


def obtener_factura(id: str) -> Optional[Factura]:
    """
    Devuelve la Factura con el id indicado, o None si no existe.
    Lanza ClientError ante fallos de conectividad o permisos.
    """
    response = _tabla_facturas().get_item(Key={"id": id})
    item = response.get("Item")
    return _item_a_factura(item) if item else None


def batch_get_facturas(ids: list) -> list:
    """
    Recupera múltiples facturas por id usando batch_get_item (máx 100 por llamada).
    Devuelve solo las facturas encontradas; los ids inexistentes se ignoran.
    Útil para construir resúmenes de lote sin recorrer toda la tabla.
    """
    facturas = []
    for i in range(0, len(ids), 100):         # DynamoDB batch_get_item: máx 100 claves
        chunk = ids[i : i + 100]
        keys  = [{"id": fid} for fid in chunk]
        resp  = _get_dynamodb().batch_get_item(
            RequestItems={TABLA_FACTURAS: {"Keys": keys}}
        )
        for item in resp.get("Responses", {}).get(TABLA_FACTURAS, []):
            facturas.append(_item_a_factura(item))
    return facturas


# ── Auditoría ─────────────────────────────────────────────────────────────────

@dataclass
class RegistroAuditoria:
    usuario: str
    campo_modificado: str
    valor_anterior: str
    valor_nuevo: str
    factura_id: str
    id: str = None
    timestamp: str = None

    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if self.timestamp is None:
            self.timestamp = datetime.now(tz=timezone.utc).isoformat()


def guardar_auditoria(
    factura_id: str,
    usuario: str,
    campo_modificado: str,
    valor_anterior: str,
    valor_nuevo: str,
) -> RegistroAuditoria:
    """
    Escribe un registro de auditoría inmutable en DynamoDB.
    Solo usa PutItem — nunca UpdateItem ni DeleteItem — para garantizar
    la inmutabilidad exigida por el CONTEXT.md.
    """
    registro = RegistroAuditoria(
        factura_id=factura_id,
        usuario=usuario,
        campo_modificado=campo_modificado,
        valor_anterior=valor_anterior,
        valor_nuevo=valor_nuevo,
    )
    _tabla_auditoria().put_item(
        Item={
            "id":               registro.id,
            "factura_id":       registro.factura_id,
            "usuario":          registro.usuario,
            "campo_modificado": registro.campo_modificado,
            "valor_anterior":   registro.valor_anterior,
            "valor_nuevo":      registro.valor_nuevo,
            "timestamp":        registro.timestamp,
        },
        # ConditionExpression garantiza que ningún registro existente
        # pueda ser sobreescrito (clave uuid colisión prácticamente imposible,
        # pero la condición refuerza la inmutabilidad a nivel de DynamoDB).
        ConditionExpression="attribute_not_exists(id)",
    )
    return registro
