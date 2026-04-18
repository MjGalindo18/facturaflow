"""
Tests unitarios para functions/recibir_zip/handler.py

Comportamiento verificado:
  - ZIP válido → 202, lote_id único, total_facturas correcto
  - Cuerpo vacío → 400
  - Archivo no es ZIP → 400
  - ZIP sin PDFs → 400
  - ZIP en base64 (API Gateway) → se decodifica y procesa correctamente
  - Filtrado de metadatos macOS (__MACOSX) y archivos ocultos (.)
  - S3: cifrado AES-256, clave con formato facturas/{lote_id}/{nombre}
  - SQS: mensajes con campos correctos, lotes de 10 cuando hay > 10 PDFs
"""
import base64
import importlib.util
import io
import json
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest

# ── Carga el handler con nombre único para evitar colisión entre funciones ────
_HANDLER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "functions", "recibir_zip", "handler.py"
)
_spec = importlib.util.spec_from_file_location("recibir_zip_handler", _HANDLER_PATH)
modulo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modulo)
handler = modulo.handler


# ── Helpers para construir ZIPs en memoria ────────────────────────────────────

def _zip_con_pdfs(*nombres: str) -> bytes:
    """Crea un ZIP en memoria con archivos PDF ficticios."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre in nombres:
            zf.writestr(nombre, b"%PDF-1.4 contenido ficticio")
    return buf.getvalue()


def _zip_sin_pdfs() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notas.txt", "texto plano")
        zf.writestr("imagen.png", b"\x89PNG")
    return buf.getvalue()


def _evento(body, base64_encoded=False):
    return {
        "body": base64.b64encode(body).decode() if base64_encoded else body,
        "isBase64Encoded": base64_encoded,
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singletons():
    """Resetea los singletons de boto3 entre tests para evitar contaminación."""
    modulo._s3 = None
    modulo._sqs = None
    yield
    modulo._s3 = None
    modulo._sqs = None


@pytest.fixture()
def mock_aws():
    """Retorna (mock_s3, mock_sqs) con put_object y send_message_batch configurados."""
    s3 = MagicMock()
    sqs = MagicMock()
    sqs.send_message_batch.return_value = {"Successful": [], "Failed": []}
    with patch.object(modulo, "boto3") as mock_boto3:
        mock_boto3.client.side_effect = lambda svc, **_: s3 if svc == "s3" else sqs
        yield s3, sqs


# ── Caso feliz ────────────────────────────────────────────────────────────────

class TestCasoFeliz:
    def test_202_con_un_pdf(self, mock_aws):
        evento = _evento(_zip_con_pdfs("factura1.pdf"))
        resp = handler(evento, None)
        assert resp["statusCode"] == 202

    def test_respuesta_incluye_lote_id(self, mock_aws):
        evento = _evento(_zip_con_pdfs("factura1.pdf"))
        resp = handler(evento, None)
        body = json.loads(resp["body"])
        assert "lote_id" in body
        assert len(body["lote_id"]) == 36  # UUID v4

    def test_total_facturas_correcto(self, mock_aws):
        evento = _evento(_zip_con_pdfs("f1.pdf", "f2.pdf", "f3.pdf"))
        resp = handler(evento, None)
        body = json.loads(resp["body"])
        assert body["total_facturas"] == 3

    def test_content_type_json(self, mock_aws):
        evento = _evento(_zip_con_pdfs("factura1.pdf"))
        resp = handler(evento, None)
        assert resp["headers"]["Content-Type"] == "application/json"

    def test_lote_id_unico_por_invocacion(self, mock_aws):
        evento = _evento(_zip_con_pdfs("f.pdf"))
        id1 = json.loads(handler(evento, None)["body"])["lote_id"]
        modulo._s3 = None
        modulo._sqs = None
        id2 = json.loads(handler(evento, None)["body"])["lote_id"]
        assert id1 != id2


# ── Casos de error ────────────────────────────────────────────────────────────

class TestErrores:
    def test_cuerpo_vacio_devuelve_400(self, mock_aws):
        resp = handler({"body": "", "isBase64Encoded": False}, None)
        assert resp["statusCode"] == 400
        assert "error" in json.loads(resp["body"])

    def test_body_none_devuelve_400(self, mock_aws):
        resp = handler({"body": None, "isBase64Encoded": False}, None)
        assert resp["statusCode"] == 400

    def test_archivo_no_es_zip_devuelve_400(self, mock_aws):
        evento = _evento(b"esto no es un zip")
        resp = handler(evento, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "ZIP" in body["error"]

    def test_zip_sin_pdfs_devuelve_400(self, mock_aws):
        evento = _evento(_zip_sin_pdfs())
        resp = handler(evento, None)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "PDF" in body["error"]


# ── Decodificación base64 (API Gateway) ───────────────────────────────────────

class TestBase64:
    def test_zip_en_base64_se_procesa(self, mock_aws):
        evento = _evento(_zip_con_pdfs("factura.pdf"), base64_encoded=True)
        resp = handler(evento, None)
        assert resp["statusCode"] == 202

    def test_total_facturas_correcto_en_base64(self, mock_aws):
        evento = _evento(_zip_con_pdfs("a.pdf", "b.pdf"), base64_encoded=True)
        body = json.loads(handler(evento, None)["body"])
        assert body["total_facturas"] == 2


# ── Filtrado de archivos no deseados ─────────────────────────────────────────

class TestFiltrado:
    def test_macosx_excluido(self, mock_aws):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("factura.pdf",          b"%PDF real")
            zf.writestr("__MACOSX/factura.pdf", b"%PDF metadata macOS")
        resp = handler(_evento(buf.getvalue()), None)
        body = json.loads(resp["body"])
        assert body["total_facturas"] == 1

    def test_archivos_ocultos_excluidos(self, mock_aws):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("factura.pdf", b"%PDF real")
            zf.writestr(".hidden.pdf", b"%PDF oculto")
        resp = handler(_evento(buf.getvalue()), None)
        body = json.loads(resp["body"])
        assert body["total_facturas"] == 1

    def test_no_pdf_en_zip_mixto_no_cuenta(self, mock_aws):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("factura.pdf", b"%PDF")
            zf.writestr("notas.docx",  b"docx")
            zf.writestr("imagen.jpg",  b"jpeg")
        resp = handler(_evento(buf.getvalue()), None)
        body = json.loads(resp["body"])
        assert body["total_facturas"] == 1


# ── Integración con S3 ────────────────────────────────────────────────────────

class TestS3:
    def test_put_object_llamado_por_cada_pdf(self, mock_aws):
        s3, _ = mock_aws
        evento = _evento(_zip_con_pdfs("f1.pdf", "f2.pdf"))
        handler(evento, None)
        assert s3.put_object.call_count == 2

    def test_cifrado_aes256(self, mock_aws):
        s3, _ = mock_aws
        evento = _evento(_zip_con_pdfs("factura.pdf"))
        handler(evento, None)
        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["ServerSideEncryption"] == "AES256"

    def test_formato_s3_key(self, mock_aws):
        s3, _ = mock_aws
        evento = _evento(_zip_con_pdfs("factura.pdf"))
        resp = handler(evento, None)
        lote_id = json.loads(resp["body"])["lote_id"]
        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Key"] == f"facturas/{lote_id}/factura.pdf"

    def test_content_type_pdf(self, mock_aws):
        s3, _ = mock_aws
        evento = _evento(_zip_con_pdfs("factura.pdf"))
        handler(evento, None)
        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["ContentType"] == "application/pdf"


# ── Integración con SQS ───────────────────────────────────────────────────────

class TestSQS:
    def test_send_message_batch_llamado(self, mock_aws):
        _, sqs = mock_aws
        evento = _evento(_zip_con_pdfs("f.pdf"))
        handler(evento, None)
        sqs.send_message_batch.assert_called_once()

    def test_mensaje_contiene_campos_requeridos(self, mock_aws):
        _, sqs = mock_aws
        evento = _evento(_zip_con_pdfs("factura.pdf"))
        handler(evento, None)
        entries = sqs.send_message_batch.call_args.kwargs["Entries"]
        msg = json.loads(entries[0]["MessageBody"])
        assert "lote_id" in msg
        assert "s3_key" in msg
        assert "nombre_archivo" in msg

    def test_nombre_archivo_en_mensaje(self, mock_aws):
        _, sqs = mock_aws
        evento = _evento(_zip_con_pdfs("factura_enero.pdf"))
        handler(evento, None)
        entries = sqs.send_message_batch.call_args.kwargs["Entries"]
        msg = json.loads(entries[0]["MessageBody"])
        assert msg["nombre_archivo"] == "factura_enero.pdf"

    def test_lotes_de_10_con_mas_de_10_pdfs(self, mock_aws):
        """SQS solo admite 10 mensajes por batch; el handler debe hacer 2 llamadas para 11 PDFs."""
        _, sqs = mock_aws
        nombres = [f"factura_{i:02d}.pdf" for i in range(11)]
        evento = _evento(_zip_con_pdfs(*nombres))
        handler(evento, None)
        assert sqs.send_message_batch.call_count == 2

    def test_un_solo_batch_con_exactamente_10_pdfs(self, mock_aws):
        _, sqs = mock_aws
        nombres = [f"factura_{i:02d}.pdf" for i in range(10)]
        evento = _evento(_zip_con_pdfs(*nombres))
        handler(evento, None)
        assert sqs.send_message_batch.call_count == 1
