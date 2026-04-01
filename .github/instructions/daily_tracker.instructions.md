---
name: daily-tracker-coding-standards
applyTo: "**/*"
description: "Use when implementing or refactoring features for the daily_tracker app: prefer clean Flask + SQLAlchemy patterns, clear form validation, and edge-case handling. Siempre haz uso de las mejores prácticas."
---

# Daily Tracker Workspace Instructions

## Objetivo
Garantizar consistencia y calidad en el código de este proyecto Python/Flask + frontend templating.

## Reglas clave (mejores prácticas)
1. Validación en backend
   - Validate required fields, types and business rules in routes/controllers before DB ops.
   - Return clear flash/messages and HTTP status when el cliente envía datos inválidos.

2. SQL y DB
   - Usa parámetros en consultas `?` o SQLAlchemy ORM para evitar SQL injection.
   - Preferir `db.session` + transacciones explícitas en operaciones críticas.

3. Seguridad
   - Sanitiza datos de usuario para templates y evita inyecciones XSS.
   - Usa CSRF token si se añade WTForms/Flask-WTF.

4. Frontend
   - Reutiliza componentes HTML/CSS existentes (estilos globales del proyecto).
   - Mantén JS modular y sin lógica de presentación duplicada.

5. Calidad de código
   - Funciones con responsabilidad única (SRP).
   - Docs y comentarios para rutas, funciones complejas y cálculos de tiempo.
   - Añade tests unitarios o integrados para bugfixes y nuevas features.

## Ejemplo de prompts útiles para el agente
- "Implementa validación en `app.py` para que `hours` esté entre 0.25 y 12 y se redondee a 0.25"
- "Refactoriza `templates/index.html` para mejorar accesibilidad `aria` y evitar etiquetas obsoletas"
- "Agrega manejo de error `try/except` en `excel_reporte.py` para el archivo no encontrado"

## Siguientes pasos
- Después de guardar este archivo, prueba generando código con un prompt tipo:
  "Refactoriza la API de actividad a método `create_activity` siguiendo los estándares del archivo de instrucciones".
