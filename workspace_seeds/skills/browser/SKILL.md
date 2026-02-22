---
name: browser
description: Browse websites, fill forms, and scrape web data. Use when interacting with websites or automating web tasks without API access.
---

# Browser Automation

Lucy can browse the web using Composio's browser tools when there's no API available for a service.

## When to Use

- Scraping data from websites that don't have APIs
- Filling out web forms
- Taking screenshots of web pages
- Interacting with web applications

## How It Works

Use `COMPOSIO_SEARCH_TOOLS` to find browser automation tools, or use `COMPOSIO_REMOTE_WORKBENCH` to run Playwright scripts directly.

## Best Practices

1. **Prefer APIs over scraping** — always check if there's an integration available first
2. **Use screenshots for verification** — take screenshots to confirm you're on the right page
3. **Handle errors gracefully** — websites change; be prepared for elements not being found
4. **Respect rate limits** — don't hammer websites with rapid requests
5. **Check robots.txt** — respect website scraping policies

## Common Patterns

### Scraping Structured Data
```python
# Use COMPOSIO_REMOTE_WORKBENCH to run a scraping script
import httpx
from bs4 import BeautifulSoup

response = httpx.get("https://example.com/products")
soup = BeautifulSoup(response.text, "html.parser")
products = [item.text for item in soup.select(".product-title")]
```

### Taking Screenshots
Use browser tools to navigate to a URL and capture a screenshot for visual inspection or sharing in Slack.

### Form Submission
When filling forms, always:
1. Navigate to the form page
2. Verify the form structure
3. Fill in fields one by one
4. Review before submitting
5. Capture confirmation
