from datetime import datetime

def _parse_iso_date(value: str | None, field_name: str) -> str | None:
    """Valida fecha en formato YYYY-MM-DD y retorna el mismo valor si es válido."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        raise ValueError(f"{field_name} debe tener formato YYYY-MM-DD.")


def _normalizar_campos_dashboard_actividad(datos: dict) -> tuple[dict, str | None]:
    """Normaliza y valida campos extendidos de actividad para dashboard."""
    datos["friendly_name"] = (datos.get("friendly_name") or "").strip() or None

    try:
        datos["fecha_inicio"] = _parse_iso_date(datos.get("fecha_inicio"), "Fecha de inicio")
        datos["fecha_fin_est"] = _parse_iso_date(datos.get("fecha_fin_est"), "Fecha fin estimada")
        datos["fecha_fin_real"] = _parse_iso_date(datos.get("fecha_fin_real"), "Fecha fin real")
    except ValueError as exc:
        return datos, str(exc)

    dias_acordados = (datos.get("dias_acordados") or "").strip()
    avance_pct = (datos.get("avance_pct") or "").strip()

    if datos.get("fecha_inicio") and datos.get("fecha_fin_est"):
        f_inicio = datetime.strptime(datos["fecha_inicio"], "%Y-%m-%d").date()
        f_fin_est = datetime.strptime(datos["fecha_fin_est"], "%Y-%m-%d").date()
        if f_fin_est < f_inicio:
            return datos, "La fecha fin estimada no puede ser menor a la fecha de inicio."
        datos["dias_acordados"] = str((f_fin_est - f_inicio).days + 1)
    elif dias_acordados:
        if not dias_acordados.isdigit():
            return datos, "Los días acordados deben ser un número entero."
        dias = int(dias_acordados)
        if dias < 1 or dias > 365:
            return datos, "Los días acordados deben estar entre 1 y 365."
        datos["dias_acordados"] = str(dias)
    else:
        datos["dias_acordados"] = None

    if avance_pct:
        if not avance_pct.isdigit():
            return datos, "El porcentaje de avance debe ser un número entero."
        avance = int(avance_pct)
        if avance < 0 or avance > 100:
            return datos, "El porcentaje de avance debe estar entre 0 y 100."
        datos["avance_pct"] = str(avance)
    else:
        datos["avance_pct"] = "0"

    if datos.get("fecha_inicio") and datos.get("fecha_fin_real"):
        f_inicio = datetime.strptime(datos["fecha_inicio"], "%Y-%m-%d").date()
        f_fin_real = datetime.strptime(datos["fecha_fin_real"], "%Y-%m-%d").date()
        if f_fin_real < f_inicio:
            return datos, "La fecha fin real no puede ser menor a la fecha de inicio."

    return datos, None


def organizar_actividades_para_vista(actividades: list) -> list:
    """Agrupa hijas bajo su padre cuando ambas están en la misma página de resultados."""
    if not actividades:
        return []

    ids_on_page = {a["ID"] for a in actividades}
    children_by_parent: dict = {}
    for a in actividades:
        padre_id = a.get("ID_ACTIVIDAD_PADRE")
        if padre_id and padre_id in ids_on_page:
            children_by_parent.setdefault(padre_id, []).append(a)

    placed: set = set()
    result: list = []

    for a in actividades:
        if a["ID"] in placed:
            continue

        padre_id = a.get("ID_ACTIVIDAD_PADRE")
        if padre_id and padre_id in ids_on_page:
            continue

        item = dict(a)
        item["_nivel"] = 0
        hijas_en_pagina = children_by_parent.get(a["ID"], [])
        if hijas_en_pagina:
            item["_es_padre_en_vista"] = True
            item["_hijas_en_pagina"] = [
                {**h, "_nivel": 1, "_padre_en_pagina": True} for h in hijas_en_pagina
            ]
            for h in hijas_en_pagina:
                placed.add(h["ID"])
        elif padre_id and padre_id not in ids_on_page:
            item["_es_hija_suelta"] = True

        result.append(item)
        placed.add(a["ID"])

    return result
