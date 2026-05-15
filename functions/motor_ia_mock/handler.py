"""
motor_ia_mock — Lambda que simula el motor de extracción IA de FacturaFlow.
Invocada directamente por procesar_factura (Lambda-to-Lambda), no por API Gateway.

Contrato de salida: devuelve un dict con todos los campos necesarios para que
procesar_factura construya un objeto Factura (shared/models.py) y ejecute
los validadores (shared/validators.py).
"""
import os
import random
import time
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

# ── Datos de proveedores ficticios (Colombia) ─────────────────────────────────
_PROVEEDORES = [
    {"nombre": "Aceros del Caribe S.A.S",        "nit": "900234567-1"},
    {"nombre": "Cementos Argos Colombia",         "nit": "860002184-0"},
    {"nombre": "Ferretería Nacional Ltda",        "nit": "830054321-9"},
    {"nombre": "Maderas y Construcción S.A",      "nit": "901234567-3"},
    {"nombre": "Pinturas Tito Pabón",             "nit": "800987654-2"},
    {"nombre": "Electricidad Industrial S.A.S",   "nit": "901345678-5"},
    {"nombre": "Tubos y Conexiones del Norte",    "nit": "890567890-7"},
    {"nombre": "Andamios y Equipos S.A",          "nit": "800123456-8"},
    {"nombre": "Concretos y Agregados Ltda",      "nit": "802345678-4"},
    {"nombre": "Suministros de Obra S.A.S",       "nit": "900456789-6"},
]

_IVA_POR_PAIS = {
    "colombia": [Decimal("19"), Decimal("5")],
    "mexico":   [Decimal("16"), Decimal("8")],
    "chile":    [Decimal("19"), Decimal("0")],
}
_PORCENTAJES_IVA = _IVA_POR_PAIS.get(
    os.environ.get("PAIS", "colombia").lower(),
    _IVA_POR_PAIS["colombia"],
)

# Rango nivel_confianza: [0.75, 0.99]
#   > 0.85 → APROBADO (aprox. 70% de los casos)
#   ≤ 0.85 → REQUIERE_REVISION (aprox. 30% de los casos)
_CONFIANZA_MIN = 0.75
_CONFIANZA_MAX = 0.99


# ── Generadores de datos ──────────────────────────────────────────────────────

def _fecha_emision_aleatoria() -> str:
    """Fecha de emisión dentro de los últimos 60 días naturales."""
    dias_atras = random.randint(0, 60)
    return (date.today() - timedelta(days=dias_atras)).isoformat()


def _generar_extraccion(s3_key: str) -> dict:
    """
    Simula los datos que un motor OCR/IA extraería de un PDF de factura.
    Los montos siempre son matemáticamente consistentes para que la
    validación de totales dependa únicamente del validador real.
    """
    proveedor          = random.choice(_PROVEEDORES)
    porcentaje         = random.choice(_PORCENTAJES_IVA)
    subtotal           = Decimal(str(round(random.uniform(500_000, 50_000_000), 2)))
    valor_impuesto     = (subtotal * porcentaje / 100).quantize(
                             Decimal("0.01"), rounding=ROUND_HALF_UP
                         )
    gran_total         = subtotal + valor_impuesto
    nivel_confianza    = round(random.uniform(_CONFIANZA_MIN, _CONFIANZA_MAX), 4)

    return {
        "id_factura":          str(uuid.uuid4()),
        "s3_key":              s3_key,
        "fecha_emision":       _fecha_emision_aleatoria(),
        "proveedor":           proveedor,
        "subtotal":            str(subtotal),
        "porcentaje_impuesto": str(porcentaje),
        "valor_impuesto":      str(valor_impuesto),
        "gran_total":          str(gran_total),
        "nivel_confianza":     nivel_confianza,
    }


# ── Handler principal ─────────────────────────────────────────────────────────

def handler(event, _context):
    s3_key = event.get("s3_key", "")
    if not s3_key:
        return {
            "statusCode": 400,
            "error": "Falta el campo 's3_key' en el evento.",
        }

    # Simular latencia del motor IA: 3-5 segundos (CONTEXT.md)
    time.sleep(random.uniform(3, 5))

    extraccion = _generar_extraccion(s3_key)

    return {
        "statusCode": 200,
        "extraccion": extraccion,
    }
