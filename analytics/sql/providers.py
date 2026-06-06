from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import psycopg
from psycopg import sql as psycopg_sql
from psycopg.rows import tuple_row

from analytics.sql.exceptions import SqlExecutionError
from analytics.sql.safety import SqlSafetyValidator


class SqlProvider(Protocol):
    """Protocol for SQL query execution providers."""

    @property
    def dialect(self) -> str:
        """Return the dialect name for this provider."""
        raise NotImplementedError

    def execute_readonly(self, sql: str) -> str:
        """Execute a read-only SQL query and return results as formatted text."""
        raise NotImplementedError


@dataclass(frozen=True)
class SqlProviderConfig:
    """Configuration for SQL providers."""

    database_url: str
    dialect: str
    timeout_seconds: int
    max_rows: int
    max_chars: int


@dataclass
class PostgresProvider:
    """SQL provider for PostgreSQL."""

    config: SqlProviderConfig

    @property
    def dialect(self) -> str:
        """Return the dialect name for this provider."""
        return "postgres"

    def execute_readonly(self, sql: str) -> str:
        """Execute a read-only SQL query and return results as formatted text."""
        if not self.config.database_url:
            raise SqlExecutionError("ANALYTICS_DATABASE_URL is not configured.")

        statement = SqlSafetyValidator(self.dialect).validate(sql)
        limited_statement = self._with_limit(statement)

        try:
            with (
                psycopg.connect(
                    self.config.database_url,
                    row_factory=tuple_row,
                    connect_timeout=self.config.timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    psycopg_sql.SQL("set statement_timeout = {}").format(
                        psycopg_sql.Literal(self.config.timeout_seconds * 1000)
                    )
                )
                cursor.execute(psycopg_sql.SQL("begin transaction read only"))
                cursor.execute(cast(Any, limited_statement))
                rows = cursor.fetchall()
                columns = [column.name for column in cursor.description or []]
                cursor.execute(psycopg_sql.SQL("commit"))
        except psycopg.Error as exc:
            raise SqlExecutionError(str(exc)) from exc

        return format_table_text(columns, rows, self.config.max_chars)

    def _with_limit(self, statement: str) -> str:
        """Wrap the SQL statement with a limit clause if not already present."""
        if " limit " in f" {statement.lower()} ":
            return statement
        return (
            "select * from "
            f"({statement}) as analytics_limited_result limit {self.config.max_rows}"
        )


@dataclass
class UnsupportedSqlProvider:
    """SQL provider for unsupported dialects."""

    config: SqlProviderConfig

    @property
    def dialect(self) -> str:
        """Return the dialect name for this provider."""
        return self.config.dialect

    def execute_readonly(self, sql: str) -> str:
        """Raise exception as this dialect is not implemented."""
        raise SqlExecutionError(
            f"SQL dialect '{self.config.dialect}' is not implemented yet."
        )


def format_table_text(
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    max_chars: int,
) -> str:
    """Format the query results as CSV-like text with truncation.

    Args:
        columns: List of column names.
        rows: List of rows, where each row is a list of values.
        max_chars: Maximum number of characters to include in the output.

    Returns:
        Formatted table text, truncated if it exceeds max_chars.
    """
    lines = [",".join(_cell(column) for column in columns)]
    lines.extend(",".join(_cell(value) for value in row) for row in rows)
    lines.append(f"({len(rows)} rows)")
    output = "\n".join(lines)
    if len(output) <= max_chars:
        return output
    return (
        output[:max_chars].rstrip()
        + f"\n... output truncated after {max_chars} characters. Refine the query."
    )


def _cell(value: object) -> str:
    """Format a single value for CSV-like output."""
    if value is None:
        return ""
    text = str(value)
    if any(char in text for char in [",", "\n", '"']):
        return '"' + text.replace('"', '""').replace("\n", " ") + '"'
    return text
