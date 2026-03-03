"""
A production-ready Python wrapper module for the Notion API.

This wrapper provides a set of tools for an AI assistant to interact with Notion,
covering operations related to pages, users, databases, and blocks.
It uses httpx for asynchronous HTTP requests and handles authentication
via a Bearer token.
"""

import httpx
import json

BASE_URL = "https://api.notion.com/v1"

async def _make_request(
    method: str,
    url: str,
    api_key: str,
    json_data: dict = None,
    params: dict = None
) -> dict:
    """
    Helper function to make authenticated HTTP requests to the Notion API.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",  # Specify API version
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            response = await client.request(
                method, url, headers=headers, json=json_data, params=params
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP error occurred: {e.response.status_code} - {e.response.text}"}
        except httpx.RequestError as e:
            return {"error": f"An error occurred while requesting {e.request.url!r}: {e}"}
        except json.JSONDecodeError:
            return {"error": f"Failed to decode JSON from response: {response.text}"}
        except Exception as e:
            return {"error": f"An unexpected error occurred: {e}"}

async def notion_create_page(api_key: str, parent: dict, properties: dict = None, children: list = None, template: dict = None) -> dict:
    """
    Creates a new page in Notion.

    Args:
        api_key: The Notion integration token.
        parent: An object specifying the parent of the new page (e.g., {"page_id": "..."} or {"database_id": "..."}).
        properties: An object containing the properties of the new page.
        children: An array of block objects to be added as children to the new page.
        template: An object specifying a template to use for the new page.

    Returns:
        A dictionary containing the created page object or an error message.
    """
    payload = {"parent": parent}
    if properties:
        payload["properties"] = properties
    if children:
        payload["children"] = children
    if template:
        payload["template"] = template
    return await _make_request("POST", f"{BASE_URL}/pages", api_key, json_data=payload)

async def notion_list_users(api_key: str) -> dict:
    """
    Lists all users in the Notion workspace.

    Args:
        api_key: The Notion integration token.

    Returns:
        A dictionary containing a list of user objects or an error message.
    """
    return await _make_request("GET", f"{BASE_URL}/users", api_key)

async def notion_query_database(api_key: str, database_id: str, filter: dict = None) -> dict:
    """
    Queries a Notion database to retrieve pages matching specific filters.

    Args:
        api_key: The Notion integration token.
        database_id: The ID of the database to query.
        filter: An object defining the filter conditions for the query.

    Returns:
        A dictionary containing the query results (list of page objects) or an error message.
    """
    payload = {}
    if filter:
        payload["filter"] = filter
    return await _make_request("POST", f"{BASE_URL}/databases/{database_id}/query", api_key, json_data=payload)

async def notion_append_block_children(api_key: str, block_id: str, children: list) -> dict:
    """
    Appends new blocks as children to an existing block (e.g., a page).

    Args:
        api_key: The Notion integration token.
        block_id: The ID of the block to append children to.
        children: An array of block objects to append.

    Returns:
        A dictionary containing the appended block objects or an error message.
    """
    payload = {"children": children}
    return await _make_request("PATCH", f"{BASE_URL}/blocks/{block_id}/children", api_key, json_data=payload)


TOOLS = [
    {
        "name": "notion_create_page",
        "description": "Creates a new page in Notion. You can specify its parent (another page or a database), properties, and initial content (children blocks).",
        "parameters": {
            "type": "object",
            "properties": {
                "parent": {
                    "type": "object",
                    "description": "The parent of the new page. Must be either a 'page_id' or 'database_id'. Example: {'page_id': '...'}",
                    "oneOf": [
                        {"type": "object", "properties": {"page_id": {"type": "string"}}, "required": ["page_id"]},
                        {"type": "object", "properties": {"database_id": {"type": "string"}}, "required": ["database_id"]}
                    ]
                },
                "properties": {
                    "type": "object",
                    "description": "An object containing the properties of the new page. Keys are property names, values are Notion property objects. Example: {'Name': {'title': [{'text': {'content': 'My New Page'}}]}}"
                },
                "children": {
                    "type": "array",
                    "description": "An array of block objects to be added as children to the new page. Each object represents a block type (e.g., paragraph, heading).",
                    "items": {"type": "object"}
                },
                "template": {
                    "type": "object",
                    "description": "An object specifying a template to use for the new page. Example: {'template_id': '...'}"
                }
            },
            "required": ["parent"]
        }
    },
    {
        "name": "notion_list_users",
        "description": "Lists all users in the Notion workspace. This can be useful for identifying users to assign tasks or mention.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "notion_query_database",
        "description": "Queries a Notion database to retrieve pages (items) that match specific filter criteria. Useful for finding specific entries in a database.",
        "parameters": {
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The ID of the database to query."
                },
                "filter": {
                    "type": "object",
                    "description": "An object defining the filter conditions for the query. Consult Notion API documentation for filter syntax. Example: {'property': 'Status', 'status': {'equals': 'Done'}}"
                }
            },
            "required": ["database_id"]
        }
    },
    {
        "name": "notion_append_block_children",
        "description": "Appends new blocks as children to an existing block, such as a page or another block. This allows adding content dynamically.",
        "parameters": {
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "string",
                    "description": "The ID of the block (e.g., a page ID) to which children blocks will be appended."
                },
                "children": {
                    "type": "array",
                    "description": "An array of block objects to append. Each object represents a block type (e.g., paragraph, heading).",
                    "items": {"type": "object"}
                }
            },
            "required": ["block_id", "children"]
        }
    }
]

async def execute(tool_name: str, args: dict, api_key: str) -> dict:
    """
    Executes the specified Notion API tool with the given arguments and API key.
    """
    if tool_name == "notion_create_page":
        return await notion_create_page(api_key, **args)
    elif tool_name == "notion_list_users":
        return await notion_list_users(api_key)
    elif tool_name == "notion_query_database":
        return await notion_query_database(api_key, **args)
    elif tool_name == "notion_append_block_children":
        return await notion_append_block_children(api_key, **args)
    else:
        return {"error": f"Tool '{tool_name}' not found."}