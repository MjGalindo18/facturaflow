from dataclasses import dataclass
from decimal import Decimal

from shared.models import EstadoFactura, Factura

UMBRAL_CONFIANZA = Decimal("0.85")


@dataclass
class ResultadoValidacion:
    confianza_valida: bool
    totales_validos: bool

    @property
    def aprobada(self) -> bool:
        return self.confianza_valida and self.totales_validos

    @property
    def estado(self) -> EstadoFactura:
        return EstadoFactura.APROBADO if self.aprobada else EstadoFactura.REQUIERE_REVISION


def validar_confianza(factura: Factura) -> bool:
    return Decimal(str(factura.nivel_confianza)) > UMBRAL_CONFIANZA


def validar_totales(factura: Factura) -> bool:
    return factura.subtotal + factura.valor_impuesto == factura.gran_total


def validar_factura(factura: Factura) -> ResultadoValidacion:
    """
    Aplica las reglas de negocio del CONTEXT.md:
      - nivel_confianza > 0.85
      - subtotal + valor_impuesto == gran_total
    Ambas deben cumplirse para APROBADO; cualquier fallo → REQUIERE_REVISION.
    Actualiza factura.estado como efecto secundario.
    """
    resultado = ResultadoValidacion(
        confianza_valida=validar_confianza(factura),
        totales_validos=validar_totales(factura),
    )
    factura.estado = resultado.estado
    return resultado
