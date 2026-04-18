"""
notificar_analista — Lambda de notificación de FacturaFlow.
Invocada cuando un lote termina de procesarse.

Flujo:
  1. Recibe lote_id, email_analista y factura_ids en el evento
  2. Consulta DynamoDB en batch para obtener el estado de cada factura
  3. Construye resumen: APROBADAS vs REQUIEREN_REVISION
  4. Envía correo HTML profesional al analista vía AWS SES
"""
import os
from datetime import datetime, timezone

import boto3

from shared.db import batch_get_facturas
from shared.models import EstadoFactura

# ── Variables de entorno ──────────────────────────────────────────────────────
REMITENTE_EMAIL = os.environ.get("REMITENTE_EMAIL", "noreply@facturaflow.com")
AWS_REGION      = os.environ.get("AWS_REGION",      "us-east-1")

# ── Singleton SES ─────────────────────────────────────────────────────────────
_ses = None


def _get_ses():
    global _ses
    if _ses is None:
        _ses = boto3.client("ses", region_name=AWS_REGION)
    return _ses


# ── Construcción del correo ───────────────────────────────────────────────────

def _filas_revision(ids_revision: list) -> str:
    """Genera las filas HTML de la tabla de IDs que requieren revisión."""
    if not ids_revision:
        return "<tr><td colspan='2' style='padding:12px;text-align:center;color:#888;'>Ninguna</td></tr>"
    filas = ""
    for i, fid in enumerate(ids_revision, start=1):
        fondo = "#f9f9f9" if i % 2 == 0 else "#ffffff"
        filas += (
            f"<tr style='background:{fondo};'>"
            f"<td style='padding:10px 16px;color:#555;'>{i}</td>"
            f"<td style='padding:10px 16px;font-family:monospace;color:#333;'>{fid}</td>"
            "</tr>"
        )
    return filas


def _construir_html(lote_id: str, total: int, aprobadas: int,
                    revision: int, ids_revision: list, timestamp: str) -> str:
    pct_aprobadas = round(aprobadas / total * 100) if total else 0
    pct_revision  = round(revision  / total * 100) if total else 0

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,Helvetica,sans-serif;">

  <!-- Encabezado -->
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="background:#1a1a2e;padding:28px 40px;">
        <h1 style="margin:0;color:#ffffff;font-size:22px;letter-spacing:1px;">
          FacturaFlow
          <span style="font-weight:300;font-size:16px;margin-left:8px;">· Reporte de Lote</span>
        </h1>
      </td>
    </tr>
  </table>

  <!-- Cuerpo -->
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="padding:32px 40px;">

        <p style="color:#444;font-size:15px;margin:0 0 8px;">
          El lote <strong>{lote_id}</strong> ha terminado de procesarse.
        </p>
        <p style="color:#888;font-size:13px;margin:0 0 28px;">{timestamp}</p>

        <!-- Tarjetas de resumen -->
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
          <tr>
            <td width="32%" style="background:#ffffff;border:1px solid #e0e0e0;border-radius:6px;
                padding:20px 24px;text-align:center;">
              <div style="font-size:36px;font-weight:700;color:#1a1a2e;">{total}</div>
              <div style="font-size:13px;color:#888;margin-top:4px;">TOTAL PROCESADAS</div>
            </td>
            <td width="4%"></td>
            <td width="32%" style="background:#ffffff;border:1px solid #e0e0e0;border-radius:6px;
                padding:20px 24px;text-align:center;">
              <div style="font-size:36px;font-weight:700;color:#27ae60;">{aprobadas}</div>
              <div style="font-size:13px;color:#888;margin-top:4px;">APROBADAS ({pct_aprobadas}%)</div>
            </td>
            <td width="4%"></td>
            <td width="32%" style="background:#ffffff;border:1px solid #e0e0e0;border-radius:6px;
                padding:20px 24px;text-align:center;">
              <div style="font-size:36px;font-weight:700;color:#e67e22;">{revision}</div>
              <div style="font-size:13px;color:#888;margin-top:4px;">REQUIEREN REVISIÓN ({pct_revision}%)</div>
            </td>
          </tr>
        </table>

        <!-- Tabla de IDs para revisión -->
        <h2 style="color:#1a1a2e;font-size:16px;margin:0 0 12px;">
          Facturas que requieren revisión manual
        </h2>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;
                      border-collapse:collapse;background:#fff;">
          <thead>
            <tr style="background:#1a1a2e;">
              <th style="padding:12px 16px;color:#fff;font-size:13px;text-align:left;width:60px;">#</th>
              <th style="padding:12px 16px;color:#fff;font-size:13px;text-align:left;">ID de Factura</th>
            </tr>
          </thead>
          <tbody>
            {_filas_revision(ids_revision)}
          </tbody>
        </table>

      </td>
    </tr>
  </table>

  <!-- Pie de página -->
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="background:#f0f0f0;border-top:1px solid #ddd;padding:16px 40px;
                 font-size:12px;color:#aaa;text-align:center;">
        Este mensaje fue generado automáticamente por FacturaFlow.
        Por favor no responda a este correo.
      </td>
    </tr>
  </table>

</body>
</html>"""


def _construir_texto_plano(lote_id: str, total: int, aprobadas: int,
                            revision: int, ids_revision: list, timestamp: str) -> str:
    """Versión texto plano del correo (fallback para clientes sin HTML)."""
    lineas_ids = "\n".join(f"  - {fid}" for fid in ids_revision) or "  (ninguna)"
    return (
        f"FacturaFlow — Reporte de Lote\n"
        f"{'='*40}\n"
        f"Lote ID  : {lote_id}\n"
        f"Fecha    : {timestamp}\n\n"
        f"TOTAL PROCESADAS : {total}\n"
        f"APROBADAS        : {aprobadas}\n"
        f"REQUIEREN REV.   : {revision}\n\n"
        f"Facturas que requieren revisión:\n{lineas_ids}\n"
    )


# ── Envío SES ─────────────────────────────────────────────────────────────────

def _enviar_correo(email_analista: str, asunto: str,
                   html: str, texto: str) -> None:
    _get_ses().send_email(
        Source=REMITENTE_EMAIL,
        Destination={"ToAddresses": [email_analista]},
        Message={
            "Subject": {"Data": asunto,  "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": html,   "Charset": "UTF-8"},
                "Text": {"Data": texto,  "Charset": "UTF-8"},
            },
        },
    )


# ── Handler principal ─────────────────────────────────────────────────────────

def handler(event, _context):
    lote_id        = event.get("lote_id", "")
    email_analista = event.get("email_analista", "")
    factura_ids    = event.get("factura_ids", [])

    if not lote_id or not email_analista:
        return {
            "statusCode": 400,
            "error": "Faltan campos requeridos: lote_id, email_analista.",
        }

    if not factura_ids:
        return {
            "statusCode": 400,
            "error": "El campo factura_ids está vacío.",
        }

    # 1. Consultar DynamoDB en batch para obtener el estado de cada factura
    facturas = batch_get_facturas(factura_ids)

    # 2. Construir resumen por estado
    aprobadas   = [f for f in facturas if f.estado == EstadoFactura.APROBADO]
    en_revision = [f for f in facturas if f.estado == EstadoFactura.REQUIERE_REVISION]
    ids_revision = [f.id for f in en_revision]

    total    = len(facturas)
    timestamp = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    print(
        f"[{lote_id}] Resumen: {total} procesadas, "
        f"{len(aprobadas)} aprobadas, {len(en_revision)} en revisión."
    )

    # 3. Construir y enviar correo HTML
    asunto = f"FacturaFlow — Lote {lote_id[:8]}... procesado ({total} facturas)"
    html   = _construir_html(lote_id, total, len(aprobadas), len(en_revision), ids_revision, timestamp)
    texto  = _construir_texto_plano(lote_id, total, len(aprobadas), len(en_revision), ids_revision, timestamp)

    _enviar_correo(email_analista, asunto, html, texto)

    print(f"[{lote_id}] Correo enviado a {email_analista}.")

    return {
        "statusCode": 200,
        "lote_id":          lote_id,
        "total":            total,
        "aprobadas":        len(aprobadas),
        "requieren_revision": len(en_revision),
    }
