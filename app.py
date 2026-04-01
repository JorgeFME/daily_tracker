import os
import uuid
from datetime import datetime, timedelta, date
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, jsonify, url_for, send_file
import io
from db import (
    load_env_from_dotenv,
    ejecutar_query,
    # Daily tracker
    guardar_registro_actividad,
    get_horas_semanales,
    get_horas_diarias,
    get_horas_ausencia_dia,
    get_horas_ausencia_periodo,
    agregar_categoria_db,
    eliminar_categoria_db,
    obtener_datos_grafica_proyectos,
    obtener_registros_recientes_filtrados,
    # Actividades
    obtener_actividades,
    obtener_actividad_por_id,
    obtener_actividades_hijas,
    obtener_actividad_nombre,
    eliminar_actividad,
    obtener_registros,
    obtener_registro_por_id,
    actualizar_registro,
    eliminar_registro,
    obtener_datos_reporte_proyecto,
    crear_actividad,
    actualizar_actividad,
    guardar_responsables_actividad,
    obtener_actividades_por_proyecto,
    recalcular_avance_actividad,
    # Detalle
    obtener_historial_actividad,
    obtener_detalles_actividad,
    crear_detalle_actividad,
    toggle_detalle_completado,
    eliminar_detalle_actividad,
    # Evidencia
    obtener_evidencias_actividad,
    crear_evidencia,
    eliminar_evidencia,
    # Catálogos
    obtener_estatus_actividad,
    obtener_tipos_evidencia,
    obtener_tipos_actividad,
    obtener_recursos,
    obtener_recursos_actividad,
    guardar_recursos_actividad,
    reasignar_registros_proyecto,
)

load_env_from_dotenv()
app = Flask(__name__)

# ── Configuración de uploads ───────────────────────────────────────────────
# Estructura en disco:
#   static/uploads/evidencias/<proyecto_id>/<actividad_id>/<uuid>_<filename>
#
# Para migrar a SAP DMS en el futuro: reemplaza _save_upload() y
# servir_evidencia() — el resto de la app no cambia.

UPLOAD_BASE    = os.path.join(app.root_path, "static", "uploads", "evidencias")
MAX_FILE_MB    = int(os.getenv("MAX_FILE_MB", "20"))
MAX_PROJECT_MB = int(os.getenv("MAX_PROJECT_MB", "500"))   # cuota por proyecto
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {
    # Capturas
    "png", "jpg", "jpeg", "gif", "webp", "bmp",
    # Correos
    "eml", "msg",
    # Documentos
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
}

def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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

    # Verificar cuota del proyecto
    project_folder = os.path.join(UPLOAD_BASE, proyecto_id)
    used_mb = _folder_size_mb(project_folder)
    if used_mb >= MAX_PROJECT_MB:
        raise ValueError(
            f"El proyecto ha alcanzado su límite de almacenamiento "
            f"({MAX_PROJECT_MB} MB). Elimina evidencias antiguas para continuar."
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


@app.route("/uploads/evidencias/<proyecto_id>/<actividad_id>/<filename>")
def servir_evidencia(proyecto_id, actividad_id, filename):
    """Sirve los archivos de evidencia almacenados en el servidor."""
    folder = os.path.join(UPLOAD_BASE, proyecto_id, actividad_id)
    return send_file(os.path.join(folder, filename))


@app.route("/api/storage/<proyecto_id>")
def api_storage_proyecto(proyecto_id):
    """Devuelve el uso de almacenamiento de un proyecto (en MB)."""
    project_folder = os.path.join(UPLOAD_BASE, proyecto_id)
    used  = round(_folder_size_mb(project_folder), 2)
    quota = MAX_PROJECT_MB
    return jsonify({
        "proyecto_id": proyecto_id,
        "used_mb":     used,
        "quota_mb":    quota,
        "pct":         round((used / quota) * 100, 1) if quota else 0,
        "free_mb":     round(quota - used, 2),
    })


# ── Helpers de catálogos reutilizables ────────────────────────────────────


def _catalogo_base():
    """Datos comunes para todas las páginas."""
    return {
        "users": ejecutar_query(
            'SELECT "ID","NOMBRE_COMPLETO" FROM "USUARIOS" WHERE "ACTIVO"=1 ORDER BY "NOMBRE_COMPLETO"'
        ),
        "projects": ejecutar_query(
            'SELECT "ID","NOMBRE_PROYECTO" FROM "PROYECTOS" WHERE "ACTIVO"=1'
        ),
    }


# ── DAILY TRACKER ─────────────────────────────────────────────────────────


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        datos = request.form.to_dict()
        try:
            ok = guardar_registro_actividad(datos)
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        if ok:
            if datos.get("actividad_id"):
                recalcular_avance_actividad(datos.get("actividad_id"))
            return jsonify({"status": "success", "message": "¡Registro guardado correctamente!"})
        return jsonify({"status": "error", "message": "Hubo un fallo al conectar con SAP HANA."}), 500

    base = _catalogo_base()
    return render_template(
        "index.html",
        users=base["users"],
        projects=base["projects"],
        tipos_actividad=obtener_tipos_actividad(),
    )


@app.route("/api/weekly_hours")
def api_weekly_hours():
    return jsonify(
        {
            "total": get_horas_semanales(
                request.args.get("user"), request.args.get("date")
            )
        }
    )


@app.route("/api/daily_hours")
def api_daily_hours():
    user_id = request.args.get("user")
    fecha = request.args.get("date")
    total = get_horas_diarias(user_id, fecha)
    horas_ausencia = get_horas_ausencia_dia(user_id, fecha)
    return jsonify({"total": total, "horas_ausencia": horas_ausencia})


@app.route("/api/actividades_proyecto")
def api_actividades_proyecto():
    proyecto_id = request.args.get("proyecto")
    if not proyecto_id:
        return jsonify([])
    acts = obtener_actividades_por_proyecto(proyecto_id)
    return jsonify(
        [
            {
                "id": a["ID"],
                "nombre": a["NOMBRE_ACTIVIDAD"],
                "horas": float(a["HORAS_INVERTIDAS"]),
                "estatus": a["ESTATUS"],
            }
            for a in acts
        ]
    )


@app.route("/api/categories", methods=["POST"])
def add_category():
    name = request.json.get("name")
    return (
        jsonify({"status": "success"})
        if agregar_categoria_db(name)
        else (jsonify({"status": "error"}), 500)
    )


@app.route("/api/categories/<id>", methods=["DELETE"])
def delete_category(id):
    return (
        jsonify({"status": "success"})
        if eliminar_categoria_db(id)
        else (jsonify({"status": "error"}), 500)
    )


@app.route("/api/dashboard_data")
def dashboard_data():
    user_id = request.args.get("user") or None
    period = request.args.get("period", "month")
    fecha_ref = request.args.get("fecha_ref") or None

    try:
        ref_date = datetime.strptime(fecha_ref, "%Y-%m-%d").date() if fecha_ref else date.today()
    except Exception:
        ref_date = date.today()

    if period == 'day':
        start_date = ref_date
        end_date = ref_date
    elif period == 'week':
        start_date = ref_date - timedelta(days=ref_date.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == 'month':
        from calendar import monthrange
        start_date = date(ref_date.year, ref_date.month, 1)
        end_date = date(ref_date.year, ref_date.month, monthrange(ref_date.year, ref_date.month)[1])
    elif period == 'year':
        start_date = date(ref_date.year, 1, 1)
        end_date = date(ref_date.year, 12, 31)
    else:
        start_date = date.today()
        end_date = date.today()

    ausencia_horas = get_horas_ausencia_periodo(user_id, start_date.isoformat(), end_date.isoformat())

    proyectos = obtener_datos_grafica_proyectos(
        user_id=user_id, period=period, fecha_ref=fecha_ref
    )
    registros = obtener_registros_recientes_filtrados(
        user_id=user_id, period=period, fecha_ref=fecha_ref, limite=10
    )

    return jsonify(
        {
            "mode": "global" if not user_id else "individual",
            "period": period,
            "chart": {
                "labels": [p["NOMBRE_PROYECTO"] for p in proyectos],
                "values": [float(p["TOTAL"]) for p in proyectos],
            },
            "records": [
                {
                    "fecha": str(r["FECHA"]),
                    "usuario": r.get("USUARIO", ""),
                    "proyecto": r["NOMBRE_PROYECTO"],
                    "tipo": r.get("TIPO") or "—",
                    "accion": r["ACCION"],
                    "horas": float(r["HORAS"]),
                    "id": r["ID"],
                }
                for r in registros
            ],
            "ausencia_horas": float(ausencia_horas),
        }
    )


# ── ACTIVIDADES ────────────────────────────────────────────────────────────


@app.route("/actividades")
def actividades():
    proyecto_id  = request.args.get("proyecto")    or None
    estatus_id   = request.args.get("estatus")     or None
    usuario_id   = request.args.get("usuario")     or None
    fecha_desde  = request.args.get("fecha_desde") or None
    fecha_hasta  = request.args.get("fecha_hasta") or None
    base = _catalogo_base()
    return render_template(
        "actividades.html",
        actividades=obtener_actividades(proyecto_id, estatus_id, usuario_id, fecha_desde, fecha_hasta),
        todas_actividades=obtener_actividades(),
        estatus_list=obtener_estatus_actividad(),
        projects=base["projects"],
        users=base["users"],
        recursos=obtener_recursos(),
        filtro_proyecto=proyecto_id,
        filtro_estatus=estatus_id,
        filtro_usuario=usuario_id,
        filtro_fecha_desde=fecha_desde,
        filtro_fecha_hasta=fecha_hasta,
    )


@app.route("/actividades/nueva", methods=["POST"])
def nueva_actividad():
    datos = request.form.to_dict()
    if crear_actividad(datos):
        rows = ejecutar_query(
            'SELECT "ID" FROM "ACTIVIDADES" WHERE "NOMBRE_ACTIVIDAD"=? AND "ID_PROYECTO"=? ORDER BY "CREADO_EN" DESC',
            (datos.get("nombre_actividad"), datos.get("id_proyecto")),
        )
        if rows:
            act_id = rows[0]["ID"]
            guardar_responsables_actividad(act_id, request.form.getlist("responsables"))
            guardar_recursos_actividad(act_id, request.form.getlist("recursos"))
        return jsonify(
            {"status": "success", "message": "Actividad creada correctamente."}
        )
    return (
        jsonify({"status": "error", "message": "Error al guardar la actividad."}),
        500,
    )


@app.route("/actividades/<actividad_id>")
def detalle_actividad(actividad_id):
    actividad = obtener_actividad_por_id(actividad_id)
    if not actividad:
        return redirect(url_for("actividades"))
    base = _catalogo_base()
    actividad_padre = None
    if actividad.get("ID_ACTIVIDAD_PADRE"):
        actividad_padre = obtener_actividad_nombre(actividad["ID_ACTIVIDAD_PADRE"])
    return render_template(
        "detalle_actividad.html",
        actividad=actividad,
        actividad_padre=actividad_padre,
        actividades_hijas=obtener_actividades_hijas(actividad_id),
        historial=obtener_historial_actividad(actividad_id),
        evidencias=obtener_evidencias_actividad(actividad_id),
        estatus_list=obtener_estatus_actividad(),
        tipos_evidencia=obtener_tipos_evidencia(),
        recursos=obtener_recursos(),
        recursos_actividad=obtener_recursos_actividad(actividad_id),
        users=base["users"],
        projects=base["projects"],
        todas_actividades=obtener_actividades(),
    )


@app.route("/actividades/<actividad_id>/editar", methods=["POST"])
def editar_actividad(actividad_id):
    datos = request.form.to_dict()

    # Si cambió el proyecto, reasignar los registros de horas al nuevo proyecto
    actividad_actual = obtener_actividad_por_id(actividad_id)
    nuevo_proyecto   = datos.get("id_proyecto")
    proyecto_cambio  = (
        actividad_actual
        and nuevo_proyecto
        and str(actividad_actual["ID_PROYECTO"]) != str(nuevo_proyecto)
    )

    if actualizar_actividad(actividad_id, datos):
        if proyecto_cambio:
            reasignar_registros_proyecto(actividad_id, nuevo_proyecto)
        guardar_responsables_actividad(actividad_id, request.form.getlist("responsables"))
        guardar_recursos_actividad(actividad_id, request.form.getlist("recursos"))
        msg = "Actividad actualizada y horas reasignadas al nuevo proyecto." if proyecto_cambio else "Actividad actualizada."
        return jsonify({"status": "success", "message": msg})
    return jsonify({"status": "error", "message": "Error al actualizar."}), 500


@app.route("/actividades/<actividad_id>", methods=["DELETE"])
def borrar_actividad(actividad_id):
    if eliminar_actividad(actividad_id):
        return jsonify({"status": "success"})
    return (
        jsonify({"status": "error", "message": "Error al eliminar la actividad."}),
        500,
    )


# ── DETALLE ────────────────────────────────────────────────────────────────


@app.route("/actividades/<actividad_id>/detalle", methods=["POST"])
def agregar_detalle(actividad_id):
    datos = request.form.to_dict()
    if crear_detalle_actividad(actividad_id, datos):
        return jsonify({"status": "success", "message": "Detalle agregado."})
    return jsonify({"status": "error", "message": "Error al guardar el detalle."}), 500


@app.route("/detalle/<detalle_id>/toggle", methods=["POST"])
def toggle_detalle(detalle_id):
    completado = request.json.get("completado", False)
    if toggle_detalle_completado(detalle_id, completado):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500


@app.route("/detalle/<detalle_id>", methods=["DELETE"])
def borrar_detalle(detalle_id):
    if eliminar_detalle_actividad(detalle_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500


# ── EVIDENCIA ──────────────────────────────────────────────────────────────


@app.route("/actividades/<actividad_id>/evidencia", methods=["POST"])
def agregar_evidencia(actividad_id):
    datos = request.form.to_dict()

    # Necesitamos el proyecto_id para organizar carpetas y controlar cuota
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


@app.route("/evidencia/<evidencia_id>", methods=["DELETE"])
def borrar_evidencia(evidencia_id):
    # Obtener URL antes de borrar para eliminar el archivo del disco
    rows = ejecutar_query('SELECT "URL_ARCHIVO" FROM "EVIDENCIA_ACTIVIDAD" WHERE "ID"=?', (evidencia_id,))
    if rows and rows[0].get("URL_ARCHIVO"):
        # URL: /uploads/evidencias/<proyecto_id>/<actividad_id>/<file>
        # En disco: static/uploads/evidencias/<proyecto_id>/<actividad_id>/<file>
        disk_path = os.path.join(app.root_path, "static", rows[0]["URL_ARCHIVO"].lstrip("/"))
        if os.path.isfile(disk_path):
            try:
                os.remove(disk_path)
                # Limpiar carpeta de actividad si quedó vacía
                act_folder = os.path.dirname(disk_path)
                if os.path.isdir(act_folder) and not os.listdir(act_folder):
                    os.rmdir(act_folder)
            except Exception as e:
                print(f"[borrar_evidencia] no se pudo eliminar archivo: {e}")

    if eliminar_evidencia(evidencia_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500


# ── REGISTROS ──────────────────────────────────────────────────────────────


@app.route("/registros")
def vista_registros():
    base = _catalogo_base()
    filtros = {
        "user_id": request.args.get("user_id"),
        "proyecto_id": request.args.get("proyecto_id"),
        "fecha_ini": request.args.get("fecha_ini"),
        "fecha_fin": request.args.get("fecha_fin"),
    }
    registros = obtener_registros(filtros)
    return render_template(
        "registros.html",
        registros=registros,
        users=base["users"],
        projects=base["projects"],
        tipos_actividad=obtener_tipos_actividad(),
        filtros=filtros,
    )


@app.route("/registros/<registro_id>", methods=["GET"])
def api_registro(registro_id):
    r = obtener_registro_por_id(registro_id)
    if not r:
        return jsonify({"status": "error", "message": "No encontrado"}), 404
    return jsonify({"status": "success", "data": dict(r)})


@app.route("/registros/<registro_id>", methods=["PUT"])
def api_actualizar_registro(registro_id):
    datos = request.get_json()
    if actualizar_registro(registro_id, datos):
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Error al actualizar"}), 500


@app.route("/registros/<registro_id>", methods=["DELETE"])
def api_eliminar_registro(registro_id):
    if eliminar_registro(registro_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Error al eliminar"}), 500


#

# ── Pega este bloque en app.py justo antes de: if __name__ == '__main__': ──


@app.route("/reporte/excel/<proyecto_id>")
def exportar_reporte_excel(proyecto_id):
    from excel_reporte import generar_reporte
    import io

    base = _catalogo_base()
    proyecto = next((p for p in base["projects"] if p["ID"] == proyecto_id), None)
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404
    actividades, evidencias_por_actividad = obtener_datos_reporte_proyecto(proyecto_id)
    nombre = proyecto["NOMBRE_PROYECTO"]
    upload_base = os.path.join(app.root_path, "static", "uploads", "evidencias")
    xlsx_bytes = generar_reporte(nombre, actividades, evidencias_por_actividad, upload_base)
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f'Reporte_{nombre.replace(" ","_")}.xlsx',
    )


# ── CATÁLOGOS ──────────────────────────────────────────────────────────────

from db import (
    obtener_estatus_actividad_todos, crear_estatus, actualizar_estatus,
    toggle_estatus, eliminar_estatus,
    obtener_tipos_actividad_todos, crear_tipo_actividad, actualizar_tipo_actividad,
    toggle_tipo_actividad, eliminar_tipo_actividad,
    obtener_tipos_evidencia_todos, crear_tipo_evidencia, actualizar_tipo_evidencia,
    toggle_tipo_evidencia, eliminar_tipo_evidencia,
    obtener_recursos_todos, crear_recurso, actualizar_recurso,
    toggle_recurso, eliminar_recurso,
)


@app.route("/catalogos")
def vista_catalogos():
    return render_template(
        "catalogos.html",
        estatus   = obtener_estatus_actividad_todos(),
        tipos     = obtener_tipos_actividad_todos(),
        tipos_ev  = obtener_tipos_evidencia_todos(),
        recursos  = obtener_recursos_todos(),
    )


def _json_ok():              return jsonify({"status": "success"})
def _json_err(msg, code=500): return jsonify({"status": "error", "message": msg}), code


@app.route("/api/catalogos/estatus", methods=["POST"])
def api_cat_estatus_crear():
    return _json_ok() if crear_estatus(request.get_json()) else _json_err("Error al crear estatus.")

@app.route("/api/catalogos/estatus/<id>", methods=["PUT"])
def api_cat_estatus_editar(id):
    return _json_ok() if actualizar_estatus(id, request.get_json()) else _json_err("Error al actualizar.")

@app.route("/api/catalogos/estatus/<id>/toggle", methods=["POST"])
def api_cat_estatus_toggle(id):
    return _json_ok() if toggle_estatus(id, request.get_json().get("activo", 1)) else _json_err("Error.")

@app.route("/api/catalogos/estatus/<id>", methods=["DELETE"])
def api_cat_estatus_eliminar(id):
    ok, msg = eliminar_estatus(id)
    return _json_ok() if ok else _json_err(msg, 400)


@app.route("/api/catalogos/tipos", methods=["POST"])
def api_cat_tipos_crear():
    return _json_ok() if crear_tipo_actividad(request.get_json()) else _json_err("Error al crear tipo.")

@app.route("/api/catalogos/tipos/<id>", methods=["PUT"])
def api_cat_tipos_editar(id):
    return _json_ok() if actualizar_tipo_actividad(id, request.get_json()) else _json_err("Error al actualizar.")

@app.route("/api/catalogos/tipos/<id>/toggle", methods=["POST"])
def api_cat_tipos_toggle(id):
    return _json_ok() if toggle_tipo_actividad(id, request.get_json().get("activo", 1)) else _json_err("Error.")

@app.route("/api/catalogos/tipos/<id>", methods=["DELETE"])
def api_cat_tipos_eliminar(id):
    ok, msg = eliminar_tipo_actividad(id)
    return _json_ok() if ok else _json_err(msg, 400)


@app.route("/api/catalogos/evidencia", methods=["POST"])
def api_cat_ev_crear():
    return _json_ok() if crear_tipo_evidencia(request.get_json()) else _json_err("Error al crear tipo.")

@app.route("/api/catalogos/evidencia/<id>", methods=["PUT"])
def api_cat_ev_editar(id):
    return _json_ok() if actualizar_tipo_evidencia(id, request.get_json()) else _json_err("Error al actualizar.")

@app.route("/api/catalogos/evidencia/<id>/toggle", methods=["POST"])
def api_cat_ev_toggle(id):
    return _json_ok() if toggle_tipo_evidencia(id, request.get_json().get("activo", 1)) else _json_err("Error.")

@app.route("/api/catalogos/evidencia/<id>", methods=["DELETE"])
def api_cat_ev_eliminar(id):
    ok, msg = eliminar_tipo_evidencia(id)
    return _json_ok() if ok else _json_err(msg, 400)


@app.route("/api/catalogos/recursos", methods=["POST"])
def api_cat_rec_crear():
    return _json_ok() if crear_recurso(request.get_json()) else _json_err("Error al crear recurso.")

@app.route("/api/catalogos/recursos/<id>", methods=["PUT"])
def api_cat_rec_editar(id):
    return _json_ok() if actualizar_recurso(id, request.get_json()) else _json_err("Error al actualizar.")

@app.route("/api/catalogos/recursos/<id>/toggle", methods=["POST"])
def api_cat_rec_toggle(id):
    return _json_ok() if toggle_recurso(id, request.get_json().get("activo", 1)) else _json_err("Error.")

@app.route("/api/catalogos/recursos/<id>", methods=["DELETE"])
def api_cat_rec_eliminar(id):
    ok, msg = eliminar_recurso(id)
    return _json_ok() if ok else _json_err(msg, 400)

# ── CALENDARIO ─────────────────────────────────────────────────────────────

from db import (
    obtener_ausencias, obtener_ausencias_mes, guardar_ausencia,
    actualizar_ausencia, eliminar_ausencia,
    obtener_dias_festivos, obtener_dias_festivos_mes,
    guardar_dia_festivo, actualizar_dia_festivo,
    toggle_dia_festivo, eliminar_dia_festivo,
    get_horas_ausencia_dia,
)


@app.route("/calendario")
def calendario():
    from datetime import date
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes  = int(request.args.get("mes",  hoy.month))
    base = _catalogo_base()
    return render_template(
        "calendario.html",
        users=base["users"],
        anio=anio,
        mes=mes,
        ausencias=obtener_ausencias_mes(anio, mes),
        festivos=obtener_dias_festivos_mes(anio, mes),
        todos_festivos=obtener_dias_festivos(anio),
        todas_ausencias=obtener_ausencias({"anio": anio}),
    )


@app.route("/api/ausencias")
def api_ausencias():
    filtros = {
        "user_id": request.args.get("user_id"),
        "tipo":    request.args.get("tipo"),
        "anio":    request.args.get("anio"),
    }
    rows = obtener_ausencias(filtros)
    return jsonify([
        {
            "id":           r["ID"],
            "id_usuario":   r["ID_USUARIO"],
            "usuario":      r["USUARIO"],
            "fecha_inicio": str(r["FECHA_INICIO"]),
            "fecha_fin":    str(r["FECHA_FIN"]),
            "tipo":         r["TIPO"],
            "horas_dia":    float(r["HORAS_DIA"]),
            "descripcion":  r.get("DESCRIPCION") or "",
        }
        for r in rows
    ])


@app.route("/api/ausencias", methods=["POST"])
def api_ausencia_crear():
    datos = request.get_json()
    return _json_ok() if guardar_ausencia(datos) else _json_err("Error al guardar la ausencia.")


@app.route("/api/ausencias/<ausencia_id>", methods=["PUT"])
def api_ausencia_editar(ausencia_id):
    return _json_ok() if actualizar_ausencia(ausencia_id, request.get_json()) else _json_err("Error al actualizar.")


@app.route("/api/ausencias/<ausencia_id>", methods=["DELETE"])
def api_ausencia_eliminar(ausencia_id):
    return _json_ok() if eliminar_ausencia(ausencia_id) else _json_err("Error al eliminar.")


@app.route("/api/ausencias/dia")
def api_ausencias_dia():
    user_id = request.args.get("user")
    fecha   = request.args.get("fecha")
    horas   = get_horas_ausencia_dia(user_id, fecha)
    return jsonify({"horas_ausencia": horas})


# ── DÍAS FESTIVOS ──────────────────────────────────────────────────────────

@app.route("/api/festivos")
def api_festivos():
    from datetime import date
    anio = int(request.args.get("anio", date.today().year))
    rows = obtener_dias_festivos(anio)
    return jsonify([
        {
            "id":          r["ID"],
            "fecha":       str(r["FECHA"]),
            "nombre":      r["NOMBRE"],
            "tipo":        r["TIPO"],
            "aplica_todos": bool(r["APLICA_TODOS"]),
            "activo":      bool(r["ACTIVO"]),
        }
        for r in rows
    ])


@app.route("/api/festivos", methods=["POST"])
def api_festivo_crear():
    return _json_ok() if guardar_dia_festivo(request.get_json()) else _json_err("Error al guardar.")


@app.route("/api/festivos/<festivo_id>", methods=["PUT"])
def api_festivo_editar(festivo_id):
    return _json_ok() if actualizar_dia_festivo(festivo_id, request.get_json()) else _json_err("Error al actualizar.")


@app.route("/api/festivos/<festivo_id>/toggle", methods=["POST"])
def api_festivo_toggle(festivo_id):
    return _json_ok() if toggle_dia_festivo(festivo_id, request.get_json().get("activo", 1)) else _json_err("Error.")


@app.route("/api/festivos/<festivo_id>", methods=["DELETE"])
def api_festivo_eliminar(festivo_id):
    return _json_ok() if eliminar_dia_festivo(festivo_id) else _json_err("Error al eliminar.")


# health route

@app.route('/health')
def health():
    return {'status': 'ok'}, 200


if __name__ == "__main__":
    app.run(debug=True)