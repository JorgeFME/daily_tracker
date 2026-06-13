import os
import uuid
from flask import current_app
from werkzeug.utils import secure_filename
from config import Config

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
        raise ValueError(
            f"El proyecto ha alcanzado su límite de almacenamiento "
            f"({max_project_mb} MB). Elimina evidencias antiguas para continuar."
        )

    folder = os.path.join(project_folder, actividad_id)
    os.makedirs(folder, exist_ok=True)

    original  = secure_filename(file.filename)
    unique    = f"{uuid.uuid4().hex}_{original}"
    full_path = os.path.join(folder, unique)
    file.save(full_path)
    size = os.path.getsize(full_path)

    rel_url = f"/uploads/evidencias/{proyecto_id}/{actividad_id}/{unique}"
    return {"url": rel_url, "nombre": original, "mime": file.mimetype, "size": size}


def _delete_evidence_file(file_url: str | None):
    if not file_url:
        return
    disk_path = os.path.join(current_app.static_folder, file_url.lstrip("/").replace("uploads/evidencias/", "uploads/evidencias/"))
    # Clean fallback resolution in case of path mismatches
    if not os.path.isfile(disk_path):
        # Fallback to static folder base matching
        disk_path = os.path.join(current_app.static_folder, file_url.lstrip("/"))
        if not os.path.isfile(disk_path):
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
