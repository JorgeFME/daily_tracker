from web_app.database import ejecutar_query, ejecutar_dml, _cached_query, invalidar_cache

def get_active_user_count():
    try:
        rows = ejecutar_query('SELECT COUNT(*) AS CNT FROM "USUARIOS" WHERE "ACTIVO"=1')
        return int(rows[0]["CNT"]) if rows else 0
    except Exception as e:
        print(f"[get_active_user_count] {e}")
        return 0


# ── Helpers internos de Catálogos ───────────────────────────────────────────

def _cat_toggle(tabla: str, id_val: str, activo: int) -> bool:
    return ejecutar_dml(f'UPDATE "{tabla}" SET "ACTIVO"=? WHERE "ID"=?', (activo, id_val))


def _cat_delete(tabla: str, id_val: str) -> bool:
    return ejecutar_dml(f'DELETE FROM "{tabla}" WHERE "ID"=?', (id_val,))


def _cat_in_use(tabla_ref: str, col_fk: str, id_val: str) -> bool:
    """Verifica si un registro de catálogo está referenciado en otra tabla."""
    rows = ejecutar_query(f'SELECT COUNT(*) AS N FROM "{tabla_ref}" WHERE "{col_fk}"=?', (id_val,))
    return bool(rows and int(rows[0]["N"]) > 0)


def _cat_nombre_existe(tabla: str, nombre: str, exclude_id: str | None = None) -> bool:
    nombre_normalizado = (nombre or "").strip()
    if not nombre_normalizado:
        return False
    sql = f'SELECT COUNT(*) AS "N" FROM "{tabla}" WHERE UPPER(TRIM("NOMBRE"))=UPPER(TRIM(?))'
    params = [nombre_normalizado]
    if exclude_id:
        sql += ' AND "ID"<>?'
        params.append(exclude_id)
    rows = ejecutar_query(sql, tuple(params))
    return bool(rows and int(rows[0]["N"]) > 0)


# ── CATÁLOGOS AUXILIARES  —  con caché ───────────────────────────────────────

def obtener_estatus_actividad():
    return _cached_query(
        "cat_estatus_actividad",
        'SELECT "ID","DESCRIPCION","COLOR_HEX" FROM "CAT_ESTATUS_ACTIVIDAD" WHERE "ACTIVO"=1 ORDER BY "ORDEN"'
    )


def obtener_tipos_evidencia():
    return _cached_query(
        "cat_tipos_evidencia",
        'SELECT "ID","DESCRIPCION" FROM "CAT_TIPO_EVIDENCIA" WHERE "ACTIVO"=1'
    )


def obtener_tipos_actividad():
    return _cached_query(
        "cat_tipos_actividad",
        'SELECT "ID","DESCRIPCION" FROM "CAT_TIPO_ACTIVIDAD" WHERE "ACTIVO"=1 ORDER BY "ORDEN"'
    )


def obtener_recursos():
    return _cached_query(
        "cat_recursos",
        'SELECT "ID","NOMBRE" FROM "CAT_RECURSOS" WHERE "ACTIVO"=1 ORDER BY "NOMBRE"'
    )


def obtener_solicitantes():
    return _cached_query(
        "cat_solicitantes",
        'SELECT "ID","NOMBRE","ACTIVO" FROM "CAT_SOLICITANTES" WHERE "ACTIVO"=1 ORDER BY "NOMBRE"'
    )


def obtener_entregables():
    return _cached_query(
        "cat_entregables",
        'SELECT "ID","NOMBRE", "ACTIVO" FROM "CAT_ENTREGABLES" WHERE "ACTIVO"=1 ORDER BY "NOMBRE"'
    )


# ── CAT_ESTATUS_ACTIVIDAD ─────────────────────────────────────────────────────

def obtener_estatus_actividad_todos():
    """Todos los estatus (activos e inactivos) para la vista de administración."""
    return ejecutar_query(
        'SELECT "ID","DESCRIPCION","COLOR_HEX","ACTIVO","ORDEN" '
        'FROM "CAT_ESTATUS_ACTIVIDAD" ORDER BY "ORDEN","DESCRIPCION"'
    )


def crear_estatus(datos: dict) -> bool:
    desc  = datos.get("descripcion", "")
    clave = desc.upper().replace(" ", "_")[:30]
    sql = """INSERT INTO "CAT_ESTATUS_ACTIVIDAD"
                ("ID","CLAVE","DESCRIPCION","COLOR_HEX","ORDEN","ACTIVO")
             VALUES (SYSUUID,?,?,?,
                (SELECT COALESCE(MAX("ORDEN"),0)+1 FROM "CAT_ESTATUS_ACTIVIDAD"),1)"""
    ok = ejecutar_dml(sql, (clave, desc, datos.get("color_hex") or "2563EB"))
    if ok: invalidar_cache("cat_estatus_actividad")
    return ok


def actualizar_estatus(id_val: str, datos: dict) -> bool:
    sql = 'UPDATE "CAT_ESTATUS_ACTIVIDAD" SET "DESCRIPCION"=?, "COLOR_HEX"=? WHERE "ID"=?'
    ok = ejecutar_dml(sql, (datos.get("descripcion"), datos.get("color_hex"), id_val))
    if ok: invalidar_cache("cat_estatus_actividad")
    return ok


def toggle_estatus(id_val: str, activo: int) -> bool:
    ok = _cat_toggle("CAT_ESTATUS_ACTIVIDAD", id_val, activo)
    if ok: invalidar_cache("cat_estatus_actividad")
    return ok


def eliminar_estatus(id_val: str):
    """Devuelve (ok: bool, mensaje: str)."""
    if _cat_in_use("ACTIVIDADES", "ID_ESTATUS", id_val):
        return False, "Este estatus está asignado a una o más actividades y no puede eliminarse."
    ok = _cat_delete("CAT_ESTATUS_ACTIVIDAD", id_val)
    if ok: invalidar_cache("cat_estatus_actividad")
    return ok, "" if ok else "Error al eliminar."


# ── CAT_TIPO_ACTIVIDAD ────────────────────────────────────────────────────────

def obtener_tipos_actividad_todos():
    return ejecutar_query(
        'SELECT "ID","CLAVE","DESCRIPCION","ORDEN","ACTIVO" '
        'FROM "CAT_TIPO_ACTIVIDAD" ORDER BY "ORDEN","DESCRIPCION"'
    )


def crear_tipo_actividad(datos: dict) -> bool:
    desc  = datos.get("descripcion", "")
    clave = desc.upper().replace(" ", "_")[:30]
    sql = """INSERT INTO "CAT_TIPO_ACTIVIDAD" ("ID","CLAVE","DESCRIPCION","ORDEN","ACTIVO")
             VALUES (SYSUUID,?,?,
                (SELECT COALESCE(MAX("ORDEN"),0)+1 FROM "CAT_TIPO_ACTIVIDAD"),1)"""
    ok = ejecutar_dml(sql, (clave, desc))
    if ok: invalidar_cache("cat_tipos_actividad")
    return ok


def actualizar_tipo_actividad(id_val: str, datos: dict) -> bool:
    desc  = datos.get("descripcion", "")
    clave = desc.upper().replace(" ", "_")[:30]
    sql = 'UPDATE "CAT_TIPO_ACTIVIDAD" SET "DESCRIPCION"=?, "CLAVE"=? WHERE "ID"=?'
    ok = ejecutar_dml(sql, (desc.upper(), clave, id_val))
    if ok: invalidar_cache("cat_tipos_actividad")
    return ok


def toggle_tipo_actividad(id_val: str, activo: int) -> bool:
    ok = _cat_toggle("CAT_TIPO_ACTIVIDAD", id_val, activo)
    if ok: invalidar_cache("cat_tipos_actividad")
    return ok


def eliminar_tipo_actividad(id_val: str):
    if _cat_in_use("REGISTRO_ACTIVIDADES", "ID_TIPO_ACT", id_val):
        return False, "Este tipo está usado en registros de horas y no puede eliminarse."
    if _cat_in_use("DETALLE_ACTIVIDAD", "ID_TIPO", id_val):
        return False, "Este tipo está usado en detalles de actividad y no puede eliminarse."
    ok = _cat_delete("CAT_TIPO_ACTIVIDAD", id_val)
    if ok: invalidar_cache("cat_tipos_actividad")
    return ok, "" if ok else "Error al eliminar."


# ── CAT_TIPO_EVIDENCIA ────────────────────────────────────────────────────────

def obtener_tipos_evidencia_todos():
    return ejecutar_query(
        'SELECT "ID","DESCRIPCION","ACTIVO" FROM "CAT_TIPO_EVIDENCIA" ORDER BY "DESCRIPCION"'
    )


def crear_tipo_evidencia(datos: dict) -> bool:
    desc  = datos.get("descripcion", "")
    clave = desc.upper().replace(" ", "_")[:30]
    sql = 'INSERT INTO "CAT_TIPO_EVIDENCIA" ("ID","CLAVE","DESCRIPCION","ACTIVO") VALUES (SYSUUID,?,?,1)'
    ok = ejecutar_dml(sql, (clave, desc))
    if ok: invalidar_cache("cat_tipos_evidencia")
    return ok


def actualizar_tipo_evidencia(id_val: str, datos: dict) -> bool:
    sql = 'UPDATE "CAT_TIPO_EVIDENCIA" SET "DESCRIPCION"=? WHERE "ID"=?'
    ok = ejecutar_dml(sql, (datos.get("descripcion"), id_val))
    if ok: invalidar_cache("cat_tipos_evidencia")
    return ok


def toggle_tipo_evidencia(id_val: str, activo: int) -> bool:
    ok = _cat_toggle("CAT_TIPO_EVIDENCIA", id_val, activo)
    if ok: invalidar_cache("cat_tipos_evidencia")
    return ok


def eliminar_tipo_evidencia(id_val: str):
    if _cat_in_use("EVIDENCIA_ACTIVIDAD", "ID_TIPO", id_val):
        return False, "Este tipo está usado en evidencias existentes y no puede eliminarse."
    ok = _cat_delete("CAT_TIPO_EVIDENCIA", id_val)
    if ok: invalidar_cache("cat_tipos_evidencia")
    return ok, "" if ok else "Error al eliminar."


# ── CAT_RECURSOS ──────────────────────────────────────────────────────────────

def obtener_recursos_todos():
    return ejecutar_query(
        'SELECT "ID","NOMBRE","ACTIVO" FROM "CAT_RECURSOS" ORDER BY "NOMBRE"'
    )


def crear_recurso(datos: dict) -> bool:
    sql = 'INSERT INTO "CAT_RECURSOS" ("ID","NOMBRE","ACTIVO") VALUES (SYSUUID,?,1)'
    ok = ejecutar_dml(sql, (datos.get("nombre"),))
    if ok: invalidar_cache("cat_recursos")
    return ok


def actualizar_recurso(id_val: str, datos: dict) -> bool:
    sql = 'UPDATE "CAT_RECURSOS" SET "NOMBRE"=? WHERE "ID"=?'
    ok = ejecutar_dml(sql, (datos.get("nombre"), id_val))
    if ok: invalidar_cache("cat_recursos")
    return ok


def toggle_recurso(id_val: str, activo: int) -> bool:
    ok = _cat_toggle("CAT_RECURSOS", id_val, activo)
    if ok: invalidar_cache("cat_recursos")
    return ok


def eliminar_recurso(id_val: str):
    if _cat_in_use("ACTIVIDAD_RECURSOS", "ID_RECURSO", id_val):
        return False, "Este recurso está asignado a actividades y no puede eliminarse."
    ok = _cat_delete("CAT_RECURSOS", id_val)
    if ok: invalidar_cache("cat_recursos")
    return ok, "" if ok else "Error al eliminar."


# ── CAT_SOLICITANTES ───────────────────────────────────────────────────────────

def obtener_solicitantes_todos():
    return ejecutar_query(
        'SELECT "ID","NOMBRE","ACTIVO" FROM "CAT_SOLICITANTES" ORDER BY "NOMBRE"'
    )


def _normalizar_nombre_solicitante(nombre: str | None) -> str:
    return (nombre or "").strip().upper()


def crear_solicitante(datos: dict) -> bool:
    nombre = _normalizar_nombre_solicitante(datos.get("nombre"))
    if not nombre or _cat_nombre_existe("CAT_SOLICITANTES", nombre):
        return False
    ok = ejecutar_dml('INSERT INTO "CAT_SOLICITANTES" ("ID","NOMBRE","ACTIVO") VALUES (SYSUUID,?,1)', (nombre,))
    if ok:
        invalidar_cache("cat_solicitantes")
    return ok


def actualizar_solicitante(id_val: str, datos: dict) -> bool:
    nombre_nuevo = _normalizar_nombre_solicitante(datos.get("nombre"))
    if not nombre_nuevo or _cat_nombre_existe("CAT_SOLICITANTES", nombre_nuevo, exclude_id=id_val):
        return False

    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute('SELECT "NOMBRE" FROM "CAT_SOLICITANTES" WHERE "ID"=?', (id_val,))
            row = cur.fetchone()
            if not row:
                return False

            nombre_actual = (row[0] or "").strip()
            cur.execute('UPDATE "CAT_SOLICITANTES" SET "NOMBRE"=? WHERE "ID"=?', (nombre_nuevo, id_val))
            if nombre_actual and nombre_actual != nombre_nuevo:
                cur.execute('UPDATE "ACTIVIDADES" SET "SOLICITANTE"=? WHERE "SOLICITANTE"=?', (nombre_nuevo, nombre_actual))
            conn.commit()
            invalidar_cache("cat_solicitantes")
            return True
        except Exception as e:
            print(f"[actualizar_solicitante] {e}")
            conn.rollback()
            return False
        finally:
            cur.close()


def toggle_solicitante(id_val: str, activo: int) -> bool:
    ok = _cat_toggle("CAT_SOLICITANTES", id_val, activo)
    if ok:
        invalidar_cache("cat_solicitantes")
    return ok


def eliminar_solicitante(id_val: str):
    rows = ejecutar_query('SELECT "NOMBRE" FROM "CAT_SOLICITANTES" WHERE "ID"=?', (id_val,))
    if not rows:
        return False, "El solicitante ya no existe."

    nombre = (rows[0].get("NOMBRE") or "").strip()
    if nombre:
        uso = ejecutar_query('SELECT COUNT(*) AS "N" FROM "ACTIVIDADES" WHERE "SOLICITANTE"=?', (nombre,))
        if uso and int(uso[0]["N"]) > 0:
            return False, "Este solicitante está asignado a una o más actividades y no puede eliminarse."

    ok = _cat_delete("CAT_SOLICITANTES", id_val)
    if ok:
        invalidar_cache("cat_solicitantes")
    return ok, "" if ok else "Error al eliminar."


# ── PROYECTOS ─────────────────────────────────────────────────────────────────

def obtener_proyectos_todos():
    return ejecutar_query(
        'SELECT "ID","NOMBRE_PROYECTO","DESCRIPCION","CLIENTE","ACTIVO" '
        'FROM "PROYECTOS" ORDER BY "NOMBRE_PROYECTO"'
    )


def crear_proyecto(datos: dict) -> bool:
    nombre = (datos.get("nombre_proyecto") or "").strip()
    descripcion = (datos.get("descripcion") or "").strip() or None
    cliente = (datos.get("cliente") or "").strip() or None
    if not nombre:
        return False

    sql = (
        'INSERT INTO "PROYECTOS" '
        '("ID","NOMBRE_PROYECTO","DESCRIPCION","CLIENTE","ACTIVO","CREADO_EN") '
        'VALUES (SYSUUID,?,?,?,1,CURRENT_TIMESTAMP)'
    )
    return ejecutar_dml(sql, (nombre, descripcion, cliente))


def actualizar_proyecto(id_val: str, datos: dict) -> bool:
    nombre = (datos.get("nombre_proyecto") or "").strip()
    descripcion = (datos.get("descripcion") or "").strip() or None
    cliente = (datos.get("cliente") or "").strip() or None
    if not nombre:
        return False

    sql = (
        'UPDATE "PROYECTOS" '
        'SET "NOMBRE_PROYECTO"=?, "DESCRIPCION"=?, "CLIENTE"=?, "ACTUALIZADO_EN"=CURRENT_TIMESTAMP '
        'WHERE "ID"=?'
    )
    return ejecutar_dml(sql, (nombre, descripcion, cliente, id_val))


def toggle_proyecto(id_val: str, activo: int) -> bool:
    return _cat_toggle("PROYECTOS", id_val, activo)


def eliminar_proyecto(id_val: str):
    if _cat_in_use("ACTIVIDADES", "ID_PROYECTO", id_val):
        return False, "Este proyecto está asignado a actividades y no puede eliminarse."
    if _cat_in_use("REGISTRO_ACTIVIDADES", "ID_PROYECTO", id_val):
        return False, "Este proyecto tiene registros de horas y no puede eliminarse."

    ok = _cat_delete("PROYECTOS", id_val)
    return ok, "" if ok else "Error al eliminar."


# ── DÍAS FESTIVOS ─────────────────────────────────────────────────────────────

def obtener_dias_festivos(anio: int = None):
    if anio:
        return ejecutar_query(
            'SELECT "ID","FECHA","NOMBRE","TIPO","APLICA_TODOS","ACTIVO" '
            'FROM "DIAS_FESTIVOS" WHERE YEAR("FECHA")=? ORDER BY "FECHA"',
            (anio,)
        )
    return ejecutar_query(
        'SELECT "ID","FECHA","NOMBRE","TIPO","APLICA_TODOS","ACTIVO" FROM "DIAS_FESTIVOS" ORDER BY "FECHA"'
    )


def obtener_dias_festivos_mes(anio: int, mes: int):
    return ejecutar_query(
        'SELECT "ID","FECHA","NOMBRE","TIPO","APLICA_TODOS","ACTIVO" '
        'FROM "DIAS_FESTIVOS" WHERE YEAR("FECHA")=? AND MONTH("FECHA")=? AND "ACTIVO"=1 ORDER BY "FECHA"',
        (anio, mes)
    )


def guardar_dia_festivo(datos: dict) -> bool:
    return ejecutar_dml(
        'INSERT INTO "DIAS_FESTIVOS" ("ID","FECHA","NOMBRE","TIPO","APLICA_TODOS","ACTIVO") VALUES (SYSUUID,?,?,?,?,1)',
        (datos.get("fecha"), datos.get("nombre"), datos.get("tipo", "OFICIAL"), 1 if datos.get("aplica_todos", True) else 0)
    )


def actualizar_dia_festivo(festivo_id: str, datos: dict) -> bool:
    return ejecutar_dml(
        'UPDATE "DIAS_FESTIVOS" SET "FECHA"=?, "NOMBRE"=?, "TIPO"=?, "APLICA_TODOS"=? WHERE "ID"=?',
        (datos.get("fecha"), datos.get("nombre"), datos.get("tipo", "OFICIAL"), 1 if datos.get("aplica_todos", True) else 0, festivo_id)
    )


def toggle_dia_festivo(festivo_id: str, activo: int) -> bool:
    return ejecutar_dml('UPDATE "DIAS_FESTIVOS" SET "ACTIVO"=? WHERE "ID"=?', (activo, festivo_id))


def eliminar_dia_festivo(festivo_id: str) -> bool:
    return ejecutar_dml('DELETE FROM "DIAS_FESTIVOS" WHERE "ID"=?', (festivo_id,))


# ── CATEGORÍAS TIPO ACTIVIDAD (VÍA AJAX) ────────────────────────────────────

def agregar_categoria_db(descripcion):
    clave = descripcion.upper().replace(' ', '_')[:30]
    sql = """INSERT INTO "CAT_TIPO_ACTIVIDAD" ("ID","CLAVE","DESCRIPCION","ORDEN","ACTIVO")
             VALUES (SYSUUID,?,?,(SELECT COALESCE(MAX("ORDEN"),0)+1 FROM "CAT_TIPO_ACTIVIDAD"),1)"""
    ok = ejecutar_dml(sql, (clave, descripcion.upper()))
    if ok:
        invalidar_cache("cat_tipos_actividad")
    return ok


def eliminar_categoria_db(id_cat):
    ok = ejecutar_dml('UPDATE "CAT_TIPO_ACTIVIDAD" SET "ACTIVO"=0 WHERE "ID"=?', (id_cat,))
    if ok:
        invalidar_cache("cat_tipos_actividad")
    return ok
