import os
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from config import Config

from web_app.database import ejecutar_query
from web_app.modules.tracker.routes import _catalogo_base
from web_app.modules.evidencias.queries import (
    obtener_evidencias_actividad,
    obtener_evidencia_por_id,
    obtener_evidencias_filtradas,
    obtener_actividades_con_evidencia,
    crear_evidencia,
    actualizar_evidencia,
    eliminar_evidencia,
)
from web_app.modules.evidencias.services import (
    _allowed,
    _folder_size_mb,
    _save_upload,
    _delete_evidence_file,
)

evidencias_bp = Blueprint("evidencias_bp", __name__)


def _serialize_evidencia(row):
    return {
        "id": row["ID"],
        "actividad_id": row["ID_ACTIVIDAD"],
        "tipo_id": row["ID_TIPO"],
        "titulo": row.get("TITULO") or row.get("NOMBRE_ARCHIVO") or row.get("TIPO") or "Sin título",
        "contenido_texto": row.get("CONTENIDO_TEXTO") or "",
        "nombre_archivo": row.get("NOMBRE_ARCHIVO") or "",
        "url_archivo": row.get("URL_ARCHIVO") or "",
        "mime_type": row.get("MIME_TYPE") or "",
        "tamano_bytes": int(row.get("TAMANO_BYTES") or 0),
        "subido_por": row.get("SUBIDO_POR") or "",
        "subido_por_nombre": row.get("USUARIO_NOMBRE") or row.get("SUBIDO_POR") or "Sin usuario",
        "creado_en": str(row.get("CREADO_EN") or ""),
        "tipo": row.get("TIPO") or "Sin tipo",
        "actividad_nombre": row.get("ACTIVIDAD_NOMBRE") or "Sin actividad",
        "proyecto_id": row.get("PROYECTO_ID") or "",
        "proyecto_nombre": row.get("PROYECTO_NOMBRE") or "Sin proyecto",
        "es_imagen": (row.get("MIME_TYPE") or "").startswith("image/"),
    }


@evidencias_bp.route("/uploads/evidencias/<proyecto_id>/<actividad_id>/<filename>")
def servir_evidencia(proyecto_id, actividad_id, filename):
    """Sirve los archivos de evidencia almacenados en el servidor."""
    upload_base = os.path.join(current_app.static_folder, "uploads", "evidencias")
    folder = os.path.join(upload_base, proyecto_id, actividad_id)
    return send_file(os.path.join(folder, filename))


@evidencias_bp.route("/api/storage/<proyecto_id>")
def api_storage_proyecto(proyecto_id):
    """Devuelve el uso de almacenamiento de un proyecto (en MB)."""
    upload_base = os.path.join(current_app.static_folder, "uploads", "evidencias")
    project_folder = os.path.join(upload_base, proyecto_id)
    used  = round(_folder_size_mb(project_folder), 2)
    quota = Config.MAX_PROJECT_MB
    return jsonify({
        "proyecto_id": proyecto_id,
        "used_mb":     used,
        "quota_mb":    quota,
        "pct":         round((used / quota) * 100, 1) if quota else 0,
        "free_mb":     round(quota - used, 2),
    })


@evidencias_bp.route("/actividades/<actividad_id>/evidencia", methods=["POST"])
def agregar_evidencia(actividad_id):
    datos = request.form.to_dict()
    from web_app.modules.dashboard.queries import obtener_actividad_por_id
    actividad = obtener_actividad_por_id(actividad_id)
    proyecto_id = actividad["ID_PROYECTO"] if actividad else "sin_proyecto"

    archivo = request.files.get("archivo")
    if archivo and archivo.filename:
        if not _allowed(archivo.filename):
            return jsonify({"status": "error", "message": "Tipo de archivo no permitido."}), 400
        try:
            meta = _save_upload(archivo, proyecto_id, actividad_id)
        except ValueError as quota_err:
            return jsonify({"status": "error", "message": str(quota_err)}), 413
        if meta:
            datos["url_archivo"]    = meta["url"]
            datos["nombre_archivo"] = meta["nombre"]
            datos["mime_type"]      = meta["mime"]
            datos["tamano_bytes"]   = str(meta["size"])

    if crear_evidencia(actividad_id, datos):
        return jsonify({"status": "success", "message": "Evidencia guardada."})
    return jsonify({"status": "error", "message": "Error al guardar la evidencia."}), 500


@evidencias_bp.route("/evidencia/<evidencia_id>", methods=["PUT", "DELETE"])
def api_evidencia(evidencia_id):
    if request.method == "PUT":
        datos = request.get_json(silent=True) or {}
        if not (datos.get("id_tipo") or "").strip():
            return jsonify({"status": "error", "message": "El tipo de evidencia es obligatorio."}), 400
        if not actualizar_evidencia(evidencia_id, datos):
            return jsonify({"status": "error", "message": "No se pudo actualizar la evidencia."}), 500
        evidencia = obtener_evidencia_por_id(evidencia_id)
        if not evidencia:
            return jsonify({"status": "error", "message": "Evidencia no encontrada."}), 404
        return jsonify({"status": "success", "evidencia": _serialize_evidencia(evidencia)})

    rows = ejecutar_query('SELECT "URL_ARCHIVO" FROM "EVIDENCIA_ACTIVIDAD" WHERE "ID"=?', (evidencia_id,))
    if rows:
        _delete_evidence_file(rows[0].get("URL_ARCHIVO"))

    if eliminar_evidencia(evidencia_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500


@evidencias_bp.route("/evidencias")
def vista_evidencias():
    base = _catalogo_base()
    from web_app.modules.catalogos.queries import obtener_tipos_evidencia
    return render_template(
        "evidencias/evidencias.html",
        projects=base["projects"],
        users=base["users"],
        tipos_evidencia=obtener_tipos_evidencia(),
        actividades_con_evidencia=obtener_actividades_con_evidencia(),
    )


@evidencias_bp.route("/api/evidencias/explore")
def api_evidencias_explore():
    proyecto_id = request.args.get("proyecto_id") or None
    actividad_id = request.args.get("actividad_id") or None
    tipo_id = request.args.get("tipo_id") or None
    usuario_id = request.args.get("usuario_id") or None
    fecha_desde = request.args.get("fecha_desde") or None
    fecha_hasta = request.args.get("fecha_hasta") or None
    q = (request.args.get("q") or "").strip() or None

    evidencias = obtener_evidencias_filtradas(
        proyecto_id=proyecto_id,
        actividad_id=actividad_id,
        tipo_id=tipo_id,
        usuario_id=usuario_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
    )
    actividades = obtener_actividades_con_evidencia(proyecto_id=proyecto_id)

    return jsonify({
        "evidencias": [_serialize_evidencia(row) for row in evidencias],
        "actividades": [
            {
                "id": row["ID"],
                "proyecto_id": row["ID_PROYECTO"],
                "nombre": row["NOMBRE_ACTIVIDAD"],
                "proyecto_nombre": row["NOMBRE_PROYECTO"],
            }
            for row in actividades
        ],
    })
