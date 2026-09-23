from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import Flask
from werkzeug.datastructures import FileStorage

from web_app.modules.evidencias.queries import crear_evidencia
from web_app.modules.evidencias.services import (
    EvidencePersistenceError,
    guardar_evidencia_actividad,
)


class EvidenciasTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.app = Flask(__name__, static_folder=self.temp_dir.name)
        self.datos = {
            "id_tipo": "tipo-1",
            "titulo": "Prueba",
            "subido_por": "usuario-1",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _archivo(self):
        return FileStorage(
            stream=BytesIO(b"contenido de prueba"),
            filename="evidencia.txt",
            content_type="text/plain",
        )

    def test_crear_evidencia_respeta_orden_de_metadatos(self):
        datos = {
            **self.datos,
            "nombre_archivo": "evidencia.txt",
            "url_archivo": "/uploads/evidencias/p/a/archivo.txt",
            "mime_type": "text/plain",
            "tamano_bytes": "18",
        }
        with patch("web_app.modules.evidencias.queries.ejecutar_dml", return_value=True) as dml:
            self.assertTrue(crear_evidencia("actividad-1", datos))

        self.assertEqual(
            dml.call_args.args[1],
            (
                "actividad-1", "tipo-1", "Prueba", None,
                "evidencia.txt", "/uploads/evidencias/p/a/archivo.txt",
                "text/plain", 18, "usuario-1",
            ),
        )

    def test_limpia_archivo_si_falla_la_base_de_datos(self):
        with self.app.app_context(), patch(
            "web_app.modules.evidencias.queries.crear_evidencia", return_value=False
        ):
            with self.assertRaises(EvidencePersistenceError):
                guardar_evidencia_actividad("actividad-1", "proyecto-1", dict(self.datos), self._archivo())

            uploads = Path(self.temp_dir.name) / "uploads" / "evidencias"
            self.assertFalse(any(uploads.rglob("*")) if uploads.exists() else False)

    def test_conserva_archivo_y_metadatos_si_se_guarda_la_evidencia(self):
        datos = dict(self.datos)
        with self.app.app_context(), patch(
            "web_app.modules.evidencias.queries.crear_evidencia", return_value=True
        ):
            meta = guardar_evidencia_actividad("actividad-1", "proyecto-1", datos, self._archivo())

        self.assertGreater(meta["size"], 0)
        self.assertEqual(datos["nombre_archivo"], "evidencia.txt")
        self.assertEqual(datos["tamano_bytes"], str(meta["size"]))
        self.assertTrue((Path(self.temp_dir.name) / meta["url"].lstrip("/")).is_file())


if __name__ == "__main__":
    unittest.main()
