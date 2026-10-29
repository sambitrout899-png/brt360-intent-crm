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


# ---------------------------------------------------------------- per-session read cache
#
# Some lookups run many times in one request (the tenant's licences on every
# intent the catalog checks, the tenant's intent settings) - and the session's
# identity map only holds rows weakly, so even db.get() goes back to the
# database once the caller lets go. cached() keeps one answer per session: a
# flush that changes a row of the given model drops it, and so does the end of
# the transaction, so it is never staler than the transaction would be.


def cached(db: Session, key: tuple, models: type | tuple[type, ...], load):
    """load() once per session (and transaction), until a row of `models` changes."""
    store = db.info.setdefault("brt_cache", {})
    if key not in store:
        store[key] = (models if isinstance(models, tuple) else (models,), load())
    return store[key][1]


@event.listens_for(Session, "after_flush")
def _drop_changed(session: Session, flush_context) -> None:
    store = session.info.get("brt_cache")
    if store:
        # a row whose own columns changed - not one that only gained a related row (a definition's new value)
        dirty = (o for o in session.dirty if session.is_modified(o, include_collections=False))
        changed = {type(o) for o in (*session.new, *dirty, *session.deleted)}
        for key in [k for k, (models, _) in store.items() if changed.intersection(models)]:
            del store[key]


@event.listens_for(Session, "after_soft_rollback")
def _drop_all(session: Session, *args) -> None:
    # any rollback, a savepoint's too: what was read since may be undone
    session.info.pop("brt_cache", None)


@event.listens_for(Session, "after_transaction_end")
def _drop_at_end(session: Session, transaction) -> None:
    # the transaction itself, not a savepoint (each BIM row has one): the next one reads afresh
    if transaction.parent is None:
        session.info.pop("brt_cache", None)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
