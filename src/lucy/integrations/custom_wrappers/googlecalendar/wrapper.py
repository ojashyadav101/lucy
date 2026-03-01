"""Google Calendar custom wrapper — executes via Composio REST API.

Unlike Clerk/Polar which use direct HTTP + API keys, Google Calendar
uses OAuth through Composio. This wrapper bypasses Composio's 3-step
meta-tool chain (SEARCH → GET_SCHEMA → MULTI_EXECUTE) by executing
actions directly via the v2 REST API.

The connected_account_id is resolved at first use by searching all
Composio connected accounts for an active googlecalendar connection.
"""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger()

# ── Tool Definitions ──────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lucy_custom_googlecalendar_list_events",
            "description": (
                "List events from Google Calendar for a date range. "
                "Returns event titles, times, attendees, and meeting links."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": (
                            "Start of date range in RFC3339 format "
                            "(e.g. '2026-03-01T00:00:00+05:30'). "
                            "REQUIRED."
                        ),
                    },
                    "time_max": {
                        "type": "string",
                        "description": (
                            "End of date range in RFC3339 format "
                            "(e.g. '2026-03-01T23:59:59+05:30'). "
                            "REQUIRED."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default: 20).",
                    },
                    "search_query": {
                        "type": "string",
                        "description": "Free-text search query to filter events.",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary').",
                    },
                },
                "required": ["time_min", "time_max"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lucy_custom_googlecalendar_create_event",
            "description": (
                "Create a new event on Google Calendar. "
                "Specify title, start time, duration, and optionally add "
                "attendees and create a Google Meet link."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Event title/summary. REQUIRED.",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": (
                            "Event start time as YYYY-MM-DDTHH:MM:SS "
                            "(naive, uses calendar timezone). REQUIRED."
                        ),
                    },
                    "event_duration_minutes": {
                        "type": "integer",
                        "description": "Duration in minutes (default: 60).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Event description/notes.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location.",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee email addresses.",
                    },
                    "create_meeting_room": {
                        "type": "boolean",
                        "description": "Create a Google Meet link (default: false).",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary').",
                    },
                },
                "required": ["title", "start_datetime"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lucy_custom_googlecalendar_quick_add",
            "description": (
                "Quickly add an event using natural language. "
                "Example: 'Lunch with Sarah at noon tomorrow'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "Natural language description of the event. REQUIRED."
                        ),
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary').",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lucy_custom_googlecalendar_find_free_slots",
            "description": (
                "Find free time slots for a set of people in a date range. "
                "Useful for scheduling meetings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start of range (RFC3339). REQUIRED.",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of range (RFC3339). REQUIRED.",
                    },
                    "attendee_emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Email addresses to check availability for. "
                            "REQUIRED."
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Timezone (default: 'Asia/Kolkata').",
                    },
                },
                "required": ["time_min", "time_max", "attendee_emails"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lucy_custom_googlecalendar_delete_event",
            "description": "Delete an event from Google Calendar by its event ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to delete. REQUIRED.",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary').",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lucy_custom_googlecalendar_update_event",
            "description": (
                "Update an existing calendar event. Provide the event_id "
                "and only the fields you want to change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The event ID to update. REQUIRED.",
                    },
                    "title": {
                        "type": "string",
                        "description": "New event title.",
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "New start time (YYYY-MM-DDTHH:MM:SS).",
                    },
                    "event_duration_minutes": {
                        "type": "integer",
                        "description": "New duration in minutes.",
                    },
                    "description": {
                        "type": "string",
                        "description": "New event description.",
                    },
                    "location": {
                        "type": "string",
                        "description": "New location.",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "New attendee list (replaces existing).",
                    },
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (default: 'primary').",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
]

# ── Composio Execution ────────────────────────────────────────────────

# Cache the resolved connected_account_id
_connected_account_id: str | None = None


def _resolve_connection(api_key: str) -> str | None:
    """Find the active googlecalendar connected_account_id."""
    global _connected_account_id
    if _connected_account_id:
        return _connected_account_id

    try:
        resp = httpx.get(
            "https://backend.composio.dev/api/v1/connectedAccounts",
            headers={"x-api-key": api_key},
            params={"appNames": "googlecalendar", "status": "ACTIVE"},
            timeout=10.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            _connected_account_id = items[0]["id"]
            logger.info(
                "calendar_connection_resolved",
                connected_account_id=_connected_account_id,
                entity=items[0].get("clientUniqueUserId"),
            )
            return _connected_account_id
    except Exception as e:
        logger.warning("calendar_connection_resolve_failed", error=str(e))

    return None


def _execute_composio_action(
    action_name: str,
    params: dict,
    api_key: str,
) -> dict:
    """Execute a Composio action via REST API."""
    conn_id = _resolve_connection(api_key)
    if not conn_id:
        return {
            "error": (
                "Google Calendar is not connected. "
                "Please connect it via the integrations settings."
            ),
        }

    try:
        resp = httpx.post(
            f"https://backend.composio.dev/api/v2/actions/{action_name}/execute",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "connectedAccountId": conn_id,
                "input": params,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            return {"error": data["error"]}

        return data.get("data", data)

    except httpx.TimeoutException:
        return {"error": "Google Calendar request timed out. Please try again."}
    except Exception as e:
        return {"error": f"Google Calendar API error: {e}"}


# ── Tool Dispatch ─────────────────────────────────────────────────────

def _format_events(raw: dict) -> dict:
    """Format raw Calendar API response into clean event list."""
    items = raw.get("items", [])
    if not items:
        return {"events": [], "count": 0, "message": "No events found."}

    events = []
    for ev in items:
        start = ev.get("start", {})
        end = ev.get("end", {})
        attendees = [
            a.get("email") for a in ev.get("attendees", [])
            if a.get("email")
        ]
        entry = {
            "id": ev.get("id"),
            "title": ev.get("summary", "(No title)"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "location": ev.get("location"),
            "status": ev.get("status"),
        }
        if attendees:
            entry["attendees"] = attendees
        # Extract Google Meet link
        conference = ev.get("conferenceData", {})
        entry_points = conference.get("entryPoints", [])
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                entry["meeting_link"] = ep.get("uri")
                break
        if ev.get("hangoutLink"):
            entry.setdefault("meeting_link", ev["hangoutLink"])
        events.append(entry)

    return {"events": events, "count": len(events)}


def execute(tool_name: str, parameters: dict, api_key: str) -> dict:
    """Execute a calendar tool. Dispatches to the right Composio action."""

    if tool_name == "googlecalendar_list_events":
        params = {
            "calendarId": parameters.get("calendar_id", "primary"),
            "timeMin": parameters["time_min"],
            "timeMax": parameters["time_max"],
            "maxResults": parameters.get("max_results", 20),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if parameters.get("search_query"):
            params["q"] = parameters["search_query"]

        result = _execute_composio_action(
            "GOOGLECALENDAR_EVENTS_LIST", params, api_key,
        )
        if "error" in result:
            return result
        return _format_events(result)

    elif tool_name == "googlecalendar_create_event":
        params = {
            "summary": parameters["title"],
            "start_datetime": parameters["start_datetime"],
            "event_duration_minutes": parameters.get("event_duration_minutes", 60),
        }
        if parameters.get("description"):
            params["description"] = parameters["description"]
        if parameters.get("location"):
            params["location"] = parameters["location"]
        if parameters.get("attendees"):
            params["attendees"] = parameters["attendees"]
        if parameters.get("create_meeting_room"):
            params["create_meeting_room"] = True
        if parameters.get("calendar_id"):
            params["calendar_id"] = parameters["calendar_id"]

        result = _execute_composio_action(
            "GOOGLECALENDAR_CREATE_EVENT", params, api_key,
        )
        if "error" in result:
            return result
        # Format the created event
        return {
            "created": True,
            "event_id": result.get("id"),
            "title": result.get("summary"),
            "start": result.get("start", {}).get("dateTime"),
            "end": result.get("end", {}).get("dateTime"),
            "link": result.get("htmlLink"),
            "meeting_link": result.get("hangoutLink"),
        }

    elif tool_name == "googlecalendar_quick_add":
        params = {
            "text": parameters["text"],
            "calendar_id": parameters.get("calendar_id", "primary"),
        }
        result = _execute_composio_action(
            "GOOGLECALENDAR_QUICK_ADD", params, api_key,
        )
        if "error" in result:
            return result
        return {
            "created": True,
            "event_id": result.get("id"),
            "title": result.get("summary"),
            "start": result.get("start", {}).get("dateTime"),
            "link": result.get("htmlLink"),
        }

    elif tool_name == "googlecalendar_find_free_slots":
        items = [{"id": email} for email in parameters["attendee_emails"]]
        params = {
            "items": items,
            "time_min": parameters["time_min"],
            "time_max": parameters["time_max"],
            "timezone": parameters.get("timezone", "Asia/Kolkata"),
        }
        result = _execute_composio_action(
            "GOOGLECALENDAR_FIND_FREE_SLOTS", params, api_key,
        )
        return result

    elif tool_name == "googlecalendar_delete_event":
        params = {
            "event_id": parameters["event_id"],
        }
        if parameters.get("calendar_id"):
            params["calendar_id"] = parameters["calendar_id"]

        result = _execute_composio_action(
            "GOOGLECALENDAR_DELETE_EVENT", params, api_key,
        )
        if "error" in result:
            return result
        return {"deleted": True, "event_id": parameters["event_id"]}

    elif tool_name == "googlecalendar_update_event":
        params = {
            "event_id": parameters["event_id"],
            "calendar_id": parameters.get("calendar_id", "primary"),
        }
        if parameters.get("title"):
            params["summary"] = parameters["title"]
        if parameters.get("start_datetime"):
            params["start_datetime"] = parameters["start_datetime"]
        if parameters.get("event_duration_minutes"):
            params["event_duration_minutes"] = parameters["event_duration_minutes"]
        if parameters.get("description"):
            params["description"] = parameters["description"]
        if parameters.get("location"):
            params["location"] = parameters["location"]
        if parameters.get("attendees"):
            params["attendees"] = parameters["attendees"]

        result = _execute_composio_action(
            "GOOGLECALENDAR_PATCH_EVENT", params, api_key,
        )
        if "error" in result:
            return result
        return {
            "updated": True,
            "event_id": parameters["event_id"],
            "title": result.get("summary"),
            "start": result.get("start", {}).get("dateTime"),
        }

    else:
        return {"error": f"Unknown calendar tool: {tool_name}"}
