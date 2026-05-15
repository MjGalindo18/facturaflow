"""
Pruebas unitarias para shared/validators.py — FacturaFlow

Regla de negocio:
  nivel_confianza > 0.85  Y  subtotal + valor_impuesto == gran_total  →  APROBADO
  Cualquier otra condición                                             →  REQUIERE_REVISION

6 casos cubiertos:
  1. Confianza > 0.85 + matemática correcta      → APROBADO
  2. Confianza < 0.85 + matemática correcta      → REQUIERE_REVISION
  3. Confianza > 0.85 + matemática incorrecta    → REQUIERE_REVISION
  4. Ambas condiciones fallidas                  → REQUIERE_REVISION
  5. Confianza exactamente en 0.85 (límite)      → REQUIERE_REVISION
  6. Confianza 1.0 (máximo absoluto)             → APROBADO
"""
from datetime import date
from decimal import Decimal

import pytest

from shared.models import EstadoFactura, Factura, Proveedor
from shared.validators import validar_confianza, validar_factura, validar_totales

# ── Proveedores colombianos de prueba ─────────────────────────────────────────

PROVEEDOR_ACERO = Proveedor(nombre="Aceros del Caribe S.A.S", nit="900234567-1")
PROVEEDOR_TECH = Proveedor(nombre="Tecnología Avanzada de Colombia S.A", nit="830012345-6")
PROVEEDOR_LOGISTICA = Proveedor(nombre="Logística Nacional Ltda", nit="860045678-3")


def _factura(
    subtotal: str,
    porcentaje_impuesto: str,
    valor_impuesto: str,
    gran_total: str,
    nivel_confianza: float,
    proveedor: Proveedor = PROVEEDOR_ACERO,
) -> Factura:
    """Construye una Factura mínima con los campos relevantes para la validación."""
    return Factura(
        fecha_emision=date(2025, 3, 10),
        proveedor=proveedor,
        subtotal=Decimal(subtotal),
        porcentaje_impuesto=Decimal(porcentaje_impuesto),
        valor_impuesto=Decimal(valor_impuesto),
        gran_total=Decimal(gran_total),
        nivel_confianza=nivel_confianza,
        estado=EstadoFactura.REQUIERE_REVISION,
    )


# ── Caso 1: confianza > 0.85 + matemática correcta → APROBADO ────────────────

class TestCaso1ConfianzaAltaMatematicaCorrecta:
    """IVA 19% Colombia: subtotal $1.000.000 → impuesto $190.000 → total $1.190.000"""

    def test_estado_aprobado(self):
        factura = _factura("1000000", "19", "190000", "1190000", 0.95)
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.APROBADO

    def test_ambas_banderas_verdaderas(self):
        factura = _factura("1000000", "19", "190000", "1190000", 0.95)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is True
        assert resultado.totales_validos is True
        assert resultado.aprobada is True

    def test_factura_estado_mutado(self):
        """validar_factura actualiza factura.estado como efecto secundario."""
        factura = _factura("500000", "5", "25000", "525000", 0.90,
                           proveedor=PROVEEDOR_TECH)
        validar_factura(factura)
        assert factura.estado == EstadoFactura.APROBADO


# ── Caso 2: confianza < 0.85 + matemática correcta → REQUIERE_REVISION ───────

class TestCaso2ConfianzaBaja:
    """Motor IA devolvió confianza insuficiente aunque los montos cuadren."""

    def test_estado_requiere_revision(self):
        factura = _factura("1000000", "19", "190000", "1190000", 0.75)
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_confianza_invalida_totales_validos(self):
        factura = _factura("1000000", "19", "190000", "1190000", 0.75)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is False
        assert resultado.totales_validos is True

    def test_confianza_baja_proveedor_logistica(self):
        factura = _factura("2500000", "19", "475000", "2975000", 0.80,
                           proveedor=PROVEEDOR_LOGISTICA)
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION


# ── Caso 3: confianza > 0.85 + matemática incorrecta → REQUIERE_REVISION ─────

class TestCaso3MatematicaIncorrecta:
    """OCR leyó mal alguno de los montos; la suma no cierra."""

    def test_estado_requiere_revision(self):
        factura = _factura("1000000", "19", "190000", "1300000",   # total inflado
                           0.95)
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_confianza_valida_totales_invalidos(self):
        factura = _factura("1000000", "19", "190000", "1300000", 0.95)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is True
        assert resultado.totales_validos is False

    def test_gran_total_subdeclarado(self):
        """gran_total menor al correcto también invalida."""
        factura = _factura("1000000", "19", "190000", "1000000",   # falta el impuesto
                           0.92)
        resultado = validar_factura(factura)
        assert resultado.totales_validos is False
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION


# ── Caso 4: ambas condiciones fallidas → REQUIERE_REVISION ───────────────────

class TestCaso4AmbasCondicionesFallidas:
    """Peor escenario: confianza baja Y totales descuadrados."""

    def test_estado_requiere_revision(self):
        factura = _factura("1000000", "19", "190000", "1300000",   # total incorrecto
                           0.75)                                    # confianza baja
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_ambas_banderas_falsas(self):
        factura = _factura("1000000", "19", "190000", "1300000", 0.75)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is False
        assert resultado.totales_validos is False
        assert resultado.aprobada is False

    def test_estado_inicial_sobreescrito(self):
        """El estado previo de la factura no contamina el resultado."""
        factura = _factura("1000000", "19", "190000", "1300000", 0.75)
        factura.estado = EstadoFactura.APROBADO
        validar_factura(factura)
        assert factura.estado == EstadoFactura.REQUIERE_REVISION


# ── Caso 5: confianza exactamente 0.85 (límite) → REQUIERE_REVISION ──────────

class TestCaso5LimiteExacto:
    """La regla es estrictamente mayor (>), no mayor-o-igual (>=)."""

    def test_limite_exacto_no_aprueba(self):
        factura = _factura("1000000", "19", "190000", "1190000", 0.85)
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.REQUIERE_REVISION

    def test_confianza_valida_false_en_limite(self):
        factura = _factura("1000000", "19", "190000", "1190000", 0.85)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is False

    def test_un_punto_por_encima_del_limite_aprueba(self):
        """0.851 supera el umbral; documenta la frontera con precisión."""
        factura = _factura("1000000", "19", "190000", "1190000", 0.851)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is True
        assert resultado.estado == EstadoFactura.APROBADO


# ── Caso 6: confianza 1.0 (máximo absoluto) → APROBADO ───────────────────────

class TestCaso6ConfianzaMaxima:
    """nivel_confianza == 1.0 es el valor más alto posible; debe aprobar."""

    def test_confianza_perfecta_aprueba(self):
        factura = _factura("3500000", "19", "665000", "4165000", 1.0,
                           proveedor=PROVEEDOR_TECH)
        resultado = validar_factura(factura)
        assert resultado.estado == EstadoFactura.APROBADO

    def test_ambas_banderas_verdaderas(self):
        factura = _factura("3500000", "19", "665000", "4165000", 1.0,
                           proveedor=PROVEEDOR_TECH)
        resultado = validar_factura(factura)
        assert resultado.confianza_valida is True
        assert resultado.totales_validos is True
        assert resultado.aprobada is True

    def test_factura_estado_mutado(self):
        factura = _factura("800000", "19", "152000", "952000", 1.0,
                           proveedor=PROVEEDOR_LOGISTICA)
        validar_factura(factura)
        assert factura.estado == EstadoFactura.APROBADO


# ── Pruebas paramétricas de funciones auxiliares ─────────────────────────────

class TestValidarConfianzaParametrico:
    @pytest.mark.parametrize("nivel, esperado", [
        (0.86,  True),
        (0.90,  True),
        (0.99,  True),
        (1.00,  True),   # caso 6 — máximo absoluto
        (0.85,  False),  # caso 5 — límite exacto: NO supera
        (0.84,  False),
        (0.75,  False),
        (0.00,  False),
    ])
    def test_umbral(self, nivel, esperado):
        factura = _factura("100000", "19", "19000", "119000", nivel)
        assert validar_confianza(factura) is esperado


class TestValidarTotalesParametrico:
    @pytest.mark.parametrize("subtotal, impuesto, gran_total, esperado", [
        ("1000000", "190000", "1190000", True),   # IVA 19% general
        ("500000",  "25000",  "525000",  True),   # IVA 5% medicamentos Colombia
        ("1000000", "190000", "1200000", False),  # total inflado
        ("1000000", "190000", "1000000", False),  # falta sumar impuesto
        ("1000000", "190000", "1189999", False),  # un peso menos
        ("1000000", "190000", "1190001", False),  # un peso más
    ])
    def test_suma(self, subtotal, impuesto, gran_total, esperado):
        factura = _factura(subtotal, "19", impuesto, gran_total, 0.95)
        assert validar_totales(factura) is esperado
