"""Audita la consistencia entre EVIDENCIA_ACTIVIDAD y static/uploads.

Ejecutar en el mismo entorno de la aplicación:
    python scripts/auditar_evidencias.py

No modifica archivos ni la base de datos. Devuelve código 1 si detecta
inconsistencias para que pueda usarse en una verificación de despliegue.
"""

from pathlib import Path
import sys


# Permite ejecutar el script directamente desde la raíz del repositorio.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web_app import create_app
from web_app.database import ejecutar_query
from web_app.modules.evidencias.services import _evidence_file_path


def main() -> int:
    app = create_app()
    with app.app_context():
        rows = ejecutar_query(
            'SELECT "ID", "NOMBRE_ARCHIVO", "URL_ARCHIVO", "TAMANO_BYTES", "CREADO_EN" '
            'FROM "EVIDENCIA_ACTIVIDAD" '
            'WHERE "URL_ARCHIVO" IS NOT NULL OR "NOMBRE_ARCHIVO" IS NOT NULL'
        )

        issues = []
        for row in rows:
            path = _evidence_file_path(row["URL_ARCHIVO"])
            expected_size = int(row["TAMANO_BYTES"] or 0)
            if not path or not Path(path).is_file():
                issues.append((row, "archivo no encontrado o URL fuera de static/uploads/evidencias"))
                continue
            actual_size = Path(path).stat().st_size
            if expected_size != actual_size:
                issues.append((row, f"tamaño en BD: {expected_size} B; tamaño en disco: {actual_size} B"))

        print(f"Evidencias con archivo: {len(rows)}")
        print(f"Inconsistencias: {len(issues)}")
        for row, reason in issues:
            print(f'- {row["ID"]} | {row["CREADO_EN"]} | {row["NOMBRE_ARCHIVO"] or "sin nombre"}: {reason}')
        return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
