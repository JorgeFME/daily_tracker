import os
import uuid
from calendar import monthrange
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
    obtener_catalogo_actividades,
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
    obtener_solicitantes,
    obtener_responsables_actividad,
    obtener_recursos_actividad,
    guardar_recursos_actividad,
    obtener_actividades_default_mes,
    contar_actividades_default_mes,
    reasignar_registros_proyecto,
    contar_actividades,
    obtener_plan_de_trabajo,
    contar_plan_de_trabajo,
)

load_env_from_dotenv()
app = Flask(__name__)

# ── Filtro Jinja2 para formatear fechas ───────────────────────────────────
@app.template_filter('strftime')
def _filter_strftime(value, fmt='%Y-%m-%d'):
    """Formatea un objeto date/datetime o string ISO al formato indicado."""
    if value is None:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime(fmt)
    # Si viene como string (ej. '2026-05-18'), convertir primero
    try:
        from datetime import date as _date
        return _date.fromisoformat(str(value)[:10]).strftime(fmt)
    except Exception:
        return str(value)

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
        "solicitantes": obtener_solicitantes(),
    }


def _local_today() -> date:
    """Fecha local configurable para evitar desfases entre servidor y operación."""
    tz_name = os.getenv("APP_TIMEZONE", "America/Mexico_City")
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return date.today()


def _mes_actual_limites() -> tuple[str, str]:
    hoy = _local_today()
    inicio = hoy.replace(day=1)
    fin = hoy.replace(day=monthrange(hoy.year, hoy.month)[1])
    return inicio.isoformat(), fin.isoformat()


def _semana_actual_limites() -> tuple[date, date]:
    """Regresa el rango lunes-domingo de la semana local actual."""
    hoy = _local_today()
    inicio = hoy - timedelta(days=hoy.weekday())
    fin = inicio + timedelta(days=6)
    return inicio, fin


def _validar_fecha_hasta_fin_semana_actual(fecha_obj: date) -> bool:
    """Permite fechas históricas y bloquea solo fechas posteriores al fin de semana actual."""
    _, fin = _semana_actual_limites()
    return fecha_obj <= fin


def _registros_filters_from_request(req_args, proyecto_id: str | None = None):
    filtros = {
        "user_id": (req_args.get("user_id") or "").strip() or None,
        "proyecto_id": proyecto_id or (req_args.get("proyecto_id") or "").strip() or None,
        "actividad_id": (req_args.get("actividad_id") or "").strip() or None,
        "tipo_id": (req_args.get("tipo_id") or "").strip() or None,
        "fecha_ini": (req_args.get("fecha_ini") or "").strip() or None,
        "fecha_fin": (req_args.get("fecha_fin") or "").strip() or None,
    }
    tiene_filtros_explicitos = any(filtros.values())
    usando_mes_actual_default = False

    if not filtros["fecha_ini"] and not filtros["fecha_fin"]:
        filtros["fecha_ini"], filtros["fecha_fin"] = _mes_actual_limites()
        usando_mes_actual_default = True

    return filtros, {
        "tiene_filtros_explicitos": tiene_filtros_explicitos,
        "usando_mes_actual_default": usando_mes_actual_default,
    }


def _registros_activity_options(filtros):
    return obtener_catalogo_actividades(
        proyecto_id=filtros.get("proyecto_id"),
    )


def _catalog_label(items, selected_id, id_key, label_key, fallback="Todos"):
    if not selected_id:
        return fallback
    for item in items:
        if str(item.get(id_key)) == str(selected_id):
            return item.get(label_key) or fallback
    return fallback


def _fmt_fecha_corta(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return value


def _parse_iso_date(value: str | None, field_name: str) -> str | None:
    """Valida fecha en formato YYYY-MM-DD y retorna el mismo valor si es válido."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        raise ValueError(f"{field_name} debe tener formato YYYY-MM-DD.")


def _normalizar_campos_dashboard_actividad(datos: dict) -> tuple[dict, str | None]:
    """Normaliza y valida campos extendidos de actividad para dashboard."""
    datos["friendly_name"] = (datos.get("friendly_name") or "").strip() or None

    try:
        datos["fecha_inicio"] = _parse_iso_date(datos.get("fecha_inicio"), "Fecha de inicio")
        datos["fecha_fin_est"] = _parse_iso_date(datos.get("fecha_fin_est"), "Fecha fin estimada")
        datos["fecha_fin_real"] = _parse_iso_date(datos.get("fecha_fin_real"), "Fecha fin real")
    except ValueError as exc:
        return datos, str(exc)

    dias_acordados = (datos.get("dias_acordados") or "").strip()
    avance_pct = (datos.get("avance_pct") or "").strip()

    if datos.get("fecha_inicio") and datos.get("fecha_fin_est"):
        f_inicio = datetime.strptime(datos["fecha_inicio"], "%Y-%m-%d").date()
        f_fin_est = datetime.strptime(datos["fecha_fin_est"], "%Y-%m-%d").date()
        if f_fin_est < f_inicio:
            return datos, "La fecha fin estimada no puede ser menor a la fecha de inicio."
        datos["dias_acordados"] = str((f_fin_est - f_inicio).days + 1)
    elif dias_acordados:
        if not dias_acordados.isdigit():
            return datos, "Los días acordados deben ser un número entero."
        dias = int(dias_acordados)
        if dias < 1 or dias > 365:
            return datos, "Los días acordados deben estar entre 1 y 365."
        datos["dias_acordados"] = str(dias)
    else:
        datos["dias_acordados"] = None

    if avance_pct:
        if not avance_pct.isdigit():
            return datos, "El porcentaje de avance debe ser un número entero."
        avance = int(avance_pct)
        if avance < 0 or avance > 100:
            return datos, "El porcentaje de avance debe estar entre 0 y 100."
        datos["avance_pct"] = str(avance)
    else:
        datos["avance_pct"] = "0"

    if datos.get("fecha_inicio") and datos.get("fecha_fin_real"):
        f_inicio = datetime.strptime(datos["fecha_inicio"], "%Y-%m-%d").date()
        f_fin_real = datetime.strptime(datos["fecha_fin_real"], "%Y-%m-%d").date()
        if f_fin_real < f_inicio:
            return datos, "La fecha fin real no puede ser menor a la fecha de inicio."

    return datos, None


def _build_registros_export_context(filtros, filtros_meta, base, actividades=None):
    proyecto_nombre = _catalog_label(
        base["projects"], filtros.get("proyecto_id"), "ID", "NOMBRE_PROYECTO", fallback="Sin proyecto"
    )
    desarrollador = _catalog_label(
        base["users"], filtros.get("user_id"), "ID", "NOMBRE_COMPLETO"
    )
    actividades = actividades if actividades is not None else _registros_activity_options(filtros)
    actividad = _catalog_label(
        actividades, filtros.get("actividad_id"), "ID", "NOMBRE_ACTIVIDAD", fallback="Todas"
    )
    tipos = obtener_tipos_actividad()
    tipo = _catalog_label(tipos, filtros.get("tipo_id"), "ID", "DESCRIPCION")
    fecha_ini = _fmt_fecha_corta(filtros.get("fecha_ini"))
    fecha_fin = _fmt_fecha_corta(filtros.get("fecha_fin"))
    origen_rango = "Mes actual por defecto" if filtros_meta.get("usando_mes_actual_default") else "Rango seleccionado"

    return {
        "proyecto": proyecto_nombre,
        "desarrollador": desarrollador,
        "actividad": actividad,
        "tipo": tipo,
        "fecha_ini": fecha_ini,
        "fecha_fin": fecha_fin,
        "origen_rango": origen_rango,
        "scope_label": (
            f"Proyecto: {proyecto_nombre} · Desarrollador: {desarrollador} · "
            f"Actividad: {actividad} · Tipo: {tipo} · "
            f"Rango: {fecha_ini} a {fecha_fin} · {origen_rango}"
        ),
        "scope_label_short": (
            f"Desarrollador: {desarrollador} · Actividad: {actividad} · Tipo: {tipo} · "
            f"Rango: {fecha_ini} a {fecha_fin}"
        ),
    }


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


def _solicitantes_para_formulario(actual: str | None = None):
    solicitantes = [dict(item) for item in obtener_solicitantes()]
    actual_normalizado = (actual or "").strip()
    if actual_normalizado and not any(
        (item.get("NOMBRE") or "").strip().casefold() == actual_normalizado.casefold()
        for item in solicitantes
    ):
        solicitantes.append({
            "ID": f"legacy::{actual_normalizado}",
            "NOMBRE": actual_normalizado,
            "ACTIVO": 0,
            "ES_LEGACY": 1,
        })
    return solicitantes


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


def _guardar_registro_con_evidencia(datos, request_files):
    """Guarda un registro individual y, opcionalmente, su evidencia adjunta.
    Retorna True si se guardó correctamente, False en caso contrario.
    Puede lanzar ValueError si la actividad está en un estado inválido.
    """
    ok = guardar_registro_actividad(datos)
    if ok:
        if datos.get("incluir_evidencia") == "on" and datos.get("actividad_id"):
            actividad_id = datos.get("actividad_id")
            actividad = obtener_actividad_por_id(actividad_id)
            proyecto_id = actividad["ID_PROYECTO"] if actividad else "sin_proyecto"

            archivo = request_files.get("archivo_evidencia")
            if archivo and archivo.filename:
                if _allowed(archivo.filename):
                    try:
                        meta = _save_upload(archivo, proyecto_id, actividad_id)
                        datos["url_archivo"] = meta["url"]
                        datos["nombre_archivo"] = meta["nombre"]
                        datos["mime_type"] = meta["mime"]
                        datos["tamano_bytes"] = str(meta["size"])
                    except ValueError:
                        pass

            if datos.get("id_tipo_evidencia"):
                datos["id_tipo"] = datos.get("id_tipo_evidencia")
                datos["titulo"] = datos.get("titulo_evidencia") or None
                datos["contenido_texto"] = datos.get("contenido_evidencia") or None
                datos["subido_por"] = datos.get("user")
                crear_evidencia(actividad_id, datos)
    return ok


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        datos = request.form.to_dict()
        datos["quick_recursos"] = request.form.getlist("quick_recursos")
        semana_inicio, semana_fin = _semana_actual_limites()

        # ── Modo rango de fechas ──────────────────────────────────────────────
        date_start_str = datos.get("date_start", "").strip()
        date_end_str = datos.get("date_end", "").strip()

        if date_start_str and date_end_str:
            try:
                date_start = datetime.strptime(date_start_str, "%Y-%m-%d").date()
                date_end = datetime.strptime(date_end_str, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"status": "error", "message": "Formato de fecha inválido."}), 400

            if date_start > date_end:
                return jsonify({"status": "error", "message": "La fecha de inicio debe ser anterior o igual a la fecha fin."}), 400

            if not (_validar_fecha_hasta_fin_semana_actual(date_start) and _validar_fecha_hasta_fin_semana_actual(date_end)):
                return jsonify({
                    "status": "error",
                    "message": (
                        "No puedes registrar fechas posteriores al fin de la semana en curso "
                        f"({semana_fin.isoformat()})."
                    ),
                }), 400

            horas_pedidas = float(datos.get("hours") or 0)
            user_id = datos.get("user")

            registrados, omitidos, errores = [], [], []
            current = date_start

            while current <= date_end:
                # Saltar fines de semana
                if current.weekday() >= 5:
                    current += timedelta(days=1)
                    continue

                fecha_str = current.strftime("%Y-%m-%d")

                ausencia = get_horas_ausencia_dia(user_id, fecha_str)
                ya_registradas = get_horas_diarias(user_id, fecha_str)
                disponibles = max(0.0, (8.0 - ausencia) - ya_registradas)

                if disponibles <= 0:
                    omitidos.append(fecha_str)
                    current += timedelta(days=1)
                    continue

                horas_dia = min(horas_pedidas, disponibles)
                datos_dia = dict(datos)
                datos_dia["date"] = fecha_str
                datos_dia["hours"] = str(horas_dia)
                # En modo rango no se permite modificar estatus de actividad.
                datos_dia.pop("nuevo_estatus_id", None)
                datos_dia.pop("finalizar_actividad", None)

                try:
                    ok = _guardar_registro_con_evidencia(datos_dia, request.files)
                    if ok:
                        registrados.append(fecha_str)
                    else:
                        errores.append(fecha_str)
                except ValueError as e:
                    return jsonify({"status": "error", "message": str(e)}), 400
                except Exception:
                    app.logger.exception(f"Error al guardar registro de rango para {fecha_str}")
                    errores.append(fecha_str)

                current += timedelta(days=1)

            if not registrados and not errores:
                msg = f"No se creó ningún registro. {len(omitidos)} día(s) ya tenían el límite de horas cubierto."
                return jsonify({"status": "error", "message": msg}), 400

            partes = []
            if registrados:
                partes.append(f"✅ {len(registrados)} día(s) registrado(s)")
            if omitidos:
                partes.append(f"⏭️ {len(omitidos)} día(s) omitido(s) (sin horas disponibles)")
            if errores:
                partes.append(f"⚠️ {len(errores)} día(s) con error")

            return jsonify({
                "status": "success",
                "message": " · ".join(partes),
                "registrados": len(registrados),
                "omitidos": len(omitidos),
                "errores": len(errores),
                "fechas_registradas": registrados,
                "fechas_omitidas": omitidos,
            })

        # ── Modo fecha única (comportamiento original) ────────────────────────
        date_single_str = (datos.get("date") or "").strip()
        if date_single_str:
            try:
                date_single = datetime.strptime(date_single_str, "%Y-%m-%d").date()
            except ValueError:
                return jsonify({"status": "error", "message": "Formato de fecha inválido."}), 400

            if not _validar_fecha_hasta_fin_semana_actual(date_single):
                return jsonify({
                    "status": "error",
                    "message": (
                        "No puedes registrar fechas posteriores al fin de la semana en curso "
                        f"({semana_fin.isoformat()})."
                    ),
                }), 400

        try:
            ok = _guardar_registro_con_evidencia(datos, request.files)
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        except Exception:
            app.logger.exception("Error inesperado al guardar registro de actividad")
            return jsonify({"status": "error", "message": "Ocurrio un error interno al guardar el registro."}), 500
        if ok:
            return jsonify({"status": "success", "message": "¡Registro guardado correctamente!"})
        return jsonify({"status": "error", "message": "Hubo un fallo al conectar con SAP HANA."}), 500

    base = _catalogo_base()
    semana_inicio, semana_fin = _semana_actual_limites()
    return render_template(
        "index.html",
        users=base["users"],
        projects=base["projects"],
        solicitantes=base["solicitantes"],
        estatus_list=obtener_estatus_actividad(),
        recursos=obtener_recursos(),
        tipos_actividad=obtener_tipos_actividad(),
        tipos_evidencia=obtener_tipos_evidencia(),
        current_week_start=semana_inicio.isoformat(),
        current_week_end=semana_fin.isoformat(),
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
    actividad_actual_id = request.args.get("actividad_actual") or None
    if not proyecto_id:
        return jsonify([])
    acts = obtener_actividades_por_proyecto(
        proyecto_id, usuario_id, incluir_actividad_id=actividad_actual_id
    )
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


@app.route("/api/plan_de_trabajo")
def plan_de_trabajo_rapidas():
    proyecto_id = (request.args.get("proyecto_id") or "").strip() or None
    estatus_id = (request.args.get("estatus_id") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    friendly_name_q = (request.args.get("friendly_name_q") or "").strip() or None
    solo_retraso = str(request.args.get("solo_retraso") or "").strip().lower() in {"1", "true", "si", "yes"}
    sort_by = (request.args.get("sort") or "recientes").strip().lower()

    try:
        fecha_desde = _parse_iso_date(request.args.get("fecha_desde"), "fecha_desde")
        fecha_hasta = _parse_iso_date(request.args.get("fecha_hasta"), "fecha_hasta")
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    try:
        page = max(int(request.args.get("page", 1)), 1)
        page_size = int(request.args.get("page_size", 50))
        page_size = max(1, min(page_size, 100))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "page y page_size deben ser enteros válidos."}), 400

    total = contar_plan_de_trabajo(
        proyecto_id=proyecto_id,
        estatus_id=estatus_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        friendly_name_q=friendly_name_q,
        solo_retraso=solo_retraso,
    )
    total_pages = max((total + page_size - 1) // page_size, 1)
    if page > total_pages:
        page = total_pages

    rows = obtener_plan_de_trabajo(
        proyecto_id=proyecto_id,
        estatus_id=estatus_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        friendly_name_q=friendly_name_q,
        solo_retraso=solo_retraso,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
    )

    return jsonify({
        "status": "success",
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "rows": [
            {
                "id": r["ID"],
                "nombre_actividad": r.get("NOMBRE_ACTIVIDAD") or "",
                "friendly_name": r.get("FRIENDLY_NAME") or "",
                "proyecto": r.get("NOMBRE_PROYECTO") or "",
                "estatus": r.get("ESTATUS") or "",
                "estatus_color": r.get("ESTATUS_COLOR") or "#94a3b8",
                "responsables": r.get("RESPONSABLES") or "Sin asignar",
                "dias_acordados": r.get("DIAS_ACORDADOS"),
                "fecha_inicio": str(r.get("FECHA_INICIO") or ""),
                "fecha_fin_est": str(r.get("FECHA_FIN_EST") or ""),
                "fecha_fin_real": str(r.get("FECHA_FIN_REAL") or ""),
                "avance_pct": int(r.get("AVANCE_PCT") or 0),
                "en_retraso": bool(int(r.get("EN_RETRASO") or 0)),
                "prioridad": int(r.get("PRIORIDAD") or 2),
            }
            for r in rows
        ],
    })


@app.route("/dashboard-actividades")
def plan_de_trabajo_view():
    base = _catalogo_base()
    return render_template(
        "plan_de_trabajo.html",
        page_title="Plan de trabajo",
        projects=base["projects"],
        estatus_list=obtener_estatus_actividad(),
    )


# ── ACTIVIDADES ────────────────────────────────────────────────────────────


@app.route("/actividades")
def actividades():
    proyecto_id  = request.args.get("proyecto")    or None
    estatus_id   = request.args.get("estatus")     or None
    tipo         = (request.args.get("tipo") or "").strip().upper() or None
    usuario_id   = request.args.get("usuario")     or None
    solicitante  = (request.args.get("solicitante") or "").strip() or None
    fecha_desde  = request.args.get("fecha_desde") or None
    fecha_hasta  = request.args.get("fecha_hasta") or None
    scope        = request.args.get("scope") or "all"
    q            = (request.args.get("q") or "").strip() or None
    sort_by      = request.args.get("sort") or "recientes"
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

    # Cuando hay texto de busqueda, forzamos una consulta global sin restricciones.
    if q:
        proyecto_id = None
        estatus_id = None
        tipo = None
        usuario_id = None
        solicitante = None
        fecha_desde = None
        fecha_hasta = None
        scope = "all"

    solo_activas = False
    default_scope = None
    sin_filtros_explicitos = (
        scope == "all"
        and not q
        and not any([proyecto_id, estatus_id, tipo, usuario_id, solicitante, fecha_desde, fecha_hasta])
    )
    if scope == "active" and not estatus_id:
        solo_activas = True
    elif scope == "completed" and not estatus_id:
        estatus_id = _estatus_id_por_desc("COMPLETADO")
    elif scope == "canceled" and not estatus_id:
        estatus_id = _estatus_id_por_desc("CANCELADO")

    has_filters = bool(
        proyecto_id or estatus_id or tipo or usuario_id or solicitante or fecha_desde or fecha_hasta or q
        or (scope and scope != "all")
    )
    scope_label = "Todas las actividades"
    if scope == "active":
        scope_label = "Actividades activas"
    elif scope == "completed":
        scope_label = "Actividades completadas"
    elif scope == "canceled":
        scope_label = "Actividades canceladas"

    if sin_filtros_explicitos:
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
            tipo=tipo,
            usuario_id=usuario_id,
            solicitante=solicitante,
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
            tipo=tipo,
            usuario_id=usuario_id,
            solicitante=solicitante,
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
        "solicitantes": base["solicitantes"],
        "filtro_proyecto": proyecto_id,
        "filtro_estatus": estatus_id,
        "filtro_tipo": tipo,
        "filtro_usuario": usuario_id,
        "filtro_solicitante": solicitante,
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
    datos["tipo"] = (datos.get("tipo") or "").strip().upper()
    datos, error_campos = _normalizar_campos_dashboard_actividad(datos)
    if error_campos:
        return jsonify({"status": "error", "message": error_campos}), 400

    missing_fields = []
    if not datos.get("id_proyecto"):
        missing_fields.append("proyecto")
    if not datos.get("id_estatus"):
        missing_fields.append("estatus")
    if not datos.get("fecha_solicitud"):
        missing_fields.append("fecha de solicitud")
    if not datos.get("nombre_actividad"):
        missing_fields.append("nombre de la actividad")
    if not datos.get("tipo"):
        missing_fields.append("tipo")
    if missing_fields:
        return (
            jsonify({
                "status": "error",
                "message": f"Faltan campos requeridos: {', '.join(missing_fields)}.",
            }),
            400,
        )

    allowed_tipos = {"DESARROLLO", "TAREA"}
    if datos["tipo"] not in allowed_tipos:
        return (
            jsonify({
                "status": "error",
                "message": "El tipo de actividad es inválido. Solo se permite DESARROLLO o TAREA.",
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
        solicitantes=_solicitantes_para_formulario(actividad.get("SOLICITANTE")),
        users=base["users"],
        projects=base["projects"],
        todas_actividades=obtener_actividades(),
    )


@app.route("/api/actividades/<actividad_id>")
def api_actividad_detalle(actividad_id):
    actividad = obtener_actividad_por_id(actividad_id)
    if not actividad:
        return jsonify({"status": "error", "message": "Actividad no encontrada."}), 404

    responsables = obtener_responsables_actividad(actividad_id)
    recursos = obtener_recursos_actividad(actividad_id)
    return jsonify({
        "status": "success",
        "actividad": {
            "id": actividad["ID"],
            "id_proyecto": actividad["ID_PROYECTO"],
            "tipo": (actividad.get("TIPO") or ""),
            "nombre_actividad": actividad["NOMBRE_ACTIVIDAD"] or "",
            "friendly_name": actividad.get("FRIENDLY_NAME") or "",
            "descripcion": actividad.get("DESCRIPCION") or "",
            "solicitante": actividad.get("SOLICITANTE") or "",
            "fecha_solicitud": str(actividad.get("FECHA_SOLICITUD") or ""),
            "fecha_inicio": str(actividad.get("FECHA_INICIO") or ""),
            "fecha_fin_est": str(actividad.get("FECHA_FIN_EST") or ""),
            "fecha_fin_real": str(actividad.get("FECHA_FIN_REAL") or ""),
            "dias_acordados": str(actividad.get("DIAS_ACORDADOS") or ""),
            "avance_pct": str(actividad.get("AVANCE_PCT") or 0),
            "id_estatus": actividad["ID_ESTATUS"],
            "prioridad": str(actividad.get("PRIORIDAD") or 2),
            "id_actividad_padre": actividad.get("ID_ACTIVIDAD_PADRE") or "",
            "responsables": [item["ID"] for item in responsables],
            "recursos": [item["ID"] for item in recursos],
        },
    })


@app.route("/actividades/<actividad_id>/editar", methods=["POST"])
def editar_actividad(actividad_id):
    datos = request.form.to_dict()
    datos["solicitante"] = (datos.get("solicitante") or "").strip() or None
    datos["tipo"] = (datos.get("tipo") or "").strip().upper()
    datos, error_campos = _normalizar_campos_dashboard_actividad(datos)
    if error_campos:
        return jsonify({"status": "error", "message": error_campos}), 400

    if not datos.get("tipo"):
        return jsonify({"status": "error", "message": "El tipo es obligatorio."}), 400
    if datos["tipo"] not in {"DESARROLLO", "TAREA"}:
        return (
            jsonify({
                "status": "error",
                "message": "El tipo de actividad es inválido. Solo se permite DESARROLLO o TAREA.",
            }),
            400,
        )

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
    filtros, filtros_meta = _registros_filters_from_request(request.args)
    # Solo carga actividades cuando hay un proyecto seleccionado; si no, lista vacía
    # para evitar que el select de Actividad se popule con todas las actividades.
    actividades_registro = _registros_activity_options(filtros) if filtros.get("proyecto_id") else []
    registros = obtener_registros(filtros)
    return render_template(
        "registros.html",
        registros=registros,
        users=base["users"],
        projects=base["projects"],
        actividades=actividades_registro,
        tipos_actividad=obtener_tipos_actividad(),
        filtros=filtros,
        filtros_meta=filtros_meta,
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


@app.route("/registros/<registro_id>/duplicar", methods=["POST"])
def api_duplicar_registro(registro_id):
    payload = request.get_json(silent=True) or {}
    fecha_destino = (payload.get("fecha_destino") or "").strip()
    if not fecha_destino:
        return jsonify({"status": "error", "message": "La fecha destino es obligatoria."}), 400

    try:
        fecha_obj = datetime.strptime(fecha_destino, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"status": "error", "message": "Formato de fecha inválido."}), 400

    if not _validar_fecha_hasta_fin_semana_actual(fecha_obj):
        _, semana_fin = _semana_actual_limites()
        return jsonify({
            "status": "error",
            "message": (
                "No puedes duplicar en fechas posteriores al fin de la semana en curso "
                f"({semana_fin.isoformat()})."
            ),
        }), 400

    origen = obtener_registro_por_id(registro_id)
    if not origen:
        return jsonify({"status": "error", "message": "Registro origen no encontrado."}), 404

    user_id = origen.get("ID_USUARIO")
    horas_originales = float(origen.get("HORAS") or 0)
    if horas_originales <= 0:
        return jsonify({"status": "error", "message": "El registro origen no tiene horas válidas para duplicar."}), 400

    horas_ausencia = get_horas_ausencia_dia(user_id, fecha_destino)
    horas_ocupadas = get_horas_diarias(user_id, fecha_destino)
    disponibles = max(0.0, (8.0 - horas_ausencia) - horas_ocupadas)

    if disponibles <= 0:
        return jsonify({
            "status": "error",
            "message": "No hay horas disponibles para ese usuario en la fecha seleccionada.",
        }), 400

    horas_finales = min(horas_originales, disponibles)
    datos_duplicado = {
        "date": fecha_destino,
        "user": user_id,
        "project": origen.get("ID_PROYECTO"),
        "actividad_id": origen.get("ID_ACTIVIDAD") or None,
        "tipo_act": origen.get("ID_TIPO_ACT") or None,
        "activity_action": origen.get("ACCION") or "",
        "hours": str(round(horas_finales, 2)),
        "details": origen.get("DETALLES") or "",
    }

    try:
        ok = _guardar_registro_con_evidencia(datos_duplicado, {})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception:
        app.logger.exception("Error inesperado al duplicar registro")
        return jsonify({"status": "error", "message": "Ocurrió un error interno al duplicar el registro."}), 500

    if not ok:
        return jsonify({"status": "error", "message": "No se pudo duplicar el registro."}), 500

    ajustada = abs(horas_finales - horas_originales) > 1e-9
    return jsonify({
        "status": "success",
        "message": "Registro duplicado correctamente.",
        "horas_originales": round(horas_originales, 2),
        "horas_finales": round(horas_finales, 2),
        "ajustada": ajustada,
        "fecha_destino": fecha_destino,
    })


#

# ── Pega este bloque en app.py justo antes de: if __name__ == '__main__': ──


@app.route("/reporte/excel/<proyecto_id>")
def exportar_reporte_excel(proyecto_id):
    from excel_reporte import generar_reporte
    import io

    base = _catalogo_base()
    filtros, filtros_meta = _registros_filters_from_request(request.args, proyecto_id=proyecto_id)
    actividades_catalogo = _registros_activity_options(filtros)
    proyecto = next((p for p in base["projects"] if str(p["ID"]) == str(filtros["proyecto_id"])), None)
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404
    actividades, evidencias_por_actividad = obtener_datos_reporte_proyecto(
        filtros["proyecto_id"], filtros
    )
    nombre = proyecto["NOMBRE_PROYECTO"]
    upload_base = os.path.join(app.root_path, "static", "uploads", "evidencias")
    export_context = _build_registros_export_context(
        filtros, filtros_meta, base, actividades_catalogo
    )
    xlsx_bytes = generar_reporte(
        nombre,
        actividades,
        evidencias_por_actividad,
        upload_base,
        export_context=export_context,
    )
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=(
            f'Reporte_{nombre.replace(" ","_")}'
            f'_{export_context.get("fecha_ini","").replace("/","-")}'
            f'_al_{export_context.get("fecha_fin","").replace("/","-")}.xlsx'
        ),
    )


@app.route("/reporte/ausencias/excel")
def reporte_ausencias_excel():
    """Exporta un reporte Excel de ausencias del mes visible en Calendario."""
    from excel_ausencias_reporte import generar_reporte_ausencias, TIPOS_VALIDOS
    import io as _io
    from datetime import date as _date

    try:
        anio = int(request.args.get("anio", _date.today().year))
        mes  = int(request.args.get("mes",  _date.today().month))
        tipo = request.args.get("tipo", "").strip().upper()
    except (ValueError, TypeError):
        return jsonify({"error": "Parámetros inválidos."}), 400

    if not (2020 <= anio <= 2100) or not (1 <= mes <= 12):
        return jsonify({"error": "Año o mes fuera de rango."}), 400

    if tipo and tipo not in TIPOS_VALIDOS:
        return jsonify({"error": f"Tipo de ausencia no válido: {tipo!r}."}), 400

    try:
        base      = _catalogo_base()
        ausencias = obtener_ausencias_mes(anio, mes)
        xlsx      = generar_reporte_ausencias(anio, mes, base["users"], ausencias, tipo)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Error interno al generar el reporte."}), 500

    tipo_sufijo = f"_{tipo}" if tipo else ""
    return send_file(
        _io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"Ausencias{tipo_sufijo}_{anio}_{mes:02d}.xlsx",
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
    obtener_solicitantes_todos, crear_solicitante, actualizar_solicitante,
    toggle_solicitante, eliminar_solicitante,
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
        solicitantes = obtener_solicitantes_todos(),
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


@app.route("/api/catalogos/solicitantes", methods=["POST"])
def api_cat_solicitantes_crear():
    data = request.get_json() or {}
    if not (data.get("nombre") or "").strip():
        return _json_err("El nombre del solicitante es obligatorio.", 400)
    return _json_ok() if crear_solicitante(data) else _json_err("Error al crear solicitante. Verifica que no exista otro con el mismo nombre.")

@app.route("/api/catalogos/solicitantes/<id>", methods=["PUT"])
def api_cat_solicitantes_editar(id):
    data = request.get_json() or {}
    if not (data.get("nombre") or "").strip():
        return _json_err("El nombre del solicitante es obligatorio.", 400)
    return _json_ok() if actualizar_solicitante(id, data) else _json_err("Error al actualizar. Verifica que no exista otro con el mismo nombre.")

@app.route("/api/catalogos/solicitantes/<id>/toggle", methods=["POST"])
def api_cat_solicitantes_toggle(id):
    return _json_ok() if toggle_solicitante(id, request.get_json().get("activo", 1)) else _json_err("Error.")

@app.route("/api/catalogos/solicitantes/<id>", methods=["DELETE"])
def api_cat_solicitantes_eliminar(id):
    ok, msg = eliminar_solicitante(id)
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
