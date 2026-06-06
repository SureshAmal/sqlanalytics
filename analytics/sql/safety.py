from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import exp

from analytics.sql.exceptions import UnsafeSqlError

BLOCKED_TOKENS = {
    "alter",
    "call",
    "copy",
    "create",
    "delete",
    "drop",
    "execute",
    "exec",
    "grant",
    "insert",
    "merge",
    "revoke",
    "truncate",
    "update",
}


@dataclass(frozen=True)
class SqlSafetyValidator:
    """Validates SQL safety before execution."""

    dialect: str

    def validate(self, sql: str) -> str:
        """Validate the SQL query for safety.

        Args:
            sql: The SQL query to validate.

        Returns:
            The validated and cleaned SQL statement.

        Raises:
            UnsafeSqlError: If the SQL query is deemed unsafe."""
        statement = sql.strip()
        if not statement:
            raise UnsafeSqlError("SQL cannot be empty.")

        lowered = statement.lower()
        if ";" in statement.rstrip(";"):
            raise UnsafeSqlError("Only one SQL statement is allowed.")

        tokens = {
            token.strip(" \n\t\r(),;")
            for token in lowered.replace("\n", " ").split(" ")
            if token.strip()
        }
        blocked = sorted(tokens.intersection(BLOCKED_TOKENS))
        if blocked:
            raise UnsafeSqlError(f"Blocked SQL token: {', '.join(blocked)}.")

        try:
            parsed = sqlglot.parse(statement, read=self.dialect)
        except sqlglot.errors.SqlglotError as exc:
            raise UnsafeSqlError(f"SQL parse failed: {exc}") from exc

        if len(parsed) != 1:
            raise UnsafeSqlError("Only one SQL statement is allowed.")

        maybe_root = parsed[0]
        if maybe_root is None:
            raise UnsafeSqlError("SQL parse returned no statement.")
        root = cast(exp.Expression, maybe_root)
        if not _is_readonly_expression(root):
            raise UnsafeSqlError("Only read-only SELECT queries are allowed.")

        return statement.rstrip(";")


def _is_readonly_expression(expression: exp.Expression) -> bool:
    """Check if an expression is a read-only SQL construct."""
    readonly_types = (exp.Select, exp.Union, exp.Intersect, exp.Except)
    return isinstance(expression, readonly_types)
