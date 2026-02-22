---
name: google_calendar
description: Google Calendar integration for hello@ojash.com. Use proxy tools (not built-in actions which have OAuth issues). Covers listing calendars, querying events, creating/updating events, and free/busy checks.
---

## Account Structure

- **Authenticated user:** `hello@ojash.com`
- **Primary calendar:** `hello@ojash.com` (timezone: `Asia/Kolkata`)
- **Google Workspace:** ojash.com domain
- **Conference support:** Google Meet (`hangoutsMeet`)
- **Default event length:** 60 minutes
- **Default reminders:** popup at 10 minutes
- **Locale:** en_GB, week starts Sunday, 12-hour time

### Calendars

| Calendar | ID | Access | Notes |
|----------|----|--------|-------|
| **Primary** | `hello@ojash.com` | owner | Main calendar, Asia/Kolkata TZ |
| Transferred from payment@ojashyadav.com | `c_85133842631cf6d00f60ae019a21cc844d74bc00b50e6bec2796c52e1b7f96ae@group.calendar.google.com` | owner | Legacy transferred events |
| Transferred from hello@ojash.com | `c_e061b7ce9c18e142ff1a257dcf5b18b1bfa7fd15b8ca9846b1061c04b4d600e1@group.calendar.google.com` | owner | Legacy transferred events |
| Transferred from ojash@macbookjournal.com | `c_18da603abdf537edc1eaa8bf132ce7be050709fe8a55043db521dee112d67581@group.calendar.google.com` | owner | Legacy transferred events |
| Holidays in India | `en-gb.indian#holiday@group.v.calendar.google.com` | reader | Public holiday calendar |

## Critical: Use Proxy Tools, Not Built-in Actions

**The built-in Pipedream action tools (`pd_google_calendar_list_events`, `pd_google_calendar_create_event`, etc.) fail with OAuth errors** (`Cannot read properties of undefined (reading 'oauth_access_token')`). The `pd_google_calendar_configure` tool also returns 404 errors.

**Always use the proxy tools instead:**
- `pd_google_calendar_proxy_get` — read-only, no approval needed
- `pd_google_calendar_proxy_post` — write, requires draft approval
- `pd_google_calendar_proxy_put` — write, requires draft approval
- `pd_google_calendar_proxy_patch` — write, requires draft approval
- `pd_google_calendar_proxy_delete` — write, requires draft approval

**Only working built-in tool:** `pd_google_calendar_get_date_time()` (returns UTC server time, not very useful)

Base URL for all API calls: `https://www.googleapis.com/calendar/v3`

## Important Functions

### Read Operations (proxy_get, no approval needed)

#### List All Calendars
```python
from sdk.tools.pd_google_calendar import pd_google_calendar_proxy_get

result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/users/me/calendarList"
)
# result["content"] is a JSON string; parse with json.loads()
# body.items[] contains calendar list entries
```

#### List Events (Primary Calendar, Upcoming)
```python
result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
    query_params={
        "timeMin": "2026-02-22T00:00:00+05:30",
        "timeMax": "2026-02-23T00:00:00+05:30",
        "singleEvents": "true",        # Expand recurring events
        "orderBy": "startTime",         # Requires singleEvents=true
        "maxResults": "20",
        "timeZone": "Asia/Kolkata"
    }
)
```

#### List Events (Specific Calendar)
```python
cal_id = "c_85133842631cf6d00f60ae019a21cc844d74bc00b50e6bec2796c52e1b7f96ae@group.calendar.google.com"
result = await pd_google_calendar_proxy_get(
    url=f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events",
    query_params={
        "timeMin": "2026-02-01T00:00:00Z",
        "timeMax": "2026-03-01T00:00:00Z",
        "singleEvents": "true",
        "maxResults": "50"
    }
)
```

#### Search Events by Text
```python
result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
    query_params={
        "q": "Design Review",           # Free-text search
        "singleEvents": "true",
        "orderBy": "startTime"
    }
)
```

#### Get Single Event
```python
result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events/EVENT_ID"
)
```

#### Get Calendar Details
```python
result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/calendars/primary"
)
```

#### Get User Settings
```python
result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/users/me/settings"
)
```

#### Get Color Palette
```python
result = await pd_google_calendar_proxy_get(
    url="https://www.googleapis.com/calendar/v3/colors"
)
# Returns calendar colors (1-24) and event colors (1-11)
```

### Write Operations (proxy_post/put/patch/delete, requires draft approval)

#### Create Event
```python
from sdk.tools.pd_google_calendar import pd_google_calendar_proxy_post

result = await pd_google_calendar_proxy_post(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
    json_body={
        "summary": "Team Standup",
        "description": "Weekly standup meeting",
        "location": "Google Meet",
        "start": {
            "dateTime": "2026-02-23T10:00:00+05:30",
            "timeZone": "Asia/Kolkata"
        },
        "end": {
            "dateTime": "2026-02-23T10:30:00+05:30",
            "timeZone": "Asia/Kolkata"
        },
        "attendees": [
            {"email": "person@example.com"}
        ],
        "conferenceData": {
            "createRequest": {
                "requestId": "unique-string-here",
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        }
    },
    query_params={"conferenceDataVersion": "1"}  # Required for Meet link creation
)
# Returns a draft_id requiring approval before execution
```

#### Create All-Day Event
```python
result = await pd_google_calendar_proxy_post(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events",
    json_body={
        "summary": "Company Holiday",
        "start": {"date": "2026-03-01"},    # Use "date" not "dateTime"
        "end": {"date": "2026-03-02"}       # End date is exclusive
    }
)
```

#### Update Event (PATCH — partial update)
```python
from sdk.tools.pd_google_calendar import pd_google_calendar_proxy_patch

result = await pd_google_calendar_proxy_patch(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events/EVENT_ID",
    json_body={
        "summary": "Updated Title",
        "description": "Updated description"
    }
)
```

#### Delete Event
```python
from sdk.tools.pd_google_calendar import pd_google_calendar_proxy_delete

result = await pd_google_calendar_proxy_delete(
    url="https://www.googleapis.com/calendar/v3/calendars/primary/events/EVENT_ID"
)
```

#### Free/Busy Query
```python
result = await pd_google_calendar_proxy_post(
    url="https://www.googleapis.com/calendar/v3/freeBusy",
    json_body={
        "timeMin": "2026-02-22T00:00:00+05:30",
        "timeMax": "2026-02-23T00:00:00+05:30",
        "timeZone": "Asia/Kolkata",
        "items": [{"id": "hello@ojash.com"}]
    }
)
```

## Date/Time Formatting

- **Timed events:** RFC3339 with timezone offset: `2026-02-22T10:00:00+05:30`
- **All-day events:** Date only: `2026-02-22` (end date is exclusive — use next day)
- **Primary timezone:** `Asia/Kolkata` (UTC+05:30)
- **Always include timezone offset** in dateTime values, or set `timeZone` in the body

## Response Parsing Pattern

Proxy responses have this structure:
```python
import json

result = await pd_google_calendar_proxy_get(url=...)
# result is a dict with "content" (JSON string) or direct body
content = result.get("content", "")
parsed = json.loads(content) if isinstance(content, str) else result
body = parsed.get("body", parsed)
# Now body contains the actual API response (e.g., body["items"] for lists)
```

## Pagination

List endpoints return `nextPageToken` when more results exist:
```python
query_params = {"maxResults": "50", "timeMin": "..."}
# Add to next request:
query_params["pageToken"] = body["nextPageToken"]
```

## Known Limitations

- **OAuth scopes:** ACL endpoints (`/acl`) return 403 — insufficient scopes
- **Built-in tools broken:** All `pd_google_calendar_*` action tools (list_events, create_event, etc.) fail — use proxy
- **Configure tool:** Returns 404 errors — cannot dynamically discover dropdown options
- **Transferred calendars:** 3 transferred calendars exist but are currently empty
- **Write operations:** All proxy write tools create drafts requiring Slack approval flow

## Event Color IDs (Quick Reference)

| ID | Color |
|----|-------|
| 1  | Lavender (#a4bdfc) |
| 2  | Sage (#7ae7bf) |
| 3  | Grape (#dbadff) |
| 4  | Flamingo (#ff887c) |
| 5  | Banana (#fbd75b) |
| 6  | Tangerine (#ffb878) |
| 7  | Peacock (#46d6db) |
| 8  | Graphite (#e1e1e1) |
| 9  | Blueberry (#5484ed) |
| 10 | Basil (#51b749) |
| 11 | Tomato (#dc2127) |

---
**Last verified:** 2026-02-22
**Note:** If built-in Pipedream actions start working in the future, prefer them over proxy calls for simplicity. Test with a simple `list_calendars` call first.
