import math
import streamlit as st
from datetime import datetime, date, time
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Date, Time, Text, text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(50), nullable=False)
    role = Column(String(20), nullable=False)  # 'Admin', 'Docente', 'Alumno'
    assigned_section = Column(String(50), nullable=True)  # Sección asignada como Orientador/Tutor
    student_id = Column(Integer, ForeignKey('students.id'), nullable=True)
    device_id = Column(String(100), nullable=True)  # Dispositivo vinculado para asistencia por QR

    student = relationship('Student')


class Student(Base):
    __tablename__ = 'students'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    section = Column(String(50), nullable=False)
    attendances = relationship('Attendance', back_populates='student')
    cases = relationship('CaseTracker', back_populates='student')
    justifications = relationship('Justification', back_populates='student')


class Subject(Base):
    __tablename__ = 'subjects'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)


class Attendance(Base):
    __tablename__ = 'attendances'
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    subject_id = Column(Integer, ForeignKey('subjects.id'), nullable=False)
    date = Column(Date, default=date.today, nullable=False)
    status = Column(String(40), default='Presente', nullable=False)  # 'Presente', 'Tardanza', 'Ausente', 'Permiso'
    observation = Column(Text, nullable=True)
    evidencia_path = Column(Text, nullable=True)  # Ruta al archivo de evidencia (imagen/PDF) de la justificación

    student = relationship('Student', back_populates='attendances')
    subject = relationship('Subject', back_populates='subject_attendances')


Subject.subject_attendances = relationship('Attendance', back_populates='subject')


class CaseTracker(Base):
    __tablename__ = 'case_tracker'
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    status = Column(String(30), default='Observación')
    notes = Column(Text, nullable=True)
    created_at = Column(Date, default=date.today)

    student = relationship('Student', back_populates='cases')


class Justification(Base):
    __tablename__ = 'justifications'
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    reason = Column(String(50), nullable=False)
    details = Column(Text, nullable=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    created_at = Column(Date, default=date.today)

    student = relationship('Student', back_populates='justifications')


class ActaAsistencia(Base):
    __tablename__ = 'actas_asistencia'
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    fecha_generacion = Column(Date, default=date.today, nullable=False)
    pct_asistencia_snapshot = Column(Integer, nullable=True)  # % de asistencia al momento de generar el acta
    patrones_snapshot = Column(Text, nullable=True)  # patrones detectados, uno por línea
    acuerdos = Column(Text, nullable=True)  # acuerdos del estudiante, uno por línea
    compromisos = Column(Text, nullable=True)  # compromisos institucionales, uno por línea
    case_tracker_id = Column(Integer, ForeignKey('case_tracker.id'), nullable=True)
    creado_por = Column(String(50), nullable=True)  # username del docente que la generó

    student = relationship('Student')
    case = relationship('CaseTracker')


class QRAttendanceToken(Base):
    """Token de un QR de asistencia rápida generado por un docente, válido por 5 minutos."""
    __tablename__ = 'qr_attendance_tokens'
    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False)
    seccion = Column(String(50), nullable=False)
    subject_id = Column(Integer, ForeignKey('subjects.id'), nullable=True)
    docente_username = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class QRAttendanceCheckin(Base):
    """Registro de qué estudiante canjeó cuál token de QR (evita doble canje y sirve de auditoría)."""
    __tablename__ = 'qr_attendance_checkins'
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey('qr_attendance_tokens.id'), nullable=False)
    student_id = Column(Integer, ForeignKey('students.id'), nullable=False)
    checked_in_at = Column(DateTime, default=datetime.now, nullable=False)


class Schedule(Base):
    __tablename__ = 'schedules'
    id = Column(Integer, primary_key=True)
    section = Column(String(50), nullable=False)
    subject_id = Column(Integer, ForeignKey('subjects.id'), nullable=False)
    day_of_week = Column(String(20), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    subject = relationship('Subject')


# --- CONEXIÓN A POSTGRESQL (Neon) ---
# La URL de conexión NUNCA va escrita aquí en el código: se lee desde
# .streamlit/secrets.toml en local, o desde el panel "Secrets" de Streamlit
# Cloud en producción. Así, el mismo código se conecta a la rama 'dev' cuando
# trabajas en PyCharm y a la rama 'production' cuando corre desplegado,
# simplemente porque cada entorno tiene un secrets.toml distinto.
DB_URL = st.secrets["connections"]["postgresql"]["url"]

# pool_pre_ping=True: revisa que la conexión siga viva antes de usarla.
# Es importante con Neon porque el "pooler" puede cerrar conexiones inactivas.
engine = create_engine(DB_URL, pool_pre_ping=True, echo=False)


def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # Distancia en metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def init_db():
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # --- PARCHE QUIRÚRGICO: agrega columnas nuevas a bases de datos ya existentes ---
    # (Base.metadata.create_all NO agrega columnas a tablas que ya existen, solo crea
    # tablas nuevas; por eso columnas añadidas después del primer despliegue necesitan
    # esta migración manual, protegida con try/except para no romper si ya existe.)
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE attendances ADD COLUMN evidencia_path TEXT"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN device_id TEXT"))
            conn.commit()
        except Exception:
            pass

    sub_names = ["Informática", "Lenguaje", "Matemática"]
    for s_name in sub_names:
        if not session.query(Subject).filter_by(name=s_name).first():
            session.add(Subject(name=s_name))
    session.commit()



    if session.query(User).count() == 0:
        st1 = session.query(Student).filter_by(name="Juan Pérez").first()

        u_admin = User(username="admin", password="123", role="Admin")
        u_docente = User(username="profe", password="123", role="Docente", assigned_section="1° Desarrollo de Software")
        u_alumno = User(username="juan", password="123", role="Alumno", assigned_section="1° Desarrollo de Software",
                        student_id=st1.id if st1 else None)

        session.add_all([u_admin, u_docente, u_alumno])
        session.commit()

    if session.query(Schedule).count() == 0:
        sub1 = session.query(Subject).filter_by(name="Informática").first()
        sub2 = session.query(Subject).filter_by(name="Lenguaje").first()
        sub3 = session.query(Subject).filter_by(name="Matemática").first()

        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        for day in days:
            sch1 = Schedule(section="1° Desarrollo de Software", subject_id=sub1.id, day_of_week=day,
                            start_time=time(7, 0), end_time=time(8, 30))
            sch2 = Schedule(section="1° Desarrollo de Software", subject_id=sub2.id, day_of_week=day,
                            start_time=time(8, 40), end_time=time(10, 10))
            sch3 = Schedule(section="1° Desarrollo de Software", subject_id=sub3.id, day_of_week=day,
                            start_time=time(10, 20), end_time=time(12, 0))
            sch4 = Schedule(section="1° Desarrollo de Software", subject_id=sub1.id, day_of_week=day,
                            start_time=time(12, 1), end_time=time(23, 59))

            session.add_all([sch1, sch2, sch3, sch4])
        session.commit()

    session.close()


def get_session():
    Session = sessionmaker(bind=engine)
    return Session()