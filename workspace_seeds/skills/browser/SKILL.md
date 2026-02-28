---
name: browser
description: Browse websites using CamoFox anti-detection browser. Use when scraping data, filling forms, automating web tasks, or researching sites without API access.
---

# Browser Automation: CamoFox

Lucy uses [CamoFox](https://github.com/redf0x1/camofox-browser), an anti-detection browser server built on Camoufox (a stealth Firefox fork). Anti-detection is at the C++ engine level and is not detectable by standard bot-detection systems.

## Architecture

CamoFox runs as a REST API server (default port 9377) alongside Lucy. It provides:
- Multi-session browser tabs with persistent profiles
- Accessibility snapshots with token-efficient `eN` element references
- 14 built-in search macros for common sites
- Per-user persistent profiles (cookies, localStorage survive sessions)

## Core Workflow

```
1. POST /tabs                          → Create a new tab (returns tab_id)
2. POST /tabs/:id/navigate             → Navigate to a URL
3. GET  /tabs/:id/snapshot             → Get accessibility snapshot with eN refs
4. POST /tabs/:id/click|type|press     → Interact with elements by eN ref
5. GET  /tabs/:id/snapshot             → Verify result
6. DELETE /tabs/:id                    → Close tab when done
```

## API Reference

### Tab Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST /tabs` | Create tab | `{"userId": "workspace_id"}`: persistent profile per user |
| `GET /tabs` | List tabs | Returns all open tabs |
| `DELETE /tabs/:id` | Close tab | Clean up when done |

### Navigation

| Method | Endpoint | Body |
|--------|----------|------|
| `POST /tabs/:id/navigate` | Go to URL | `{"url": "https://..."}` |
| `POST /tabs/:id/go_back` | Browser back | (no body) |
| `POST /tabs/:id/go_forward` | Browser forward | (no body) |
| `POST /tabs/:id/reload` | Reload page | (no body) |

### Snapshot (Read the Page)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET /tabs/:id/snapshot` | Accessibility snapshot | Returns page structure with `eN` element refs |

Snapshots are ~90% smaller than raw HTML; extremely token-efficient for LLM consumption. Each interactive element gets a unique `eN` reference (e.g. `e14`, `e27`) that you use for click/type interactions.

### Interaction

| Method | Endpoint | Body |
|--------|----------|------|
| `POST /tabs/:id/click` | Click element | `{"ref": "e14"}` |
| `POST /tabs/:id/type` | Type text (appends) | `{"ref": "e14", "text": "hello"}` |
| `POST /tabs/:id/fill` | Clear and replace text | `{"ref": "e14", "text": "hello"}` |
| `POST /tabs/:id/press` | Press key | `{"ref": "e14", "key": "Enter"}` |
| `POST /tabs/:id/select` | Select dropdown option | `{"ref": "e14", "value": "option1"}` |
| `POST /tabs/:id/scroll` | Scroll element | `{"ref": "e14", "direction": "down"}` |
| `POST /tabs/:id/hover` | Hover over element | `{"ref": "e14"}` |

### Screenshot

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET /tabs/:id/screenshot` | Full page screenshot | Returns PNG image |

## Search Macros

Built-in search macros navigate and extract structured results automatically:

| Macro | Usage | Returns |
|-------|-------|---------|
| `@google_search` | `POST /tabs/:id/navigate {"url": "@google_search query"}` | Search results with titles, URLs, snippets |
| `@youtube_search` | `@youtube_search query` | Video results |
| `@reddit_search` | `@reddit_search query` | Reddit posts and comments |
| `@amazon_search` | `@amazon_search query` | Product listings |
| `@github_search` | `@github_search query` | Repositories and code |
| `@twitter_search` | `@twitter_search query` | Tweets |
| `@linkedin_search` | `@linkedin_search query` | Profiles and posts |
| `@bing_search` | `@bing_search query` | Search results |
| `@duckduckgo_search` | `@duckduckgo_search query` | Search results |
| `@wikipedia_search` | `@wikipedia_search query` | Articles |
| `@stackoverflow_search` | `@stackoverflow_search query` | Questions and answers |
| `@hacker_news_search` | `@hacker_news_search query` | HN posts |
| `@maps_search` | `@maps_search query` | Map results |
| `@news_search` | `@news_search query` | News articles |

## Practical Examples

### Research a Company
```
1. Create tab → navigate "@google_search CompanyName"
2. Read snapshot → find company website URL
3. Navigate to website → snapshot → extract key info
4. Navigate "@linkedin_search CompanyName" → get company profile
5. Close tab
```

### Fill a Web Form
```
1. Create tab → navigate to form URL
2. Snapshot → identify form fields (eN refs)
3. Fill each field: POST /tabs/:id/fill {"ref": "e5", "text": "value"}
4. Click submit: POST /tabs/:id/click {"ref": "e12"}
5. Snapshot → verify confirmation
6. Close tab
```

### Scrape Structured Data
```
1. Create tab → navigate to data page
2. Snapshot → read the accessibility tree
3. Extract data from the structured snapshot (tables, lists, text nodes)
4. If paginated: click "Next" → snapshot → extract → repeat
5. Close tab
```

## Best Practices

1. **Always snapshot before interacting**: don't click blindly; read the page first
2. **Use search macros for research**: they're faster and more reliable than manual navigation
3. **Close tabs when done**: free up browser resources
4. **Prefer APIs over browsing**: check if there's a Composio integration first
5. **Use persistent profiles**: set `userId` to workspace_id so login sessions persist
6. **Handle dynamic pages**: wait 1-3 seconds after navigation, then snapshot to check if content loaded
7. **Short incremental waits**: prefer waiting 2s → snapshot → check, rather than one long 10s wait

## Anti-Patterns

- Don't browse when an API/integration exists (check `COMPOSIO_SEARCH_TOOLS` first)
- Don't click elements without snapshotting first to get current `eN` refs
- Don't keep tabs open indefinitely; close them when the task is complete
- Don't rapid-fire requests; add small delays between interactions for page rendering
- Don't try to interact with iframe content; only elements outside iframes are accessible
