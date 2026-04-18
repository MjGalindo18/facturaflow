"""
Tests unitarios para shared/validators.py

Regla de negocio (CONTEXT.md):
  nivel_confianza > 0.85  Y  subtotal + valor_impuesto == gran_total  →  APROBADO
  Cualquier otra condición                                             →  REQUIERE_REVISION
"""
from datetime import date
from decimal import Decimal

import pytest

from shared.models import EstadoFactura, Factura, Proveedor
from shared.validators import (
    ResultadoValidacion,
    validar_confianza,
    validar_factura,
    validar_totales,
)

# ── Fixture base ──────────────────────────────────────────────────────────────

PROVEEDOR = Proveedor(nombre="Aceros del Caribe S.A.S", nit="900234567-1")


def _factura(
    subtotal: str,
    porcentaje_impuesto: str,
    valor_impuesto: str,
    gran_total: str,
    nivel_confianza: float,
) -> Factura:
    """Construye una Factura con los campos relevantes para la validación."""
    return Factura(
        fecha_emision=date(2025, 1, 15),
        proveedor=PROVEEDOR,
        subtotal=Decimal(subtotal),
        porcentaje_impuesto=Decimal(porcentaje_impuesto),
        valor_impuesto=Decimal(valor_impuesto),
        gran_total=Decimal(gran_total),
        nivel_confianza=nivel_confianza,
        estado=EstadoFactura.REQUIERE_REVISION,  # estado inicial irrelevante
    )


# ── Caso 1: confianza alta + matemática correcta → APROBADO ──────────────────

class TestAprobado:
    def test_estado_es_aprobado(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_190_000",
            nivel_confianza=0.95,
        )
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.APROBADO

    def test_ambas_validaciones_verdaderas(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_190_000",
            nivel_confianza=0.95,
        )
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is True
        assert resultado.totales_validos is True
        assert resultado.aprobada is True

    def test_factura_estado_actualizado(self):
        """validar_factura debe actualizar factura.estado como efecto secundario."""
        factura = _factura(
            subtotal="500_000",
            porcentaje_impuesto="5",
            valor_impuesto="25_000",
            gran_total="525_000",
            nivel_confianza=0.99,
        )
        validar_factura(factura)
        assert factura.estado == EstadoFactura.APROBADO

    def test_confianza_limite_superior(self):
        """Confianza máxima posible (0.9999) debe aprobar."""
        factura = _factura(
            subtotal="200_000",
            porcentaje_impuesto="19",
            valor_impuesto="38_000",
            gran_total="238_000",
            nivel_confianza=0.9999,
        )
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.APROBADO


# ── Caso 2: confianza baja + matemática correcta → REQUIERE_REVISION ─────────

class TestBajaConfianza:
    def test_estado_es_requiere_revision(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_190_000",
            nivel_confianza=0.75,
        )
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_confianza_invalida_totales_validos(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_190_000",
            nivel_confianza=0.75,
        )
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is False
        assert resultado.totales_validos is True

    def test_confianza_exactamente_umbral_no_aprueba(self):
        """nivel_confianza == 0.85 no supera el umbral (regla: estrictamente mayor)."""
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_190_000",
            nivel_confianza=0.85,
        )
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is False
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_confianza_minima(self):
        """Confianza mínima del rango del motor (0.75) no aprueba."""
        factura = _factura(
            subtotal="500_000",
            porcentaje_impuesto="5",
            valor_impuesto="25_000",
            gran_total="525_000",
            nivel_confianza=0.75,
        )
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION


# ── Caso 3: confianza alta + matemática incorrecta → REQUIERE_REVISION ───────

class TestMatematicaIncorrecta:
    def test_estado_es_requiere_revision(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_300_000",   # debería ser 1_190_000
            nivel_confianza=0.95,
        )
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_confianza_valida_totales_invalidos(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_300_000",
            nivel_confianza=0.95,
        )
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is True
        assert resultado.totales_validos is False

    def test_gran_total_menor_al_correcto(self):
        """gran_total subdeclarado también invalida la factura."""
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_000_000",   # falta sumar el impuesto
            nivel_confianza=0.95,
        )
        resultado = validar_factura(factura)
        assert resultado.totales_validos is False
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_impuesto_inconsistente_con_porcentaje(self):
        """valor_impuesto que no corresponde al porcentaje declarado invalida totales."""
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="50_000",   # debería ser 190_000 al 19%
            gran_total="1_050_000",    # subtotal + valor_impuesto incorrecto cuadra internamente
            nivel_confianza=0.95,
        )
        # subtotal(1_000_000) + valor_impuesto(50_000) == gran_total(1_050_000) → totales OK
        # pero el porcentaje es inconsistente. El validador solo verifica la suma,
        # no el porcentaje — este test documenta ese comportamiento explícitamente.
        resultado = validar_factura(factura)
        assert resultado.totales_validos is True  # la suma cuadra aunque el % sea raro


# ── Caso 4: ambas condiciones fallidas → REQUIERE_REVISION ───────────────────

class TestAmbasCondicionesFallidas:
    def test_estado_es_requiere_revision(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_300_000",   # matemática incorrecta
            nivel_confianza=0.75,     # confianza baja
        )
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_ambas_validaciones_falsas(self):
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_300_000",
            nivel_confianza=0.75,
        )
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is False
        assert resultado.totales_validos is False
        assert resultado.aprobada is False

    def test_factura_estado_actualizado(self):
        """validar_factura actualiza factura.estado aunque ambas condiciones fallen."""
        factura = _factura(
            subtotal="1_000_000",
            porcentaje_impuesto="19",
            valor_impuesto="190_000",
            gran_total="1_300_000",
            nivel_confianza=0.75,
        )
        # estado inicial no debe influir en el resultado
        factura.estado = EstadoFactura.APROBADO
        validar_factura(factura)
        assert factura.estado == EstadoFactura.REQUIERE_REVISION


# ── Tests unitarios de funciones auxiliares ───────────────────────────────────

class TestValidarConfianza:
    @pytest.mark.parametrize("nivel, esperado", [
        (0.86, True),
        (0.99, True),
        (0.85, False),   # límite exacto: no supera
        (0.84, False),
        (0.75, False),
    ])
    def test_umbral(self, nivel, esperado):
        factura = _factura("100", "19", "19", "119", nivel)
        assert validar_confianza(factura) is esperado


class TestValidarTotales:
    @pytest.mark.parametrize("subtotal, impuesto, gran_total, esperado", [
        ("1000", "190",  "1190",  True),
        ("1000", "50",   "1050",  True),
        ("1000", "190",  "1200",  False),  # gran_total inflado
        ("1000", "190",  "1000",  False),  # falta el impuesto
        ("1000", "190",  "1189",  False),  # un peso menos
        ("1000", "190",  "1191",  False),  # un peso más
    ])
    def test_suma(self, subtotal, impuesto, gran_total, esperado):
        factura = _factura(subtotal, "19", impuesto, gran_total, 0.95)
        assert validar_totales(factura) is esperado
