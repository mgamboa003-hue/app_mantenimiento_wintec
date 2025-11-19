from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, make_response
import pandas as pd
import os
from flask_mail import Mail, Message
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm, mm
from datetime import datetime, timedelta
import io
import csv
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics import renderPDF
from io import BytesIO
from reportlab.platypus import Table, TableStyle
from reportlab.lib.utils import ImageReader
from flask import jsonify
import matplotlib
matplotlib.use('Agg')  # para que no necesite pantalla
import matplotlib.pyplot as plt

# ===================== FUNCIONES DE NORMALIZACIÓN =====================

def normalizar_texto(texto):
    """Deja el texto con primera letra mayúscula y resto minúscula."""
    if texto is None:
        return None
    texto = str(texto).strip()
    if texto == "":
        return ""
    return texto.lower().capitalize()


def normalizar_usuario(nombre):
    """Normaliza el nombre de usuario para evitar problemas de may/min."""
    if not nombre:
        return ""
    return str(nombre).strip().lower()


def normalizar_rol(rol):
    """Normaliza el rol (admin, tecnico, etc.) a minúsculas."""
    if not rol:
        return ""
    return str(rol).strip().lower()


# ===================== MANTENCIONES =====================

def cargar_mantenciones():
    """Lee mantenciones.csv y normaliza Máquinas y Responsables."""
    try:
        df = pd.read_csv("mantenciones.csv")
    except FileNotFoundError:
        return pd.DataFrame()

    for col in ['Máquina', 'Responsable']:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .str.lower()
                .str.capitalize()
            )

    return df


def aplicar_filtros(df, maquina=None, responsable=None, fecha_desde=None, fecha_hasta=None):
    """
    Aplica filtros sobre el DataFrame de mantenciones.
    Todos los parámetros son opcionales.
    """
    if df.empty:
        return df

    # Normalizar valores de filtros
    if maquina and maquina not in ["Todas", "todos"]:
        maquina = normalizar_texto(maquina)
    else:
        maquina = "Todas"

    if responsable and responsable not in ["Todos", "todos"]:
        responsable = normalizar_texto(responsable)
    else:
        responsable = "Todos"

    # Asegurar tipo fecha
    if 'Fecha' in df.columns:
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
        df = df.dropna(subset=['Fecha'])

    df_filtrado = df.copy()

    # Filtro por máquina
    if maquina != "Todas" and 'Máquina' in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado['Máquina'] == maquina]

    # Filtro por responsable
    if responsable != "Todos" and 'Responsable' in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado['Responsable'] == responsable]

    # Filtro por fechas
    if fecha_desde:
        try:
            f_desde = datetime.strptime(fecha_desde, "%Y-%m-%d").date()
            df_filtrado = df_filtrado[df_filtrado['Fecha'].dt.date >= f_desde]
        except ValueError:
            pass

    if fecha_hasta:
        try:
            f_hasta = datetime.strptime(fecha_hasta, "%Y-%m-%d").date()
            df_filtrado = df_filtrado[df_filtrado['Fecha'].dt.date <= f_hasta]
        except ValueError:
            pass

    return df_filtrado


# ===================== FLASK APP / MAIL =====================

app = Flask(__name__)
app.secret_key = 'wintec_secret_key'
load_dotenv()

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.getenv("MAIL_PASSWORD")

mail = Mail(app)

USERS_FILE = "usuarios.csv"


# ===================== USUARIOS =====================

def cargar_usuarios():
    """Lee usuarios desde usuarios.csv, normalizando usuario y rol."""
    usuarios = []
    if not os.path.exists(USERS_FILE):
        return usuarios

    with open(USERS_FILE, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row['usuario'] = normalizar_usuario(row.get('usuario', ''))
            row['rol'] = normalizar_rol(row.get('rol', ''))
            usuarios.append(row)
    return usuarios


def guardar_usuarios(lista_usuarios):
    """Guarda la lista completa de usuarios en usuarios.csv."""
    fieldnames = ['usuario', 'contrasena', 'rol']
    with open(USERS_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(lista_usuarios)


# ===================== RUTAS PRINCIPALES =====================

@app.route('/')
def home():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    # Usuario y rol desde la sesión
    usuario = session.get("usuario", "admin")
    rol = session.get("rol", "admin")
    puede_editar = rol in ['admin', 'tecnico']

    # Leer filtros desde la URL (GET)
    maquina_filtro = request.args.get("maquina", "Todas")
    responsable_filtro = request.args.get("responsable", "Todos")
    fecha_desde = request.args.get("fecha_desde", "")
    fecha_hasta = request.args.get("fecha_hasta", "")

    # Cargar CSV normalizado
    df = cargar_mantenciones()

    # Aseguramos que existan las columnas opcionales
    for col in ['Hora_inicio', 'Hora_fin', 'Duración_horas', 'Tipo', 'Frecuencia_dias', 'Próximo_mantenimiento']:
        if col not in df.columns:
            df[col] = None

    # ================= SEMÁFORO PREVENTIVO POR FILA =================
    hoy = datetime.now().date()

    def dias_restantes_row(row):
        if row.get('Tipo') != 'Preventivo' or pd.isna(row.get('Próximo_mantenimiento')) or row.get('Próximo_mantenimiento') in [None, ""]:
            return None
        try:
            fecha = datetime.strptime(str(row['Próximo_mantenimiento']), "%Y-%m-%d").date()
            return (fecha - hoy).days
        except Exception:
            return None

    if not df.empty:
        df['Dias_restantes_prev'] = df.apply(dias_restantes_row, axis=1)

        def estado_prev_row(row):
            dr = row['Dias_restantes_prev']
            if dr is None:
                return ""
            if dr < 0:
                return "Vencido"
            if dr == 0:
                return "Hoy"
            if dr <= 7:
                return "Próximo"
            return "OK"

        df['Estado_prev'] = df.apply(estado_prev_row, axis=1)
    else:
        df['Estado_prev'] = []

    # ================= LISTAS PARA LOS SELECT =================
    if not df.empty:
        maquinas_unicas = sorted(df['Máquina'].dropna().unique()) if 'Máquina' in df.columns else []
        responsables_unicos = sorted(df['Responsable'].dropna().unique()) if 'Responsable' in df.columns else []
    else:
        maquinas_unicas = []
        responsables_unicos = []

    # ================== APLICAR FILTROS A LA TABLA ==================
    df_filtrado = aplicar_filtros(df, maquina_filtro, responsable_filtro, fecha_desde, fecha_hasta)

    if not df_filtrado.empty:
        df_filtrado['Fecha'] = pd.to_datetime(df_filtrado['Fecha'], errors='coerce')
        df_tabla = df_filtrado[['Máquina', 'Fecha', 'Descripción', 'Responsable', 'Duración_horas', 'Estado_prev']].copy()
        df_tabla['Fecha'] = df_tabla['Fecha'].dt.strftime('%d-%m-%Y')
    else:
        df_tabla = pd.DataFrame(columns=['Máquina', 'Fecha', 'Descripción', 'Responsable', 'Duración_horas', 'Estado_prev'])

    # --------- RESUMEN PREVENTIVOS PARA EL AVISO (banner arriba) ---------
    total_prev = 0
    vencidos_prev = 0
    proximos_prev = 0

    try:
        prev = df[df['Tipo'] == 'Preventivo'].copy()
        prev_valid = prev[prev['Dias_restantes_prev'].notna()]

        if not prev_valid.empty:
            total_prev = len(prev_valid)
            vencidos_prev = int((prev_valid['Dias_restantes_prev'] < 0).sum())
            proximos_prev = int(prev_valid['Dias_restantes_prev'].between(0, 7).sum())
    except Exception:
        pass
    # ---------------------------------------------------------------------

    return render_template(
        'home.html',
        title="Mantenimiento Wintec",
        usuario=usuario,
        rol=rol,
        puede_editar=puede_editar,
        mantenimientos=df_tabla.to_dict(orient='records'),
        total_prev=total_prev,
        vencidos_prev=vencidos_prev,
        proximos_prev=proximos_prev,
        maquinas=maquinas_unicas,
        responsables=responsables_unicos,
        maquina_filtro=maquina_filtro,
        responsable_filtro=responsable_filtro,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_raw = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        username = normalizar_usuario(username_raw)

        usuarios = cargar_usuarios()
        usuario_encontrado = None

        for u in usuarios:
            if u['usuario'] == username and u['contrasena'] == password:
                usuario_encontrado = u
                break

        if usuario_encontrado:
            session['logged_in'] = True
            session['usuario'] = usuario_encontrado['usuario']
            session['rol'] = usuario_encontrado['rol']

            flash(f"Bienvenido {usuario_encontrado['usuario']}", "success")
            return redirect(url_for('home'))
        else:
            flash("Usuario o contraseña incorrectos.", "danger")

    return render_template('login.html', title="Iniciar sesión")


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ===================== CRUD MANTENCIONES =====================

@app.route('/agregar', methods=['POST'])
def agregar_mantenimiento():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    rol = session.get("rol", "admin")
    if rol not in ['admin', 'tecnico']:
        flash("No tienes permisos para agregar mantenimientos.", "warning")
        return redirect(url_for('home'))

    hora_inicio = request.form.get('hora_inicio', '').strip()
    hora_fin = request.form.get('hora_fin', '').strip()
    duracion = request.form.get('duracion', '').strip()

    tipo = request.form.get('tipo', 'Correctivo')
    freq_str = request.form.get('frecuencia_dias', '').strip()
    frecuencia = int(freq_str) if freq_str.isdigit() and int(freq_str) > 0 else None

    proximo = None
    if tipo == 'Preventivo' and frecuencia:
        fecha_base = datetime.strptime(request.form['fecha'], "%Y-%m-%d")
        proximo_dt = fecha_base + timedelta(days=frecuencia)
        proximo = proximo_dt.strftime("%Y-%m-%d")

    maquina_normalizada = normalizar_texto(request.form['maquina'])
    responsable_normalizado = normalizar_texto(request.form['responsable'])

    nuevo = {
        'Máquina': maquina_normalizada,
        'Fecha': request.form['fecha'],
        'Descripción': request.form['descripcion'],
        'Responsable': responsable_normalizado,
        'Hora_inicio': hora_inicio if hora_inicio != '' else None,
        'Hora_fin': hora_fin if hora_fin != '' else None,
        'Duración_horas': duracion if duracion != '' else None,
        'Tipo': tipo,
        'Frecuencia_dias': frecuencia,
        'Próximo_mantenimiento': proximo
    }

    df = cargar_mantenciones()
    for col in ['Hora_inicio', 'Hora_fin', 'Duración_horas', 'Tipo', 'Frecuencia_dias', 'Próximo_mantenimiento']:
        if col not in df.columns:
            df[col] = None

    df = pd.concat([df, pd.DataFrame([nuevo])], ignore_index=True)
    df.to_csv("mantenciones.csv", index=False)

    flash("Mantenimiento agregado exitosamente", "success")
    return redirect(url_for('home'))


@app.route('/eliminar/<int:indice>', methods=['POST'])
def eliminar_mantenimiento(indice):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    rol = session.get("rol", "admin")
    if rol not in ['admin', 'tecnico']:
        flash("No tienes permisos para eliminar mantenimientos.", "warning")
        return redirect(url_for('home'))

    df = cargar_mantenciones()

    if indice < 0 or indice >= len(df):
        flash("Registro no encontrado", "danger")
        return redirect(url_for('home'))

    df = df.drop(index=indice).reset_index(drop=True)
    df.to_csv("mantenciones.csv", index=False)
    flash("Mantenimiento eliminado correctamente", "success")
    return redirect(url_for('home'))


@app.route('/editar/<int:indice>', methods=['POST'])
def editar_mantenimiento(indice):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    rol = session.get("rol", "admin")
    if rol not in ['admin', 'tecnico']:
        flash("No tienes permisos para editar mantenimientos.", "warning")
        return redirect(url_for('home'))

    df = cargar_mantenciones()

    if indice < 0 or indice >= len(df):
        flash("Registro no encontrado", "danger")
        return redirect(url_for('home'))

    for col in ['Hora_inicio', 'Hora_fin', 'Duración_horas', 'Tipo', 'Frecuencia_dias']:
        if col not in df.columns:
            df[col] = None

    df.loc[indice, 'Máquina'] = normalizar_texto(request.form['maquina'])
    df.loc[indice, 'Fecha'] = request.form['fecha']
    df.loc[indice, 'Descripción'] = request.form['descripcion']
    df.loc[indice, 'Responsable'] = normalizar_texto(request.form['responsable'])

    hora_inicio = request.form.get('hora_inicio', '').strip()
    hora_fin = request.form.get('hora_fin', '').strip()
    duracion = request.form.get('duracion', '').strip()

    df.loc[indice, 'Hora_inicio'] = hora_inicio if hora_inicio != '' else None
    df.loc[indice, 'Hora_fin'] = hora_fin if hora_fin != '' else None
    df.loc[indice, 'Duración_horas'] = duracion if duracion != '' else None

    tipo = request.form.get('tipo', 'Correctivo')
    freq_str = request.form.get('frecuencia_dias', '').strip()
    frecuencia = int(freq_str) if freq_str.isdigit() and int(freq_str) > 0 else None

    df.loc[indice, 'Tipo'] = tipo
    df.loc[indice, 'Frecuencia_dias'] = frecuencia

    df.to_csv("mantenciones.csv", index=False)
    flash("Mantenimiento actualizado correctamente", "success")
    return redirect(url_for('home'))


# ===================== DASHBOARD =====================

@app.route('/dashboard')
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    usuario = session.get("usuario", "admin")
    rol = session.get("rol", "admin")

    df = cargar_mantenciones()

    if df.empty or 'Fecha' not in df.columns:
        return render_template(
            'dashboard.html',
            title="Dashboard de mantenimiento",
            usuario=usuario,
            rol=rol,
            total_mantenimientos=0,
            total_maquinas=0,
            fallas_mes_actual=0,
            mtbf_global=None,
            mttr_global=None,
            disponibilidad_global=None,
            labels_mes=[],
            values_mes=[]
        )

    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])

    if df.empty:
        return render_template(
            'dashboard.html',
            title="Dashboard de mantenimiento",
            usuario=usuario,
            rol=rol,
            total_mantenimientos=0,
            total_maquinas=0,
            fallas_mes_actual=0,
            mtbf_global=None,
            mttr_global=None,
            disponibilidad_global=None,
            labels_mes=[],
            values_mes=[]
        )

    total_mantenimientos = len(df)
    total_maquinas = df['Máquina'].nunique() if 'Máquina' in df.columns else 0

    hoy = pd.Timestamp.today()
    periodo_actual = hoy.to_period('M')
    df['Periodo'] = df['Fecha'].dt.to_period('M')
    fallas_mes_actual = df[df['Periodo'] == periodo_actual].shape[0]

    mtbf_global = None
    if 'Máquina' in df.columns:
        df_mtbf = df.sort_values(by=['Máquina', 'Fecha'])
        df_mtbf['DifDias'] = df_mtbf.groupby('Máquina')['Fecha'].diff().dt.days
        if df_mtbf['DifDias'].notna().any():
            mtbf_global = round(df_mtbf['DifDias'].dropna().mean(), 1)

    mttr_global = None
    if 'Duración_horas' in df.columns:
        df['Duración_horas'] = pd.to_numeric(df['Duración_horas'], errors='coerce')
        if df['Duración_horas'].notna().any():
            mttr_global = round(df['Duración_horas'].dropna().mean(), 1)

    disponibilidad_global = None
    if all(col in df.columns for col in ['Hora_inicio', 'Hora_fin']):
        df_horas = df.copy()
        df_horas['Hora_inicio'] = df_horas['Hora_inicio'].fillna('')
        df_horas['Hora_fin'] = df_horas['Hora_fin'].fillna('')

        df_horas['Inicio_dt'] = pd.to_datetime(
            df_horas['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + df_horas['Hora_inicio'],
            errors='coerce'
        )
        df_horas['Fin_dt'] = pd.to_datetime(
            df_horas['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + df_horas['Hora_fin'],
            errors='coerce'
        )

        mask = df_horas['Inicio_dt'].notna() & df_horas['Fin_dt'].notna()
        df_valid = df_horas[mask].copy()

        if not df_valid.empty:
            df_valid['Downtime_horas'] = (
                df_valid['Fin_dt'] - df_valid['Inicio_dt']
            ).dt.total_seconds() / 3600.0
            df_valid['Downtime_horas'] = df_valid['Downtime_horas'].clip(lower=0)

            fecha_min = df_valid['Fecha'].min()
            fecha_max = df_valid['Fecha'].max()
            dias_periodo = (fecha_max - fecha_min).days + 1
            horas_totales = dias_periodo * 24

            downtime_total = df_valid['Downtime_horas'].sum()
            if horas_totales > 0:
                disponibilidad_global = round(
                    (horas_totales - downtime_total) / horas_totales * 100, 2
                )

    fallas_por_mes = (
        df
        .groupby(df['Fecha'].dt.to_period('M'))
        .size()
        .sort_index()
    )
    labels_mes = [str(p) for p in fallas_por_mes.index]
    values_mes = fallas_por_mes.tolist()

    return render_template(
        'dashboard.html',
        title="Dashboard de mantenimiento",
        usuario=usuario,
        rol=rol,
        total_mantenimientos=total_mantenimientos,
        total_maquinas=total_maquinas,
        fallas_mes_actual=fallas_mes_actual,
        mtbf_global=mtbf_global,
        mttr_global=mttr_global,
        disponibilidad_global=disponibilidad_global,
        labels_mes=labels_mes,
        values_mes=values_mes
    )


# ===================== EXPORTAR DATOS =====================

@app.route('/exportar_datos')
def exportar_datos():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if df.empty or 'Fecha' not in df.columns:
        flash("No hay datos para exportar.", "warning")
        return redirect(url_for('dashboard'))

    maquina = request.args.get('maquina', '').strip()
    responsable = request.args.get('responsable', '').strip()
    fecha_desde = request.args.get('fecha_desde', '').strip()
    fecha_hasta = request.args.get('fecha_hasta', '').strip()

    if maquina:
        maquina = normalizar_texto(maquina)
    if responsable:
        responsable = normalizar_texto(responsable)

    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])

    if maquina and 'Máquina' in df.columns:
        df = df[df['Máquina'] == maquina]

    if responsable and 'Responsable' in df.columns:
        df = df[df['Responsable'] == responsable]

    if fecha_desde:
        try:
            f_desde = pd.to_datetime(fecha_desde)
            df = df[df['Fecha'] >= f_desde]
        except Exception:
            pass

    if fecha_hasta:
        try:
            f_hasta = pd.to_datetime(fecha_hasta)
            df = df[df['Fecha'] <= f_hasta]
        except Exception:
            pass

    if df.empty:
        flash("No hay datos que coincidan con los filtros para exportar.", "warning")
        return redirect(url_for('dashboard'))

    df = df.sort_values(by='Fecha')

    from io import StringIO
    output = StringIO()
    df.to_csv(output, index=False, sep=';', encoding='utf-8-sig')
    csv_data = output.getvalue()

    resp = make_response(csv_data)
    resp.headers["Content-Disposition"] = "attachment; filename=mantenimientos_filtrados.csv"
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"

    return resp


# ===================== ANÁLISIS / PARETO =====================

@app.route('/analisis')
def analisis():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if df.empty or 'Máquina' not in df.columns:
        flash("No hay datos suficientes para análisis.", "warning")
        return render_template(
            'analisis.html',
            title="Análisis de mantenimiento - Pareto",
            pareto=[],
            labels=[],
            counts=[],
            acumulado=[]
        )

    df = df.dropna(subset=['Máquina'])

    pareto = df['Máquina'].value_counts().reset_index()
    pareto.columns = ['Máquina', 'Cantidad']

    total = pareto['Cantidad'].sum()
    pareto['Porcentaje'] = (pareto['Cantidad'] / total * 100).round(1)
    pareto['Acumulado'] = pareto['Porcentaje'].cumsum().round(1)

    labels = pareto['Máquina'].tolist()
    counts = pareto['Cantidad'].tolist()
    acumulado = pareto['Acumulado'].tolist()

    pareto_registros = pareto.to_dict(orient='records')

    return render_template(
        'analisis.html',
        title="Análisis de mantenimiento - Pareto",
        pareto=pareto_registros,
        labels=labels,
        counts=counts,
        acumulado=acumulado
    )


# ===================== REPETITIVIDAD =====================

@app.route('/repetitividad')
def repetitividad():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if df.empty:
        flash("No se encontró el archivo de mantenciones.", "danger")
        return render_template(
            'repetitividad.html',
            title="Repetitividad de fallas",
            rep=[],
            labels=[],
            values=[],
            maquinas=[],
            maquina_seleccionada="",
            fecha_desde="",
            fecha_hasta=""
        )

    if 'Descripción' not in df.columns or 'Fecha' not in df.columns:
        flash("No existen columnas 'Descripción' y/o 'Fecha' en los datos.", "danger")
        return render_template(
            'repetitividad.html',
            title="Repetitividad de fallas",
            rep=[],
            labels=[],
            values=[],
            maquinas=[],
            maquina_seleccionada="",
            fecha_desde="",
            fecha_hasta=""
        )

    df = df.dropna(subset=['Descripción'])
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])

    maquina_sel = (request.args.get('maquina') or "").strip()
    if maquina_sel:
        maquina_sel = normalizar_texto(maquina_sel)

    fecha_desde = (request.args.get('fecha_desde') or "").strip()
    fecha_hasta = (request.args.get('fecha_hasta') or "").strip()

    df_filtrado = df.copy()

    if maquina_sel and 'Máquina' in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado['Máquina'] == maquina_sel]

    if fecha_desde:
        try:
            f_desde = pd.to_datetime(fecha_desde)
            df_filtrado = df_filtrado[df_filtrado['Fecha'] >= f_desde]
        except Exception:
            pass

    if fecha_hasta:
        try:
            f_hasta = pd.to_datetime(fecha_hasta)
            df_filtrado = df_filtrado[df_filtrado['Fecha'] <= f_hasta]
        except Exception:
            pass

    if df_filtrado.empty:
        flash("No hay registros que coincidan con esos filtros.", "warning")
        return render_template(
            'repetitividad.html',
            title="Repetitividad de fallas",
            rep=[],
            labels=[],
            values=[],
            maquinas=sorted(df['Máquina'].dropna().unique().tolist()) if 'Máquina' in df.columns else [],
            maquina_seleccionada=maquina_sel,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta
        )

    rep = df_filtrado['Descripción'].value_counts().reset_index()
    rep.columns = ['Descripción', 'Cantidad']

    total = rep['Cantidad'].sum()
    rep['Porcentaje'] = (rep['Cantidad'] / total * 100).round(1)

    rep_top = rep.head(15)

    labels = rep_top['Descripción'].tolist()
    values = rep_top['Cantidad'].tolist()

    rep_list = rep.to_dict(orient='records')

    maquinas_unicas = []
    if 'Máquina' in df.columns:
        maquinas_unicas = sorted(df['Máquina'].dropna().unique().tolist())

    return render_template(
        'repetitividad.html',
        title="Repetitividad de fallas",
        rep=rep_list,
        labels=labels,
        values=values,
        maquinas=maquinas_unicas,
        maquina_seleccionada=maquina_sel,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta
    )


# ===================== MTBF =====================

@app.route('/mtbf')
def mtbf():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    df = df.dropna(subset=['Máquina', 'Fecha'])
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])
    df = df.sort_values(by=['Máquina', 'Fecha'])

    df['DifDias'] = df.groupby('Máquina')['Fecha'].diff().dt.days

    mtbf_df = df.groupby('Máquina').agg(
        Fallas=('Fecha', 'count'),
        MTBF_dias=('DifDias', 'mean')
    ).reset_index()

    mtbf_df['MTBF_dias'] = mtbf_df['MTBF_dias'].round(1)
    mtbf_df['MTBF_dias'] = mtbf_df['MTBF_dias'].where(mtbf_df['MTBF_dias'].notna(), None)
    mtbf_df = mtbf_df.sort_values(by='MTBF_dias', na_position='last')

    labels = mtbf_df['Máquina'].tolist()
    values = mtbf_df['MTBF_dias'].fillna(0).tolist()

    mtbf_list = mtbf_df.to_dict(orient='records')

    return render_template(
        'mtbf.html',
        title="MTBF - Días entre fallas por máquina",
        mtbf=mtbf_list,
        labels=labels,
        values=values
    )


# ===================== MTTR =====================

@app.route('/mttr')
def mttr():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if 'Duración_horas' not in df.columns:
        flash("Aún no hay datos de duración para calcular MTTR.", "warning")
        return redirect(url_for('analisis'))

    df['Duración_horas'] = pd.to_numeric(df['Duración_horas'], errors='coerce')
    df_valid = df.dropna(subset=['Máquina', 'Duración_horas'])

    if df_valid.empty:
        flash("No hay registros con duración para calcular MTTR.", "warning")
        return redirect(url_for('analisis'))

    mttr_df = df_valid.groupby('Máquina').agg(
        Intervenciones=('Duración_horas', 'count'),
        MTTR_horas=('Duración_horas', 'mean')
    ).reset_index()

    mttr_df['MTTR_horas'] = mttr_df['MTTR_horas'].round(1)
    mttr_df = mttr_df.sort_values(by='MTTR_horas', ascending=False)

    labels = mttr_df['Máquina'].tolist()
    values = mttr_df['MTTR_horas'].tolist()

    mttr_list = mttr_df.to_dict(orient='records')

    return render_template(
        'mttr.html',
        title="MTTR - Tiempo promedio de reparación por máquina",
        mttr=mttr_list,
        labels=labels,
        values=values
    )


# ===================== DISPONIBILIDAD =====================

@app.route('/disponibilidad')
def disponibilidad():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    for col in ['Máquina', 'Fecha', 'Hora_inicio', 'Hora_fin']:
        if col not in df.columns:
            flash("Faltan datos de horas para calcular disponibilidad.", "warning")
            return redirect(url_for('analisis'))

    df = df.dropna(subset=['Máquina', 'Fecha'])
    if df.empty:
        flash("No hay datos suficientes para calcular disponibilidad.", "warning")
        return redirect(url_for('analisis'))

    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])
    if df.empty:
        flash("No hay fechas válidas para calcular disponibilidad.", "warning")
        return redirect(url_for('analisis'))

    df['Hora_inicio'] = df['Hora_inicio'].fillna('')
    df['Hora_fin'] = df['Hora_fin'].fillna('')

    df['Inicio_dt'] = pd.to_datetime(
        df['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + df['Hora_inicio'],
        errors='coerce'
    )
    df['Fin_dt'] = pd.to_datetime(
        df['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + df['Hora_fin'],
        errors='coerce'
    )

    mask = df['Inicio_dt'].notna() & df['Fin_dt'].notna()
    df_valid = df[mask].copy()

    if df_valid.empty:
        flash("No hay registros con hora de inicio y fin para calcular disponibilidad.", "warning")
        return redirect(url_for('analisis'))

    df_valid['Downtime_horas'] = (df_valid['Fin_dt'] - df_valid['Inicio_dt']).dt.total_seconds() / 3600.0
    df_valid['Downtime_horas'] = df_valid['Downtime_horas'].clip(lower=0)

    resultados = []
    for maquina, grupo in df_valid.groupby('Máquina'):
        fecha_min = grupo['Fecha'].min()
        fecha_max = grupo['Fecha'].max()
        dias_periodo = (fecha_max - fecha_min).days + 1
        horas_totales = dias_periodo * 24

        downtime_total = grupo['Downtime_horas'].sum()
        disponibilidad_val = None
        if horas_totales > 0:
            disponibilidad_val = (horas_totales - downtime_total) / horas_totales * 100

        resultados.append({
            'Máquina': maquina,
            'Fecha_inicio': fecha_min.date().isoformat(),
            'Fecha_fin': fecha_max.date().isoformat(),
            'Horas_totales': round(horas_totales, 1),
            'Downtime_total': round(downtime_total, 1),
            'Disponibilidad': round(disponibilidad_val, 2) if disponibilidad_val is not None else None
        })

    res_df = pd.DataFrame(resultados)
    res_df = res_df.sort_values(by='Disponibilidad', ascending=True)

    labels = res_df['Máquina'].tolist()
    values = res_df['Disponibilidad'].tolist()

    disp_list = res_df.to_dict(orient='records')

    return render_template(
        'disponibilidad.html',
        title="Disponibilidad por máquina",
        disp=disp_list,
        labels=labels,
        values=values
    )


# ===================== INFORME PDF =====================

@app.route('/informe_pdf')
def informe_pdf():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    usuario = session.get("usuario", "admin")
    rol = session.get("rol", "admin")

    maquina_filtro = request.args.get("maquina", "Todas")
    responsable_filtro = request.args.get("responsable", "Todos")
    fecha_desde = request.args.get("fecha_desde", "")
    fecha_hasta = request.args.get("fecha_hasta", "")

    df = cargar_mantenciones()
    df = aplicar_filtros(df, maquina_filtro, responsable_filtro, fecha_desde, fecha_hasta)

    if df.empty or 'Fecha' not in df.columns:
        total_mantenimientos = 0
        total_maquinas = 0
        mtbf_global = None
        mttr_global = None
        disponibilidad_global = None
        detalle = []
    else:
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
        df = df.dropna(subset=['Fecha'])

        total_mantenimientos = len(df)
        total_maquinas = df['Máquina'].nunique() if 'Máquina' in df.columns else 0

        mtbf_global = None
        if 'Máquina' in df.columns:
            df_mtbf = df.sort_values(by=['Máquina', 'Fecha'])
            df_mtbf['DifDias'] = df_mtbf.groupby('Máquina')['Fecha'].diff().dt.days
            if df_mtbf['DifDias'].notna().any():
                mtbf_global = round(df_mtbf['DifDias'].dropna().mean(), 1)

        mttr_global = None
        if 'Duración_horas' in df.columns:
            df['Duración_horas'] = pd.to_numeric(df['Duración_horas'], errors='coerce')
            if df['Duración_horas'].notna().any():
                mttr_global = round(df['Duración_horas'].dropna().mean(), 1)

        disponibilidad_global = None
        if all(col in df.columns for col in ['Hora_inicio', 'Hora_fin']):
            df_horas = df.copy()
            df_horas['Hora_inicio'] = df_horas['Hora_inicio'].fillna('')
            df_horas['Hora_fin'] = df_horas['Hora_fin'].fillna('')

            df_horas['Inicio_dt'] = pd.to_datetime(
                df_horas['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + df_horas['Hora_inicio'],
                errors='coerce'
            )
            df_horas['Fin_dt'] = pd.to_datetime(
                df_horas['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + df_horas['Hora_fin'],
                errors='coerce'
            )

            mask = df_horas['Inicio_dt'].notna() & df_horas['Fin_dt'].notna()
            df_valid = df_horas[mask].copy()

            if not df_valid.empty:
                df_valid['Downtime_horas'] = (
                    df_valid['Fin_dt'] - df_valid['Inicio_dt']
                ).dt.total_seconds() / 3600.0
                df_valid['Downtime_horas'] = df_valid['Downtime_horas'].clip(lower=0)

                fecha_min = df_valid['Fecha'].min()
                fecha_max = df_valid['Fecha'].max()
                dias_periodo = (fecha_max - fecha_min).days + 1
                horas_totales = dias_periodo * 24

                downtime_total = df_valid['Downtime_horas'].sum()
                if horas_totales > 0:
                    disponibilidad_global = round(
                        (horas_totales - downtime_total) / horas_totales * 100, 2
                    )

        detalle = []
        for _, row in df.sort_values('Fecha').iterrows():
            fecha_str = row['Fecha'].strftime('%d-%m-%Y')
            maquina = row.get('Máquina', '')
            tipo = row.get('Tipo', '')
            resp = row.get('Responsable', '')
            dur = row.get('Duración_horas', '')
            if pd.isna(dur):
                dur_str = '-'
            else:
                dur_str = str(dur)
            detalle.append([fecha_str, maquina, tipo, resp, dur_str])

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    header_height = 30 * mm
    azul_wintec = colors.HexColor("#1F3B8F")
    logo_path = os.path.join(app.root_path, 'static', 'logo_wintec.png')

    def dibujar_header():
        c.setFillColor(azul_wintec)
        c.rect(0, height - header_height, width, header_height, fill=1, stroke=0)

        c.setStrokeColor(colors.white)
        c.setLineWidth(1)
        c.line(0, height - header_height, width, height - header_height)

        if os.path.exists(logo_path):
            c.drawImage(
                logo_path,
                15 * mm,
                height - header_height + 6 * mm,
                width=45 * mm,
                height=20 * mm,
                preserveAspectRatio=True,
                mask='auto'
            )

        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(70 * mm, height - header_height + 16 * mm, "Informe de Mantenimiento")

        c.setFont("Helvetica-Bold", 9)
        fecha_str = datetime.now().strftime("%d-%m-%Y %H:%M")
        c.drawRightString(
            width - 15 * mm,
            height - header_height + 8 * mm,
            f"Generado el: {fecha_str}  |  Usuario: {usuario}  |  Rol: {rol}"
        )

    def dibujar_footer():
        page_num = c.getPageNumber()
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.grey)
        c.drawCentredString(
            width / 2.0,
            10 * mm,
            f"Wintec S.A. - Departamento de Mantenimiento Industrial  |  Página {page_num}"
        )

    texto_maquina = f"Máquina: {maquina_filtro}" if maquina_filtro != "Todas" else "Máquina: Todas"
    texto_resp = f"Responsable: {responsable_filtro}" if responsable_filtro != "Todos" else "Responsable: Todos"
    texto_desde = fecha_desde if fecha_desde else "-"
    texto_hasta = fecha_hasta if fecha_hasta else "-"

    # -------- PÁGINA 1: PORTADA --------
    dibujar_header()

    c.setFillColor(colors.whitesmoke)
    box_width = width - 60 * mm
    box_height = 60 * mm
    box_x = (width - box_width) / 2.0
    box_y = (height - box_height) / 2.0
    c.rect(box_x, box_y, box_width, box_height, fill=1, stroke=0)

    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width / 2.0, box_y + box_height - 12 * mm, "Informe de Mantenimiento")

    c.setFont("Helvetica", 11)
    c.drawCentredString(width / 2.0, box_y + box_height - 25 * mm, "Resumen de actividades de mantenimiento")

    c.setFont("Helvetica", 10)
    c.drawCentredString(
        width / 2.0,
        box_y + box_height - 40 * mm,
        f"{texto_maquina}   |   {texto_resp}"
    )
    c.drawCentredString(
        width / 2.0,
        box_y + box_height - 50 * mm,
        f"Desde: {texto_desde}   |   Hasta: {texto_hasta}"
    )

    dibujar_footer()
    c.showPage()

    # -------- PÁGINA 2: RESUMEN + GRÁFICOS --------
    dibujar_header()
    c.setFillColor(colors.black)
    y = height - header_height - 15 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(15 * mm, y, "Filtros aplicados")
    y -= 6 * mm
    c.setLineWidth(0.5)
    c.setStrokeColor(colors.grey)
    c.line(15 * mm, y, width - 15 * mm, y)
    y -= 6 * mm

    c.setFont("Helvetica", 9)
    c.drawString(15 * mm, y, f"{texto_maquina}    |    {texto_resp}")
    y -= 5 * mm
    c.drawString(15 * mm, y, f"Desde: {texto_desde}    |    Hasta: {texto_hasta}")
    y -= 10 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(15 * mm, y, "Resumen general")
    y -= 6 * mm
    c.setStrokeColor(colors.grey)
    c.line(15 * mm, y, width - 15 * mm, y)
    y -= 6 * mm

    datos_resumen = [
        ["Mantenimientos totales", "Máquinas involucradas", "MTBF global (días)"],
        [str(total_mantenimientos),
         str(total_maquinas),
         f"{mtbf_global} días" if mtbf_global is not None else "N/A"],
        ["MTTR global (horas)", "Disponibilidad global (%)", ""],
        [f"{mttr_global} h" if mttr_global is not None else "N/A",
         f"{disponibilidad_global} %" if disponibilidad_global is not None else "N/A",
         ""]
    ]

    tabla_resumen = Table(
        datos_resumen,
        colWidths=[(width - 30 * mm) / 3] * 3
    )
    tabla_resumen.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 9),
        ('FONT', (0, 2), (-1, 2), 'Helvetica-Bold', 9),
        ('FONT', (0, 1), (-1, 1), 'Helvetica', 10),
        ('FONT', (0, 3), (-1, 3), 'Helvetica', 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
        ('BACKGROUND', (0, 2), (-1, 2), colors.whitesmoke),
    ]))

    w_tab, h_tab = tabla_resumen.wrap(width - 30 * mm, y)
    tabla_resumen.drawOn(c, 15 * mm, y - h_tab)
    y = y - h_tab - 10 * mm

    graf_height = 45 * mm
    graf_width = width - 30 * mm

    # Gráfico 1: Mantenimientos por máquina
    if not df.empty and 'Máquina' in df.columns:
        conteo = df['Máquina'].fillna("Sin máquina").value_counts().sort_values(ascending=True)

        if not conteo.empty:
            fig, ax = plt.subplots(figsize=(6, 2.6))
            ax.barh(conteo.index, conteo.values, alpha=0.9)
            ax.set_title("Mantenimientos por máquina", fontsize=10)
            ax.set_xlabel("Cantidad", fontsize=9)
            ax.set_ylabel("Máquina", fontsize=9)
            ax.xaxis.grid(True, linestyle='--', linewidth=0.5, alpha=0.5)
            ax.tick_params(axis='y', labelsize=8)
            ax.tick_params(axis='x', labelsize=8)
            plt.tight_layout()

            graf_buffer = io.BytesIO()
            fig.savefig(graf_buffer, format='PNG', dpi=130)
            plt.close(fig)
            graf_buffer.seek(0)

            img = ImageReader(graf_buffer)
            c.drawImage(img, 15 * mm, y - graf_height, width=graf_width, height=graf_height)
            y = y - graf_height - 8 * mm

    # Gráfico 2: Mantenimientos por tipo
    if not df.empty and 'Tipo' in df.columns:
        df_tipo = df.copy()
        df_tipo['Tipo'] = df_tipo['Tipo'].fillna('Sin tipo')
        conteo_tipo = df_tipo['Tipo'].value_counts()

        if not conteo_tipo.empty:
            fig2, ax2 = plt.subplots(figsize=(4.5, 2.5))
            ax2.bar(conteo_tipo.index, conteo_tipo.values, alpha=0.9)
            ax2.set_title("Mantenimientos por tipo", fontsize=10)
            ax2.set_xlabel("Tipo", fontsize=9)
            ax2.set_ylabel("Cantidad", fontsize=9)
            ax2.grid(axis='y', linestyle='--', linewidth=0.5, alpha=0.5)
            ax2.tick_params(axis='x', labelsize=8)
            ax2.tick_params(axis='y', labelsize=8)
            plt.tight_layout()

            graf_buffer2 = io.BytesIO()
            fig2.savefig(graf_buffer2, format='PNG', dpi=130)
            plt.close(fig2)
            graf_buffer2.seek(0)

            img2 = ImageReader(graf_buffer2)

            min_y = 40 * mm
            if y - graf_height < min_y:
                dibujar_footer()
                c.showPage()
                dibujar_header()
                c.setFillColor(colors.black)
                y = height - header_height - 20 * mm
                c.setFont("Helvetica-Bold", 11)
                c.drawString(15 * mm, y, "Indicadores visuales (continuación)")
                y -= 10 * mm

            c.drawImage(img2, 25 * mm, y - graf_height, width=graf_width - 20 * mm, height=graf_height)
            y = y - graf_height - 8 * mm

    dibujar_footer()
    c.showPage()

    # -------- DETALLE MULTIPÁGINA --------
    encabezado_detalle = ["Fecha", "Máquina", "Tipo", "Responsable", "Duración (h)"]
    filas_por_pagina = 25
    start = 0

    while True:
        page_rows = detalle[start:start + filas_por_pagina]
        if not page_rows:
            break

        dibujar_header()
        c.setFillColor(colors.black)
        y = height - header_height - 20 * mm

        titulo_detalle = "Detalle de mantenimientos"
        if start > 0:
            titulo_detalle += " (continuación)"

        c.setFont("Helvetica-Bold", 11)
        c.drawString(15 * mm, y, titulo_detalle)
        y -= 6 * mm
        c.setStrokeColor(colors.grey)
        c.line(15 * mm, y, width - 15 * mm, y)
        y -= 8 * mm

        datos_pagina = [encabezado_detalle] + page_rows
        tabla_detalle = Table(
            datos_pagina,
            colWidths=[25 * mm, 35 * mm, 35 * mm, 40 * mm, 25 * mm]
        )

        style_detalle = [
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 8),
            ('FONT', (0, 1), (-1, -1), 'Helvetica', 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),
            ('ALIGN', (-1, 1), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#F0F0F0")),
        ]

        row_count = len(datos_pagina)
        for row in range(1, row_count):
            if row % 2 == 0:
                style_detalle.append(('BACKGROUND', (0, row), (-1, row), colors.whitesmoke))

        tabla_detalle.setStyle(TableStyle(style_detalle))
        w_tab2, h_tab2 = tabla_detalle.wrap(width - 30 * mm, y)
        tabla_detalle.drawOn(c, 15 * mm, y - h_tab2)

        dibujar_footer()
        c.showPage()

        start += filas_por_pagina

    # -------- PÁGINA FINAL: FIRMAS --------
    dibujar_header()
    c.setFillColor(colors.black)
    y = height - header_height - 25 * mm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(15 * mm, y, "Firmas y aprobación")
    y -= 15 * mm

    c.setFont("Helvetica", 10)

    c.drawString(25 * mm, y, f"Elaborado por: {usuario}")
    c.line(25 * mm, y - 3 * mm, 85 * mm, y - 3 * mm)

    y -= 18 * mm
    c.drawString(25 * mm, y, "Revisado por:")
    c.line(25 * mm, y - 3 * mm, 85 * mm, y - 3 * mm)

    y -= 18 * mm
    c.drawString(25 * mm, y, "Aprobado por:")
    c.line(25 * mm, y - 3 * mm, 85 * mm, y - 3 * mm)

    dibujar_footer()
    c.showPage()
    c.save()

    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name="informe_mantenimiento.pdf",
        mimetype="application/pdf"
    )


# ===================== ANÁLISIS POR MÁQUINA =====================

@app.route('/maquinas')
def maquinas():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if df.empty or 'Máquina' not in df.columns or 'Fecha' not in df.columns:
        flash("No hay datos suficientes para análisis por máquina.", "warning")
        return render_template(
            'maquinas.html',
            title="Análisis por máquina",
            maquinas=[],
            maquina_seleccionada=None,
            total_mant=0,
            total_corr=0,
            total_prev=0,
            disponibilidad=None,
            mtbf=None,
            mttr=None,
            labels_hist=[],
            values_hist=[],
            tabla=[],
            usuario=session.get("usuario"),
            rol=session.get("rol")
        )

    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])

    maquinas = sorted(df['Máquina'].dropna().unique().tolist())

    if not maquinas:
        flash("No hay máquinas registradas aún.", "warning")
        return render_template(
            'maquinas.html',
            title="Análisis por máquina",
            maquinas=[],
            maquina_seleccionada=None,
            total_mant=0,
            total_corr=0,
            total_prev=0,
            disponibilidad=None,
            mtbf=None,
            mttr=None,
            labels_hist=[],
            values_hist=[],
            tabla=[],
            usuario=session.get("usuario"),
            rol=session.get("rol")
        )

    maquina_sel = request.args.get('maquina')
    if not maquina_sel or maquina_sel not in maquinas:
        maquina_sel = maquinas[0]

    sub = df[df['Máquina'] == maquina_sel].copy()

    if 'Tipo' not in sub.columns:
        sub['Tipo'] = None
    if 'Duración_horas' not in sub.columns:
        sub['Duración_horas'] = None

    sub['Duración_horas'] = pd.to_numeric(sub['Duración_horas'], errors='coerce')

    total_mant = len(sub)
    total_corr = int((sub['Tipo'] == 'Correctivo').sum()) if 'Tipo' in sub.columns else 0
    total_prev = int((sub['Tipo'] == 'Preventivo').sum()) if 'Tipo' in sub.columns else 0

    mtbf = None
    if total_mant > 1:
        sub_mtbf = sub.sort_values(by='Fecha')
        sub_mtbf['DifDias'] = sub_mtbf['Fecha'].diff().dt.days
        if sub_mtbf['DifDias'].notna().any():
            mtbf = round(sub_mtbf['DifDias'].dropna().mean(), 1)

    mttr = None
    if sub['Duración_horas'].notna().any():
        mttr = round(sub['Duración_horas'].dropna().mean(), 1)

    disponibilidad = None
    if 'Hora_inicio' in sub.columns and 'Hora_fin' in sub.columns:
        sh = sub.copy()
        sh['Hora_inicio'] = sh['Hora_inicio'].fillna('')
        sh['Hora_fin'] = sh['Hora_fin'].fillna('')

        sh['Inicio_dt'] = pd.to_datetime(
            sh['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + sh['Hora_inicio'],
            errors='coerce'
        )
        sh['Fin_dt'] = pd.to_datetime(
            sh['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + sh['Hora_fin'],
            errors='coerce'
        )

        mask = sh['Inicio_dt'].notna() & sh['Fin_dt'].notna()
        sv = sh[mask].copy()

        if not sv.empty:
            sv['Downtime_horas'] = (sv['Fin_dt'] - sv['Inicio_dt']).dt.total_seconds() / 3600.0
            sv['Downtime_horas'] = sv['Downtime_horas'].clip(lower=0)

            fecha_min = sv['Fecha'].min()
            fecha_max = sv['Fecha'].max()
            dias_periodo = (fecha_max - fecha_min).days + 1
            horas_totales = dias_periodo * 24

            downtime_total = sv['Downtime_horas'].sum()
            if horas_totales > 0:
                disponibilidad = round(
                    (horas_totales - downtime_total) / horas_totales * 100, 2
                )

    hist = (
        sub.groupby(sub['Fecha'].dt.strftime('%Y-%m-%d'))
           .size()
           .reset_index(name='conteo')
           .sort_values('Fecha')
    )

    if not hist.empty:
        labels_hist = hist['Fecha'].tolist()
        values_hist = hist['conteo'].tolist()
    else:
        labels_hist = []
        values_hist = []

    tabla = []
    for _, row in sub.sort_values('Fecha').iterrows():
        tabla.append({
            'Fecha_str': row['Fecha'].strftime('%Y-%m-%d'),
            'Tipo': row.get('Tipo'),
            'Descripción': row.get('Descripción', ''),
            'Responsable': row.get('Responsable', ''),
            'Duración_str': (
                "-" if pd.isna(row.get('Duración_horas'))
                else str(row['Duración_horas'])
            )
        })

    return render_template(
        'maquinas.html',
        title="Análisis por máquina",
        maquinas=maquinas,
        maquina_seleccionada=maquina_sel,
        total_mant=total_mant,
        total_corr=total_corr,
        total_prev=total_prev,
        disponibilidad=disponibilidad,
        mtbf=mtbf,
        mttr=mttr,
        labels_hist=labels_hist,
        values_hist=values_hist,
        tabla=tabla,
        usuario=session.get("usuario"),
        rol=session.get("rol")
    )


@app.route('/maquina/<maquina>')
def maquina_detalle(maquina):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if df.empty or 'Máquina' not in df.columns:
        flash("No hay datos para analizar esta máquina.", "warning")
        return redirect(url_for('maquinas'))

    # IMPORTANTE: las máquinas ya vienen normalizadas desde cargar_mantenciones
    df = df[df['Máquina'] == maquina]

    if df.empty:
        flash(f"No se encontraron registros para la máquina '{maquina}'.", "warning")
        return redirect(url_for('maquinas'))

    if 'Fecha' in df.columns:
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    else:
        df['Fecha'] = pd.NaT

    if 'Duración_horas' in df.columns:
        df['Duración_horas'] = pd.to_numeric(df['Duración_horas'], errors='coerce')
    else:
        df['Duración_horas'] = None

    for col in ['Hora_inicio', 'Hora_fin']:
        if col not in df.columns:
            df[col] = None

    df = df.sort_values(by='Fecha')

    fallas = len(df)
    fecha_primera = df['Fecha'].min()
    fecha_ultima = df['Fecha'].max()

    mtbf = None
    if df['Fecha'].notna().sum() >= 2:
        dif = df['Fecha'].diff().dt.days
        if dif.notna().any():
            mtbf = round(dif.dropna().mean(), 1)

    mttr = None
    if df['Duración_horas'].notna().any():
        mttr = round(df['Duración_horas'].dropna().mean(), 1)

    disponibilidad = None
    if df['Fecha'].notna().any():
        g_horas = df.copy()
        g_horas['Hora_inicio'] = g_horas['Hora_inicio'].fillna('')
        g_horas['Hora_fin'] = g_horas['Hora_fin'].fillna('')

        g_horas['Inicio_dt'] = pd.to_datetime(
            g_horas['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + g_horas['Hora_inicio'],
            errors='coerce'
        )
        g_horas['Fin_dt'] = pd.to_datetime(
            g_horas['Fecha'].dt.strftime('%Y-%m-%d') + ' ' + g_horas['Hora_fin'],
            errors='coerce'
        )

        mask = g_horas['Inicio_dt'].notna() & g_horas['Fin_dt'].notna()
        g_valid = g_horas[mask].copy()

        if not g_valid.empty:
            g_valid['Downtime_horas'] = (
                g_valid['Fin_dt'] - g_valid['Inicio_dt']
            ).dt.total_seconds() / 3600.0
            g_valid['Downtime_horas'] = g_valid['Downtime_horas'].clip(lower=0)

            fecha_min = g_horas['Fecha'].min()
            fecha_max = g_horas['Fecha'].max()
            dias_periodo = (fecha_max - fecha_min).days + 1
            horas_totales = dias_periodo * 24

            downtime_total = g_valid['Downtime_horas'].sum()
            if horas_totales > 0:
                disponibilidad = round(
                    (horas_totales - downtime_total) / horas_totales * 100, 2
                )

    historial = df.sort_values(by='Fecha').to_dict(orient='records')

    df_fechas = df.copy()
    df_fechas = df_fechas.dropna(subset=['Fecha'])
    conteo = df_fechas['Fecha'].dt.date.value_counts().sort_index()
    labels = [d.strftime("%d-%m-%Y") for d in conteo.index]
    values = conteo.values.tolist()

    return render_template(
        'maquina_detalle.html',
        title=f"Detalle máquina: {maquina}",
        maquina=maquina,
        fallas=fallas,
        fecha_primera=fecha_primera.date().isoformat() if pd.notna(fecha_primera) else None,
        fecha_ultima=fecha_ultima.date().isoformat() if pd.notna(fecha_ultima) else None,
        mtbf=mtbf,
        mttr=mttr,
        disponibilidad=disponibilidad,
        historial=historial,
        labels=labels,
        values=values
    )


# ===================== PREVENTIVOS =====================

@app.route('/preventivos')
def preventivos():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if 'Próximo_mantenimiento' not in df.columns or 'Tipo' not in df.columns:
        flash("No hay información de preventivos.", "warning")
        return redirect(url_for('home'))

    hoy = datetime.now().date()
    preventivos_list = []

    for i, row in df.iterrows():
        if row['Tipo'] == 'Preventivo' and pd.notna(row['Próximo_mantenimiento']):
            prox = datetime.strptime(str(row['Próximo_mantenimiento']), "%Y-%m-%d").date()
            dias = (prox - hoy).days

            preventivos_list.append({
                "indice": i,
                "Máquina": row['Máquina'],
                "Fecha": row['Fecha'],
                "Próximo_mantenimiento": row['Próximo_mantenimiento'],
                "dias": dias,
                "Estado_prev": (
                    "Vencido" if dias < 0 else
                    "Hoy" if dias == 0 else
                    "Próximo" if dias <= 7 else
                    "OK"
                )
            })

    total = len(preventivos_list)
    vencidos = len([p for p in preventivos_list if p["Estado_prev"] == "Vencido"])
    proximos = len([p for p in preventivos_list if p["Estado_prev"] == "Próximo"])
    ok = len([p for p in preventivos_list if p["Estado_prev"] == "OK"])

    return render_template(
        'preventivos.html',
        preventivos=preventivos_list,
        total=total,
        vencidos=vencidos,
        proximos=proximos,
        ok=ok,
        rol=session.get("rol")
    )
@app.route('/calendario')
def calendario():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    return render_template(
        "calendario.html",
        title="Calendario de mantenciones",
        usuario=session.get("usuario"),
        rol=session.get("rol")
    )
@app.route('/api/calendario')
def api_calendario():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    try:
        df = pd.read_csv("mantenciones.csv")
    except FileNotFoundError:
        return jsonify([])

    if 'Fecha' not in df.columns:
        return jsonify([])

    # Normalizar fecha
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])

    eventos = []

    for _, row in df.iterrows():
        maquina = str(row.get('Máquina', 'Sin máquina'))
        tipo = str(row.get('Tipo', ''))
        fecha = row['Fecha'].date().isoformat()

        # Texto que se ve en el día
        titulo = f"{maquina} ({tipo})" if tipo else maquina

        # Colores por tipo (puedes cambiarlos)
        color = '#28a745'  # verde por defecto
        if tipo == 'Correctivo':
            color = '#dc3545'  # rojo
        elif tipo == 'Preventivo':
            color = '#007bff'  # azul

        eventos.append({
            "title": titulo,
            "start": fecha,
            "color": color
        })

    return jsonify(eventos)


@app.route("/preventivos/marcar/<int:indice>", methods=["POST"])
def marcar_realizado(indice):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    df = cargar_mantenciones()

    if indice < 0 or indice >= len(df):
        flash("No se encontró el registro de preventivo.", "danger")
        return redirect(url_for("preventivos"))

    fila = df.loc[indice]

    hoy_date = datetime.now().date()
    hoy_str = hoy_date.strftime("%Y-%m-%d")

    freq = 0
    if "Frecuencia_dias" in df.columns and not pd.isna(fila.get("Frecuencia_dias")):
        try:
            freq = int(fila["Frecuencia_dias"])
        except ValueError:
            freq = 0

    proximo_date = hoy_date + timedelta(days=freq)
    proximo_str = proximo_date.strftime("%Y-%m-%d")

    df.at[indice, "Fecha"] = hoy_str
    df.at[indice, "Próximo_mantenimiento"] = proximo_str

    df.to_csv("mantenciones.csv", index=False)

    flash("Preventivo marcado como realizado correctamente.", "success")
    return redirect(url_for("preventivos"))


# ===================== ADMIN USUARIOS =====================

@app.route('/usuarios')
def usuarios():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if session.get("rol") != "admin":
        flash("Solo el administrador puede administrar usuarios.", "warning")
        return redirect(url_for("home"))

    lista = cargar_usuarios()
    return render_template(
        'usuarios.html',
        title="Administración de usuarios",
        usuarios=lista,
        usuario=session.get("usuario"),
        rol=session.get("rol")
    )


@app.route('/usuarios/nuevo', methods=['POST'])
def crear_usuario():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    if session.get("rol") != "admin":
        flash("No tienes permisos para crear usuarios.", "danger")
        return redirect(url_for("usuarios"))

    nombre_raw = request.form.get('usuario', '').strip()
    contrasena = request.form.get('contrasena', '').strip()
    rol_raw = request.form.get('rol', '').strip()

    nombre = normalizar_usuario(nombre_raw)
    rol = normalizar_rol(rol_raw)

    if not nombre or not contrasena or not rol:
        flash("Todos los campos son obligatorios.", "warning")
        return redirect(url_for("usuarios"))

    usuarios_lista = cargar_usuarios()

    # Evitar duplicados por may/min
    if any(u['usuario'] == nombre for u in usuarios_lista):
        flash("Ya existe un usuario con ese nombre (independiente de mayúsculas o minúsculas).", "danger")
        return redirect(url_for("usuarios"))

    usuarios_lista.append({
        'usuario': nombre,
        'contrasena': contrasena,
        'rol': rol
    })

    guardar_usuarios(usuarios_lista)
    flash("Usuario creado correctamente.", "success")
    return redirect(url_for("usuarios"))


@app.route('/usuarios/eliminar/<usuario_nombre>', methods=['POST'])
def eliminar_usuario(usuario_nombre):
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    if session.get("rol") != "admin":
        flash("No tienes permisos para eliminar usuarios.", "danger")
        return redirect(url_for("usuarios"))

    usuario_nombre_norm = normalizar_usuario(usuario_nombre)

    usuarios_lista = cargar_usuarios()

    # Nunca dejar borrar al admin principal (case-insensitive)
    if usuario_nombre_norm == "admin":
        flash("No puedes eliminar al usuario admin.", "warning")
        return redirect(url_for("usuarios"))

    nuevos_usuarios = [u for u in usuarios_lista if u['usuario'] != usuario_nombre_norm]

    if len(nuevos_usuarios) == len(usuarios_lista):
        flash("Usuario no encontrado.", "warning")
    else:
        guardar_usuarios(nuevos_usuarios)
        flash("Usuario eliminado correctamente.", "success")

    return redirect(url_for("usuarios"))


# ===================== UTILIDAD =====================

def requiere_admin():
    if not session.get("logged_in"):
        return False
    return session.get("rol") == "admin"


if __name__ == '__main__':
    app.run(debug=True)
