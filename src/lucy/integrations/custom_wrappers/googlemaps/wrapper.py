"""
A Python wrapper module for the Google Maps API, providing tools for geocoding
functionality.

This wrapper allows an AI assistant to convert addresses to geographic
coordinates, geographic coordinates to human-readable addresses, and Place IDs
to human-readable addresses using the Google Maps Geocoding API.
"""

import httpx
import json

API_BASE_URL = "https://maps.googleapis.com/"

async def _make_request(
    method: str,
    url: str,
    api_key: str,
    params: dict = None,
    data: dict = None,
) -> dict:
    """
    Helper function to make HTTP requests to the Google Maps API.
    """
    headers = {}
    if params is None:
        params = {}
    params['key'] = api_key

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            if method == "GET":
                response = await client.get(url, params=params, headers=headers)
            elif method == "POST":
                response = await client.post(url, params=params, headers=headers, json=data)
            else:
                return {"error": f"Unsupported HTTP method: {method}"}

            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP error occurred: {e.response.status_code} - {e.response.text}"}
    except httpx.RequestError as e:
        return {"error": f"Request error occurred: {e}"}
    except json.JSONDecodeError:
        return {"error": "Failed to decode JSON response from API."}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}

async def googlemaps_geocode_address(api_key: str, address: str, components: str = None, bounds: str = None, region: str = None, language: str = None) -> dict:
    """
    Converts a human-readable address into geographic coordinates (latitude and longitude).
    """
    params = {"address": address}
    if components:
        params["components"] = components
    if bounds:
        params["bounds"] = bounds
    if region:
        params["region"] = region
    if language:
        params["language"] = language
    return await _make_request("GET", f"{API_BASE_URL}/maps/api/geocode/json", api_key, params=params)

async def googlemaps_reverse_geocode_coordinates(api_key: str, latlng: str, result_type: str = None, location_type: str = None, language: str = None) -> dict:
    """
    Converts geographic coordinates into a human-readable address.
    """
    params = {"latlng": latlng}
    if result_type:
        params["result_type"] = result_type
    if location_type:
        params["location_type"] = location_type
    if language:
        params["language"] = language
    return await _make_request("GET", f"{API_BASE_URL}/maps/api/geocode/json", api_key, params=params)

async def googlemaps_reverse_geocode_place_id(api_key: str, place_id: str, result_type: str = None, location_type: str = None, language: str = None) -> dict:
    """
    Converts a Place ID into a human-readable address.
    """
    params = {"place_id": place_id}
    if result_type:
        params["result_type"] = result_type
    if location_type:
        params["location_type"] = location_type
    if language:
        params["language"] = language
    return await _make_request("GET", f"{API_BASE_URL}/maps/api/geocode/json", api_key, params=params)

TOOLS = [
    {
        "name": "googlemaps_geocode_address",
        "description": "Converts a human-readable address into geographic coordinates (latitude and longitude).",
        "parameters": {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "The address to geocode (e.g., '1600 Amphitheatre Parkway, Mountain View, CA')."
                },
                "components": {
                    "type": "string",
                    "description": "A component filter for geocoding. For example: 'country:GB|locality:London'."
                },
                "bounds": {
                    "type": "string",
                    "description": "The bounding box of the viewport within which to bias geocode results. For example: '34.172684,-118.604794|34.236144,-118.500938'."
                },
                "region": {
                    "type": "string",
                    "description": "The region code, specified as a ccTLD ('top-level domain') two-character value. For example: 'uk'."
                },
                "language": {
                    "type": "string",
                    "description": "The language in which to return results. For example: 'en' for English."
                }
            },
            "required": ["address"]
        }
    },
    {
        "name": "googlemaps_reverse_geocode_coordinates",
        "description": "Converts geographic coordinates (latitude and longitude) into a human-readable address.",
        "parameters": {
            "type": "object",
            "properties": {
                "latlng": {
                    "type": "string",
                    "description": "The latitude and longitude from which to retrieve the nearest address. For example: '40.714224,-73.961452'."
                },
                "result_type": {
                    "type": "string",
                    "description": "One or more address types to restrict the results to. For example: 'street_address|postal_code'."
                },
                "location_type": {
                    "type": "string",
                    "description": "One or more location types to restrict the results to. For example: 'ROOFTOP|RANGE_INTERPOLATED'."
                },
                "language": {
                    "type": "string",
                    "description": "The language in which to return results. For example: 'es' for Spanish."
                }
            },
            "required": ["latlng"]
        }
    },
    {
        "name": "googlemaps_reverse_geocode_place_id",
        "description": "Converts a Place ID into a human-readable address.",
        "parameters": {
            "type": "object",
            "properties": {
                "place_id": {
                    "type": "string",
                    "description": "The Place ID of the location for which to retrieve the nearest address. For example: 'ChIJd8nM42_uwoAR0yK_0e0_t_A'."
                },
                "result_type": {
                    "type": "string",
                    "description": "One or more address types to restrict the results to. For example: 'street_address|postal_code'."
                },
                "location_type": {
                    "type": "string",
                    "description": "One or more location types to restrict the results to. For example: 'ROOFTOP|RANGE_INTERPOLATED'."
                },
                "language": {
                    "type": "string",
                    "description": "The language in which to return results. For example: 'fr' for French."
                }
            },
            "required": ["place_id"]
        }
    }
]

async def execute(tool_name: str, args: dict, api_key: str) -> dict:
    """
    Executes the specified Google Maps tool with the given arguments and API key.
    """
    if tool_name == "googlemaps_geocode_address":
        return await googlemaps_geocode_address(api_key, **args)
    elif tool_name == "googlemaps_reverse_geocode_coordinates":
        return await googlemaps_reverse_geocode_coordinates(api_key, **args)
    elif tool_name == "googlemaps_reverse_geocode_place_id":
        return await googlemaps_reverse_geocode_place_id(api_key, **args)
    else:
        return {"error": f"Tool {tool_name} not found."}