"""
scrapers/rosgan_downloader.py

Descarga automática de todos los Excel de Rosgan para un año dado.
Usa Playwright para navegar el sitio como un browser real (evita el 403).

Instalación (una sola vez):
    pip install playwright
    playwright install chromium

Uso:
    # Descargar todos los remates de 2026
    python scrapers/rosgan_downloader.py --anio 2026

    # Descargar y además importar directo a la DB
    python scrapers/rosgan_downloader.py --anio 2026 --importar

    # Ver sin descargar (modo dry-run)
    python scrapers/rosgan_downloader.py --anio 2026 --dryrun
"""

import re
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DESTINO = Path.home() / "rosgan_excels"
ROSGAN_URL = "https://www.bcr.com.ar/es/mercados/rosgan"


# ── Descarga ──────────────────────────────────────────────────────────────────

def descargar_remates(anio: int, destino: Path, dryrun: bool = False) -> list[Path]:
    """
    Abre el sitio de Rosgan, filtra por año y descarga todos los Excel
    disponibles. Retorna lista de archivos descargados.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright no está instalado.")
        print("   Corré: pip install playwright && playwright install chromium")
        sys.exit(1)

    destino.mkdir(parents=True, exist_ok=True)
    descargados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        print(f"🌐 Abriendo sitio de Rosgan...")
        page.goto(ROSGAN_URL, timeout=30000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # ── Buscar selector de año ────────────────────────────────────────────
        # El sitio puede tener un <select>, tabs, o links por año
        anio_str = str(anio)

        # Intentar click en tab/link del año
        try:
            page.click(f"text={anio_str}", timeout=5000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)
            print(f"✅ Año {anio} seleccionado")
        except Exception:
            print(f"⚠️  No encontré tab de año — explorando página tal como está")

        # ── Buscar todos los links a Excel ────────────────────────────────────
        # Los links de Rosgan siguen el patrón:
        # /sites/default/files/rosgan/Precios_Rosgan_YYYY_Remate_NNNN.xlsx
        links_xlsx = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({ href: a.href, text: a.innerText.trim() }))
                .filter(l => l.href.includes('.xlsx') || l.href.includes('Remate'));
        }""")

        # Filtrar por año
        patron = re.compile(
            rf"Precios_Rosgan_{anio}_Remate_(\d+)\.xlsx", re.IGNORECASE
        )
        links_anio = [l for l in links_xlsx if patron.search(l["href"])]

        if not links_anio:
            # Plan B: buscar cualquier link con "Remate" en la URL
            links_anio = [
                l for l in links_xlsx
                if str(anio) in l["href"] and "remate" in l["href"].lower()
            ]

        if not links_anio:
            print(f"\n⚠️  No encontré links de Excel para {anio}.")
            print("   Puede que la página haya cambiado su estructura.")
            print("   Intentando con el número de remate conocido (1928+)...")

            # Plan C: construir URLs directamente y verificar con HEAD
            import requests
            encontrados = []
            for num in range(1920, 1950):
                url = (
                    f"https://www.bcr.com.ar/sites/default/files/rosgan/"
                    f"Precios_Rosgan_{anio}_Remate_{num}.xlsx"
                )
                # Usar cookies del browser para el request
                cookies = context.cookies()
                cookie_str = "; ".join(
                    [f"{c['name']}={c['value']}" for c in cookies]
                )
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36"
                    ),
                    "Referer": ROSGAN_URL,
                    "Cookie": cookie_str,
                }
                try:
                    r = requests.head(url, headers=headers, timeout=8)
                    if r.status_code == 200:
                        encontrados.append({
                            "href": url,
                            "text": f"Remate {num}"
                        })
                        print(f"  ✅ Encontrado: Remate #{num}")
                    elif r.status_code == 404:
                        # 404 = no existe ese número, seguir
                        pass
                except Exception:
                    pass

            links_anio = encontrados

        print(f"\n📋 {len(links_anio)} remate(s) encontrado(s) para {anio}:")
        for l in links_anio:
            m = patron.search(l["href"])
            num = m.group(1) if m else "?"
            print(f"   #{num} — {l['href']}")

        if dryrun:
            print("\n[dry-run] No se descargó nada.")
            browser.close()
            return []

        # ── Descargar cada Excel ──────────────────────────────────────────────
        print()
        for link in links_anio:
            href = link["href"]
            m = patron.search(href)
            num = m.group(1) if m else "0000"
            nombre = f"Precios_Rosgan_{anio}_Remate_{num}.xlsx"
            ruta_destino = destino / nombre

            if ruta_destino.exists():
                print(f"  ⏭️  Remate #{num}: ya existe, saltando")
                descargados.append(ruta_destino)
                continue

            try:
                print(f"  ⬇️  Descargando Remate #{num}...", end=" ", flush=True)

                with page.expect_download(timeout=30000) as dl_info:
                    page.evaluate(f"window.location.href = '{href}'")

                download = dl_info.value
                download.save_as(str(ruta_destino))
                size_kb = ruta_destino.stat().st_size // 1024
                print(f"✅ {size_kb} KB → {ruta_destino.name}")
                descargados.append(ruta_destino)
                time.sleep(1)  # Pausa cortés entre descargas

            except Exception as e:
                # Fallback: descargar con requests usando cookies del browser
                try:
                    import requests
                    cookies = {c["name"]: c["value"] for c in context.cookies()}
                    headers = {
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36"
                        ),
                        "Referer": ROSGAN_URL,
                    }
                    r = requests.get(
                        href, cookies=cookies, headers=headers, timeout=30
                    )
                    if r.status_code == 200 and len(r.content) > 5000:
                        ruta_destino.write_bytes(r.content)
                        size_kb = len(r.content) // 1024
                        print(f"✅ {size_kb} KB (via requests) → {ruta_destino.name}")
                        descargados.append(ruta_destino)
                    else:
                        print(f"❌ HTTP {r.status_code}")
                except Exception as e2:
                    print(f"❌ Error: {e2}")

        browser.close()

    return descargados


# ── Importar a DB ─────────────────────────────────────────────────────────────

def importar_a_db(archivos: list[Path]):
    """Importa la lista de archivos descargados a la base de datos SQLite."""
    from scrapers.rosgan import importar_local, init_db, stats

    init_db()
    patron = re.compile(r"Precios_Rosgan_(\d{4})_Remate_(\d+)\.xlsx", re.IGNORECASE)
    ok = 0

    print(f"\n📥 Importando {len(archivos)} archivo(s) a la DB...\n")
    for archivo in archivos:
        m = patron.match(archivo.name)
        if not m:
            print(f"  ⚠️  Nombre no reconocido: {archivo.name}")
            continue
        anio   = int(m.group(1))
        numero = int(m.group(2))
        try:
            df = importar_local(str(archivo), numero, anio)
            print(f"  ✅ Remate #{numero} ({anio}): {len(df)} filas, "
                  f"{int(df['es_resumen'].sum())} categorías")
            ok += 1
        except Exception as e:
            print(f"  ❌ Remate #{numero}: {e}")

    print(f"\n{'='*45}")
    s = stats()
    print(f"Total en DB: {s.get('remates',0)} remates · {s.get('filas',0)} filas")
    print(f"Período:     {s.get('desde')} – {s.get('hasta')}")
    print(f"{'='*45}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import date

    ap = argparse.ArgumentParser(
        description="Descarga automática de Excel de Rosgan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scrapers/rosgan_downloader.py --anio 2026
  python scrapers/rosgan_downloader.py --anio 2026 --importar
  python scrapers/rosgan_downloader.py --anio 2026 --destino ~/mis_excels --dryrun
        """
    )
    ap.add_argument(
        "--anio", type=int, default=date.today().year,
        help="Año a descargar (default: año actual)"
    )
    ap.add_argument(
        "--destino", type=str, default=str(DESTINO),
        help=f"Carpeta de destino (default: {DESTINO})"
    )
    ap.add_argument(
        "--importar", action="store_true",
        help="Importar automáticamente a SQLite después de descargar"
    )
    ap.add_argument(
        "--dryrun", action="store_true",
        help="Solo listar remates disponibles sin descargar"
    )
    args = ap.parse_args()

    destino = Path(args.destino)

    print(f"\n{'='*50}")
    print(f"  Rosgan Downloader — Año {args.anio}")
    print(f"  Destino: {destino}")
    print(f"{'='*50}\n")

    archivos = descargar_remates(args.anio, destino, dryrun=args.dryrun)

    if archivos and args.importar:
        importar_a_db(archivos)
    elif archivos:
        print(f"\n✅ {len(archivos)} archivo(s) en: {destino}")
        print(f"\nPara importar a la DB corré:")
        print(f"  python scrapers/rosgan.py --carpeta {destino}")
