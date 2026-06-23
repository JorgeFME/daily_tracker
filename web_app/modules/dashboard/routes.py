from datetime import datetime, timedelta, date
from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    url_for,
    redirect,
    current_app,
)

from web_app.database import ejecutar_query
from web_app.modules.dashboard.queries import (
    obtener_datos_grafica_proyectos,
    obtener_registros_recientes_filtrados,
    get_horas_ausencia_periodo,
    contar_plan_de_trabajo,
    obtener_plan_de_trabajo,
    contar_actividades_default_mes,
    obtener_actividades_default_mes,
    contar_actividades,
    obtener_actividades,
    crear_actividad,
    obtener_actividad_por_id,
    guardar_responsables_actividad,
    guardar_recursos_actividad,
    obtener_actividad_nombre,
    obtener_actividades_hijas,
    obtener_historial_actividad,
    obtener_detalles_actividad,
    obtener_recursos_actividad,
    obtener_responsables_actividad,
    reasignar_registros_proyecto,
    actualizar_actividad,
    eliminar_actividad,
    crear_detalle_actividad,
    toggle_detalle_completado,
    eliminar_detalle_actividad,
    obtener_ausencias_mes,
    obtener_ausencias,
    guardar_ausencia,
    actualizar_ausencia,
    eliminar_ausencia,
)
from web_app.modules.tracker.utils import _local_today
from web_app.modules.tracker.routes import (
    _catalogo_base,
    _catalog_label,
    _fmt_fecha_corta,
    _parse_iso_date,
)
from web_app.modules.dashboard.utils import (
    _normalizar_campos_dashboard_actividad,
    organizar_actividades_para_vista,
)

dashboard_bp = Blueprint("dashboard_bp", __name__)


# ── Helpers locales para Calendario y Ausencias ─────────────────────────────


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
        fecha_inicio = datetime.strptime(
            str(datos.get("fecha_inicio")), "%Y-%m-%d"
        ).date()
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
            return (
                None,
                f"Horas ausentes fuera de rango permitido (0.25 a {max_total}).",
            )

        horas_dia = round(horas_total / dias_habiles, 2)
    else:
        try:
            horas_dia = float(datos.get("horas_dia", 8))
        except (TypeError, ValueError):
            return None, "Horas por día inválidas."

    if horas_dia < 0.25 or horas_dia > 8:
        return None, "Horas por día fuera de rango permitido (0.25 a 8)."

    tipo = str(datos.get("tipo") or "").strip().upper()
    descripcion = (
        str(datos.get("descripcion") or "").strip()
        if tipo == "OTRO"
        else _descripcion_respaldo_ausencia(tipo)
    )

    datos_normalizados = dict(datos)
    datos_normalizados["fecha_inicio"] = fecha_inicio.isoformat()
    datos_normalizados["fecha_fin"] = fecha_fin.isoformat()
    datos_normalizados["tipo"] = tipo
    datos_normalizados["horas_dia"] = horas_dia
    datos_normalizados["descripcion"] = descripcion or None
    return datos_normalizados, None


def _solicitantes_para_formulario(actual: str | None = None):
    from web_app.modules.catalogos.queries import obtener_solicitantes

    solicitantes = [dict(item) for item in obtener_solicitantes()]
    actual_normalizado = (actual or "").strip()
    if actual_normalizado and not any(
        (item.get("NOMBRE") or "").strip().casefold() == actual_normalizado.casefold()
        for item in solicitantes
    ):
        solicitantes.append(
            {
                "ID": f"legacy::{actual_normalizado}",
                "NOMBRE": actual_normalizado,
                "ACTIVO": 0,
                "ES_LEGACY": 1,
            }
        )
    return solicitantes


def _json_ok():
    return jsonify({"status": "success"})


def _json_err(msg, code=500):
    return jsonify({"status": "error", "message": msg}), code


# ── RUTAS DEL PLAN DE TRABAJO Y DASHBOARD ────────────────────────────────


@dashboard_bp.route("/dashboard-actividades")
def plan_de_trabajo_view():
    from web_app.modules.catalogos.queries import obtener_estatus_actividad

    base = _catalogo_base()
    return render_template(
        "dashboard/plan_de_trabajo.html",
        page_title="Plan de trabajo",
        projects=base["projects"],
        estatus_list=obtener_estatus_actividad(),
    )


@dashboard_bp.route("/api/dashboard_data")
def dashboard_data():
    user_id = request.args.get("user") or None
    period = request.args.get("period", "month")
    fecha_ref = request.args.get("fecha_ref") or None

    try:
        ref_date = (
            datetime.strptime(fecha_ref, "%Y-%m-%d").date()
            if fecha_ref
            else date.today()
        )
    except Exception:
        ref_date = date.today()

    if period == "day":
        start_date = ref_date
        end_date = ref_date
    elif period == "week":
        start_date = ref_date - timedelta(days=ref_date.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == "month":
        from calendar import monthrange

        start_date = date(ref_date.year, ref_date.month, 1)
        end_date = date(
            ref_date.year, ref_date.month, monthrange(ref_date.year, ref_date.month)[1]
        )
    elif period == "year":
        start_date = date(ref_date.year, 1, 1)
        end_date = date(ref_date.year, 12, 31)
    else:
        start_date = date.today()
        end_date = date.today()

    ausencia_horas = get_horas_ausencia_periodo(
        user_id, start_date.isoformat(), end_date.isoformat()
    )

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


@dashboard_bp.route("/api/plan_de_trabajo")
def plan_de_trabajo_rapidas():
    proyecto_id = (request.args.get("proyecto_id") or "").strip() or None
    estatus_id = (request.args.get("estatus_id") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    friendly_name_q = (request.args.get("friendly_name_q") or "").strip() or None
    solo_retraso = str(request.args.get("solo_retraso") or "").strip().lower() in {
        "1",
        "true",
        "si",
        "yes",
    }
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
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "page y page_size deben ser enteros válidos.",
                }
            ),
            400,
        )

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

    return jsonify(
        {
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
        }
    )


@dashboard_bp.route("/actividades")
def actividades():
    proyecto_id = request.args.get("proyecto") or None
    estatus_id = request.args.get("estatus") or None
    tipo = (request.args.get("tipo") or "").strip().upper() or None
    usuario_id = request.args.get("usuario") or None
    solicitante = (request.args.get("solicitante") or "").strip() or None
    fecha_desde = request.args.get("fecha_desde") or None
    fecha_hasta = request.args.get("fecha_hasta") or None
    scope = request.args.get("scope") or "all"
    q = (request.args.get("q") or "").strip() or None
    sort_by = request.args.get("sort") or "recientes"
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    page_size = 24
    from web_app.modules.catalogos.queries import (
        obtener_estatus_actividad,
        obtener_recursos,
        obtener_entregables,
    )

    estatus_list = obtener_estatus_actividad()

    def _estatus_id_por_desc(*targets):
        targets_up = {t.upper() for t in targets}
        for e in estatus_list:
            desc = (e.get("DESCRIPCION") or "").upper()
            if desc in targets_up:
                return e.get("ID")
        return None

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
        and not any(
            [
                proyecto_id,
                estatus_id,
                tipo,
                usuario_id,
                solicitante,
                fecha_desde,
                fecha_hasta,
            ]
        )
    )
    if scope == "active" and not estatus_id:
        solo_activas = True
    elif scope == "completed" and not estatus_id:
        estatus_id = _estatus_id_por_desc("COMPLETADO")
    elif scope == "canceled" and not estatus_id:
        estatus_id = _estatus_id_por_desc("CANCELADO")

    has_filters = bool(
        proyecto_id
        or estatus_id
        or tipo
        or usuario_id
        or solicitante
        or fecha_desde
        or q
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
        "actividades": organizar_actividades_para_vista(actividades_data),
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
        return render_template(
            "partials/actividades_resultados.html", **template_context
        )

    return render_template(
        "dashboard/actividades.html",
        todas_actividades=obtener_actividades(),
        recursos=obtener_recursos(),
        **template_context,
        entregables_list=obtener_entregables(),
    )


@dashboard_bp.route("/actividades/nueva", methods=["POST"])
def nueva_actividad():
    datos = request.form.to_dict()
    datos["nombre_actividad"] = (datos.get("nombre_actividad") or "").strip()
    datos["descripcion"] = (datos.get("descripcion") or "").strip() or None
    datos["solicitante"] = (datos.get("solicitante") or "").strip() or None
    datos["tipo"] = (datos.get("tipo") or "").strip().upper()

    # Si la petición viene de una subactividad rápida (tiene id_actividad_padre),
    # duplicamos automáticamente la fecha de solicitud en la fecha de inicio.
    if datos.get("id_actividad_padre"):
        datos["fecha_inicio"] = datos.get("fecha_solicitud")

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
            jsonify(
                {
                    "status": "error",
                    "message": f"Faltan campos requeridos: {', '.join(missing_fields)}.",
                }
            ),
            400,
        )

    allowed_tipos = {"DESARROLLO", "TAREA"}
    if datos["tipo"] not in allowed_tipos:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "El tipo de actividad es inválido. Solo se permite DESARROLLO o TAREA.",
                }
            ),
            400,
        )

    padre_id = datos.get("id_actividad_padre") or None
    if padre_id:
        actividad_padre = obtener_actividad_por_id(padre_id)
        if not actividad_padre:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "La actividad padre seleccionada ya no existe.",
                    }
                ),
                400,
            )
        if str(actividad_padre["ID_PROYECTO"]) != str(datos.get("id_proyecto")):
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "La actividad padre debe pertenecer al mismo proyecto.",
                    }
                ),
                400,
            )

    if crear_actividad(datos):
        actividad_payload = None
        rows = ejecutar_query(
            'SELECT "ID" FROM "ACTIVIDADES" WHERE "NOMBRE_ACTIVIDAD"=? AND "ID_PROYECTO"=? '
            "AND COALESCE(\"ID_ACTIVIDAD_PADRE\", '')=COALESCE(?, '') ORDER BY \"CREADO_EN\" DESC",
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


@dashboard_bp.route("/actividades/<actividad_id>")
def detalle_actividad(actividad_id):
    actividad = obtener_actividad_por_id(actividad_id)
    if not actividad:
        return redirect(url_for("dashboard_bp.actividades"))
    base = _catalogo_base()
    actividad_padre = None
    if actividad.get("ID_ACTIVIDAD_PADRE"):
        actividad_padre = obtener_actividad_nombre(actividad["ID_ACTIVIDAD_PADRE"])
    from web_app.modules.catalogos.queries import (
        obtener_estatus_actividad,
        obtener_tipos_evidencia,
        obtener_recursos,
    )
    from web_app.modules.evidencias.queries import obtener_evidencias_actividad

    return render_template(
        "dashboard/detalle_actividad.html",
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


@dashboard_bp.route("/api/actividades/<actividad_id>")
def api_actividad_detalle(actividad_id):
    actividad = obtener_actividad_por_id(actividad_id)
    if not actividad:
        return jsonify({"status": "error", "message": "Actividad no encontrada."}), 404

    responsables = obtener_responsables_actividad(actividad_id)
    recursos = obtener_recursos_actividad(actividad_id)
    return jsonify(
        {
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
                "id_entregable": actividad.get("ID_ENTREGABLE"),
            },
        }
    )


@dashboard_bp.route("/actividades/<actividad_id>/editar", methods=["POST"])
def editar_actividad(actividad_id):
    datos = request.form.to_dict()
    datos["solicitante"] = (datos.get("solicitante") or "").strip() or None
    datos["tipo"] = (datos.get("tipo") or "").strip().upper()

    # 1. Traer la actividad actual de SAP HANA para tener el respaldo real de los datos actuales
    actividad_actual = obtener_actividad_por_id(actividad_id)
    if not actividad_actual:
        return jsonify({"status": "error", "message": "La actividad no existe."}), 404

    # 2. BLINDAJE CONTRA SOBREESCRITURAS ENTRE FORMULARIOS
    # Si un campo clave viene ausente o completamente vacío (""), heredamos el valor real de la base de datos.
    
    if "nombre_actividad" not in datos or not datos.get("nombre_actividad"):
        datos["nombre_actividad"] = actividad_actual.get("NOMBRE_ACTIVIDAD")

    if "fecha_solicitud" not in datos or datos.get("fecha_solicitud") == "":
        datos["fecha_solicitud"] = str(actividad_actual.get("FECHA_SOLICITUD") or "")

    if "fecha_inicio" not in datos or datos.get("fecha_inicio") == "":
        datos["fecha_inicio"] = str(actividad_actual.get("FECHA_INICIO") or "")

    if "fecha_fin_est" not in datos or datos.get("fecha_fin_est") == "":
        datos["fecha_fin_est"] = str(actividad_actual.get("FECHA_FIN_EST") or "")

    if "fecha_fin_real" not in datos or datos.get("fecha_fin_real") == "":
        datos["fecha_fin_real"] = str(actividad_actual.get("FECHA_FIN_REAL") or "")

    if "avance_pct" not in datos or datos.get("avance_pct") in (None, ""):
    # Lo pasamos como string para que el .strip() de utils.py no truene
        datos["avance_pct"] = str(actividad_actual.get("AVANCE_PCT") or 0)

    if "id_proyecto" not in datos:
        datos["id_proyecto"] = actividad_actual.get("ID_PROYECTO")

    if "id_estatus" not in datos:
        datos["id_estatus"] = actividad_actual.get("ID_ESTATUS")

    if not datos.get("tipo"):
        datos["tipo"] = actividad_actual.get("TIPO")

    # 3. Mantenemos tu lógica existente para id_entregable
    id_entregable_form = datos.get("id_entregable") or request.form.get("id_entregable")
    if not id_entregable_form and actividad_actual:
        id_entregable_form = actividad_actual.get("ID_ENTREGABLE")

    datos["id_entregable"] = (
        str(id_entregable_form) if id_entregable_form else ""
    ).strip() or None

    # 4. Ejecutamos la función de normalización del dashboard con el payload combinado y seguro
    datos, error_campos = _normalizar_campos_dashboard_actividad(datos)
    if error_campos:
        return jsonify({"status": "error", "message": error_campos}), 400

    if not datos.get("id_entregable") and id_entregable_form:
        datos["id_entregable"] = str(id_entregable_form).strip()

    if not datos.get("tipo"):
        return jsonify({"status": "error", "message": "El tipo es obligatorio."}), 400
        
    if datos["tipo"] not in {"DESARROLLO", "TAREA"}:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "El tipo de actividad es inválido. Solo se permite DESARROLLO o TAREA.",
                }
            ),
            400,
        )

    nuevo_proyecto = datos.get("id_proyecto")
    proyecto_cambio = (
        actividad_actual
        and nuevo_proyecto
        and str(actividad_actual["ID_PROYECTO"]) != str(nuevo_proyecto)
    )

    # 5. Guardado en Base de Datos
    if actualizar_actividad(actividad_id, datos):
        if proyecto_cambio:
            reasignar_registros_proyecto(actividad_id, nuevo_proyecto)
            
        # BLINDAJE PARA RELACIONES: Solo actualizar responsables y recursos si el formulario enviado los incluía.
        # Esto previene que si un modal no despliega o no selecciona encargados, limpie la tabla relacional.
        if "responsables" in request.form:
            guardar_responsables_actividad(
                actividad_id, request.form.getlist("responsables")
            )
        if "recursos" in request.form:
            guardar_recursos_actividad(actividad_id, request.form.getlist("recursos"))
            
        msg = (
            "Actividad actualizada y horas reasignadas al nuevo proyecto."
            if proyecto_cambio
            else "Actividad actualizada."
        )
        return jsonify({"status": "success", "message": msg})
        
    return jsonify({"status": "error", "message": "Error al actualizar."}), 500


@dashboard_bp.route("/actividades/<actividad_id>", methods=["DELETE"])
def borrar_actividad(actividad_id):
    if eliminar_actividad(actividad_id):
        return jsonify({"status": "success"})
    return (
        jsonify({"status": "error", "message": "Error al eliminar la actividad."}),
        500,
    )


@dashboard_bp.route("/actividades/<actividad_id>/detalle", methods=["POST"])
def agregar_detalle(actividad_id):
    datos = request.form.to_dict()
    if crear_detalle_actividad(actividad_id, datos):
        return jsonify({"status": "success", "message": "Detalle agregado."})
    return jsonify({"status": "error", "message": "Error al guardar el detalle."}), 500


@dashboard_bp.route("/detalle/<detalle_id>/toggle", methods=["POST"])
def toggle_detalle(detalle_id):
    completado = request.json.get("completado", False)
    if toggle_detalle_completado(detalle_id, completado):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500


@dashboard_bp.route("/detalle/<detalle_id>", methods=["DELETE"])
def borrar_detalle(detalle_id):
    if eliminar_detalle_actividad(detalle_id):
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 500

@dashboard_bp.route("/api/catalogos/entregables/por-proyecto/<id_proyecto>", methods=["GET"])
def api_entregables_por_proyecto_json(id_proyecto):
    """
    Regresa los entregables activos en JSON pertenecientes al id_proyecto 
    para alimentar dinámicamente los selects en los formularios.
    """
    from web_app.modules.catalogos.queries import obtener_entregables
    try:
        # Usamos la función modificada que recibe opcionalmente el proyecto
        rows = obtener_entregables(id_proyecto)
        return jsonify({
            "status": "success",
            "data": [
                {"id": r["ID"], "nombre": r["NOMBRE"]} 
                for r in rows
            ]
        })
    except Exception as e:
        print(f"[api_entregables_por_proyecto_json] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
    
    
# ── CALENDARIO Y AUSENCIAS ROUTES ────────────────────────────────────────


@dashboard_bp.route("/calendario")
def calendario():
    hoy = date.today()
    anio = int(request.args.get("anio", hoy.year))
    mes = int(request.args.get("mes", hoy.month))
    base = _catalogo_base()
    from web_app.modules.catalogos.queries import (
        obtener_dias_festivos_mes,
        obtener_dias_festivos,
    )

    return render_template(
        "dashboard/calendario.html",
        users=base["users"],
        anio=anio,
        mes=mes,
        ausencias=obtener_ausencias_mes(anio, mes),
        festivos=obtener_dias_festivos_mes(anio, mes),
        todos_festivos=obtener_dias_festivos(anio),
        todas_ausencias=obtener_ausencias({"anio": anio}),
    )


@dashboard_bp.route("/api/ausencias")
def api_ausencias():
    filtros = {
        "user_id": request.args.get("user_id"),
        "tipo": request.args.get("tipo"),
        "anio": request.args.get("anio"),
    }
    rows = obtener_ausencias(filtros)
    return jsonify(
        [
            {
                "id": r["ID"],
                "id_usuario": r["ID_USUARIO"],
                "usuario": r["USUARIO"],
                "fecha_inicio": str(r["FECHA_INICIO"]),
                "fecha_fin": str(r["FECHA_FIN"]),
                "tipo": r["TIPO"],
                "horas_dia": float(r["HORAS_DIA"]),
                "descripcion": r.get("DESCRIPCION")
                or _descripcion_respaldo_ausencia(r.get("TIPO")),
            }
            for r in rows
        ]
    )


@dashboard_bp.route("/api/ausencias", methods=["POST"])
def api_ausencia_crear():
    datos = request.get_json(silent=True) or {}
    datos_normalizados, error = _normalizar_payload_ausencia(datos)
    if error:
        return _json_err(error, 400)
    return (
        _json_ok()
        if guardar_ausencia(datos_normalizados)
        else _json_err("Error al guardar la ausencia.")
    )


@dashboard_bp.route("/api/ausencias/<ausencia_id>", methods=["PUT"])
def api_ausencia_editar(ausencia_id):
    datos = request.get_json(silent=True) or {}
    datos_normalizados, error = _normalizar_payload_ausencia(datos)
    if error:
        return _json_err(error, 400)
    return (
        _json_ok()
        if actualizar_ausencia(ausencia_id, datos_normalizados)
        else _json_err("Error al actualizar.")
    )


@dashboard_bp.route("/api/ausencias/<ausencia_id>", methods=["DELETE"])
def api_ausencia_eliminar(ausencia_id):
    return (
        _json_ok()
        if eliminar_ausencia(ausencia_id)
        else _json_err("Error al eliminar.")
    )


@dashboard_bp.route("/api/ausencias/dia")
def api_ausencias_dia():
    from web_app.modules.tracker.queries import get_horas_ausencia_dia

    user_id = request.args.get("user")
    fecha = request.args.get("fecha")
    horas = get_horas_ausencia_dia(user_id, fecha)
    return jsonify({"horas_ausencia": horas})
