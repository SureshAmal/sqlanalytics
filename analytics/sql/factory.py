from django.conf import settings

from analytics.sql.providers import (
    PostgresProvider,
    SqlProvider,
    SqlProviderConfig,
    UnsupportedSqlProvider,
)


def create_sql_provider() -> SqlProvider:
    """Create a SQL provider based on Django settings."""
    config = SqlProviderConfig(
        database_url=settings.ANALYTICS_DATABASE_URL,
        dialect=settings.ANALYTICS_DATABASE_DIALECT,
        timeout_seconds=settings.ANALYTICS_SQL_TIMEOUT_SECONDS,
        max_rows=settings.ANALYTICS_SQL_MAX_ROWS,
        max_chars=settings.ANALYTICS_SQL_MAX_CHARS,
    )
    if config.dialect == "postgres":
        return PostgresProvider(config)
    return UnsupportedSqlProvider(config)
