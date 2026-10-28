import datetime as dt
from collections.abc import Generator

from sqlalchemy import DateTime, Integer, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app import brt_id
from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
if settings.database_app_role and settings.database_url.startswith("postgresql"):
    from app import rls

    rls.use_role(engine, settings.database_app_role)  # the plain role row-level security applies to
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Every table carries the record's BRT_ID (its identity across BRT 360,
    BIM loads and integrations - see app/brt_id.py) and system fields: when
    and by whom it was created (with the creator's position and department -
    what record visibility follows) and last updated. All are filled in
    automatically when a record is saved."""

    brt_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    sys_created_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    sys_created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sys_created_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sys_department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sys_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    sys_updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


brt_id.install()


@event.listens_for(Base.metadata, "after_create")
def _create_brt_id_register(target, connection, **kw) -> None:
    # Databases built straight from the models (the test suite); production
    # gets the register from its migration.
    brt_id.create_counter(connection)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
