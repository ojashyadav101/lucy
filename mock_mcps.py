import asyncio
import logging
import uvicorn
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)

def make_mcp(name, tools):
    mcp = FastMCP(name)
    for tool_name, tool_func in tools.items():
        mcp.add_tool(tool_func, name=tool_name)
    return mcp

# Define tools for 10 servers
def get_weather(location: str) -> str:
    """Get the current weather for a location."""
    return f"Sunny and 72F in {location}"

def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b

def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b

def convert_currency(amount: float, from_c: str, to_c: str) -> str:
    """Convert currency."""
    return f"{amount} {from_c} is {amount * 1.2} {to_c}"

def get_time(tz: str) -> str:
    """Get the current time in a timezone."""
    return f"It is 12:00 PM in {tz}"

def translate(text: str, lang: str) -> str:
    """Translate text to a language."""
    return f"Translated '{text}' to {lang}: Hola"

def get_fact(topic: str) -> str:
    """Get a random fact about a topic."""
    return f"Here is a fact about {topic}: It is interesting."

def reverse_string(text: str) -> str:
    """Reverse a string."""
    return text[::-1]

def get_ip_info(ip: str) -> str:
    """Get info about an IP address."""
    return f"IP {ip} is located in New York."

def get_stock_price(ticker: str) -> str:
    """Get the current price of a stock."""
    return f"{ticker} is trading at $150.00"

def get_user(user_id: int) -> str:
    """Get user details by ID."""
    return f"User {user_id} is Alice."

mcps = [
    make_mcp("WeatherMCP", {"get_weather": get_weather}),
    make_mcp("CalcMCP", {"add": add, "multiply": multiply}),
    make_mcp("CurrencyMCP", {"convert_currency": convert_currency}),
    make_mcp("TimeMCP", {"get_time": get_time}),
    make_mcp("TranslateMCP", {"translate": translate}),
    make_mcp("FactMCP", {"get_fact": get_fact}),
    make_mcp("StringMCP", {"reverse_string": reverse_string}),
    make_mcp("IpMCP", {"get_ip_info": get_ip_info}),
    make_mcp("StockMCP", {"get_stock_price": get_stock_price}),
    make_mcp("UserMCP", {"get_user": get_user}),
]

async def run_server(app, port):
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    tasks = [run_server(mcp.streamable_http_app(), 8001 + i) for i, mcp in enumerate(mcps)]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
