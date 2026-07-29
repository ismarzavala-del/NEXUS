import math
from datetime import datetime, date, time
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Date, Time, Text
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
    status = Column(String(40), default='Presente', nullable=False)
    observation = Column(Text, nullable=True)

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


class Schedule(Base):
    __tablename__ = 'schedules'
    id = Column(Integer, primary_key=True)
    section = Column(String(50), nullable=False)
    subject_id = Column(Integer, ForeignKey('subjects.id'), nullable=False)
    day_of_week = Column(String(20), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    subject = relationship('Subject')


engine = create_engine('sqlite:///student_monitor.db', echo=False)


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


