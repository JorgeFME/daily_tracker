-- ============================================================
-- CATALOGO DE SOLICITANTES
-- Ejecutar en SAP HANA Cloud antes de usar el nuevo catalogo
-- ============================================================

CREATE COLUMN TABLE "CAT_SOLICITANTES" (
    "ID"         NVARCHAR(36)  NOT NULL DEFAULT SYSUUID,
    "NOMBRE"     NVARCHAR(150) NOT NULL,
    "ACTIVO"     TINYINT       DEFAULT 1 NOT NULL,
    "CREADO_EN"  LONGDATE      DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("ID")
) UNLOAD PRIORITY 5 AUTO MERGE;
