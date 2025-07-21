from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


URL_DATABASE = "postgresql://postgres:pass@127.0.0.1:5432/universe"

engine = create_engine(
    URL_DATABASE,
    pool_size=100,          # Increase pool size (default was 5)
    max_overflow=30,       # Increase overflow limit (default was 10)
    pool_timeout=60,       # Increase timeout to 60 seconds
    pool_pre_ping=True     # Enable pre-ping to detect stale connections
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()