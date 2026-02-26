"""
A production-ready Python wrapper module for the Polar.sh API.

This wrapper provides asynchronous functions to interact with the Polar.sh API,
covering various functionalities like managing benefits, checkouts, customers,
orders, products, subscriptions, and more. It includes robust error handling,
retry mechanisms with exponential backoff, rate limit awareness, and
automatic pagination for list endpoints.

Authentication is done via a Bearer token.
"""

import httpx
import asyncio
import json
import time

# Base URL for the Polar.sh API
BASE_URL = "https://api.polar.sh"

# Max retries for transient errors (429, 5xx)
MAX_RETRIES = 3
# Initial backoff delay in seconds
INITIAL_BACKOFF = 1
# Max page size for fetching all records
MAX_PAGE_SIZE = 100


class PolarAPIError(Exception):
    """Custom exception for Polar API errors."""
    pass


async def _make_request(
    method: str,
    url: str,
    api_key: str,
    params: dict = None,
    json_data: dict = None,
    retry_count: int = 0,
) -> dict:
    """
    Makes an HTTP request to the Polar.sh API with retry logic and rate limiting.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(30.0)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.request(
                method, url, params=params, json=json_data, headers=headers
            )
            response.raise_for_status()  # Raise an exception for 4xx or 5xx responses
            return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429 or (
            500 <= e.response.status_code < 600 and retry_count < MAX_RETRIES
        ):
            retry_after = e.response.headers.get("Retry-After")
            if retry_after:
                delay = int(retry_after)
            else:
                delay = INITIAL_BACKOFF * (2**retry_count)
            await asyncio.sleep(delay)
            return await _make_request(
                method, url, api_key, params, json_data, retry_count + 1
            )
        elif 400 <= e.response.status_code < 500:
            try:
                error_detail = e.response.json()
            except json.JSONDecodeError:
                error_detail = {"detail": e.response.text}
            return {"error": f"API Error {e.response.status_code}: {error_detail}"}
        else:
            return {"error": f"HTTP Error {e.response.status_code}: {e.response.text}"}
    except httpx.RequestError as e:
        return {"error": f"Request Error: {e}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}


async def _fetch_all_pages(
    url: str, api_key: str, params: dict, resource_key: str
) -> list:
    """
    Automatically paginates through results to fetch all records.
    """
    all_records = []
    page = 1
    has_more = True

    while has_more:
        current_params = {**params, "page": page, "limit": MAX_PAGE_SIZE}
        response = await _make_request("GET", url, api_key, params=current_params)

        if "error" in response:
            # If an error occurs during pagination, return what we have so far
            # along with the error.
            return all_records, response

        records = response.get(resource_key, [])
        all_records.extend(records)

        # Check for pagination info. Assuming 'pagination' key with 'total_pages' or similar.
        # If not present, assume single page or end of data.
        pagination_info = response.get("pagination")
        if pagination_info and pagination_info.get("total_pages"):
            has_more = page < pagination_info["total_pages"]
        elif not records:  # No records on this page, assume end
            has_more = False
        else:
            # If no explicit pagination info, and we got records,
            # assume there might be more unless the number of records
            # is less than the limit, indicating the last page.
            has_more = len(records) == MAX_PAGE_SIZE

        page += 1
        if has_more:
            await asyncio.sleep(0.5)  # Be kind to the API

    return all_records, None


def _compact_response(data: dict, keys_to_keep: list) -> dict:
    """Strips verbose internal fields from a dictionary."""
    if isinstance(data, dict):
        return {k: data[k] for k in keys_to_keep if k in data}
    return data


def _compact_list_response(data_list: list, keys_to_keep: list) -> list:
    """Strips verbose internal fields from a list of dictionaries."""
    return [_compact_response(item, keys_to_keep) for item in data_list]


# --- Tool Definitions ---

TOOLS = []


# --- Benefit Grants ---
async def polarsh_list_benefit_grants(api_key: str, organization_id: str = None, customer_id: str = None, external_customer_id: str = None, is_granted: bool = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of benefit grants. Default limit is 10, max is 100.
    Use polarsh_fetch_all_benefit_grants for the complete dataset.
    Returns fields like 'id', 'benefit_id', 'customer_id', 'granted_at', 'is_granted'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/benefit-grants/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'benefit_id', 'customer_id', 'granted_at', 'is_granted', 'expires_at'])
    return response

async def polarsh_fetch_all_benefit_grants(api_key: str, organization_id: str = None, customer_id: str = None, external_customer_id: str = None, is_granted: bool = None) -> dict:
    """
    Fetches ALL benefit grants with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'benefit_id', 'customer_id', 'granted_at', 'is_granted'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/benefit-grants/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'benefit_id', 'customer_id', 'granted_at', 'is_granted', 'expires_at'])}

TOOLS.append({
    "name": "polarsh_list_benefit_grants",
    "description": polarsh_list_benefit_grants.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "external_customer_id": {"type": "string", "description": "Filter by external customer ID."},
            "is_granted": {"type": "boolean", "description": "Filter by whether the benefit is granted."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_benefit_grants",
    "description": polarsh_fetch_all_benefit_grants.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "external_customer_id": {"type": "string", "description": "Filter by external customer ID."},
            "is_granted": {"type": "boolean", "description": "Filter by whether the benefit is granted."},
        },
    },
})


# --- Benefits ---
async def polarsh_list_benefits(api_key: str, organization_id: str = None, type: str = None, id: str = None, exclude_id: str = None, query: str = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of benefits. Default limit is 10, max is 100.
    Use polarsh_fetch_all_benefits for the complete dataset.
    Returns fields like 'id', 'type', 'description', 'is_active', 'product_id'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/benefits/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'type', 'description', 'is_active', 'product_id', 'created_at', 'properties'])
    return response

async def polarsh_fetch_all_benefits(api_key: str, organization_id: str = None, type: str = None, id: str = None, exclude_id: str = None, query: str = None) -> dict:
    """
    Fetches ALL benefits with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'type', 'description', 'is_active', 'product_id'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/benefits/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'type', 'description', 'is_active', 'product_id', 'created_at', 'properties'])}

async def polarsh_create_benefit(api_key: str, type: str, description: str, organization_id: str, properties: dict = None, is_active: bool = True) -> dict:
    """
    Creates a new benefit.
    Returns fields like 'id', 'type', 'description', 'is_active', 'product_id'.
    """
    json_data = {
        "type": type,
        "description": description,
        "organization_id": organization_id,
        "properties": properties,
        "is_active": is_active,
    }
    response = await _make_request("POST", f"{BASE_URL}/v1/benefits/", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'type', 'description', 'is_active', 'product_id', 'created_at', 'properties'])

async def polarsh_get_benefit(api_key: str, id: str) -> dict:
    """
    Retrieves a specific benefit by its ID.
    Returns fields like 'id', 'type', 'description', 'is_active', 'product_id'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/benefits/{id}", api_key)
    return _compact_response(response, ['id', 'type', 'description', 'is_active', 'product_id', 'created_at', 'properties'])

async def polarsh_update_benefit(api_key: str, id: str, description: str = None, properties: dict = None, is_active: bool = None) -> dict:
    """
    Updates an existing benefit.
    Returns fields like 'id', 'type', 'description', 'is_active', 'product_id'.
    """
    json_data = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "id"]}
    response = await _make_request("PATCH", f"{BASE_URL}/v1/benefits/{id}", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'type', 'description', 'is_active', 'product_id', 'created_at', 'properties'])

async def polarsh_delete_benefit(api_key: str, id: str) -> dict:
    """
    Deletes a benefit by its ID.
    Returns a success message or error.
    """
    return await _make_request("DELETE", f"{BASE_URL}/v1/benefits/{id}", api_key)

TOOLS.append({
    "name": "polarsh_list_benefits",
    "description": polarsh_list_benefits.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "type": {"type": "string", "description": "Filter by benefit type."},
            "id": {"type": "string", "description": "Filter by benefit ID."},
            "exclude_id": {"type": "string", "description": "Exclude benefit by ID."},
            "query": {"type": "string", "description": "Search query for benefits."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_benefits",
    "description": polarsh_fetch_all_benefits.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "type": {"type": "string", "description": "Filter by benefit type."},
            "id": {"type": "string", "description": "Filter by benefit ID."},
            "exclude_id": {"type": "string", "description": "Exclude benefit by ID."},
            "query": {"type": "string", "description": "Search query for benefits."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_create_benefit",
    "description": polarsh_create_benefit.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "description": "The type of the benefit.", "enum": ["articles", "custom", "discord", "download", "github_repository", "github_repository_benefit", "ads", "product", "subscription", "physical_product", "shipping", "free_tier", "generate_articles", "generate_download_links", "generate_license_keys", "generate_custom_fields", "generate_product_codes", "generate_product_codes_for_external_products", "generate_product_codes_for_external_products_with_shipping", "generate_product_codes_for_external_products_with_shipping_and_custom_fields", "generate_product_codes_for_external_products_with_custom_fields"]},
            "description": {"type": "string", "description": "Description of the benefit."},
            "organization_id": {"type": "string", "description": "ID of the organization the benefit belongs to."},
            "properties": {"type": "object", "description": "Additional properties for the benefit (e.g., repository ID for GitHub benefit)."},
            "is_active": {"type": "boolean", "description": "Whether the benefit is active (default true)."},
        },
        "required": ["type", "description", "organization_id"],
    },
})
TOOLS.append({
    "name": "polarsh_get_benefit",
    "description": polarsh_get_benefit.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the benefit to retrieve."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_update_benefit",
    "description": polarsh_update_benefit.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the benefit to update."},
            "description": {"type": "string", "description": "New description for the benefit."},
            "properties": {"type": "object", "description": "Updated properties for the benefit."},
            "is_active": {"type": "boolean", "description": "Whether the benefit should be active."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_delete_benefit",
    "description": polarsh_delete_benefit.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the benefit to delete."},
        },
        "required": ["id"],
    },
})


# --- Checkout Links ---
async def polarsh_list_checkout_links(api_key: str, organization_id: str = None, product_id: str = None, page: int = 1, limit: int = 10, sorting: list = None) -> dict:
    """
    Returns ONE PAGE of checkout links. Default limit is 10, max is 100.
    Use polarsh_fetch_all_checkout_links for the complete dataset.
    Returns fields like 'id', 'url', 'product_id', 'organization_id', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/checkout-links/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'url', 'product_id', 'organization_id', 'created_at', 'active'])
    return response

async def polarsh_fetch_all_checkout_links(api_key: str, organization_id: str = None, product_id: str = None, sorting: list = None) -> dict:
    """
    Fetches ALL checkout links with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'url', 'product_id', 'organization_id', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/checkout-links/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'url', 'product_id', 'organization_id', 'created_at', 'active'])}

async def polarsh_create_checkout_link(api_key: str, product_id: str, organization_id: str, url: str = None, active: bool = True) -> dict:
    """
    Creates a new checkout link.
    Returns fields like 'id', 'url', 'product_id', 'organization_id', 'created_at'.
    """
    json_data = {
        "product_id": product_id,
        "organization_id": organization_id,
        "url": url,
        "active": active,
    }
    response = await _make_request("POST", f"{BASE_URL}/v1/checkout-links/", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'url', 'product_id', 'organization_id', 'created_at', 'active'])

async def polarsh_get_checkout_link(api_key: str, id: str) -> dict:
    """
    Retrieves a specific checkout link by its ID.
    Returns fields like 'id', 'url', 'product_id', 'organization_id', 'created_at'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/checkout-links/{id}", api_key)
    return _compact_response(response, ['id', 'url', 'product_id', 'organization_id', 'created_at', 'active'])

async def polarsh_update_checkout_link(api_key: str, id: str, url: str = None, active: bool = None) -> dict:
    """
    Updates an existing checkout link.
    Returns fields like 'id', 'url', 'product_id', 'organization_id', 'created_at'.
    """
    json_data = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "id"]}
    response = await _make_request("PATCH", f"{BASE_URL}/v1/checkout-links/{id}", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'url', 'product_id', 'organization_id', 'created_at', 'active'])

async def polarsh_delete_checkout_link(api_key: str, id: str) -> dict:
    """
    Deletes a checkout link by its ID.
    Returns a success message or error.
    """
    return await _make_request("DELETE", f"{BASE_URL}/v1/checkout-links/{id}", api_key)

TOOLS.append({
    "name": "polarsh_list_checkout_links",
    "description": polarsh_list_checkout_links.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
            "sorting": {"type": "array", "items": {"type": "string"}, "description": "Sorting criteria."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_checkout_links",
    "description": polarsh_fetch_all_checkout_links.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "sorting": {"type": "array", "items": {"type": "string"}, "description": "Sorting criteria."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_create_checkout_link",
    "description": polarsh_create_checkout_link.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "ID of the product this checkout link is for."},
            "organization_id": {"type": "string", "description": "ID of the organization the checkout link belongs to."},
            "url": {"type": "string", "description": "Custom URL for the checkout link."},
            "active": {"type": "boolean", "description": "Whether the checkout link is active (default true)."},
        },
        "required": ["product_id", "organization_id"],
    },
})
TOOLS.append({
    "name": "polarsh_get_checkout_link",
    "description": polarsh_get_checkout_link.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the checkout link to retrieve."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_update_checkout_link",
    "description": polarsh_update_checkout_link.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the checkout link to update."},
            "url": {"type": "string", "description": "New custom URL for the checkout link."},
            "active": {"type": "boolean", "description": "Whether the checkout link should be active."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_delete_checkout_link",
    "description": polarsh_delete_checkout_link.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the checkout link to delete."},
        },
        "required": ["id"],
    },
})


# --- Checkouts ---
async def polarsh_list_checkout_sessions(api_key: str, organization_id: str = None, product_id: str = None, customer_id: str = None, external_customer_id: str = None, status: str = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of checkout sessions. Default limit is 10, max is 100.
    Use polarsh_fetch_all_checkout_sessions for the complete dataset.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/checkouts/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at', 'url'])
    return response

async def polarsh_fetch_all_checkout_sessions(api_key: str, organization_id: str = None, product_id: str = None, customer_id: str = None, external_customer_id: str = None, status: str = None) -> dict:
    """
    Fetches ALL checkout sessions with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/checkouts/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at', 'url'])}

async def polarsh_create_checkout_session(api_key: str, product_id: str, organization_id: str, customer_id: str = None, external_customer_id: str = None, success_url: str = None, cancel_url: str = None, metadata: dict = None) -> dict:
    """
    Creates a new checkout session.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at', 'url'.
    """
    json_data = {
        "product_id": product_id,
        "organization_id": organization_id,
        "customer_id": customer_id,
        "external_customer_id": external_customer_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": metadata,
    }
    json_data = {k: v for k, v in json_data.items() if v is not None}
    response = await _make_request("POST", f"{BASE_URL}/v1/checkouts/", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at', 'url', 'client_secret'])

async def polarsh_get_checkout_session(api_key: str, id: str) -> dict:
    """
    Retrieves a specific checkout session by its ID.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at', 'url'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/checkouts/{id}", api_key)
    return _compact_response(response, ['id', 'status', 'customer_id', 'product_id', 'amount_total', 'created_at', 'url', 'client_secret'])

TOOLS.append({
    "name": "polarsh_list_checkout_sessions",
    "description": polarsh_list_checkout_sessions.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "external_customer_id": {"type": "string", "description": "Filter by external customer ID."},
            "status": {"type": "string", "description": "Filter by checkout session status."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_checkout_sessions",
    "description": polarsh_fetch_all_checkout_sessions.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "external_customer_id": {"type": "string", "description": "Filter by external customer ID."},
            "status": {"type": "string", "description": "Filter by checkout session status."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_create_checkout_session",
    "description": polarsh_create_checkout_session.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "ID of the product to checkout."},
            "organization_id": {"type": "string", "description": "ID of the organization the checkout belongs to."},
            "customer_id": {"type": "string", "description": "ID of an existing customer."},
            "external_customer_id": {"type": "string", "description": "External ID of an existing customer."},
            "success_url": {"type": "string", "description": "URL to redirect to after successful checkout."},
            "cancel_url": {"type": "string", "description": "URL to redirect to if checkout is cancelled."},
            "metadata": {"type": "object", "description": "Arbitrary metadata for the checkout session."},
        },
        "required": ["product_id", "organization_id"],
    },
})
TOOLS.append({
    "name": "polarsh_get_checkout_session",
    "description": polarsh_get_checkout_session.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the checkout session to retrieve."},
        },
        "required": ["id"],
    },
})


# --- Customers ---
async def polarsh_list_customers(api_key: str, organization_id: str = None, email: str = None, query: str = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of customers. Default limit is 10, max is 100.
    Use polarsh_fetch_all_customers for the complete dataset.
    Returns fields like 'id', 'email', 'name', 'external_id', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/customers/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'email', 'name', 'external_id', 'created_at'])
    return response

async def polarsh_fetch_all_customers(api_key: str, organization_id: str = None, email: str = None, query: str = None) -> dict:
    """
    Fetches ALL customers with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'email', 'name', 'external_id', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/customers/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'email', 'name', 'external_id', 'created_at'])}

async def polarsh_create_customer(api_key: str, organization_id: str, email: str, name: str = None, external_id: str = None) -> dict:
    """
    Creates a new customer.
    Returns fields like 'id', 'email', 'name', 'external_id', 'created_at'.
    """
    json_data = {
        "organization_id": organization_id,
        "email": email,
        "name": name,
        "external_id": external_id,
    }
    json_data = {k: v for k, v in json_data.items() if v is not None}
    response = await _make_request("POST", f"{BASE_URL}/v1/customers/", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'email', 'name', 'external_id', 'created_at'])

async def polarsh_get_customer(api_key: str, id: str) -> dict:
    """
    Retrieves a specific customer by their ID.
    Returns fields like 'id', 'email', 'name', 'external_id', 'created_at'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/customers/{id}", api_key)
    return _compact_response(response, ['id', 'email', 'name', 'external_id', 'created_at'])

async def polarsh_update_customer(api_key: str, id: str, email: str = None, name: str = None, external_id: str = None) -> dict:
    """
    Updates an existing customer.
    Returns fields like 'id', 'email', 'name', 'external_id', 'created_at'.
    """
    json_data = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "id"]}
    response = await _make_request("PATCH", f"{BASE_URL}/v1/customers/{id}", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'email', 'name', 'external_id', 'created_at'])

async def polarsh_delete_customer(api_key: str, id: str, anonymize: bool = False) -> dict:
    """
    Deletes a customer by their ID. Can optionally anonymize customer data.
    Returns a success message or error.
    """
    params = {"anonymize": anonymize} if anonymize else None
    return await _make_request("DELETE", f"{BASE_URL}/v1/customers/{id}", api_key, params=params)

TOOLS.append({
    "name": "polarsh_list_customers",
    "description": polarsh_list_customers.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "email": {"type": "string", "description": "Filter by customer email."},
            "query": {"type": "string", "description": "Search query for customers."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_customers",
    "description": polarsh_fetch_all_customers.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "email": {"type": "string", "description": "Filter by customer email."},
            "query": {"type": "string", "description": "Search query for customers."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_create_customer",
    "description": polarsh_create_customer.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "ID of the organization the customer belongs to."},
            "email": {"type": "string", "format": "email", "description": "Email address of the customer."},
            "name": {"type": "string", "description": "Name of the customer."},
            "external_id": {"type": "string", "description": "An optional external ID for the customer."},
        },
        "required": ["organization_id", "email"],
    },
})
TOOLS.append({
    "name": "polarsh_get_customer",
    "description": polarsh_get_customer.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the customer to retrieve."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_update_customer",
    "description": polarsh_update_customer.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the customer to update."},
            "email": {"type": "string", "format": "email", "description": "New email address for the customer."},
            "name": {"type": "string", "description": "New name for the customer."},
            "external_id": {"type": "string", "description": "New external ID for the customer."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_delete_customer",
    "description": polarsh_delete_customer.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the customer to delete."},
            "anonymize": {"type": "boolean", "description": "If true, anonymize customer data instead of full deletion (default false)."},
        },
        "required": ["id"],
    },
})


# --- Orders ---
async def polarsh_list_orders(api_key: str, organization_id: str = None, product_id: str = None, product_billing_type: str = None, discount_id: str = None, customer_id: str = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of orders. Default limit is 10, max is 100.
    Use polarsh_fetch_all_orders for the complete dataset.
    Returns fields like 'id', 'amount_total', 'currency', 'status', 'customer_id', 'product_id', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/orders/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'amount_total', 'currency', 'status', 'customer_id', 'product_id', 'created_at', 'tax_amount', 'discount_amount'])
    return response

async def polarsh_fetch_all_orders(api_key: str, organization_id: str = None, product_id: str = None, product_billing_type: str = None, discount_id: str = None, customer_id: str = None) -> dict:
    """
    Fetches ALL orders with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'amount_total', 'currency', 'status', 'customer_id', 'product_id', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/orders/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'amount_total', 'currency', 'status', 'customer_id', 'product_id', 'created_at', 'tax_amount', 'discount_amount'])}

async def polarsh_get_order(api_key: str, id: str) -> dict:
    """
    Retrieves a specific order by its ID.
    Returns fields like 'id', 'amount_total', 'currency', 'status', 'customer_id', 'product_id', 'created_at'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/orders/{id}", api_key)
    return _compact_response(response, ['id', 'amount_total', 'currency', 'status', 'customer_id', 'product_id', 'created_at', 'tax_amount', 'discount_amount', 'invoice_id'])

async def polarsh_generate_order_invoice(api_key: str, id: str) -> dict:
    """
    Generates an invoice for a specific order.
    Returns the invoice details including 'id', 'url', 'status'.
    """
    response = await _make_request("POST", f"{BASE_URL}/v1/orders/{id}/invoice", api_key)
    return _compact_response(response, ['id', 'url', 'status', 'created_at'])

async def polarsh_get_order_invoice(api_key: str, id: str) -> dict:
    """
    Retrieves the invoice for a specific order.
    Returns the invoice details including 'id', 'url', 'status'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/orders/{id}/invoice", api_key)
    return _compact_response(response, ['id', 'url', 'status', 'created_at'])

TOOLS.append({
    "name": "polarsh_list_orders",
    "description": polarsh_list_orders.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "product_billing_type": {"type": "string", "description": "Filter by product billing type."},
            "discount_id": {"type": "string", "description": "Filter by discount ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_orders",
    "description": polarsh_fetch_all_orders.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "product_billing_type": {"type": "string", "description": "Filter by product billing type."},
            "discount_id": {"type": "string", "description": "Filter by discount ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_get_order",
    "description": polarsh_get_order.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the order to retrieve."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_generate_order_invoice",
    "description": polarsh_generate_order_invoice.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the order to generate an invoice for."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_get_order_invoice",
    "description": polarsh_get_order_invoice.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the order to retrieve the invoice for."},
        },
        "required": ["id"],
    },
})


# --- Products ---
async def polarsh_list_products(api_key: str, id: str = None, organization_id: str = None, query: str = None, is_archived: bool = None, is_recurring: bool = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of products. Default limit is 10, max is 100.
    Use polarsh_fetch_all_products for the complete dataset.
    Returns fields like 'id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/products/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at', 'organization_id', 'benefits'])
    return response

async def polarsh_fetch_all_products(api_key: str, id: str = None, organization_id: str = None, query: str = None, is_archived: bool = None, is_recurring: bool = None) -> dict:
    """
    Fetches ALL products with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/products/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at', 'organization_id', 'benefits'])}

async def polarsh_create_product(api_key: str, name: str, organization_id: str, type: str, price_amount: int, price_currency: str, description: str = None, is_archived: bool = False, benefits: list = None) -> dict:
    """
    Creates a new product.
    Returns fields like 'id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at'.
    """
    json_data = {
        "name": name,
        "organization_id": organization_id,
        "type": type,
        "price_amount": price_amount,
        "price_currency": price_currency,
        "description": description,
        "is_archived": is_archived,
        "benefits": benefits,
    }
    json_data = {k: v for k, v in json_data.items() if v is not None}
    response = await _make_request("POST", f"{BASE_URL}/v1/products/", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at', 'organization_id', 'benefits'])

async def polarsh_get_product(api_key: str, id: str) -> dict:
    """
    Retrieves a specific product by its ID.
    Returns fields like 'id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/products/{id}", api_key)
    return _compact_response(response, ['id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at', 'organization_id', 'benefits'])

async def polarsh_update_product(api_key: str, id: str, name: str = None, description: str = None, price_amount: int = None, price_currency: str = None, is_archived: bool = None) -> dict:
    """
    Updates an existing product.
    Returns fields like 'id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at'.
    """
    json_data = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "id"]}
    response = await _make_request("PATCH", f"{BASE_URL}/v1/products/{id}", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at', 'organization_id', 'benefits'])

async def polarsh_update_product_benefits(api_key: str, id: str, benefits: list) -> dict:
    """
    Updates the benefits associated with a product.
    Returns fields like 'id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at'.
    """
    json_data = {"benefits": benefits}
    response = await _make_request("POST", f"{BASE_URL}/v1/products/{id}/benefits", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'name', 'type', 'price_amount', 'currency', 'is_archived', 'created_at', 'organization_id', 'benefits'])

TOOLS.append({
    "name": "polarsh_list_products",
    "description": polarsh_list_products.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Filter by product ID."},
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "query": {"type": "string", "description": "Search query for products."},
            "is_archived": {"type": "boolean", "description": "Filter by archived status."},
            "is_recurring": {"type": "boolean", "description": "Filter by recurring status."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_products",
    "description": polarsh_fetch_all_products.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Filter by product ID."},
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "query": {"type": "string", "description": "Search query for products."},
            "is_archived": {"type": "boolean", "description": "Filter by archived status."},
            "is_recurring": {"type": "boolean", "description": "Filter by recurring status."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_create_product",
    "description": polarsh_create_product.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the product."},
            "organization_id": {"type": "string", "description": "ID of the organization the product belongs to."},
            "type": {"type": "string", "description": "Type of the product (e.g., 'subscription', 'one_time')."},
            "price_amount": {"type": "integer", "description": "Price amount in cents."},
            "price_currency": {"type": "string", "description": "Currency of the price (e.g., 'usd')."},
            "description": {"type": "string", "description": "Description of the product."},
            "is_archived": {"type": "boolean", "description": "Whether the product is archived (default false)."},
            "benefits": {"type": "array", "items": {"type": "string"}, "description": "List of benefit IDs associated with this product."},
        },
        "required": ["name", "organization_id", "type", "price_amount", "price_currency"],
    },
})
TOOLS.append({
    "name": "polarsh_get_product",
    "description": polarsh_get_product.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the product to retrieve."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_update_product",
    "description": polarsh_update_product.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the product to update."},
            "name": {"type": "string", "description": "New name for the product."},
            "description": {"type": "string", "description": "New description for the product."},
            "price_amount": {"type": "integer", "description": "New price amount in cents."},
            "price_currency": {"type": "string", "description": "New currency of the price."},
            "is_archived": {"type": "boolean", "description": "Whether the product should be archived."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_update_product_benefits",
    "description": polarsh_update_product_benefits.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the product to update benefits for."},
            "benefits": {"type": "array", "items": {"type": "string"}, "description": "List of benefit IDs to associate with this product. This replaces existing benefits."},
        },
        "required": ["id", "benefits"],
    },
})


# --- Subscriptions ---
async def polarsh_list_subscriptions(api_key: str, organization_id: str = None, product_id: str = None, customer_id: str = None, external_customer_id: str = None, discount_id: str = None, page: int = 1, limit: int = 10) -> dict:
    """
    Returns ONE PAGE of subscriptions. Default limit is 10, max is 100.
    Use polarsh_fetch_all_subscriptions for the complete dataset.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "limit"]}
    params["limit"] = limit
    response = await _make_request("GET", f"{BASE_URL}/v1/subscriptions/", api_key, params=params)
    if "items" in response:
        response["items"] = _compact_list_response(response["items"], ['id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end', 'created_at'])
    return response

async def polarsh_fetch_all_subscriptions(api_key: str, organization_id: str = None, product_id: str = None, customer_id: str = None, external_customer_id: str = None, discount_id: str = None) -> dict:
    """
    Fetches ALL subscriptions with automatic pagination. Use for bulk data exports.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end'.
    """
    params = {k: v for k, v in locals().items() if v is not None and k != "api_key"}
    records, error = await _fetch_all_pages(f"{BASE_URL}/v1/subscriptions/", api_key, params, "items")
    if error:
        return error
    return {"items": _compact_list_response(records, ['id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end', 'created_at'])}

async def polarsh_create_subscription(api_key: str, product_id: str, customer_id: str, organization_id: str, price_id: str = None, discount_id: str = None, trial_end_at: str = None, cancel_at_period_end: bool = False) -> dict:
    """
    Creates a new subscription.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end'.
    """
    json_data = {
        "product_id": product_id,
        "customer_id": customer_id,
        "organization_id": organization_id,
        "price_id": price_id,
        "discount_id": discount_id,
        "trial_end_at": trial_end_at,
        "cancel_at_period_end": cancel_at_period_end,
    }
    json_data = {k: v for k, v in json_data.items() if v is not None}
    response = await _make_request("POST", f"{BASE_URL}/v1/subscriptions/", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end', 'created_at'])

async def polarsh_get_subscription(api_key: str, id: str) -> dict:
    """
    Retrieves a specific subscription by its ID.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end'.
    """
    response = await _make_request("GET", f"{BASE_URL}/v1/subscriptions/{id}", api_key)
    return _compact_response(response, ['id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end', 'created_at'])

async def polarsh_update_subscription(api_key: str, id: str, cancel_at_period_end: bool = None, price_id: str = None) -> dict:
    """
    Updates an existing subscription.
    Returns fields like 'id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end'.
    """
    json_data = {k: v for k, v in locals().items() if v is not None and k not in ["api_key", "id"]}
    response = await _make_request("PATCH", f"{BASE_URL}/v1/subscriptions/{id}", api_key, json_data=json_data)
    return _compact_response(response, ['id', 'status', 'customer_id', 'product_id', 'current_period_start', 'current_period_end', 'cancel_at_period_end', 'created_at'])

async def polarsh_revoke_subscription(api_key: str, id: str) -> dict:
    """
    Revokes (cancels immediately) a subscription by its ID.
    Returns a success message or error.
    """
    return await _make_request("DELETE", f"{BASE_URL}/v1/subscriptions/{id}", api_key)

TOOLS.append({
    "name": "polarsh_list_subscriptions",
    "description": polarsh_list_subscriptions.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "external_customer_id": {"type": "string", "description": "Filter by external customer ID."},
            "discount_id": {"type": "string", "description": "Filter by discount ID."},
            "page": {"type": "integer", "description": "Page number to retrieve (default 1)."},
            "limit": {"type": "integer", "description": "Number of items per page (default 10, max 100)."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_fetch_all_subscriptions",
    "description": polarsh_fetch_all_subscriptions.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "organization_id": {"type": "string", "description": "Filter by organization ID."},
            "product_id": {"type": "string", "description": "Filter by product ID."},
            "customer_id": {"type": "string", "description": "Filter by customer ID."},
            "external_customer_id": {"type": "string", "description": "Filter by external customer ID."},
            "discount_id": {"type": "string", "description": "Filter by discount ID."},
        },
    },
})
TOOLS.append({
    "name": "polarsh_create_subscription",
    "description": polarsh_create_subscription.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "ID of the product to subscribe to."},
            "customer_id": {"type": "string", "description": "ID of the customer subscribing."},
            "organization_id": {"type": "string", "description": "ID of the organization the subscription belongs to."},
            "price_id": {"type": "string", "description": "ID of the specific price to use for the subscription."},
            "discount_id": {"type": "string", "description": "ID of a discount to apply."},
            "trial_end_at": {"type": "string", "format": "date-time", "description": "Timestamp when the trial period ends."},
            "cancel_at_period_end": {"type": "boolean", "description": "Whether to cancel the subscription at the end of the current period (default false)."},
        },
        "required": ["product_id", "customer_id", "organization_id"],
    },
})
TOOLS.append({
    "name": "polarsh_get_subscription",
    "description": polarsh_get_subscription.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the subscription to retrieve."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_update_subscription",
    "description": polarsh_update_subscription.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the subscription to update."},
            "cancel_at_period_end": {"type": "boolean", "description": "Whether to cancel at period end."},
            "price_id": {"type": "string", "description": "ID of the new price to switch to."},
        },
        "required": ["id"],
    },
})
TOOLS.append({
    "name": "polarsh_revoke_subscription",
    "description": polarsh_revoke_subscription.__doc__,
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The ID of the subscription to revoke."},
        },
        "required": ["id"],
    },
})


_TOOL_DISPATCH = {t["name"]: t["name"] for t in TOOLS}


async def execute(tool_name: str, args: dict, api_key: str) -> dict:
    """Dispatch a tool call to the appropriate function."""
    fn = globals().get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}
    return await fn(api_key=api_key, **args)