from datetime import datetime
import re
from web_app.database import _pool, ejecutar_query
from web_app.modules.tracker.queries import _registros_where


def _reporte_rh_safe(value):
    text = str(value).strip() if value is not None else ""
    return text or "—"


def _reporte_rh_fecha(value):
    if value is None:
        return "—"
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else (text or "—")


def _reporte_rh_prioridad(value):
    if value is None:
        return "—"
    try:
        return {1: "Alta", 2: "Media", 3: "Baja"}.get(int(value), "—")
    except (TypeError, ValueError):
        return "—"


def _reporte_rh_horas(actividad):
    """Replica las horas visibles de la hoja Actividades del Excel."""
    es_padre_con_hijas = (
        not actividad.get("ID_ACTIVIDAD_PADRE")
        and int(actividad.get("NUM_HIJAS") or 0) > 0
    )
    campo = "HORAS_DIRECTAS" if es_padre_con_hijas else "HORAS_TOTALES"
    return float(actividad.get(campo) or 0)


def obtener_matriz_reporte_rh(filtros=None):
    """
    Devuelve las 16 columnas visibles de la hoja ``Actividades`` para la vista RH.

    La consulta y el armado de ``DESGLOSE_REPORTE`` se reutilizan directamente de
    ``obtener_datos_reporte_proyecto`` para preservar los saltos de línea, viñetas
    y reglas de horas del archivo Excel.
    """
    filtros = dict(filtros or {})
    proyectos = ejecutar_query(
        'SELECT "ID" FROM "PROYECTOS" WHERE "ACTIVO"=1 ORDER BY "NOMBRE_PROYECTO"'
    )
    if filtros.get("proyecto_id"):
        proyectos = [
            proyecto for proyecto in proyectos
            if str(proyecto.get("ID")) == str(filtros["proyecto_id"])
        ]
    where_registros, params_registros = _registros_where('R', filtros)
    where_registros.extend([
        'R."ID_ACTIVIDAD" IS NOT NULL',
        'COALESCE(R."ES_PROPAGADO", 0) != 1',
    ])
    where_ultimo_registro = ' AND '.join(where_registros)
    registros_recientes = ejecutar_query(
        """
        SELECT R."ID_ACTIVIDAD", MAX(R."CREADO_EN") AS "ULTIMO_REGISTRO"
        FROM "REGISTRO_ACTIVIDADES" R
        WHERE """ + where_ultimo_registro + """
        GROUP BY R."ID_ACTIVIDAD"
        """,
        tuple(params_registros),
    )
    ultima_fecha_por_actividad = {
        str(registro["ID_ACTIVIDAD"]): str(registro["ULTIMO_REGISTRO"])
        for registro in registros_recientes
    }
    filas = []

    for proyecto in proyectos:
        actividades, _, _ = obtener_datos_reporte_proyecto(proyecto["ID"], filtros)
        for actividad in actividades:
            es_hija = bool(actividad.get("ID_ACTIVIDAD_PADRE"))
            nombre = _reporte_rh_safe(actividad.get("NOMBRE_ACTIVIDAD"))
            if es_hija:
                descripcion = f"      ↳ {nombre}"
            else:
                numero_hijas = int(actividad.get("NUM_HIJAS") or 0)
                descripcion = (
                    f"▾ {nombre}  ({numero_hijas} subactividad{'es' if numero_hijas != 1 else ''})"
                    if numero_hijas else f"  {nombre}"
                )

            horas = _reporte_rh_horas(actividad)
            tipo = _reporte_rh_safe(actividad.get("TIPO"))
            try:
                avance = float(actividad.get("AVANCE_PCT") or 0)
                avance = avance / 100.0 if avance > 1 else avance
            except (TypeError, ValueError):
                avance = 0

            # El orden y significado de cada valor corresponde exactamente a A:P
            # de la hoja Actividades. En particular, la columna C conserva los
            # recursos y D el valor DESARROLLO/TAREA como el Excel actual.
            fila = {
                "entregable": _reporte_rh_safe(actividad.get("NOMBRE_ENTREGABLE")),
                "descripcion": descripcion,
                "tipo_desarrollo": _reporte_rh_safe(actividad.get("RECURSOS")),
                "fase": tipo,
                "fecha_solicitud": _reporte_rh_fecha(actividad.get("FECHA_SOLICITUD")),
                "fecha_inicio": _reporte_rh_fecha(actividad.get("FECHA_INICIO")),
                "fecha_fin": _reporte_rh_fecha(actividad.get("FECHA_FIN_REAL")),
                "complejidad": _reporte_rh_prioridad(actividad.get("PRIORIDAD")),
                "horas_desarrollo": horas if tipo == "DESARROLLO" else "",
                "horas_tarea": horas if tipo == "TAREA" else "",
                "avance": avance,
                "estatus": _reporte_rh_safe(actividad.get("ESTATUS")),
                "responsable": _reporte_rh_safe(actividad.get("SOLICITANTE")),
                "recurso": _reporte_rh_safe(actividad.get("RESPONSABLES")),
                "dependencia": (
                    _reporte_rh_safe(actividad.get("NOMBRE_ACTIVIDAD_PADRE"))
                    if es_hija else "—"
                ),
                "actividades_texto": _reporte_rh_safe(actividad.get("DESGLOSE_REPORTE")),
            }
            # La hoja se muestra por el último registro capturado, no por la
            # fecha de creación de la actividad. Las fechas están en el mismo
            # desglose que consume Excel y no se exponen como una 17ª columna.
            fechas_registro = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", fila["actividades_texto"])
            fecha_orden = (
                ultima_fecha_por_actividad.get(str(actividad.get("ID")))
                or (max(fechas_registro) if fechas_registro else fila["fecha_solicitud"])
            )
            filas.append((fecha_orden, fila))

    filas.sort(key=lambda item: item[0] if item[0] != "—" else "", reverse=True)
    return [fila for _, fila in filas]

def obtener_datos_reporte_proyecto(proyecto_id, filtros=None):
    """
    Trae en una sola conexion:
      - actividades con todos sus metadatos (incluyendo SOLICITANTE y jerarquías)
      - evidencias agrupadas por actividad
      - resumen de horas por desarrollador (Desarrollo / Tarea / Total)
    Devuelve: (actividades, evidencias_por_actividad, resumen_desarrolladores)
    """
    filtros_reporte = dict(filtros or {})
    filtros_reporte['proyecto_id'] = proyecto_id
    where_registros, params_registros = _registros_where('R', filtros_reporte)
    where_registros_sql = ' AND '.join(where_registros) if where_registros else '1=1'

    # Si el reporte se está exportando filtrado por un desarrollador específico,
    # la columna "Recurso" (RESPONSABLES) debe mostrar solo a ese usuario y no a
    # todos los responsables asignados a la actividad.
    user_id_filtro = filtros_reporte.get('user_id')
    if user_id_filtro:
        resp_filter_sql = ' AND AR."ID_USUARIO" = ?'
        resp_params = [user_id_filtro]
    else:
        resp_filter_sql = ''
        resp_params = []

    sql_acts = """
        SELECT
            A."ID",
            A."NOMBRE_ACTIVIDAD",
            A."DESCRIPCION",
            A."TIPO",
            A."FECHA_SOLICITUD",
            A."FECHA_INICIO",
            A."FECHA_FIN_REAL",
            A."PRIORIDAD",
            A."SOLICITANTE",
            A."AVANCE_PCT",
            A."ID_ACTIVIDAD_PADRE",
            PADRE."NOMBRE_ACTIVIDAD" AS "NOMBRE_ACTIVIDAD_PADRE",
            E."DESCRIPCION"   AS "ESTATUS",
            P."NOMBRE_PROYECTO",
            CAT."NOMBRE" AS "NOMBRE_ENTREGABLE",
            COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                       WHERE R."ID_ACTIVIDAD" = A."ID" AND """ + where_registros_sql + """), 0) AS "HORAS_TOTALES",
            COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                       WHERE R."ID_ACTIVIDAD" = A."ID"
                         AND COALESCE(R."ES_PROPAGADO", 0) = 0
                         AND """ + where_registros_sql + """), 0) AS "HORAS_DIRECTAS",
            (SELECT STRING_AGG(U2."NOMBRE_COMPLETO", ' / ')
             FROM "ACTIVIDAD_RESPONSABLES" AR
             JOIN "USUARIOS" U2 ON AR."ID_USUARIO" = U2."ID"
             WHERE AR."ID_ACTIVIDAD" = A."ID" """ + resp_filter_sql + """) AS "RESPONSABLES",
            (SELECT STRING_AGG(RC."NOMBRE", ' / ')
             FROM "ACTIVIDAD_RECURSOS" ARR
             JOIN "CAT_RECURSOS" RC ON ARR."ID_RECURSO" = RC."ID"
             WHERE ARR."ID_ACTIVIDAD" = A."ID") AS "RECURSOS",
            (SELECT COUNT(*) FROM "ACTIVIDADES" H
             WHERE H."ID_ACTIVIDAD_PADRE" = A."ID") AS "NUM_HIJAS",
            (SELECT STRING_AGG(H2."NOMBRE_ACTIVIDAD", ' | ')
             FROM "ACTIVIDADES" H2
             WHERE H2."ID_ACTIVIDAD_PADRE" = A."ID") AS "NOMBRES_HIJAS"
        FROM "ACTIVIDADES" A
        JOIN "PROYECTOS" P ON A."ID_PROYECTO" = P."ID"
        JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS" = E."ID"
        LEFT JOIN "ACTIVIDADES" PADRE ON A."ID_ACTIVIDAD_PADRE" = PADRE."ID"
        LEFT JOIN "CAT_ENTREGABLES" CAT ON A."ID_ENTREGABLE" = CAT."ID"
        WHERE A."ID_PROYECTO" = ?
          AND (
              /* Condición 1: La actividad tiene registros de horas válidos */
              EXISTS (
                  SELECT 1
                  FROM "REGISTRO_ACTIVIDADES" R
                  WHERE R."ID_ACTIVIDAD" = A."ID" AND """ + where_registros_sql + """
              )
              OR
              /* Condición 2: Es un Padre con 0 horas pero tiene hijos que sí tienen registros de horas */
              EXISTS (
                  SELECT 1
                  FROM "ACTIVIDADES" HIJA
                  JOIN "REGISTRO_ACTIVIDADES" R ON R."ID_ACTIVIDAD" = HIJA."ID"
                  WHERE HIJA."ID_ACTIVIDAD_PADRE" = A."ID" AND """ + where_registros_sql + """
              )
          )
        ORDER BY A."FECHA_SOLICITUD" ASC, A."CREADO_EN" ASC
    """

    sql_evs = """
        SELECT
            EV."ID_ACTIVIDAD",
            EV."TITULO",
            EV."NOMBRE_ARCHIVO",
            EV."URL_ARCHIVO",
            EV."MIME_TYPE",
            T."DESCRIPCION" AS "TIPO"
        FROM "EVIDENCIA_ACTIVIDAD" EV
        JOIN "CAT_TIPO_EVIDENCIA" T ON EV."ID_TIPO" = T."ID"
        JOIN "ACTIVIDADES" A ON EV."ID_ACTIVIDAD" = A."ID"
        WHERE A."ID_PROYECTO" = ?
          AND EXISTS (
              SELECT 1
              FROM "REGISTRO_ACTIVIDADES" R
              WHERE R."ID_ACTIVIDAD" = A."ID" AND """ + where_registros_sql + """
          )
        ORDER BY EV."CREADO_EN" ASC
    """
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            # HORAS_DIRECTAS agrega un bloque extra de params_registros en el SELECT
            # Orden de parámetros en sql_acts (debe coincidir con el orden textual
            # en que aparecen los "?" dentro de sql_acts):
            #   1. where_registros_sql en HORAS_TOTALES subquery       → params_registros
            #   2. where_registros_sql en HORAS_DIRECTAS subquery      → params_registros
            #   3. resp_filter_sql en RESPONSABLES subquery            → resp_params (solo si hay user_id)
            #   4. WHERE A."ID_PROYECTO" = ?                           → proyecto_id
            #   5. where_registros_sql en EXISTS condición 1           → params_registros
            #   6. where_registros_sql en EXISTS condición 2           → params_registros
            params_acts = tuple(
                params_registros          # HORAS_TOTALES subquery
                + params_registros        # HORAS_DIRECTAS subquery
                + resp_params              # RESPONSABLES subquery (filtro por usuario)
                + [proyecto_id]
                + params_registros        # EXISTS condición 1
                + params_registros        # EXISTS condición 2
            )
            cur.execute(sql_acts, params_acts)
            cols_a = [c[0] for c in cur.description]
            actividades = [dict(zip(cols_a, row)) for row in cur.fetchall()]

            # Mapa actividad_id -> TIPO (DESARROLLO/TAREA), para clasificar horas por desarrollador
            tipo_por_actividad = {a.get("ID"): (a.get("TIPO") or "—") for a in actividades}
            nombre_por_actividad = {a.get("ID"): (a.get("NOMBRE_ACTIVIDAD") or "—") for a in actividades}

            resumen_desarrolladores = {}

            def _acumular_dev(nombre, horas, tipo, actividad_id, actividad_nombre=None, detalle_key=None):
                nombre = (nombre or "").strip() or "Sin desarrollador"
                horas = float(horas or 0)
                dev = resumen_desarrolladores.setdefault(nombre, {
                    "horas_total": 0.0,
                    "horas_desarrollo": 0.0,
                    "horas_tarea": 0.0,
                    "registros": 0,
                    "actividades": set(),
                    "detalle": {},
                })
                dev["horas_total"] += horas
                if tipo == "DESARROLLO":
                    dev["horas_desarrollo"] += horas
                else:
                    dev["horas_tarea"] += horas
                dev["registros"] += 1
                if actividad_id:
                    dev["actividades"].add(actividad_id)

                # Detalle: cuántas horas/registros le corresponden a cada actividad
                # dentro del total de este desarrollador (clave estable aunque la
                # actividad no tenga ID, como las Actividades Rápidas / ad-hoc).
                clave = detalle_key if detalle_key is not None else actividad_id
                if clave is not None:
                    item = dev["detalle"].setdefault(clave, {
                        "nombre": actividad_nombre or "—",
                        "tipo": tipo,
                        "horas": 0.0,
                        "registros": 0,
                    })
                    item["horas"] += horas
                    item["registros"] += 1

            sql_registros_desglose = """
                SELECT
                    R."ID_ACTIVIDAD",
                    R."FECHA",
                    R."CREADO_EN",
                    R."ACCION",
                    R."DETALLES",
                    R."HORAS",
                    COALESCE(R."ES_PROPAGADO", 0) AS "ES_PROPAGADO",
                    U."NOMBRE_COMPLETO" AS "DESARROLLADOR"
                FROM "REGISTRO_ACTIVIDADES" R
                LEFT JOIN "USUARIOS" U ON R."ID_USUARIO" = U."ID"
                WHERE """ + where_registros_sql + """
                ORDER BY R."FECHA" ASC, R."CREADO_EN" ASC, R."ID" ASC
            """
            cur.execute(sql_registros_desglose, tuple(params_registros))
            cols_r = [c[0] for c in cur.description]
            desglose_por_actividad = {}
            for row in cur.fetchall():
                registro = dict(zip(cols_r, row))
                actividad_id = registro.get("ID_ACTIVIDAD")
                if not actividad_id:
                    continue

                # Los registros propagados (ES_PROPAGADO=1) son "espejos" automáticos
                # que el padre recibe cuando una hija registra horas. Si los sumáramos
                # aquí, cada hora de una hija se contaría dos veces (una en la hija,
                # otra en el espejo del padre). Se excluyen para que el total coincida
                # con las hojas "Resumen" y "Actividades", que ya descartan los espejos.
                es_propagado = int(registro.get("ES_PROPAGADO") or 0)
                if es_propagado == 1:
                    continue

                desarrollador = (registro.get("DESARROLLADOR") or "").strip() or "Sin desarrollador"
                horas_registro = float(registro.get("HORAS") or 0)
                fecha_registro = str(registro.get("FECHA") or "").strip()[:10] or "—"
                accion = (registro.get("ACCION") or "").strip()
                detalles = (registro.get("DETALLES") or "").strip()
                accion_o_detalle = accion or detalles or "—"
                descripcion_registro = detalles or "—"
                linea = f"• {fecha_registro} - {accion_o_detalle}: {descripcion_registro} ({horas_registro:g}h)"
                bloques_actividad = desglose_por_actividad.setdefault(actividad_id, {})
                bloques_actividad.setdefault(desarrollador, []).append(linea)

                tipo_actividad = tipo_por_actividad.get(actividad_id, "—")
                nombre_actividad = nombre_por_actividad.get(actividad_id, "—")
                _acumular_dev(
                    desarrollador, horas_registro, tipo_actividad, actividad_id,
                    actividad_nombre=nombre_actividad, detalle_key=actividad_id,
                )

            params_evs = tuple([proyecto_id] + params_registros)
            cur.execute(sql_evs, params_evs)
            cols_e = [c[0] for c in cur.description]
            evidencias_por_actividad = {}
            for row in cur.fetchall():
                ev = dict(zip(cols_e, row))
                act_id = ev["ID_ACTIVIDAD"]
                evidencias_por_actividad.setdefault(act_id, []).append(ev)

            # Inyectar pseudo-actividades individuales para Actividades Rápidas (Ad-hoc)
            where_adhoc, params_adhoc = _registros_where('R', filtros_reporte)
            sql_adhoc = """
                SELECT
                    R."FECHA",
                    R."HORAS",
                    R."ACCION",
                    R."DETALLES",
                    T."DESCRIPCION" AS "TIPO",
                    U."NOMBRE_COMPLETO" AS "RESPONSABLE",
                    P."NOMBRE_PROYECTO"
                FROM "REGISTRO_ACTIVIDADES" R
                LEFT JOIN "CAT_TIPO_ACTIVIDAD" T ON R."ID_TIPO_ACT" = T."ID"
                LEFT JOIN "USUARIOS" U ON R."ID_USUARIO" = U."ID"
                LEFT JOIN "PROYECTOS" P ON R."ID_PROYECTO" = P."ID"
                WHERE """ + (' AND '.join(where_adhoc + ['R."ID_ACTIVIDAD" IS NULL']) if where_adhoc else 'R."ID_ACTIVIDAD" IS NULL') + """
                ORDER BY R."FECHA" ASC
            """
            cur.execute(sql_adhoc, tuple(params_adhoc))
            for rec in cur.fetchall():
                fecha_val = rec[0]
                horas_val = float(rec[1] or 0)
                accion_val = rec[2] or "Actividad Rápida"
                detalles_val = rec[3] or "Horas registrados directamente al proyecto."
                tipo_val = rec[4] or "Ad-hoc"
                responsable_val = rec[5] or "—"
                proyecto_nombre = rec[6] or "Proyecto"

                fecha_str = str(fecha_val)[:10] if fecha_val else None

                # Las actividades rápidas (ad-hoc) se clasifican como TAREA para el resumen por desarrollador.
                # No tienen ID real, así que se agrupan por su descripción para no duplicar líneas
                # cuando el mismo desarrollador repite la misma acción rápida varias veces.
                nombre_adhoc = f"⚡ {tipo_val} - {accion_val}"
                _acumular_dev(
                    responsable_val, horas_val, "TAREA", None,
                    actividad_nombre=nombre_adhoc, detalle_key=f"adhoc::{nombre_adhoc}",
                )

                actividades.append({
                    "ID": None,
                    "NOMBRE_ACTIVIDAD": f"⚡ {tipo_val} - {accion_val}",
                    "DESCRIPCION": detalles_val,
                    "TIPO": "TAREA",
                    "FECHA_SOLICITUD": fecha_str,
                    "FECHA_INICIO": fecha_str,
                    "FECHA_FIN_REAL": fecha_str,
                    "PRIORIDAD": None,
                    "SOLICITANTE": "—",
                    "ID_ACTIVIDAD_PADRE": None,
                    "NOMBRE_ACTIVIDAD_PADRE": None,
                    "ESTATUS": "Completado",
                    "NOMBRE_PROYECTO": proyecto_nombre,
                    "HORAS_TOTALES": horas_val,
                    "HORAS_DIRECTAS": horas_val,  # Ad-hoc: siempre directas
                    "RESPONSABLES": responsable_val,
                    "RECURSOS": "—",
                    "NUM_HIJAS": 0,
                    "NOMBRES_HIJAS": "",
                    "ES_ACTIVIDAD_RAPIDA_HISTORICA": 1,
                    "NOTAS_REPORTE": "Actividad rápida histórica registrada antes de capturar solicitante, prioridad y recursos en forma estructurada.",
                    "DESGLOSE_REPORTE": f"{responsable_val}:\n  • {fecha_str or '—'} - {accion_val}: {detalles_val or '—'} ({horas_val:g}h)"
                })

            for actividad in actividades:
                if "ES_ACTIVIDAD_RAPIDA_HISTORICA" not in actividad:
                    actividad["ES_ACTIVIDAD_RAPIDA_HISTORICA"] = 0
                if "NOTAS_REPORTE" not in actividad:
                    actividad["NOTAS_REPORTE"] = ""
                # Garantizar que HORAS_DIRECTAS siempre existe en todos los registros
                if "HORAS_DIRECTAS" not in actividad:
                    actividad["HORAS_DIRECTAS"] = actividad.get("HORAS_TOTALES", 0)
                if "DESGLOSE_REPORTE" not in actividad:
                    actividad_id = actividad.get("ID")
                    bloques = desglose_por_actividad.get(actividad_id, {})
                    lineas_desglose = []
                    for desarrollador, items in bloques.items():
                        lineas_desglose.append(f"{desarrollador}:")
                        for item in items:
                            lineas_desglose.append(f"  {item}")
                        lineas_desglose.append("")
                    if lineas_desglose and lineas_desglose[-1] == "":
                        lineas_desglose.pop()
                    actividad["DESGLOSE_REPORTE"] = "\n".join(lineas_desglose) or "—"
                actividad["TIPO"] = (actividad.get("TIPO") or "—").strip() or "—"

            # Convertir los sets de actividades en conteos, y el detalle en lista ordenada
            for dev in resumen_desarrolladores.values():
                dev["actividades"] = len(dev["actividades"])
                detalle_map = dev.pop("detalle", {})
                dev["detalle"] = sorted(
                    detalle_map.values(), key=lambda item: item["horas"], reverse=True
                )

            return actividades, evidencias_por_actividad, resumen_desarrolladores
        finally:
            cur.close()
