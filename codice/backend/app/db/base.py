from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from app.config import settings


class Base(DeclarativeBase):
    pass


# NullPool: ogni task crea e chiude la propria connessione.
# Necessario per Celery (asyncio.run() crea un nuovo event loop ad ogni task —
# il connection pool standard causa "Future attached to a different loop").
engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
