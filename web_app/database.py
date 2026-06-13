import os
import json
import threading
import time
from queue import Queue, Empty
from hdbcli import dbapi
from datetime import datetime, timedelta
from config import Config

# ══════════════════════════════════════════════════════════════════════════════
#  SAP HANA CREDENTIALS
# ══════════════════════════════════════════════════════════════════════════════

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
        "host":     os.getenv("HANA_HOST") or Config.HANA_HOST,
        "port":     int(os.getenv("HANA_PORT")) if os.getenv("HANA_PORT") else Config.HANA_PORT,
        "user":     os.getenv("HANA_USER") or Config.HANA_USER,
        "password": os.getenv("HANA_PASSWORD") or Config.HANA_PASSWORD,
        "schema":   os.getenv("HANA_SCHEMA") or Config.HANA_SCHEMA,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CONNECTION POOL
# ══════════════════════════════════════════════════════════════════════════════

class HanaConnectionPool:
    def __init__(self, size: int = 5):
        self._size       = size
        self._pool       = Queue(maxsize=size)
        self._lock       = threading.Lock()
        self._creds      = None          # se inicializa al primer uso
        self._initialized = False

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

    def get_connection(self, timeout: float = 10.0):
        if not self._initialized:
            self._init_pool()
        return _PooledConnection(self, timeout)

    def _acquire(self, timeout: float):
        try:
            conn = self._pool.get(timeout=timeout)
        except Empty:
            raise TimeoutError("No hay conexiones disponibles en el pool (timeout).")
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
            try:
                conn.close()
            except Exception:
                pass


class _PooledConnection:
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
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            self._pool._release(self._conn)
        return False


# Instancia global del pool (creada al importar el módulo)
_pool = HanaConnectionPool(size=Config.HANA_POOL_SIZE)


# ── Helpers de bajo nivel ──────────────────────────────────────────────────

def get_hana_connection():
    if not _pool._initialized:
        _pool._init_pool()
    conn = _pool._acquire(timeout=10.0)
    return _ReleasableConn(_pool, conn)


class _ReleasableConn:
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
# ══════════════════════════════════════════════════════════════════════════════

_cache: dict = {}
_cache_lock  = threading.Lock()
CACHE_TTL    = Config.CATALOG_CACHE_TTL


def _cached_query(cache_key: str, query: str, params=None):
    """Ejecuta una query y cachea el resultado. Invalida por TTL."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (now - entry["ts"]) < CACHE_TTL:
            return entry["data"]
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
