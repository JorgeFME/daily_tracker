import os
import uuid
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
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
    obtener_evidencia_por_id,
    obtener_evidencias_filtradas,
    obtener_actividades_con_evidencia,
    crear_evidencia,
    actualizar_evidencia,
    eliminar_evidencia,
    # Catálogos
    obtener_estatus_actividad,
    obtener_tipos_evidencia,
    obtener_tipos_actividad,
    obtener_recursos,
    obtener_recursos_actividad,
    guardar_recursos_actividad,
    obtener_actividades_default_mes,
    contar_actividades_default_mes,
    reasignar_registros_proyecto,
    contar_actividades,
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


def _local_today() -> date:
    """Fecha local configurable para evitar desfases entre servidor y operación."""
    tz_name = os.getenv("APP_TIMEZONE", "America/Mexico_City")
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return date.today()


def _delete_evidence_file(file_url: str | None):
    if not file_url:
        return
    disk_path = os.path.join(app.root_path, "static", file_url.lstrip("/"))
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
            
            # Manejar evidencia si está incluida
            if datos.get("incluir_evidencia") == "on" and datos.get("actividad_id"):
                actividad_id = datos.get("actividad_id")
                actividad = obtener_actividad_por_id(actividad_id)
                proyecto_id = actividad["ID_PROYECTO"] if actividad else "sin_proyecto"
                
                archivo = request.files.get("archivo_evidencia")
                if archivo and archivo.filename:
                    if _allowed(archivo.filename):
                        try:
                            meta = _save_upload(archivo, proyecto_id, actividad_id)
                            datos["url_archivo"] = meta["url"]
                            datos["nombre_archivo"] = meta["nombre"]
                            datos["mime_type"] = meta["mime"]
                            datos["tamano_bytes"] = str(meta["size"])
                        except ValueError:
                            pass  # Ignorar error de cuota en evidencia
                
                # Guardar evidencia con datos obligatorios
                if datos.get("id_tipo_evidencia"):
                    datos["id_tipo"] = datos.get("id_tipo_evidencia")
                    datos["titulo"] = datos.get("titulo_evidencia") or None
                    datos["contenido_texto"] = datos.get("contenido_evidencia") or None
                    datos["subido_por"] = datos.get("user")
                    crear_evidencia(actividad_id, datos)
            
            return jsonify({"status": "success", "message": "¡Registro guardado correctamente!"})
        return jsonify({"status": "error", "message": "Hubo un fallo al conectar con SAP HANA."}), 500

    base = _catalogo_base()
    return render_template(
        "index.html",
        users=base["users"],
        projects=base["projects"],
        estatus_list=obtener_estatus_actividad(),
        recursos=obtener_recursos(),
        tipos_actividad=obtener_tipos_actividad(),
        tipos_evidencia=obtener_tipos_evidencia(),
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
    usuario_id = request.args.get("usuario")
    if not proyecto_id:
        return jsonify([])
    acts = obtener_actividades_por_proyecto(proyecto_id, usuario_id)
    return jsonify(
        [
            {
                "id": a["ID"],
                "nombre": a["NOMBRE_ACTIVIDAD"],
                "horas": float(a["HORAS_INVERTIDAS"]),
                "estatus": a["ESTATUS"],
                "grupo": a.get("GRUPO", "otra"),
                "grupo_orden": int(a.get("GRUPO_ORDEN", 3)),
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
    scope        = request.args.get("scope") or "all"
    q            = (request.args.get("q") or "").strip() or None
    sort_by      = request.args.get("sort") or "prioridad"
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    page_size    = 24
    estatus_list = obtener_estatus_actividad()

    def _estatus_id_por_desc(*targets):
        targets_up = {t.upper() for t in targets}
        for e in estatus_list:
            desc = (e.get("DESCRIPCION") or "").upper()
            if desc in targets_up:
                return e.get("ID")
        return None

    # Default UX: mostrar actividades del mes actual y conservar abiertas anteriores al final.
    sin_filtros = (
        scope == "all"
        and not any([proyecto_id, estatus_id, usuario_id, fecha_desde, fecha_hasta, q])
    )
    solo_activas = False
    default_scope = None
    if scope == "active" and not estatus_id:
        solo_activas = True
    elif scope == "completed" and not estatus_id:
        estatus_id = _estatus_id_por_desc("COMPLETADO")
    elif scope == "canceled" and not estatus_id:
        estatus_id = _estatus_id_por_desc("CANCELADO")

    has_filters = bool(
        proyecto_id or estatus_id or usuario_id or fecha_desde or fecha_hasta or q
        or (scope and scope != "all")
    )
    scope_label = "Todas las actividades"
    if scope == "active":
        scope_label = "Actividades activas"
    elif scope == "completed":
        scope_label = "Actividades completadas"
    elif scope == "canceled":
        scope_label = "Actividades canceladas"

    actividades_data = None
    if sin_filtros:
        today = _local_today()
        fecha_desde = today.replace(day=1).isoformat()
        fecha_hasta = today.isoformat()
        default_scope = "Mes actual + abiertas anteriores"
        total_actividades = contar_actividades_default_mes(
            mes_inicio=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        total_paginas = max((total_actividades + page_size - 1) // page_size, 1)
        if page > total_paginas:
            page = total_paginas
        actividades_data = obtener_actividades_default_mes(
            mes_inicio=fecha_desde,
            fecha_hasta=fecha_hasta,
            sort_by=sort_by,
            page=page,
            page_size=page_size,
        )
    else:
        total_actividades = contar_actividades(
            proyecto_id=proyecto_id,
            estatus_id=estatus_id,
            usuario_id=usuario_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            q=q,
            solo_activas=solo_activas,
        )
        total_paginas = max((total_actividades + page_size - 1) // page_size, 1)
        if page > total_paginas:
            page = total_paginas
        actividades_data = obtener_actividades(
            proyecto_id=proyecto_id,
            estatus_id=estatus_id,
            usuario_id=usuario_id,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            q=q,
            sort_by=sort_by,
            page=page,
            page_size=page_size,
            solo_activas=solo_activas,
        )

    base = _catalogo_base()
    template_context = {
        "actividades": actividades_data,
        "estatus_list": estatus_list,
        "projects": base["projects"],
        "users": base["users"],
        "filtro_proyecto": proyecto_id,
        "filtro_estatus": estatus_id,
        "filtro_usuario": usuario_id,
        "filtro_fecha_desde": fecha_desde,
        "filtro_fecha_hasta": fecha_hasta,
        "filtro_q": q,
        "filtro_sort": sort_by,
        "page": page,
        "total_paginas": total_paginas,
        "total_actividades": total_actividades,
        "page_size": page_size,
        "default_scope": default_scope,
        "filtro_scope": scope,
        "has_filters": has_filters,
        "scope_label": scope_label,
    }
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("partials/actividades_resultados.html", **template_context)

    return render_template(
        "actividades.html",
        todas_actividades=obtener_actividades(),
        recursos=obtener_recursos(),
        **template_context,
    )


@app.route("/actividades/nueva", methods=["POST"])
def nueva_actividad():
    datos = request.form.to_dict()
    datos["nombre_actividad"] = (datos.get("nombre_actividad") or "").strip()
    datos["descripcion"] = (datos.get("descripcion") or "").strip() or None
    datos["solicitante"] = (datos.get("solicitante") or "").strip() or None

    missing_fields = []
    if not datos.get("id_proyecto"):
        missing_fields.append("proyecto")
    if not datos.get("id_estatus"):
        missing_fields.append("estatus")
    if not datos.get("fecha_solicitud"):
        missing_fields.append("fecha de solicitud")
    if not datos.get("nombre_actividad"):
        missing_fields.append("nombre de la actividad")
    if missing_fields:
        return (
            jsonify({
                "status": "error",
                "message": f"Faltan campos requeridos: {', '.join(missing_fields)}.",
            }),
            400,
        )

    padre_id = datos.get("id_actividad_padre") or None
    if padre_id:
        actividad_padre = obtener_actividad_por_id(padre_id)
        if not actividad_padre:
            return (
                jsonify({
                    "status": "error",
                    "message": "La actividad padre seleccionada ya no existe.",
                }),
                400,
            )
        if str(actividad_padre["ID_PROYECTO"]) != str(datos.get("id_proyecto")):
            return (
                jsonify({
                    "status": "error",
                    "message": "La actividad padre debe pertenecer al mismo proyecto.",
                }),
                400,
            )

    if crear_actividad(datos):
        actividad_payload = None
        rows = ejecutar_query(
            'SELECT "ID" FROM "ACTIVIDADES" WHERE "NOMBRE_ACTIVIDAD"=? AND "ID_PROYECTO"=? '
            'AND COALESCE("ID_ACTIVIDAD_PADRE", \'\')=COALESCE(?, \'\') ORDER BY "CREADO_EN" DESC',
            (datos.get("nombre_actividad"), datos.get("id_proyecto"), padre_id),
        )
        if rows:
            act_id = rows[0]["ID"]
            guardar_responsables_actividad(act_id, request.form.getlist("responsables"))
            guardar_recursos_actividad(act_id, request.form.getlist("recursos"))
            actividad = obtener_actividad_por_id(act_id)
            if actividad:
                actividad_payload = {
                    "id": actividad["ID"],
                    "nombre": actividad["NOMBRE_ACTIVIDAD"],
                    "estatus": actividad["ESTATUS"],
                    "proyecto_id": actividad["ID_PROYECTO"],
                    "actividad_padre_id": actividad.get("ID_ACTIVIDAD_PADRE"),
                }
        return jsonify(
            {
                "status": "success",
                "message": "Actividad creada correctamente.",
                "actividad": actividad_payload,
            }
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


@app.route("/evidencia/<evidencia_id>", methods=["PUT", "DELETE"])
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


# ── REGISTROS ──────────────────────────────────────────────────────────────


@app.route("/registros")
def vista_registros():
    base = _catalogo_base()
    filtros = {
        "user_id": request.args.get("user_id"),
        "proyecto_id": request.args.get("proyecto_id"),
        "tipo_id": request.args.get("tipo_id"),
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


@app.route("/evidencias")
def vista_evidencias():
    base = _catalogo_base()
    return render_template(
        "evidencias.html",
        projects=base["projects"],
        users=base["users"],
        tipos_evidencia=obtener_tipos_evidencia(),
        actividades_con_evidencia=obtener_actividades_con_evidencia(),
    )


@app.route("/api/evidencias/explore")
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
    obtener_proyectos_todos, crear_proyecto, actualizar_proyecto,
    toggle_proyecto, eliminar_proyecto,
)


@app.route("/catalogos")
def vista_catalogos():
    return render_template(
        "catalogos.html",
        estatus   = obtener_estatus_actividad_todos(),
        tipos     = obtener_tipos_actividad_todos(),
        tipos_ev  = obtener_tipos_evidencia_todos(),
        recursos  = obtener_recursos_todos(),
        proyectos = obtener_proyectos_todos(),
    )


def _json_ok():              return jsonify({"status": "success"})
def _json_err(msg, code=500): return jsonify({"status": "error", "message": msg}), code


def _contar_dias_habiles(inicio: date, fin: date) -> int:
    dias = 0
    actual = inicio
    while actual <= fin:
        if actual.weekday() < 5:
            dias += 1
        actual += timedelta(days=1)
    return dias


def _normalizar_payload_ausencia(datos: dict):
    required_fields = ("id_usuario", "fecha_inicio", "fecha_fin", "tipo")
    if any(not str(datos.get(field) or "").strip() for field in required_fields):
        return None, "Completa los campos obligatorios: usuario, fechas y tipo."

    try:
        fecha_inicio = datetime.strptime(str(datos.get("fecha_inicio")), "%Y-%m-%d").date()
        fecha_fin = datetime.strptime(str(datos.get("fecha_fin")), "%Y-%m-%d").date()
    except ValueError:
        return None, "Formato de fecha inválido. Usa YYYY-MM-DD."

    if fecha_fin < fecha_inicio:
        return None, "La fecha fin no puede ser menor que la fecha inicio."

    dias_habiles = _contar_dias_habiles(fecha_inicio, fecha_fin)
    if dias_habiles <= 0:
        return None, "Selecciona al menos un día hábil dentro del rango de ausencia."

    horas_total_raw = datos.get("horas_total")
    if horas_total_raw not in (None, ""):
        try:
            horas_total = float(horas_total_raw)
        except (TypeError, ValueError):
            return None, "Horas ausentes inválidas."

        max_total = dias_habiles * 8
        if horas_total < 0.25 or horas_total > max_total:
            return None, f"Horas ausentes fuera de rango permitido (0.25 a {max_total})."

        horas_dia = round(horas_total / dias_habiles, 2)
    else:
        try:
            horas_dia = float(datos.get("horas_dia", 8))
        except (TypeError, ValueError):
            return None, "Horas por día inválidas."

    if horas_dia < 0.25 or horas_dia > 8:
        return None, "Horas por día fuera de rango permitido (0.25 a 8)."

    tipo = str(datos.get("tipo") or "").strip().upper()
    descripcion = str(datos.get("descripcion") or "").strip() if tipo == "OTRO" else _descripcion_respaldo_ausencia(tipo)

    datos_normalizados = dict(datos)
    datos_normalizados["fecha_inicio"] = fecha_inicio.isoformat()
    datos_normalizados["fecha_fin"] = fecha_fin.isoformat()
    datos_normalizados["tipo"] = tipo
    datos_normalizados["horas_dia"] = horas_dia
    datos_normalizados["descripcion"] = descripcion or None
    return datos_normalizados, None


def _descripcion_respaldo_ausencia(tipo: str) -> str:
    tipos = {
        "VACACIONES": "Vacaciones",
        "INCAPACIDAD": "Incapacidad",
        "DIA_LIBRE": "Día libre",
        "PERMISO": "Permiso",
        "OTRO": "Otro",
    }
    tipo_normalizado = str(tipo or "").strip().upper()
    return tipos.get(tipo_normalizado) or tipo_normalizado.replace("_", " ").title()


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


@app.route("/api/catalogos/proyectos", methods=["POST"])
def api_cat_proyectos_crear():
    data = request.get_json() or {}
    if not (data.get("nombre_proyecto") or "").strip():
        return _json_err("El nombre del proyecto es obligatorio.", 400)
    return _json_ok() if crear_proyecto(data) else _json_err("Error al crear proyecto.")

@app.route("/api/catalogos/proyectos/<id>", methods=["PUT"])
def api_cat_proyectos_editar(id):
    data = request.get_json() or {}
    if not (data.get("nombre_proyecto") or "").strip():
        return _json_err("El nombre del proyecto es obligatorio.", 400)
    return _json_ok() if actualizar_proyecto(id, data) else _json_err("Error al actualizar.")

@app.route("/api/catalogos/proyectos/<id>/toggle", methods=["POST"])
def api_cat_proyectos_toggle(id):
    return _json_ok() if toggle_proyecto(id, request.get_json().get("activo", 1)) else _json_err("Error.")

@app.route("/api/catalogos/proyectos/<id>", methods=["DELETE"])
def api_cat_proyectos_eliminar(id):
    ok, msg = eliminar_proyecto(id)
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
            "descripcion":  r.get("DESCRIPCION") or _descripcion_respaldo_ausencia(r.get("TIPO")),
        }
        for r in rows
    ])


@app.route("/api/ausencias", methods=["POST"])
def api_ausencia_crear():
    datos = request.get_json(silent=True) or {}
    datos_normalizados, error = _normalizar_payload_ausencia(datos)
    if error:
        return _json_err(error, 400)
    return _json_ok() if guardar_ausencia(datos_normalizados) else _json_err("Error al guardar la ausencia.")


@app.route("/api/ausencias/<ausencia_id>", methods=["PUT"])
def api_ausencia_editar(ausencia_id):
    datos = request.get_json(silent=True) or {}
    datos_normalizados, error = _normalizar_payload_ausencia(datos)
    if error:
        return _json_err(error, 400)
    return _json_ok() if actualizar_ausencia(ausencia_id, datos_normalizados) else _json_err("Error al actualizar.")


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
