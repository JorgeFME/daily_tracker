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


def _validar_fecha_hasta_fin_semana_actual(fecha_obj: date) -> bool:
    """Permite fechas históricas y bloquea solo fechas posteriores al fin de semana actual."""
    _, fin = _semana_actual_limites()
    return fecha_obj <= fin
