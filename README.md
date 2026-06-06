# SQL Analytics

SQL Analytics is a Django REST application that streams Markdown business
reports from natural-language questions. It runs a local LangChain agent
with only two tools:

- `memory`: retrieve and store Lossmemory context for the session.
- `sql`: execute guarded read-only SQL and return raw compact table text.

The agent does not receive a preloaded schema. It discovers database structure,
samples rows, validates assumptions, and runs final report queries through the
same `sql` tool.

## Configuration

Copy `.env.example` to `.env` and fill the values.

```bash
cp .env.example .env
```

Database configuration is intentionally split:

- `DATABASE_URL`: Django application database for auth/admin/session tables.
- `ANALYTICS_DATABASE_URL`: target business database queried by the report
  agent.

Example:

```env
DATABASE_URL=postgres://postgres:postgres@localhost:5432/sqlanalytics
ANALYTICS_DATABASE_URL=postgres://postgres:postgres@localhost:5432/travel_erp_uat_qc
ANALYTICS_DATABASE_DIALECT=postgres
```

## Run Locally

Ensure you have a local Redis server running. Use separate Redis DBs/queues from
Lossmemory:

```env
CELERY_BROKER_URL=redis://127.0.0.1:6379/1
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
CELERY_TASK_DEFAULT_QUEUE=sqlanalytics
REPORT_STREAM_REDIS_URL=redis://127.0.0.1:6379/2
```

1. Run Django migrations:
   ```bash
   uv run manage.py migrate
   ```

2. Start the Django development server:
   ```bash
   uv run manage.py runserver 127.0.0.1:8001
   ```

3. Start the Celery worker (in a separate terminal):
   ```bash
   uv run celery -A config worker -Q sqlanalytics --loglevel=info
   ```

4. Start the standalone frontend with live-server:
   ```bash
   live-server frontend --host=127.0.0.1 --port=8080
   ```

Django does not serve frontend files. It only exposes the API under `/api/v1/`.

## API

```http
POST /api/v1/reports/query/
Content-Type: application/json
Accept: text/event-stream
```

Request:

```json
{
  "query": "find the top 10 international routes",
  "session": "session-123",
  "user_id": "optional-user-id",
  "include_tool_results": true
}
```

The response is streamed as Server-Sent Events. The final report arrives in
`markdown` events.

See [API docs](docs/api.md) for cURL, Python, and JavaScript examples.

## Application Flow

```text
Client
  |
  | POST /api/v1/reports/query/
  v
ReportQueryView
  |
  |-- Enqueues Celery task (run_report_task)
  |-- Subscribes to Redis Stream (report:<task_id>)
  v
StreamingHttpResponse (Consumer reads stream chunks in real-time)
  |
  + <--- Redis Stream --- (Celery Worker runs pipeline)
                             |
                             |-- Langfuse trace start
                             |-- LangChainReportRunner
                             |     |-- memory tool (publishes events to Redis)
                             |     |-- sql tool (publishes events to Redis)
                             |           |-- SqlSafetyValidator
                             |           |-- SqlProvider.execute_readonly
                             |-- MemoryClient.store
                             |-- Langfuse flush
```

The service does not prefetch memory before the agent starts. The agent calls
the `memory` tool on demand when it needs prior context or stored SQL patterns.

Detailed flow documentation is in [application flow](docs/application-flow.md).

## Safety Model

- SQL execution is read-only.
- Only one SQL statement is allowed per tool call.
- DML, DDL, procedure execution, and multi-statement SQL are blocked.
- Query timeout, row limit, and output character limit are enforced.
- SQL output is returned to the agent as compact raw table text, not JSON.

## Quality Checks

```bash
uv run manage.py check
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run ty check
uv run pytest
```

## Project Layout

```text
analytics/
  agents/          LangChain report runner
  memory/          Lossmemory HTTP client
  observability/   Langfuse tracing wrapper
  services/        Report orchestration
  sql/             SQL safety and provider adapters
  serializers.py   DRF request validation
  streaming.py     SSE event encoding
  views.py         Streaming API view
config/            Django settings and URL routing
docs/              API and flow documentation
frontend/          Standalone HTML/CSS/JS app for live-server
tests/             Unit and API tests
```
