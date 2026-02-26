"""
A production-ready Python wrapper module for the Clerk API.

This wrapper provides an asynchronous interface to interact with the Clerk backend API,
covering major business categories such as user management, organization management,
session management, and webhook management. It includes tools for CRUD operations,
listing resources with pagination, and fetching all records for paginated endpoints.

Authentication is handled via a bearer token (API key).
All HTTP requests are made using `httpx` with robust error handling,
including retries with exponential backoff for transient errors (429, 5xx).
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Union

import httpx

# API Base URL
BASE_URL = "https://api.clerk.com/v1"

# Common HTTP client with default timeout and follow_redirects
_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True)


async def _make_request(
    method: str,
    url: str,
    api_key: str,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    retries: int = 3,
    backoff_factor: float = 1.0,
) -> Dict[str, Any]:
    """
    Helper function to make an HTTP request with retry logic and exponential backoff.
    Handles 429 (Too Many Requests) and 5xx (Server Error) status codes.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(retries + 1):
        try:
            response = await _client.request(
                method, url, headers=headers, json=json_data, params=params
            )
            response.raise_for_status()  # Raise an exception for 4xx or 5xx responses
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                retry_after = 0
                if "Retry-After" in e.response.headers:
                    try:
                        retry_after = int(e.response.headers["Retry-After"])
                    except ValueError:
                        pass  # Ignore if not an integer

                wait_time = max(
                    retry_after, backoff_factor * (2**attempt)
                )  # Exponential backoff
                await asyncio.sleep(wait_time)
                continue
            elif e.response.status_code == 404:
                return {"error": f"Resource not found: {url}", "status_code": 404}
            else:
                try:
                    error_detail = e.response.json()
                except json.JSONDecodeError:
                    error_detail = {"message": e.response.text}
                return {
                    "error": f"API error: {e.response.status_code} - {error_detail}",
                    "status_code": e.response.status_code,
                    "details": error_detail,
                }
        except httpx.RequestError as e:
            if attempt < retries:
                await asyncio.sleep(backoff_factor * (2**attempt))
                continue
            return {"error": f"Network or request error: {e}", "status_code": 0}
    return {"error": "Max retries exceeded", "status_code": 0}


async def _paginate_all(
    url_path: str,
    api_key: str,
    params: Optional[Dict[str, Any]] = None,
    page_size: int = 50,
    sleep_between_pages: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Helper function to paginate through all records of an endpoint.
    """
    all_records = []
    offset = 0
    while True:
        current_params = (params or {}).copy()
        current_params["limit"] = page_size
        current_params["offset"] = offset

        response = await _make_request(
            "GET", f"{BASE_URL}{url_path}", api_key, params=current_params
        )

        if "error" in response:
            return response  # Propagate error

        if not isinstance(response, list):
            # Clerk API often returns a list directly, but sometimes an object with 'data'
            if isinstance(response, dict) and "data" in response:
                current_page_records = response.get("data", [])
            else:
                # If it's not a list and not an object with 'data', something is wrong
                return {"error": "Unexpected API response format during pagination."}
        else:
            current_page_records = response

        if not current_page_records:
            break

        all_records.extend(current_page_records)

        if len(current_page_records) < page_size:
            break  # Last page

        offset += page_size
        await asyncio.sleep(sleep_between_pages)  # Be kind to the API

    return all_records


# --- User Management ---


async def clerk_list_users(
    api_key: str,
    limit: int = 10,
    offset: int = 0,
    query: Optional[str] = None,
    email_address: Optional[str] = None,
    phone_number: Optional[str] = None,
    external_id: Optional[str] = None,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    order_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of users.
    """
    params = {
        "limit": limit,
        "offset": offset,
    }
    if query:
        params["query"] = query
    if email_address:
        params["email_address"] = email_address
    if phone_number:
        params["phone_number"] = phone_number
    if external_id:
        params["external_id"] = external_id
    if username:
        params["username"] = username
    if user_id:
        params["user_id"] = user_id
    if order_by:
        params["order_by"] = order_by
    return await _make_request("GET", f"{BASE_URL}/users", api_key, params=params)


async def clerk_fetch_all_users(
    api_key: str,
    query: Optional[str] = None,
    email_address: Optional[str] = None,
    phone_number: Optional[str] = None,
    external_id: Optional[str] = None,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    order_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL users by auto-paginating through the list users endpoint.
    """
    params = {}
    if query:
        params["query"] = query
    if email_address:
        params["email_address"] = email_address
    if phone_number:
        params["phone_number"] = phone_number
    if external_id:
        params["external_id"] = external_id
    if username:
        params["username"] = username
    if user_id:
        params["user_id"] = user_id
    if order_by:
        params["order_by"] = order_by
    return await _paginate_all(f"/users", api_key, params=params)


async def clerk_get_user(api_key: str, user_id: str) -> Dict[str, Any]:
    """
    Retrieves a user by their ID.
    """
    return await _make_request("GET", f"{BASE_URL}/users/{user_id}", api_key)


async def clerk_create_user(
    api_key: str,
    email_address: Optional[List[str]] = None,
    phone_number: Optional[List[str]] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    external_id: Optional[str] = None,
    public_metadata: Optional[Dict[str, Any]] = None,
    private_metadata: Optional[Dict[str, Any]] = None,
    unsafe_metadata: Optional[Dict[str, Any]] = None,
    skip_password_checks: Optional[bool] = None,
    skip_pwned_password_checks: Optional[bool] = None,
    send_invitation: Optional[bool] = None,
    invite_redirect_url: Optional[str] = None,
    send_email_address_verification: Optional[bool] = None,
    send_phone_number_verification: Optional[bool] = None,
    totp_enabled: Optional[bool] = None,
    backup_code_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Creates a new user.
    """
    payload = {}
    if email_address is not None:
        payload["email_address"] = email_address
    if phone_number is not None:
        payload["phone_number"] = phone_number
    if username is not None:
        payload["username"] = username
    if password is not None:
        payload["password"] = password
    if first_name is not None:
        payload["first_name"] = first_name
    if last_name is not None:
        payload["last_name"] = last_name
    if external_id is not None:
        payload["external_id"] = external_id
    if public_metadata is not None:
        payload["public_metadata"] = public_metadata
    if private_metadata is not None:
        payload["private_metadata"] = private_metadata
    if unsafe_metadata is not None:
        payload["unsafe_metadata"] = unsafe_metadata
    if skip_password_checks is not None:
        payload["skip_password_checks"] = skip_password_checks
    if skip_pwned_password_checks is not None:
        payload["skip_pwned_password_checks"] = skip_pwned_password_checks
    if send_invitation is not None:
        payload["send_invitation"] = send_invitation
    if invite_redirect_url is not None:
        payload["invite_redirect_url"] = invite_redirect_url
    if send_email_address_verification is not None:
        payload["send_email_address_verification"] = send_email_address_verification
    if send_phone_number_verification is not None:
        payload["send_phone_number_verification"] = send_phone_number_verification
    if totp_enabled is not None:
        payload["totp_enabled"] = totp_enabled
    if backup_code_enabled is not None:
        payload["backup_code_enabled"] = backup_code_enabled

    return await _make_request("POST", f"{BASE_URL}/users", api_key, json_data=payload)


async def clerk_update_user(
    api_key: str,
    user_id: str,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    public_metadata: Optional[Dict[str, Any]] = None,
    private_metadata: Optional[Dict[str, Any]] = None,
    unsafe_metadata: Optional[Dict[str, Any]] = None,
    primary_email_address_id: Optional[str] = None,
    primary_phone_number_id: Optional[str] = None,
    primary_web3_wallet_id: Optional[str] = None,
    profile_image_id: Optional[str] = None,
    password: Optional[str] = None,
    skip_password_checks: Optional[bool] = None,
    skip_pwned_password_checks: Optional[bool] = None,
    totp_enabled: Optional[bool] = None,
    backup_code_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Updates an existing user.
    """
    payload = {}
    if first_name is not None:
        payload["first_name"] = first_name
    if last_name is not None:
        payload["last_name"] = last_name
    if username is not None:
        payload["username"] = username
    if public_metadata is not None:
        payload["public_metadata"] = public_metadata
    if private_metadata is not None:
        payload["private_metadata"] = private_metadata
    if unsafe_metadata is not None:
        payload["unsafe_metadata"] = unsafe_metadata
    if primary_email_address_id is not None:
        payload["primary_email_address_id"] = primary_email_address_id
    if primary_phone_number_id is not None:
        payload["primary_phone_number_id"] = primary_phone_number_id
    if primary_web3_wallet_id is not None:
        payload["primary_web3_wallet_id"] = primary_web3_wallet_id
    if profile_image_id is not None:
        payload["profile_image_id"] = profile_image_id
    if password is not None:
        payload["password"] = password
    if skip_password_checks is not None:
        payload["skip_password_checks"] = skip_password_checks
    if skip_pwned_password_checks is not None:
        payload["skip_pwned_password_checks"] = skip_pwned_password_checks
    if totp_enabled is not None:
        payload["totp_enabled"] = totp_enabled
    if backup_code_enabled is not None:
        payload["backup_code_enabled"] = backup_code_enabled

    return await _make_request(
        "PATCH", f"{BASE_URL}/users/{user_id}", api_key, json_data=payload
    )


async def clerk_delete_user(api_key: str, user_id: str) -> Dict[str, Any]:
    """
    Deletes a user by their ID.
    """
    return await _make_request("DELETE", f"{BASE_URL}/users/{user_id}", api_key)


# --- Organization Management ---


async def clerk_list_organizations(
    api_key: str,
    limit: int = 10,
    offset: int = 0,
    query: Optional[str] = None,
    user_id: Optional[str] = None,
    order_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of organizations.
    """
    params = {
        "limit": limit,
        "offset": offset,
    }
    if query:
        params["query"] = query
    if user_id:
        params["user_id"] = user_id
    if order_by:
        params["order_by"] = order_by
    return await _make_request("GET", f"{BASE_URL}/organizations", api_key, params=params)


async def clerk_fetch_all_organizations(
    api_key: str,
    query: Optional[str] = None,
    user_id: Optional[str] = None,
    order_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL organizations by auto-paginating through the list organizations endpoint.
    """
    params = {}
    if query:
        params["query"] = query
    if user_id:
        params["user_id"] = user_id
    if order_by:
        params["order_by"] = order_by
    return await _paginate_all(f"/organizations", api_key, params=params)


async def clerk_get_organization(api_key: str, organization_id: str) -> Dict[str, Any]:
    """
    Retrieves an organization by its ID.
    """
    return await _make_request(
        "GET", f"{BASE_URL}/organizations/{organization_id}", api_key
    )


async def clerk_create_organization(
    api_key: str,
    name: str,
    slug: Optional[str] = None,
    created_by: Optional[str] = None,
    public_metadata: Optional[Dict[str, Any]] = None,
    private_metadata: Optional[Dict[str, Any]] = None,
    unsafe_metadata: Optional[Dict[str, Any]] = None,
    max_allowed_memberships: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Creates a new organization.
    """
    payload = {"name": name}
    if slug is not None:
        payload["slug"] = slug
    if created_by is not None:
        payload["created_by"] = created_by
    if public_metadata is not None:
        payload["public_metadata"] = public_metadata
    if private_metadata is not None:
        payload["private_metadata"] = private_metadata
    if unsafe_metadata is not None:
        payload["unsafe_metadata"] = unsafe_metadata
    if max_allowed_memberships is not None:
        payload["max_allowed_memberships"] = max_allowed_memberships
    return await _make_request(
        "POST", f"{BASE_URL}/organizations", api_key, json_data=payload
    )


async def clerk_update_organization(
    api_key: str,
    organization_id: str,
    name: Optional[str] = None,
    slug: Optional[str] = None,
    public_metadata: Optional[Dict[str, Any]] = None,
    private_metadata: Optional[Dict[str, Any]] = None,
    unsafe_metadata: Optional[Dict[str, Any]] = None,
    max_allowed_memberships: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Updates an existing organization.
    """
    payload = {}
    if name is not None:
        payload["name"] = name
    if slug is not None:
        payload["slug"] = slug
    if public_metadata is not None:
        payload["public_metadata"] = public_metadata
    if private_metadata is not None:
        payload["private_metadata"] = private_metadata
    if unsafe_metadata is not None:
        payload["unsafe_metadata"] = unsafe_metadata
    if max_allowed_memberships is not None:
        payload["max_allowed_memberships"] = max_allowed_memberships
    return await _make_request(
        "PATCH", f"{BASE_URL}/organizations/{organization_id}", api_key, json_data=payload
    )


async def clerk_delete_organization(api_key: str, organization_id: str) -> Dict[str, Any]:
    """
    Deletes an organization by its ID.
    """
    return await _make_request(
        "DELETE", f"{BASE_URL}/organizations/{organization_id}", api_key
    )


# --- Organization Memberships ---


async def clerk_list_organization_memberships(
    api_key: str,
    organization_id: str,
    limit: int = 10,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of memberships for a given organization.
    """
    params = {"limit": limit, "offset": offset}
    return await _make_request(
        "GET",
        f"{BASE_URL}/organizations/{organization_id}/memberships",
        api_key,
        params=params,
    )


async def clerk_fetch_all_organization_memberships(
    api_key: str,
    organization_id: str,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL memberships for a given organization by auto-paginating.
    """
    return await _paginate_all(
        f"/organizations/{organization_id}/memberships", api_key
    )


async def clerk_create_organization_membership(
    api_key: str, organization_id: str, user_id: str, role: str
) -> Dict[str, Any]:
    """
    Creates a new organization membership.
    """
    payload = {"user_id": user_id, "role": role}
    return await _make_request(
        "POST",
        f"{BASE_URL}/organizations/{organization_id}/memberships",
        api_key,
        json_data=payload,
    )


async def clerk_update_organization_membership(
    api_key: str, organization_id: str, user_id: str, role: str
) -> Dict[str, Any]:
    """
    Updates an organization membership's role.
    """
    payload = {"role": role}
    return await _make_request(
        "PATCH",
        f"{BASE_URL}/organizations/{organization_id}/memberships/{user_id}",
        api_key,
        json_data=payload,
    )


async def clerk_delete_organization_membership(
    api_key: str, organization_id: str, user_id: str
) -> Dict[str, Any]:
    """
    Deletes an organization membership.
    """
    return await _make_request(
        "DELETE",
        f"{BASE_URL}/organizations/{organization_id}/memberships/{user_id}",
        api_key,
    )


# --- Session Management ---


async def clerk_list_sessions(
    api_key: str,
    limit: int = 10,
    offset: int = 0,
    user_id: Optional[str] = None,
    client_id: Optional[str] = None,
    status: Optional[str] = None,
    order_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of sessions.
    """
    params = {
        "limit": limit,
        "offset": offset,
    }
    if user_id:
        params["user_id"] = user_id
    if client_id:
        params["client_id"] = client_id
    if status:
        params["status"] = status
    if order_by:
        params["order_by"] = order_by
    return await _make_request("GET", f"{BASE_URL}/sessions", api_key, params=params)


async def clerk_fetch_all_sessions(
    api_key: str,
    user_id: Optional[str] = None,
    client_id: Optional[str] = None,
    status: Optional[str] = None,
    order_by: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL sessions by auto-paginating through the list sessions endpoint.
    """
    params = {}
    if user_id:
        params["user_id"] = user_id
    if client_id:
        params["client_id"] = client_id
    if status:
        params["status"] = status
    if order_by:
        params["order_by"] = order_by
    return await _paginate_all(f"/sessions", api_key, params=params)


async def clerk_get_session(api_key: str, session_id: str) -> Dict[str, Any]:
    """
    Retrieves a session by its ID.
    """
    return await _make_request("GET", f"{BASE_URL}/sessions/{session_id}", api_key)


async def clerk_revoke_session(api_key: str, session_id: str) -> Dict[str, Any]:
    """
    Revokes a session by its ID.
    """
    return await _make_request(
        "POST", f"{BASE_URL}/sessions/{session_id}/revoke", api_key
    )


# --- Webhook Management (Clerk does not expose direct CRUD for webhooks via API,
#     but rather for webhook test events and delivery attempts.
#     We'll focus on test events as they are API-manageable.) ---


async def clerk_create_webhook_test_event(
    api_key: str,
    webhook_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Creates a new webhook test event for a given webhook.
    """
    json_data = {"event_type": event_type, "payload": payload}
    return await _make_request(
        "POST", f"{BASE_URL}/webhooks/{webhook_id}/test", api_key, json_data=json_data
    )


async def clerk_list_webhook_delivery_attempts(
    api_key: str,
    webhook_id: str,
    limit: int = 10,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of delivery attempts for a given webhook.
    """
    params = {"limit": limit, "offset": offset}
    return await _make_request(
        "GET",
        f"{BASE_URL}/webhooks/{webhook_id}/delivery_attempts",
        api_key,
        params=params,
    )


async def clerk_fetch_all_webhook_delivery_attempts(
    api_key: str,
    webhook_id: str,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL delivery attempts for a given webhook by auto-paginating.
    """
    return await _paginate_all(
        f"/webhooks/{webhook_id}/delivery_attempts", api_key
    )


# --- Email Management (Email Addresses) ---


async def clerk_list_email_addresses(
    api_key: str,
    limit: int = 10,
    offset: int = 0,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of email addresses.
    """
    params = {"limit": limit, "offset": offset}
    if user_id:
        params["user_id"] = user_id
    return await _make_request(
        "GET", f"{BASE_URL}/email_addresses", api_key, params=params
    )


async def clerk_fetch_all_email_addresses(
    api_key: str,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL email addresses by auto-paginating.
    """
    params = {}
    if user_id:
        params["user_id"] = user_id
    return await _paginate_all(f"/email_addresses", api_key, params=params)


async def clerk_get_email_address(api_key: str, email_address_id: str) -> Dict[str, Any]:
    """
    Retrieves an email address by its ID.
    """
    return await _make_request(
        "GET", f"{BASE_URL}/email_addresses/{email_address_id}", api_key
    )


async def clerk_create_email_address(
    api_key: str, user_id: str, email_address: str, verified: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Creates a new email address for a user.
    """
    payload = {"user_id": user_id, "email_address": email_address}
    if verified is not None:
        payload["verified"] = verified
    return await _make_request(
        "POST", f"{BASE_URL}/email_addresses", api_key, json_data=payload
    )


async def clerk_delete_email_address(api_key: str, email_address_id: str) -> Dict[str, Any]:
    """
    Deletes an email address by its ID.
    """
    return await _make_request(
        "DELETE", f"{BASE_URL}/email_addresses/{email_address_id}", api_key
    )


# --- Phone Number Management ---


async def clerk_list_phone_numbers(
    api_key: str,
    limit: int = 10,
    offset: int = 0,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns ONE PAGE of phone numbers.
    """
    params = {"limit": limit, "offset": offset}
    if user_id:
        params["user_id"] = user_id
    return await _make_request(
        "GET", f"{BASE_URL}/phone_numbers", api_key, params=params
    )


async def clerk_fetch_all_phone_numbers(
    api_key: str,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetches ALL phone numbers by auto-paginating.
    """
    params = {}
    if user_id:
        params["user_id"] = user_id
    return await _paginate_all(f"/phone_numbers", api_key, params=params)


async def clerk_get_phone_number(api_key: str, phone_number_id: str) -> Dict[str, Any]:
    """
    Retrieves a phone number by its ID.
    """
    return await _make_request(
        "GET", f"{BASE_URL}/phone_numbers/{phone_number_id}", api_key
    )


async def clerk_create_phone_number(
    api_key: str, user_id: str, phone_number: str, verified: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Creates a new phone number for a user.
    """
    payload = {"user_id": user_id, "phone_number": phone_number}
    if verified is not None:
        payload["verified"] = verified
    return await _make_request(
        "POST", f"{BASE_URL}/phone_numbers", api_key, json_data=payload
    )


async def clerk_delete_phone_number(api_key: str, phone_number_id: str) -> Dict[str, Any]:
    """
    Deletes a phone number by its ID.
    """
    return await _make_request(
        "DELETE", f"{BASE_URL}/phone_numbers/{phone_number_id}", api_key
    )


# --- Clerk does not directly expose "products", "orders", "invoices", "billing"
#     or "checkout/payment flows" via its backend API.
#     It focuses on user identity, authentication, and authorization.
#     Analytics are typically accessed via the Clerk Dashboard.
#     The tools below cover the primary resources available via the backend API.


TOOLS = [
    # User Management
    {
        "name": "clerk_list_users",
        "description": "Returns ONE PAGE of users, optionally filtered by query, email, phone, external ID, username, user ID, or ordered by a specific field.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
                "query": {"type": "string", "description": "Search users by email, username, or phone number."},
                "email_address": {"type": "string", "description": "Filter users by email address."},
                "phone_number": {"type": "string", "description": "Filter users by phone number."},
                "external_id": {"type": "string", "description": "Filter users by external ID."},
                "username": {"type": "string", "description": "Filter users by username."},
                "user_id": {"type": "string", "description": "Filter users by user ID."},
                "order_by": {"type": "string", "description": "Order results by a specific field (e.g., 'created_at', '-created_at')."},
            },
        },
    },
    {
        "name": "clerk_fetch_all_users",
        "description": "Fetches ALL users by auto-paginating through the list users endpoint, optionally filtered by query, email, phone, external ID, username, user ID, or ordered by a specific field.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search users by email, username, or phone number."},
                "email_address": {"type": "string", "description": "Filter users by email address."},
                "phone_number": {"type": "string", "description": "Filter users by phone number."},
                "external_id": {"type": "string", "description": "Filter users by external ID."},
                "username": {"type": "string", "description": "Filter users by username."},
                "user_id": {"type": "string", "description": "Filter users by user ID."},
                "order_by": {"type": "string", "description": "Order results by a specific field (e.g., 'created_at', '-created_at')."},
            },
        },
    },
    {
        "name": "clerk_get_user",
        "description": "Retrieves a user by their ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The ID of the user to retrieve.", "required": True},
            },
        },
    },
    {
        "name": "clerk_create_user",
        "description": "Creates a new user with specified details.",
        "parameters": {
            "type": "object",
            "properties": {
                "email_address": {"type": "array", "items": {"type": "string"}, "description": "List of email addresses for the user."},
                "phone_number": {"type": "array", "items": {"type": "string"}, "description": "List of phone numbers for the user."},
                "username": {"type": "string", "description": "The username for the user."},
                "password": {"type": "string", "description": "The password for the user."},
                "first_name": {"type": "string", "description": "The first name of the user."},
                "last_name": {"type": "string", "description": "The last name of the user."},
                "external_id": {"type": "string", "description": "An external ID for the user."},
                "public_metadata": {"type": "object", "description": "Public metadata for the user."},
                "private_metadata": {"type": "object", "description": "Private metadata for the user."},
                "unsafe_metadata": {"type": "object", "description": "Unsafe metadata for the user."},
                "skip_password_checks": {"type": "boolean", "description": "Whether to skip password checks.", "default": False},
                "skip_pwned_password_checks": {"type": "boolean", "description": "Whether to skip pwned password checks.", "default": False},
                "send_invitation": {"type": "boolean", "description": "Whether to send an invitation email to the user.", "default": False},
                "invite_redirect_url": {"type": "string", "description": "The URL to redirect the user to after accepting the invitation."},
                "send_email_address_verification": {"type": "boolean", "description": "Whether to send an email address verification email.", "default": False},
                "send_phone_number_verification": {"type": "boolean", "description": "Whether to send a phone number verification SMS.", "default": False},
                "totp_enabled": {"type": "boolean", "description": "Whether TOTP is enabled for the user.", "default": False},
                "backup_code_enabled": {"type": "boolean", "description": "Whether backup codes are enabled for the user.", "default": False},
            },
        },
    },
    {
        "name": "clerk_update_user",
        "description": "Updates an existing user with specified details.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The ID of the user to update.", "required": True},
                "first_name": {"type": "string", "description": "The first name of the user."},
                "last_name": {"type": "string", "description": "The last name of the user."},
                "username": {"type": "string", "description": "The username for the user."},
                "public_metadata": {"type": "object", "description": "Public metadata for the user."},
                "private_metadata": {"type": "object", "description": "Private metadata for the user."},
                "unsafe_metadata": {"type": "object", "description": "Unsafe metadata for the user."},
                "primary_email_address_id": {"type": "string", "description": "The ID of the primary email address."},
                "primary_phone_number_id": {"type": "string", "description": "The ID of the primary phone number."},
                "primary_web3_wallet_id": {"type": "string", "description": "The ID of the primary web3 wallet."},
                "profile_image_id": {"type": "string", "description": "The ID of the profile image."},
                "password": {"type": "string", "description": "The new password for the user."},
                "skip_password_checks": {"type": "boolean", "description": "Whether to skip password checks.", "default": False},
                "skip_pwned_password_checks": {"type": "boolean", "description": "Whether to skip pwned password checks.", "default": False},
                "totp_enabled": {"type": "boolean", "description": "Whether TOTP is enabled for the user.", "default": False},
                "backup_code_enabled": {"type": "boolean", "description": "Whether backup codes are enabled for the user.", "default": False},
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "clerk_delete_user",
        "description": "Deletes a user by their ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The ID of the user to delete.", "required": True},
            },
        },
    },
    # Organization Management
    {
        "name": "clerk_list_organizations",
        "description": "Returns ONE PAGE of organizations, optionally filtered by query, user ID, or ordered by a specific field.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
                "query": {"type": "string", "description": "Search organizations by name or slug."},
                "user_id": {"type": "string", "description": "Filter organizations by a user ID (organizations the user is a member of)."},
                "order_by": {"type": "string", "description": "Order results by a specific field (e.g., 'created_at', '-created_at')."},
            },
        },
    },
    {
        "name": "clerk_fetch_all_organizations",
        "description": "Fetches ALL organizations by auto-paginating through the list organizations endpoint, optionally filtered by query, user ID, or ordered by a specific field.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search organizations by name or slug."},
                "user_id": {"type": "string", "description": "Filter organizations by a user ID (organizations the user is a member of)."},
                "order_by": {"type": "string", "description": "Order results by a specific field (e.g., 'created_at', '-created_at')."},
            },
        },
    },
    {
        "name": "clerk_get_organization",
        "description": "Retrieves an organization by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization to retrieve.", "required": True},
            },
        },
    },
    {
        "name": "clerk_create_organization",
        "description": "Creates a new organization.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The name of the organization.", "required": True},
                "slug": {"type": "string", "description": "The slug of the organization."},
                "created_by": {"type": "string", "description": "The ID of the user who created the organization."},
                "public_metadata": {"type": "object", "description": "Public metadata for the organization."},
                "private_metadata": {"type": "object", "description": "Private metadata for the organization."},
                "unsafe_metadata": {"type": "object", "description": "Unsafe metadata for the organization."},
                "max_allowed_memberships": {"type": "integer", "description": "The maximum number of memberships allowed for the organization."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "clerk_update_organization",
        "description": "Updates an existing organization.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization to update.", "required": True},
                "name": {"type": "string", "description": "The new name of the organization."},
                "slug": {"type": "string", "description": "The new slug of the organization."},
                "public_metadata": {"type": "object", "description": "Public metadata for the organization."},
                "private_metadata": {"type": "object", "description": "Private metadata for the organization."},
                "unsafe_metadata": {"type": "object", "description": "Unsafe metadata for the organization."},
                "max_allowed_memberships": {"type": "integer", "description": "The maximum number of memberships allowed for the organization."},
            },
            "required": ["organization_id"],
        },
    },
    {
        "name": "clerk_delete_organization",
        "description": "Deletes an organization by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization to delete.", "required": True},
            },
        },
    },
    # Organization Memberships
    {
        "name": "clerk_list_organization_memberships",
        "description": "Returns ONE PAGE of memberships for a given organization.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization.", "required": True},
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
            },
        },
    },
    {
        "name": "clerk_fetch_all_organization_memberships",
        "description": "Fetches ALL memberships for a given organization by auto-paginating.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization.", "required": True},
            },
        },
    },
    {
        "name": "clerk_create_organization_membership",
        "description": "Creates a new organization membership.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization.", "required": True},
                "user_id": {"type": "string", "description": "The ID of the user to add to the organization.", "required": True},
                "role": {"type": "string", "description": "The role of the user in the organization (e.g., 'admin', 'member').", "required": True},
            },
        },
    },
    {
        "name": "clerk_update_organization_membership",
        "description": "Updates an organization membership's role.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization.", "required": True},
                "user_id": {"type": "string", "description": "The ID of the user whose membership role to update.", "required": True},
                "role": {"type": "string", "description": "The new role for the user in the organization (e.g., 'admin', 'member').", "required": True},
            },
        },
    },
    {
        "name": "clerk_delete_organization_membership",
        "description": "Deletes an organization membership.",
        "parameters": {
            "type": "object",
            "properties": {
                "organization_id": {"type": "string", "description": "The ID of the organization.", "required": True},
                "user_id": {"type": "string", "description": "The ID of the user whose membership to delete.", "required": True},
            },
        },
    },
    # Session Management
    {
        "name": "clerk_list_sessions",
        "description": "Returns ONE PAGE of sessions, optionally filtered by user ID, client ID, status, or ordered by a specific field.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
                "user_id": {"type": "string", "description": "Filter sessions by user ID."},
                "client_id": {"type": "string", "description": "Filter sessions by client ID."},
                "status": {"type": "string", "description": "Filter sessions by status (e.g., 'active', 'revoked')."},
                "order_by": {"type": "string", "description": "Order results by a specific field (e.g., 'created_at', '-created_at')."},
            },
        },
    },
    {
        "name": "clerk_fetch_all_sessions",
        "description": "Fetches ALL sessions by auto-paginating through the list sessions endpoint, optionally filtered by user ID, client ID, status, or ordered by a specific field.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Filter sessions by user ID."},
                "client_id": {"type": "string", "description": "Filter sessions by client ID."},
                "status": {"type": "string", "description": "Filter sessions by status (e.g., 'active', 'revoked')."},
                "order_by": {"type": "string", "description": "Order results by a specific field (e.g., 'created_at', '-created_at')."},
            },
        },
    },
    {
        "name": "clerk_get_session",
        "description": "Retrieves a session by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The ID of the session to retrieve.", "required": True},
            },
        },
    },
    {
        "name": "clerk_revoke_session",
        "description": "Revokes a session by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "The ID of the session to revoke.", "required": True},
            },
        },
    },
    # Webhook Management (Test Events & Delivery Attempts)
    {
        "name": "clerk_create_webhook_test_event",
        "description": "Creates a new webhook test event for a given webhook.",
        "parameters": {
            "type": "object",
            "properties": {
                "webhook_id": {"type": "string", "description": "The ID of the webhook.", "required": True},
                "event_type": {"type": "string", "description": "The type of event to simulate (e.g., 'user.created', 'organization.created').", "required": True},
                "payload": {"type": "object", "description": "The payload of the test event.", "required": True},
            },
        },
    },
    {
        "name": "clerk_list_webhook_delivery_attempts",
        "description": "Returns ONE PAGE of delivery attempts for a given webhook.",
        "parameters": {
            "type": "object",
            "properties": {
                "webhook_id": {"type": "string", "description": "The ID of the webhook.", "required": True},
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
            },
        },
    },
    {
        "name": "clerk_fetch_all_webhook_delivery_attempts",
        "description": "Fetches ALL delivery attempts for a given webhook by auto-paginating.",
        "parameters": {
            "type": "object",
            "properties": {
                "webhook_id": {"type": "string", "description": "The ID of the webhook.", "required": True},
            },
        },
    },
    # Email Address Management
    {
        "name": "clerk_list_email_addresses",
        "description": "Returns ONE PAGE of email addresses, optionally filtered by user ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
                "user_id": {"type": "string", "description": "Filter email addresses by user ID."},
            },
        },
    },
    {
        "name": "clerk_fetch_all_email_addresses",
        "description": "Fetches ALL email addresses by auto-paginating, optionally filtered by user ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Filter email addresses by user ID."},
            },
        },
    },
    {
        "name": "clerk_get_email_address",
        "description": "Retrieves an email address by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "email_address_id": {"type": "string", "description": "The ID of the email address to retrieve.", "required": True},
            },
        },
    },
    {
        "name": "clerk_create_email_address",
        "description": "Creates a new email address for a user.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The ID of the user to associate the email address with.", "required": True},
                "email_address": {"type": "string", "description": "The email address to create.", "required": True},
                "verified": {"type": "boolean", "description": "Whether the email address is verified.", "default": False},
            },
        },
    },
    {
        "name": "clerk_delete_email_address",
        "description": "Deletes an email address by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "email_address_id": {"type": "string", "description": "The ID of the email address to delete.", "required": True},
            },
        },
    },
    # Phone Number Management
    {
        "name": "clerk_list_phone_numbers",
        "description": "Returns ONE PAGE of phone numbers, optionally filtered by user ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "The number of records to return.", "default": 10},
                "offset": {"type": "integer", "description": "The number of records to skip.", "default": 0},
                "user_id": {"type": "string", "description": "Filter phone numbers by user ID."},
            },
        },
    },
    {
        "name": "clerk_fetch_all_phone_numbers",
        "description": "Fetches ALL phone numbers by auto-paginating, optionally filtered by user ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "Filter phone numbers by user ID."},
            },
        },
    },
    {
        "name": "clerk_get_phone_number",
        "description": "Retrieves a phone number by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number_id": {"type": "string", "description": "The ID of the phone number to retrieve.", "required": True},
            },
        },
    },
    {
        "name": "clerk_create_phone_number",
        "description": "Creates a new phone number for a user.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "The ID of the user to associate the phone number with.", "required": True},
                "phone_number": {"type": "string", "description": "The phone number to create.", "required": True},
                "verified": {"type": "boolean", "description": "Whether the phone number is verified.", "default": False},
            },
        },
    },
    {
        "name": "clerk_delete_phone_number",
        "description": "Deletes a phone number by its ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number_id": {"type": "string", "description": "The ID of the phone number to delete.", "required": True},
            },
        },
    },
]


async def execute(tool_name: str, args: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """
    Executes the specified Clerk API tool with the given arguments and API key.
    """
    if tool_name == "clerk_list_users":
        return await clerk_list_users(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_users":
        return await clerk_fetch_all_users(api_key=api_key, **args)
    elif tool_name == "clerk_get_user":
        return await clerk_get_user(api_key=api_key, **args)
    elif tool_name == "clerk_create_user":
        return await clerk_create_user(api_key=api_key, **args)
    elif tool_name == "clerk_update_user":
        return await clerk_update_user(api_key=api_key, **args)
    elif tool_name == "clerk_delete_user":
        return await clerk_delete_user(api_key=api_key, **args)
    elif tool_name == "clerk_list_organizations":
        return await clerk_list_organizations(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_organizations":
        return await clerk_fetch_all_organizations(api_key=api_key, **args)
    elif tool_name == "clerk_get_organization":
        return await clerk_get_organization(api_key=api_key, **args)
    elif tool_name == "clerk_create_organization":
        return await clerk_create_organization(api_key=api_key, **args)
    elif tool_name == "clerk_update_organization":
        return await clerk_update_organization(api_key=api_key, **args)
    elif tool_name == "clerk_delete_organization":
        return await clerk_delete_organization(api_key=api_key, **args)
    elif tool_name == "clerk_list_organization_memberships":
        return await clerk_list_organization_memberships(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_organization_memberships":
        return await clerk_fetch_all_organization_memberships(api_key=api_key, **args)
    elif tool_name == "clerk_create_organization_membership":
        return await clerk_create_organization_membership(api_key=api_key, **args)
    elif tool_name == "clerk_update_organization_membership":
        return await clerk_update_organization_membership(api_key=api_key, **args)
    elif tool_name == "clerk_delete_organization_membership":
        return await clerk_delete_organization_membership(api_key=api_key, **args)
    elif tool_name == "clerk_list_sessions":
        return await clerk_list_sessions(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_sessions":
        return await clerk_fetch_all_sessions(api_key=api_key, **args)
    elif tool_name == "clerk_get_session":
        return await clerk_get_session(api_key=api_key, **args)
    elif tool_name == "clerk_revoke_session":
        return await clerk_revoke_session(api_key=api_key, **args)
    elif tool_name == "clerk_create_webhook_test_event":
        return await clerk_create_webhook_test_event(api_key=api_key, **args)
    elif tool_name == "clerk_list_webhook_delivery_attempts":
        return await clerk_list_webhook_delivery_attempts(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_webhook_delivery_attempts":
        return await clerk_fetch_all_webhook_delivery_attempts(api_key=api_key, **args)
    elif tool_name == "clerk_list_email_addresses":
        return await clerk_list_email_addresses(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_email_addresses":
        return await clerk_fetch_all_email_addresses(api_key=api_key, **args)
    elif tool_name == "clerk_get_email_address":
        return await clerk_get_email_address(api_key=api_key, **args)
    elif tool_name == "clerk_create_email_address":
        return await clerk_create_email_address(api_key=api_key, **args)
    elif tool_name == "clerk_delete_email_address":
        return await clerk_delete_email_address(api_key=api_key, **args)
    elif tool_name == "clerk_list_phone_numbers":
        return await clerk_list_phone_numbers(api_key=api_key, **args)
    elif tool_name == "clerk_fetch_all_phone_numbers":
        return await clerk_fetch_all_phone_numbers(api_key=api_key, **args)
    elif tool_name == "clerk_get_phone_number":
        return await clerk_get_phone_number(api_key=api_key, **args)
    elif tool_name == "clerk_create_phone_number":
        return await clerk_create_phone_number(api_key=api_key, **args)
    elif tool_name == "clerk_delete_phone_number":
        return await clerk_delete_phone_number(api_key=api_key, **args)
    else:
        return {"error": f"Tool {tool_name} not found."}