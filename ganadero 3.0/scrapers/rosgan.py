"""
scrapers/rosgan.py
Parser e importador de Excel de Rosgan (BCR).

Uso:
    # Importar un archivo:
    python scrapers/rosgan.py --local archivo.xlsx --remate 1928 --anio 2026

    # Importar toda una carpeta:
    python scrapers/rosgan.py --carpeta excels/

    # Ver estadísticas de la DB:
    python scrapers/rosgan.py --stats
"""

import io, re, sys, sqlite3, requests, pandas as pd
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path(__file__).parent.parent / "data" / "rosgan.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.bcr.com.ar/es/mercados/rosgan",
}


# ── Base de datos ─────────────────────────────────────────────────────────────

def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS remates (
            numero       INTEGER PRIMARY KEY,
            anio         INTEGER NOT NULL,
            fecha_remate TEXT,
            filas        INTEGER,
            descargado   TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS precios (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            remate       INTEGER NOT NULL,
            anio         INTEGER NOT NULL,
            fecha_remate TEXT,
            formulario   TEXT,
            categoria    TEXT,
            rango_kg     TEXT,
            peso_prom    REAL,
            cantidad     REAL,
            operaciones  REAL,
            minimo       REAL,
            maximo       REAL,
            promedio     REAL,
            es_resumen   INTEGER DEFAULT 0,
            tipo_raza    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_cat ON precios(categoria, formulario, anio);
        CREATE INDEX IF NOT EXISTS idx_rem ON precios(remate);
    """)
    conn.commit()
    conn.close()


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_excel(raw: bytes, numero: int, anio: int, fecha_remate: str) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(raw), engine="openpyxl")
    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.rename(columns={
        "Formulario":  "formulario",
        "Categoría":   "categoria",
        "Kilaje":      "rango_kg",
        "Peso Prom":   "peso_prom",
        "Cantidad":    "cantidad",
        "Operaciones": "operaciones",
        "Mínimo":      "minimo",
        "Máximo":      "maximo",
        "Promedio":    "promedio",
    })
    df["formulario"] = df["formulario"].ffill()
    df["categoria"]  = df["categoria"].ffill()
    df["es_resumen"] = (
        df["rango_kg"].str.upper().str.contains("RESUMEN", na=False).astype(int)
    )

    # Si el Excel ya tiene la columna 'fecha_remate' (porque fue importado antes desde la UI),
    # la respetamos. Si no, usamos la que nos pasaron por parámetro.
    if "fecha_remate" in df.columns:
        df["fecha_remate"] = df["fecha_remate"].fillna(fecha_remate)
    else:
        df["fecha_remate"] = fecha_remate

    df["remate"] = numero
    df["anio"]   = anio

    # Clasificación automática Británicas vs Holando (para separar producción de carne)
    df["tipo_raza"] = df["categoria"].apply(
        lambda x: "holando" if isinstance(x, str) and "holando" in x.lower() else "británicas"
    )

    for c in ["peso_prom", "minimo", "maximo", "promedio", "cantidad", "operaciones"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.drop(columns=["Descripción"], errors="ignore")

    cols = ["remate", "anio", "fecha_remate", "formulario", "categoria",
            "rango_kg", "peso_prom", "cantidad", "operaciones",
            "minimo", "maximo", "promedio", "es_resumen", "tipo_raza"]
    return df[[c for c in cols if c in df.columns]]


# ── Guardar ───────────────────────────────────────────────────────────────────

def guardar(df: pd.DataFrame, numero: int, anio: int, fecha_remate: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO remates (numero,anio,fecha_remate,filas) VALUES (?,?,?,?)",
        (numero, anio, fecha_remate, len(df))
    )
    conn.execute("DELETE FROM precios WHERE remate=?", (numero,))
    df.to_sql("precios", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    print(f"  ✅ Remate #{numero} ({anio}): {len(df)} filas guardadas")


# ── Importar archivo local ────────────────────────────────────────────────────

def importar_local(ruta: str, numero: int, anio: int, fecha_remate: str = None) -> pd.DataFrame:
    init_db()
    fecha = fecha_remate or f"{anio}-01-01"
    with open(ruta, "rb") as f:
        raw = f.read()
    df = parse_excel(raw, numero, anio, fecha)
    guardar(df, numero, anio, fecha)
    return df


# ── Importar carpeta completa ─────────────────────────────────────────────────

def importar_carpeta(carpeta: str):
    """Importa todos los Excel de Rosgan que estén en una carpeta."""
    init_db()
    patron = re.compile(r"Precios_Rosgan_(\d{4})_Remate_(\d+)\.xlsx", re.IGNORECASE)
    archivos = sorted(Path(carpeta).glob("*.xlsx"))

    if not archivos:
        print(f"❌ No se encontraron archivos .xlsx en: {carpeta}")
        return

    print(f"\n📂 {len(archivos)} archivo(s) encontrado(s) en {carpeta}\n")
    ok, errores = 0, 0

    for archivo in archivos:
        m = patron.match(archivo.name)
        if not m:
            print(f"  ⚠️  Ignorado (nombre no reconocido): {archivo.name}")
            continue
        anio   = int(m.group(1))
        numero = int(m.group(2))
        try:
            df = importar_local(str(archivo), numero, anio)
            resumenes = int(df["es_resumen"].sum())
            print(f"     → {resumenes} categorías importadas")
            ok += 1
        except Exception as e:
            print(f"  ❌ Remate #{numero}: {e}")
            errores += 1

    print(f"\n{'='*45}")
    print(f"Importados: {ok} remates  |  Errores: {errores}")
    s = stats()
    print(f"Total en DB: {s.get('remates',0)} remates · {s.get('filas',0)} filas")
    print(f"{'='*45}")


# ── Consultas ─────────────────────────────────────────────────────────────────

def ultimo_remate() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT formulario, categoria, rango_kg, peso_prom,
               cantidad, operaciones, minimo, maximo, promedio,
               fecha_remate, remate
        FROM precios
        WHERE remate = (SELECT MAX(numero) FROM remates)
          AND es_resumen = 1
        ORDER BY formulario, categoria
    """, conn)
    conn.close()
    return df


def serie_categoria(categoria: str, formulario: str = "INVERNADA") -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT p.remate, p.anio, p.fecha_remate,
               p.categoria, p.promedio, p.cantidad,
               p.peso_prom, p.minimo, p.maximo
        FROM precios p
        WHERE p.formulario = ? AND p.categoria = ? AND p.es_resumen = 1
        ORDER BY p.remate
    """, conn, params=(formulario, categoria))
    conn.close()
    return df


def categorias_disponibles(formulario: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    cats = [r[0] for r in conn.execute(
        "SELECT DISTINCT categoria FROM precios WHERE formulario=? AND es_resumen=1 ORDER BY categoria",
        (formulario,)
    ).fetchall()]
    conn.close()
    return cats


def stats() -> dict:
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("""
        SELECT COUNT(DISTINCT remates.numero),
               COUNT(*),
               MIN(remates.anio),
               MAX(remates.anio),
               MAX(remates.numero)
        FROM remates JOIN precios ON remates.numero = precios.remate
    """).fetchone()
    conn.close()
    if not r or r[0] == 0:
        return {"remates": 0, "filas": 0}
    return dict(zip(["remates","filas","desde","hasta","ultimo_remate"], r))


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Importador de Excel de Rosgan BCR")
    ap.add_argument("--local",   help="Ruta a un Excel local")
    ap.add_argument("--remate",  type=int, help="Número de remate")
    ap.add_argument("--anio",    type=int, help="Año del remate")
    ap.add_argument("--fecha",   help="Fecha del remate YYYY-MM-DD")
    ap.add_argument("--carpeta", help="Carpeta con múltiples Excel de Rosgan")
    ap.add_argument("--stats",   action="store_true", help="Ver estadísticas de DB")
    args = ap.parse_args()

    if args.stats:
        init_db()
        s = stats()
        print(f"Remates en DB : {s.get('remates', 0)}")
        print(f"Filas totales : {s.get('filas', 0)}")
        print(f"Período       : {s.get('desde')} – {s.get('hasta')}")
        print(f"Último remate : #{s.get('ultimo_remate')}")

    elif args.carpeta:
        importar_carpeta(args.carpeta)

    elif args.local:
        if not args.remate or not args.anio:
            print("❌  --local requiere --remate y --anio")
        else:
            importar_local(args.local, args.remate, args.anio, args.fecha)
            print("\n📊 Categorías importadas:")
            print(ultimo_remate()[["formulario","categoria","promedio"]].to_string(index=False))

    else:
        ap.print_help()
