# SQL Analytics API

Base URL:

```text
/api/v1
```

## Query Report

Streams a Markdown report for a natural-language analytics question.

```http
POST /api/v1/reports/query/
Content-Type: application/json
Accept: text/event-stream
```

### Request Body

| Field | Required | Description |
|---|---:|---|
| `query` | Yes | Natural-language report request. |
| `session` | Yes | Session/thread identifier used for memory and tracing. |
| `user_id` | No | Optional caller identifier for future tracing/policy use. |
| `include_tool_results` | No | Reserved flag for controlling tool visibility. Defaults to `true`. |

Example:

```json
{
  "query": "Create an agent performance report for agent 5928",
  "session": "agent-5928-review",
  "include_tool_results": true
}
```

### Streaming Response

The response uses `text/event-stream`. Each event is framed as:

```text
event: <event-name>
data: <payload>

```

Supported event names:

| Event | Purpose |
|---|---|
| `status` | Progress messages from the service. |
| `tool_call` | Reserved for visible tool-call notices. |
| `tool_result` | Reserved for compact tool-result notices. |
| `markdown` | Final report content chunks. |
| `error` | Recoverable failure details. |
| `done` | Terminal event with completion metadata. |

Example stream:

```text
event: status
data: retrieving memory

event: status
data: running report agent

event: markdown
data: # Agent Performance Report

event: markdown
data: Total priced bookings: 28,470

event: done
data: {"sql_calls": 8}

```

### Validation Error

Validation errors are normal JSON responses, not streams.

```json
{
  "success": false,
  "error": {
    "message": "Invalid report query request.",
    "details": {
      "query": ["This field may not be blank."],
      "session": ["This field may not be blank."]
    }
  }
}
```

## cURL Examples

### Stream a Report

```bash
curl -N \
  -X POST http://127.0.0.1:8000/api/v1/reports/query/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{
    "query": "Create an agent performance report for agent 5928",
    "session": "agent-5928-review"
  }'
```

### Validate Request Handling

```bash
curl -s \
  -X POST http://127.0.0.1:8000/api/v1/reports/query/ \
  -H 'Content-Type: application/json' \
  -d '{"query": "", "session": ""}'
```

## JavaScript Fetch Example

Use `fetch()` for POST streaming. Browser `EventSource` is GET-only, so it is
not suitable for this endpoint.

```js
const response = await fetch("http://127.0.0.1:8000/api/v1/reports/query/", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  },
  body: JSON.stringify({
    query: "find the top 10 international routes",
    session: "route-report-session",
  }),
});

if (!response.ok || !response.body) {
  throw new Error(await response.text());
}

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const chunk = decoder.decode(value, { stream: true });
  console.log(chunk);
}
```

## Python httpx Example

```python
import httpx

payload = {
    "query": "summarize booking sales by service for agent 5928",
    "session": "agent-5928-sales",
}

with httpx.stream(
    "POST",
    "http://127.0.0.1:8000/api/v1/reports/query/",
    headers={"Accept": "text/event-stream"},
    json=payload,
    timeout=None,
) as response:
    response.raise_for_status()
    for line in response.iter_lines():
        if line:
            print(line)
```

## Tool Contract

The agent receives only two tools.

### `memory(input: str) -> str`

Uses Lossmemory over HTTP.

- Retrieves prior context for the session and query.
- Stores the query, SQL call count, and final answer after the run using direct
  memory creation first, with event extraction as fallback.
- Does not query the business database.

### `sql(input: str) -> str`

Executes one guarded read-only SQL statement against `ANALYTICS_DATABASE_URL`.

The return value is raw compact table text:

```text
booking_service,bookings,net_sale,margin
Airline,23653,257506494.21,6959144.84
Hotel,129,2830887.14,55742.59
(2 rows)
```

There is no JSON wrapper around SQL results. The agent uses this same tool for
database discovery, sampling, validation, and final report queries.

## Error Notes

- If `ANALYTICS_DATABASE_URL` is missing, SQL tool calls return an SQL error.
- If `MEMORY_API_BASE_URL` is missing, memory calls return a configuration
  message and the report can still proceed.
- If Langfuse keys are missing, tracing is skipped without breaking requests.
