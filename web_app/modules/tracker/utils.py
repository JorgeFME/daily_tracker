import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

def _local_today() -> date:
    """Fecha local configurable para evitar desfases entre servidor y operación."""
    tz_name = os.getenv("APP_TIMEZONE", "America/Mexico_City")
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return date.today()


def _semana_actual_limites() -> tuple[date, date]:
    """Regresa el rango lunes-domingo de la semana local actual."""
    hoy = _local_today()
    inicio = hoy - timedelta(days=hoy.weekday())
    fin = inicio + timedelta(days=6)
    return inicio, fin


def _fecha_maxima_registro() -> date:
    """Última fecha permitida para registros: hoy más siete días calendario."""
    return _local_today() + timedelta(days=7)


def _validar_fecha_hasta_siete_dias(fecha_obj: date) -> bool:
    """Permite fechas pasadas y hasta siete días calendario después de hoy."""
    return fecha_obj <= _fecha_maxima_registro()
