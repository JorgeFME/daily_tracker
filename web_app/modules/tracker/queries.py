from datetime import datetime, timedelta
from web_app.database import _pool, ejecutar_query, ejecutar_dml

def _obtener_estatus_id(cur, *descripciones):
    objetivos = [str(desc).strip().upper() for desc in descripciones if str(desc).strip()]
    if not objetivos:
        return None

    placeholders = ",".join("?" for _ in objetivos)
    cur.execute(
        f'SELECT "ID", TRIM(UPPER("DESCRIPCION")) AS "DESC" '
        f'FROM "CAT_ESTATUS_ACTIVIDAD" '
        f'WHERE "ACTIVO"=1 AND TRIM(UPPER("DESCRIPCION")) IN ({placeholders}) '
        f'ORDER BY "ORDEN"',
        tuple(objetivos),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    ids_por_desc = {str(row[1]).strip().upper(): row[0] for row in rows}
    for objetivo in objetivos:
        if objetivo in ids_por_desc:
            return ids_por_desc[objetivo]
    return rows[0][0]


def _crear_actividad_rapida_desde_registro(cur, datos):
    solicitante = (datos.get("quick_solicitante") or "").strip()
    friendly_name = (datos.get("quick_friendly_name") or "").strip() or None
    prioridad_raw = str(datos.get("quick_prioridad") or "").strip()
    recursos = list(dict.fromkeys(
        str(recurso).strip() for recurso in (datos.get("quick_recursos") or []) if str(recurso).strip()
    ))

    missing_fields = []
    if not solicitante:
        missing_fields.append("solicitante")
    if not prioridad_raw:
        missing_fields.append("prioridad")
    if not recursos:
        missing_fields.append("recursos utilizados")
    if missing_fields:
        raise ValueError(
            "Completa los datos requeridos para la Actividad Rápida: "
            + ", ".join(missing_fields)
            + "."
        )

    try:
        prioridad = int(prioridad_raw)
    except (TypeError, ValueError):
        raise ValueError("La prioridad seleccionada para la Actividad Rápida no es válida.")

    if prioridad not in (1, 2, 3):
        raise ValueError("La prioridad seleccionada para la Actividad Rápida no es válida.")

    proyecto_id = datos.get("project")
    usuario_id = datos.get("user")
    fecha_base = datos.get("date")
    nombre_actividad = (datos.get("activity_action") or "").strip()
    if nombre_actividad and not nombre_actividad.startswith("⚡"):
        nombre_actividad = f"⚡ {nombre_actividad}"
    descripcion = (datos.get("details") or "").strip() or None
    creado_por = usuario_id or datos.get("creado_por") or "SISTEMA"

    if not proyecto_id or not usuario_id or not fecha_base or not nombre_actividad:
        raise ValueError("No se pudo preparar la Actividad Rápida con la información capturada.")

    # Regla de negocio: toda Actividad Rapida se crea como tipo TAREA.
    datos["tipo"] = "TAREA"

    estatus_completado_id = _obtener_estatus_id(cur, "COMPLETADO")
    if not estatus_completado_id:
        raise ValueError('No se encontró el estatus activo "Completado" para la Actividad Rápida.')

    cur.execute(
        """
            INSERT INTO "ACTIVIDADES"
                ("ID","ID_PROYECTO","NOMBRE_ACTIVIDAD","DESCRIPCION",
                 "FECHA_SOLICITUD","SOLICITANTE","FRIENDLY_NAME","FECHA_INICIO","FECHA_FIN_REAL",
                 "ID_ESTATUS","PRIORIDAD","TIPO","CREADO_EN","CREADO_POR")
            VALUES (SYSUUID,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)
        """,
        (
            proyecto_id,
            nombre_actividad,
            descripcion,
            fecha_base,
            solicitante,
            friendly_name,
            fecha_base,
            fecha_base,
            estatus_completado_id,
            prioridad,
            "TAREA",
            creado_por,
        ),
    )

    cur.execute(
        """
            SELECT TOP 1 "ID"
            FROM "ACTIVIDADES"
            WHERE "ID_PROYECTO"=?
              AND "NOMBRE_ACTIVIDAD"=?
              AND COALESCE("SOLICITANTE", '') = COALESCE(?, '')
              AND "CREADO_POR"=?
            ORDER BY "CREADO_EN" DESC
        """,
        (proyecto_id, nombre_actividad, solicitante, creado_por),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError("No se pudo vincular la Actividad Rápida recién creada al registro.")

    actividad_id = row[0]

    cur.execute(
        'INSERT INTO "ACTIVIDAD_RESPONSABLES" ("ID_ACTIVIDAD","ID_USUARIO") VALUES (?,?)',
        (actividad_id, usuario_id),
    )
    for recurso_id in recursos:
        cur.execute(
            'INSERT INTO "ACTIVIDAD_RECURSOS" ("ID_ACTIVIDAD","ID_RECURSO") VALUES (?,?)',
            (actividad_id, recurso_id),
        )

    datos["actividad_id"] = actividad_id
    datos["actividad_rapida_creada"] = "1"
    return actividad_id


def _propagar_horas_a_padre(cur, actividad_id, datos):
    """
    Si la actividad hija tiene ID_ACTIVIDAD_PADRE:
      1. Valida que el padre no esté COMPLETADO/CANCELADO.
      2. Inserta un registro espejo en REGISTRO_ACTIVIDADES apuntando al padre.
      3. Recalcula AVANCE_PCT del padre como promedio de sus hijas.
    Ejecuta dentro de la misma transacción abierta por el cursor `cur`.
    """
    # 1. Obtener ID_ACTIVIDAD_PADRE de la hija
    cur.execute(
        'SELECT "ID_ACTIVIDAD_PADRE", "ID_PROYECTO" '
        'FROM "ACTIVIDADES" '
        'WHERE "ID"=?',
        (actividad_id,)
    )
    row = cur.fetchone()
    if not row:
        return  # actividad no encontrada, nada que propagar

    padre_id = row[0]
    if not padre_id:
        return  # no tiene padre, fin

    # 2. Validar estatus del padre
    cur.execute(
        'SELECT A."ID_ESTATUS", E."DESCRIPCION" '
        'FROM "ACTIVIDADES" A '
        'JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID" '
        'WHERE A."ID"=?',
        (padre_id,)
    )
    padre_row = cur.fetchone()
    if padre_row:
        estatus_padre = str(padre_row[1] or '').strip().upper()
        if estatus_padre in ('COMPLETADO', 'CANCELADO'):
            raise ValueError(
                f"No se pueden propagar horas: la actividad padre tiene estatus '{padre_row[1]}'. "
                "Actualiza el estatus del padre antes de registrar horas en sus actividades hijas."
            )

    # 3. Insertar registro espejo apuntando al padre (marcado como propagado)
    cur.execute(
        """
            INSERT INTO "REGISTRO_ACTIVIDADES"
                ("ID","FECHA","ID_USUARIO","ID_PROYECTO","ID_ACTIVIDAD","ID_TIPO_ACT","ACCION","HORAS","DETALLES","ES_PROPAGADO","CREADO_EN")
            VALUES (SYSUUID,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
        """,
        (
            datos.get('date'),
            datos.get('user'),
            datos.get('project'),
            padre_id,
            datos.get('tipo_act') or None,
            datos.get('activity_action'),
            datos.get('hours'),
            datos.get('details'),
        )
    )

    # 4. Recalcular AVANCE_PCT del padre como promedio de sus hijas
    cur.execute(
        'SELECT COALESCE(AVG(CAST("AVANCE_PCT" AS DECIMAL)), 0) '
        'FROM "ACTIVIDADES" '
        'WHERE "ID_ACTIVIDAD_PADRE"=?',
        (padre_id,)
    )
    avg_row = cur.fetchone()
    nuevo_avance = int(round(float(avg_row[0] or 0))) if avg_row else 0
    nuevo_avance = max(0, min(100, nuevo_avance))

    cur.execute(
        'UPDATE "ACTIVIDADES" '
        'SET "AVANCE_PCT"=?, "ACTUALIZADO_EN"=CURRENT_TIMESTAMP '
        'WHERE "ID"=?',
        (nuevo_avance, padre_id)
    )


def guardar_registro_actividad(datos):
    actividad_id = datos.get('actividad_id')
    crear_actividad_rapida = actividad_id == 'ad_hoc'
    nuevo_estatus_id = str(datos.get('nuevo_estatus_id') or '').strip() or None
    if crear_actividad_rapida or not actividad_id:
        actividad_id = None
        nuevo_estatus_id = None

    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            if crear_actividad_rapida:
                actividad_id = _crear_actividad_rapida_desde_registro(cur, datos)

            if actividad_id:
                cur.execute(
                    'SELECT A."ID_ESTATUS", E."DESCRIPCION" '
                    'FROM "ACTIVIDADES" A '
                    'JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID" '
                    'WHERE A."ID"=?',
                    (actividad_id,)
                )
                actividad_row = cur.fetchone()
                if not actividad_row:
                    raise ValueError('La actividad seleccionada ya no existe o no está disponible.')

                estatus_actual_desc = str(actividad_row[1] or '').strip().upper()
                if estatus_actual_desc in ("COMPLETADO", "CANCELADO"):
                    raise ValueError(
                        f"No se pueden registrar horas en una actividad con estatus '{actividad_row[1]}'."
                    )

            cur.execute(
                """
                    INSERT INTO "REGISTRO_ACTIVIDADES"
                        ("ID","FECHA","ID_USUARIO","ID_PROYECTO","ID_ACTIVIDAD","ID_TIPO_ACT","ACCION","HORAS","DETALLES","CREADO_EN")
                    VALUES (SYSUUID,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                """,
                (
                    datos.get('date'),
                    datos.get('user'),
                    datos.get('project'),
                    actividad_id,
                    datos.get('tipo_act') or None,
                    datos.get('activity_action'),
                    datos.get('hours'),
                    datos.get('details'),
                )
            )
            datos["actividad_id"] = actividad_id

            # ── Propagación automática al padre (si la actividad es hija) ──────
            if actividad_id:
                _propagar_horas_a_padre(cur, actividad_id, datos)

            estatus_objetivo_id = None
            estatus_objetivo_desc = None

            if actividad_id and nuevo_estatus_id:
                cur.execute(
                    'SELECT TRIM(UPPER("DESCRIPCION")) AS "DESC" '
                    'FROM "CAT_ESTATUS_ACTIVIDAD" '
                    'WHERE "ID"=? AND "ACTIVO"=1',
                    (nuevo_estatus_id,)
                )
                row_estatus = cur.fetchone()
                if not row_estatus:
                    raise ValueError('El estatus seleccionado no existe o está inactivo.')
                estatus_objetivo_id = nuevo_estatus_id
                estatus_objetivo_desc = str(row_estatus[0] or '').strip().upper()
            elif actividad_id and datos.get('finalizar_actividad') == '1':
                estatus_completado_id = _obtener_estatus_id(cur, 'COMPLETADO')
                if not estatus_completado_id:
                    raise ValueError('No se encontró el estatus activo "Completado" para finalizar la actividad.')
                estatus_objetivo_id = estatus_completado_id
                estatus_objetivo_desc = 'COMPLETADO'

            if actividad_id and estatus_objetivo_id:
                fecha_fin_real = datos.get('date') if estatus_objetivo_desc == 'COMPLETADO' else None

                cur.execute(
                    'UPDATE "ACTIVIDADES" SET "ID_ESTATUS"=?, "FECHA_FIN_REAL"=?, "ACTUALIZADO_EN"=CURRENT_TIMESTAMP WHERE "ID"=?',
                    (estatus_objetivo_id, fecha_fin_real, actividad_id)
                )

            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def get_horas_semanales(user_id, fecha_str):
    if not user_id or not fecha_str:
        return 0.0
    dt    = datetime.strptime(fecha_str, '%Y-%m-%d')
    lunes = dt - timedelta(days=dt.weekday())
    vier  = lunes + timedelta(days=4)
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                'SELECT COALESCE(SUM("HORAS"), 0) FROM "REGISTRO_ACTIVIDADES" '
                'WHERE "ID_USUARIO"=? AND "FECHA" BETWEEN ? AND ? '
                'AND COALESCE("ES_PROPAGADO", 0) != 1',
                (user_id, lunes.strftime('%Y-%m-%d'), vier.strftime('%Y-%m-%d'))
            )
            r = cur.fetchone()
            return float(r[0]) if r and r[0] else 0.0
        except Exception as e:
            print(f"[get_horas_semanales] {e}")
            return 0.0
        finally:
            cur.close()


def get_horas_diarias(user_id, fecha_str):
    if not user_id or not fecha_str:
        return 0.0
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                'SELECT COALESCE(SUM("HORAS"), 0) FROM "REGISTRO_ACTIVIDADES" '
                'WHERE "ID_USUARIO"=? AND "FECHA"=? '
                'AND COALESCE("ES_PROPAGADO", 0) != 1',
                (user_id, fecha_str)
            )
            r = cur.fetchone()
            return float(r[0]) if r and r[0] else 0.0
        except Exception as e:
            print(f"[get_horas_diarias] {e}")
            return 0.0
        finally:
            cur.close()


def get_horas_ausencia_dia(user_id, fecha_str):
    """Devuelve las horas de ausencia de un usuario en una fecha.
    Considera tanto ausencias personales como días festivos activos."""
    if not user_id or not fecha_str:
        return 0.0
    total = 0.0
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            # 1. Ausencias personales del usuario
            cur.execute(
                'SELECT COALESCE(SUM("HORAS_DIA"), 0) FROM "AUSENCIAS_USUARIO" '
                'WHERE "ID_USUARIO"=? AND ? BETWEEN "FECHA_INICIO" AND "FECHA_FIN"',
                (user_id, fecha_str)
            )
            r = cur.fetchone()
            total += float(r[0]) if r and r[0] else 0.0

            # 2. Días festivos activos que aplican a todos
            cur.execute(
                'SELECT COUNT(*) FROM "DIAS_FESTIVOS" '
                'WHERE "FECHA"=? AND "APLICA_TODOS"=1 AND "ACTIVO"=1',
                (fecha_str,)
            )
            r2 = cur.fetchone()
            if r2 and int(r2[0]) > 0:
                total = 8.0  # día festivo = día completo ausente
        except Exception as e:
            print(f"[get_horas_ausencia_dia] {e}")
        finally:
            cur.close()
    return min(total, 8.0)  # tope máximo de 8h


# ══════════════════════════════════════════════════════════════════════════════
#  GESTIÓN DE REGISTROS (CRUD)
# ══════════════════════════════════════════════════════════════════════════════

def _registros_where(alias='R', filtros=None):
    if filtros is None:
        filtros = {}
    params, where = [], []
    if filtros.get('user_id'):
        where.append(f'"{alias}"."ID_USUARIO" = ?')
        params.append(filtros['user_id'])
    if filtros.get('proyecto_id'):
        where.append(f'"{alias}"."ID_PROYECTO" = ?')
        params.append(filtros['proyecto_id'])
    if filtros.get('actividad_id'):
        where.append(f'"{alias}"."ID_ACTIVIDAD" = ?')
        params.append(filtros['actividad_id'])
    if filtros.get('fecha_ini'):
        where.append(f'"{alias}"."FECHA" >= ?')
        params.append(filtros['fecha_ini'])
    if filtros.get('fecha_fin'):
        where.append(f'"{alias}"."FECHA" <= ?')
        params.append(filtros['fecha_fin'])
    if filtros.get('tipo_id'):
        where.append(f'"{alias}"."ID_TIPO_ACT" = ?')
        params.append(filtros['tipo_id'])
    if filtros.get('ocultar_propagados'):
        where.append(f'COALESCE("{alias}"."ES_PROPAGADO", 0) != 1')
    return where, params


def obtener_registros(filtros=None):
    where, params = _registros_where('R', filtros)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    sql = f"""
        SELECT R."ID", R."FECHA", R."HORAS", R."ACCION", R."DETALLES",
               U."NOMBRE_COMPLETO" as "USUARIO", U."ID" as "ID_USUARIO",
               P."NOMBRE_PROYECTO", P."ID" as "ID_PROYECTO",
               A."NOMBRE_ACTIVIDAD", A."ID" as "ID_ACTIVIDAD",
               T."DESCRIPCION" as "TIPO", T."ID" as "ID_TIPO_ACT",
               COALESCE(R."ES_PROPAGADO", 0) as "ES_PROPAGADO",
               R."CREADO_EN"
        FROM "REGISTRO_ACTIVIDADES" R
        JOIN  "USUARIOS"  U ON R."ID_USUARIO"  = U."ID"
        JOIN  "PROYECTOS" P ON R."ID_PROYECTO" = P."ID"
        LEFT JOIN "ACTIVIDADES"        A ON R."ID_ACTIVIDAD" = A."ID"
        LEFT JOIN "CAT_TIPO_ACTIVIDAD" T ON R."ID_TIPO_ACT"  = T."ID"
        {where_sql}
        ORDER BY R."FECHA" DESC, R."CREADO_EN" DESC
    """
    return ejecutar_query(sql, tuple(params) if params else None)


def obtener_registro_por_id(registro_id):
    sql = """
        SELECT R."ID", R."FECHA", R."HORAS", R."ACCION", R."DETALLES",
               R."ID_USUARIO", R."ID_PROYECTO", R."ID_ACTIVIDAD", R."ID_TIPO_ACT"
        FROM "REGISTRO_ACTIVIDADES" R
        WHERE R."ID" = ?
    """
    rows = ejecutar_query(sql, (registro_id,))
    return rows[0] if rows else None


def actualizar_registro(registro_id, datos):
    sql = """
        UPDATE "REGISTRO_ACTIVIDADES"
        SET "FECHA"       = ?,
            "HORAS"       = ?,
            "ACCION"      = ?,
            "DETALLES"    = ?,
            "ID_ACTIVIDAD"= ?,
            "ID_TIPO_ACT" = ?
        WHERE "ID" = ?
    """
    return ejecutar_dml(sql, (
        datos.get('fecha'),
        datos.get('horas'),
        datos.get('accion'),
        datos.get('detalles') or None,
        datos.get('id_actividad') or None,
        datos.get('id_tipo_act') or None,
        registro_id,
    ))


def eliminar_registro(registro_id):
    """
    Elimina de forma segura un registro de actividades.
    Si el registro pertenece a una actividad hija, localiza y elimina 
    su registro espejo correspondiente en la actividad padre y actualiza el AVANCE_PCT.
    """
    if not registro_id:
        return False

    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            # 1. Recuperar los datos del registro que se va a eliminar
            cur.execute(
                """
                SELECT "FECHA", "ID_USUARIO", "ID_PROYECTO", "ID_ACTIVIDAD", 
                       "ACCION", "HORAS", "DETALLES", COALESCE("ES_PROPAGADO", 0)
                FROM "REGISTRO_ACTIVIDADES"
                WHERE "ID" = ?
                """,
                (registro_id,)
            )
            registro = cur.fetchone()
            if not registro:
                return False  # El registro ya no existe

            fecha, user_id, proyecto_id, actividad_id, accion, horas, detalles, es_propagado = registro

            # Evitar que borren directamente un espejo desde la lista general si estuviera visible
            if es_propagado == 1:
                raise ValueError("No se puede eliminar un registro propagado directamente. Elimina el registro de la actividad hija.")

            padre_id = None
            # 2. Si el registro tiene actividad, verificar si es una actividad hija para hallar al padre
            if actividad_id:
                cur.execute(
                    'SELECT "ID_ACTIVIDAD_PADRE" FROM "ACTIVIDADES" WHERE "ID" = ?',
                    (actividad_id,)
                )
                act_row = cur.fetchone()
                if act_row:
                    padre_id = act_row[0]

            # 3. Si existe un padre, buscar y eliminar su registro espejo correspondiente
            if padre_id:
                cur.execute(
                    """
                    DELETE FROM "REGISTRO_ACTIVIDADES"
                    WHERE "FECHA" = ?
                      AND "ID_USUARIO" = ?
                      AND "ID_PROYECTO" = ?
                      AND "ID_ACTIVIDAD" = ?
                      AND "ACCION" = ?
                      AND "HORAS" = ?
                      AND COALESCE("DETALLES", '') = COALESCE(?, '')
                      AND "ES_PROPAGADO" = 1
                    """,
                    (fecha, user_id, proyecto_id, padre_id, accion, horas, detalles)
                )

            # 4. Eliminar el registro original seleccionado por el usuario
            cur.execute('DELETE FROM "REGISTRO_ACTIVIDADES" WHERE "ID" = ?', (registro_id,))

            # 5. Si hubo una actividad padre involucrada, recalcular su AVANCE_PCT inmediatamente
            if padre_id:
                cur.execute(
                    """
                    SELECT COALESCE(AVG(CAST("AVANCE_PCT" AS DECIMAL)), 0)
                    FROM "ACTIVIDADES"
                    WHERE "ID_ACTIVIDAD_PADRE" = ?
                    """,
                    (padre_id,)
                )
                avg_row = cur.fetchone()
                nuevo_avance = int(round(float(avg_row[0] or 0))) if avg_row else 0
                nuevo_avance = max(0, min(100, nuevo_avance))

                cur.execute(
                    """
                    UPDATE "ACTIVIDADES"
                    SET "AVANCE_PCT" = ?, "ACTUALIZADO_EN" = CURRENT_TIMESTAMP
                    WHERE "ID" = ?
                    """,
                    (nuevo_avance, padre_id)
                )

            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def obtener_actividad_completa(actividad_id):
    """
    Recupera todas las columnas de una actividad específica.
    Mantiene estrictamente el esquema de base de datos exacto de SAP HANA.
    """
    sql = """
        SELECT ID, ID_PROYECTO, NOMBRE_ACTIVIDAD, DESCRIPCION, FECHA_SOLICITUD, 
               SOLICITANTE, FECHA_INICIO, FECHA_FIN_EST, FECHA_FIN_REAL, ID_ESTATUS, 
               AVANCE_PCT, PRIORIDAD, ASIGNADO_A, CREADO_EN, ACTUALIZADO_EN, 
               CREADO_POR, ACTUALIZADO_POR, ID_ACTIVIDAD_PADRE, TIPO, FRIENDLY_NAME, 
               DIAS_ACORDADOS, ID_ENTREGABLE
        FROM "ACTIVIDADES"
        WHERE ID = ?
    """
    resultado = ejecutar_query(sql, [actividad_id])
    return resultado[0] if resultado else None