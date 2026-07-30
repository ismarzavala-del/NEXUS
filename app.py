import io
import streamlit as st
import pandas as pd
from datetime import date, datetime, timedelta
from streamlit_js_eval import get_geolocation

from database import (
    init_db, get_session, Student, Subject, Attendance,
    CaseTracker, Schedule, Justification, User, calculate_distance
)
from analytics import evaluate_student_risk, get_institutional_semaphore
from ai_recommender import generate_recommendation, generate_pedagogical_act

# Coordenadas de prueba del Instituto y radio máximo permitido (en metros)
INSTITUTE_LAT = 13.35054
INSTITUTE_LON = -88.34890
MAX_DISTANCE_METERS = 200.0

# Módulo de Horarios integrado localmente para la nube
FASTAPI_HORARIOS_URL = "modo_integrado"

st.set_page_config(page_title="NEXUS", page_icon="🎓", layout="wide")

try:
    init_db()
    session = get_session()
except Exception as e:
    st.error(f"Error en la base de datos: {e}")
    st.stop()

# Manejo de Sesión / Estado
if 'user' not in st.session_state:
    st.session_state['user'] = None

if 'admin_view' not in st.session_state:
    st.session_state['admin_view'] = 'dashboard'  # Opciones: 'dashboard', 'horarios', 'asistencia'

# -----------------------------------------------------------------------------
# AUTENTICACIÓN / LOGIN
# -----------------------------------------------------------------------------
if st.session_state['user'] is None:
    st.title("🎓 NEXUS ACCESO A SISTEMA")
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
        st.subheader("Iniciar Sesión")
        username_input = st.text_input("Usuario", key="login_user_input")
        password_input = st.text_input("Contraseña", type="password", key="login_pass_input")

        if st.button("Ingresar", type="primary", width='stretch', key="login_submit_btn"):
            user_found = session.query(User).filter_by(username=username_input, password=password_input).first()
            if user_found:
                st.session_state['user'] = user_found
                st.session_state['admin_view'] = 'dashboard'
                st.success(f"Bienvenido {user_found.username} ({user_found.role})")
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos")

        st.caption("🔒 **Credenciales de Prueba:**")
        st.caption(
            "- **Admin:** `admin` / `123` | **Docente Orientador:** `profe` / `123` | **Alumno:** `juan` / `123`")
    st.stop()

current_user = st.session_state['user']

# Encabezado
col_h1, col_h2 = st.columns([4, 1])
with col_h1:
    st.title("🎓 NEXUS SISTEMA INTEGRAL")
    st.caption(f"Usuario: **{current_user.username}** | Rol: **{current_user.role}**" + (
        f" | Sección Asignada (Orientador): **{current_user.assigned_section}**" if current_user.assigned_section else ""))
with col_h2:
    if st.button("🚪 Cerrar Sesión", key="btn_logout_header"):
        st.session_state['user'] = None
        st.session_state['admin_view'] = 'dashboard'
        st.rerun()

st.divider()

# -----------------------------------------------------------------------------
# 1. PERFIL ALUMNO (GPS)
# -----------------------------------------------------------------------------
if current_user.role == "Alumno":
    st.subheader("📍 Marcaje de Asistencia por Bloque (Geolocalizado)")
    student_data = session.query(Student).filter_by(id=current_user.student_id).first()
    if not student_data:
        st.error("Registro de alumno no encontrado.")
        st.stop()

    st.info(f"Estudiante: **{student_data.name}** | Sección: **{student_data.section}**")

    now = datetime.now()
    dias_ingles = {'Monday': 'Lunes', 'Tuesday': 'Martes', 'Wednesday': 'Miércoles', 'Thursday': 'Jueves',
                   'Friday': 'Viernes', 'Saturday': 'Sábado', 'Sunday': 'Domingo'}
    current_day_eng = now.strftime('%A')
    current_day_esp = dias_ingles.get(current_day_eng, current_day_eng)
    current_time = now.time()

    st.write(f"🕒 Hora actual del sistema: **{now.strftime('%H:%M:%S')}** ({current_day_esp})")

    active_sch_found = None
    import sqlite3

    try:
        conn_h = sqlite3.connect("modulo_horarios/database.db")
        cursor_h = conn_h.cursor()

        cursor_h.execute("SELECT id FROM seccion WHERE nombre = ?", (student_data.section,))
        sec_res = cursor_h.fetchone()

        if sec_res:
            sec_id_val = sec_res[0]
            cursor_h.execute("""
                             SELECT hora_texto, materia_id, id
                             FROM horario
                             WHERE seccion_id = ?
                               AND (dia = ? OR dia = ?)
                             """, (sec_id_val, current_day_esp, current_day_eng))

            bloques = cursor_h.fetchall()

            for b in bloques:
                hora_texto_val, sub_id_h, bloque_id_val = b[0], b[1], b[2]

                h_ini_str, h_fin_str = "07:00", "12:00"
                if "-" in str(hora_texto_val):
                    partes = hora_texto_val.split("-")
                    h_ini_str = partes[0].strip()
                    h_fin_str = partes[1].strip()


                def parse_custom_time(t_str):
                    t_str = t_str.strip().lower()
                    for fmt in ("%I:%M %p", "%H:%M", "%H:%M:%S", "%I:%M%p"):
                        try:
                            return datetime.strptime(t_str, fmt).time()
                        except ValueError:
                            continue
                    return None


                t_inicio = parse_custom_time(h_ini_str)
                t_fin = parse_custom_time(h_fin_str)

                if t_inicio and t_fin:
                    # Convertir t_inicio a datetime de hoy para sumarle exactamente 10 minutos de tolerancia
                    dt_hoy = datetime.today().date()
                    dt_inicio_obj = datetime.combine(dt_hoy, t_inicio)
                    dt_limite_obj = dt_inicio_obj + timedelta(minutes=10)
                    t_limite = dt_limite_obj.time()

                    # La clase está activa SOLO si estamos entre el inicio y los 10 minutos posteriores
                    if t_inicio <= current_time <= t_limite:
                        active_sch_found = (h_ini_str, h_fin_str, sub_id_h)
                        break

        conn_h.close()

    except Exception as ex:
        active_sch_local = session.query(Schedule).filter(
            Schedule.section == student_data.section,
            Schedule.day_of_week == current_day_eng,
            Schedule.start_time <= current_time,
            Schedule.end_time >= current_time
        ).first()
        if active_sch_local:
            active_sch_found = (str(active_sch_local.start_time), str(active_sch_local.end_time),
                                active_sch_local.subject_id)

    if not active_sch_found:
        st.warning(
            "⚠️ **No tienes clases activas en este momento.** (Estás en horario libre, receso o fuera de clase).")
    else:
        h_ini, h_fin, sub_id_val = active_sch_found

        sub_name = "Asignatura"
        try:
            conn_h = sqlite3.connect("modulo_horarios/database.db")
            cursor_h = conn_h.cursor()
            cursor_h.execute("SELECT nombre FROM materia WHERE id = ?", (sub_id_val,))
            res_sub = cursor_h.fetchone()
            if res_sub:
                sub_name = res_sub[0]
            conn_h.close()
        except:
            sub_obj = session.query(Subject).filter_by(id=sub_id_val).first()
            if sub_obj:
                sub_name = sub_obj.name

        st.success(f"📖 **Clase Activa en este Bloque:** {sub_name} ({h_ini} - {h_fin})")

        # -----------------------------------------------------------------
        # VALIDACIÓN Y REGISTRO AUTOMÁTICO DE GPS (SIN BOTÓN)
        # -----------------------------------------------------------------
        loc = get_geolocation()
        if loc and 'coords' in loc:
            user_lat, user_lon = loc['coords']['latitude'], loc['coords']['longitude']
            distance = calculate_distance(user_lat, user_lon, INSTITUTE_LAT, INSTITUTE_LON)

            if distance <= MAX_DISTANCE_METERS:
                sub_real_id = sub_id_val if isinstance(sub_id_val, int) else 1

                existing_att = session.query(Attendance).filter_by(
                    student_id=student_data.id,
                    subject_id=sub_real_id,
                    date=date.today()
                ).first()

                if existing_att and existing_att.status == "Presente":
                    st.success(
                        f"✅ Ya se registró tu asistencia previamente para la clase de **{sub_name}** en este bloque.")
                else:
                    if not existing_att:
                        session.add(Attendance(
                            student_id=student_data.id,
                            subject_id=sub_real_id,
                            date=date.today(),
                            status="Presente",
                            observation=f"GPS Bloque {h_ini}-{h_fin}"
                        ))
                    else:
                        existing_att.status = "Presente"
                        existing_att.observation = f"GPS Bloque {h_ini}-{h_fin}"

                        # --- VALIDACIÓN ANTI-FRAUDE POR DISPOSITIVO ---
                        # Creamos una clave única basada en el dispositivo/navegador para este bloque, fecha y materia
                        clave_dispositivo_key = f"dispositivo_marco_{sub_real_id}_{date.today()}"

                        # Verificamos si en esta sesión/navegador ya se registró asistencia para este bloque
                        if st.session_state.get(clave_dispositivo_key, False):
                            st.error(
                                "❌ Este dispositivo ya registró una asistencia en este bloque. No se permite registrar a múltiples alumnos desde el mismo equipo.")
                        else:
                            session.commit()
                            # Marcamos la sesión actual del navegador como "ocupada" para este bloque
                            st.session_state[clave_dispositivo_key] = True

                            st.balloons()
                            st.success(
                                f"🚀 ¡Ubicación validada! Asistencia registrada automáticamente para: **{sub_name}**")
                            st.rerun()

            else:
                st.error(
                    f"❌ Debes estar dentro del instituto para registrar tu asistencia. (Te encuentras a {distance:.2f} metros)")
        else:
            st.warning("Por favor activa y concede el acceso a tu ubicación GPS en el navegador.")
    st.stop()

# -----------------------------------------------------------------------------
# 2. PERFIL ADMINISTRADOR: PANTALLA PRINCIPAL CON SELECCIÓN DE MÓDULOS
# -----------------------------------------------------------------------------
if current_user.role == "Admin":
    if st.session_state['admin_view'] == 'dashboard':
        st.subheader("🏛️ Panel Central de Administración NEXUS")
        st.write("Selecciona el módulo al que deseas ingresar:")
        st.write("")

        col_m1, col_m2 = st.columns(2)

        with col_m1:
            with st.container(border=True):
                st.markdown("### 📅 MÓDULO DE HORARIOS")
                st.caption(
                    "Gestión y programación de horarios institucionales, asignación de materias y carga académica.")
                st.write("")
                if st.button("👉 Ingresar a Módulo de Horarios", type="primary", width='stretch',
                             key="btn_goto_horarios_dash"):
                    st.session_state['admin_view'] = 'horarios'
                    st.rerun()

        with col_m2:
            with st.container(border=True):
                st.markdown("### 📊 MÓDULO DE ASISTENCIA")
                st.caption(
                    "Control inteligente de asistencia, geolocalización, seguimiento de casos y semáforo de permanencia.")
                st.write("")
                if st.button("👉 Ingresar a Módulo de Asistencia", type="primary", width='stretch',
                             key="btn_goto_asistencia_dash"):
                    st.session_state['admin_view'] = 'asistencia'
                    st.rerun()

        st.stop()



    elif st.session_state['admin_view'] == 'horarios':

        if st.button("⬅️ Volver al Panel Principal NEXUS", key="btn_back_from_horarios_view"):
            st.session_state['admin_view'] = 'dashboard'

            st.rerun()

        st.divider()

        st.subheader("📅 Módulo Avanzado de Horarios y Carga Académica")

        st.info(
            "Sistema integrado nativamente: gestión de secciones, docentes, cargas, generación algorítmica y control total.")

        import sqlite3

        import random

        try:
            conn_h = sqlite3.connect("modulo_horarios/database.db")
            cursor_h = conn_h.cursor()

            # --- PARCHE QUIRÚRGICO: Asegurar columnas de días en la tabla docente ---
            try:
                cursor_h.execute("ALTER TABLE docente ADD COLUMN dias_matutino TEXT;")
                conn_h.commit()
            except Exception:
                pass

            try:
                cursor_h.execute("ALTER TABLE docente ADD COLUMN dias_vespertino TEXT;")
                conn_h.commit()
            except Exception:
                pass
            # ------------------------------------------------------------------------

            # Reducido a 4 pestañas limpias y funcionales
            tab_h1, tab_h2, tab_h3, tab_h4 = st.tabs([

                "🏫 Secciones", "📚 Materias", "👨‍🏫 Docentes y Cargas", "👁️ Visualizar y Generar Horarios"

            ])

            # -----------------------------------------------------------------
            # 1. GESTIÓN DE SECCIONES
            # -----------------------------------------------------------------
            with tab_h1:
                st.markdown("#### Gestión y Creación de Secciones")

                MAPA_ABREVIACIONES = {
                    "Bachillerato Academico": "BTO-A",
                    "Bachillerato técnico productivo en sistemas eléctricos y energías renovables": "BTP-SEER",
                    "Bachillerato Tecnico vocacional Sistemas Electricos": "BTV-SE",
                    "Bachillerato técnico vocacional en desarrollo de software": "BTV-DS",
                    "bachillerato técnico vocacional administrativo contable": "BTV-AC",
                    "Bachillerato técnico vocacional en diseño grafico": "BTV-DG",
                    "Bachillerato técnico productivo en salud y bienestar social": "BTP-SBS",
                    "Bachillerato Tecnico vocacional Atención Primaria en salud": "BTV-APS"
                }

                with st.form("form_crear_seccion_nativo"):
                    modalidad_sel = st.selectbox("Modalidad", list(MAPA_ABREVIACIONES.keys()))
                    anio_sel = st.selectbox("Año", ["1° año", "2° año", "3° año"])
                    grupo_sel = st.selectbox("Grupo", ["A", "A1", "A2", "B", "C", "D", "E"])
                    btn_sec_sub = st.form_submit_button("➕ Crear Sección Oficial", width='stretch')

                    if btn_sec_sub:
                        import re

                        num_anio = re.search(r'\d+', str(anio_sel))
                        num_anio_str = num_anio.group() if num_anio else ""
                        prefijo = MAPA_ABREVIACIONES.get(modalidad_sel, "SEC")
                        nombre_generado = f"{prefijo}-{num_anio_str}{grupo_sel}"
                        formato_anio = f"{anio_sel} '{grupo_sel}'"

                        try:
                            cursor_h.execute(
                                "INSERT INTO seccion (nombre, modalidad, anio) VALUES (?, ?, ?)",
                                (nombre_generado, modalidad_sel, formato_anio)
                            )
                            conn_h.commit()
                            st.success(f"¡Sección '{nombre_generado}' creada con éxito!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al crear la sección: {e}")

                st.divider()
                st.markdown("#### 🗑️ Eliminar Sección")

                cursor_h.execute("SELECT id, nombre FROM seccion ORDER BY nombre")
                secs_existentes = cursor_h.fetchall()

                if secs_existentes:
                    mapa_secs_del = {s[1]: s[0] for s in secs_existentes}
                    sec_a_borrar = st.selectbox("Selecciona la sección a eliminar", list(mapa_secs_del.keys()),
                                                key="select_del_sec")

                    if st.button("❌ Eliminar Sección Seleccionada", type="primary", width='stretch'):
                        sec_id_del = mapa_secs_del[sec_a_borrar]
                        try:
                            # Opcional: Eliminar primero los registros asociados en horario y carga si es necesario para evitar errores de integridad
                            cursor_h.execute("DELETE FROM horario WHERE seccion_id = ?", (sec_id_del,))
                            cursor_h.execute("DELETE FROM cargaacademica WHERE seccion_id = ?", (sec_id_del,))
                            cursor_h.execute("DELETE FROM seccion WHERE id = ?", (sec_id_del,))
                            conn_h.commit()
                            st.success(f"¡Sección '{sec_a_borrar}' eliminada correctamente!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al eliminar la sección: {e}")
                else:
                    st.info("No hay secciones disponibles para eliminar.")

                st.divider()
                df_secciones = pd.read_sql("SELECT * FROM seccion", conn_h)
                if not df_secciones.empty:
                    st.dataframe(df_secciones, width='stretch')
                else:
                    st.warning("No hay secciones registradas.")

                # -----------------------------------------------------------------
                # 2. GESTIÓN DE MATERIAS
                # -----------------------------------------------------------------
            with tab_h2:
                        st.markdown("#### Gestión de Materias")
                        with st.form("form_crear_materia_nativo"):
                            mat_nombre = st.text_input("Nombre de la Materia")
                            mat_tipo = st.selectbox("Tipo de Materia", ["Básica", "Modular"])
                            btn_mat_sub = st.form_submit_button("➕ Registrar Materia", width='stretch')

                            if btn_mat_sub:
                                if mat_nombre.strip():
                                    try:
                                        cursor_h.execute("INSERT INTO materia (nombre, tipo) VALUES (?, ?)",
                                                         (mat_nombre.strip(), mat_tipo))
                                        conn_h.commit()
                                        st.success(f"¡Materia '{mat_nombre.strip()}' guardada con éxito!")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Error: {e}")
                                else:
                                    st.warning("Ingresa un nombre válido.")

                        st.divider()
                        st.markdown("#### 🗑️ Eliminar Materia")

                        cursor_h.execute("SELECT id, nombre FROM materia")
                        mats_existentes = cursor_h.fetchall()

                        if mats_existentes:
                            mapa_mats_del = {m[1]: m[0] for m in mats_existentes}
                            mat_a_borrar = st.selectbox("Selecciona la materia a eliminar", list(mapa_mats_del.keys()),
                                                        key="select_del_mat")

                            if st.button("❌ Eliminar Materia Seleccionada", type="primary", width='stretch'):
                                mat_id_del = mapa_mats_del[mat_a_borrar]
                                try:
                                    cursor_h.execute("DELETE FROM horario WHERE materia_id = ?", (mat_id_del,))
                                    cursor_h.execute("DELETE FROM cargaacademica WHERE materia_id = ?", (mat_id_del,))
                                    cursor_h.execute("DELETE FROM materia WHERE id = ?", (mat_id_del,))
                                    conn_h.commit()
                                    st.success(f"¡Materia '{mat_a_borrar}' eliminada correctamente!")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Error al eliminar la materia: {e}")
                        else:
                            st.info("No hay materias disponibles para eliminar.")

                        st.divider()
                        df_mat = pd.read_sql("SELECT * FROM materia", conn_h)
                        if not df_mat.empty:
                            st.dataframe(df_mat, width='stretch')
                        else:
                            st.warning("No hay materias registradas.")
                # -----------------------------------------------------------------
                # 3. GESTIÓN DE DOCENTES Y CARGAS ACADÉMICAS
                # -----------------------------------------------------------------
            with tab_h3:
                                st.markdown("#### Registro de Docentes y Asignación de Carga")

                                cursor_h.execute("SELECT id, nombre FROM seccion ORDER BY nombre")
                                secs_db = cursor_h.fetchall()
                                mapa_secs_id = {s[1]: s[0] for s in secs_db}

                                cursor_h.execute("SELECT id, nombre FROM materia")
                                mats_db = cursor_h.fetchall()
                                mapa_mats_id = {m[1]: m[0] for m in mats_db}

                                if 'num_cargas_dinamicas' not in st.session_state:
                                    st.session_state['num_cargas_dinamicas'] = 1

                                doc_nombre = st.text_input("Nombre del Docente", key="doc_nombre_input")
                                doc_correo = st.text_input("Correo Institucional", value="docente@instituto.edu",
                                                           key="doc_correo_input")

                                doc_turno = st.selectbox(
                                    "Turno Preferente",
                                    ["Matutino", "Vespertino", "Doble Turno", "Horario Accesible"],
                                    key="doc_turno_select"
                                )

                                # Variables para capturar los días por turno
                                dias_matutino_str = ""
                                dias_vespertino_str = ""

                                if doc_turno == "Horario Accesible":
                                    st.markdown("##### 🗓️ Configurar Días por Turno")
                                    dias_semana_opciones = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]

                                    col_d1, col_d2 = st.columns(2)
                                    with col_d1:
                                        dias_mat = st.multiselect("🌅 Días en Turno Matutino", dias_semana_opciones,
                                                                  key="form_dias_mat")
                                    with col_d2:
                                        dias_vesp = st.multiselect("🌇 Días en Turno Vespertino", dias_semana_opciones,
                                                                   key="form_dias_vesp")

                                    dias_matutino_str = ", ".join(dias_mat) if dias_mat else ""
                                    dias_vespertino_str = ", ".join(dias_vesp) if dias_vesp else ""

                                st.divider()
                                st.markdown("##### Asignación de Carga (Sección + Materia + Horas Semanales)")

                                cargas_ingresadas = []
                                if secs_db and mats_db:
                                    for i in range(st.session_state['num_cargas_dinamicas']):
                                        col_c1, col_c2, col_c3 = st.columns(3)
                                        with col_c1:
                                            s_sel = st.selectbox(f"Sección #{i + 1}",
                                                                 ["Ninguna"] + list(mapa_secs_id.keys()),
                                                                 key=f"c_sec_{i}")
                                        with col_c2:
                                            m_sel = st.selectbox(f"Materia #{i + 1}",
                                                                 ["Ninguna"] + list(mapa_mats_id.keys()),
                                                                 key=f"c_mat_{i}")
                                        with col_c3:
                                            h_val = st.number_input(f"Medios Bloques #{i + 1}", min_value=0,
                                                                    max_value=20, value=0,
                                                                    key=f"c_hrs_{i}")

                                        if s_sel != "Ninguna" and m_sel != "Ninguna" and h_val > 0:
                                            cargas_ingresadas.append((mapa_secs_id[s_sel], mapa_mats_id[m_sel], h_val))
                                else:
                                    st.info("Primero debes crear Secciones y Materias para asignar cargas.")

                                if secs_db and mats_db:
                                    if st.button("➕ Agregar otra materia/carga", width='stretch',
                                                 key="btn_add_carga_extra"):
                                        st.session_state['num_cargas_dinamicas'] += 1
                                        st.rerun()

                                st.divider()

                                if st.button("🚀 Registrar Docente y Carga Oficial", width='stretch',
                                             key="btn_registrar_docente_final"):
                                    if doc_nombre.strip():
                                        try:
                                            cursor_h.execute(
                                                "INSERT INTO docente (nombre, correo_institucional, turno_preferente, dias_matutino, dias_vespertino) VALUES (?, ?, ?, ?, ?)",
                                                (doc_nombre.strip(), doc_correo.strip(), doc_turno, dias_matutino_str,
                                                 dias_vespertino_str)
                                            )
                                            doc_id_nuevo = cursor_h.lastrowid

                                            for s_id, m_id, hrs in cargas_ingresadas:
                                                cursor_h.execute(
                                                    "INSERT INTO cargaacademica (docente_id, seccion_id, materia_id, horas_semanales) VALUES (?, ?, ?, ?)",
                                                    (doc_id_nuevo, s_id, m_id, hrs)
                                                )
                                            conn_h.commit()
                                            st.session_state['num_cargas_dinamicas'] = 1
                                            st.success(f"¡Docente '{doc_nombre.strip()}' registrado con éxito!")
                                            st.rerun()
                                        except Exception as err:
                                            st.error(f"Error detallado al registrar: {err}")
                                    else:
                                        st.warning("El nombre del docente es obligatorio.")

                                st.divider()
                                st.markdown("##### 📋 Listado y Gestión de Docentes Registrados")

                                cursor_h.execute("""
                                                 SELECT d.id,
                                                        d.nombre,
                                                        d.correo_institucional,
                                                        d.turno_preferente,
                                                        d.dias_matutino,
                                                        d.dias_vespertino,
                                                        s.nombre as seccion,
                                                        m.nombre as materia,
                                                        c.horas_semanales
                                                 FROM docente d
                                                          LEFT JOIN cargaacademica c ON d.id = c.docente_id
                                                          LEFT JOIN seccion s ON c.seccion_id = s.id
                                                          LEFT JOIN materia m ON c.materia_id = m.id
                                                 """)
                                datos_registrados = cursor_h.fetchall()

                                if datos_registrados:
                                    import pandas as pd

                                    df_docentes = pd.DataFrame(datos_registrados, columns=[
                                        "ID", "Nombre", "Correo", "Turno", "Días Mat.", "Días Vesp.", "Sección",
                                        "Materia", "Horas"
                                    ])
                                    st.dataframe(df_docentes, width='stretch')

                                    st.markdown("##### 🗑️ Eliminar Docente")
                                    # Obtener lista única de docentes para eliminar
                                    cursor_h.execute("SELECT id, nombre FROM docente")
                                    docentes_db = cursor_h.fetchall()

                                    if docentes_db:
                                        mapa_docentes_del = {f"{d[1]} (ID: {d[0]})": d[0] for d in docentes_db}
                                        docente_a_borrar_sel = st.selectbox(
                                            "Selecciona el docente que deseas eliminar",
                                            list(mapa_docentes_del.keys()),
                                            key="select_docente_eliminar"
                                        )

                                        if st.button("🗑️ Eliminar Docente Seleccionado", width='stretch',
                                                     key="btn_ejecutar_eliminar_docente"):
                                            doc_id_del = mapa_docentes_del[docente_a_borrar_sel]
                                            try:
                                                # Borrar primero la carga académica asociada por la llave foránea
                                                cursor_h.execute("DELETE FROM cargaacademica WHERE docente_id = ?",
                                                                 (doc_id_del,))
                                                # Borrar al docente
                                                cursor_h.execute("DELETE FROM docente WHERE id = ?", (doc_id_del,))
                                                conn_h.commit()
                                                st.success(f"¡Docente eliminado con éxito junto con su carga asociada!")
                                                st.rerun()
                                            except Exception as err:
                                                st.error(f"Error al eliminar el registro: {err}")
                                else:
                                    st.info("Aún no hay docentes registrados en la base de datos.")
                                # -----------------------------------------------------------------
                                # 4. VISUALIZAR EN CUADRÍCULA Y MOTOR DE GENERACIÓN UNIFICADO
                                # -----------------------------------------------------------------
            with tab_h4:

                st.markdown("#### ⚡ Panel de Generación y Visualización de Horarios")

                # Botón de Generación Integrado Arriba
                if st.button("🚀 Ejecutar Generador Global de Horarios", type="primary",
                             width='stretch',
                             key="btn_ejecutar_gen_global_h4"):

                    # --- CONSULTAR CARGAS ACADÉMICAS + TURNO Y DÍAS DEL DOCENTE ---
                    cursor_h.execute("""
                        SELECT ca.id, ca.docente_id, ca.seccion_id, ca.materia_id, ca.horas_semanales,
                               d.turno_preferente, d.dias_matutino, d.dias_vespertino
                        FROM cargaacademica ca
                        JOIN docente d ON ca.docente_id = d.id
                    """)
                    cargas_raw = cursor_h.fetchall()

                    # --- DIAGNÓSTICO Y VALIDACIÓN ---
                    if not cargas_raw:
                        st.warning(
                            "⚠️ La tabla `cargaacademica` está completamente vacía. Primero debes registrar cargas académicas a los docentes.")
                    else:
                        MEDIOS_BLOQUES = [
                            {"id": 1, "hora": "7:00 am - 7:45 am"},
                            {"id": 2, "hora": "7:45 am - 8:30 am"},
                            {"id": 3, "hora": "8:40 am - 9:25 am"},
                            {"id": 4, "hora": "9:25 am - 10:10 am"},
                            {"id": 5, "hora": "10:20 am - 11:05 am"},
                            {"id": 6, "hora": "11:05 am - 11:50 am"},
                            {"id": 7, "hora": "1:00 pm - 1:45 pm"},
                            {"id": 8, "hora": "1:45 pm - 2:30 pm"},
                            {"id": 9, "hora": "2:40 pm - 3:25 pm"},
                            {"id": 10, "hora": "3:25 pm - 4:10 pm"},
                            {"id": 11, "hora": "4:20 pm - 5:10 pm"},
                            {"id": 12, "hora": "5:10 pm - 5:50 pm"},
                        ]

                        DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
                        # Bloques matutinos (7:00 am - 12:00 pm) y vespertinos (1:00 pm - 5:50 pm)
                        # agrupados en parejas: cada pareja = 2 medios bloques seguidos = 1 bloque completo.
                        PAREJAS_MATUTINO = [(1, 2), (3, 4), (5, 6)]
                        PAREJAS_VESPERTINO = [(7, 8), (9, 10), (11, 12)]

                        import random

                        def slots_permitidos(turno, dias_mat_str, dias_vesp_str):
                            """Devuelve la lista de (dia, (id1, id2)) que un docente puede usar
                            según su turno preferente y, si aplica, sus días personalizados."""
                            dias_mat_str = dias_mat_str or ""
                            dias_vesp_str = dias_vesp_str or ""
                            slots = []
                            if turno == "Matutino":
                                for d in DIAS_SEMANA:
                                    for p in PAREJAS_MATUTINO:
                                        slots.append((d, p))
                            elif turno == "Vespertino":
                                for d in DIAS_SEMANA:
                                    for p in PAREJAS_VESPERTINO:
                                        slots.append((d, p))
                            elif turno == "Doble Turno":
                                for d in DIAS_SEMANA:
                                    for p in PAREJAS_MATUTINO + PAREJAS_VESPERTINO:
                                        slots.append((d, p))
                            elif turno == "Horario Accesible":
                                dias_mat = [x.strip() for x in dias_mat_str.split(",") if x.strip()]
                                dias_vesp = [x.strip() for x in dias_vesp_str.split(",") if x.strip()]
                                for d in dias_mat:
                                    for p in PAREJAS_MATUTINO:
                                        slots.append((d, p))
                                for d in dias_vesp:
                                    for p in PAREJAS_VESPERTINO:
                                        slots.append((d, p))
                            return slots

                        # --- REGENERACIÓN GLOBAL: se limpia el horario anterior antes de crear el nuevo ---
                        cursor_h.execute("DELETE FROM horario")

                        docentes_ocupados = set()
                        secciones_ocupadas = set()
                        cargas_lista = list(cargas_raw)
                        random.shuffle(cargas_lista)

                        intentos_exitosos = 0
                        cargas_incompletas = []

                        for c_id, doc_id, sec_id, mat_id, hrs_sem, turno, dias_mat, dias_vesp in cargas_lista:
                            horas_pendientes = hrs_sem
                            slots = slots_permitidos(turno, dias_mat, dias_vesp)
                            random.shuffle(slots)

                            # 1) Asignar bloques completos: siempre 2 medios bloques seguidos
                            for dia, (id1, id2) in slots:
                                if horas_pendientes < 2:
                                    break

                                b1 = next((b for b in MEDIOS_BLOQUES if b["id"] == id1), None)
                                b2 = next((b for b in MEDIOS_BLOQUES if b["id"] == id2), None)
                                if not b1 or not b2:
                                    continue

                                c_doc1, c_doc2 = (doc_id, dia, id1), (doc_id, dia, id2)
                                c_sec1, c_sec2 = (sec_id, dia, id1), (sec_id, dia, id2)

                                if (
                                        c_doc1 not in docentes_ocupados and c_doc2 not in docentes_ocupados and
                                        c_sec1 not in secciones_ocupadas and c_sec2 not in secciones_ocupadas):

                                    for bloq in [b1, b2]:
                                        cursor_h.execute(
                                            "INSERT INTO horario (seccion_id, docente_id, materia_id, dia, bloque_id, hora_texto) VALUES (?, ?, ?, ?, ?, ?)",
                                            (sec_id, doc_id, mat_id, dia, bloq["id"], bloq["hora"])
                                        )
                                        docentes_ocupados.add((doc_id, dia, bloq["id"]))
                                        secciones_ocupadas.add((sec_id, dia, bloq["id"]))
                                    horas_pendientes -= 2
                                    intentos_exitosos += 1

                            # 2) Si sobra 1 medio bloque suelto (horas impares), asignarlo aparte
                            if horas_pendientes == 1:
                                medios_sueltos = [(dia, i) for dia, par in slots for i in par]
                                random.shuffle(medios_sueltos)
                                for dia, id_suelto in medios_sueltos:
                                    b = next((x for x in MEDIOS_BLOQUES if x["id"] == id_suelto), None)
                                    if not b:
                                        continue
                                    c_doc, c_sec = (doc_id, dia, id_suelto), (sec_id, dia, id_suelto)
                                    if c_doc not in docentes_ocupados and c_sec not in secciones_ocupadas:
                                        cursor_h.execute(
                                            "INSERT INTO horario (seccion_id, docente_id, materia_id, dia, bloque_id, hora_texto) VALUES (?, ?, ?, ?, ?, ?)",
                                            (sec_id, doc_id, mat_id, dia, b["id"], b["hora"])
                                        )
                                        docentes_ocupados.add(c_doc)
                                        secciones_ocupadas.add(c_sec)
                                        horas_pendientes -= 1
                                        intentos_exitosos += 1
                                        break

                            if horas_pendientes > 0:
                                cargas_incompletas.append((doc_id, sec_id, horas_pendientes))

                        conn_h.commit()
                        st.success(
                            f"🎉 ¡Generación finalizada! Se insertaron {intentos_exitosos} bloques horarios en la base de datos.")
                        if cargas_incompletas:
                            st.warning(
                                f"⚠️ {len(cargas_incompletas)} carga(s) académica(s) no se pudieron completar del todo: "
                                f"no había suficientes espacios disponibles dentro del turno/días permitidos de ese docente. "
                                f"Revisa si tiene demasiadas horas asignadas para su turno.")
                        st.rerun()
                st.divider()

                # Visualizador en Cuadrícula debajo del botón
                cursor_h.execute("SELECT id, nombre FROM seccion ORDER BY nombre")
                secs_db = cursor_h.fetchall()

                if secs_db:
                    # Crear diccionario y lista limpios basados estrictamente en la BD
                    mapa_secs = {str(r[1]): r[0] for r in secs_db}
                    secs_nombres = list(mapa_secs.keys())

                    sec_ids_ordenados = [r[0] for r in secs_db]

                    if "sec_vis_seleccionada_id" not in st.session_state or \
                            st.session_state["sec_vis_seleccionada_id"] not in sec_ids_ordenados:
                        st.session_state["sec_vis_seleccionada_id"] = sec_ids_ordenados[0]

                    idx_actual = sec_ids_ordenados.index(st.session_state["sec_vis_seleccionada_id"])

                    sec_elegida = st.selectbox("Seleccionar Sección a Consultar", secs_nombres,
                                               index=idx_actual,
                                               key="select_vis_sec_unificado")
                    sec_id_sel = mapa_secs[sec_elegida]
                    st.session_state["sec_vis_seleccionada_id"] = sec_id_sel

                    MEDIOS_BLOQUES_VIS = [
                        {"id": 1, "hora": "7:00 am - 7:45 am", "es_pausa": False},
                        {"id": 2, "hora": "7:45 am - 8:30 am", "es_pausa": False},
                        {"id": 991, "hora": "8:30 am - 8:40 am", "es_pausa": True, "tipo": "☕ RECESO"},
                        {"id": 3, "hora": "8:40 am - 9:25 am", "es_pausa": False},
                        {"id": 4, "hora": "9:25 am - 10:10 am", "es_pausa": False},
                        {"id": 992, "hora": "10:10 am - 10:20 am", "es_pausa": True, "tipo": "☕ RECESO"},
                        {"id": 5, "hora": "10:20 am - 11:05 am", "es_pausa": False},
                        {"id": 6, "hora": "11:05 am - 11:50 am", "es_pausa": False},
                        {"id": 993, "hora": "11:50 am - 1:00 pm", "es_pausa": True, "tipo": "🍽️ ALMUERZO"},
                        {"id": 7, "hora": "1:00 pm - 1:45 pm", "es_pausa": False},
                        {"id": 8, "hora": "1:45 pm - 2:30 pm", "es_pausa": False},
                        {"id": 994, "hora": "2:30 pm - 2:40 pm", "es_pausa": True, "tipo": "☕ RECESO"},
                        {"id": 9, "hora": "2:40 pm - 3:25 pm", "es_pausa": False},
                        {"id": 10, "hora": "3:25 pm - 4:10 pm", "es_pausa": False},
                        {"id": 995, "hora": "4:10 pm - 4:20 pm", "es_pausa": True, "tipo": "☕ RECESO"},
                        {"id": 11, "hora": "4:20 pm - 5:10 pm", "es_pausa": False},
                        {"id": 12, "hora": "5:10 pm - 5:50 pm", "es_pausa": False},
                    ]

                    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
                    tabla_matriz = []

                    for b in MEDIOS_BLOQUES_VIS:
                        fila = {"Hora / Bloque": b["hora"]}
                        if b["es_pausa"]:
                            for d in dias_semana:
                                fila[d] = b["tipo"]
                        else:
                            for d in dias_semana:
                                cursor_h.execute("""
                                                 SELECT m.nombre, doc.nombre
                                                 FROM horario h
                                                          JOIN materia m ON h.materia_id = m.id
                                                          JOIN docente doc ON h.docente_id = doc.id
                                                 WHERE h.seccion_id = ?
                                                   AND h.dia = ?
                                                   AND h.bloque_id = ?
                                                 """, (sec_id_sel, d, b["id"]))
                                res = cursor_h.fetchone()
                                if res:
                                    fila[d] = f"{res[0]}\n({res[1]})"
                                else:
                                    fila[d] = "-"
                        tabla_matriz.append(fila)

                    df_final_matriz = pd.DataFrame(tabla_matriz)

                    st.markdown(f"##### Vista de Horario en Cuadrícula: **{sec_elegida}**")
                    st.dataframe(df_final_matriz, width='stretch', hide_index=True)
                else:
                    st.info("No hay secciones creadas para visualizar.")
            conn_h.close()

        except Exception as ex:
            st.error(f"Error operando el módulo de horarios: {ex}")

        st.stop()

elif st.session_state['admin_view'] == 'asistencia':
    if st.button("⬅️ Volver al Panel Principal NEXUS", key="btn_back_from_asistencia_view"):
        st.session_state['admin_view'] = 'dashboard'
        st.rerun()
    st.divider()
# -----------------------------------------------------------------------------
# 3. PESTAÑAS DEL MÓDULO DE ASISTENCIA (DOCENTES Y ADMIN)
# -----------------------------------------------------------------------------
available_tabs = []

if current_user.role == "Admin":
    available_tabs.append("🎓 Carga y Gestión de Estudiantes")
    available_tabs.append("👨‍🏫 Carga y Gestión de Docentes")

if current_user.role in ["Docente", "Admin"] and current_user.assigned_section:
    available_tabs.append("📋 Mi Sección (Orientador)")
    available_tabs.append("📝 Gestión de Permisos")

tabs = st.tabs(available_tabs)

# TAB: CARGA Y GESTIÓN DE ESTUDIANTES (EXCEL Y SECCIONES)
if "🎓 Carga y Gestión de Estudiantes" in available_tabs:
    idx = available_tabs.index("🎓 Carga y Gestión de Estudiantes")
    with tabs[idx]:
        st.subheader("🎓 Carga Masiva y Gestión de Estudiantes")

        import sqlite3

        lista_secciones = []
        lista_docentes_horarios = []

        try:
            conn_h = sqlite3.connect("modulo_horarios/database.db")
            cursor_h = conn_h.cursor()

            cursor_h.execute("SELECT nombre FROM seccion")
            resultados_sec = cursor_h.fetchall()
            lista_secciones = [row[0] for row in resultados_sec if row[0]]

            cursor_h.execute("SELECT nombre FROM docente")
            resultados_doc = cursor_h.fetchall()
            lista_docentes_horarios = [row[0] for row in resultados_doc if row[0]]

            conn_h.close()
        except Exception as e:
            st.error(f"Error conectando a la BD de horarios: {e}")

        if not lista_secciones:
            lista_secciones = ["Sin Secciones Creadas en Horarios"]

        if not lista_docentes_horarios:
            lista_docentes_horarios = ["Sin Docentes Creados en Horarios"]

        col_ex1, col_ex2 = st.columns([1, 1])

        with col_ex1:
            with st.container(border=True):
                st.markdown("#### 📥 Importar Nómina desde Excel")

                seccion_destino = st.selectbox("1. Asignar a la Sección:", lista_secciones,
                                               key="sb_excel_section_dest_upload")
                docente_orientador = st.selectbox("2. Asignar Docente Orientador:", lista_docentes_horarios,
                                                  key="sb_excel_docente_orientador_select")
                uploaded_file = st.file_uploader("3. Seleccionar archivo Excel (.xlsx)", type=["xlsx", "xls"],
                                                 key="excel_file_uploader_input")

                if st.button("🚀 Cargar e Importar Alumnos", type="primary", width='stretch',
                             key="btn_import_excel_action"):
                    if uploaded_file is not None and lista_secciones != ["Sin Secciones Creadas en Horarios"]:
                        try:
                            df = pd.read_excel(uploaded_file)
                            df.columns = [str(c).strip().lower() for c in df.columns]

                            col_nie = next((c for c in df.columns if 'nie' in c or 'id' in c or 'codigo' in c), None)
                            col_nom = next(
                                (c for c in df.columns if 'nombre' in c or 'alumno' in c or 'estudiante' in c), None)

                            if col_nie and col_nom:
                                nuevos = 0
                                actualizados = 0
                                for _, r in df.iterrows():
                                    nie_val = str(r[col_nie]).strip()
                                    nom_val = str(r[col_nom]).strip()

                                    if pd.notna(r[col_nie]) and pd.notna(r[col_nom]):
                                        est = session.query(Student).filter_by(id=nie_val).first()
                                        if not est:
                                            session.add(Student(id=nie_val, name=nom_val, section=seccion_destino))

                                            user_existente = session.query(User).filter_by(username=nie_val).first()
                                            if not user_existente:
                                                nuevo_user_alumno = User(
                                                    username=nie_val,
                                                    password="indet2026",
                                                    role="Alumno",
                                                    assigned_section=seccion_destino,
                                                    student_id=nie_val
                                                )
                                                session.add(nuevo_user_alumno)

                                            nuevos += 1
                                        else:
                                            est.section = seccion_destino
                                            user_existente = session.query(User).filter_by(username=nie_val).first()
                                            if user_existente:
                                                user_existente.assigned_section = seccion_destino
                                            actualizados += 1
                                if docente_orientador != "Sin Docentes Creados en Horarios":
                                    doc_user = session.query(User).filter_by(username=docente_orientador).first()
                                    if not doc_user:
                                        nuevo_doc = User(
                                            username=docente_orientador,
                                            password="indet2026",
                                            role="Docente",
                                            assigned_section=seccion_destino
                                        )
                                        session.add(nuevo_doc)
                                    else:
                                        doc_user.assigned_section = seccion_destino

                                session.commit()
                                st.success(
                                    f"✅ Proceso completado: {nuevos} registrados, {actualizados} actualizados en {seccion_destino} (Orientador: {docente_orientador}).")
                                st.rerun()
                            else:
                                st.error("El archivo debe incluir las columnas 'NIE' y 'Nombre'.")
                        except Exception as ex:
                            st.error(f"Error procesando el archivo: {ex}")
                    else:
                        st.warning("Selecciona un archivo Excel y asegúrate de tener secciones creadas.")

            with st.container(border=True):
                st.markdown("#### ✍️ Registro Manual de Estudiante")

                with st.form("form_registro_manual_estudiante"):
                    manual_nie = st.text_input("NIE del Estudiante")
                    manual_nombre = st.text_input("Nombre Completo")
                    manual_seccion = st.selectbox("Sección Asignada", lista_secciones, key="sb_manual_reg_section")
                    manual_orientador = st.selectbox("Docente Orientador Asignado", lista_docentes_horarios,
                                                     key="sb_manual_reg_orientador")

                    submit_manual = st.form_submit_button("➕ Registrar Estudiante Individual", width='stretch')

                    if submit_manual:
                        if manual_nie.strip() and manual_nombre.strip():
                            if lista_secciones == ["Sin Secciones Creadas en Horarios"]:
                                st.error("No hay secciones disponibles para asignar.")
                            else:
                                est_existente = session.query(Student).filter_by(id=manual_nie.strip()).first()
                                if est_existente:
                                    st.error(f"Ya existe un estudiante registrado con el NIE: {manual_nie}")
                                else:
                                    nuevo_est = Student(id=manual_nie.strip(), name=manual_nombre.strip(),
                                                        section=manual_seccion)
                                    session.add(nuevo_est)

                                    user_existente = session.query(User).filter_by(username=manual_nie.strip()).first()
                                    if not user_existente:
                                        nuevo_user_alumno = User(
                                            username=manual_nie.strip(),
                                            password="indet2026",
                                            role="Alumno",
                                            assigned_section=manual_seccion,
                                            student_id=manual_nie.strip()
                                        )
                                        session.add(nuevo_user_alumno)

                                    if manual_orientador != "Sin Docentes Creados en Horarios":
                                        doc_user = session.query(User).filter_by(username=manual_orientador).first()
                                        if not doc_user:
                                            nuevo_doc = User(
                                                username=manual_orientador,
                                                password="indet2026",
                                                role="Docente",
                                                assigned_section=manual_seccion
                                            )
                                            session.add(nuevo_doc)
                                        else:
                                            doc_user.assigned_section = manual_seccion

                                    session.commit()
                                    st.success(f"¡Estudiante {manual_nombre.strip()} registrado con éxito!")
                                    st.rerun()
                        else:
                            st.warning("Por favor completa el NIE y el Nombre Completo del estudiante.")

        with col_ex2:
            with st.container(border=True):
                st.markdown("#### 🔍 Consultar y Filtrar Alumnos")
                sec_filtro = st.selectbox("Filtrar por sección:", ["Todas"] + lista_secciones,
                                          key="sb_filter_students_sec")

                q_st = session.query(Student)
                if sec_filtro != "Todas":
                    q_st = q_st.filter(Student.section == sec_filtro)
                estudiantes_lista = q_st.all()

                st.write(f"Total registrados: **{len(estudiantes_lista)}**")

                # --- BÚSQUEDA Y MUESTRA DEL DOCENTE ORIENTADOR ---
                docente_orientador_asignado = "No asignado"
                if sec_filtro != "Todas":
                    doc_encontrado = session.query(User).filter_by(assigned_section=sec_filtro, role="Docente").first()
                    if doc_encontrado:
                        docente_orientador_asignado = doc_encontrado.username
                else:
                    docente_orientador_asignado = "Seleccione una sección específica"

                st.markdown(f"👨‍🏫 Docente Orientador: **{docente_orientador_asignado}**")
                # ------------------------------------------------

                if estudiantes_lista:

                    datos_tabla = []
                    for e in estudiantes_lista:
                        user_info = session.query(User).filter_by(username=str(e.id)).first()
                        datos_tabla.append({
                            "NIE": e.id,
                            "Nombre": e.name,
                            "Sección": e.section,
                            "Usuario": user_info.username if user_info else "N/A",
                            "Contraseña": user_info.password if user_info else "N/A"
                        })

                    df_display = pd.DataFrame(datos_tabla)
                    st.dataframe(df_display, width='stretch', height=250)

                st.divider()

                if st.button("🗑️ Borrar Todos los Estudiantes", type="secondary", key="btn_clear_all_students_confirm",
                             width='stretch'):
                    try:
                        conn_db = sqlite3.connect("student_monitor.db")
                        cursor_db = conn_db.cursor()

                        cursor_db.execute("DELETE FROM students;")
                        conn_db.commit()
                        conn_db.close()

                        session.commit()

                        st.success("¡Todos los estudiantes han sido borrados con éxito!")
                        st.rerun()
                    except Exception as err:
                        st.error(f"Error al borrar los estudiantes: {err}")

                st.divider()
                st.markdown("#### 🔐 Gestión de Credenciales de Alumnos")

                with st.container(border=True):
                    st.markdown("##### 👤 Cambio Individual de Contraseña")
                    todos_alumnos_cred = session.query(Student).all()

                    if todos_alumnos_cred:
                        mapa_alumnos = {f"{e.name} (NIE: {e.id})": e.id for e in todos_alumnos_cred}
                        alumno_seleccionado = st.selectbox("Seleccionar Alumno", list(mapa_alumnos.keys()),
                                                           key="sb_cambio_pass_alumno")
                        nie_elegido = mapa_alumnos[alumno_seleccionado]

                        nueva_pass_ind = st.text_input("Nueva Contraseña", type="password",
                                                       key="txt_nueva_pass_individual")

                        if st.button("💾 Actualizar Contraseña Alumno", width='stretch',
                                     key="btn_update_single_pass"):
                            if nueva_pass_ind.strip():
                                usr_al = session.query(User).filter_by(username=str(nie_elegido)).first()
                                if usr_al:
                                    usr_al.password = nueva_pass_ind.strip()
                                    session.commit()
                                    st.success(f"¡Contraseña actualizada para el alumno con NIE {nie_elegido}!")
                                    st.rerun()
                                else:
                                    st.warning("No se encontró un usuario asociado a este NIE.")
                            else:
                                st.warning("Escribe una contraseña válida.")
                    else:
                        st.info("No hay alumnos registrados.")

                    st.divider()

                    st.markdown("##### 🔄 Restablecimiento Masivo")
                    st.write("Asigna una nueva contraseña a **todos** los alumnos registrados de forma simultánea.")

                    nueva_pass_masiva = st.text_input("Contraseña para el Cambio Masivo", type="password",
                                                      key="txt_nueva_pass_masiva")

                    if st.button("⚠️ Aplicar Contraseña a Todos", type="secondary", width='stretch',
                                 key="btn_reset_all_passwords"):
                        if nueva_pass_masiva.strip():
                            try:
                                alumnos_users = session.query(User).filter_by(role="Alumno").all()
                                contador_resets = 0
                                for u in alumnos_users:
                                    u.password = nueva_pass_masiva.strip()
                                    contador_resets += 1
                                session.commit()
                                st.success(
                                    f"¡Se ha actualizado la contraseña para {contador_resets} credenciales de alumnos con éxito!")
                                st.rerun()
                            except Exception as ex:
                                session.rollback()
                                st.error(f"Error al actualizar las contraseñas: {ex}")
                        else:
                            st.warning("Por favor ingresa una contraseña válida para el cambio masivo.")

# -------------------------------------------------------------
# TAB: MI SECCIÓN (ORIENTADOR)
# -------------------------------------------------------------
if "📋 Mi Sección (Orientador)" in available_tabs:
    idx = available_tabs.index("📋 Mi Sección (Orientador)")
    with tabs[idx]:
        st.subheader(f"📋 Panel del Docente Orientador — Sección: {current_user.assigned_section}")

        my_students = session.query(Student).filter_by(section=current_user.assigned_section).all()
        st.write(f"Total de Estudiantes Tutorados: **{len(my_students)}**")

        if not my_students:
            st.info("No hay estudiantes registrados en tu sección asignada.")
        else:
            # Selector de fecha para consultar y modificar la asistencia diaria
            fecha_consulta = st.date_input("Seleccionar Fecha a Consultar", value=date.today(),
                                           key="date_asistencia_orientador_diaria")

            st.markdown("---")
            st.markdown(f"### 📊 Control y Modificación de Asistencia del Día: {fecha_consulta.strftime('%d/%m/%Y')}")
            st.info(
                "💡 Como docente orientador, puedes modificar manualmente el estado de asistencia de cada alumno si se presenta un retraso o justificación.")

            opciones_estado = ["Presente", "Tardanza", "Ausente"]

            # Listado interactivo por estudiante para modificar el estado
            for est in my_students:
                # Buscamos si ya tiene un registro para esta fecha exacta
                reg_existente = session.query(Attendance).filter_by(student_id=est.id, date=fecha_consulta).first()

                estado_actual = reg_existente.status if reg_existente else "Ausente"
                if estado_actual not in opciones_estado:
                    estado_actual = "Ausente"

                idx_default = opciones_estado.index(estado_actual)

                with st.container(border=True):
                    col_e1, col_e2, col_e3 = st.columns([1, 3, 2])
                    with col_e1:
                        st.markdown(f"**NIE:** `{est.id}`")
                    with col_e2:
                        st.markdown(f"**Estudiante:** {est.name}")
                    with col_e3:
                        nuevo_estado = st.selectbox(
                            "Estado",
                            options=opciones_estado,
                            index=idx_default,
                            key=f"sel_mod_estado_{est.id}_{fecha_consulta}",
                            label_visibility="collapsed"
                        )

                        # Si el docente cambia el selector, se actualiza automáticamente en la BD
                        if nuevo_estado != estado_actual:
                            if reg_existente:
                                reg_existente.status = nuevo_estado
                            else:
                                nuevo_reg = Attendance(
                                    student_id=est.id,
                                    date=fecha_consulta,
                                    status=nuevo_estado
                                )
                                session.add(nuevo_reg)
                            session.commit()
                            st.success(f"¡Actualizado a {nuevo_estado}!")
                            st.rerun()

            st.markdown("---")
            st.markdown("### 🔍 Análisis de Patrones de Comportamiento e Individual")

            # Selector de estudiante para ver su expediente y patrones detallados
            mapa_nombres_est = {f"{e.name} (NIE: {e.id})": e for e in my_students}
            est_seleccionado_label = st.selectbox("Seleccionar Estudiante para Diagnóstico Detallado",
                                                  list(mapa_nombres_est.keys()),
                                                  key="sb_orientador_select_alumno_detalle")
            est_obj = mapa_nombres_est[est_seleccionado_label]

            # Obtenemos todo el historial de asistencia del estudiante
            historial_est = session.query(Attendance).filter_by(student_id=est_obj.id).all()

            total_asistencias = len(historial_est)
            presentes = sum(1 for h in historial_est if h.status == "Presente")
            ausentes = sum(1 for h in historial_est if h.status == "Ausente")
            tardanzas = sum(1 for h in historial_est if h.status == "Tardanza")

            col_p1, col_p2, col_p3 = st.columns(3)
            col_p1.metric("Total Registros", total_asistencias)
            col_p2.metric("Asistencias / Presente", presentes)
            col_p3.metric("Tardanzas / Ausencias", f"{tardanzas} / {ausentes}")

            st.markdown("#### 🧠 Patrones Detectados en el Estudiante:")
            patrones = []

            if tardanzas >= 2:
                patrones.append(f"⏱️ **Llegadas tardías frecuentes:** Registra {tardanzas} tardanzas acumuladas.")
            if ausentes >= 2:
                patrones.append(f"⚠️ **Ausencias recurrentes:** Cuenta con {ausentes} inasistencias registradas.")

            # Patrón de días de la semana con faltas
            if historial_est:
                dias_faltas = [h.date.strftime('%A') for h in historial_est if h.status == "Ausente"]
                if dias_faltas:
                    dia_mas_frecuente = max(set(dias_faltas), key=dias_faltas.count)
                    patrones.append(f"📅 **Patrón por día:** Tiende a faltar o fallar más los días {dia_mas_frecuente}.")

            if not patrones:
                patrones.append(
                    "🟢 **Comportamiento regular:** No se detectan anomalías graves ni patrones de riesgo en este periodo.")

            for pat in patrones:
                st.info(pat)

# -------------------------------------------------------------
# TAB: GESTIÓN DE PERMISOS Y JUSTIFICACIONES (ORIENTADOR)
# -------------------------------------------------------------
if "📝 Gestión de Permisos" in available_tabs:
    idx_perm = available_tabs.index("📝 Gestión de Permisos")
    with tabs[idx_perm]:
        st.subheader(f"📝 Gestión de Permisos y Justificaciones — Sección: {current_user.assigned_section}")
        st.info(
            "Registra ausencias justificadas por rango de fechas (incapacidades médicas, permisos oficiales, etc.) para que el sistema genere automáticamente el estatus correspondiente.")

        my_students_perm = session.query(Student).filter_by(section=current_user.assigned_section).all()

        if not my_students_perm:
            st.warning("No hay estudiantes registrados en tu sección para gestionar permisos.")
        else:
            mapa_est_perm = {f"{e.name} (NIE: {e.id})": e for e in my_students_perm}
            est_perm_label = st.selectbox("Seleccionar Estudiante", list(mapa_est_perm.keys()),
                                          key="sb_permiso_select_estudiante")
            est_obj_perm = mapa_est_perm[est_perm_label]

            st.write(f"Estudiante seleccionado: **{est_obj_perm.name}** (NIE: `{est_obj_perm.id}`)")

            st.divider()
            with st.form("form_registro_permiso_rango"):
                st.markdown("#### 📅 Definir Rango de Fechas del Permiso / Constancia")

                col_f1, col_f2 = st.columns(2)
                with col_f1:
                    fecha_inicio = st.date_input("Fecha de Inicio", value=date.today(), key="date_permiso_inicio")
                with col_f2:
                    fecha_fin = st.date_input("Fecha de Fin", value=date.today(), key="date_permiso_fin")

                tipo_permiso = st.selectbox(
                    "Motivo / Tipo de Justificación",
                    ["Constancia Médica", "Permiso Familiar", "Actividad Institucional / Deportiva", "Otro Motivo"],
                    key="sb_tipo_permiso_motivo"
                )

                observacion_permiso = st.text_area(
                    "Detalles / Observación de la Justificación",
                    placeholder="Ej. Reposo médico prescrito por el ISSS por 3 días.",
                    key="txt_obs_permiso_detalle"
                )

                btn_guardar_permiso = st.form_submit_button("🚀 Registrar Permiso y Generar Asistencias Automáticas",
                                                            width='stretch')

                if btn_guardar_permiso:
                    if fecha_inicio > fecha_fin:
                        st.error("❌ La fecha de inicio no puede ser posterior a la fecha de fin.")
                    else:
                        # Iterar por cada día del rango seleccionado
                        delta_dias = (fecha_fin - fecha_inicio).days
                        dias_generados = 0

                        for i in range(delta_dias + 1):
                            dia_actual = fecha_inicio + timedelta(days=i)

                            # Opcional: Omitir fines de semana (Sábado=5, Domingo=6) si se desea, o registrar todos los días corridos
                            # Registrarémos todos los días del rango según lo solicite el docente

                            # Verificamos si ya existe un registro de asistencia para este alumno en esta fecha exacta
                            reg_att = session.query(Attendance).filter_by(student_id=est_obj_perm.id,
                                                                          date=dia_actual).first()

                            texto_observacion = f"[{tipo_permiso}] {observacion_permiso}".strip()

                            if not reg_att:
                                nuevo_reg_att = Attendance(
                                    student_id=est_obj_perm.id,
                                    subject_id=1,  # ID por defecto para justificaciones generales del orientador
                                    date=dia_actual,
                                    status="Ausente",
                                    observation=texto_observacion
                                )
                                session.add(nuevo_reg_att)
                            else:
                                reg_att.subject_id = reg_att.subject_id if reg_att.subject_id else 1
                                reg_att.status = "Ausente"
                                reg_att.observation = texto_observacion
                            dias_generados += 1

                        session.commit()
                        st.success(
                            f"✅ ¡Permiso aplicado con éxito! Se han generado/actualizado los registros de asistencia para {dias_generados} día(s).")
                        st.rerun()
        st.divider()
        st.markdown("#### 📋 Historial de Inasistencias y Permisos Registrados del Estudiante")

        # Consultamos los registros de asistencia de este alumno que contengan una observación de permiso o justificación
        historial_permisos = session.query(Attendance).filter(
            Attendance.student_id == est_obj_perm.id,
            Attendance.observation.isnot(None),
            Attendance.observation != ""
        ).order_by(Attendance.date.desc()).all()

        if historial_permisos:
            datos_hist_perm = []
            for hp in historial_permisos:
                datos_hist_perm.append({
                    "Fecha": hp.date.strftime('%d/%m/%Y'),
                    "Estatus": hp.status,
                    "Detalle / Observación": hp.observation
                })

            df_hp = pd.DataFrame(datos_hist_perm)
            st.dataframe(df_hp, width='stretch', height=200)
        else:
            st.info("ℹ️ No hay permisos ni observaciones registradas para este estudiante actualmente.")
# -------------------------------------------------------------
# TAB: CARGA Y GESTIÓN DE DOCENTES (SOLO ADMIN)
# -------------------------------------------------------------
if "👨‍🏫 Carga y Gestión de Docentes" in available_tabs:
    idx_doc = available_tabs.index("👨‍🏫 Carga y Gestión de Docentes")
    with tabs[idx_doc]:
        st.subheader("👨‍🏫 Carga y Gestión de Cuentas de Docentes")

        col_doc1, col_doc2 = st.columns([1, 1])

        with col_doc1:
            with st.container(border=True):
                st.markdown("#### 👤 Registrar Nuevo Docente / Orientador")
                with st.form("form_crear_docente_nuevo_tab"):
                    docente_seleccionado_reg = st.selectbox("Nombre del Docente", lista_docentes_horarios,
                                                            key="sb_docente_nombre_select_tab")
                    nuevo_user_doc = st.text_input("NIE / Usuario del Docente", key="txt_nie_doc_tab")
                    nuevo_pass_doc = st.text_input("Contraseña", value="indet2026", type="password",
                                                   key="txt_pass_doc_tab")

                    btn_crear_doc = st.form_submit_button("➕ Crear / Actualizar Cuenta de Docente",
                                                          width='stretch')

                    if btn_crear_doc:
                        if nuevo_user_doc.strip() and docente_seleccionado_reg != "Sin Docentes Creados en Horarios":
                            nie_doc_str = nuevo_user_doc.strip()

                            seccion_encontrada = "Ninguna"
                            try:
                                conn_h = sqlite3.connect("modulo_horarios/database.db")
                                cursor_h = conn_h.cursor()
                                cursor_h.execute("""
                                                 SELECT s.nombre
                                                 FROM seccion s
                                                          JOIN horario h ON s.id = h.seccion_id
                                                          JOIN docente d ON h.docente_id = d.id
                                                 WHERE d.nombre = ?
                                                 """, (docente_seleccionado_reg,))
                                res_sec = cursor_h.fetchone()
                                if res_sec and res_sec[0]:
                                    seccion_encontrada = res_sec[0]
                                conn_h.close()
                            except Exception as ex:
                                pass

                            sec_val = None if seccion_encontrada == "Ninguna" else seccion_encontrada

                            doc_existente = session.query(User).filter_by(username=nie_doc_str).first()
                            if not doc_existente:
                                nuevo_docente = User(
                                    username=nie_doc_str,
                                    password=nuevo_pass_doc.strip() if nuevo_pass_doc.strip() else "indet2026",
                                    role="Docente",
                                    assigned_section=sec_val
                                )
                                session.add(nuevo_docente)
                                session.commit()
                                st.success(
                                    f"¡Docente '{docente_seleccionado_reg}' (Usuario: {nie_doc_str}) creado con éxito! Sección asignada: {seccion_encontrada}")
                                st.rerun()
                            else:
                                doc_existente.password = nuevo_pass_doc.strip() if nuevo_pass_doc.strip() else doc_existente.password
                                doc_existente.assigned_section = sec_val
                                session.commit()
                                st.success(
                                    f"¡Docente '{nie_doc_str}' actualizado correctamente con la sección {seccion_encontrada}!")
                                st.rerun()
                        else:
                            st.warning("Por favor ingresa el NIE/Usuario y selecciona un docente válido.")

        with col_doc2:
            with st.container(border=True):
                st.markdown("#### 📋 Listado de Docentes Registrados")
                docentes_users = session.query(User).filter_by(role="Docente").all()
                if docentes_users:
                    datos_docentes = []
                    for d in docentes_users:
                        datos_docentes.append({
                            "Usuario / NIE": d.username,
                            "Contraseña": d.password,
                            "Sección Asignada": d.assigned_section if d.assigned_section else "Ninguna"
                        })
                    df_docentes_display = pd.DataFrame(datos_docentes)
                    st.dataframe(df_docentes_display, width='stretch', height=250)
                else:
                    st.info("No hay cuentas de docentes creadas todavía.")

                st.divider()

                # --- ELIMINAR DOCENTE ---
                st.markdown("#### 🗑️ Eliminar Cuenta de Docente")
                if docentes_users:
                    mapa_docentes_del = {f"{d.username} (Sección: {d.assigned_section or 'Ninguna'})": d.username for d in
                                         docentes_users}
                    docente_a_borrar_label = st.selectbox("Seleccionar Docente a Eliminar", list(mapa_docentes_del.keys()),
                                                          key="sb_docente_eliminar")
                    username_a_borrar = mapa_docentes_del[docente_a_borrar_label]

                    if st.button("🗑️ Borrar Docente Seleccionado", type="secondary", width='stretch',
                                 key="btn_delete_single_docente"):
                        try:
                            doc_obj = session.query(User).filter_by(username=username_a_borrar, role="Docente").first()
                            if doc_obj:
                                session.delete(doc_obj)
                                session.commit()
                                st.success(f"¡Cuenta del docente '{username_a_borrar}' eliminada con éxito!")
                                st.rerun()
                            else:
                                st.error("No se encontró la cuenta del docente.")
                        except Exception as err:
                            session.rollback()
                            st.error(f"Error al eliminar el docente: {err}")
                else:
                    st.info("No hay docentes disponibles para eliminar.")

                st.divider()

                # --- MODIFICAR CONTRASEÑA DE DOCENTE ---
                st.markdown("#### 🔐 Modificar Contraseña de Docente")
                if docentes_users:
                    mapa_docentes_pass = {f"{d.username} (Sección: {d.assigned_section or 'Ninguna'})": d.username for d in
                                          docentes_users}
                    docente_pass_label = st.selectbox("Seleccionar Docente", list(mapa_docentes_pass.keys()),
                                                      key="sb_docente_pass_select")
                    username_pass = mapa_docentes_pass[docente_pass_label]

                    nueva_pass_docente = st.text_input("Nueva Contraseña para el Docente", type="password",
                                                       key="txt_nueva_pass_docente_mod")

                    if st.button("💾 Actualizar Contraseña Docente", width='stretch',
                                 key="btn_update_docente_pass"):
                        if nueva_pass_docente.strip():
                            doc_usr = session.query(User).filter_by(username=username_pass, role="Docente").first()
                            if doc_usr:
                                doc_usr.password = nueva_pass_docente.strip()
                                session.commit()
                                st.success(f"¡Contraseña actualizada con éxito para el docente '{username_pass}'!")
                                st.rerun()
                            else:
                                st.warning("No se encontró el usuario docente.")
                        else:
                            st.warning("Por favor ingresa una contraseña válida.")
                else:
                    st.info("No hay docentes registrados para modificar contraseña.")