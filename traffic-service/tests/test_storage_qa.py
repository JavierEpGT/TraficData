
import sqlite3

import pytest

from app.storage import Storage


def test_qa_storage_cierra_conexiones_sqlite(tmp_path, monkeypatch):
    """
    QA-INC-001:
    Verificar que Storage cierre las conexiones SQLite
    después de inicializar la base de datos.
    """

    conexiones = []
    conexion_original = Storage._connect

    def conexion_monitoreada(self):
        conn = conexion_original(self)
        conexiones.append(conn)
        return conn

    monkeypatch.setattr(Storage, "_connect", conexion_monitoreada)

    try:
        ruta_db = tmp_path / "qa_storage.db"

        Storage(db_path=str(ruta_db))

        assert conexiones, "No se registraron conexiones SQLite"

        for conn in conexiones:
            with pytest.raises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    finally:
        for conn in conexiones:
            try:
                conn.close()
            except sqlite3.ProgrammingError:
                pass
