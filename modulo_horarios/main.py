import io
import os
import random
from typing import List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ReportLab para exportación PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlmodel import Field, Session, SQLModel, create_engine, select, delete

# ---------------------------------------------------------
# 1. CONFIGURACIÓN DE BASE DE DATOS SQLMODEL
# ---------------------------------------------------------
sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})


def get_session():
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------
# 2. MODELOS DE DATOS (TABLAS DE LA BD)
# ---------------------------------------------------------
class Seccion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    modalidad: str
    anio: str


class Docente(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    correo_institucional: str
    turno_preferente: str
    dias_matutino: Optional[str] = None
    dias_vespertino: Optional[str] = None


class Materia(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nombre: str
    tipo: str  # 'Básica' o 'Modular'


class CargaAcademica(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    docente_id: int = Field(foreign_key="docente.id")
    seccion_id: int = Field(foreign_key="seccion.id")
    materia_id: int = Field(foreign_key="materia.id")
    horas_semanales: int  # Medios bloques asignados


class Horario(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    seccion_id: int = Field(foreign_key="seccion.id")
    docente_id: int = Field(foreign_key="docente.id")
    materia_id: int = Field(foreign_key="materia.id")
    dia: str
    bloque_id: int
    hora_texto: str


class Estudiante(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    nie: Optional[str] = Field(default=None, index=True)  # Campo independiente para NIE/Código
    nombre: str
    grado: str
    seccion: str


# ---------------------------------------------------------
# 3. INICIALIZACIÓN DE FASTAPI Y RUTAS ESTÁTICAS
# ---------------------------------------------------------
app = FastAPI(title="NEXUS Engine CSP")

SQLModel.metadata.create_all(engine)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ---------------------------------------------------------
# 4. CONSTANTES DE BLOQUES Y TURNOS (INCLUYENDO RECESOS)
# ---------------------------------------------------------
MEDIOS_BLOQUES = [
    {"id": 1, "hora": "7:00 am - 7:45 am", "turno": "matutino", "pareja_id": 2, "es_pausa": False},
    {"id": 2, "hora": "7:45 am - 8:30 am", "turno": "matutino", "pareja_id": 1, "es_pausa": False},
    {"id": 991, "hora": "8:30 am - 8:40 am", "turno": "matutino", "pareja_id": None, "es_pausa": True,
     "tipo_pausa": "Receso"},
    {"id": 3, "hora": "8:40 am - 9:25 am", "turno": "matutino", "pareja_id": 4, "es_pausa": False},
    {"id": 4, "hora": "9:25 am - 10:10 am", "turno": "matutino", "pareja_id": 3, "es_pausa": False},
    {"id": 992, "hora": "10:10 am - 10:20 am", "turno": "matutino", "pareja_id": None, "es_pausa": True,
     "tipo_pausa": "Receso"},
    {"id": 5, "hora": "10:20 am - 11:05 am", "turno": "matutino", "pareja_id": 6, "es_pausa": False},
    {"id": 6, "hora": "11:05 am - 11:50 am", "turno": "matutino", "pareja_id": 5, "es_pausa": False},
    {"id": 993, "hora": "11:50 am - 1:00 pm", "turno": "almuerzo", "pareja_id": None, "es_pausa": True,
     "tipo_pausa": "Almuerzo"},
    {"id": 7, "hora": "1:00 pm - 1:45 pm", "turno": "vespertino", "pareja_id": 8, "es_pausa": False},
    {"id": 8, "hora": "1:45 pm - 2:30 pm", "turno": "vespertino", "pareja_id": 7, "es_pausa": False},
    {"id": 994, "hora": "2:30 pm - 2:40 pm", "turno": "vespertino", "pareja_id": None, "es_pausa": True,
     "tipo_pausa": "Receso"},
    {"id": 9, "hora": "2:40 pm - 3:25 pm", "turno": "vespertino", "pareja_id": 10, "es_pausa": False},
    {"id": 10, "hora": "3:25 pm - 4:10 pm", "turno": "vespertino", "pareja_id": 9, "es_pausa": False},
    {"id": 995, "hora": "4:10 pm - 4:20 pm", "turno": "vespertino", "pareja_id": None, "es_pausa": True,
     "tipo_pausa": "Receso"},
    {"id": 11, "hora": "4:20 pm - 5:10 pm", "turno": "vespertino", "pareja_id": 12, "es_pausa": False},
    {"id": 12, "hora": "5:10 pm - 5:50 pm", "turno": "vespertino", "pareja_id": 11, "es_pausa": False},
]

DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    replacements = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"))
    s = texto.lower().strip()
    for a, b in replacements:
        s = s.replace(a, b)
    return s


def es_bloque_permitido(docente: Docente, dia: str, bloque: dict) -> bool:
    if bloque.get("es_pausa"):
        return False

    turno = normalizar_texto(docente.turno_preferente or "")
    bloque_turno = bloque["turno"]
    dia_norm = normalizar_texto(dia)

    if "matutino" in turno and "doble" not in turno and "accesible" not in turno:
        return bloque_turno == "matutino"

    if "vespertino" in turno and "doble" not in turno and "accesible" not in turno:
        return bloque_turno == "vespertino"

    if "doble" in turno or turno == "doble_turno":
        return True

    if "accesible" in turno:
        if bloque_turno == "matutino":
            dias_mat = [normalizar_texto(d) for d in (docente.dias_matutino or "").split(",") if d.strip()]
            return dia_norm in dias_mat
        elif bloque_turno == "vespertino":
            dias_vesp = [normalizar_texto(d) for d in (docente.dias_vespertino or "").split(",") if d.strip()]
            return dia_norm in dias_vesp

    return False


# ---------------------------------------------------------
# 5. VISTA PRINCIPAL
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(
        request: Request,
        seccion_seleccionada: Optional[str] = None,
        seccion_estudiante: Optional[str] = None,
        session: Session = Depends(get_session)
):
    secciones = session.exec(select(Seccion)).all()
    lista_secciones = [s.nombre for s in secciones]

    seccion_actual = seccion_seleccionada if seccion_seleccionada else (
        lista_secciones[0] if lista_secciones else "Sin Secciones")
    sec_obj = session.exec(select(Seccion).where(Seccion.nombre == seccion_actual)).first()

    mantenimiento_dias = [
        ("lunes", "Lunes"),
        ("martes", "Martes"),
        ("miercoles", "Miércoles"),
        ("jueves", "Jueves"),
        ("viernes", "Viernes")
    ]

    tabla_horario = []
    for b in MEDIOS_BLOQUES:
        fila = {
            "hora": b["hora"],
            "es_pausa": b.get("es_pausa", False),
            "tipo_pausa": b.get("tipo_pausa", "")
        }
        for dia_clave, dia_bd in mantenimiento_dias:
            clase_data = {"materia": "-", "docente": "-", "tipo": "ninguno"}

            if sec_obj and not b.get("es_pausa"):
                clase = session.exec(
                    select(Horario).where(
                        Horario.seccion_id == sec_obj.id,
                        Horario.dia == dia_bd,
                        Horario.bloque_id == b["id"]
                    )
                ).first()
                if clase:
                    doc = session.get(Docente, clase.docente_id)
                    mat = session.get(Materia, clase.materia_id)
                    clase_data = {
                        "materia": mat.nombre if mat else "Desconocida",
                        "docente": doc.nombre if doc else "Desconocido",
                        "tipo": mat.tipo if mat else "Básica"
                    }
            fila[dia_clave] = clase_data
        tabla_horario.append(fila)

    docentes = session.exec(select(Docente)).all()
    materias = session.exec(select(Materia)).all()

    # FILTRADO DINÁMICO DE ESTUDIANTES POR SECCIÓN
    query_estudiantes = select(Estudiante)
    if seccion_estudiante and seccion_estudiante != "Todas":
        query_estudiantes = query_estudiantes.where(Estudiante.seccion == seccion_estudiante)

    estudiantes = session.exec(query_estudiantes).all()
    total_estudiantes_registrados = len(session.exec(select(Estudiante)).all())

    docentes_con_cargas = []
    for d in docentes:
        cargas_doc = session.exec(select(CargaAcademica).where(CargaAcademica.docente_id == d.id)).all()
        cargas_info = []
        for c in cargas_doc:
            s_item = session.get(Seccion, c.seccion_id)
            m_item = session.get(Materia, c.materia_id)
            cargas_info.append({
                "seccion": s_item,
                "materia": m_item,
                "medios_bloques_semanales": c.horas_semanales
            })
        docentes_con_cargas.append({
            "id": d.id,
            "nombre": d.nombre,
            "correo_institucional": d.correo_institucional,
            "turno_preferente": d.turno_preferente,
            "cargas": cargas_info
        })

    contexto = {
        "usuario": "admin",
        "rol": "Admin",
        "username": "admin",
        "secciones": secciones,
        "lista_secciones": lista_secciones,
        "seccion_actual": seccion_actual,
        "seccion_estudiante_filtro": seccion_estudiante or "Todas",
        "tabla_horario": tabla_horario,
        "docentes": docentes_con_cargas,
        "materias": materias,
        "estudiantes": estudiantes,
        "total_estudiantes": total_estudiantes_registrados,
        "total_docentes": len(docentes),
        "total_secciones": len(secciones),
        "alerta_cruce": False
    }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=contexto
    )


# ---------------------------------------------------------
# 6. ENDPOINTS CRUD REGISTROS
# ---------------------------------------------------------

@app.post("/api/secciones/eliminar/{seccion_id}")
def eliminar_seccion(seccion_id: int, session: Session = Depends(get_session)):
    sec = session.get(Seccion, seccion_id)
    if sec:
        session.delete(sec)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------
# ENDPOINT AGREGADO: CREAR SECCIÓN
# ---------------------------------------------------------
# Mapeo oficial de nomenclaturas en el backend
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

@app.post("/api/secciones/crear")
def crear_seccion(
        nombre: Optional[str] = Form(None),
        modalidad: Optional[str] = Form(None),
        anio: Optional[str] = Form(None),
        grupo: Optional[str] = Form(None),
        orientador_id: Optional[int] = Form(None),
        session: Session = Depends(get_session)
):
    # Generar el código abreviado oficial (Ej: BTV-DS-1A)
    nombre_seccion = nombre
    if not nombre_seccion and modalidad and anio and grupo:
        import re
        num_anio = re.search(r'\d+', str(anio))
        num_anio_str = num_anio.group() if num_anio else ""
        prefijo = MAPA_ABREVIACIONES.get(modalidad, "SEC")
        nombre_seccion = f"{prefijo}-{num_anio_str}{grupo}"

    if not nombre_seccion:
        nombre_seccion = "Sección Sin Nombre"

    nueva_seccion = Seccion(
        nombre=nombre_seccion,
        modalidad=modalidad or "General",
        anio=f"{anio} '{grupo}'" if anio and grupo else (anio or "1° año")
    )

    session.add(nueva_seccion)
    session.commit()
    session.refresh(nueva_seccion)

    return RedirectResponse(url="/", status_code=303)

@app.post("/api/docentes/crear")
def crear_docente(
        nombre: str = Form(...),
        correo_institucional: str = Form(...),
        turno_preferente: str = Form(...),
        dias_accesibles_matutino: List[str] = Form([]),
        dias_accesibles_vespertino: List[str] = Form([]),
        seccion_ids: List[int] = Form([]),
        materia_ids: List[int] = Form([]),
        horas_asignadas: List[int] = Form([]),
        session: Session = Depends(get_session)
):
    d_mat = ",".join(dias_accesibles_matutino)
    d_vesp = ",".join(dias_accesibles_vespertino)

    docente = Docente(
        nombre=nombre,
        correo_institucional=correo_institucional,
        turno_preferente=turno_preferente,
        dias_matutino=d_mat,
        dias_vespertino=d_vesp
    )
    session.add(docente)
    session.commit()
    session.refresh(docente)

    for sec_id, mat_id, hrs in zip(seccion_ids, materia_ids, horas_asignadas):
        if sec_id and mat_id and hrs:
            carga = CargaAcademica(
                docente_id=docente.id,
                seccion_id=sec_id,
                materia_id=mat_id,
                horas_semanales=hrs
            )
            session.add(carga)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/docentes/eliminar/{docente_id}")
def eliminar_docente(docente_id: int, session: Session = Depends(get_session)):
    doc = session.get(Docente, docente_id)
    if doc:
        # Borrado masivo de cargas
        session.exec(delete(CargaAcademica).where(CargaAcademica.docente_id == docente_id))
        session.delete(doc)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/materias/crear")
def crear_materia(nombre: str = Form(...), tipo: str = Form(...), session: Session = Depends(get_session)):
    mat = Materia(nombre=nombre, tipo=tipo)
    session.add(mat)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/materias/eliminar/{materia_id}")
def eliminar_materia(materia_id: int, session: Session = Depends(get_session)):
    mat = session.get(Materia, materia_id)
    if mat:
        session.delete(mat)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/estudiantes/crear")
def crear_estudiante(
        nie: Optional[str] = Form(None),
        nombre: str = Form(...),
        grado: str = Form(...),
        seccion: str = Form(...),
        session: Session = Depends(get_session)
):
    est = Estudiante(nie=nie, nombre=nombre, grado=grado, seccion=seccion)
    session.add(est)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/estudiantes/eliminar/{estudiante_id}")
def eliminar_estudiante(estudiante_id: int, session: Session = Depends(get_session)):
    est = session.get(Estudiante, estudiante_id)
    if est:
        session.delete(est)
        session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/estudiantes/limpiar_todos")
def limpiar_estudiantes(session: Session = Depends(get_session)):
    session.exec(delete(Estudiante))
    session.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------
# 7. GENERADOR GLOBAL Y PDF EXPORT
# ---------------------------------------------------------
@app.post("/generar_horarios_globales")
def generar_horarios_globales(session: Session = Depends(get_session)):
    # Limpieza masiva optimizada
    session.exec(delete(Horario))
    session.commit()

    cargas = session.exec(select(CargaAcademica)).all()
    docentes_ocupados = set()
    secciones_ocupadas = set()

    cargas_lista = list(cargas)
    random.shuffle(cargas_lista)

    bloques_clase = [b for b in MEDIOS_BLOQUES if not b.get("es_pausa")]
    parejas_naturales = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12)]

    for carga in cargas_lista:
        docente = session.get(Docente, carga.docente_id)
        if not docente:
            continue

        horas_pendientes = carga.horas_semanales

        for dia in DIAS_SEMANA:
            if horas_pendientes < 2:
                break

            for id1, id2 in parejas_naturales:
                if horas_pendientes < 2:
                    break

                b1 = next((item for item in bloques_clase if item["id"] == id1), None)
                b2 = next((item for item in bloques_clase if item["id"] == id2), None)

                if not b1 or not b2:
                    continue

                if es_bloque_permitido(docente, dia, b1) and es_bloque_permitido(docente, dia, b2):
                    c_doc1, c_doc2 = (docente.id, dia, b1["id"]), (docente.id, dia, b2["id"])
                    c_sec1, c_sec2 = (carga.seccion_id, dia, b1["id"]), (carga.seccion_id, dia, b2["id"])

                    if (c_doc1 not in docentes_ocupados and c_doc2 not in docentes_ocupados and
                            c_sec1 not in secciones_ocupadas and c_sec2 not in secciones_ocupadas):

                        for bloq in [b1, b2]:
                            session.add(Horario(
                                seccion_id=carga.seccion_id,
                                docente_id=docente.id,
                                materia_id=carga.materia_id,
                                dia=dia,
                                bloque_id=bloq["id"],
                                hora_texto=bloq["hora"]
                            ))
                            docentes_ocupados.add((docente.id, dia, bloq["id"]))
                            secciones_ocupadas.add((carga.seccion_id, dia, bloq["id"]))

                        horas_pendientes -= 2

        if horas_pendientes == 1:
            asignado = False
            for dia in DIAS_SEMANA:
                if asignado:
                    break
                for b in bloques_clase:
                    if es_bloque_permitido(docente, dia, b):
                        c_doc = (docente.id, dia, b["id"])
                        c_sec = (carga.seccion_id, dia, b["id"])
                        if c_doc not in docentes_ocupados and c_sec not in secciones_ocupadas:
                            session.add(Horario(
                                seccion_id=carga.seccion_id,
                                docente_id=docente.id,
                                materia_id=carga.materia_id,
                                dia=dia,
                                bloque_id=b["id"],
                                hora_texto=b["hora"]
                            ))
                            docentes_ocupados.add(c_doc)
                            secciones_ocupadas.add(c_sec)
                            horas_pendientes -= 1
                            asignado = True
                            break

    session.commit()
    return RedirectResponse(url="/?msg=Horarios+Generados+Exitosamente", status_code=303)


@app.get("/exportar_pdf_todos")
def exportar_pdf_todos(session: Session = Depends(get_session)):
    pdf_path = os.path.join(STATIC_DIR, "Horarios_Consolidados_INDET.pdf")
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(letter),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )
    story = []
    styles = getSampleStyleSheet()

    secciones = session.exec(select(Seccion)).all()

    for idx, sec in enumerate(secciones):
        story.append(Paragraph(f"<b>HORARIO DE CLASES INSTITUCIONAL - SECCIÓN: {sec.nombre}</b>", styles['Heading1']))
        story.append(Spacer(1, 10))

        headers = ["Hora", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
        tabla_datos = [headers]

        for b in MEDIOS_BLOQUES:
            if b.get("es_pausa"):
                fila = [b["hora"], b.get("tipo_pausa", "Receso"), "", "", "", ""]
            else:
                fila = [b["hora"]]
                for dia in DIAS_SEMANA:
                    clase = session.exec(
                        select(Horario).where(
                            Horario.seccion_id == sec.id,
                            Horario.dia == dia,
                            Horario.bloque_id == b["id"]
                        )
                    ).first()

                    if clase:
                        docente = session.get(Docente, clase.docente_id)
                        materia = session.get(Materia, clase.materia_id)
                        nom_mat = materia.nombre if materia else "Desconocida"
                        nom_doc = docente.nombre if docente else "Desconocido"
                        fila.append(f"{nom_mat}\n({nom_doc})")
                    else:
                        fila.append("-")
            tabla_datos.append(fila)

        t = Table(tabla_datos, colWidths=[85, 130, 130, 130, 130, 130])

        t_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 0), (-1, -1), 7),
        ]

        for row_idx, b in enumerate(MEDIOS_BLOQUES, start=1):
            if b.get("es_pausa"):
                t_style.append(('SPAN', (1, row_idx), (5, row_idx)))
                t_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor('#F3F4F6')))
                t_style.append(('TEXTCOLOR', (0, row_idx), (-1, row_idx), colors.HexColor('#4B5563')))
                t_style.append(('FONTNAME', (0, row_idx), (-1, row_idx), 'Helvetica-Bold'))

        t.setStyle(TableStyle(t_style))
        story.append(t)

        # Insertar salto de página si no es la última sección
        if idx < len(secciones) - 1:
            story.append(PageBreak())

    doc.build(story)
    return FileResponse(pdf_path, media_type='application/pdf', filename="Horarios_Consolidados_INDET.pdf")


# ---------------------------------------------------------
# RUTA OPTIMIZADA: CARGA MASIVA DESDE EXCEL
# ---------------------------------------------------------
@app.post("/api/estudiantes/cargar_excel")
async def cargar_estudiantes_excel(
        file: UploadFile = File(...),
        seccion_destino: str = Form(...),
        grado_destino: str = Form("Bachillerato"),
        session: Session = Depends(get_session)
):
    contents = await file.read()
    df = pd.read_excel(io.BytesIO(contents))
    df.columns = [str(c).strip().lower() for c in df.columns]

    for _, row in df.iterrows():
        nie_raw = row.get("nie") or row.get("id") or row.get("codigo") or row.get("nie/id")
        nie_val = None

        if not pd.isna(nie_raw) and str(nie_raw).strip() != "":
            try:
                nie_val = str(int(float(str(nie_raw).strip())))
            except ValueError:
                nie_val = str(nie_raw).strip()

        nombre = str(
            row.get("nombre") or row.get("estudiante") or row.get("alumno") or row.get("nombre completo") or ""
        ).strip()

        if nombre:
            est_existente = None
            if nie_val:
                est_existente = session.exec(select(Estudiante).where(Estudiante.nie == nie_val)).first()

            if est_existente:
                est_existente.seccion = seccion_destino
                est_existente.grado = grado_destino
                session.add(est_existente)
            else:
                nuevo_est = Estudiante(
                    nie=nie_val,
                    nombre=nombre,
                    grado=grado_destino,
                    seccion=seccion_destino
                )
                session.add(nuevo_est)

    session.commit()
    return RedirectResponse(url=f"/?seccion_estudiante={seccion_destino}", status_code=303)