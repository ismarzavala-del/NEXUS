from sqlalchemy import Column, Integer, String, ForeignKey, Table, JSON
from sqlalchemy.orm import relationship
import database

# Solución al AttributeError: exportamos Base
Base = database.Base

# Tabla intermedia Docente - Materia
docente_materia = Table(
    'docente_materia',
    Base.metadata,
    Column('docente_id', Integer, ForeignKey('docentes.id'), primary_key=True),
    Column('materia_id', Integer, ForeignKey('materias.id'), primary_key=True)
)

class Seccion(Base):
    __tablename__ = "secciones"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, index=True)
    modalidad = Column(String)
    anio = Column(String)

    cargas = relationship("CargaAcademica", back_populates="seccion", cascade="all, delete-orphan")
    estudiantes = relationship("Estudiante", back_populates="seccion")


class Docente(Base):
    __tablename__ = "docentes"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    correo_institucional = Column(String, unique=True)
    turno_preferente = Column(String)
    dias_accesibles = Column(JSON, default=[])

    materias = relationship("Materia", secondary=docente_materia, back_populates="docentes")
    cargas = relationship("CargaAcademica", back_populates="docente", cascade="all, delete-orphan")


class Materia(Base):
    __tablename__ = "materias"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True)
    tipo = Column(String)

    docentes = relationship("Docente", secondary=docente_materia, back_populates="materias")
    cargas = relationship("CargaAcademica", back_populates="materia")


class CargaAcademica(Base):
    __tablename__ = "cargas_academicas"

    id = Column(Integer, primary_key=True, index=True)
    docente_id = Column(Integer, ForeignKey("docentes.id"))
    seccion_id = Column(Integer, ForeignKey("secciones.id"))
    materia_id = Column(Integer, ForeignKey("materias.id"))
    medios_bloques_semanales = Column(Integer, default=4)

    docente = relationship("Docente", back_populates="cargas")
    seccion = relationship("Seccion", back_populates="cargas")
    materia = relationship("Materia", back_populates="cargas")


class Estudiante(Base):
    __tablename__ = "estudiantes"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String)
    grado = Column(String)
    seccion_id = Column(Integer, ForeignKey("secciones.id"), nullable=True)

    seccion = relationship("Seccion", back_populates="estudiantes")
    asistencias = relationship("Asistencia", back_populates="estudiante", cascade="all, delete-orphan")


class Aula(Base):
    __tablename__ = "aulas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True)
    capacidad = Column(Integer)


class Asistencia(Base):
    __tablename__ = "asistencias"

    id = Column(Integer, primary_key=True, index=True)
    estudiante_id = Column(Integer, ForeignKey("estudiantes.id"))
    fecha = Column(String)
    estado = Column(String)
    observacion = Column(String, nullable=True)

    estudiante = relationship("Estudiante", back_populates="asistencias")