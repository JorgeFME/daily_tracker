import os
from flask import Flask
from config import Config

def create_app():
    # Configure Flask to use the root 'static' directory relative to this package
    app = Flask(
        __name__,
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    )
    app.config.from_object(Config)

    # Register Jinja2 filter 'strftime'
    @app.template_filter('strftime')
    def _filter_strftime(value, fmt='%Y-%m-%d'):
        """Formatea un objeto date/datetime o string ISO al formato indicado."""
        if value is None:
            return ''
        if hasattr(value, 'strftime'):
            return value.strftime(fmt)
        try:
            from datetime import date as _date
            return _date.fromisoformat(str(value)[:10]).strftime(fmt)
        except Exception:
            return str(value)

    # Register Blueprints
    from web_app.modules.tracker.routes import tracker_bp
    from web_app.modules.dashboard.routes import dashboard_bp
    from web_app.modules.reports.routes import reports_bp
    from web_app.modules.evidencias.routes import evidencias_bp
    from web_app.modules.catalogos.routes import catalogos_bp

    app.register_blueprint(tracker_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(evidencias_bp)
    app.register_blueprint(catalogos_bp)

    return app
