import unittest

from web_app.modules.tracker.queries import _validar_limite_diario


class CursorFalso:
    def __init__(self, resultados):
        self.resultados = list(resultados)
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return self.resultados.pop(0)


class LimiteHorasTests(unittest.TestCase):
    def test_bloquea_y_permite_horas_disponibles(self):
        cursor = CursorFalso([(3.0,), (2.0,)])

        _validar_limite_diario(cursor, "usuario-1", "2026-09-22", "3")

        self.assertIn('LOCK TABLE "REGISTRO_ACTIVIDADES" IN EXCLUSIVE MODE', cursor.sql[0][0])

    def test_rechaza_horas_que_superan_saldo(self):
        cursor = CursorFalso([(6.0,), (1.0,)])

        with self.assertRaisesRegex(ValueError, "Disponibles: 1.00 h"):
            _validar_limite_diario(cursor, "usuario-1", "2026-09-22", "1.5")


if __name__ == "__main__":
    unittest.main()
