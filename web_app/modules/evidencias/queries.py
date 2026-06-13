from web_app.database import ejecutar_query, ejecutar_dml

def obtener_evidencias_actividad(actividad_id):
    sql = """SELECT E."ID", E."TITULO", E."CONTENIDO_TEXTO",
                    E."NOMBRE_ARCHIVO", E."URL_ARCHIVO", E."MIME_TYPE", E."TAMANO_BYTES",
                    E."CREADO_EN",
                    T."DESCRIPCION" as "TIPO",
                    U."NOMBRE_COMPLETO" as "SUBIDO_POR"
             FROM "EVIDENCIA_ACTIVIDAD" E
             JOIN "CAT_TIPO_EVIDENCIA" T ON E."ID_TIPO"=T."ID"
             LEFT JOIN "USUARIOS" U ON E."SUBIDO_POR"=U."ID"
             WHERE E."ID_ACTIVIDAD"=?
             ORDER BY E."CREADO_EN" DESC"""
    return ejecutar_query(sql, (actividad_id,))


def obtener_evidencia_por_id(evidencia_id):
    sql = """SELECT E."ID", E."ID_ACTIVIDAD", E."ID_TIPO", E."TITULO", E."CONTENIDO_TEXTO",
                    E."NOMBRE_ARCHIVO", E."URL_ARCHIVO", E."MIME_TYPE", E."TAMANO_BYTES",
                    E."SUBIDO_POR", E."CREADO_EN",
                    T."DESCRIPCION" AS "TIPO",
                    U."NOMBRE_COMPLETO" AS "USUARIO_NOMBRE",
                    A."NOMBRE_ACTIVIDAD" as "ACTIVIDAD_NOMBRE",
                    A."ID_PROYECTO" as "PROYECTO_ID",
                    P."NOMBRE_PROYECTO" as "PROYECTO_NOMBRE"
             FROM "EVIDENCIA_ACTIVIDAD" E
             JOIN "CAT_TIPO_EVIDENCIA" T ON E."ID_TIPO" = T."ID"
             JOIN "ACTIVIDADES" A ON E."ID_ACTIVIDAD" = A."ID"
             JOIN "PROYECTOS" P ON A."ID_PROYECTO" = P."ID"
             LEFT JOIN "USUARIOS" U ON E."SUBIDO_POR" = U."ID"
             WHERE E."ID"=?"""
    rows = ejecutar_query(sql, (evidencia_id,))
    return rows[0] if rows else None


def obtener_evidencias_filtradas(
    proyecto_id=None,
    actividad_id=None,
    tipo_id=None,
    usuario_id=None,
    fecha_desde=None,
    fecha_hasta=None,
    q=None,
):
    where = []
    params = []

    if proyecto_id:
        where.append('A."ID_PROYECTO" = ?')
        params.append(proyecto_id)
    if actividad_id:
        where.append('E."ID_ACTIVIDAD" = ?')
        params.append(actividad_id)
    if tipo_id:
        where.append('E."ID_TIPO" = ?')
        params.append(tipo_id)
    if usuario_id:
        where.append('E."SUBIDO_POR" = ?')
        params.append(usuario_id)
    if fecha_desde:
        where.append('CAST(E."CREADO_EN" AS DATE) >= ?')
        params.append(fecha_desde)
    if fecha_hasta:
        where.append('CAST(E."CREADO_EN" AS DATE) <= ?')
        params.append(fecha_hasta)
    if q:
        tokens = [token.strip().upper() for token in q.split() if token.strip()]
        for token in tokens:
            where.append(
                '(UPPER(COALESCE(E."TITULO", \'\')) LIKE ? '
                'OR UPPER(COALESCE(E."CONTENIDO_TEXTO", \'\')) LIKE ? '
                'OR UPPER(COALESCE(A."NOMBRE_ACTIVIDAD", \'\')) LIKE ? '
                'OR UPPER(COALESCE(P."NOMBRE_PROYECTO", \'\')) LIKE ?)'
            )
            like = f'%{token}%'
            params.extend([like, like, like, like])

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    sql = (
        'SELECT E."ID", E."ID_ACTIVIDAD", E."ID_TIPO", E."TITULO", E."CONTENIDO_TEXTO", '
        'E."NOMBRE_ARCHIVO", E."URL_ARCHIVO", E."MIME_TYPE", E."TAMANO_BYTES", '
        'E."SUBIDO_POR", E."CREADO_EN", '
        'T."DESCRIPCION" AS "TIPO", '
        'U."NOMBRE_COMPLETO" AS "USUARIO_NOMBRE", '
        'A."NOMBRE_ACTIVIDAD" AS "ACTIVIDAD_NOMBRE", '
        'A."ID_PROYECTO" AS "PROYECTO_ID", '
        'P."NOMBRE_PROYECTO" AS "PROYECTO_NOMBRE" '
        'FROM "EVIDENCIA_ACTIVIDAD" E '
        'JOIN "CAT_TIPO_EVIDENCIA" T ON E."ID_TIPO" = T."ID" '
        'JOIN "ACTIVIDADES" A ON E."ID_ACTIVIDAD" = A."ID" '
        'JOIN "PROYECTOS" P ON A."ID_PROYECTO" = P."ID" '
        'LEFT JOIN "USUARIOS" U ON E."SUBIDO_POR" = U."ID"'
        + where_sql +
        ' ORDER BY E."CREADO_EN" DESC, P."NOMBRE_PROYECTO", A."NOMBRE_ACTIVIDAD"'
    )
    return ejecutar_query(sql, tuple(params))


def obtener_actividades_con_evidencia(proyecto_id=None):
    where = []
    params = []
    if proyecto_id:
        where.append('A."ID_PROYECTO" = ?')
        params.append(proyecto_id)

    where_clause = ""
    if where:
        where_clause = " WHERE " + " AND ".join(where)

    sql = 'SELECT DISTINCT A."ID", A."ID_PROYECTO", A."NOMBRE_ACTIVIDAD", P."NOMBRE_PROYECTO" ' \
          'FROM "EVIDENCIA_ACTIVIDAD" E ' \
          'JOIN "ACTIVIDADES" A ON E."ID_ACTIVIDAD" = A."ID" ' \
          'JOIN "PROYECTOS" P ON A."ID_PROYECTO" = P."ID"' + where_clause + \
          ' ORDER BY P."NOMBRE_PROYECTO", A."NOMBRE_ACTIVIDAD"'
    return ejecutar_query(sql, tuple(params))


def crear_evidencia(actividad_id, datos):
    sql = """INSERT INTO "EVIDENCIA_ACTIVIDAD"
                 ("ID","ID_ACTIVIDAD","ID_TIPO","TITULO","CONTENIDO_TEXTO",
                  "NOMBRE_ARCHIVO","URL_ARCHIVO","MIME_TYPE","TAMANO_BYTES",
                  "SUBIDO_POR","CREADO_EN")
             VALUES (SYSUUID,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"""
    return ejecutar_dml(sql, (
        actividad_id, datos.get('id_tipo'),
        datos.get('titulo') or None, datos.get('contenido_texto') or None,
        datos.get('url_archivo') or datos.get('nombre_archivo') or None,  # Match original parameter order/handling if needed, wait.
        # Let's check original db.py:
        # datos.get('nombre_archivo') or None, datos.get('url_archivo') or None,
        # Yes:
        datos.get('nombre_archivo') or None,
        datos.get('url_archivo') or None,
        datos.get('mime_type') or None,
        int(datos['tamano_bytes']) if datos.get('tamano_bytes') else None,
        datos.get('subido_por') or None,
    ))


def actualizar_evidencia(evidencia_id, datos):
    sql = '''UPDATE "EVIDENCIA_ACTIVIDAD"
             SET "ID_TIPO"=?,
                 "TITULO"=?,
                 "CONTENIDO_TEXTO"=?
             WHERE "ID"=?'''
    return ejecutar_dml(sql, (
        datos.get('id_tipo'),
        datos.get('titulo') or None,
        datos.get('contenido_texto') or None,
        evidencia_id,
    ))


def eliminar_evidencia(evidencia_id):
    return ejecutar_dml('DELETE FROM "EVIDENCIA_ACTIVIDAD" WHERE "ID"=?', (evidencia_id,))
