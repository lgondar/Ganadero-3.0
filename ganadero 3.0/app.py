"""
app.py — Dashboard Rosgan
Ejecutar: streamlit run app.py
"""

import sqlite3
import requests
import warnings
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from pathlib import Path
from datetime import date, datetime

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

warnings.filterwarnings("ignore")

DB_PATH = Path(__file__).parent / "data" / "rosgan.db"


# ── Tipo de cambio histórico ──────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def obtener_tc_historico() -> pd.DataFrame:
    """
    Serie histórica de MEP calculada desde data912.com.
    MEP = precio cierre AL30 (ARS) / precio cierre AL30D (USD)
    Cubre desde sep-2021 hasta hoy. Sin BCRA, sin Bluelytics.
    """
    try:
        r_ars = requests.get("https://data912.com/historical/bonds/AL30",  timeout=15)
        r_usd = requests.get("https://data912.com/historical/bonds/AL30D", timeout=15)

        if r_ars.status_code == 200 and r_usd.status_code == 200:
            df_ars = pd.DataFrame(r_ars.json())
            df_usd = pd.DataFrame(r_usd.json())

            df_ars["fecha"] = pd.to_datetime(df_ars["date"])
            df_usd["fecha"] = pd.to_datetime(df_usd["date"])

            df_mep = df_ars[["fecha","c"]].rename(columns={"c":"al30_ars"}).merge(
                df_usd[["fecha","c"]].rename(columns={"c":"al30_usd"}),
                on="fecha", how="inner"
            )
            df_mep["tc_oficial"] = (df_mep["al30_ars"] / df_mep["al30_usd"]).round(2)
            df_mep["fuente"] = "MEP AL30 (data912)"
            return df_mep[["fecha","tc_oficial","fuente"]].sort_values("fecha")
    except Exception:
        pass

    return pd.DataFrame(columns=["fecha","tc_oficial","fuente"])


@st.cache_data(ttl=1800)
def obtener_tc_live() -> dict:
    """
    MEP del día calculado desde data912 live.
    Divide ars_ask / usd_ask del AL30 en el mercado en tiempo real.
    """
    try:
        r = requests.get("https://data912.com/live/mep", timeout=10)
        if r.status_code == 200:
            for item in r.json():
                if item.get("ticker") == "AL30":
                    ars = item.get("ars_ask") or item.get("ars_bid")
                    usd = item.get("usd_ask") or item.get("usd_bid")
                    if ars and usd and usd > 0:
                        return {"mep": round(ars/usd, 2), "fuente": "MEP AL30 live (data912)", "ticker": "AL30"}
    except Exception:
        pass
    # Fallback: usar el último valor del histórico
    df = obtener_tc_historico()
    if not df.empty:
        ultimo = df.iloc[-1]
        return {"mep": float(ultimo["tc_oficial"]), "fuente": "MEP AL30 histórico (data912)", "ticker": "AL30"}
    return {"mep": None, "fuente": "Sin datos", "ticker": None}


def tc_para_fecha(fecha_str: str, df_tc: pd.DataFrame) -> float | None:
    """Devuelve el TC oficial más cercano a la fecha dada."""
    if df_tc.empty or not fecha_str:
        return None
    try:
        fecha = pd.to_datetime(fecha_str)
        idx = (df_tc["fecha"] - fecha).abs().idxmin()
        return float(df_tc.loc[idx, "tc_oficial"])
    except Exception:
        return None


# ── Precios en tiempo real: Maíz y Novillo (para Ciclo Ganadero) ─────────────

@st.cache_data(ttl=300)
def obtener_precio_maiz() -> dict | None:
    """
    Precio del maíz en Cámara Arbitral de Rosario (contado).
    Fuente: bolsadecereales.com
    """
    if BeautifulSoup is None:
        return None
    url = "https://www.bolsadecereales.com/camara-arbitral"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for fila in soup.find_all("tr"):
            celdas = fila.find_all(["td", "th"])
            if len(celdas) >= 2:
                texto = celdas[0].get_text(strip=True).upper()
                if "MAIZ" in texto or "MAÍZ" in texto:
                    precio_str = celdas[1].get_text(strip=True).replace(".", "").replace(",", ".")
                    if precio_str.replace(".", "", 1).isdigit():
                        return {
                            "precio": float(precio_str),
                            "fuente": "Cámara Arbitral Rosario",
                            "unidad": "ARS/ton"
                        }
    except Exception:
        pass
    return None


@st.cache_data(ttl=60)
def obtener_novillo_ultimo_remate() -> dict | None:
    """
    Toma el precio promedio y el peso promedio del Novillo (categoría Británicas)
    del último remate cargado en la base de datos.
    """
    try:
        df = query("""
            SELECT 
                p.promedio,
                p.peso_prom,
                p.fecha_remate,
                p.remate,
                r.fecha_remate as fecha_remate_remate
            FROM precios p
            JOIN remates r ON p.remate = r.numero
            WHERE p.formulario = 'INVERNADA'
              AND p.es_resumen = 1
              AND (LOWER(p.categoria) LIKE '%novillo%' OR LOWER(p.categoria) LIKE '%novillos%')
              AND (p.tipo_raza = 'británicas' OR p.tipo_raza IS NULL)
            ORDER BY p.remate DESC
            LIMIT 1
        """)

        if df.empty:
            return None

        row = df.iloc[0]
        precio = float(row["promedio"]) if pd.notna(row["promedio"]) else None
        peso = float(row["peso_prom"]) if pd.notna(row["peso_prom"]) else None
        remate_num = int(row["remate"])
        fecha = row["fecha_remate"] or row["fecha_remate_remate"]

        if precio is None:
            return None

        return {
            "precio": precio,
            "peso_prom": peso,
            "remate": remate_num,
            "fecha_remate": fecha,
            "fuente": f"Rosgan #{remate_num}",
            "unidad": "ARS/kg vivo"
        }
    except Exception:
        return None


def convertir_precio(precio_ars: float, moneda: str, tc: float | None) -> str:
    """Formatea precio en ARS o USD según la moneda seleccionada."""
    if moneda == "USD" and tc and tc > 0:
        return f"U$S {precio_ars / tc:,.2f}"
    return f"${precio_ars:,.0f}"

st.set_page_config(
    page_title="Precios Rosgan",
    page_icon="🐄",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.block-container { padding-top: 1.5rem; }
div[data-testid="metric-container"] {
    background: rgba(255,255,255,0.05);
    border-radius: 10px;
    padding: 12px 16px;
    border: 1px solid rgba(255,255,255,0.08);
}
</style>
""", unsafe_allow_html=True)


# ── Helpers DB ────────────────────────────────────────────────────────────────

@st.cache_resource
def get_conn():
    if not DB_PATH.exists():
        st.error("❌ No hay base de datos. Importá al menos un Excel primero.")
        st.stop()
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def query(sql, params=()):
    return pd.read_sql(sql, get_conn(), params=params)

@st.cache_data(ttl=300)
def cargar_remates():
    return query("SELECT * FROM remates ORDER BY numero DESC")

@st.cache_data(ttl=300)
def cargar_precios(remate):
    return query("SELECT * FROM precios WHERE remate=? ORDER BY formulario, categoria", (remate,))

@st.cache_data(ttl=300)
def cargar_serie(categoria, formulario):
    return query("""
        SELECT p.remate, p.anio, p.fecha_remate,
               p.categoria, p.promedio, p.minimo, p.maximo,
               p.cantidad, p.operaciones, p.peso_prom
        FROM precios p
        WHERE p.formulario=? AND p.categoria=? AND p.es_resumen=1
        ORDER BY p.remate
    """, (formulario, categoria))

@st.cache_data(ttl=300)
def categorias_disponibles(formulario):
    r = query("SELECT DISTINCT categoria FROM precios WHERE formulario=? AND es_resumen=1 ORDER BY categoria", (formulario,))
    return r["categoria"].tolist()

@st.cache_data(ttl=300)
def todos_los_precios(formulario):
    return query("""
        SELECT p.remate, p.anio, p.fecha_remate, p.categoria,
               p.promedio, p.minimo, p.maximo, p.cantidad, p.peso_prom
        FROM precios p
        WHERE p.formulario=? AND p.es_resumen=1
        ORDER BY p.remate, p.categoria
    """, (formulario,))


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🐄 Precios Rosgan")
    st.caption("BCR — Bolsa de Comercio de Rosario")
    st.divider()

    remates_df = cargar_remates()
    if remates_df.empty:
        st.error("Sin remates en la base de datos.")
        st.stop()

    opciones = [
        f"#{r['numero']} — {r['anio']}  ({r['fecha_remate'] or 'sin fecha'})"
        for _, r in remates_df.iterrows()
    ]
    seleccion = st.selectbox("📅 Remate", opciones, index=0)
    remate_num = int(seleccion.split("#")[1].split(" ")[0])

    st.divider()
    formulario = st.radio("Formulario", ["INVERNADA", "VIENTRES"], horizontal=True)

    grupo = st.radio(
        "Grupo",
        ["Británicas", "Holando", "Todo"],
        horizontal=True,
        help="Británicas = foco carne | Holando = raza lechera (separada porque no es ideal para producción de carne)"
    )
    st.divider()

    # ── Importar nuevo remate ─────────────────────────────────────────────────
    with st.expander("➕ Importar nuevo remate"):
        archivo = st.file_uploader("Excel de Rosgan (.xlsx)", type=["xlsx"])
        col1, col2 = st.columns(2)
        num_nuevo  = col1.number_input("Nº remate", min_value=1800, max_value=2100, value=1929)
        anio_nuevo = col2.number_input("Año", min_value=2020, max_value=2030, value=date.today().year)
        fecha_nueva = st.date_input("Fecha del remate", value=date.today())

        if st.button("📥 Importar", use_container_width=True) and archivo:
            import io, sys
            from openpyxl import load_workbook
            sys.path.insert(0, str(Path(__file__).parent))
            from scrapers.rosgan import parse_excel, guardar, init_db

            init_db()
            raw = archivo.read()
            df_nuevo = parse_excel(raw, num_nuevo, anio_nuevo, str(fecha_nueva))
            guardar(df_nuevo, num_nuevo, anio_nuevo, str(fecha_nueva))

            # Actualizar fecha en remates y precios
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE remates SET fecha_remate=? WHERE numero=?",
                        (str(fecha_nueva), num_nuevo))
            conn.execute("UPDATE precios SET fecha_remate=? WHERE remate=?",
                        (str(fecha_nueva), num_nuevo))
            conn.commit()
            conn.close()

            # ── Guardar Excel enriquecido con la fecha DENTRO del archivo ─────
            importados_dir = Path(__file__).parent / "data" / "excels_importados"
            importados_dir.mkdir(parents=True, exist_ok=True)

            try:
                wb = load_workbook(io.BytesIO(raw))
                ws = wb.active

                # Encabezados actuales
                headers = [cell.value for cell in ws[1] if cell.value]
                extra = ["remate", "anio", "fecha_remate", "tipo_raza"]

                # Agregar columnas nuevas si no existen
                for col_name in extra:
                    if col_name not in headers:
                        ws.cell(row=1, column=len(headers) + 1, value=col_name)
                        headers.append(col_name)

                col_r = headers.index("remate") + 1
                col_a = headers.index("anio") + 1
                col_f = headers.index("fecha_remate") + 1
                col_t = headers.index("tipo_raza") + 1

                # Índice de la columna Categoría (para clasificar Británicas vs Holando)
                cat_col = headers.index("Categoría") + 1 if "Categoría" in headers else 2

                # Llenar todas las filas de datos con los valores elegidos + clasificación
                for row_idx in range(2, ws.max_row + 1):
                    if ws.cell(row=row_idx, column=1).value is None:
                        continue
                    ws.cell(row=row_idx, column=col_r, value=num_nuevo)
                    ws.cell(row=row_idx, column=col_a, value=anio_nuevo)
                    ws.cell(row=row_idx, column=col_f, value=str(fecha_nueva))

                    # Clasificación tipo_raza
                    cat_val = str(ws.cell(row=row_idx, column=cat_col).value or "")
                    tipo = "holando" if "holando" in cat_val.lower() else "británicas"
                    ws.cell(row=row_idx, column=col_t, value=tipo)

                # Nombre descriptivo que incluye la fecha real que puso el usuario
                nombre = f"Precios_Rosgan_{anio_nuevo}_Remate_{num_nuevo}_{fecha_nueva}.xlsx"
                ruta = importados_dir / nombre
                wb.save(ruta)

                st.success(f"✅ Remate #{num_nuevo} importado correctamente")
                st.info(f"📁 Excel con fecha embebida guardado en:\n`{ruta}`")
            except Exception as e:
                st.warning(f"⚠️ Remate guardado en la base de datos, pero no se pudo generar el Excel enriquecido: {e}")

            st.cache_data.clear()
            st.rerun()

    # ── Editar fechas de remates existentes ──────────────────────────────────
    with st.expander("📅 Editar fechas de remates"):
        st.caption("Completá la fecha real de cada remate — se usa en el eje X de los gráficos")

        # Fechas conocidas de Rosgan 2026 (precargadas)
        FECHAS_CONOCIDAS = {
            1848: "2026-01-11", 1874: "2026-01-25", 1900: "2026-02-08",
            1901: "2026-02-22", 1910: "2026-03-08", 1915: "2026-03-22",
            1920: "2026-04-05", 1921: "2026-04-12", 1922: "2026-04-19",
            1923: "2026-04-26", 1924: "2026-05-03", 1925: "2026-05-10",
            1926: "2026-05-17", 1927: "2026-05-24", 1928: "2026-03-20",
            1929: "2026-03-27", 1930: "2026-04-03", 1931: "2026-04-10",
            1932: "2026-04-17", 1933: "2026-03-26",
        }

        conn_edit = sqlite3.connect(DB_PATH)
        cambios = 0
        for _, rem in remates_df.iterrows():
            num = int(rem["numero"])
            fecha_actual = rem["fecha_remate"]
            # Autocompletar si la fecha es genérica y tenemos la real
            fecha_sugerida = FECHAS_CONOCIDAS.get(num)
            if fecha_sugerida and (not fecha_actual or fecha_actual.endswith("-01-01")):
                conn_edit.execute("UPDATE remates SET fecha_remate=? WHERE numero=?", (fecha_sugerida, num))
                conn_edit.execute("UPDATE precios SET fecha_remate=? WHERE remate=?", (fecha_sugerida, num))
                cambios += 1
                fecha_actual = fecha_sugerida

            col_r, col_f = st.columns([1, 2])
            col_r.markdown(f"**#{num}**")
            try:
                from datetime import datetime as dt
                fp = dt.strptime(fecha_actual, "%Y-%m-%d").date() if fecha_actual else date(int(rem["anio"]), 1, 1)
            except Exception:
                fp = date(int(rem["anio"]), 1, 1)
            nueva = col_f.date_input("", value=fp, label_visibility="collapsed", key=f"fe_{num}")
            if str(nueva) != str(fecha_actual):
                conn_edit.execute("UPDATE remates SET fecha_remate=? WHERE numero=?", (str(nueva), num))
                conn_edit.execute("UPDATE precios SET fecha_remate=? WHERE remate=?", (str(nueva), num))
                cambios += 1

        conn_edit.commit()
        conn_edit.close()

        if cambios > 0:
            st.caption(f"✅ {cambios} fecha(s) actualizadas")

        if st.button("🔄 Aplicar y recargar", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.caption(f"📁 {len(remates_df)} remate(s) en DB")
    st.caption(f"`{DB_PATH}`")


# ── Datos del remate seleccionado ─────────────────────────────────────────────

df_remate  = cargar_precios(remate_num)

# Aplicar filtro de grupo (Británicas / Holando / Todo)
if grupo != "Todo":
    df_remate = df_remate[df_remate["tipo_raza"] == grupo.lower()]

df_resumen = df_remate[(df_remate["formulario"] == formulario) & (df_remate["es_resumen"] == 1)].copy()
meta       = remates_df[remates_df["numero"] == remate_num].iloc[0]
fecha_str  = meta["fecha_remate"] or "sin fecha"

# ── Selector de moneda (necesita fecha_str ya definida) ──────────────────────
with st.sidebar:
    st.divider()
    st.markdown("💱 **Moneda**")
    moneda = st.radio("", ["ARS", "USD"], horizontal=True, label_visibility="collapsed",
                      key="moneda_selector")

    df_tc = obtener_tc_historico()
    tc_actual = tc_para_fecha(fecha_str, df_tc)

    if moneda == "USD":
        tc_live = obtener_tc_live()
        if tc_live["mep"]:
            st.caption(f"MEP hoy (AL30): **${tc_live['mep']:,.0f}** ARS/USD")
        if tc_actual:
            st.caption(f"TC del remate seleccionado: ${tc_actual:,.0f}")
        elif tc_live["mep"]:
            tc_actual = tc_live["mep"]
        else:
            tc_actual = st.number_input("TC manual", min_value=100.0,
                                         max_value=5000.0, value=1430.0, step=10.0)
            st.caption("⚠️ No se pudo obtener MEP")

# Función de formateo rápido según moneda activa
def fmt(precio_ars):
    if moneda == "USD" and tc_actual and tc_actual > 0:
        return f"U$S {precio_ars / tc_actual:,.2f}/kg"
    return f"${precio_ars:,.0f}/kg"

def fmt_cab(precio_ars):
    """Para vientres donde el precio es por cabeza."""
    if moneda == "USD" and tc_actual and tc_actual > 0:
        return f"U$S {precio_ars / tc_actual:,.0f}/cab"
    return f"${precio_ars:,.0f}/cab"

# ── Header ────────────────────────────────────────────────────────────────────

st.title(f"Remate #{remate_num}  ·  {formulario}")
st.caption(f"Fecha: {fecha_str}  ·  Año: {meta['anio']}  ·  {meta['filas']} filas totales")
st.divider()

# ── KPIs ──────────────────────────────────────────────────────────────────────

if df_resumen.empty:
    st.warning(f"No hay datos de {formulario} para el remate #{remate_num}.")
    st.stop()

if formulario == "INVERNADA":
    cats_kpi = ["Terneros","Terneros/as","Novillitos","Novillos","Terneras","Vaquillonas"]
    kpi_data = []
    for cat in cats_kpi:
        row = df_resumen[df_resumen["categoria"] == cat]
        if not row.empty:
            r = row.iloc[0]
            kpi_data.append({"cat": cat, "prom": r["promedio"], "min": r["minimo"],
                             "max": r["maximo"], "cant": r["cantidad"], "peso": r["peso_prom"]})
    cols = st.columns(len(kpi_data))
    for i, k in enumerate(kpi_data):
        with cols[i]:
            st.metric(label=f"🐄 {k['cat']}", value=fmt(k['prom']),
                      delta=f"{fmt(k['min'])} – {fmt(k['max'])}", delta_color="off")
            cant = int(k['cant']) if pd.notna(k['cant']) else 0
            peso = k['peso'] if pd.notna(k['peso']) else 0
            st.caption(f"📦 {cant:,} cab · ⚖️ {peso:.0f} kg")
else:
    cols = st.columns(min(len(df_resumen), 4))
    for i, (_, row) in enumerate(df_resumen.iterrows()):
        with cols[i % len(cols)]:
            st.metric(label=row["categoria"], value=f"${row['promedio']:,.0f}",
                      delta=f"${row['minimo']:,.0f} – ${row['maximo']:,.0f}", delta_color="off")

st.divider()

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📊 Resumen del remate",
    "📈 Serie temporal",
    "🌾 Análisis criador",
    "🔍 Detalle por rango",
    "📋 Datos crudos",
    "🔄 Ciclo ganadero",
    "💱 Tipo de cambio",
])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — Resumen del remate
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    col_iz, col_der = st.columns([3, 2])

    with col_iz:
        st.subheader("Precio promedio por categoría")
        df_plot = df_resumen.sort_values("promedio", ascending=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=df_plot["categoria"], x=df_plot["promedio"], orientation="h",
            marker_color="#3b82f6", name="Promedio",
            text=[f"${v:,.0f}" for v in df_plot["promedio"]], textposition="outside",
        ))
        fig.add_trace(go.Scatter(
            y=df_plot["categoria"], x=df_plot["minimo"], mode="markers",
            marker=dict(color="#ef4444", size=8, symbol="line-ew-open", line_width=2), name="Mínimo",
        ))
        fig.add_trace(go.Scatter(
            y=df_plot["categoria"], x=df_plot["maximo"], mode="markers",
            marker=dict(color="#22c55e", size=8, symbol="line-ew-open", line_width=2), name="Máximo",
        ))
        fig.update_layout(height=380, xaxis_title="ARS/kg", xaxis_tickformat="$,.0f",
                         xaxis_range=[0, df_plot["maximo"].max()*1.15],
                         legend=dict(orientation="h", y=-0.15),
                         margin=dict(l=10,r=60,t=10,b=40),
                         plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    with col_der:
        st.subheader("Volumen por categoría")
        df_vol = df_resumen[df_resumen["cantidad"] > 0].sort_values("cantidad", ascending=False)
        fig2 = px.pie(df_vol, names="categoria", values="cantidad", hole=0.4,
                      color_discrete_sequence=px.colors.qualitative.Set2)
        fig2.update_traces(textposition="inside", textinfo="percent+label")
        fig2.update_layout(height=380, showlegend=False,
                           margin=dict(l=10,r=10,t=10,b=10),
                           paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Tabla resumen")
    df_tabla = df_resumen[["categoria","peso_prom","cantidad","operaciones","minimo","maximo","promedio"]].copy()
    df_tabla.columns = ["Categoría","Peso prom (kg)","Cabezas","Operaciones","Mínimo","Máximo","Promedio"]
    for c in ["Mínimo","Máximo","Promedio"]:
        df_tabla[c] = df_tabla[c].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    for c in ["Cabezas","Operaciones"]:
        df_tabla[c] = df_tabla[c].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "")
    st.dataframe(df_tabla, use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — Serie temporal
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Evolución de precios entre remates")

    if len(remates_df) < 2:
        st.info("💡 Con un solo remate no hay serie. Importá más remates para ver la evolución.")
    else:
        cats_disp = categorias_disponibles(formulario)

        # ── Controles ─────────────────────────────────────────────────────────
        col_cats, col_moneda = st.columns([3, 1])
        with col_cats:
            cats_sel = st.multiselect("Categorías", cats_disp,
                                      default=[c for c in ["Vaquillonas","Terneros","Novillitos"] if c in cats_disp])
        with col_moneda:
            moneda_serie = st.radio("Moneda", ["ARS", "USD (MEP)"], horizontal=False, key="moneda_serie")

        # ── TC MEP por remate ──────────────────────────────────────────────────
        df_tc_hist = obtener_tc_historico()
        tc_live    = obtener_tc_live()
        usar_usd   = moneda_serie == "USD (MEP)"

        if usar_usd:
            if tc_live["mep"]:
                st.caption(f"💱 MEP actual: **${tc_live['mep']:,.0f}** ARS/USD ({tc_live['ticker']})"
                           f" · Los precios históricos usan TC oficial BCRA por fecha de remate")
            else:
                st.caption("⚠️ No se pudo obtener MEP. Se usa TC oficial BCRA.")

        if cats_sel:
            dfs = [cargar_serie(c, formulario) for c in cats_sel]
            df_series = pd.concat([d for d in dfs if not d.empty])

            # Aplicar filtro de grupo (Británicas / Holando)
            if grupo != "Todo" and "tipo_raza" in df_series.columns:
                df_series = df_series[df_series["tipo_raza"] == grupo.lower()]

            # Ordenar cronológicamente
            df_series["fecha_dt"] = pd.to_datetime(df_series["fecha_remate"], errors="coerce")
            df_series = df_series.sort_values("fecha_dt")

            # Calcular TC por fecha y precio en USD
            def tc_por_fecha(fecha_str):
                # Para el remate más reciente, usar MEP live
                if tc_live["mep"] and fecha_str == remates_df.iloc[0]["fecha_remate"]:
                    return tc_live["mep"]
                return tc_para_fecha(fecha_str, df_tc_hist)

            df_series["tc"] = df_series["fecha_remate"].apply(tc_por_fecha)

            # Precio a graficar según moneda
            if usar_usd:
                df_series["precio_graf"] = df_series.apply(
                    lambda r: r["promedio"] / r["tc"] if r["tc"] and r["tc"] > 0 else None, axis=1
                )
                df_series["minimo_graf"] = df_series.apply(
                    lambda r: r["minimo"] / r["tc"] if r["tc"] and r["tc"] > 0 else None, axis=1
                )
                df_series["maximo_graf"] = df_series.apply(
                    lambda r: r["maximo"] / r["tc"] if r["tc"] and r["tc"] > 0 else None, axis=1
                )
                eje_y_titulo = "USD/kg (MEP)"
                tick_fmt     = "$.2f"
                precio_fmt   = lambda v, tc: f"U$S {v/tc:.2f}/kg" if tc else f"${v:,.0f}/kg"
            else:
                df_series["precio_graf"]  = df_series["promedio"]
                df_series["minimo_graf"]  = df_series["minimo"]
                df_series["maximo_graf"]  = df_series["maximo"]
                eje_y_titulo = "ARS/kg"
                tick_fmt     = "$,.0f"
                precio_fmt   = lambda v, tc: f"${v:,.0f}/kg"

            # Tooltip enriquecido
            def make_tooltip(r):
                base = f"Remate #{int(r['remate'])}  ·  {r['fecha_remate']}"
                if usar_usd and r["tc"]:
                    base += f"  ·  TC: ${r['tc']:,.0f}"
                return base

            df_series["tooltip"] = df_series.apply(make_tooltip, axis=1)
            df_series = df_series.dropna(subset=["precio_graf"])

            colors = px.colors.qualitative.Set1
            fig3 = go.Figure()
            for i, cat in enumerate(cats_sel):
                df_cat = df_series[df_series["categoria"] == cat].copy()
                if df_cat.empty:
                    continue
                color = colors[i % len(colors)]
                fig3.add_trace(go.Scatter(
                    x=df_cat["fecha_dt"],
                    y=df_cat["precio_graf"],
                    mode="lines+markers",
                    name=cat,
                    line=dict(color=color, width=2),
                    marker=dict(size=8, color=color),
                    customdata=df_cat[["tooltip","minimo_graf","maximo_graf","cantidad","tc"]].values,
                    hovertemplate=(
                        "<b>%{customdata[0]}</b><br>"
                        f"<b>{cat}</b><br>"
                        "Promedio: %{y:,.2f}<br>"
                        "Mín: %{customdata[1]:,.2f}  ·  Máx: %{customdata[2]:,.2f}<br>"
                        "Cabezas: %{customdata[3]:,.0f}<extra></extra>"
                    )
                ))

            fig3.update_layout(
                height=420,
                yaxis_tickformat=tick_fmt,
                yaxis_title=eje_y_titulo,
                xaxis_title="Fecha del remate",
                xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                legend=dict(orientation="h", y=-0.2),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10,r=10,t=10,b=70),
                hovermode="x unified"
            )
            st.plotly_chart(fig3, use_container_width=True)

            # ── Variación acumulada ───────────────────────────────────────────
            st.subheader("Variación acumulada 2026")
            cols_var = st.columns(len(cats_sel))
            for i, cat in enumerate(cats_sel):
                df_cat = df_series[df_series["categoria"] == cat].dropna(subset=["precio_graf"])
                if len(df_cat) >= 2:
                    primero  = df_cat.iloc[0]["precio_graf"]
                    ultimo   = df_cat.iloc[-1]["precio_graf"]
                    var_pct  = ((ultimo - primero) / primero) * 100
                    unidad   = "U$S" if usar_usd else "$"
                    decimals = ".2f" if usar_usd else ",.0f"
                    valor_str = f"{unidad} {ultimo:{decimals}}/kg"
                    with cols_var[i]:
                        st.metric(cat, valor_str, f"{var_pct:+.1f}% acumulado 2026")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — Análisis criador (VAQUILLONAS)
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("🌾 Análisis para productor criador")
    st.caption("Foco en vaquillonas — decisiones de venta y benchmark de mercado")

    df_todos = todos_los_precios("INVERNADA")

    # Aplicar filtro de grupo
    if grupo != "Todo" and "tipo_raza" in df_todos.columns:
        df_todos = df_todos[df_todos["tipo_raza"] == grupo.lower()]

    if df_todos.empty:
        st.info("Sin datos suficientes.")
    else:
        # ── Sección 1: Precio de vaquillonas vs otras categorías ─────────────
        st.markdown("### Vaquillonas vs otras categorías — último remate")

        ultimo_num = df_todos["remate"].max()
        df_ultimo  = df_todos[df_todos["remate"] == ultimo_num].copy()

        cats_comp = ["Vaquillonas","Terneras","Terneros/as","Terneros","Novillitos","Novillos"]
        df_comp   = df_ultimo[df_ultimo["categoria"].isin(cats_comp)].copy()

        if not df_comp.empty:
            df_comp["color"] = df_comp["categoria"].apply(
                lambda x: "#f59e0b" if x == "Vaquillonas" else "#3b82f6"
            )
            fig_comp = go.Figure()
            for _, row in df_comp.sort_values("promedio", ascending=False).iterrows():
                color = "#f59e0b" if row["categoria"] == "Vaquillonas" else "#3b82f6"
                fig_comp.add_trace(go.Bar(
                    x=[row["categoria"]], y=[row["promedio"]],
                    marker_color=color, name=row["categoria"],
                    text=f"${row['promedio']:,.0f}", textposition="outside",
                    showlegend=False
                ))
            # Línea de precio vaquillona como referencia
            vaq = df_comp[df_comp["categoria"] == "Vaquillonas"]
            if not vaq.empty:
                precio_vaq = vaq.iloc[0]["promedio"]
                fig_comp.add_hline(y=precio_vaq, line_dash="dash", line_color="#f59e0b",
                                   annotation_text=f"Precio vaquillona: ${precio_vaq:,.0f}",
                                   annotation_position="top right")
            fig_comp.update_layout(height=340, yaxis_tickformat="$,.0f", yaxis_title="ARS/kg",
                                   plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                   margin=dict(l=10,r=10,t=30,b=10))
            st.plotly_chart(fig_comp, use_container_width=True)

        st.divider()

        # ── Sección 2: Evolución vaquillonas entre remates ───────────────────
        st.markdown("### Evolución del precio de vaquillonas")

        serie_vaq = cargar_serie("Vaquillonas", "INVERNADA")

        if len(serie_vaq) < 2:
            st.info("Necesitás al menos 2 remates con vaquillonas para ver la evolución.")
        else:
            col_a, col_b, col_c = st.columns(3)
            precio_actual  = serie_vaq.iloc[-1]["promedio"]
            precio_inicial = serie_vaq.iloc[0]["promedio"]
            precio_max     = serie_vaq["promedio"].max()
            precio_min     = serie_vaq["promedio"].min()
            var_total      = ((precio_actual - precio_inicial) / precio_inicial) * 100
            vol_total      = serie_vaq["cantidad"].sum()

            col_a.metric("Precio actual", f"${precio_actual:,.0f}/kg", f"{var_total:+.1f}% acumulado")
            col_b.metric("Rango 2026", f"${precio_min:,.0f} – ${precio_max:,.0f}/kg")
            col_c.metric("Cabezas operadas", f"{int(vol_total):,}")

            # Gráfico con banda de confianza mín/máx
            serie_vaq["eje_x"] = serie_vaq.apply(
                lambda r: r["fecha_remate"] if r["fecha_remate"] and r["fecha_remate"] != f"{r['anio']}-01-01"
                          else f"#{int(r['remate'])}", axis=1
            )
            fig_vaq = go.Figure()
            fig_vaq.add_trace(go.Scatter(
                x=serie_vaq["eje_x"], y=serie_vaq["maximo"],
                mode="lines", line=dict(width=0), showlegend=False,
                fillcolor="rgba(245,158,11,0.15)", fill=None, name="Máximo"
            ))
            fig_vaq.add_trace(go.Scatter(
                x=serie_vaq["eje_x"], y=serie_vaq["minimo"],
                mode="lines", line=dict(width=0),
                fillcolor="rgba(245,158,11,0.15)", fill="tonexty",
                name="Rango mín–máx", showlegend=True
            ))
            fig_vaq.add_trace(go.Scatter(
                x=serie_vaq["eje_x"], y=serie_vaq["promedio"],
                mode="lines+markers", line=dict(color="#f59e0b", width=3),
                marker=dict(size=8), name="Precio promedio"
            ))
            fig_vaq.update_layout(height=320, yaxis_tickformat="$,.0f",
                                  xaxis_title="Fecha / Remate", yaxis_title="ARS/kg",
                                  xaxis_tickangle=-30,
                                  legend=dict(orientation="h", y=-0.2),
                                  plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                  margin=dict(l=10,r=10,t=10,b=50))
            st.plotly_chart(fig_vaq, use_container_width=True)

        st.divider()

        # ── Sección 3: Relación vaquillona / ternera (indicador retención) ───
        st.markdown("### Relación vaquillona / ternera — indicador de retención")
        st.caption("Cuando la vaquillona vale más de 1,1x la ternera, el mercado está reteniendo hembras")

        serie_t = cargar_serie("Terneros", "INVERNADA")
        serie_v = cargar_serie("Vaquillonas", "INVERNADA")

        if not serie_t.empty and not serie_v.empty:
            df_rel = serie_v[["remate","promedio"]].rename(columns={"promedio":"precio_vaq"})
            df_rel = df_rel.merge(
                serie_t[["remate","promedio"]].rename(columns={"promedio":"precio_ternero"}),
                on="remate", how="inner"
            )
            df_rel["relacion"] = df_rel["precio_vaq"] / df_rel["precio_ternero"]

            fig_rel = go.Figure()
            fig_rel.add_hline(y=1.1, line_dash="dash", line_color="#22c55e",
                              annotation_text="Zona retención (>1.1x)",
                              annotation_position="top right",
                              annotation_font_color="#22c55e")
            fig_rel.add_hline(y=1.0, line_dash="dot", line_color="#ef4444",
                              annotation_text="Paridad (1.0x)",
                              annotation_position="bottom right",
                              annotation_font_color="#ef4444")
            df_rel["eje_x"] = df_rel["remate"].apply(lambda r: f"#{int(r)}")
            # Usar fecha si está en serie_v
            fecha_map = dict(zip(serie_v["remate"], serie_v["fecha_remate"]))
            df_rel["eje_x"] = df_rel["remate"].apply(
                lambda r: fecha_map.get(r, f"#{int(r)}")
                if fecha_map.get(r) and fecha_map.get(r) != f"2026-01-01" else f"#{int(r)}"
            )
            fig_rel.add_trace(go.Scatter(
                x=df_rel["eje_x"], y=df_rel["relacion"],
                mode="lines+markers", line=dict(color="#f59e0b", width=3),
                marker=dict(size=9,
                            color=["#22c55e" if v >= 1.1 else "#ef4444" for v in df_rel["relacion"]],
                            line=dict(color="#f59e0b", width=2)),
                name="Relación vaq/ternero"
            ))
            fig_rel.update_layout(height=280, yaxis_title="Ratio precio vaq / ternero",
                                  xaxis_title="Fecha / Remate",
                                  xaxis_tickangle=-30,
                                  yaxis_tickformat=".2f",
                                  plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                  margin=dict(l=10,r=10,t=10,b=40),
                                  showlegend=False)
            st.plotly_chart(fig_rel, use_container_width=True)

            # Interpretación automática
            ultimo_ratio = df_rel.iloc[-1]["relacion"]
            if ultimo_ratio >= 1.1:
                st.success(f"✅ Ratio actual: **{ultimo_ratio:.2f}x** — El mercado está en zona de **retención de vientres**. Buen momento para vender vaquillonas.")
            else:
                st.warning(f"⚠️ Ratio actual: **{ultimo_ratio:.2f}x** — El mercado está en zona de **liquidación**. Las vaquillonas cotizan cerca de los terneros.")
        else:
            st.info("Se necesitan datos de terneros y vaquillonas en al menos un remate.")

        st.divider()

        # ── Sección 4: Volatilidad de precios ────────────────────────────────
        st.markdown("### Volatilidad por categoría — riesgo de precio")
        st.caption("Amplitud del rango mín–máx como % del precio promedio. Menor volatilidad = precio más predecible.")

        df_vol2 = df_ultimo[df_ultimo["cantidad"] > 0].copy()
        df_vol2["volatilidad_pct"] = ((df_vol2["maximo"] - df_vol2["minimo"]) / df_vol2["promedio"] * 100).round(1)
        df_vol2 = df_vol2.sort_values("volatilidad_pct")

        fig_vol = px.bar(
            df_vol2, x="categoria", y="volatilidad_pct",
            color="volatilidad_pct",
            color_continuous_scale=["#22c55e","#f59e0b","#ef4444"],
            text=df_vol2["volatilidad_pct"].apply(lambda x: f"{x:.1f}%"),
            labels={"categoria":"Categoría","volatilidad_pct":"Volatilidad (%)"}
        )
        fig_vol.update_traces(textposition="outside")
        fig_vol.update_layout(height=300, showlegend=False, coloraxis_showscale=False,
                              plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                              margin=dict(l=10,r=10,t=10,b=40))
        st.plotly_chart(fig_vol, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 — Detalle por rango de kg
# ════════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("Detalle por rango de kilaje")
    cats_det = df_remate[(df_remate["formulario"]==formulario) & (df_remate["es_resumen"]==0)]["categoria"].unique().tolist()

    if not cats_det:
        st.info("No hay datos de detalle para este remate.")
    else:
        cat_det = st.selectbox("Categoría", sorted(cats_det))
        df_det  = df_remate[(df_remate["formulario"]==formulario) &
                            (df_remate["categoria"]==cat_det) &
                            (df_remate["es_resumen"]==0)].copy()

        if not df_det.empty:
            fig5 = go.Figure()
            fig5.add_trace(go.Bar(x=df_det["rango_kg"], y=df_det["promedio"],
                                  name="Promedio", marker_color="#3b82f6",
                                  text=[f"${v:,.0f}" for v in df_det["promedio"]], textposition="outside"))
            fig5.add_trace(go.Scatter(x=df_det["rango_kg"], y=df_det["minimo"],
                                      mode="markers+lines", name="Mínimo",
                                      line=dict(color="#ef4444",dash="dot"), marker=dict(size=8)))
            fig5.add_trace(go.Scatter(x=df_det["rango_kg"], y=df_det["maximo"],
                                      mode="markers+lines", name="Máximo",
                                      line=dict(color="#22c55e",dash="dot"), marker=dict(size=8)))
            fig5.update_layout(height=360, yaxis_title="ARS/kg", yaxis_tickformat="$,.0f",
                               legend=dict(orientation="h",y=-0.2),
                               plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                               margin=dict(l=10,r=10,t=10,b=50))
            st.plotly_chart(fig5, use_container_width=True)

            col_a, col_b = st.columns(2)
            with col_a:
                fig6 = px.bar(df_det, x="rango_kg", y="cantidad", title="Cabezas por rango",
                              color_discrete_sequence=["#f59e0b"])
                fig6.update_layout(height=260, plot_bgcolor="rgba(0,0,0,0)",
                                   paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=10,r=10,t=40,b=40))
                st.plotly_chart(fig6, use_container_width=True)
            with col_b:
                fig7 = px.bar(df_det, x="rango_kg", y="peso_prom", title="Peso promedio por rango",
                              color_discrete_sequence=["#8b5cf6"])
                fig7.update_layout(height=260, plot_bgcolor="rgba(0,0,0,0)",
                                   paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=10,r=10,t=40,b=40))
                st.plotly_chart(fig7, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 5 — Datos crudos
# ════════════════════════════════════════════════════════════════════════════════
with tab5:
    st.subheader(f"Datos completos — Remate #{remate_num} · {formulario}")
    df_crudo = df_remate[df_remate["formulario"]==formulario].copy()
    df_crudo["es_resumen"] = df_crudo["es_resumen"].map({1:"✅ Resumen",0:"Detalle"})
    st.dataframe(df_crudo.drop(columns=["remate","anio","fecha_remate","formulario"], errors="ignore"),
                 use_container_width=True, hide_index=True)
    csv = df_crudo.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Descargar CSV", data=csv,
                       file_name=f"rosgan_remate_{remate_num}_{formulario.lower()}.csv",
                       mime="text/csv")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 6 — Ciclo ganadero
# ════════════════════════════════════════════════════════════════════════════════
with tab6:
    st.subheader("🔄 Indicadores del ciclo ganadero")
    st.caption("Señales para identificar si el mercado está en fase de retención o liquidación")

    # ── Precios en tiempo real: Maíz / Novillo ────────────────────────────────
    st.markdown("### 📈 Relación Maíz / Novillo (tiempo real)")

    col_m, col_n, col_r = st.columns(3)

    maiz_data = obtener_precio_maiz()
    novillo_data = obtener_novillo_ultimo_remate()

    with col_m:
        if maiz_data:
            st.metric(
                "🌽 Maíz Rosario (Cámara Arbitral)",
                f"${maiz_data['precio']:,.0f}",
                maiz_data.get("unidad", "ARS/ton")
            )
        else:
            st.metric("🌽 Maíz Rosario", "Sin datos", "Cámara Arbitral")

    with col_n:
        if novillo_data:
            precio_str = f"${novillo_data['precio']:,.0f}"
            delta = f"Peso prom: {novillo_data['peso_prom']:.0f} kg" if novillo_data.get("peso_prom") else None
            st.metric(
                f"🐮 Novillo (último remate Rosgan #{novillo_data['remate']})",
                precio_str,
                delta
            )
        else:
            st.metric("🐮 Novillo (Rosgan)", "Sin datos", "Cargar más remates")

    with col_r:
        if maiz_data and novillo_data:
            # Relación clásica: cuántos kg de maíz se necesitan para comprar 1 kg de novillo vivo
            precio_maiz_ton = maiz_data["precio"]
            precio_maiz_kg = precio_maiz_ton / 1000
            relacion = novillo_data["precio"] / precio_maiz_kg
            st.metric(
                "📊 Relación (kg maíz / kg novillo)",
                f"{relacion:.1f} kg",
                "Cuanto más alto, más caro está el maíz relativo al novillo físico"
            )
        else:
            st.metric("📊 Relación Maíz/Novillo", "Calculando...")

    st.caption("Maíz: Cámara Arbitral Rosario | Novillo: último remate cargado en tu base de datos (categoría Británicas).")
    if st.button("🔄 Actualizar precios ahora", key="btn_actualizar_maiz_novillo"):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    df_todos = query("""
        SELECT p.remate, p.anio, p.fecha_remate, p.categoria,
               p.promedio, p.cantidad, p.peso_prom, p.tipo_raza
        FROM precios p
        WHERE p.formulario='INVERNADA' AND p.es_resumen=1
        ORDER BY p.fecha_remate, p.remate
    """)

    # Aplicar filtro de grupo
    if grupo != "Todo" and "tipo_raza" in df_todos.columns:
        df_todos = df_todos[df_todos["tipo_raza"] == grupo.lower()]

    if df_todos.empty:
        st.info("Sin datos suficientes.")
    else:
        df_todos["fecha_dt"] = pd.to_datetime(df_todos["fecha_remate"], errors="coerce")
        df_todos = df_todos.sort_values("fecha_dt")

        # ── Indicador 1: Relación ternero / novillo ───────────────────────────
        st.markdown("### 📊 Relación precio ternero vs novillo")
        st.caption("Cuando el ternero vale más del 90% del novillo → el mercado valoriza la cría → señal de retención")

        df_t = df_todos[df_todos["categoria"].isin(["Terneros","Terneros/as"])].groupby("fecha_dt")["promedio"].mean().reset_index()
        df_n = df_todos[df_todos["categoria"] == "Novillos"].groupby("fecha_dt")["promedio"].mean().reset_index()
        df_rel1 = df_t.merge(df_n, on="fecha_dt", suffixes=("_ternero","_novillo"))
        df_rel1["ratio"] = (df_rel1["promedio_ternero"] / df_rel1["promedio_novillo"] * 100).round(1)

        if not df_rel1.empty:
            fig_c1 = go.Figure()
            fig_c1.add_hrect(y0=90, y1=110, fillcolor="rgba(34,197,94,0.08)",
                             line_width=0, annotation_text="Zona retención", annotation_position="top left")
            fig_c1.add_hline(y=90, line_dash="dash", line_color="#22c55e",
                             annotation_text="Umbral retención (90%)", annotation_position="bottom right")
            fig_c1.add_trace(go.Scatter(
                x=df_rel1["fecha_dt"], y=df_rel1["ratio"],
                mode="lines+markers", line=dict(color="#f59e0b", width=3),
                marker=dict(size=9,
                    color=["#22c55e" if v >= 90 else "#ef4444" for v in df_rel1["ratio"]],
                    line=dict(color="#f59e0b", width=2)),
                hovertemplate="Fecha: %{x|%d %b %Y}<br>Ternero/Novillo: %{y:.1f}%<extra></extra>"
            ))
            fig_c1.update_layout(height=280, yaxis_title="% ternero / novillo",
                                  xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                                  plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                  margin=dict(l=10,r=10,t=10,b=50), showlegend=False)
            st.plotly_chart(fig_c1, use_container_width=True)

            ultimo_ratio = df_rel1.iloc[-1]["ratio"]
            if ultimo_ratio >= 90:
                st.success(f"✅ Ratio actual: **{ultimo_ratio:.1f}%** — Mercado en zona de **retención**. Los terneros cotizan fuerte.")
            else:
                st.warning(f"⚠️ Ratio actual: **{ultimo_ratio:.1f}%** — Mercado más cerca de **liquidación**. Los novillos se valorizan más que los terneros.")

        st.divider()

        # ── Indicador 2: Relación vaquillona / ternero ────────────────────────
        st.markdown("### 📊 Relación vaquillona / ternero")
        st.caption("Mayor diferencia → el mercado paga más por retener hembras → señal de retención de vientres")

        df_v  = df_todos[df_todos["categoria"] == "Vaquillonas"].groupby("fecha_dt")["promedio"].mean().reset_index()
        df_t2 = df_todos[df_todos["categoria"].isin(["Terneros","Terneros/as"])].groupby("fecha_dt")["promedio"].mean().reset_index()
        df_rel2 = df_v.merge(df_t2, on="fecha_dt", suffixes=("_vaq","_tern"))
        df_rel2["ratio"] = (df_rel2["promedio_vaq"] / df_rel2["promedio_tern"]).round(3)

        if not df_rel2.empty:
            fig_c2 = go.Figure()
            fig_c2.add_hline(y=1.1, line_dash="dash", line_color="#22c55e",
                             annotation_text="Zona retención (>1.10x)", annotation_position="top right")
            fig_c2.add_hline(y=1.0, line_dash="dot", line_color="#ef4444",
                             annotation_text="Paridad (1.0x)", annotation_position="bottom right")
            fig_c2.add_trace(go.Scatter(
                x=df_rel2["fecha_dt"], y=df_rel2["ratio"],
                mode="lines+markers", line=dict(color="#8b5cf6", width=3),
                marker=dict(size=9,
                    color=["#22c55e" if v >= 1.1 else "#ef4444" for v in df_rel2["ratio"]],
                    line=dict(color="#8b5cf6", width=2)),
                hovertemplate="Fecha: %{x|%d %b %Y}<br>Vaq/Ternero: %{y:.3f}x<extra></extra>"
            ))
            fig_c2.update_layout(height=280, yaxis_title="Ratio vaquillona / ternero",
                                  xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                                  plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                  margin=dict(l=10,r=10,t=10,b=50), showlegend=False)
            st.plotly_chart(fig_c2, use_container_width=True)

        st.divider()

        # ── Indicador 3: Evolución del peso promedio ──────────────────────────
        st.markdown("### ⚖️ Evolución del peso promedio por categoría")
        st.caption("Animales más livianos en el mercado → restricción de oferta o salida anticipada → señal de liquidación")

        cats_peso = ["Terneros","Novillitos","Novillos","Vaquillonas"]
        df_peso = df_todos[df_todos["categoria"].isin(cats_peso)].copy()
        df_peso = df_peso.dropna(subset=["peso_prom"])

        if not df_peso.empty:
            fig_c3 = px.line(df_peso, x="fecha_dt", y="peso_prom", color="categoria",
                             markers=True,
                             labels={"fecha_dt":"Fecha","peso_prom":"Peso prom (kg)","categoria":"Categoría"},
                             color_discrete_sequence=px.colors.qualitative.Set2)
            fig_c3.update_layout(height=300,
                                  xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                                  legend=dict(orientation="h", y=-0.25),
                                  plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                  margin=dict(l=10,r=10,t=10,b=70))
            st.plotly_chart(fig_c3, use_container_width=True)

        st.divider()

        # ── Indicador 4: Volumen operado ──────────────────────────────────────
        st.markdown("### 📦 Volumen operado por remate")
        st.caption("Caída en cabezas operadas puede indicar retención o menor oferta disponible")

        df_vol = df_todos.groupby(["fecha_dt","remate"])["cantidad"].sum().reset_index()
        fig_c4 = px.bar(df_vol, x="fecha_dt", y="cantidad",
                        labels={"fecha_dt":"Fecha","cantidad":"Cabezas totales"},
                        color_discrete_sequence=["#3b82f6"])
        fig_c4.update_layout(height=260,
                              xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                              plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                              margin=dict(l=10,r=10,t=10,b=50))
        st.plotly_chart(fig_c4, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 7 — Tipo de cambio
# ════════════════════════════════════════════════════════════════════════════════
with tab7:
    st.subheader("💱 Precios en USD — Tipo de cambio oficial")

    df_tc = obtener_tc_historico()

    if df_tc.empty:
        st.warning("⚠️ No se pudo obtener el tipo de cambio automáticamente.")
        tc_manual = st.number_input("Ingresá el TC manual (ARS/USD)", min_value=100.0,
                                     max_value=10000.0, value=1200.0, step=10.0)
        df_tc = pd.DataFrame([{"fecha": pd.to_datetime(date.today()), "tc_oficial": tc_manual}])

    # TC para cada remate
    remates_con_tc = []
    for _, rem in remates_df.iterrows():
        tc = tc_para_fecha(rem["fecha_remate"], df_tc)
        remates_con_tc.append({
            "remate": rem["numero"],
            "fecha": rem["fecha_remate"],
            "tc_oficial": tc
        })
    df_rem_tc = pd.DataFrame(remates_con_tc)

    # Fuente del TC
    fuente_tc = df_tc["fuente"].iloc[0] if "fuente" in df_tc.columns and not df_tc.empty else "Desconocida"
    st.caption(f"Fuente: **{fuente_tc}** · Actualizado cada 30 min")

    # TC actual (último disponible)
    if not df_tc.empty:
        tc_hoy = df_tc.iloc[-1]["tc_oficial"]
        col1, col2, col3 = st.columns(3)
        col1.metric("TC actual", f"${tc_hoy:,.0f}", "ARS/USD")
        col2.metric("TC máximo 2026", f"${df_tc['tc_oficial'].max():,.0f}")
        col3.metric("TC mínimo 2026", f"${df_tc['tc_oficial'].min():,.0f}")

    st.divider()

    # Mostrar TC por remate
    st.markdown("### TC por fecha de remate")
    df_show = df_rem_tc.copy()
    df_show["tc_oficial"] = df_show["tc_oficial"].apply(
        lambda x: f"${x:,.0f}" if x else "⚠️ Sin datos"
    )
    df_show.columns = ["N° Remate", "Fecha", "TC (ARS/USD)"]
    st.dataframe(df_show, use_container_width=True, hide_index=True)

    st.divider()

    # Evolución TC
    st.markdown("### Evolución del tipo de cambio")
    fig_tc = px.line(df_tc, x="fecha", y="tc_oficial",
                     labels={"fecha":"Fecha","tc_oficial":"ARS por USD"},
                     color_discrete_sequence=["#22c55e"])
    fig_tc.update_layout(height=300,
                          xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                          yaxis_tickformat="$,.0f",
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                          margin=dict(l=10,r=10,t=10,b=50))
    st.plotly_chart(fig_tc, use_container_width=True)

    st.divider()

    # Precios en USD por categoría — todos los remates
    st.markdown("### Precio por categoría en USD — evolución")

    df_todos2 = query("""
        SELECT p.remate, p.fecha_remate, p.categoria, p.promedio
        FROM precios p
        WHERE p.formulario='INVERNADA' AND p.es_resumen=1
        ORDER BY p.fecha_remate, p.remate
    """)

    if not df_todos2.empty:
        df_todos2["fecha_dt"] = pd.to_datetime(df_todos2["fecha_remate"], errors="coerce")
        df_todos2 = df_todos2.sort_values("fecha_dt")
        df_todos2["tc"] = df_todos2["fecha_remate"].apply(lambda f: tc_para_fecha(f, df_tc))
        df_todos2["precio_usd"] = df_todos2.apply(
            lambda r: r["promedio"] / r["tc"] if r["tc"] and r["tc"] > 0 else None, axis=1
        )
        df_todos2 = df_todos2.dropna(subset=["precio_usd"])

        cats_usd = st.multiselect(
            "Categorías",
            df_todos2["categoria"].unique().tolist(),
            default=[c for c in ["Terneros","Novillitos","Vaquillonas"]
                     if c in df_todos2["categoria"].unique()]
        )

        if cats_usd:
            df_plot_usd = df_todos2[df_todos2["categoria"].isin(cats_usd)]
            fig_usd = px.line(df_plot_usd, x="fecha_dt", y="precio_usd", color="categoria",
                              markers=True,
                              labels={"fecha_dt":"Fecha","precio_usd":"USD/kg","categoria":"Categoría"},
                              color_discrete_sequence=px.colors.qualitative.Set1)
            fig_usd.update_layout(height=350,
                                   yaxis_tickformat="$,.2f",
                                   yaxis_title="USD/kg",
                                   xaxis=dict(tickformat="%d %b %Y", tickangle=-30),
                                   legend=dict(orientation="h", y=-0.25),
                                   plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                   margin=dict(l=10,r=10,t=10,b=70))
            st.plotly_chart(fig_usd, use_container_width=True)
