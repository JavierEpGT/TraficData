
"""Pruebas adicionales de QA para el Data Warehouse."""

import httpx
import pytest
import respx

from app.dw_client import DwClient, DwClientError


URL = "https://dw.example.com/ingest"
PAYLOAD = {"reading_id": "qa-test-001"}


@respx.mock
def test_qa_envio_exitoso():
    ruta = respx.post(URL).mock(
        return_value=httpx.Response(200)
    )

    with DwClient(URL, max_attempts=1) as cliente:
        cliente.send(PAYLOAD)

    assert ruta.call_count == 1


@respx.mock
def test_qa_error_400_sin_reintentos():
    ruta = respx.post(URL).mock(
        return_value=httpx.Response(400)
    )

    with DwClient(URL, max_attempts=3) as cliente:
        with pytest.raises(DwClientError):
            cliente.send(PAYLOAD)

    assert ruta.call_count == 1


@respx.mock
def test_qa_error_500_con_reintento(monkeypatch):
    monkeypatch.setattr(
        "app.dw_client.time.sleep",
        lambda segundos: None
    )

    ruta = respx.post(URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200)
        ]
    )

    with DwClient(URL, max_attempts=3) as cliente:
        cliente.send(PAYLOAD)

    assert ruta.call_count == 2
