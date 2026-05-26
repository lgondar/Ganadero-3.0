# 🐄 Dashboard Rosgan — Precios de Hacienda

Panel de análisis de precios de Rosgan (BCR — Bolsa de Comercio de Rosario).
Procesa los Excel descargados desde el sitio de Rosgan y los visualiza en un dashboard interactivo.

---

## Instalación

```bash
# 1. Crear entorno virtual (recomendado)
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# 2. Instalar dependencias
pip install -r requirements.txt
```

---

## Uso diario — 3 pasos

### Paso 1: Descargar el Excel de Rosgan

Ir a: https://www.bcr.com.ar/es/mercados/rosgan

- Elegir el año → el remate → descargar Excel
- El archivo se llama: `Precios_Rosgan_2026_Remate_1928.xlsx`
- El número de remate está en el nombre del archivo

### Paso 2: Importar el Excel

```bash
python scrapers/rosgan.py \
    --local Precios_Rosgan_2026_Remate_1928.xlsx \
    --remate 1928 \
    --anio 2026 \
    --fecha 2026-05-01
```

> **Tip:** Hacé esto cada vez que Rosgan publique un nuevo remate.
> Los remates nuevos de 2026 serán 1929, 1930, etc.

### Paso 3: Abrir el dashboard

```bash
streamlit run app.py
```

Se abre automáticamente en http://localhost:8501

---

## Flujo recomendado: Importar desde la interfaz (UI)

La forma más cómoda y segura ahora es importar **directamente desde la aplicación**:

1. Ejecutá `streamlit run app.py`
2. En el sidebar, abrí el expansor **"➕ Importar nuevo remate"**
3. Subí el Excel
4. Completá:
   - Número de remate
   - Año
   - **Fecha real del remate** (esto es clave)
5. Apretá **"📥 Importar"**

**Qué hace ahora la app:**
- Guarda los datos en la base de datos
- **Escribe la fecha dentro del Excel** como columna nueva (`fecha_remate`, `remate`, `anio`)
- Guarda una copia del Excel enriquecido en `data/excels_importados/`

Esto resuelve el problema de tener que recordar o reescribir fechas manualmente después.

> **Ventaja:** Si después querés re-importar ese Excel, la fecha ya viene adentro del archivo.

---

## Estructura del proyecto

```
ganadero/
├── app.py                  ← Dashboard Streamlit (ejecutar esto)
├── requirements.txt        ← Dependencias Python
├── README.md               ← Este archivo
├── data/
│   └── rosgan.db           ← Base de datos SQLite (se crea automáticamente)
└── scrapers/
    └── rosgan.py           ← Parser e importador de Excel
```

---

## Comandos útiles

```bash
# Ver cuántos remates hay en la base de datos
python scrapers/rosgan.py --stats

# Intentar descargar automáticamente remates de un año
# (funciona si Rosgan tiene los archivos públicos disponibles)
python scrapers/rosgan.py --anio 2026

# Importar múltiples archivos de una vez (ejemplo bash)
for f in *.xlsx; do
    # Extraer número de remate del nombre del archivo
    num=$(echo $f | grep -oP '\d{4}(?=\.xlsx)')
    anio=$(echo $f | grep -oP '(?<=Rosgan_)\d{4}')
    python scrapers/rosgan.py --local "$f" --remate "$num" --anio "$anio"
done
```

---

## Formato del Excel de Rosgan

El scraper espera exactamente este formato (el estándar de Rosgan):

| Formulario | Categoría | Kilaje | Peso Prom | Cantidad | Operaciones | Mínimo | Máximo | Promedio |
|---|---|---|---|---|---|---|---|---|
| INVERNADA | Terneros | RESUMEN CATEGORÍA | 193 | 1148 | 20 | 5700 | 6650 | 6183 |
| INVERNADA | Terneros | 161 - 200 Kgs. | 182 | 822 | 14 | 5700 | 6650 | 6192 |
| VIENTRES | Vacas c/cría al pie | RESUMEN CATEGORÍA | 214 | 124 | 3 | 2000000 | 2240000 | 2060968 |

- **Formulario**: INVERNADA o VIENTRES
- **Kilaje**: "RESUMEN CATEGORÍA" para la fila resumen, o rango ("161 - 200 Kgs.")
- **Precios INVERNADA**: en ARS/kg vivo
- **Precios VIENTRES**: en ARS/cabeza (precio total del animal)

---

## Agregar remates de años anteriores

Para armar la serie histórica, descargá los Excel de años anteriores desde Rosgan
y los importás igual:

```bash
python scrapers/rosgan.py --local Precios_Rosgan_2025_Remate_1925.xlsx --remate 1925 --anio 2025
python scrapers/rosgan.py --local Precios_Rosgan_2025_Remate_1924.xlsx --remate 1924 --anio 2025
# etc.
```

Con 5+ remates cargados, el tab "Serie temporal" del dashboard muestra la evolución de precios.

---

## Notas

- La base de datos (`data/rosgan.db`) se crea automáticamente al importar el primer archivo.
- Si importás el mismo remate dos veces, el scraper lo sobreescribe (no duplica).
- El dashboard se actualiza automáticamente cada vez que importás un nuevo remate.
- En el dashboard también podés importar archivos directamente desde la interfaz (sidebar → "Importar nuevo remate").
