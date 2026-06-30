import io
import os
from datetime import datetime
from flask import Blueprint, request, jsonify, send_file, current_app

from web_app.modules.tracker.routes import _catalogo_base, _registros_filters_from_request, _registros_activity_options, _catalog_label, _fmt_fecha_corta
from web_app.modules.reports.queries import obtener_datos_reporte_proyecto
from web_app.modules.reports.services import generar_reporte_proyecto_xlsx, generar_reporte_ausencias

reports_bp = Blueprint("reports_bp", __name__)


def _build_registros_export_context(filtros, filtros_meta, base, actividades=None):
    from web_app.modules.catalogos.queries import obtener_tipos_actividad
    
    proyecto_nombre = _catalog_label(
        base["projects"], filtros.get("proyecto_id"), "ID", "NOMBRE_PROYECTO", fallback="Sin proyecto"
    )
    desarrollador = _catalog_label(
        base["users"], filtros.get("user_id"), "ID", "NOMBRE_COMPLETO"
    )
    actividades = actividades if actividades is not None else _registros_activity_options(filtros)
    actividad = _catalog_label(
        actividades, filtros.get("actividad_id"), "ID", "NOMBRE_ACTIVIDAD", fallback="Todas"
    )
    tipos = obtener_tipos_actividad()
    tipo = _catalog_label(tipos, filtros.get("tipo_id"), "ID", "DESCRIPCION")
    fecha_ini = _fmt_fecha_corta(filtros.get("fecha_ini"))
    fecha_fin = _fmt_fecha_corta(filtros.get("fecha_fin"))
    origen_rango = "Mes actual por defecto" if filtros_meta.get("usando_mes_actual_default") else "Rango seleccionado"

    return {
        "proyecto": proyecto_nombre,
        "desarrollador": desarrollador,
        "actividad": actividad,
        "tipo": tipo,
        "fecha_ini": fecha_ini,
        "fecha_fin": fecha_fin,
        "origen_rango": origen_rango,
        "scope_label": (
            f"Proyecto: {proyecto_nombre} · Desarrollador: {desarrollador} · "
            f"Actividad: {actividad} · Tipo: {tipo} · "
            f"Rango: {fecha_ini} a {fecha_fin} · {origen_rango}"
        ),
        "scope_label_short": (
            f"Desarrollador: {desarrollador} · Actividad: {actividad} · Tipo: {tipo} · "
            f"Rango: {fecha_ini} a {fecha_fin}"
        ),
    }


@reports_bp.route("/reporte/excel/<proyecto_id>")
def exportar_reporte_excel(proyecto_id):
    base = _catalogo_base()
    filtros, filtros_meta = _registros_filters_from_request(request.args, proyecto_id=proyecto_id)
    actividades_catalogo = _registros_activity_options(filtros)
    proyecto = next((p for p in base["projects"] if str(p["ID"]) == str(filtros["proyecto_id"])), None)
    if not proyecto:
        return jsonify({"error": "Proyecto no encontrado"}), 404
    actividades, evidencias_por_actividad, resumen_desarrolladores = obtener_datos_reporte_proyecto(
        filtros["proyecto_id"], filtros
    )
    nombre = proyecto["NOMBRE_PROYECTO"]
    # Config upload base references root static uploads folder
    upload_base = os.path.join(current_app.static_folder, "uploads", "evidencias")
    export_context = _build_registros_export_context(
        filtros, filtros_meta, base, actividades_catalogo
    )
    xlsx_bytes = generar_reporte_proyecto_xlsx(
        nombre,
        actividades,
        evidencias_por_actividad,
        upload_base,
        export_context=export_context,
        resumen_desarrolladores=resumen_desarrolladores,
    )
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=(
            f'Reporte_{nombre.replace(" ","_")}'
            f'_{export_context.get("fecha_ini","").replace("/","-")}'
            f'_al_{export_context.get("fecha_fin","").replace("/","-")}.xlsx'
        ),
    )


@reports_bp.route("/reporte/ausencias/excel")
def reporte_ausencias_excel():
    """Exporta un reporte Excel de ausencias del mes visible en Calendario."""
    from web_app.modules.reports.excel_ausencias_reporte import TIPOS_VALIDOS
    from datetime import date as _date

    try:
        anio = int(request.args.get("anio", _date.today().year))
        mes  = int(request.args.get("mes",  _date.today().month))
        tipo = request.args.get("tipo", "").strip().upper()
    except (ValueError, TypeError):
        return jsonify({"error": "Parámetros inválidos."}), 400

    if not (2020 <= anio <= 2100) or not (1 <= mes <= 12):
        return jsonify({"error": "Año o mes fuera de rango."}), 400

    if tipo and tipo not in TIPOS_VALIDOS:
        return jsonify({"error": f"Tipo de ausencia no válido: {tipo!r}."}), 400

    try:
        from web_app.modules.dashboard.queries import obtener_ausencias_mes
        base      = _catalogo_base()
        ausencias = obtener_ausencias_mes(anio, mes)
        xlsx      = generar_reporte_ausencias(anio, mes, base["users"], ausencias, tipo)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        return jsonify({"error": "Error interno al generar el reporte."}), 500

    tipo_sufijo = f"_{tipo}" if tipo else ""
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"Ausencias{tipo_sufijo}_{anio}_{mes:02d}.xlsx",
    )