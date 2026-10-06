from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

engine = None
SessionLocal = None


def init_db(database_url: str) -> None:
    global engine, SessionLocal
    if engine is not None:
        engine.dispose()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(engine)
    _ensure_binary_column("screenshots", "thumbnail_data")
    _ensure_binary_column("full_images", "image_data")
    _ensure_column("full_images", "duplicate_of_id", "INTEGER")
    _ensure_column("screenshots", "input_active", "BOOLEAN" if engine.dialect.name == "postgresql" else "INTEGER")


def _ensure_binary_column(table: str, column: str) -> None:
    column_type = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
    _ensure_column(table, column, column_type)


def _ensure_column(table: str, column: str, column_type: str) -> None:
    insp = inspect(engine)
    if not insp.has_table(table):
        return
    names = {item["name"] for item in insp.get_columns(table)}
    if column in names:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        raise RuntimeError("Database is not initialized")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
