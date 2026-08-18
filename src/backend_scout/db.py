from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine


def create_db_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{db_path}")


def init_db(db_path: Path) -> None:
    engine = create_db_engine(db_path)
    SQLModel.metadata.create_all(engine)


def open_session(db_path: Path) -> Session:
    engine = create_db_engine(db_path)
    return Session(engine)

