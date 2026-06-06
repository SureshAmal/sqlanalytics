class SqlExecutionError(Exception):
    """Base exception for all SQL execution errors."""

    pass


class UnsafeSqlError(SqlExecutionError):
    """Exception raised when the SQL query is deemed unsafe for execution."""

    pass
