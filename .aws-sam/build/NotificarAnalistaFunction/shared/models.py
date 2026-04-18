from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid


class EstadoFactura(str, Enum):
    APROBADO = "APROBADO"
    REQUIERE_REVISION = "REQUIERE_REVISION"


@dataclass
class Proveedor:
    nombre: str
    nit: str

    def to_dict(self) -> dict:
        return {"nombre": self.nombre, "nit": self.nit}

    @classmethod
    def from_dict(cls, data: dict) -> "Proveedor":
        return cls(nombre=data["nombre"], nit=data["nit"])


@dataclass
class Factura:
    fecha_emision: date
    proveedor: Proveedor
    subtotal: Decimal
    porcentaje_impuesto: Decimal
    valor_impuesto: Decimal
    gran_total: Decimal
    nivel_confianza: float
    estado: EstadoFactura
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    fecha_carga: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "fecha_emision": self.fecha_emision.isoformat(),
            "proveedor": self.proveedor.to_dict(),
            "subtotal": str(self.subtotal),
            "porcentaje_impuesto": str(self.porcentaje_impuesto),
            "valor_impuesto": str(self.valor_impuesto),
            "gran_total": str(self.gran_total),
            "nivel_confianza": self.nivel_confianza,
            "estado": self.estado.value,
            "fecha_carga": self.fecha_carga.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Factura":
        return cls(
            id=data["id"],
            fecha_emision=date.fromisoformat(data["fecha_emision"]),
            proveedor=Proveedor.from_dict(data["proveedor"]),
            subtotal=Decimal(data["subtotal"]),
            porcentaje_impuesto=Decimal(data["porcentaje_impuesto"]),
            valor_impuesto=Decimal(data["valor_impuesto"]),
            gran_total=Decimal(data["gran_total"]),
            nivel_confianza=float(data["nivel_confianza"]),
            estado=EstadoFactura(data["estado"]),
            fecha_carga=datetime.fromisoformat(data["fecha_carga"]),
        )
