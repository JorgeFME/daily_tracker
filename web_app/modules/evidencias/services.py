import os
import uuid
from urllib.parse import urlparse
from flask import current_app
from werkzeug.utils import secure_filename
from config import Config


class EvidenceUploadError(Exception):
    """Error controlado al persistir una evidencia en el almacenamiento local."""


class ProjectStorageQuotaExceeded(EvidenceUploadError):
    """La carga excedería la cuota configurada para el proyecto."""


class EvidencePersistenceError(EvidenceUploadError):
    """No fue posible vincular el archivo con su evidencia en la base de datos."""


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def _folder_size_mb(path: str) -> float:
    """Calcula el tamaño en MB de una carpeta recursivamente."""
    total = 0
    if not os.path.isdir(path):
        return 0.0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total / (1024 * 1024)


def _save_upload(file, proyecto_id: str, actividad_id: str) -> dict | None:
    """
    Guarda el archivo bajo static/uploads/evidencias/<proyecto_id>/<actividad_id>/
    Verifica cuota del proyecto antes de guardar.
    Devuelve metadatos para la BD, o None si hay error.
    """
    if not file or not file.filename:
        return None
    if not _allowed(file.filename):
        return None

    upload_base = os.path.join(current_app.static_folder, "uploads", "evidencias")
    max_project_mb = Config.MAX_PROJECT_MB

    # Verificar cuota del proyecto
    project_folder = os.path.join(upload_base, proyecto_id)
    used_mb = _folder_size_mb(project_folder)
    if used_mb >= max_project_mb:
        raise ProjectStorageQuotaExceeded(
            f"El proyecto ha alcanzado su límite de almacenamiento "
            f"({max_project_mb} MB). Elimina evidencias antiguas para continuar."
        )

    folder = os.path.join(project_folder, actividad_id)
    os.makedirs(folder, exist_ok=True)

    original = secure_filename(file.filename)
    if not original:
        raise EvidenceUploadError("El nombre del archivo no es válido.")
    unique    = f"{uuid.uuid4().hex}_{original}"
    full_path = os.path.join(folder, unique)
    try:
        file.save(full_path)
        size = os.path.getsize(full_path)
    except OSError as error:
        try:
            if os.path.exists(full_path):
                os.remove(full_path)
        except OSError:
            pass
        raise EvidenceUploadError("No se pudo guardar el archivo adjunto.") from error

    # La comprobación previa evita trabajo innecesario; esta segunda considera
    # el tamaño real del archivo y no permite rebasar la cuota por una carga.
    if _folder_size_mb(project_folder) > max_project_mb:
        try:
            os.remove(full_path)
            if not os.listdir(folder):
                os.rmdir(folder)
            if os.path.isdir(project_folder) and not os.listdir(project_folder):
                os.rmdir(project_folder)
        except OSError:
            pass
        raise ProjectStorageQuotaExceeded(
            f"El archivo supera el límite de almacenamiento del proyecto "
            f"({max_project_mb} MB)."
        )

    rel_url = f"/uploads/evidencias/{proyecto_id}/{actividad_id}/{unique}"
    return {"url": rel_url, "nombre": original, "mime": file.mimetype, "size": size}


def _evidence_file_path(file_url: str | None) -> str | None:
    """Resuelve una URL de evidencia sólo si apunta al almacenamiento local esperado."""
    if not file_url:
        return None

    relative_path = urlparse(file_url).path.lstrip("/")
    expected_prefix = os.path.join("uploads", "evidencias")
    normalized_relative = os.path.normpath(relative_path)
    if not normalized_relative.startswith(expected_prefix + os.sep):
        return None

    evidence_root = os.path.abspath(
        os.path.join(current_app.static_folder, "uploads", "evidencias")
    )
    candidate = os.path.abspath(os.path.join(current_app.static_folder, normalized_relative))
    try:
        if os.path.commonpath((evidence_root, candidate)) != evidence_root:
            return None
    except ValueError:
        return None
    return candidate


def _delete_evidence_file(file_url: str | None):
    disk_path = _evidence_file_path(file_url)
    if not disk_path or not os.path.isfile(disk_path):
        return
    try:
        os.remove(disk_path)
        act_folder = os.path.dirname(disk_path)
        if os.path.isdir(act_folder) and not os.listdir(act_folder):
            os.rmdir(act_folder)
        project_folder = os.path.dirname(act_folder)
        if os.path.isdir(project_folder) and not os.listdir(project_folder):
            os.rmdir(project_folder)
    except Exception as e:
        print(f"[_delete_evidence_file] no se pudo eliminar archivo: {e}")


def guardar_evidencia_actividad(actividad_id: str, proyecto_id: str, datos: dict, archivo=None):
    """Guarda una evidencia y, si hay archivo, mantiene disco y BD consistentes.

    Se usa desde la actividad y desde el registro de horas para que ambos
    recorran exactamente el mismo flujo de validación, guardado y limpieza.
    """
    meta = None
    if archivo and archivo.filename:
        if not _allowed(archivo.filename):
            raise EvidenceUploadError("Tipo de archivo no permitido.")
        meta = _save_upload(archivo, proyecto_id, actividad_id)
        datos.update({
            "url_archivo": meta["url"],
            "nombre_archivo": meta["nombre"],
            "mime_type": meta["mime"],
            "tamano_bytes": str(meta["size"]),
        })

    # Se importa aquí para evitar una dependencia circular al cargar módulos.
    from web_app.modules.evidencias.queries import crear_evidencia

    if crear_evidencia(actividad_id, datos):
        return meta

    if meta:
        _delete_evidence_file(meta["url"])
    raise EvidencePersistenceError("No se pudo guardar la evidencia.")
