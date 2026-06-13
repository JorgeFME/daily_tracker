import os

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

# Cargar variables de entorno al importar la configuración
load_env_from_dotenv()

class Config:
    # SAP HANA Connection Parameters
    HANA_HOST = os.getenv("HANA_HOST")
    HANA_PORT = int(os.getenv("HANA_PORT")) if os.getenv("HANA_PORT") else None
    HANA_USER = os.getenv("HANA_USER")
    HANA_PASSWORD = os.getenv("HANA_PASSWORD")
    HANA_SCHEMA = os.getenv("HANA_SCHEMA")
    HANA_POOL_SIZE = int(os.getenv("HANA_POOL_SIZE", "5"))

    # Catalog cache configuration
    CATALOG_CACHE_TTL = int(os.getenv("CATALOG_CACHE_TTL", "300"))

    # Timezone configuration
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/Mexico_City")

    # File uploads limits
    MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "20"))
    MAX_PROJECT_MB = int(os.getenv("MAX_PROJECT_MB", "500"))
    MAX_CONTENT_LENGTH = MAX_FILE_MB * 1024 * 1024

    ALLOWED_EXTENSIONS = {
        # Capturas
        "png", "jpg", "jpeg", "gif", "webp", "bmp",
        # Correos
        "eml", "msg",
        # Documentos
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
    }
