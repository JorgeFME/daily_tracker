import os
import json
import threading
import time
from queue import Queue, Empty
from hdbcli import dbapi
from datetime import datetime, timedelta


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE ENTORNO
# ══════════════════════════════════════════════════════════════════════════════

def load_env_from_dotenv():
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" in s:
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if not os.getenv(k):
                        os.environ[k] = v
    except Exception:
        pass


def get_hana_credentials():
    if not (os.getenv("HANA_HOST") and os.getenv("HANA_USER") and os.getenv("HANA_PASSWORD")):
        vcap = os.getenv("VCAP_SERVICES")
        if vcap:
            try:
                data = json.loads(vcap)
                creds = None
                for _, services in data.items():
                    for s in services:
                        c = s.get("credentials", {})
                        if c.get("host") and (c.get("user") or c.get("username")) and c.get("password"):
                            creds = c
                            break
                    if creds:
                        break
                if creds:
                    os.environ.setdefault("HANA_HOST",     str(creds.get("host")))
                    port_val = creds.get("port") or creds.get("port_tls")
                    if port_val is not None:
                        os.environ.setdefault("HANA_PORT", str(port_val))
                    os.environ.setdefault("HANA_USER",     str(creds.get("user") or creds.get("username")))
                    os.environ.setdefault("HANA_PASSWORD", str(creds.get("password")))
                    if creds.get("schema"):
                        os.environ.setdefault("HANA_SCHEMA", str(creds.get("schema")))
            except Exception:
                pass
    return {
        "host":     os.getenv("HANA_HOST"),
        "port":     int(os.getenv("HANA_PORT")) if os.getenv("HANA_PORT") else None,
        "user":     os.getenv("HANA_USER"),
        "password": os.getenv("HANA_PASSWORD"),
        "schema":   os.getenv("HANA_SCHEMA"),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CONNECTION POOL
#  - Mantiene conexiones abiertas y las reutiliza entre requests.
#  - Evita el overhead de dbapi.connect() + TLS + SET SCHEMA en cada query.
#  - Pool por defecto: 5 conexiones (ajustable con HANA_POOL_SIZE en .env).
#  - Thread-safe: cada worker de Gunicorn comparte el mismo pool del proceso.
# ══════════════════════════════════════════════════════════════════════════════

class HanaConnectionPool:
    def __init__(self, size: int = 5):
        self._size       = size
        self._pool       = Queue(maxsize=size)
        self._lock       = threading.Lock()
        self._creds      = None          # se inicializa al primer uso
        self._initialized = False

    # ── Inicialización diferida ────────────────────────────────────────────

    def _init_pool(self):
        """Crea las conexiones iniciales. Se llama una sola vez (lazy)."""
        with self._lock:
            if self._initialized:
                return
            self._creds = get_hana_credentials()
            missing = [k for k in ["host", "user", "password", "schema"] if not self._creds.get(k)]
            if missing:
                labels = {"host": "HANA_HOST", "user": "HANA_USER",
                          "password": "HANA_PASSWORD", "schema": "HANA_SCHEMA"}
                raise ValueError("Faltan variables de entorno: " + ", ".join(labels[m] for m in missing))
            for _ in range(self._size):
                self._pool.put(self._create_connection())
            self._initialized = True

    def _create_connection(self):
        """Abre una conexión nueva y aplica el schema."""
        c = self._creds
        conn = dbapi.connect(
            address=c["host"], port=c["port"] or 443,
            user=c["user"], password=c["password"],
            encrypt=True, sslValidateCertificate=False,
        )
        if c["schema"]:
            cur = conn.cursor()
            cur.execute(f'SET SCHEMA "{c["schema"]}"')
            cur.close()
        return conn

    def _is_alive(self, conn) -> bool:
        """Verifica que la conexión siga activa."""
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM DUMMY")
            cur.close()
            return True
        except Exception:
            return False

    # ── Contexto ───────────────────────────────────────────────────────────

    def get_connection(self, timeout: float = 10.0):
        """
        Devuelve un objeto _PooledConnection que se usa como context manager:

            with pool.get_connection() as conn:
                cur = conn.cursor()
                ...
        """
        if not self._initialized:
            self._init_pool()
        return _PooledConnection(self, timeout)

    def _acquire(self, timeout: float):
        try:
            conn = self._pool.get(timeout=timeout)
        except Empty:
            raise TimeoutError("No hay conexiones disponibles en el pool (timeout).")
        # Reconectar si la conexión cayó
        if not self._is_alive(conn):
            try:
                conn.close()
            except Exception:
                pass
            conn = self._create_connection()
        return conn

    def _release(self, conn):
        try:
            self._pool.put_nowait(conn)
        except Exception:
            # Pool lleno (no debería pasar), cerramos la sobrante
            try:
                conn.close()
            except Exception:
                pass


class _PooledConnection:
    """Context manager que devuelve la conexión al pool al salir."""
    __slots__ = ("_pool", "_timeout", "_conn")

    def __init__(self, pool: HanaConnectionPool, timeout: float):
        self._pool    = pool
        self._timeout = timeout
        self._conn    = None

    def __enter__(self):
        self._conn = self._pool._acquire(self._timeout)
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            if exc_type is not None:
                # Si hubo error intentamos rollback silencioso
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            self._pool._release(self._conn)
        return False   # no suprimimos excepciones


# Instancia global del pool (creada al importar el módulo)
_pool = HanaConnectionPool(size=int(os.getenv("HANA_POOL_SIZE", "5")))


# ── Helpers de bajo nivel ──────────────────────────────────────────────────

def get_hana_connection():
    """
    Compatibilidad: devuelve una conexión del pool.
    ADVERTENCIA: el caller es responsable de llamar conn.close()
    (que en realidad devuelve la conexión al pool).
    Para código nuevo usa `with _pool.get_connection() as conn:`.
    """
    if not _pool._initialized:
        _pool._init_pool()
    conn = _pool._acquire(timeout=10.0)
    # Envolvemos para que .close() devuelva al pool en vez de cerrar
    return _ReleasableConn(_pool, conn)


class _ReleasableConn:
    """Wrapper que redirige .close() a pool._release()."""
    def __init__(self, pool, conn):
        self._pool = pool
        self._conn = conn

    def close(self):
        self._pool._release(self._conn)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def ejecutar_query(query, params=None):
    with _pool.get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            cols = [col[0] for col in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()


def ejecutar_dml(query, params=None):
    with _pool.get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return True
        except Exception as e:
            print(f"[HANA DML Error] {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()


# ══════════════════════════════════════════════════════════════════════════════
#  CACHÉ DE CATÁLOGOS
#  Los catálogos (estatus, tipos, recursos) son datos casi estáticos.
#  Se cachean en memoria con un TTL de 5 minutos para evitar queries
#  repetidas en cada carga de página.
# ══════════════════════════════════════════════════════════════════════════════

_cache: dict = {}
_cache_lock  = threading.Lock()
CACHE_TTL    = int(os.getenv("CATALOG_CACHE_TTL", "300"))   # segundos


def _cached_query(cache_key: str, query: str, params=None):
    """Ejecuta una query y cachea el resultado. Invalida por TTL."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (now - entry["ts"]) < CACHE_TTL:
            return entry["data"]
    # Fuera del lock para no bloquear el pool
    data = ejecutar_query(query, params)
    with _cache_lock:
        _cache[cache_key] = {"data": data, "ts": now}
    return data


def invalidar_cache(cache_key: str = None):
    """Limpia una clave del caché o todo el caché si no se especifica clave."""
    with _cache_lock:
        if cache_key:
            _cache.pop(cache_key, None)
        else:
            _cache.clear()


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
#  DAILY TRACKER
# ══════════════════════════════════════════════════════════════════════════════

def guardar_registro_actividad(datos):
    # Validar que la actividad no esté Completada o Cancelada
    actividad_id = datos.get('actividad_id') or None
    if actividad_id:
        rows = ejecutar_query(
            'SELECT E."DESCRIPCION" FROM "ACTIVIDADES" A '
            'JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS"=E."ID" '
            'WHERE A."ID"=?',
            (actividad_id,)
        )
        if rows:
            estatus = rows[0]["DESCRIPCION"].upper()
            if estatus in ("COMPLETADO", "CANCELADO"):
                raise ValueError(
                    f"No se pueden registrar horas en una actividad con estatus '{rows[0]['DESCRIPCION']}'."
                )

    sql = """
        INSERT INTO "REGISTRO_ACTIVIDADES"
            ("ID","FECHA","ID_USUARIO","ID_PROYECTO","ID_ACTIVIDAD","ID_TIPO_ACT","ACCION","HORAS","DETALLES")
        VALUES (SYSUUID,?,?,?,?,?,?,?,?)
    """
    return ejecutar_dml(sql, (
        datos.get('date'),
        datos.get('user'),
        datos.get('project'),
        actividad_id,
        datos.get('tipo_act') or None,
        datos.get('activity_action'),
        datos.get('hours'),
        datos.get('details'),
    ))


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
                'WHERE "ID_USUARIO"=? AND "FECHA" BETWEEN ? AND ?',
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
                'WHERE "ID_USUARIO"=? AND "FECHA"=?',
                (user_id, fecha_str)
            )
            r = cur.fetchone()
            return float(r[0]) if r and r[0] else 0.0
        except Exception as e:
            print(f"[get_horas_diarias] {e}")
            return 0.0
        finally:
            cur.close()


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


# ══════════════════════════════════════════════════════════════════════════════
#  ACTIVIDADES
# ══════════════════════════════════════════════════════════════════════════════

def obtener_actividades(proyecto_id=None, estatus_id=None, usuario_id=None, fecha_desde=None, fecha_hasta=None):
    filtros, params = [], []
    if proyecto_id:
        filtros.append('A."ID_PROYECTO"=?')
        params.append(proyecto_id)
    if estatus_id:
        filtros.append('A."ID_ESTATUS"=?')
        params.append(estatus_id)
    if usuario_id:
        filtros.append('EXISTS (SELECT 1 FROM "ACTIVIDAD_RESPONSABLES" AR WHERE AR."ID_ACTIVIDAD"=A."ID" AND AR."ID_USUARIO"=?)')
        params.append(usuario_id)
    if fecha_desde:
        filtros.append('A."FECHA_SOLICITUD" >= ?')
        params.append(fecha_desde)
    if fecha_hasta:
        filtros.append('A."FECHA_SOLICITUD" <= ?')
        params.append(fecha_hasta)
    where = ('WHERE ' + ' AND '.join(filtros)) if filtros else ''
    sql = f"""SELECT A."ID", A."NOMBRE_ACTIVIDAD", A."DESCRIPCION",
                     A."FECHA_SOLICITUD", A."SOLICITANTE",
                     A."FECHA_INICIO", A."FECHA_FIN_REAL",
                     A."PRIORIDAD", A."CREADO_EN",
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
              {where} ORDER BY A."PRIORIDAD" ASC, A."CREADO_EN" DESC"""
    return ejecutar_query(sql, tuple(params) if params else None)


def obtener_actividad_por_id(actividad_id):
    sql = """SELECT A."ID", A."NOMBRE_ACTIVIDAD", A."DESCRIPCION",
                    A."FECHA_SOLICITUD", A."SOLICITANTE",
                    A."FECHA_INICIO", A."FECHA_FIN_REAL",
                    A."PRIORIDAD",
                    A."ID_PROYECTO", A."ID_ESTATUS",
                    A."ID_ACTIVIDAD_PADRE",
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
    """Retorna actividades que tienen como padre la actividad dada."""
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
    """Retorna nombre y proyecto de una actividad para mostrar como referencia."""
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
                  "FECHA_SOLICITUD","SOLICITANTE","FECHA_INICIO",
                  "ID_ESTATUS","PRIORIDAD","ID_ACTIVIDAD_PADRE",
                  "CREADO_EN","CREADO_POR")
             VALUES (SYSUUID,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)"""
    return ejecutar_dml(sql, (
        datos.get('id_proyecto'), datos.get('nombre_actividad'),
        datos.get('descripcion') or None,
        datos.get('fecha_solicitud') or None, datos.get('solicitante') or None,
        datos.get('fecha_inicio') or None,
        datos.get('id_estatus'), int(datos.get('prioridad', 2)),
        datos.get('id_actividad_padre') or None,
        datos.get('creado_por', 'SISTEMA'),
    ))


def actualizar_actividad(actividad_id, datos):
    sql = """UPDATE "ACTIVIDADES" SET
                 "ID_PROYECTO"=?,
                 "NOMBRE_ACTIVIDAD"=?, "DESCRIPCION"=?,
                 "FECHA_SOLICITUD"=?, "SOLICITANTE"=?,
                 "FECHA_INICIO"=?, "FECHA_FIN_REAL"=?,
                 "ID_ESTATUS"=?, "PRIORIDAD"=?,
                 "ID_ACTIVIDAD_PADRE"=?,
                 "ACTUALIZADO_EN"=CURRENT_TIMESTAMP, "ACTUALIZADO_POR"=?
             WHERE "ID"=?"""
    return ejecutar_dml(sql, (
        datos.get('id_proyecto'),
        datos.get('nombre_actividad'), datos.get('descripcion') or None,
        datos.get('fecha_solicitud') or None, datos.get('solicitante') or None,
        datos.get('fecha_inicio') or None, datos.get('fecha_fin_real') or None,
        datos.get('id_estatus'), int(datos.get('prioridad', 2)),
        datos.get('id_actividad_padre') or None,
        datos.get('actualizado_por', 'SISTEMA'), actividad_id,
    ))


def guardar_responsables_actividad(actividad_id, ids_usuarios):
    """Reemplaza todos los responsables de una actividad en una sola transacción."""
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
    """Borrado físico en cascada dentro de una sola transacción."""
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            # Desligar hijas antes de borrar (evita FK violation en ID_ACTIVIDAD_PADRE)
            cur.execute('UPDATE "ACTIVIDADES" SET "ID_ACTIVIDAD_PADRE"=NULL WHERE "ID_ACTIVIDAD_PADRE"=?', (actividad_id,))
            # Tablas hijas de ACTIVIDADES
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
        datos.get('notas') or None,
        datos.get('creado_por', 'SISTEMA'),
    ))


def toggle_detalle_completado(detalle_id, completado):
    return ejecutar_dml('UPDATE "DETALLE_ACTIVIDAD" SET "COMPLETADO"=? WHERE "ID"=?',
                        (1 if completado else 0, detalle_id))


def eliminar_detalle_actividad(detalle_id):
    return ejecutar_dml('DELETE FROM "DETALLE_ACTIVIDAD" WHERE "ID"=?', (detalle_id,))


# ══════════════════════════════════════════════════════════════════════════════
#  EVIDENCIA
# ══════════════════════════════════════════════════════════════════════════════

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


def crear_evidencia(actividad_id, datos):
    sql = """INSERT INTO "EVIDENCIA_ACTIVIDAD"
                 ("ID","ID_ACTIVIDAD","ID_TIPO","TITULO","CONTENIDO_TEXTO",
                  "NOMBRE_ARCHIVO","URL_ARCHIVO","MIME_TYPE","TAMANO_BYTES",
                  "SUBIDO_POR","CREADO_EN")
             VALUES (SYSUUID,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"""
    return ejecutar_dml(sql, (
        actividad_id, datos.get('id_tipo'),
        datos.get('titulo') or None, datos.get('contenido_texto') or None,
        datos.get('nombre_archivo') or None, datos.get('url_archivo') or None,
        datos.get('mime_type') or None,
        int(datos['tamano_bytes']) if datos.get('tamano_bytes') else None,
        datos.get('subido_por') or None,
    ))


def eliminar_evidencia(evidencia_id):
    return ejecutar_dml('DELETE FROM "EVIDENCIA_ACTIVIDAD" WHERE "ID"=?', (evidencia_id,))


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGOS AUXILIARES  —  con caché
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  ACTIVIDADES POR PROYECTO
# ══════════════════════════════════════════════════════════════════════════════

def obtener_actividades_por_proyecto(proyecto_id):
    """Solo devuelve actividades que aceptan nuevos registros de horas
    (excluye Completado y Cancelado)."""
    sql = """
        SELECT A."ID", A."NOMBRE_ACTIVIDAD",
               E."DESCRIPCION" as "ESTATUS",
               COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                          WHERE R."ID_ACTIVIDAD"=A."ID"), 0) as "HORAS_INVERTIDAS"
        FROM "ACTIVIDADES" A
        JOIN "CAT_ESTATUS_ACTIVIDAD" E ON A."ID_ESTATUS" = E."ID"
        WHERE A."ID_PROYECTO" = ?
          AND UPPER(E."DESCRIPCION") NOT IN ('COMPLETADO', 'CANCELADO', 'ESPERANDO APROBACIÓN')
        ORDER BY A."NOMBRE_ACTIVIDAD"
    """
    return ejecutar_query(sql, (proyecto_id,))


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


# ══════════════════════════════════════════════════════════════════════════════
#  HISTORIAL DE HORAS
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  GESTIÓN DE REGISTROS
# ══════════════════════════════════════════════════════════════════════════════

def obtener_registros(filtros=None):
    if filtros is None:
        filtros = {}
    params, where = [], []

    if filtros.get('user_id'):
        where.append('"R"."ID_USUARIO" = ?')
        params.append(filtros['user_id'])
    if filtros.get('proyecto_id'):
        where.append('"R"."ID_PROYECTO" = ?')
        params.append(filtros['proyecto_id'])
    if filtros.get('fecha_ini'):
        where.append('"R"."FECHA" >= ?')
        params.append(filtros['fecha_ini'])
    if filtros.get('fecha_fin'):
        where.append('"R"."FECHA" <= ?')
        params.append(filtros['fecha_fin'])

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    sql = f"""
        SELECT R."ID", R."FECHA", R."HORAS", R."ACCION", R."DETALLES",
               U."NOMBRE_COMPLETO" as "USUARIO", U."ID" as "ID_USUARIO",
               P."NOMBRE_PROYECTO", P."ID" as "ID_PROYECTO",
               A."NOMBRE_ACTIVIDAD", A."ID" as "ID_ACTIVIDAD",
               T."DESCRIPCION" as "TIPO", T."ID" as "ID_TIPO_ACT",
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
    return ejecutar_dml('DELETE FROM "REGISTRO_ACTIVIDADES" WHERE "ID" = ?', (registro_id,))


# ══════════════════════════════════════════════════════════════════════════════
#  RECURSOS
# ══════════════════════════════════════════════════════════════════════════════

def obtener_recursos_actividad(actividad_id):
    sql = """
        SELECT R."ID", R."NOMBRE"
        FROM "ACTIVIDAD_RECURSOS" AR
        JOIN "CAT_RECURSOS" R ON AR."ID_RECURSO" = R."ID"
        WHERE AR."ID_ACTIVIDAD" = ?
        ORDER BY R."NOMBRE"
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
    """
    Actualiza ID_PROYECTO en todos los registros de horas de una actividad.
    Se llama cuando el usuario cambia el proyecto de una actividad existente,
    garantizando que las horas históricas queden asociadas al proyecto correcto.
    """
    return ejecutar_dml(
        'UPDATE "REGISTRO_ACTIVIDADES" SET "ID_PROYECTO"=? WHERE "ID_ACTIVIDAD"=?',
        (nuevo_proyecto_id, actividad_id)
    )

# ══════════════════════════════════════════════════════════════════════════════
#  REPORTE EXCEL POR PROYECTO
# ══════════════════════════════════════════════════════════════════════════════

def obtener_datos_reporte_proyecto(proyecto_id):
    """
    Trae en una sola conexion:
      - actividades con todos sus metadatos (incluyendo SOLICITANTE)
      - evidencias agrupadas por actividad
    Devuelve: (actividades, evidencias_por_actividad)
    """
    sql_acts = """
        SELECT
            A."ID",
            A."NOMBRE_ACTIVIDAD",
            A."DESCRIPCION",
            A."FECHA_SOLICITUD",
            A."FECHA_INICIO",
            A."FECHA_FIN_REAL",
            A."PRIORIDAD",
            A."SOLICITANTE",
            A."ID_ACTIVIDAD_PADRE",
            PADRE."NOMBRE_ACTIVIDAD" AS "NOMBRE_ACTIVIDAD_PADRE",
            E."DESCRIPCION"  AS "ESTATUS",
            P."NOMBRE_PROYECTO",
            COALESCE((SELECT SUM(R."HORAS") FROM "REGISTRO_ACTIVIDADES" R
                       WHERE R."ID_ACTIVIDAD" = A."ID"), 0) AS "HORAS_TOTALES",
            (SELECT STRING_AGG(U2."NOMBRE_COMPLETO", ' / ')
             FROM "ACTIVIDAD_RESPONSABLES" AR
             JOIN "USUARIOS" U2 ON AR."ID_USUARIO" = U2."ID"
             WHERE AR."ID_ACTIVIDAD" = A."ID") AS "RESPONSABLES",
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
        WHERE A."ID_PROYECTO" = ?
        ORDER BY A."PRIORIDAD" ASC, A."FECHA_SOLICITUD" ASC
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
        ORDER BY EV."CREADO_EN" ASC
    """
    with _pool.get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql_acts, (proyecto_id,))
            cols_a = [c[0] for c in cur.description]
            actividades = [dict(zip(cols_a, row)) for row in cur.fetchall()]

            cur.execute(sql_evs, (proyecto_id,))
            cols_e = [c[0] for c in cur.description]
            evidencias_por_actividad = {}
            for row in cur.fetchall():
                ev = dict(zip(cols_e, row))
                act_id = ev["ID_ACTIVIDAD"]
                evidencias_por_actividad.setdefault(act_id, []).append(ev)

            return actividades, evidencias_por_actividad
        finally:
            cur.close()



# ══════════════════════════════════════════════════════════════════════════════
#  CRUD CATÁLOGOS — administración completa
# ══════════════════════════════════════════════════════════════════════════════

# ── Helpers internos ──────────────────────────────────────────────────────────

def _cat_toggle(tabla: str, id_val: str, activo: int) -> bool:
    return ejecutar_dml(f'UPDATE "{tabla}" SET "ACTIVO"=? WHERE "ID"=?', (activo, id_val))

def _cat_delete(tabla: str, id_val: str) -> bool:
    return ejecutar_dml(f'DELETE FROM "{tabla}" WHERE "ID"=?', (id_val,))

def _cat_in_use(tabla_ref: str, col_fk: str, id_val: str) -> bool:
    """Verifica si un registro de catálogo está referenciado en otra tabla."""
    rows = ejecutar_query(f'SELECT COUNT(*) AS N FROM "{tabla_ref}" WHERE "{col_fk}"=?', (id_val,))
    return bool(rows and int(rows[0]["N"]) > 0)


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