from datetime import datetime, timedelta, date
from web_app.database import _pool, ejecutar_query, ejecutar_dml, _cached_query, invalidar_cache

# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES DE FECHA
# ══════════════════════════════════════════════════════════════════════════════

def _get_date_range(period, fecha_ref_str=None):
    ref = datetime.strptime(fecha_ref_str, '%Y-%m-%d').date() if fecha_ref_str else datetime.today().date()
    if period == 'day':
        return ref, ref
    elif period == 'week':
        lunes = ref - timedelta(days=ref.weekday())
        return lunes, lunes + timedelta(days=6)
    elif period == 'month':
        desde = ref.replace(day=1)
        hasta = (desde + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return desde, hasta
    elif period == 'year':
        return ref.replace(month=1, day=1), ref.replace(month=12, day=31)
    return ref.replace(day=1), ref


# ══════════════════════════════════════════════════════════════════════════════
#  KPIs Y GRÁFICAS DEL DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def obtener_datos_grafica_proyectos(user_id=None, period='month', fecha_ref=None):
    fd, fh = _get_date_range(period, fecha_ref)
    params = [fd.strftime('%Y-%m-%d'), fh.strftime('%Y-%m-%d')]
    uf = 'AND R."ID_USUARIO"=?' if user_id else ''
    if user_id:
        params.append(user_id)
    sql = f"""SELECT P."NOMBRE_PROYECTO", SUM(R."HORAS") as "TOTAL"
              FROM "REGISTRO_ACTIVIDADES" R JOIN "PROYECTOS" P ON R."ID_PROYECTO"=P."ID"
              WHERE R."FECHA" BETWEEN ? AND ? {uf}
              GROUP BY P."NOMBRE_PROYECTO" ORDER BY "TOTAL" DESC"""
    return ejecutar_query(sql, tuple(params))


def obtener_registros_recientes_filtrados(user_id=None, period='month', fecha_ref=None, limite=10):
    fd, fh = _get_date_range(period, fecha_ref)
    params = [fd.strftime('%Y-%m-%d'), fh.strftime('%Y-%m-%d')]
    uf = 'AND R."ID_USUARIO"=?' if user_id else ''
    if user_id:
        params.append(user_id)
    sql = f"""SELECT TOP {limite} R."FECHA", U."NOMBRE_COMPLETO" as "USUARIO",
               P."NOMBRE_PROYECTO", A."DESCRIPCION" as "TIPO", R."ACCION", R."HORAS", R."ID"
              FROM "REGISTRO_ACTIVIDADES" R
              JOIN "PROYECTOS" P ON R."ID_PROYECTO"=P."ID"
              LEFT JOIN "CAT_TIPO_ACTIVIDAD" A ON R."ID_TIPO_ACT"=A."ID"
              JOIN "USUARIOS" U ON R."ID_USUARIO"=U."ID"
              WHERE R."FECHA" BETWEEN ? AND ? {uf}
              ORDER BY R."FECHA" DESC, R."ID" DESC"""
    return ejecutar_query(sql, tuple(params))


def get_horas_ausencia_periodo(user_id, start_date, end_date):
    """Devuelve horas de ausencia en un periodo (inclusive)."""
    from web_app.modules.catalogos.queries import get_active_user_count
    if not start_date or not end_date:
        return 0.0

    total = 0.0
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            # Ausencias personales (rango solapado)
            query = (
                'SELECT "ID_USUARIO", "FECHA_INICIO", "FECHA_FIN", "HORAS_DIA" '
                'FROM "AUSENCIAS_USUARIO" '
                'WHERE "FECHA_FIN" >= ? AND "FECHA_INICIO" <= ?'
            )
            params = [start_date, end_date]
            if user_id:
                query += ' AND "ID_USUARIO" = ?'
                params.append(user_id)

            cur.execute(query, params)
            rows = cur.fetchall()

            for row in rows:
                _user_id, fecha_inicio, fecha_fin, horas_dia = row

                # Normaliza a date
                if isinstance(fecha_inicio, datetime):
                    fecha_inicio = fecha_inicio.date()
                if isinstance(fecha_fin, datetime):
                    fecha_fin = fecha_fin.date()

                if isinstance(fecha_inicio, str):
                    fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                if isinstance(fecha_fin, str):
                    fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()

                inicio = max(fecha_inicio, datetime.strptime(start_date, '%Y-%m-%d').date())
                fin = min(fecha_fin, datetime.strptime(end_date, '%Y-%m-%d').date())
                dias = (fin - inicio).days + 1
                if dias > 0:
                    total += float(horas_dia or 0) * dias

            # Festivos globales
            cur.execute(
                'SELECT COUNT(*) FROM "DIAS_FESTIVOS" '
                'WHERE "FECHA" BETWEEN ? AND ? AND "APLICA_TODOS"=1 AND "ACTIVO"=1',
                (start_date, end_date)
            )
            festivo_row = cur.fetchone()
            festivos = int(festivo_row[0]) if festivo_row and festivo_row[0] is not None else 0
            if festivos:
                if user_id:
                    total += 8.0 * festivos
                else:
                    usuarios_activos = get_active_user_count()
                    total += 8.0 * festivos * usuarios_activos

        except Exception as e:
            print(f"[get_horas_ausencia_periodo] {e}")
        finally:
            cur.close()

    return float(total)


# ══════════════════════════════════════════════════════════════════════════════
#  ACTIVIDADES
# ══════════════════════════════════════════════════════════════════════════════

def _filtros_actividades_sql(
    proyecto_id=None,
    estatus_id=None,
    tipo=None,
    usuario_id=None,
    solicitante=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
    solo_activas=False,
):
    filtros, params = [], []

    if proyecto_id:
        filtros.append('A."ID_PROYECTO"=?')
        params.append(proyecto_id)
    if estatus_id:
        filtros.append('A."ID_ESTATUS"=?')
        params.append(estatus_id)
    if tipo:
        filtros.append('UPPER(COALESCE(A."TIPO", \'\')) = ?')
        params.append(str(tipo).strip().upper())
    if usuario_id:
        filtros.append('EXISTS (SELECT 1 FROM "ACTIVIDAD_RESPONSABLES" AR WHERE AR."ID_ACTIVIDAD"=A."ID" AND AR."ID_USUARIO"=?)')
        params.append(usuario_id)
    if solicitante:
        filtros.append('UPPER(COALESCE(A."SOLICITANTE", \'\')) = ?')
        params.append(str(solicitante).strip().upper())
    if fecha_desde:
        filtros.append('A."FECHA_SOLICITUD" >= ?')
        params.append(fecha_desde)
    if fecha_hasta:
        filtros.append('A."FECHA_SOLICITUD" <= ?')
        params.append(fecha_hasta)
    if q:
        filtros.append('(UPPER(A."NOMBRE_ACTIVIDAD") LIKE ? OR UPPER(COALESCE(A."DESCRIPCION", \'\')) LIKE ?)')
        q_norm = f"%{q.strip().upper()}%"
        params.extend([q_norm, q_norm])
    if solo_activas:
        filtros.append('UPPER(E."DESCRIPCION") NOT IN (\'COMPLETADO\', \'CANCELADO\', \'ESPERANDO APROBACIÓN\')')

    where = ('WHERE ' + ' AND '.join(filtros)) if filtros else ''
    return where, params


def obtener_actividades(
    proyecto_id=None,
    estatus_id=None,
    tipo=None,
    usuario_id=None,
    solicitante=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
    sort_by='prioridad',
    page=None,
    page_size=None,
    solo_activas=False,
):
    where, params = _filtros_actividades_sql(
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

    sort_map = {
        'prioridad': 'A."PRIORIDAD" ASC, A."CREADO_EN" DESC',
        'recientes': 'A."CREADO_EN" DESC',
        'nombre': 'A."NOMBRE_ACTIVIDAD" ASC',
        'horas': '"HORAS_INVERTIDAS" DESC, A."CREADO_EN" DESC',
    }
    order_by = sort_map.get(sort_by, sort_map['prioridad'])

    pagination_sql = ''
    if page is not None and page_size is not None:
        safe_page = max(int(page), 1)
        safe_page_size = max(min(int(page_size), 100), 1)
        offset = (safe_page - 1) * safe_page_size
        pagination_sql = f' LIMIT {safe_page_size} OFFSET {offset}'

    sql = f"""{_base_select_actividades()}
              {where}
              ORDER BY {order_by}{pagination_sql}"""
    return ejecutar_query(sql, tuple(params) if params else None)


def contar_actividades(
    proyecto_id=None,
    estatus_id=None,
    tipo=None,
    usuario_id=None,
    solicitante=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
    solo_activas=False,
):
    where, params = _filtros_actividades_sql(
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
    sql = f"""
        SELECT COUNT(*) AS "TOTAL"
        FROM "ACTIVIDADES" A
        JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID"
        {where}
    """
    rows = ejecutar_query(sql, tuple(params) if params else None)
    return int(rows[0]["TOTAL"]) if rows else 0


def obtener_catalogo_actividades(proyecto_id=None):
    sql = """
        SELECT A."ID", A."NOMBRE_ACTIVIDAD", A."ID_PROYECTO", P."NOMBRE_PROYECTO"
        FROM "ACTIVIDADES" A
        JOIN "PROYECTOS" P ON A."ID_PROYECTO" = P."ID"
    """
    params = []
    if proyecto_id:
        sql += ' WHERE A."ID_PROYECTO" = ?'
        params.append(proyecto_id)
    sql += ' ORDER BY A."NOMBRE_ACTIVIDAD" ASC, P."NOMBRE_PROYECTO" ASC'
    return ejecutar_query(sql, tuple(params) if params else None)


def _base_select_actividades():
    return '''SELECT A."ID", A."NOMBRE_ACTIVIDAD", A."FRIENDLY_NAME", A."DESCRIPCION",
                     A."FECHA_SOLICITUD", A."SOLICITANTE",
                     A."FECHA_INICIO", A."FECHA_FIN_EST", A."FECHA_FIN_REAL",
                     A."DIAS_ACORDADOS", A."AVANCE_PCT",
                     A."PRIORIDAD", A."CREADO_EN",
                     A."ID_ACTIVIDAD_PADRE",
                     PADRE."NOMBRE_ACTIVIDAD" AS "NOMBRE_ACTIVIDAD_PADRE",
                     (SELECT COUNT(*) FROM "ACTIVIDADES" H
                      WHERE H."ID_ACTIVIDAD_PADRE" = A."ID") AS "NUM_HIJAS",
                     P."NOMBRE_PROYECTO",
                     E."DESCRIPCION" as "ESTATUS", E."COLOR_HEX" as "ESTATUS_COLOR",
                     COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                                WHERE R."ID_ACTIVIDAD"=A."ID"), 0) as "HORAS_INVERTIDAS",
                     (SELECT STRING_AGG(U2."NOMBRE_COMPLETO", ', ')
                      FROM "ACTIVIDAD_RESPONSABLES" AR
                      JOIN "USUARIOS" U2 ON AR."ID_USUARIO"=U2."ID"
                      WHERE AR."ID_ACTIVIDAD"=A."ID") as "RESPONSABLES",
                     (SELECT STRING_AGG(R2."NOMBRE", ', ')
                      FROM "ACTIVIDAD_RECURSOS" ARR
                      JOIN "CAT_RECURSOS" R2 ON ARR."ID_RECURSO"=R2."ID"
                      WHERE ARR."ID_ACTIVIDAD"=A."ID") as "RECURSOS"
              FROM "ACTIVIDADES" A
              JOIN "PROYECTOS" P ON A."ID_PROYECTO"=P."ID"
              JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID"
              LEFT JOIN "ACTIVIDADES" PADRE ON A."ID_ACTIVIDAD_PADRE" = PADRE."ID"'''


def obtener_actividades_default_mes(
    mes_inicio,
    fecha_hasta,
    sort_by='prioridad',
    page=None,
    page_size=None,
):
    sort_map = {
        'prioridad': 'A."PRIORIDAD" ASC, A."CREADO_EN" DESC',
        'recientes': 'A."CREADO_EN" DESC',
        'nombre': 'A."NOMBRE_ACTIVIDAD" ASC',
        'horas': '"HORAS_INVERTIDAS" DESC, A."CREADO_EN" DESC',
    }
    order_by = sort_map.get(sort_by, sort_map['prioridad'])

    pagination_sql = ''
    if page is not None and page_size is not None:
        safe_page = max(int(page), 1)
        safe_page_size = max(min(int(page_size), 100), 1)
        offset = (safe_page - 1) * safe_page_size
        pagination_sql = f' LIMIT {safe_page_size} OFFSET {offset}'

    sql = f'''{_base_select_actividades()}
              WHERE (
                    A."FECHA_SOLICITUD" BETWEEN ? AND ?
              ) OR (
                    (A."FECHA_SOLICITUD" < ? OR A."FECHA_SOLICITUD" IS NULL)
                    AND UPPER(E."DESCRIPCION") NOT IN ('COMPLETADO', 'CANCELADO', 'ESPERANDO APROBACIÓN')
              )
              ORDER BY CASE
                  WHEN A."FECHA_SOLICITUD" BETWEEN ? AND ? THEN 0
                  ELSE 1
              END ASC,
              {order_by}{pagination_sql}'''
    params = (mes_inicio, fecha_hasta, mes_inicio, mes_inicio, fecha_hasta)
    return ejecutar_query(sql, params)


def contar_actividades_default_mes(mes_inicio, fecha_hasta):
    sql = '''SELECT COUNT(*) AS "TOTAL"
             FROM "ACTIVIDADES" A
             JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID"
             WHERE (
                   A."FECHA_SOLICITUD" BETWEEN ? AND ?
             ) OR (
                   (A."FECHA_SOLICITUD" < ? OR A."FECHA_SOLICITUD" IS NULL)
                   AND UPPER(E."DESCRIPCION") NOT IN ('COMPLETADO', 'CANCELADO', 'ESPERANDO APROBACIÓN')
             )'''
    rows = ejecutar_query(sql, (mes_inicio, fecha_hasta, mes_inicio))
    return int(rows[0]["TOTAL"]) if rows else 0


def obtener_actividad_por_id(actividad_id):
    sql = """SELECT A."ID", A."NOMBRE_ACTIVIDAD", A."FRIENDLY_NAME", A."DESCRIPCION",
                    A."FECHA_SOLICITUD", A."SOLICITANTE",
                    A."FECHA_INICIO", A."FECHA_FIN_EST", A."FECHA_FIN_REAL",
                    A."DIAS_ACORDADOS", A."AVANCE_PCT",
                    A."PRIORIDAD", A."TIPO",
                    A."ID_PROYECTO", A."ID_ESTATUS",
                    A."ID_ACTIVIDAD_PADRE",
                    A."ID_ENTREGABLE",
                    A."CREADO_EN", A."ACTUALIZADO_EN",
                    P."NOMBRE_PROYECTO",
                    E."DESCRIPCION" as "ESTATUS", E."COLOR_HEX" as "ESTATUS_COLOR",
                    COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                                WHERE R."ID_ACTIVIDAD"=A."ID"), 0) as "HORAS_INVERTIDAS",
                    (SELECT STRING_AGG(U2."NOMBRE_COMPLETO", ', ')
                     FROM "ACTIVIDAD_RESPONSABLES" AR
                     JOIN "USUARIOS" U2 ON AR."ID_USUARIO"=U2."ID"
                     WHERE AR."ID_ACTIVIDAD"=A."ID") as "RESPONSABLES",
                    (SELECT STRING_AGG(R2."NOMBRE", ', ')
                     FROM "ACTIVIDAD_RECURSOS" ARR
                     JOIN "CAT_RECURSOS" R2 ON ARR."ID_RECURSO"=R2."ID"
                     WHERE ARR."ID_ACTIVIDAD"=A."ID") as "RECURSOS"
             FROM "ACTIVIDADES" A
             JOIN "PROYECTOS" P ON A."ID_PROYECTO"=P."ID"
             JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID"
             WHERE A."ID"=?"""
    rows = ejecutar_query(sql, (actividad_id,))
    return rows[0] if rows else None


def obtener_actividades_hijas(actividad_id):
    sql = """SELECT A."ID", A."NOMBRE_ACTIVIDAD",
                    E."DESCRIPCION" as "ESTATUS", E."COLOR_HEX" as "ESTATUS_COLOR",
                    P."NOMBRE_PROYECTO",
                    COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                                WHERE R."ID_ACTIVIDAD"=A."ID"), 0) as "HORAS_INVERTIDAS",
                    A."CREADO_EN"
             FROM "ACTIVIDADES" A
             JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID"
             JOIN "PROYECTOS" P ON A."ID_PROYECTO"=P."ID"
             WHERE A."ID_ACTIVIDAD_PADRE"=?
             ORDER BY A."CREADO_EN" DESC"""
    return ejecutar_query(sql, (actividad_id,))


def obtener_actividad_nombre(actividad_id):
    sql = """SELECT A."ID", A."NOMBRE_ACTIVIDAD", P."NOMBRE_PROYECTO",
                    E."DESCRIPCION" as "ESTATUS", E."COLOR_HEX" as "ESTATUS_COLOR"
             FROM "ACTIVIDADES" A
             JOIN "PROYECTOS" P ON A."ID_PROYECTO"=P."ID"
             JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID"
             WHERE A."ID"=?"""
    rows = ejecutar_query(sql, (actividad_id,))
    return rows[0] if rows else None


def crear_actividad(datos):
    sql = """INSERT INTO "ACTIVIDADES"
                 ("ID","ID_PROYECTO","NOMBRE_ACTIVIDAD","DESCRIPCION",
               "FECHA_SOLICITUD","SOLICITANTE","FECHA_INICIO","FECHA_FIN_EST",
               "FECHA_FIN_REAL","AVANCE_PCT","FRIENDLY_NAME",
               "DIAS_ACORDADOS","ID_ESTATUS","PRIORIDAD","TIPO","ID_ACTIVIDAD_PADRE",
               "CREADO_POR", "ID_ENTREGABLE", "CREADO_EN")
           VALUES (SYSUUID,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"""
    return ejecutar_dml(sql, (
        datos.get('id_proyecto'), datos.get('nombre_actividad'),
        datos.get('descripcion') or None,
        datos.get('fecha_solicitud') or None, datos.get('solicitante') or None,
        datos.get('fecha_inicio') or None, datos.get('fecha_fin_est') or None,
        datos.get('fecha_fin_real') or None,
        int(datos.get('avance_pct') or 0),
        datos.get('friendly_name') or None,
        int(datos['dias_acordados']) if datos.get('dias_acordados') else None,
        datos.get('id_estatus'), int(datos.get('prioridad', 2)),
        datos.get('tipo'),
        datos.get('id_actividad_padre') or None,
        datos.get('creado_por', 'SISTEMA'),
        datos.get('id_entregable') or None
    ))


def actualizar_actividad(actividad_id, datos):
    sql = """UPDATE "ACTIVIDADES" SET
                 "ID_PROYECTO"=?,
                 "NOMBRE_ACTIVIDAD"=?, "FRIENDLY_NAME"=?, "DESCRIPCION"=?,
                 "FECHA_SOLICITUD"=?, "SOLICITANTE"=?,
                 "FECHA_INICIO"=?, "FECHA_FIN_EST"=?, "FECHA_FIN_REAL"=?,
                 "DIAS_ACORDADOS"=?, "AVANCE_PCT"=?,
                 "ID_ESTATUS"=?, "PRIORIDAD"=?, "TIPO"=?,
                 "ID_ACTIVIDAD_PADRE"=?,
                 "ID_ENTREGABLE"=?,
                 "ACTUALIZADO_EN"=CURRENT_TIMESTAMP, "ACTUALIZADO_POR"=?
             WHERE "ID"=?"""

    return ejecutar_dml(sql, (
        datos.get('id_proyecto'),
        datos.get('nombre_actividad'),
        datos.get('friendly_name') or None,
        datos.get('descripcion') or None,
        datos.get('fecha_solicitud') or None,
        datos.get('solicitante') or None,
        datos.get('fecha_inicio') or None,
        datos.get('fecha_fin_est') or None,
        datos.get('fecha_fin_real') or None,
        int(datos['dias_acordados']) if datos.get('dias_acordados') else None,
        int(datos.get('avance_pct') or 0),
        datos.get('id_estatus'),
        int(datos.get('prioridad', 2)),
        datos.get('tipo'),
        datos.get('id_actividad_padre') or None,
        datos.get('id_entregable') or None,
        datos.get('actualizado_por', 'SISTEMA'),
        actividad_id,
    ))


def _filtros_plan_de_trabajo_sql(
    proyecto_id=None,
    estatus_id=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
    friendly_name_q=None,
    solo_retraso=False,
):
    filtros = [
        'UPPER(COALESCE(A."NOMBRE_ACTIVIDAD", \'\')) NOT LIKE ?'
    ]
    params = []
    params.append('⚡ %')

    if proyecto_id:
        filtros.append('A."ID_PROYECTO" = ?')
        params.append(proyecto_id)
    if estatus_id:
        filtros.append('A."ID_ESTATUS" = ?')
        params.append(estatus_id)
    if fecha_desde:
        filtros.append('A."FECHA_INICIO" >= ?')
        params.append(fecha_desde)
    if fecha_hasta:
        filtros.append('COALESCE(A."FECHA_FIN_EST", A."FECHA_FIN_REAL") <= ?')
        params.append(fecha_hasta)
    if q:
        q_norm = f"%{q.strip().upper()}%"
        filtros.append('''(
            UPPER(COALESCE(A."NOMBRE_ACTIVIDAD", '')) LIKE ?
            OR UPPER(COALESCE(A."FRIENDLY_NAME", '')) LIKE ?
        )''')
        params.extend([q_norm, q_norm])
    if friendly_name_q:
        filtros.append('UPPER(COALESCE(A."FRIENDLY_NAME", \'\')) LIKE ?')
        params.append(f"%{friendly_name_q.strip().upper()}%")
    if solo_retraso:
        filtros.append('''(
            A."DIAS_ACORDADOS" IS NOT NULL
            AND A."FECHA_INICIO" IS NOT NULL
            AND DAYS_BETWEEN(A."FECHA_INICIO", CURRENT_DATE) > A."DIAS_ACORDADOS"
            AND UPPER(E."DESCRIPCION") NOT IN ('COMPLETADO', 'CANCELADO', 'ESPERANDO APROBACIÓN')
        )''')

    return 'WHERE ' + ' AND '.join(filtros), params


def obtener_plan_de_trabajo(
    proyecto_id=None,
    estatus_id=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
    friendly_name_q=None,
    solo_retraso=False,
    sort_by='recientes',
    page=1,
    page_size=50,
):
    where, params = _filtros_plan_de_trabajo_sql(
        proyecto_id=proyecto_id,
        estatus_id=estatus_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        friendly_name_q=friendly_name_q,
        solo_retraso=solo_retraso,
    )

    sort_map = {
        'retraso': '"EN_RETRASO" DESC, "AVANCE_PCT" ASC, A."PRIORIDAD" ASC, A."CREADO_EN" DESC',
        'avance': '"AVANCE_PCT" ASC, A."PRIORIDAD" ASC, A."CREADO_EN" DESC',
        'recientes': 'A."CREADO_EN" DESC',
        'nombre': 'A."NOMBRE_ACTIVIDAD" ASC',
    }
    order_by = sort_map.get(sort_by, sort_map['recientes'])

    safe_page = max(int(page), 1)
    safe_page_size = max(min(int(page_size), 100), 1)
    offset = (safe_page - 1) * safe_page_size

    sql = f"""
        SELECT
            A."ID",
            A."NOMBRE_ACTIVIDAD",
            A."FRIENDLY_NAME",
            P."ID" AS "ID_PROYECTO",
            P."NOMBRE_PROYECTO",
            A."ID_ESTATUS",
            E."DESCRIPCION" AS "ESTATUS",
            E."COLOR_HEX" AS "ESTATUS_COLOR",
            A."DIAS_ACORDADOS",
            A."FECHA_INICIO",
            A."FECHA_FIN_EST",
            A."FECHA_FIN_REAL",
            A."AVANCE_PCT",
            A."PRIORIDAD",
            A."CREADO_EN",
            CASE
                WHEN A."DIAS_ACORDADOS" IS NOT NULL
                     AND A."FECHA_INICIO" IS NOT NULL
                     AND DAYS_BETWEEN(A."FECHA_INICIO", CURRENT_DATE) > A."DIAS_ACORDADOS"
                     AND UPPER(E."DESCRIPCION") NOT IN ('COMPLETADO', 'CANCELADO', 'ESPERANDO APROBACIÓN')
                THEN 1
                ELSE 0
            END AS "EN_RETRASO",
            COALESCE((
                SELECT STRING_AGG(U2."NOMBRE_COMPLETO", ', ')
                FROM "ACTIVIDAD_RESPONSABLES" AR
                JOIN "USUARIOS" U2 ON AR."ID_USUARIO" = U2."ID"
                WHERE AR."ID_ACTIVIDAD" = A."ID"
            ), 'Sin asignar') AS "RESPONSABLES"
        FROM "ACTIVIDADES" A
        JOIN "PROYECTOS" P ON A."ID_PROYECTO" = P."ID"
        JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS" = E."ID"
        {where}
        ORDER BY {order_by}
        LIMIT {safe_page_size} OFFSET {offset}
    """
    return ejecutar_query(sql, tuple(params) if params else None)


def contar_plan_de_trabajo(
    proyecto_id=None,
    estatus_id=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
    friendly_name_q=None,
    solo_retraso=False,
):
    where, params = _filtros_plan_de_trabajo_sql(
        proyecto_id=proyecto_id,
        estatus_id=estatus_id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        q=q,
        friendly_name_q=friendly_name_q,
        solo_retraso=solo_retraso,
    )
    sql = f"""
        SELECT COUNT(*) AS "TOTAL"
        FROM "ACTIVIDADES" A
        JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS" = E."ID"
        {where}
    """
    rows = ejecutar_query(sql, tuple(params) if params else None)
    return int(rows[0]["TOTAL"]) if rows else 0


def guardar_responsables_actividad(actividad_id, ids_usuarios):
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute('DELETE FROM "ACTIVIDAD_RESPONSABLES" WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            for uid in ids_usuarios:
                if uid:
                    cur.execute(
                        'INSERT INTO "ACTIVIDAD_RESPONSABLES" ("ID_ACTIVIDAD","ID_USUARIO") VALUES (?,?)',
                        (actividad_id, uid)
                    )
            conn.commit()
            return True
        except Exception as e:
            print(f"[guardar_responsables] {e}")
            conn.rollback()
            return False
        finally:
            cur.close()


def eliminar_actividad(actividad_id):
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute('UPDATE "ACTIVIDADES" SET "ID_ACTIVIDAD_PADRE"=NULL WHERE "ID_ACTIVIDAD_PADRE"=?', (actividad_id,))
            cur.execute('DELETE FROM "ACTIVIDAD_RESPONSABLES" WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            cur.execute('DELETE FROM "ACTIVIDAD_RECURSOS"     WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            cur.execute('DELETE FROM "EVIDENCIA_ACTIVIDAD"    WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            cur.execute('DELETE FROM "DETALLE_ACTIVIDAD"      WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            cur.execute('UPDATE "REGISTRO_ACTIVIDADES" SET "ID_ACTIVIDAD"=NULL WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            cur.execute('DELETE FROM "ACTIVIDADES" WHERE "ID"=?', (actividad_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"[eliminar_actividad] {e}")
            conn.rollback()
            return False
        finally:
            cur.close()


# ══════════════════════════════════════════════════════════════════════════════
#  DETALLE DE ACTIVIDAD
# ══════════════════════════════════════════════════════════════════════════════

def obtener_detalles_actividad(actividad_id):
    sql = """SELECT D."ID", D."DESCRIPCION", D."FECHA_REGISTRO",
                    D."HORAS_ESTIMADAS", D."HORAS_REALES",
                    D."COMPLETADO", D."NOTAS", D."CREADO_EN",
                    T."DESCRIPCION" as "TIPO",
                    U."NOMBRE_COMPLETO" as "RESPONSABLE"
             FROM "DETALLE_ACTIVIDAD" D
             JOIN "CAT_TIPO_ACTIVIDAD" T ON D."ID_TIPO"=T."ID"
             LEFT JOIN "USUARIOS" U ON D."RESPONSABLE"=U."ID"
             WHERE D."ID_ACTIVIDAD"=?
             ORDER BY D."CREADO_EN" ASC"""
    return ejecutar_query(sql, (actividad_id,))


def crear_detalle_actividad(actividad_id, datos):
    sql = """INSERT INTO "DETALLE_ACTIVIDAD"
                 ("ID","ID_ACTIVIDAD","ID_TIPO","DESCRIPCION","RESPONSABLE",
                  "FECHA_REGISTRO","HORAS_ESTIMADAS","HORAS_REALES",
                  "COMPLETADO","NOTAS","CREADO_EN","CREADO_POR")
             VALUES (SYSUUID,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)"""
    return ejecutar_dml(sql, (
        actividad_id, datos.get('id_tipo'), datos.get('descripcion'),
        datos.get('responsable') or None,
        datos.get('fecha_registro') or datetime.today().strftime('%Y-%m-%d'),
        float(datos['horas_estimadas']) if datos.get('horas_estimadas') else None,
        float(datos['horas_reales'])    if datos.get('horas_reales')    else None,
        1 if datos.get('completado') else 0,
        datos.get('notes') or datos.get('notas') or None,
        datos.get('creado_por', 'SISTEMA'),
    ))


def toggle_detalle_completado(detalle_id, completado):
    return ejecutar_dml('UPDATE "DETALLE_ACTIVIDAD" SET "COMPLETADO"=? WHERE "ID"=?',
                        (1 if completado else 0, detalle_id))


def eliminar_detalle_actividad(detalle_id):
    return ejecutar_dml('DELETE FROM "DETALLE_ACTIVIDAD" WHERE "ID"=?', (detalle_id,))


# ══════════════════════════════════════════════════════════════════════════════
#  ACTIVIDADES POR PROYECTO
# ══════════════════════════════════════════════════════════════════════════════

def obtener_actividades_por_proyecto(proyecto_id, usuario_id=None, incluir_actividad_id=None):
    filtro_activas = 'UPPER(E."DESCRIPCION") NOT IN (\'COMPLETADO\', \'CANCELADO\', \'ESPERANDO APROBACIÓN\')'
    if incluir_actividad_id:
        filtro_estatus = f'({filtro_activas} OR A."ID" = ?)'
    else:
        filtro_estatus = filtro_activas
    if usuario_id:
        sql = """
            SELECT A."ID", A."NOMBRE_ACTIVIDAD",
                   E."DESCRIPCION" as "ESTATUS",
                   COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                                  WHERE R."ID_ACTIVIDAD"=A."ID"), 0) as "HORAS_INVERTIDAS",
                   CASE
                       WHEN A."ASIGNADO_A" = ? THEN 1
                       WHEN EXISTS (
                           SELECT 1
                           FROM "ACTIVIDAD_RESPONSABLES" AR
                           WHERE AR."ID_ACTIVIDAD" = A."ID"
                               AND AR."ID_USUARIO" = ?
                       ) THEN CASE
                                   WHEN (
                                           SELECT COUNT(*)
                                           FROM "ACTIVIDAD_RESPONSABLES" AR2
                                           WHERE AR2."ID_ACTIVIDAD" = A."ID"
                                   ) = 1 THEN 1
                                   ELSE 2
                               END
                       ELSE 3
                   END as "GRUPO_ORDEN",
                   CASE
                       WHEN A."ASIGNADO_A" = ? THEN 'propia'
                       WHEN EXISTS (
                           SELECT 1
                           FROM "ACTIVIDAD_RESPONSABLES" AR
                           WHERE AR."ID_ACTIVIDAD" = A."ID"
                               AND AR."ID_USUARIO" = ?
                       ) THEN CASE
                                   WHEN (
                                           SELECT COUNT(*)
                                           FROM "ACTIVIDAD_RESPONSABLES" AR2
                                           WHERE AR2."ID_ACTIVIDAD" = A."ID"
                                   ) = 1 THEN 'propia'
                                   ELSE 'compartida'
                               END
                       ELSE 'otra'
                    END as "GRUPO"
            FROM "ACTIVIDADES" A
            JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS" = E."ID"
            WHERE A."ID_PROYECTO" = ?
                AND """ + filtro_estatus + """
            ORDER BY
                CASE
                    WHEN A."ASIGNADO_A" = ? THEN 1
                    WHEN EXISTS (
                        SELECT 1
                        FROM "ACTIVIDAD_RESPONSABLES" AR
                        WHERE AR."ID_ACTIVIDAD" = A."ID"
                            AND AR."ID_USUARIO" = ?
                    ) THEN CASE
                                WHEN (
                                        SELECT COUNT(*)
                                        FROM "ACTIVIDAD_RESPONSABLES" AR2
                                        WHERE AR2."ID_ACTIVIDAD" = A."ID"
                                ) = 1 THEN 1
                                ELSE 2
                            END
                    ELSE 3
                END,
                A."PRIORIDAD" ASC,
                A."NOMBRE_ACTIVIDAD" ASC
        """
        params = [
            usuario_id,
            usuario_id,
            usuario_id,
            usuario_id,
            proyecto_id,
        ]
        if incluir_actividad_id:
            params.append(incluir_actividad_id)
        params.extend([
            usuario_id,
            usuario_id,
        ])
        return ejecutar_query(sql, tuple(params))

    sql = """
        SELECT A."ID", A."NOMBRE_ACTIVIDAD",
               E."DESCRIPCION" as "ESTATUS",
               COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                                      WHERE R."ID_ACTIVIDAD"=A."ID"), 0) as "HORAS_INVERTIDAS",
               3 as "GRUPO_ORDEN",
               'otra' as "GRUPO"
        FROM "ACTIVIDADES" A
        JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS" = E."ID"
        WHERE A."ID_PROYECTO" = ?
            AND """ + filtro_estatus + """
        ORDER BY A."PRIORIDAD" ASC, A."NOMBRE_ACTIVIDAD" ASC
    """
    params = [proyecto_id]
    if incluir_actividad_id:
        params.append(incluir_actividad_id)
    return ejecutar_query(sql, tuple(params))


def recalcular_avance_actividad(actividad_id):
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                'SELECT COALESCE(SUM("HORAS"), 0) FROM "REGISTRO_ACTIVIDADES" WHERE "ID_ACTIVIDAD"=?',
                (actividad_id,)
            )
            horas_reg = float(cur.fetchone()[0] or 0)

            cur.execute(
                'SELECT COALESCE(SUM("HORAS_ESTIMADAS"), 0) FROM "DETALLE_ACTIVIDAD" WHERE "ID_ACTIVIDAD"=?',
                (actividad_id,)
            )
            horas_est = float(cur.fetchone()[0] or 0)

            if horas_est > 0:
                pct = min(100, round((horas_reg / horas_est) * 100))
            elif horas_reg > 0:
                pct = min(100, round(horas_reg * 10))
            else:
                pct = 0

            cur.execute(
                'UPDATE "ACTIVIDADES" SET "AVANCE_PCT"=?, "ACTUALIZADO_EN"=CURRENT_TIMESTAMP WHERE "ID"=?',
                (pct, actividad_id)
            )
            conn.commit()
            return pct
        except Exception as e:
            print(f"[recalcular_avance] {e}")
            conn.rollback()
            return None
        finally:
            cur.close()


def obtener_historial_actividad(actividad_id):
    sql = """
        SELECT R."FECHA", R."ACCION", R."HORAS", R."DETALLES", R."ID",
               U."NOMBRE_COMPLETO" as "USUARIO",
               T."DESCRIPCION" as "TIPO"
        FROM "REGISTRO_ACTIVIDADES" R
        JOIN "USUARIOS" U ON R."ID_USUARIO" = U."ID"
        LEFT JOIN "CAT_TIPO_ACTIVIDAD" T ON R."ID_TIPO_ACT" = T."ID"
        WHERE R."ID_ACTIVIDAD" = ?
        ORDER BY R."FECHA" DESC, R."ID" DESC
    """
    return ejecutar_query(sql, (actividad_id,))


def obtener_recursos_actividad(actividad_id):
    sql = """
        SELECT R."ID", R."NOMBRE"
        FROM "ACTIVIDAD_RECURSOS" AR
        JOIN "CAT_RECURSOS" R ON AR."ID_RECURSO" = R."ID"
        WHERE AR."ID_ACTIVIDAD" = ?
        ORDER BY R."NOMBRE"
    """
    return ejecutar_query(sql, (actividad_id,))


def obtener_responsables_actividad(actividad_id):
    sql = """
        SELECT U."ID", U."NOMBRE_COMPLETO"
        FROM "ACTIVIDAD_RESPONSABLES" AR
        JOIN "USUARIOS" U ON AR."ID_USUARIO" = U."ID"
        WHERE AR."ID_ACTIVIDAD" = ?
        ORDER BY U."NOMBRE_COMPLETO"
    """
    return ejecutar_query(sql, (actividad_id,))


def guardar_recursos_actividad(actividad_id, ids_recursos):
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute('DELETE FROM "ACTIVIDAD_RECURSOS" WHERE "ID_ACTIVIDAD"=?', (actividad_id,))
            for rid in ids_recursos:
                if rid:
                    cur.execute(
                        'INSERT INTO "ACTIVIDAD_RECURSOS" ("ID_ACTIVIDAD","ID_RECURSO") VALUES (?,?)',
                        (actividad_id, rid)
                    )
            conn.commit()
            return True
        except Exception as e:
            print(f"[guardar_recursos_actividad] {e}")
            conn.rollback()
            return False
        finally:
            cur.close()


def reasignar_registros_proyecto(actividad_id: str, nuevo_proyecto_id: str) -> bool:
    return ejecutar_dml(
        'UPDATE "REGISTRO_ACTIVIDADES" SET "ID_PROYECTO"=? WHERE "ID_ACTIVIDAD"=?',
        (nuevo_proyecto_id, actividad_id)
    )


# ══════════════════════════════════════════════════════════════════════════════
#  MÓDULO CALENDARIO — AUSENCIAS DE USUARIO
# ══════════════════════════════════════════════════════════════════════════════

def obtener_ausencias(filtros=None):
    if filtros is None:
        filtros = {}
    params, where = [], []
    if filtros.get("user_id"):
        where.append('"A"."ID_USUARIO" = ?')
        params.append(filtros["user_id"])
    if filtros.get("tipo"):
        where.append('"A"."TIPO" = ?')
        params.append(filtros["tipo"])
    if filtros.get("anio"):
        where.append('(YEAR("A"."FECHA_INICIO") = ? OR YEAR("A"."FECHA_FIN") = ?)')
        params += [int(filtros["anio"]), int(filtros["anio"])]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT A."ID", A."ID_USUARIO", A."FECHA_INICIO", A."FECHA_FIN",
               A."TIPO", A."HORAS_DIA", A."DESCRIPCION", A."CREADO_EN",
               U."NOMBRE_COMPLETO" AS "USUARIO"
        FROM "AUSENCIAS_USUARIO" A
        JOIN "USUARIOS" U ON A."ID_USUARIO" = U."ID"
        {where_sql}
        ORDER BY A."FECHA_INICIO" DESC
    """
    return ejecutar_query(sql, tuple(params) if params else None)


def obtener_ausencias_mes(anio: int, mes: int):
    primer_dia = date(anio, mes, 1)
    ultimo_dia = date(anio + 1, 1, 1) - timedelta(days=1) if mes == 12 else date(anio, mes + 1, 1) - timedelta(days=1)
    sql = """
        SELECT A."ID", A."ID_USUARIO", A."FECHA_INICIO", A."FECHA_FIN",
               A."TIPO", A."HORAS_DIA", A."DESCRIPCION",
               U."NOMBRE_COMPLETO" AS "USUARIO"
        FROM "AUSENCIAS_USUARIO" A
        JOIN "USUARIOS" U ON A."ID_USUARIO" = U."ID"
        WHERE A."FECHA_FIN" >= ? AND A."FECHA_INICIO" <= ?
        ORDER BY A."FECHA_INICIO" ASC
    """
    return ejecutar_query(sql, (primer_dia.strftime("%Y-%m-%d"), ultimo_dia.strftime("%Y-%m-%d")))


def guardar_ausencia(datos: dict) -> bool:
    sql = """
        INSERT INTO "AUSENCIAS_USUARIO"
            ("ID","ID_USUARIO","FECHA_INICIO","FECHA_FIN","TIPO","HORAS_DIA","DESCRIPCION","CREADO_EN","CREADO_POR")
        VALUES (SYSUUID,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)
    """
    return ejecutar_dml(sql, (
        datos.get("id_usuario"),
        datos.get("fecha_inicio"),
        datos.get("fecha_fin"),
        datos.get("tipo", "DIA_LIBRE"),
        float(datos.get("horas_dia", 8)),
        datos.get("descripcion") or None,
        datos.get("creado_por") or None,
    ))


def actualizar_ausencia(ausencia_id: str, datos: dict) -> bool:
    sql = """
        UPDATE "AUSENCIAS_USUARIO"
        SET "ID_USUARIO"=?, "FECHA_INICIO"=?, "FECHA_FIN"=?,
            "TIPO"=?, "HORAS_DIA"=?, "DESCRIPCION"=?, "ACTUALIZADO_EN"=CURRENT_TIMESTAMP
        WHERE "ID"=?
    """
    return ejecutar_dml(sql, (
        datos.get("id_usuario"),
        datos.get("fecha_inicio"),
        datos.get("fecha_fin"),
        datos.get("tipo", "DIA_LIBRE"),
        float(datos.get("horas_dia", 8)),
        datos.get("descripcion") or None,
        ausencia_id,
    ))


def eliminar_ausencia(ausencia_id: str) -> bool:
    return ejecutar_dml('DELETE FROM "AUSENCIAS_USUARIO" WHERE "ID"=?', (ausencia_id,))
