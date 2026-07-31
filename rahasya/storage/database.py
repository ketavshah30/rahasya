"""Async PostgreSQL database management.

Provides the AsyncDatabaseManager for handling SQLAlchemy 2.0 async sessions,
connection pooling, and database initialization.
"""
import contextlib
from typing import AsyncGenerator
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from rahasya.config import Settings
from rahasya.storage.sql_models import BaseSQLModel


class AsyncDatabaseManager:
    """Manages the async database connection and session lifecycle."""
    
    _instance = None
    _engine: AsyncEngine | None = None
    _sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def __new__(cls) -> "AsyncDatabaseManager":
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super(AsyncDatabaseManager, cls).__new__(cls)
        return cls._instance

    def initialize(self, settings: Settings) -> None:
        """Initialize the database engine and session maker."""
        if self._engine is not None:
            return

        db_url = settings.database.dsn.unicode_string()
        # Convert postgresql:// to postgresql+asyncpg:// if needed
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")

        logger.info(f"Initializing database connection pool to {settings.database.host}")
        
        self._engine = create_async_engine(
            db_url,
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
            echo=settings.database.echo_sql,
        )
        
        self._sessionmaker = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def init_db(self) -> None:
        """Create all tables in the database."""
        if self._engine is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
            
        logger.info("Creating database tables if they do not exist.")
        try:
            async with self._engine.begin() as conn:
                await conn.run_sync(BaseSQLModel.metadata.create_all)
            logger.info("Database tables created successfully.")
        except SQLAlchemyError as e:
            logger.error(f"Failed to create database tables: {e}")
            raise

    async def close_db(self) -> None:
        """Close the database engine."""
        if self._engine is None:
            return
            
        logger.info("Closing database engine.")
        try:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            logger.info("Database engine closed.")
        except SQLAlchemyError as e:
            logger.error(f"Error closing database engine: {e}")
            raise

    async def health_check(self) -> bool:
        """Perform a simple health check query."""
        if self._engine is None:
            return False
            
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    @contextlib.asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a transactional scope around a series of operations."""
        if self._sessionmaker is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
            
        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception as e:
            logger.error(f"Session rollback due to error: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()


db_manager = AsyncDatabaseManager()
