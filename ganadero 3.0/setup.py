"""
setup.py — Configuración inicial del proyecto Rosgan

Corre este script UNA SOLA VEZ para dejar todo listo:
  1. Crea las carpetas necesarias
  2. Descarga todos los remates de 2026
  3. Los importa a la base de datos
  4. Abre el dashboard

Uso:
    python setup.py
"""

import subprocess
import sys
from pathlib import Path

def paso(n, texto):
    print(f"\n{'='*50}")
    print(f"  Paso {n}: {texto}")
    print(f"{'='*50}")

def correr(cmd, descripcion=""):
    print(f"  → {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"  ⚠️  Hubo un problema. Continuando...")
    return result.returncode == 0

# ── Paso 1: Crear carpetas ────────────────────────────────────────────────────
paso(1, "Creando estructura de carpetas")
Path("data").mkdir(exist_ok=True)
Path("scrapers").mkdir(exist_ok=True)
print("  ✅ Carpetas listas")

# ── Paso 2: Instalar dependencias ─────────────────────────────────────────────
paso(2, "Instalando dependencias Python")
correr(f"{sys.executable} -m pip install -r requirements.txt -q")
correr(f"{sys.executable} -m pip install playwright -q")
print("  ✅ Dependencias instaladas")

# ── Paso 3: Instalar Chromium ─────────────────────────────────────────────────
paso(3, "Instalando Chromium (browser para el scraper)")
print("  Esto puede tardar 1-2 minutos la primera vez...")
correr(f"{sys.executable} -m playwright install chromium")
print("  ✅ Chromium listo")

# ── Paso 4: Importar Excel local si existe ────────────────────────────────────
paso(4, "Buscando Excel de Rosgan en la carpeta")
import re
patron = re.compile(r"Precios_Rosgan_(\d{4})_Remate_(\d+)\.xlsx", re.IGNORECASE)
excels = [f for f in Path(".").glob("*.xlsx") if patron.match(f.name)]

if excels:
    for excel in excels:
        m = patron.match(excel.name)
        anio, numero = m.group(1), m.group(2)
        print(f"  📄 Encontrado: {excel.name}")
        correr(
            f"{sys.executable} scrapers/rosgan.py "
            f"--local {excel} --remate {numero} --anio {anio}"
        )
else:
    print("  ℹ️  No hay Excel en esta carpeta todavía — se descargarán en el paso 5")

# ── Paso 5: Descargar remates de 2026 ─────────────────────────────────────────
paso(5, "Descargando todos los remates de 2026 desde Rosgan")
correr(
    f"{sys.executable} scrapers/rosgan_downloader.py --anio 2026 --importar"
)

# ── Paso 6: Abrir el dashboard ────────────────────────────────────────────────
paso(6, "Abriendo el dashboard")
print("  El dashboard se va a abrir en tu navegador.")
print("  Para cerrarlo después, presioná Ctrl+C en esta terminal.")
print()
correr(f"{sys.executable} -m streamlit run app.py")
