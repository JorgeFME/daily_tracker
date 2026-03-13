"""
Generador de reporte Excel por proyecto.
Una sola hoja · Una fila por actividad · Evidencias incrustadas (imágenes)
o vinculadas (otros tipos).
"""
import io
import os
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage

# ── Paleta ──────────────────────────────────────────────────────────────────
C_DARK    = "1E293B"
C_MID     = "334155"
C_BLUE    = "2563EB"
C_LBLUE   = "DBEAFE"
C_GRAY    = "F8FAFC"
C_WHITE   = "FFFFFF"
C_ALTA    = "FEE2E2"
C_MEDIA   = "FEF9C3"
C_BAJA    = "DCFCE7"
C_LINKED  = "F0F4FF"   # fondo columna actividades ligadas
C_PARENT  = "EDE9FE"   # fondo cuando es hija de otra

ESTATUS_BG = {
    "en análisis":               "EFF6FF",
    "en proceso":                "DBEAFE",
    "en espera de info del cliente": "FEF3C7",
    "completado":                "DCFCE7",
    "cancelado":                 "F1F5F9",
}

THIN   = Side(style="thin",   color="CBD5E1")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
C_ALN  = Alignment(horizontal="center", vertical="center", wrap_text=True)
L_ALN  = Alignment(horizontal="left",   vertical="top",    wrap_text=True)
R_ALN  = Alignment(horizontal="right",  vertical="center", wrap_text=True)

def _fill(c): return PatternFill("solid", fgColor=c, start_color=c)
def _font(bold=False, color=C_DARK, size=9): return Font(name="Arial", bold=bold, color=color, size=size)

def _prioridad(v):
    return {1: "Alta", 2: "Media", 3: "Baja"}.get(int(v), "—") if v is not None else "—"

def _fecha(v):
    if v is None: return "—"
    s = str(v); return s[:10] if len(s) >= 10 else s

def _safe(v): return str(v) if v is not None else "—"


# ── Imagen helpers ───────────────────────────────────────────────────────────

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

def _disk_path(url_archivo: str, upload_base: str) -> str | None:
    """
    Convierte la URL relativa guardada en BD a ruta absoluta en disco.
    URL ejemplo: /uploads/evidencias/<proyecto_id>/<actividad_id>/<file>
    En disco:    <upload_base>/<proyecto_id>/<actividad_id>/<file>
    """
    if not url_archivo:
        return None
    # Quitar el prefijo /uploads/evidencias/
    rel = url_archivo.lstrip("/")
    if rel.startswith("uploads/evidencias/"):
        rel = rel[len("uploads/evidencias/"):]
    full = os.path.join(upload_base, rel)
    return full if os.path.isfile(full) else None


def _embed_image(ws, img_path: str, row: int, col: int, max_w=120, max_h=90):
    """Incrusta una imagen en la celda indicada, redimensionando proporcionalmente."""
    try:
        from PIL import Image as PILImage
        with PILImage.open(img_path) as pil:
            w, h = pil.size
        scale = min(max_w / w, max_h / h, 1.0)
        img = XLImage(img_path)
        img.width  = int(w * scale)
        img.height = int(h * scale)
        cell_ref = f"{get_column_letter(col)}{row}"
        ws.add_image(img, cell_ref)
        return True
    except Exception as e:
        print(f"[excel] no se pudo incrustar imagen {img_path}: {e}")
        return False


# ── Generador principal ──────────────────────────────────────────────────────

def generar_reporte(
    proyecto_nombre: str,
    actividades: list,
    evidencias_por_actividad: dict,
    upload_base: str = "",
) -> bytes:
    """
    Genera el Excel en memoria y devuelve bytes.

    Columnas (en orden):
      1  Nombre de la actividad
      2  Recursos utilizados
      3  Estatus
      4  Fecha de solicitud
      5  Fecha de inicio
      6  Fecha fin
      7  Prioridad
      8  Horas totales
      9  Responsable/s
      10 Solicitante
      11 Descripción
      12 Evidencia adjunta
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Actividades"

    # ── Fila 1: título del proyecto ──────────────────────────────────────────
    ws.merge_cells("A1:M1")
    ws["A1"] = proyecto_nombre
    ws["A1"].font      = Font(name="Arial", bold=True, size=16, color=C_WHITE)
    ws["A1"].fill      = _fill(C_DARK)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws["A1"].border    = BORDER
    ws.row_dimensions[1].height = 36

    # ── Fila 2: fecha de generación ──────────────────────────────────────────
    ws.merge_cells("A2:M2")
    ws["A2"] = f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    ws["A2"].font      = Font(name="Arial", size=8, color="94A3B8")
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 16

    # ── Fila 3: encabezados ──────────────────────────────────────────────────
    HEADERS = [
        ("Nombre de la actividad",  38),
        ("Recursos utilizados",     22),
        ("Estatus",                 16),
        ("Fecha solicitud",         14),
        ("Fecha inicio",            13),
        ("Fecha fin",               13),
        ("Prioridad",               11),
        ("Horas totales",           13),
        ("Responsable/s",           26),
        ("Solicitante",             18),
        ("Descripción",             42),
        ("Evidencia adjunta",       38),
        ("Actividades ligadas",      32),
    ]

    ws.row_dimensions[3].height = 32
    for ci, (header, width) in enumerate(HEADERS, start=1):
        c = ws.cell(row=3, column=ci, value=header)
        c.font      = _font(bold=True, color=C_WHITE)
        c.fill      = _fill(C_MID)
        c.alignment = C_ALN
        c.border    = BORDER
        ws.column_dimensions[get_column_letter(ci)].width = width

    ws.freeze_panes = "A4"

    # ── Filas de datos ───────────────────────────────────────────────────────
    IMG_ROW_H  = 80   # altura en puntos para filas con imagen
    STD_ROW_H  = 52   # altura estándar

    has_pil = True
    try:
        import PIL  # noqa
    except ImportError:
        has_pil = False

    for ri, a in enumerate(actividades, start=4):
        act_id    = a.get("ID")
        prio_txt  = _prioridad(a.get("PRIORIDAD"))
        est_txt   = _safe(a.get("ESTATUS"))
        horas     = float(a.get("HORAS_TOTALES") or 0)
        row_bg    = C_LBLUE if ri % 2 == 0 else C_GRAY

        # ── Columna 12: construir texto de evidencia ─────────────────────────
        evs = evidencias_por_actividad.get(act_id, [])
        ev_lines = []
        has_image = False
        first_img_path = None

        for ev in evs:
            nombre = ev.get("NOMBRE_ARCHIVO") or ev.get("TITULO") or ev.get("TIPO") or "Evidencia"
            url    = ev.get("URL_ARCHIVO") or ""
            mime   = (ev.get("MIME_TYPE") or "").lower()
            is_img = (
                mime.startswith("image/") or
                any(url.lower().endswith(ext) for ext in IMAGE_EXTS)
            )
            if is_img and not has_image and upload_base:
                disk = _disk_path(url, upload_base)
                if disk:
                    first_img_path = disk
                    has_image = True
                    ev_lines.append(f"🖼 {nombre}")
                else:
                    ev_lines.append(f"• {nombre}")
            else:
                ev_lines.append(f"• {nombre}")

        if evs and not has_image:
            ev_lines.append("")
            ev_lines.append("Ver evidencia completa en el dashboard.")

        ev_text = "\n".join(ev_lines) if ev_lines else "Sin evidencia"

        # ── Datos de la fila ─────────────────────────────────────────────────
        # ── Columna 13: actividades ligadas ─────────────────────────────────
        num_hijas   = int(a.get("NUM_HIJAS") or 0)
        nombres_hijas = a.get("NOMBRES_HIJAS") or ""
        padre_id    = a.get("ID_ACTIVIDAD_PADRE")
        nombre_padre = a.get("NOMBRE_ACTIVIDAD_PADRE") or f"ID {padre_id}"

        linked_parts = []

        if padre_id:
            linked_parts.append(f"🪜Derivada de:")
            linked_parts.append(f"    🔴{nombre_padre}")

        if num_hijas > 0:
            if padre_id:
                linked_parts.append("")   # separador entre secciones
            etiqueta = "actividad derivada" if num_hijas == 1 else "actividades derivadas"
            linked_parts.append(f"📝 {num_hijas} {etiqueta}:")
            if nombres_hijas:
                for nombre_h in nombres_hijas.split(" | "):
                    linked_parts.append(f"⚫ {nombre_h.strip()}")

        linked_text = "\n".join(linked_parts) if linked_parts else "—"

        row_data = [
            _safe(a.get("NOMBRE_ACTIVIDAD")),    # 1
            _safe(a.get("RECURSOS")),             # 2
            est_txt,                              # 3
            _fecha(a.get("FECHA_SOLICITUD")),     # 4
            _fecha(a.get("FECHA_INICIO")),        # 5
            _fecha(a.get("FECHA_FIN_REAL")),      # 6
            prio_txt,                             # 7
            horas,                                # 8
            _safe(a.get("RESPONSABLES")),         # 9
            _safe(a.get("SOLICITANTE")),          # 10
            _safe(a.get("DESCRIPCION")),          # 11
            ev_text,                              # 12
            linked_text,                          # 13
        ]

        row_h = IMG_ROW_H if has_image and has_pil else STD_ROW_H
        ws.row_dimensions[ri].height = row_h

        for ci, val in enumerate(row_data, start=1):
            cell = ws.cell(row=ri, column=ci, value=val)
            cell.border = BORDER
            cell.font   = _font()
            cell.fill   = _fill(row_bg)

            # Alineación y formatos especiales por columna
            if ci in (4, 5, 6):       # fechas
                cell.alignment = C_ALN
            elif ci == 7:              # prioridad
                cell.alignment = C_ALN
                bg = {"Alta": C_ALTA, "Media": C_MEDIA, "Baja": C_BAJA}.get(prio_txt, C_WHITE)
                cell.fill = _fill(bg)
                color = {"Alta": "DC2626", "Media": "D97706", "Baja": "16A34A"}.get(prio_txt, C_DARK)
                cell.font = _font(bold=True, color=color)
            elif ci == 8:              # horas
                cell.alignment  = C_ALN
                cell.number_format = "#,##0.0"
            elif ci == 3:              # estatus
                cell.alignment = C_ALN
                bg = ESTATUS_BG.get(est_txt.lower(), C_WHITE)
                cell.fill = _fill(bg)
            else:
                cell.alignment = L_ALN

        # Columna 13: formato especial para actividades ligadas
        cell13 = ws.cell(row=ri, column=13)
        if padre_id and num_hijas > 0:
            cell13.fill = _fill("E9D5FF")   # morado: es hija Y tiene hijas
        elif padre_id:
            cell13.fill = _fill(C_PARENT)   # violeta suave: es hija de otra
        elif num_hijas > 0:
            cell13.fill = _fill(C_LINKED)   # azul claro: tiene hijas propias
            cell13.font = _font(bold=True, color="2563EB")

        # Incrustar imagen en columna 12 si aplica
        if has_image and has_pil and first_img_path:
            # Limpiar el texto de la celda 12 si hay imagen
            ws.cell(row=ri, column=12).value = None
            _embed_image(ws, first_img_path, ri, 12)

    # ── Fila de total ────────────────────────────────────────────────────────
    total_row = len(actividades) + 4
    ws.row_dimensions[total_row].height = 20
    ws.merge_cells(f"A{total_row}:G{total_row}")

    tc = ws.cell(row=total_row, column=1, value="TOTAL HORAS")
    tc.font      = _font(bold=True, color=C_WHITE)
    tc.fill      = _fill(C_MID)
    tc.alignment = R_ALN
    tc.border    = BORDER

    th = ws.cell(row=total_row, column=8, value=f"=SUM(H4:H{total_row-1})")
    th.font         = _font(bold=True, color=C_WHITE)
    th.fill         = _fill(C_BLUE)
    th.alignment    = C_ALN
    th.number_format = "#,##0.0"
    th.border       = BORDER

    for ci in range(9, 14):
        c = ws.cell(row=total_row, column=ci)
        c.fill   = _fill(C_MID)
        c.border = BORDER

    # ── Nota al pie ──────────────────────────────────────────────────────────
    note_row = total_row + 2
    ws.merge_cells(f"A{note_row}:M{note_row}")
    note = ws.cell(
        row=note_row, column=1,
        value="ℹ️  Las evidencias de tipo documento, correo o nota pueden consultarse en detalle desde el dashboard del proyecto."
    )
    note.font      = Font(name="Arial", size=8, italic=True, color="94A3B8")
    note.alignment = Alignment(horizontal="left", vertical="center")

    # ── Serializar ───────────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()