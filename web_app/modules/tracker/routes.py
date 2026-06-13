from datetime import datetime, timedelta, date
from flask import Blueprint, render_template, request, jsonify, url_for, redirect, current_app
from config import Config

from web_app.database import ejecutar_query
from web_app.modules.tracker.queries import (
    guardar_registro_actividad,
    get_horas_semanales,
    get_horas_diarias,
    get_horas_ausencia_dia,
    obtener_registros,
    obtener_registro_por_id,
    actualizar_registro,
    eliminar_registro,
    obtener_actividad_completa,
)
from web_app.modules.tracker.utils import (
    _semana_actual_limites,
    _validar_fecha_hasta_fin_semana_actual,
    _local_today,
)

tracker_bp = Blueprint("tracker_bp", __name__)


# ── Helpers de catálogos y formatos ───────────────────────────────────────

def _catalogo_base():
    from web_app.modules.catalogos.queries import obtener_solicitantes
    return {
        "users": ejecutar_query(
            'SELECT "ID","NOMBRE_COMPLETO" FROM "USUARIOS" WHERE "ACTIVO"=1 ORDER BY "NOMBRE_COMPLETO"'
        ),
        "projects": ejecutar_query(
            'SELECT "ID","NOMBRE_PROYECTO" FROM "PROYECTOS" WHERE "ACTIVO"=1'
        ),
        "solicitantes": obtener_solicitantes(),
    }


def _mes_actual_limites() -> tuple[str, str]:
    from calendar import monthrange
    hoy = _local_today()
    inicio = hoy.replace(day=1)
    fin = hoy.replace(day=monthrange(hoy.year, hoy.month)[1])
    return inicio.isoformat(), fin.isoformat()


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
    from web_app.modules.dashboard.queries import obtener_catalogo_actividades
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


def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def _guardar_registro_con_evidencia(datos, request_files):
    from web_app.modules.dashboard.queries import obtener_actividad_por_id
    from web_app.modules.evidencias.services import _save_upload
    from web_app.modules.evidencias.queries import crear_evidencia

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


# ── RUTAS DE ESTRUCTURA MONOLÍTICA REUBICADAS ─────────────────────────────

@tracker_bp.route("/", methods=["GET", "POST"])
def index():
    from web_app.modules.catalogos.queries import (
        obtener_estatus_actividad,
        obtener_entregables,
        obtener_recursos,
        obtener_tipos_actividad,
        obtener_tipos_evidencia,
    )

    if request.method == "POST":
        datos = request.form.to_dict()
        datos["quick_recursos"] = request.form.getlist("quick_recursos")
        _, semana_fin = _semana_actual_limites()

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
                    current_app.logger.exception(f"Error al guardar registro de rango para {fecha_str}")
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
            current_app.logger.exception("Error inesperado al guardar registro de actividad")
            return jsonify({"status": "error", "message": "Ocurrió un error interno al guardar el registro."}), 500
        if ok:
            return jsonify({"status": "success", "message": "¡Registro guardado correctamente!"})
        return jsonify({"status": "error", "message": "Hubo un fallo al conectar con SAP HANA."}), 500

    base = _catalogo_base()
    semana_inicio, semana_fin = _semana_actual_limites()
    return render_template(
        "tracker/index.html",
        users=base["users"],
        projects=base["projects"],
        solicitantes=base["solicitantes"],
        estatus_list=obtener_estatus_actividad(),
        lista_entregables=obtener_entregables(),
        recursos=obtener_recursos(),
        tipos_actividad=obtener_tipos_actividad(),
        tipos_evidencia=obtener_tipos_evidencia(),
        current_week_start=semana_inicio.isoformat(),
        current_week_end=semana_fin.isoformat(),
    )


@tracker_bp.route("/api/weekly_hours")
def api_weekly_hours():
    return jsonify(
        {
            "total": get_horas_semanales(
                request.args.get("user"), request.args.get("date")
            )
        }
    )


@tracker_bp.route("/api/daily_hours")
def api_daily_hours():
    user_id = request.args.get("user")
    fecha = request.args.get("date")
    total = get_horas_diarias(user_id, fecha)
    horas_ausencia = get_horas_ausencia_dia(user_id, fecha)
    return jsonify({"total": total, "horas_ausencia": horas_ausencia})


@tracker_bp.route("/api/actividades_proyecto")
def api_actividades_proyecto():
    from web_app.modules.dashboard.queries import obtener_actividades_por_proyecto
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


@tracker_bp.route("/registros")
def vista_registros():
    base = _catalogo_base()
    filtros, filtros_meta = _registros_filters_from_request(request.args)
    actividades_registro = _registros_activity_options(filtros) if filtros.get("proyecto_id") else []
    registros = obtener_registros(filtros)
    from web_app.modules.catalogos.queries import obtener_tipos_actividad
    return render_template(
        "tracker/registros.html",
        registros=registros,
        users=base["users"],
        projects=base["projects"],
        actividades=actividades_registro,
        tipos_actividad=obtener_tipos_actividad(),
        filtros=filtros,
        filtros_meta=filtros_meta,
    )


@tracker_bp.route("/registros/<registro_id>", methods=["GET"])
def api_registro(registro_id):
    r = obtener_registro_por_id(registro_id)
    if not r:
        return jsonify({"status": "error", "message": "No encontrado"}), 404
    return jsonify({"status": "success", "data": dict(r)})


@tracker_bp.route("/registros/<registro_id>", methods=["PUT"])
def api_actualizar_registro(registro_id):
    datos = request.get_json()
    if actualizar_registro(registro_id, datos):
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Error al actualizar"}), 500


@tracker_bp.route("/registros/<registro_id>", methods=["DELETE"])
def api_eliminar_registro(registro_id):
    if eliminar_registro(registro_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": "Error al eliminar"}), 500


@tracker_bp.route("/registros/<registro_id>/duplicar", methods=["POST"])
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
        current_app.logger.exception("Error inesperado al duplicar registro")
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


# Endpoint de consulta de contexto
@tracker_bp.route('/api/actividades/<id_padre>/contexto_completo', methods=['GET'])
def obtener_contexto_subactividad(id_padre):
    try:
        actividad = obtener_actividad_completa(id_padre)
        if not actividad:
            return jsonify({"status": "error", "message": "Actividad padre no encontrada"}), 404
            
        # Mapeamos todas las columnas de tu consulta a llaves limpias para el Frontend
        return jsonify({
            "status": "success",
            "data": {
                "id": actividad.get('ID'),
                "id_proyecto": actividad.get('ID_PROYECTO'),
                "nombre_actividad": actividad.get('NOMBRE_ACTIVIDAD'),
                "descripcion": actividad.get('DESCRIPCION'),
                "fecha_solicitud": str(actividad.get('FECHA_SOLICITUD') or ''),
                "solicitante": actividad.get('SOLICITANTE'),
                "fecha_inicio": str(actividad.get('FECHA_INICIO') or ''),
                "fecha_fin_est": str(actividad.get('FECHA_FIN_EST') or ''),
                "fecha_fin_real": str(actividad.get('FECHA_FIN_REAL') or ''),
                "id_estatus": actividad.get('ID_ESTATUS'),
                "avance_pct": actividad.get('AVANCE_PCT', 0),
                "prioridad": actividad.get('PRIORIDAD'),
                "asignado_a": actividad.get('ASIGNADO_A'),
                "id_actividad_padre": actividad.get('ID_ACTIVIDAD_PADRE'),
                "tipo": actividad.get('TIPO'),
                "friendly_name": actividad.get('FRIENDLY_NAME'),
                "dias_acordados": actividad.get('DIAS_ACORDADOS'),
                "id_entregable": actividad.get('ID_ENTREGABLE')
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

