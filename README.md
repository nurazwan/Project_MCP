# Playwright MCP Server

A Python-based MCP (Model Context Protocol) server for browser automation using [Playwright](https://playwright.dev/python/) and [FastMCP](https://gofastmcp.com/).

## Features

- **Multi-browser support**: Chromium, Firefox, and WebKit
- **Session management**: Multiple browser pages with persistent sessions
- **Navigation**: Go to URLs, back/forward, reload
- **Element interactions**: Click, fill, type, hover, select options, check/uncheck
- **Content extraction**: Get text, HTML, attributes, query elements
- **Screenshots**: Full page, viewport, or element screenshots
- **Keyboard support**: Press keys, key combinations
- **Wait utilities**: Wait for selectors, load states, URLs
- **JavaScript evaluation**: Execute custom JS in browser context
- **File uploads**: Upload files to input elements
- **PDF export**: Save pages as PDF (Chromium only)
- **Dialog handling**: Handle alert, confirm, and prompt dialogs

## Installation

### Prerequisites

- Python 3.10 or higher
- pip or uv package manager

### Install from source

```bash
# Clone or navigate to the project
cd Project_MCP

# Install dependencies
pip install -e .

# Install Playwright browsers
python -m playwright install
```

### Install dependencies only

```bash
pip install -r requirements.txt
python -m playwright install
```

## Usage

### Running the Server

```bash
# Run directly
python -m playwright_mcp.server

# Or use the installed command
playwright-mcp

# Or with FastMCP CLI
fastmcp run src/playwright_mcp/server.py
```

### Claude Desktop Configuration

Add to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "playwright": {
      "command": "python",
      "args": ["-m", "playwright_mcp.server"],
      "cwd": "/path/to/Project_MCP"
    }
  }
}
```

Or using uv:

```json
{
  "mcpServers": {
    "playwright": {
      "command": "uv",
      "args": ["run", "playwright-mcp"],
      "cwd": "/path/to/Project_MCP"
    }
  }
}
```

## Available Tools

### Browser Management

| Tool | Description |
|------|-------------|
| `browser_launch` | Launch a browser (chromium/firefox/webkit) |
| `browser_close` | Close browser and cleanup resources |
| `page_new` | Create a new browser tab/page |
| `page_close` | Close a specific page |
| `page_list` | List all open pages |

### Navigation

| Tool | Description |
|------|-------------|
| `navigate` | Navigate to a URL |
| `go_back` | Navigate back in history |
| `go_forward` | Navigate forward in history |
| `reload` | Reload the current page |

### Element Interactions

| Tool | Description |
|------|-------------|
| `click` | Click an element |
| `fill` | Fill text into an input field |
| `type_text` | Type text character by character |
| `press_key` | Press keyboard keys |
| `hover` | Hover over an element |
| `select_option` | Select dropdown option |
| `check` | Check/uncheck checkbox or radio |
| `focus` | Focus an element |

### Content Extraction

| Tool | Description |
|------|-------------|
| `get_text` | Get text content of element |
| `get_inner_html` | Get inner HTML of element |
| `get_attribute` | Get element attribute value |
| `get_page_content` | Get full page HTML |
| `query_selector_all` | Find all matching elements |

### Screenshots

| Tool | Description |
|------|-------------|
| `screenshot` | Take screenshot (returns base64) |
| `screenshot_to_file` | Save screenshot to file |

### Wait Utilities

| Tool | Description |
|------|-------------|
| `wait_for_selector` | Wait for element state |
| `wait_for_load_state` | Wait for page load state |
| `wait_for_url` | Wait for URL pattern |

### Advanced

| Tool | Description |
|------|-------------|
| `evaluate` | Execute JavaScript |
| `save_as_pdf` | Save page as PDF |
| `scroll` | Scroll the page |
| `set_viewport` | Set viewport size |
| `handle_dialog` | Handle JS dialogs |
| `upload_file` | Upload files |
| `is_visible` | Check element visibility |
| `is_enabled` | Check if element is enabled |
| `is_checked` | Check if checkbox is checked |

## Example Workflows

### Basic Navigation and Screenshot

```
1. browser_launch(browser_type="chromium", headless=true)
2. navigate(url="https://example.com")
3. screenshot(page_id="page_1", full_page=true)
4. browser_close()
```

### Form Filling

```
1. browser_launch()
2. navigate(url="https://example.com/login")
3. fill(page_id="page_1", selector="#username", value="user@example.com")
4. fill(page_id="page_1", selector="#password", value="password123")
5. click(page_id="page_1", selector="button[type='submit']")
6. wait_for_url(page_id="page_1", url_pattern="**/dashboard")
```

### Data Extraction

```
1. browser_launch()
2. navigate(url="https://news.ycombinator.com")
3. query_selector_all(page_id="page_1", selector=".titleline > a")
4. get_text(page_id="page_1", selector=".titleline")
```

## Selector Types

The tools support various selector types:

- **CSS**: `button.submit`, `#login-btn`, `[data-testid='submit']`
- **XPath**: `xpath=//button[@type='submit']`
- **Text**: `text=Sign In`, `text=Click here`
- **Role**: `role=button[name='Submit']`

## Development

### Running Tests

```bash
pip install -e ".[dev]"
pytest
```

### Project Structure

```
Project_MCP/
├── src/
│   └── playwright_mcp/
│       ├── __init__.py
│       └── server.py
├── pyproject.toml
├── requirements.txt
└── README.md
```

## License

MIT License
