from flask import Blueprint, render_template, request, jsonify

from web_app.modules.catalogos.queries import (
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
    obtener_dias_festivos, guardar_dia_festivo, actualizar_dia_festivo,
    toggle_dia_festivo, eliminar_dia_festivo,
    agregar_categoria_db, eliminar_categoria_db,
    obtener_entregables_por_proyecto, crear_entregable, actualizar_entregable,
    toggle_entregable, eliminar_logico_entregable
)

catalogos_bp = Blueprint("catalogos_bp", __name__)


def _json_ok():              return jsonify({"status": "success"})
def _json_err(msg, code=500): return jsonify({"status": "error", "message": msg}), code


@catalogos_bp.route("/catalogos")
def vista_catalogos():

    # Obtenemos el id_proyecto desde la URL (?id_proyecto=xxx)
    id_proyecto_activo = request.args.get("id_proyecto", "")
    
    # Obtenemos los entregables filtrados si es que hay un proyecto seleccionado
    entregables = []
    if id_proyecto_activo:
        entregables = obtener_entregables_por_proyecto(id_proyecto_activo)


    return render_template(
        "catalogos/catalogos.html",
        estatus   = obtener_estatus_actividad_todos(),
        tipos     = obtener_tipos_actividad_todos(),
        tipos_ev  = obtener_tipos_evidencia_todos(),
        recursos  = obtener_recursos_todos(),
        solicitantes = obtener_solicitantes_todos(),
        proyectos = obtener_proyectos_todos(),
        entregables   = entregables,
        id_proyecto_activo = id_proyecto_activo
    )


# ── APIs DE ESTATUS ─────────────────────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/estatus", methods=["POST"])
def api_cat_estatus_crear():
    return _json_ok() if crear_estatus(request.get_json()) else _json_err("Error al crear estatus.")


@catalogos_bp.route("/api/catalogos/estatus/<id>", methods=["PUT"])
def api_cat_estatus_editar(id):
    return _json_ok() if actualizar_estatus(id, request.get_json()) else _json_err("Error al actualizar.")


@catalogos_bp.route("/api/catalogos/estatus/<id>/toggle", methods=["POST"])
def api_cat_estatus_toggle(id):
    return _json_ok() if toggle_estatus(id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/catalogos/estatus/<id>", methods=["DELETE"])
def api_cat_estatus_eliminar(id):
    ok, msg = eliminar_estatus(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── APIs DE TIPOS DE ACTIVIDAD ──────────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/tipos", methods=["POST"])
def api_cat_tipos_crear():
    return _json_ok() if crear_tipo_actividad(request.get_json()) else _json_err("Error al crear tipo.")


@catalogos_bp.route("/api/catalogos/tipos/<id>", methods=["PUT"])
def api_cat_tipos_editar(id):
    return _json_ok() if actualizar_tipo_actividad(id, request.get_json()) else _json_err("Error al actualizar.")


@catalogos_bp.route("/api/catalogos/tipos/<id>/toggle", methods=["POST"])
def api_cat_tipos_toggle(id):
    return _json_ok() if toggle_tipo_actividad(id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/catalogos/tipos/<id>", methods=["DELETE"])
def api_cat_tipos_eliminar(id):
    ok, msg = eliminar_tipo_actividad(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── APIs DE TIPOS DE EVIDENCIA ──────────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/evidencia", methods=["POST"])
def api_cat_ev_crear():
    return _json_ok() if crear_tipo_evidencia(request.get_json()) else _json_err("Error al crear tipo.")


@catalogos_bp.route("/api/catalogos/evidencia/<id>", methods=["PUT"])
def api_cat_ev_editar(id):
    return _json_ok() if actualizar_tipo_evidencia(id, request.get_json()) else _json_err("Error al actualizar.")


@catalogos_bp.route("/api/catalogos/evidencia/<id>/toggle", methods=["POST"])
def api_cat_ev_toggle(id):
    return _json_ok() if toggle_tipo_evidencia(id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/catalogos/evidencia/<id>", methods=["DELETE"])
def api_cat_ev_eliminar(id):
    ok, msg = eliminar_tipo_evidencia(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── APIs DE RECURSOS ────────────────────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/recursos", methods=["POST"])
def api_cat_rec_crear():
    return _json_ok() if crear_recurso(request.get_json()) else _json_err("Error al crear recurso.")


@catalogos_bp.route("/api/catalogos/recursos/<id>", methods=["PUT"])
def api_cat_rec_editar(id):
    return _json_ok() if actualizar_recurso(id, request.get_json()) else _json_err("Error al actualizar.")


@catalogos_bp.route("/api/catalogos/recursos/<id>/toggle", methods=["POST"])
def api_cat_rec_toggle(id):
    return _json_ok() if toggle_recurso(id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/catalogos/recursos/<id>", methods=["DELETE"])
def api_cat_rec_eliminar(id):
    ok, msg = eliminar_recurso(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── APIs DE SOLICITANTES ────────────────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/solicitantes", methods=["POST"])
def api_cat_solicitantes_crear():
    data = request.get_json() or {}
    if not (data.get("nombre") or "").strip():
        return _json_err("El nombre del solicitante es obligatorio.", 400)
    return _json_ok() if crear_solicitante(data) else _json_err("Error al crear solicitante. Verifica que no exista otro con el mismo nombre.")


@catalogos_bp.route("/api/catalogos/solicitantes/<id>", methods=["PUT"])
def api_cat_solicitantes_editar(id):
    data = request.get_json() or {}
    if not (data.get("nombre") or "").strip():
        return _json_err("El nombre del solicitante es obligatorio.", 400)
    return _json_ok() if actualizar_solicitante(id, data) else _json_err("Error al actualizar. Verifica que no exista otro con el mismo nombre.")


@catalogos_bp.route("/api/catalogos/solicitantes/<id>/toggle", methods=["POST"])
def api_cat_solicitantes_toggle(id):
    return _json_ok() if toggle_solicitante(id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/catalogos/solicitantes/<id>", methods=["DELETE"])
def api_cat_solicitantes_eliminar(id):
    ok, msg = eliminar_solicitante(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── APIs DE PROYECTOS ───────────────────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/proyectos", methods=["POST"])
def api_cat_proyectos_crear():
    data = request.get_json() or {}
    if not (data.get("nombre_proyecto") or "").strip():
        return _json_err("El nombre del proyecto es obligatorio.", 400)
    return _json_ok() if crear_proyecto(data) else _json_err("Error al crear proyecto.")


@catalogos_bp.route("/api/catalogos/proyectos/<id>", methods=["PUT"])
def api_cat_proyectos_editar(id):
    data = request.get_json() or {}
    if not (data.get("nombre_proyecto") or "").strip():
        return _json_err("El nombre del proyecto es obligatorio.", 400)
    return _json_ok() if actualizar_proyecto(id, data) else _json_err("Error al actualizar.")


@catalogos_bp.route("/api/catalogos/proyectos/<id>/toggle", methods=["POST"])
def api_cat_proyectos_toggle(id):
    return _json_ok() if toggle_proyecto(id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/catalogos/proyectos/<id>", methods=["DELETE"])
def api_cat_proyectos_eliminar(id):
    ok, msg = eliminar_proyecto(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── APIs DE CATEGORÍAS TIPO ACTIVIDAD ───────────────────────────────────────

@catalogos_bp.route("/api/categories", methods=["POST"])
def add_category():
    name = request.json.get("name")
    return (
        jsonify({"status": "success"})
        if agregar_categoria_db(name)
        else (jsonify({"status": "error"}), 500)
    )


@catalogos_bp.route("/api/categories/<id>", methods=["DELETE"])
def delete_category(id):
    return (
        jsonify({"status": "success"})
        if eliminar_categoria_db(id)
        else (jsonify({"status": "error"}), 500)
    )


# ── APIs DE DÍAS FESTIVOS ───────────────────────────────────────────────────

@catalogos_bp.route("/api/festivos")
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


@catalogos_bp.route("/api/festivos", methods=["POST"])
def api_festivo_crear():
    return _json_ok() if guardar_dia_festivo(request.get_json()) else _json_err("Error al guardar.")


@catalogos_bp.route("/api/festivos/<festivo_id>", methods=["PUT"])
def api_festivo_editar(festivo_id):
    return _json_ok() if actualizar_dia_festivo(festivo_id, request.get_json()) else _json_err("Error al actualizar.")


@catalogos_bp.route("/api/festivos/<festivo_id>/toggle", methods=["POST"])
def api_festivo_toggle(festivo_id):
    return _json_ok() if toggle_dia_festivo(festivo_id, request.get_json().get("activo", 1)) else _json_err("Error.")


@catalogos_bp.route("/api/festivos/<festivo_id>", methods=["DELETE"])
def api_festivo_eliminar(festivo_id):
    return _json_ok() if eliminar_dia_festivo(festivo_id) else _json_err("Error al eliminar.")




# ── APIs DE ENTREGABLES POR PROYECTO ────────────────────────────────────────

@catalogos_bp.route("/api/catalogos/entregables", methods=["POST"])
def api_cat_entregables_crear():
    data = request.get_json() or {}
    if not (data.get("nombre") or "").strip():
        return _json_err("El nombre del entregable es obligatorio.", 400)
    if not (data.get("id_proyecto") or "").strip():
        return _json_err("El ID del proyecto es obligatorio para asociar el entregable.", 400)
        
    return _json_ok() if crear_entregable(data) else _json_err("Error al crear el entregable.")


@catalogos_bp.route("/api/catalogos/entregables/<id>", methods=["PUT"])
def api_cat_entregables_editar(id):
    data = request.get_json() or {}
    if not (data.get("nombre") or "").strip():
        return _json_err("El nombre del entregable es obligatorio.", 400)
        
    return _json_ok() if actualizar_entregable(id, data) else _json_err("Error al actualizar el entregable.")


@catalogos_bp.route("/api/catalogos/entregables/<id>/toggle", methods=["POST"])
def api_cat_entregables_toggle(id):
    # Permite activar o desactivar de forma rápida (Estatus activo/inactivo de switch en UI)
    activo = request.get_json().get("activo", 1)
    return _json_ok() if toggle_entregable(id, activo) else _json_err("Error al cambiar el estatus del entregable.")


@catalogos_bp.route("/api/catalogos/entregables/<id>", methods=["DELETE"])
def api_cat_entregables_eliminar(id):
    # Ejecuta el borrado lógico directo (colocando ACTIVO = 0)
    ok, msg = eliminar_logico_entregable(id)
    return _json_ok() if ok else _json_err(msg, 400)


# ── HEALTHCHECK ─────────────────────────────────────────────────────────────

@catalogos_bp.route('/health')
def health():
    return {'status': 'ok'}, 200
