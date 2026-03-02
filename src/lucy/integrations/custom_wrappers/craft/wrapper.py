"""
A production-ready Python wrapper module for the Craft API.

This module provides a set of tools for an AI assistant to interact with the Craft API,
covering document management, space and folder organization, block manipulation,
comment management, user information retrieval, and search functionality.

The wrapper uses httpx for asynchronous HTTP requests and handles authentication
via a bearer token. It includes a `TOOLS` list defining available functions
with their descriptions and JSON schema parameters, and an `execute` function
to dispatch API calls.
"""

import httpx
import json

BASE_URL = "https://api.craft.do/v1"

async def _make_request(
    method: str,
    url: str,
    api_key: str,
    params: dict = None,
    json_data: dict = None,
) -> dict:
    """Helper function to make authenticated HTTP requests."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.request(
                method, url, headers=headers, params=params, json=json_data
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"API error: {e.response.status_code} - {e.response.text}"}
    except httpx.RequestError as e:
        return {"error": f"Request error: {e}"}
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON response: {response.text}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}

# --- Document Tools ---

async def craft_list_documents(api_key: str, spaceId: str = None, folderId: str = None, query: str = None, limit: int = None, offset: int = None) -> dict:
    """Retrieve a list of all documents accessible by the authenticated user, with optional filtering and pagination."""
    params = {k: v for k, v in locals().items() if k not in ['api_key', 'self'] and v is not None}
    return await _make_request("GET", f"{BASE_URL}/documents", api_key, params=params)

async def craft_get_document(api_key: str, documentId: str) -> dict:
    """Retrieve a specific document by its ID."""
    return await _make_request("GET", f"{BASE_URL}/documents/{documentId}", api_key)

async def craft_create_document(api_key: str, spaceId: str, title: str, folderId: str = None, content: str = None, templateId: str = None) -> dict:
    """Create a new document within a specified space."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'self'] and v is not None}
    return await _make_request("POST", f"{BASE_URL}/documents", api_key, json_data=json_data)

async def craft_update_document(api_key: str, documentId: str, title: str = None, content: str = None) -> dict:
    """Update an existing document's title or content."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'documentId', 'self'] and v is not None}
    return await _make_request("PATCH", f"{BASE_URL}/documents/{documentId}", api_key, json_data=json_data)

async def craft_delete_document(api_key: str, documentId: str) -> dict:
    """Delete a specific document."""
    return await _make_request("DELETE", f"{BASE_URL}/documents/{documentId}", api_key)

async def craft_export_document(api_key: str, documentId: str, format: str) -> dict:
    """Export a document in a specified format (e.g., 'markdown', 'pdf')."""
    params = {'format': format}
    return await _make_request("GET", f"{BASE_URL}/documents/{documentId}/export", api_key, params=params)

# --- Space Tools ---

async def craft_list_spaces(api_key: str) -> dict:
    """Retrieve a list of all spaces accessible by the authenticated user."""
    return await _make_request("GET", f"{BASE_URL}/spaces", api_key)

async def craft_get_space(api_key: str, spaceId: str) -> dict:
    """Retrieve a specific space by its ID."""
    return await _make_request("GET", f"{BASE_URL}/spaces/{spaceId}", api_key)

# --- Folder Tools ---

async def craft_list_folders_in_space(api_key: str, spaceId: str) -> dict:
    """Retrieve a list of folders within a specific space."""
    return await _make_request("GET", f"{BASE_URL}/spaces/{spaceId}/folders", api_key)

async def craft_get_folder(api_key: str, folderId: str) -> dict:
    """Retrieve a specific folder by its ID."""
    return await _make_request("GET", f"{BASE_URL}/folders/{folderId}", api_key)

async def craft_create_folder(api_key: str, spaceId: str, name: str, parentFolderId: str = None) -> dict:
    """Create a new folder within a space."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'spaceId', 'self'] and v is not None}
    return await _make_request("POST", f"{BASE_URL}/spaces/{spaceId}/folders", api_key, json_data=json_data)

async def craft_update_folder(api_key: str, folderId: str, name: str = None, parentFolderId: str = None) -> dict:
    """Update an existing folder's name or parent folder."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'folderId', 'self'] and v is not None}
    return await _make_request("PATCH", f"{BASE_URL}/folders/{folderId}", api_key, json_data=json_data)

async def craft_delete_folder(api_key: str, folderId: str) -> dict:
    """Delete a specific folder. This may also delete its contents."""
    return await _make_request("DELETE", f"{BASE_URL}/folders/{folderId}", api_key)

# --- Block Tools ---

async def craft_list_document_blocks(api_key: str, documentId: str) -> dict:
    """Retrieve all blocks for a given document."""
    return await _make_request("GET", f"{BASE_URL}/documents/{documentId}/blocks", api_key)

async def craft_get_block(api_key: str, blockId: str) -> dict:
    """Retrieve a specific block by its ID."""
    return await _make_request("GET", f"{BASE_URL}/blocks/{blockId}", api_key)

async def craft_add_block_to_document(api_key: str, documentId: str, content: dict, parentBlockId: str = None, index: int = None) -> dict:
    """Add a new block to a document."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'documentId', 'self'] and v is not None}
    return await _make_request("POST", f"{BASE_URL}/documents/{documentId}/blocks", api_key, json_data=json_data)

async def craft_update_block(api_key: str, blockId: str, content: dict = None, type: str = None) -> dict:
    """Update an existing block's content or properties."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'blockId', 'self'] and v is not None}
    return await _make_request("PATCH", f"{BASE_URL}/blocks/{blockId}", api_key, json_data=json_data)

async def craft_delete_block(api_key: str, blockId: str) -> dict:
    """Delete a specific block from a document."""
    return await _make_request("DELETE", f"{BASE_URL}/blocks/{blockId}", api_key)

# --- Comment Tools ---

async def craft_list_document_comments(api_key: str, documentId: str) -> dict:
    """Retrieve all comments for a given document."""
    return await _make_request("GET", f"{BASE_URL}/documents/{documentId}/comments", api_key)

async def craft_list_block_comments(api_key: str, blockId: str) -> dict:
    """Retrieve all comments for a given block."""
    return await _make_request("GET", f"{BASE_URL}/blocks/{blockId}/comments", api_key)

async def craft_get_comment(api_key: str, commentId: str) -> dict:
    """Retrieve a specific comment by its ID."""
    return await _make_request("GET", f"{BASE_URL}/comments/{commentId}", api_key)

async def craft_add_comment(api_key: str, documentId: str, content: str, blockId: str = None) -> dict:
    """Add a new comment to a document or a specific block within it."""
    json_data = {k: v for k, v in locals().items() if k not in ['api_key', 'documentId', 'self'] and v is not None}
    return await _make_request("POST", f"{BASE_URL}/documents/{documentId}/comments", api_key, json_data=json_data)

async def craft_update_comment(api_key: str, commentId: str, content: str) -> dict:
    """Update an existing comment's content."""
    json_data = {'content': content}
    return await _make_request("PATCH", f"{BASE_URL}/comments/{commentId}", api_key, json_data=json_data)

async def craft_delete_comment(api_key: str, commentId: str) -> dict:
    """Delete a specific comment."""
    return await _make_request("DELETE", f"{BASE_URL}/comments/{commentId}", api_key)

# --- User Tools ---

async def craft_get_current_user(api_key: str) -> dict:
    """Retrieve information about the authenticated user."""
    return await _make_request("GET", f"{BASE_URL}/users/me", api_key)

# --- Search Tools ---

async def craft_search(api_key: str, query: str, spaceId: str = None, limit: int = None, offset: int = None) -> dict:
    """Search for documents and blocks across all accessible content."""
    params = {k: v for k, v in locals().items() if k not in ['api_key', 'self'] and v is not None}
    return await _make_request("GET", f"{BASE_URL}/search", api_key, params=params)


TOOLS = [
    {
        "name": "craft_list_documents",
        "description": "Retrieve a list of all documents accessible by the authenticated user. Can filter by space, folder, or query, and supports pagination.",
        "parameters": {
            "type": "object",
            "properties": {
                "spaceId": {"type": "string", "description": "ID of the space to filter documents by."},
                "folderId": {"type": "string", "description": "ID of the folder to filter documents by."},
                "query": {"type": "string", "description": "A search query to filter documents by title or content."},
                "limit": {"type": "integer", "description": "Maximum number of documents to return."},
                "offset": {"type": "integer", "description": "Number of documents to skip for pagination."}
            }
        }
    },
    {
        "name": "craft_get_document",
        "description": "Retrieve a specific document by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to retrieve.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["documentId"]
        }
    },
    {
        "name": "craft_create_document",
        "description": "Create a new document within a specified space.",
        "parameters": {
            "type": "object",
            "properties": {
                "spaceId": {"type": "string", "description": "The ID of the space where the document will be created.", "pattern": "^[a-zA-Z0-9-]+$"},
                "title": {"type": "string", "description": "The title of the new document."},
                "folderId": {"type": "string", "description": "The ID of the folder to create the document in."},
                "content": {"type": "string", "description": "The initial content of the document."},
                "templateId": {"type": "string", "description": "The ID of a template to use for the new document."}
            },
            "required": ["spaceId", "title"]
        }
    },
    {
        "name": "craft_update_document",
        "description": "Update an existing document's title or content.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to update.", "pattern": "^[a-zA-Z0-9-]+$"},
                "title": {"type": "string", "description": "The new title for the document."},
                "content": {"type": "string", "description": "The new content for the document."}
            },
            "required": ["documentId"]
        }
    },
    {
        "name": "craft_delete_document",
        "description": "Delete a specific document.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to delete.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["documentId"]
        }
    },
    {
        "name": "craft_export_document",
        "description": "Export a document in a specified format.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to export.", "pattern": "^[a-zA-Z0-9-]+$"},
                "format": {"type": "string", "description": "The desired export format (e.g., 'markdown', 'pdf', 'html').", "enum": ["markdown", "pdf", "html", "json"]}
            },
            "required": ["documentId", "format"]
        }
    },
    {
        "name": "craft_list_spaces",
        "description": "Retrieve a list of all spaces accessible by the authenticated user.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "craft_get_space",
        "description": "Retrieve a specific space by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "spaceId": {"type": "string", "description": "The ID of the space to retrieve.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["spaceId"]
        }
    },
    {
        "name": "craft_list_folders_in_space",
        "description": "Retrieve a list of folders within a specific space.",
        "parameters": {
            "type": "object",
            "properties": {
                "spaceId": {"type": "string", "description": "The ID of the space to list folders from.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["spaceId"]
        }
    },
    {
        "name": "craft_get_folder",
        "description": "Retrieve a specific folder by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "folderId": {"type": "string", "description": "The ID of the folder to retrieve.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["folderId"]
        }
    },
    {
        "name": "craft_create_folder",
        "description": "Create a new folder within a space.",
        "parameters": {
            "type": "object",
            "properties": {
                "spaceId": {"type": "string", "description": "The ID of the space where the folder will be created.", "pattern": "^[a-zA-Z0-9-]+$"},
                "name": {"type": "string", "description": "The name of the new folder."},
                "parentFolderId": {"type": "string", "description": "The ID of the parent folder if creating a nested folder."}
            },
            "required": ["spaceId", "name"]
        }
    },
    {
        "name": "craft_update_folder",
        "description": "Update an existing folder's name or parent folder.",
        "parameters": {
            "type": "object",
            "properties": {
                "folderId": {"type": "string", "description": "The ID of the folder to update.", "pattern": "^[a-zA-Z0-9-]+$"},
                "name": {"type": "string", "description": "The new name for the folder."},
                "parentFolderId": {"type": "string", "description": "The new parent folder ID for the folder."}
            },
            "required": ["folderId"]
        }
    },
    {
        "name": "craft_delete_folder",
        "description": "Delete a specific folder. This action may also delete its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "folderId": {"type": "string", "description": "The ID of the folder to delete.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["folderId"]
        }
    },
    {
        "name": "craft_list_document_blocks",
        "description": "Retrieve all blocks for a given document.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to retrieve blocks from.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["documentId"]
        }
    },
    {
        "name": "craft_get_block",
        "description": "Retrieve a specific block by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "blockId": {"type": "string", "description": "The ID of the block to retrieve.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["blockId"]
        }
    },
    {
        "name": "craft_add_block_to_document",
        "description": "Add a new block to a document.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to add the block to.", "pattern": "^[a-zA-Z0-9-]+$"},
                "content": {"type": "object", "description": "The content of the new block. This should be a JSON object representing the block's structure and data."},
                "parentBlockId": {"type": "string", "description": "The ID of the parent block if adding a nested block."},
                "index": {"type": "integer", "description": "The position (0-based index) to insert the block at within its parent."}
            },
            "required": ["documentId", "content"]
        }
    },
    {
        "name": "craft_update_block",
        "description": "Update an existing block's content or properties.",
        "parameters": {
            "type": "object",
            "properties": {
                "blockId": {"type": "string", "description": "The ID of the block to update.", "pattern": "^[a-zA-Z0-9-]+$"},
                "content": {"type": "object", "description": "The new content for the block. This should be a JSON object representing the block's structure and data."},
                "type": {"type": "string", "description": "The new type for the block (e.g., 'textBlock', 'imageBlock')."}
            },
            "required": ["blockId"]
        }
    },
    {
        "name": "craft_delete_block",
        "description": "Delete a specific block from a document.",
        "parameters": {
            "type": "object",
            "properties": {
                "blockId": {"type": "string", "description": "The ID of the block to delete.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["blockId"]
        }
    },
    {
        "name": "craft_list_document_comments",
        "description": "Retrieve all comments for a given document.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to retrieve comments from.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["documentId"]
        }
    },
    {
        "name": "craft_list_block_comments",
        "description": "Retrieve all comments for a given block.",
        "parameters": {
            "type": "object",
            "properties": {
                "blockId": {"type": "string", "description": "The ID of the block to retrieve comments from.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["blockId"]
        }
    },
    {
        "name": "craft_get_comment",
        "description": "Retrieve a specific comment by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "commentId": {"type": "string", "description": "The ID of the comment to retrieve.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["commentId"]
        }
    },
    {
        "name": "craft_add_comment",
        "description": "Add a new comment to a document or a specific block within it.",
        "parameters": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string", "description": "The ID of the document to add the comment to.", "pattern": "^[a-zA-Z0-9-]+$"},
                "blockId": {"type": "string", "description": "The ID of the specific block to add the comment to. If not provided, the comment is added to the document.", "pattern": "^[a-zA-Z0-9-]+$"},
                "content": {"type": "string", "description": "The content of the new comment."}
            },
            "required": ["documentId", "content"]
        }
    },
    {
        "name": "craft_update_comment",
        "description": "Update an existing comment's content.",
        "parameters": {
            "type": "object",
            "properties": {
                "commentId": {"type": "string", "description": "The ID of the comment to update.", "pattern": "^[a-zA-Z0-9-]+$"},
                "content": {"type": "string", "description": "The new content for the comment."}
            },
            "required": ["commentId", "content"]
        }
    },
    {
        "name": "craft_delete_comment",
        "description": "Delete a specific comment.",
        "parameters": {
            "type": "object",
            "properties": {
                "commentId": {"type": "string", "description": "The ID of the comment to delete.", "pattern": "^[a-zA-Z0-9-]+$"}
            },
            "required": ["commentId"]
        }
    },
    {
        "name": "craft_get_current_user",
        "description": "Retrieve information about the authenticated user.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "craft_search",
        "description": "Search for documents and blocks across all accessible content.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query string."},
                "spaceId": {"type": "string", "description": "Optional ID of the space to limit the search to."},
                "limit": {"type": "integer", "description": "Maximum number of results to return."},
                "offset": {"type": "integer", "description": "Number of results to skip for pagination."}
            },
            "required": ["query"]
        }
    }
]

async def execute(tool_name: str, args: dict, api_key: str) -> dict:
    """
    Executes the specified Craft API tool with the given arguments.

    Args:
        tool_name (str): The name of the tool to execute (e.g., "craft_list_documents").
        args (dict): A dictionary of arguments for the tool.
        api_key (str): The bearer token for API authentication.

    Returns:
        dict: The JSON response from the API or an error dictionary.
    """
    func = globals().get(tool_name)
    if not func:
        return {"error": f"Tool '{tool_name}' not found."}
    try:
        return await func(api_key=api_key, **args)
    except TypeError as e:
        return {"error": f"Invalid arguments for tool '{tool_name}': {e}. Args provided: {args}"}
    except Exception as e:
        return {"error": f"Error executing tool '{tool_name}': {e}"}