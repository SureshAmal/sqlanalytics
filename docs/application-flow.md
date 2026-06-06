# Application Flow

This document describes how SQL Analytics handles a report request from HTTP
entrypoint to streaming output.

## High-Level Flow

```text
Client                  Django View               Redis Stream            Celery Worker
  |                          |                          |                       |
  |--- POST /reports/query ->|                          |                       |
  |                          |--- Dispatch task ------->|                       |
  |                          |    (run_report_task)     |                       |
  |                          |                          |                       |
  |                          |                          |                       |
  |<-- 200 event-stream -----|                          |                       |
  |                          |                          |                       |
  |                          |<-- Read (XREAD BLOCK) ---|                       |
  |                          |    (loops yielding SSE)  |                       |
  |                          |                          |<-- XADD Event --------| (Memory, SQL,
  |                          |                          |                         Markdown, etc.)
  |<-- Stream SSE chunk -----|                          |                       |
  |                          |                          |                       |
```

## Request Lifecycle

1. `ReportQueryView` receives `POST /api/v1/reports/query/`.
2. `ReportQuerySerializer` validates input parameters.
3. Invalid requests return a normal JSON error response.
4. Valid requests dispatch `run_report_task` to Celery.
5. The view returns `StreamingHttpResponse` immediately, wrapping an `EventStreamConsumer`.
6. The consumer reads from the task's Redis Stream (`XREAD BLOCK`) and yields SSE-encoded chunks to the client.
7. The worker executes `ReportService.execute()`, publishing events to the stream in real-time.
8. Once `done` or `error` is read, the stream closes and key memory is reclaimed in Redis.

## Service Flow

```text
ReportService.execute (runs in Celery worker)
  |
  |-- define memory tool closure (publishes events to Redis instantly)
  |-- define sql tool closure (publishes events to Redis instantly)
  |
  |-- publish status: running report agent
  |-- LangChainReportRunner.run(...)
  |     |
  |     |-- agent calls memory (emits tool_call/tool_result to Redis)
  |     |-- agent calls sql (emits tool_call/tool_result to Redis)
  |     |-- agent streams Markdown (emits markdown events to Redis)
  |
  |-- publish status: storing memory
  |-- MemoryClient.store(session, summary)
  |
  |-- publish done
```

The service does not build SQL itself. SQL generation, database discovery, and
report composition are agent responsibilities, constrained by the two tools.
Memory is not preloaded into the service. The agent calls the `memory` tool
on demand whenever it needs prior context or stored SQL patterns.

## Agent Rules

The agent gets a system prompt with these core rules:

- Use only `memory` and `sql`.
- Call `memory` on demand when prior context is needed.
- Use `sql` for all database work.
- Do not assume the schema.
- Do not ask for schema tools or table-description tools.
- Run follow-up SQL when results are incomplete or ambiguous.
- Stream the final answer as business-readable Markdown.
- Do not dump SQL in the final report unless the user asks.

## Database Discovery

There is no schema preload or memory preload.

For a broad question, the agent should use SQL to discover what it needs:

```sql
select table_schema, table_name
from information_schema.tables
where table_schema not in ('pg_catalog', 'information_schema')
order by table_schema, table_name
limit 200
```

It can then inspect columns, status values, row samples, and aggregates using
the same SQL tool.

## Multi-Query Reports

A useful business report often needs many SQL calls. For example, an agent
performance report may run separate queries for:

- agent identity and status
- transaction date range
- financial totals
- service-wise revenue and margin
- booking success rates by service
- status distributions for suspicious services
- customer or app-user counts

The final Markdown report should combine those findings and mention assumptions
that affect interpretation.

## SQL Tool Flow

```text
Agent
  |
  | sql("select ...")
  v
ReportService.sql_tool
  |
  v
SqlProvider.execute_readonly
  |
  |-- SqlSafetyValidator.validate
  |-- apply row/output limits
  |-- execute read-only query
  |-- format raw compact table text
  v
Agent
```

Example SQL tool output:

```text
service,total,success,success_pct
Air,25602,8107,31.67
Visa,5262,0,0.00
Insurance,1566,541,34.55
(3 rows)
```

## SQL Safety

The SQL layer enforces:

- one statement per call
- read-only statements only
- blocked DML/DDL/procedural tokens
- query timeout
- maximum returned rows
- maximum output characters

Blocked operations include:

```text
insert update delete merge create alter drop truncate grant revoke call exec execute copy
```

## Database Roles

Two database URLs are used for different purposes.

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | Django application database. Migrations run here. |
| `ANALYTICS_DATABASE_URL` | Business analytics database queried by the SQL tool. |

The code intentionally does not fall back between these settings.

## Memory Flow

```text
Before agent run:
  MemoryClient.retrieve(session, query)

During agent run:
  agent may call memory(input)

After agent run:
  MemoryClient.store(session, query + SQL count + final answer)
```

Memory writes first use direct memory creation (`POST /memories/`) so the final
answer and report basis are preserved exactly. If direct creation is unavailable,
the client falls back to `/memory/events` extraction. Memory is not a database
schema source of truth; the agent still validates against the live database
through SQL.

## Observability Flow

Langfuse is optional and controlled by env vars:

- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST`

When configured:

```text
one report request -> one Langfuse trace
session -> trace session_id
memory calls -> events
sql calls -> events
completion -> metadata with sql_call_count
```

If Langfuse is not configured, requests still run.

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `analytics.views` | HTTP entrypoint; dispatches Celery task and initiates streaming response. |
| `analytics.serializers` | Request validation. |
| `analytics.factories` | Central Service Factory for dependency injection. |
| `analytics.broker` | Broker logic (Redis Streams transport and consumer). |
| `analytics.tasks` | Celery tasks executing the ReportService. |
| `analytics.services.reports` | Orchestration and tool closures; publishes events directly to the broker. |
| `analytics.agents.runner` | LangChain report runner and stream adapter. |
| `analytics.memory.client` | Lossmemory HTTP integration. |
| `analytics.sql.safety` | SQL validation. |
| `analytics.sql.providers` | Database execution and raw table formatting. |
| `analytics.observability.langfuse` | Optional Langfuse tracing. |
| `analytics.streaming` | SSE event encoding. |

## Future Database Support

PostgreSQL is implemented first.

MSSQL and other SQL databases should be added behind the existing provider
interface:

```text
SqlProvider.execute_readonly(sql: str) -> str
```

Each provider must keep the same agent-facing contract: raw compact table text
from a guarded read-only SQL call.
