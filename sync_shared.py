import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "shared"

TARGETS = [
    "functions/recibir_zip/shared",
    "functions/procesar_factura/shared",
    "functions/notificar_analista/shared",
    "functions/motor_ia_mock/shared",
]

files = [f for f in SRC.iterdir() if f.is_file() and f.suffix == ".py"]

for target_path in TARGETS:
    dest = ROOT / target_path
    dest.mkdir(parents=True, exist_ok=True)
    for src_file in files:
        shutil.copy2(src_file, dest / src_file.name)
        print(f"  copiado: {src_file.name} -> {target_path}/")
    print(f"[OK] {target_path}")
    print()

print(f"Sincronizacion completa: {len(files)} archivo(s) -> {len(TARGETS)} destinos.")
