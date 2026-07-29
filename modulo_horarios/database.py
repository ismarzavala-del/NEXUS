from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

import os
from sqlmodel import create_engine

# Obtenemos la ruta absoluta del directorio donde está este archivo script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Forzamos a que cree siempre 'database.db' en la misma carpeta que el código
sqlite_file_name = os.path.join(BASE_DIR, "database.db")
sqlite_url = f"sqlite:///{sqlite_file_name}"

engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
SQLALCHEMY_DATABASE_URL = "sqlite:///./nexus.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()