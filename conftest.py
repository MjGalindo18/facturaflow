"""
conftest.py — configuración global de pytest para FacturaFlow.

No agrega las carpetas de funciones Lambda al sys.path porque todas se
llaman 'handler.py' y colisionarían entre sí. Cada test file carga su
handler mediante importlib con un nombre de módulo único.
"""
