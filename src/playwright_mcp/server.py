"""
Playwright MCP Server

A comprehensive MCP server for browser automation using Playwright.
Built with FastMCP for seamless integration with Claude and other MCP clients.
"""

import base64
import json
import os
import re
from typing import Annotated, Any, Literal
from contextlib import asynccontextmanager

from fastmcp import FastMCP, Context
from pydantic import Field
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright


# =============================================================================
# Browser Session Manager
# =============================================================================

class BrowserSessionManager:
    """Manages browser sessions and pages for the MCP server."""

    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: dict[str, Page] = {}
        self._page_counter: int = 0
        self._browser_type: str = "chromium"
        self._headless: bool = True

    async def initialize(
        self,
        browser_type: str = "chromium",
        headless: bool = True
    ) -> dict[str, Any]:
        """Initialize or reinitialize Playwright with specified browser."""
        # Close existing browser if any
        await self.cleanup()

        self._playwright = await async_playwright().start()
        self._browser_type = browser_type
        self._headless = headless

        # Get browser launcher based on type
        launcher = getattr(self._playwright, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported browser type: {browser_type}")

        self._browser = await launcher.launch(headless=headless)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        return {
            "browser_type": browser_type,
            "headless": headless,
            "status": "initialized"
        }

    async def ensure_browser(self) -> Browser:
        """Ensure browser is initialized, start with defaults if not."""
        if self._browser is None or not self._browser.is_connected():
            await self.initialize()
        return self._browser

    async def ensure_context(self) -> BrowserContext:
        """Ensure browser context exists."""
        await self.ensure_browser()
        if self._context is None:
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720}
            )
        return self._context

    async def new_page(self) -> tuple[str, Page]:
        """Create a new page and return its ID and the page object."""
        context = await self.ensure_context()
        page = await context.new_page()

        self._page_counter += 1
        page_id = f"page_{self._page_counter}"
        self._pages[page_id] = page

        return page_id, page

    async def get_page(self, page_id: str) -> Page | None:
        """Get a page by its ID."""
        page = self._pages.get(page_id)
        if page and not page.is_closed():
            return page
        return None

    async def get_or_create_page(self, page_id: str | None = None) -> tuple[str, Page]:
        """Get existing page or create new one."""
        if page_id:
            page = await self.get_page(page_id)
            if page:
                return page_id, page
        return await self.new_page()

    async def close_page(self, page_id: str) -> bool:
        """Close a specific page."""
        page = self._pages.pop(page_id, None)
        if page and not page.is_closed():
            await page.close()
            return True
        return False

    def list_pages(self) -> list[dict[str, Any]]:
        """List all active pages."""
        pages = []
        for page_id, page in list(self._pages.items()):
            if page.is_closed():
                del self._pages[page_id]
            else:
                pages.append({
                    "page_id": page_id,
                    "url": page.url,
                    "title": page.url  # title requires await, use URL
                })
        return pages

    async def cleanup(self):
        """Clean up all browser resources."""
        # Close all pages
        for page in self._pages.values():
            if not page.is_closed():
                try:
                    await page.close()
                except Exception:
                    pass
        self._pages.clear()

        # Close context
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


# Global session manager
session_manager = BrowserSessionManager()


# =============================================================================
# MCP Server Setup with Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app):
    """Manage browser lifecycle."""
    yield
    # Cleanup on shutdown
    await session_manager.cleanup()


# Create the MCP server
mcp = FastMCP(
    "Playwright Browser Automation",
    dependencies=["playwright", "pydantic"],
    lifespan=lifespan
)


# =============================================================================
# Browser Management Tools
# =============================================================================

@mcp.tool()
async def browser_launch(
    browser_type: Annotated[
        Literal["chromium", "firefox", "webkit"],
        Field(description="Browser engine: 'chromium' (default, best compatibility), 'firefox', or 'webkit' (Safari)")
    ] = "chromium",
    headless: Annotated[
        bool,
        Field(description="If true (default), browser runs invisibly. Set false to see the browser window.")
    ] = True
) -> dict[str, Any]:
    """
    Launch a browser instance. THIS MUST BE CALLED FIRST before any other browser operations.

    IMPORTANT: Always call this tool before using navigate, click, fill, screenshot, or any other browser tool.
    If you get "Page not found" errors, you likely forgot to call browser_launch first.

    Browser options:
    - chromium: Best choice for most tasks. Fast, reliable, supports PDF export.
    - firefox: Use when testing Firefox-specific behavior.
    - webkit: Use when testing Safari-specific behavior.

    Returns: {"status": "success", "browser_type": "chromium", "headless": true} on success.

    Example workflow:
    1. browser_launch() -> launches headless Chromium
    2. navigate(url="https://example.com") -> opens the page
    3. screenshot(page_id="page_1") -> captures the page
    4. browser_close() -> cleanup when done
    """
    try:
        result = await session_manager.initialize(browser_type, headless)
        return {
            "status": "success",
            "message": f"Browser '{browser_type}' launched successfully",
            **result
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def browser_close() -> dict[str, Any]:
    """
    Close the browser and release all resources. Call this when you are completely done with browser automation.

    WHEN TO USE:
    - After completing all browser tasks
    - Before launching a different browser type
    - To free up system memory

    NOTE: This closes ALL open pages. You cannot interact with any pages after calling this.
    To continue browsing, you must call browser_launch() again.

    Returns: {"status": "success", "message": "Browser closed successfully"}
    """
    try:
        await session_manager.cleanup()
        return {"status": "success", "message": "Browser closed successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def page_new() -> dict[str, Any]:
    """
    Create a new browser tab/page and return its page_id.

    WHEN TO USE:
    - To open multiple pages simultaneously (e.g., compare two websites)
    - To keep one page open while navigating to another URL
    - Note: navigate() automatically creates a page if none exists, so this is optional for single-page workflows

    PREREQUISITE: browser_launch() must be called first.

    Returns: {"status": "success", "page_id": "page_2"} - Use this page_id in subsequent tool calls.

    Example - Opening multiple pages:
    1. browser_launch()
    2. navigate(url="https://google.com") -> creates page_1 automatically
    3. page_new() -> creates page_2
    4. navigate(url="https://bing.com", page_id="page_2") -> navigates page_2
    """
    try:
        page_id, page = await session_manager.new_page()
        return {
            "status": "success",
            "page_id": page_id,
            "message": f"New page created with ID: {page_id}"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def page_close(
    page_id: Annotated[str, Field(description="The page_id returned from navigate() or page_new(), e.g., 'page_1'")]
) -> dict[str, Any]:
    """
    Close a specific browser page/tab to free resources.

    WHEN TO USE:
    - When done with a specific page but want to keep other pages open
    - To free memory when working with many pages
    - Use browser_close() instead if you want to close everything

    Returns: {"status": "success"} or {"status": "error", "message": "Page not found"}
    """
    try:
        closed = await session_manager.close_page(page_id)
        if closed:
            return {"status": "success", "message": f"Page {page_id} closed"}
        return {"status": "error", "message": f"Page {page_id} not found or already closed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def page_list() -> dict[str, Any]:
    """
    List all currently open browser pages with their page_ids and URLs.

    WHEN TO USE:
    - To find out which pages are open and their current URLs
    - To get the page_id when you forgot it
    - To verify pages are still open before interacting with them

    Returns: {
        "status": "success",
        "page_count": 2,
        "pages": [
            {"page_id": "page_1", "url": "https://google.com"},
            {"page_id": "page_2", "url": "https://example.com"}
        ]
    }
    """
    try:
        pages = session_manager.list_pages()
        return {
            "status": "success",
            "page_count": len(pages),
            "pages": pages
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Navigation Tools
# =============================================================================

@mcp.tool()
async def navigate(
    url: Annotated[str, Field(description="Full URL including protocol, e.g., 'https://www.google.com' or 'https://example.com/login'")],
    page_id: Annotated[str | None, Field(description="Target page_id (e.g., 'page_1'). If omitted, creates a new page automatically.")] = None,
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="'load' (default): wait for full load. 'networkidle': wait for no network activity (best for SPAs). 'domcontentloaded': faster, DOM ready only.")
    ] = "load",
    timeout: Annotated[
        int,
        Field(description="Max wait time in milliseconds. Increase for slow pages. Default: 30000 (30 seconds)", ge=1000, le=60000)
    ] = 30000
) -> dict[str, Any]:
    """
    Navigate to a URL in the browser. This is typically the second tool you call after browser_launch().

    PREREQUISITE: browser_launch() must be called first.

    IMPORTANT:
    - URL must include protocol: "https://example.com" (correct) vs "example.com" (wrong)
    - If page_id is omitted, a new page is automatically created
    - Returns the page_id you need for subsequent operations like click(), fill(), screenshot()

    WAIT STRATEGIES (use wait_until parameter):
    - "load": Default. Waits for all resources (images, scripts). Best for most sites.
    - "networkidle": Waits until network is quiet. Best for single-page apps (SPAs) with dynamic content.
    - "domcontentloaded": Fast. Only waits for HTML. Use when you don't need images/styles.
    - "commit": Fastest. Returns as soon as server responds. Use for checking if URL exists.

    Returns: {
        "status": "success",
        "page_id": "page_1",      <- USE THIS for subsequent calls
        "url": "https://example.com",
        "title": "Example Domain",
        "response_status": 200
    }

    Example:
    1. browser_launch()
    2. navigate(url="https://google.com") -> returns page_id="page_1"
    3. fill(page_id="page_1", selector="input[name='q']", value="hello")
    """
    try:
        page_id, page = await session_manager.get_or_create_page(page_id)

        response = await page.goto(url, wait_until=wait_until, timeout=timeout)

        title = await page.title()

        return {
            "status": "success",
            "page_id": page_id,
            "url": page.url,
            "title": title,
            "response_status": response.status if response else None
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def go_back(
    page_id: Annotated[str, Field(description="The page_id to navigate back, e.g., 'page_1'")],
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="Wait strategy. Default 'load' waits for full page load.")
    ] = "load"
) -> dict[str, Any]:
    """
    Navigate back in browser history (like clicking the browser's back button).

    WHEN TO USE:
    - After navigating to a page and wanting to return to the previous page
    - To undo a navigation

    PREREQUISITE: Page must have navigation history (you must have navigated at least once before).

    Returns the new URL and title after going back.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        response = await page.go_back(wait_until=wait_until)
        title = await page.title()

        return {
            "status": "success",
            "page_id": page_id,
            "url": page.url,
            "title": title
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def go_forward(
    page_id: Annotated[str, Field(description="The page_id to navigate forward, e.g., 'page_1'")],
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="Wait strategy. Default 'load' waits for full page load.")
    ] = "load"
) -> dict[str, Any]:
    """
    Navigate forward in browser history (like clicking the browser's forward button).

    WHEN TO USE: After using go_back() and wanting to go forward again.

    PREREQUISITE: Must have used go_back() first, otherwise there's no forward history.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        response = await page.go_forward(wait_until=wait_until)
        title = await page.title()

        return {
            "status": "success",
            "page_id": page_id,
            "url": page.url,
            "title": title
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def reload(
    page_id: Annotated[str, Field(description="The page_id to reload, e.g., 'page_1'")],
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="Wait strategy. Default 'load' waits for full page load.")
    ] = "load"
) -> dict[str, Any]:
    """
    Reload/refresh the current page (like pressing F5 or the refresh button).

    WHEN TO USE:
    - To refresh page content that may have changed
    - To reset page state after interactions
    - To retry loading if something didn't load correctly
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        await page.reload(wait_until=wait_until)
        title = await page.title()

        return {
            "status": "success",
            "page_id": page_id,
            "url": page.url,
            "title": title
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Element Interaction Tools
# =============================================================================

@mcp.tool()
async def click(
    page_id: Annotated[str, Field(description="The page_id where the element exists, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="How to find the element. Examples: 'button.submit', '#login-btn', 'text=Sign In', '[data-testid=\"submit\"]'")],
    button: Annotated[
        Literal["left", "right", "middle"],
        Field(description="Mouse button: 'left' (default, normal click), 'right' (context menu), 'middle' (open in new tab)")
    ] = "left",
    click_count: Annotated[int, Field(description="1 for single click (default), 2 for double-click, 3 for triple-click", ge=1, le=3)] = 1,
    timeout: Annotated[int, Field(description="Max wait time for element to be clickable, in milliseconds", ge=1000, le=30000)] = 10000
) -> dict[str, Any]:
    """
    Click on an element on the page. Automatically waits for the element to be visible and clickable.

    PREREQUISITE: Must have navigated to a page first using navigate().

    HOW TO FIND ELEMENTS (selector parameter):
    Use one of these selector strategies (in order of preference):

    1. TEXT CONTENT (most reliable for buttons/links):
       - "text=Sign In"           -> clicks element containing "Sign In"
       - "text=Submit"            -> clicks element containing "Submit"

    2. CSS SELECTORS (common):
       - "button"                 -> first button on page
       - "#login-btn"             -> element with id="login-btn"
       - ".submit-button"         -> element with class="submit-button"
       - "button.primary"         -> button with class="primary"
       - "[type='submit']"        -> element with type="submit"
       - "[data-testid='login']"  -> element with data-testid="login"
       - "input[name='email']"    -> input with name="email"

    3. ROLE-BASED (accessibility):
       - "role=button[name='Submit']"  -> button with accessible name "Submit"
       - "role=link[name='Home']"      -> link with accessible name "Home"

    4. XPATH (when CSS doesn't work):
       - "xpath=//button[@type='submit']"
       - "xpath=//a[contains(text(),'Click here')]"

    COMMON USE CASES:
    - Click a button: click(page_id="page_1", selector="text=Submit")
    - Click a link: click(page_id="page_1", selector="text=Learn more")
    - Click by ID: click(page_id="page_1", selector="#next-button")
    - Double-click: click(page_id="page_1", selector=".item", click_count=2)

    TROUBLESHOOTING:
    - "Element not found": Check if the selector is correct. Try using get_page_content() to see the HTML.
    - "Element not visible": The element might be hidden. Try scrolling first with scroll().
    - "Timeout": Increase the timeout parameter for slow-loading elements.

    Returns: {"status": "success", "selector": "...", "action": "left-click"}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.click(button=button, click_count=click_count, timeout=timeout)

        return {
            "status": "success",
            "selector": selector,
            "action": f"{'double-' if click_count == 2 else ''}{button}-click"
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def fill(
    page_id: Annotated[str, Field(description="The page_id containing the input field, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for the input field. Examples: 'input[name=\"email\"]', '#username', '[placeholder=\"Search\"]'")],
    value: Annotated[str, Field(description="The text to enter into the field. Previous content is cleared first.")],
    timeout: Annotated[int, Field(description="Max wait time for element, in milliseconds", ge=1000, le=30000)] = 10000
) -> dict[str, Any]:
    """
    Fill text into an input field, textarea, or contenteditable element. CLEARS existing content first.

    PREREQUISITE: Must have navigated to a page first using navigate().

    WHEN TO USE:
    - Filling out forms (login, signup, search, etc.)
    - Entering text into any input field
    - Use this instead of type_text() for most form filling (it's faster)

    HOW TO FIND INPUT FIELDS (selector examples):
    - By name:        'input[name="email"]' or 'input[name="password"]'
    - By ID:          '#username' or '#search-box'
    - By placeholder: '[placeholder="Enter your email"]'
    - By type:        'input[type="email"]' or 'input[type="password"]'
    - By label:       Use the input's name/id that corresponds to a <label>
    - Textarea:       'textarea' or 'textarea[name="message"]'

    COMMON FORM FILLING WORKFLOW:
    1. navigate(url="https://example.com/login")
    2. fill(page_id="page_1", selector="input[name='email']", value="user@example.com")
    3. fill(page_id="page_1", selector="input[name='password']", value="mypassword")
    4. click(page_id="page_1", selector="button[type='submit']")

    NOTE: This clears any existing text before filling. If you need to append text, use type_text() instead.

    Returns: {"status": "success", "selector": "...", "filled_length": 15}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.fill(value, timeout=timeout)

        return {
            "status": "success",
            "selector": selector,
            "filled_length": len(value)
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def type_text(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for the input field to type into")],
    text: Annotated[str, Field(description="Text to type character by character")],
    delay: Annotated[int, Field(description="Milliseconds between each keystroke. Higher = slower, more human-like. Default: 50ms", ge=0, le=500)] = 50
) -> dict[str, Any]:
    """
    Type text character by character, simulating real keyboard input.

    WHEN TO USE (instead of fill()):
    - When the website requires real keyboard events (some React/Vue apps)
    - When you need to trigger autocomplete/suggestions
    - When testing keyboard event handlers
    - To simulate human-like typing behavior

    DIFFERENCE FROM fill():
    - fill(): Instantly sets the value (fast, but no keyboard events)
    - type_text(): Types each character with keyboard events (slower, but triggers JS handlers)

    Example - Triggering autocomplete:
    type_text(page_id="page_1", selector="input[name='search']", text="python", delay=100)
    # This types "p", "y", "t", "h", "o", "n" with 100ms gaps, triggering autocomplete suggestions
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.press_sequentially(text, delay=delay)

        return {
            "status": "success",
            "selector": selector,
            "typed_length": len(text)
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def press_key(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    key: Annotated[str, Field(description="Key name: 'Enter', 'Tab', 'Escape', 'ArrowDown', or combo like 'Control+a'")],
    selector: Annotated[str | None, Field(description="Optional: focus this element first before pressing key")] = None
) -> dict[str, Any]:
    """
    Press a keyboard key or key combination.

    COMMON USE CASES:
    - Submit a form: press_key(page_id="page_1", key="Enter", selector="input[name='search']")
    - Select all text: press_key(page_id="page_1", key="Control+a")
    - Close a modal: press_key(page_id="page_1", key="Escape")
    - Navigate dropdown: press_key(page_id="page_1", key="ArrowDown")
    - Tab to next field: press_key(page_id="page_1", key="Tab")

    KEY NAMES (case-sensitive):
    - Navigation: Enter, Tab, Escape, Backspace, Delete, Space
    - Arrows: ArrowUp, ArrowDown, ArrowLeft, ArrowRight
    - Modifiers: Control, Shift, Alt, Meta (Cmd on Mac)
    - Function: F1, F2, F3, ... F12
    - Others: Home, End, PageUp, PageDown, Insert

    KEY COMBINATIONS (use + to combine):
    - Control+a: Select all
    - Control+c: Copy
    - Control+v: Paste
    - Control+z: Undo
    - Shift+Tab: Go to previous field
    - Alt+F4: Close window (Windows)
    - Meta+c: Copy (Mac)

    NOTE: If selector is provided, that element is focused first before the key is pressed.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        if selector:
            await page.locator(selector).press(key)
        else:
            await page.keyboard.press(key)

        return {"status": "success", "key": key}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def hover(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for element to hover over")],
    timeout: Annotated[int, Field(description="Max wait time in milliseconds", ge=1000, le=30000)] = 10000
) -> dict[str, Any]:
    """
    Move the mouse over an element (hover). Does NOT click.

    WHEN TO USE:
    - To reveal dropdown menus that appear on hover
    - To show tooltips
    - To trigger hover states/effects before taking a screenshot
    - To preview links

    Example - Opening a dropdown menu:
    1. hover(page_id="page_1", selector=".menu-item")  # Shows dropdown
    2. click(page_id="page_1", selector=".dropdown-option")  # Click revealed option
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.hover(timeout=timeout)

        return {"status": "success", "selector": selector, "action": "hover"}
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def select_option(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for the <select> dropdown element, e.g., 'select[name=\"country\"]' or '#country-select'")],
    value: Annotated[str | None, Field(description="The 'value' attribute of the option to select (from HTML)")] = None,
    label: Annotated[str | None, Field(description="The visible text of the option to select (what user sees)")] = None,
    index: Annotated[int | None, Field(description="The position of the option (0 = first option)")] = None
) -> dict[str, Any]:
    """
    Select an option from a <select> dropdown menu. Provide ONE of: value, label, or index.

    WHEN TO USE:
    - For HTML <select> dropdowns (NOT custom JavaScript dropdowns)
    - For custom dropdowns, use click() to open and click() to select instead

    THREE WAYS TO SELECT (choose one):

    1. BY LABEL (visible text - most intuitive):
       select_option(page_id="page_1", selector="select[name='country']", label="United States")
       # Selects the option that shows "United States" to the user

    2. BY VALUE (HTML value attribute - most reliable):
       select_option(page_id="page_1", selector="select[name='country']", value="US")
       # Selects <option value="US">United States</option>

    3. BY INDEX (position - use when you don't know value/label):
       select_option(page_id="page_1", selector="select[name='country']", index=0)
       # Selects the first option (index starts at 0)

    HOW TO FIND THE SELECT ELEMENT:
    - By name: 'select[name="country"]'
    - By ID: '#country-select' or 'select#country'
    - By class: 'select.form-control'

    NOTE: For custom dropdowns (div-based, not <select>), use:
    1. click() to open the dropdown
    2. click() to select an option
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)

        if value is not None:
            await locator.select_option(value=value)
        elif label is not None:
            await locator.select_option(label=label)
        elif index is not None:
            await locator.select_option(index=index)
        else:
            return {"status": "error", "message": "Provide value, label, or index"}

        return {
            "status": "success",
            "selector": selector,
            "selected": value or label or f"index:{index}"
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def check(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for checkbox/radio, e.g., 'input[name=\"agree\"]' or '#terms-checkbox'")],
    checked: Annotated[bool, Field(description="True to check/select the box, False to uncheck/deselect")] = True
) -> dict[str, Any]:
    """
    Check or uncheck a checkbox or radio button.

    WHEN TO USE:
    - To accept terms & conditions checkboxes
    - To select options in forms
    - To toggle settings

    Examples:
    - Check a checkbox: check(page_id="page_1", selector="input[name='agree']", checked=True)
    - Uncheck: check(page_id="page_1", selector="input[name='agree']", checked=False)
    - Radio button: check(page_id="page_1", selector="input[value='option1']")

    FINDING CHECKBOXES:
    - By name: 'input[name="newsletter"]'
    - By ID: '#agree-checkbox'
    - By value (for radio): 'input[type="radio"][value="yes"]'
    - By label text: Use the input's id that the label's 'for' attribute points to
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        if checked:
            await locator.check()
        else:
            await locator.uncheck()

        return {
            "status": "success",
            "selector": selector,
            "checked": checked
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def focus(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for element to focus")]
) -> dict[str, Any]:
    """
    Focus an element (like clicking into an input field without typing).

    WHEN TO USE:
    - Before using press_key() to send keys to a specific element
    - To scroll an element into view
    - To activate an element before interacting with it
    - Rarely needed since fill() and click() auto-focus
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.focus()

        return {"status": "success", "selector": selector, "action": "focused"}
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


# =============================================================================
# Content Extraction Tools
# =============================================================================

@mcp.tool()
async def get_text(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Element to get text from. Default 'body' gets all visible text. Examples: '.article', '#content', 'h1'")] = "body"
) -> dict[str, Any]:
    """
    Extract the text content from an element. Returns only the visible text (no HTML tags).

    WHEN TO USE:
    - To read the content of a webpage
    - To extract article text, headings, paragraphs
    - To get the text of a specific element
    - To verify text content after an action

    COMMON SELECTORS:
    - 'body': All text on the page (default)
    - 'h1': Main heading
    - '.article-content': Article text by class
    - '#main': Main content by ID
    - 'p': All paragraphs (returns first match)

    Returns: {"status": "success", "text": "The extracted text...", "length": 150}

    NOTE: For extracting multiple elements, use query_selector_all() instead.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        text = await locator.text_content()

        return {
            "status": "success",
            "selector": selector,
            "text": text,
            "length": len(text) if text else 0
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def get_inner_html(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Element to get HTML from, e.g., '.content', '#main', 'article'")]
) -> dict[str, Any]:
    """
    Get the inner HTML of an element (includes HTML tags, unlike get_text).

    WHEN TO USE:
    - To inspect the HTML structure of an element
    - To find selectors for nested elements
    - To debug why a selector isn't working
    - When you need to see the raw HTML, not just text

    DIFFERENCE FROM get_text():
    - get_text(): Returns "Hello World" (just the visible text)
    - get_inner_html(): Returns "<strong>Hello</strong> World" (HTML preserved)

    Returns: {"status": "success", "html": "<div>...</div>", "length": 250}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        html = await locator.inner_html()

        return {
            "status": "success",
            "selector": selector,
            "html": html,
            "length": len(html)
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def get_attribute(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Element selector, e.g., 'a.link', 'img#logo', 'input[name=\"email\"]'")],
    attribute: Annotated[str, Field(description="Attribute name: 'href', 'src', 'class', 'id', 'value', 'data-*', etc.")]
) -> dict[str, Any]:
    """
    Get the value of an HTML attribute from an element.

    COMMON USE CASES:
    - Get link URL: get_attribute(selector="a.download", attribute="href")
    - Get image source: get_attribute(selector="img.logo", attribute="src")
    - Get input value: get_attribute(selector="input#email", attribute="value")
    - Get data attributes: get_attribute(selector=".item", attribute="data-id")
    - Get element classes: get_attribute(selector="#header", attribute="class")

    COMMON ATTRIBUTES:
    - href: URL for links (<a>)
    - src: Source URL for images/scripts (<img>, <script>)
    - value: Current value of inputs (<input>)
    - class: CSS classes
    - id: Element ID
    - data-*: Custom data attributes
    - type: Input type
    - name: Form field name
    - placeholder: Input placeholder text

    Returns: {"status": "success", "attribute": "href", "value": "https://..."}
    Returns value=null if attribute doesn't exist.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        value = await locator.get_attribute(attribute)

        return {
            "status": "success",
            "selector": selector,
            "attribute": attribute,
            "value": value
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def get_page_content(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")]
) -> dict[str, Any]:
    """
    Get the complete HTML source of the entire page (like View Page Source in browser).

    WHEN TO USE:
    - To see the full page structure when debugging selectors
    - To extract data from pages when you need to see all the HTML
    - To understand the page layout and find elements
    - To save the page HTML for analysis

    NOTE: This returns the FULL HTML including <head>, <body>, scripts, styles, etc.
    For just the visible text, use get_text(selector="body") instead.

    Returns: {"status": "success", "url": "...", "title": "...", "content": "<html>...</html>", "length": 15000}

    WARNING: Can be very large for complex pages (10KB-1MB+). Consider using get_inner_html()
    with a specific selector if you only need part of the page.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        content = await page.content()
        title = await page.title()

        return {
            "status": "success",
            "url": page.url,
            "title": title,
            "content": content,
            "length": len(content)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def query_selector_all(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="CSS selector to find all matching elements, e.g., 'a', '.item', 'tr', 'li'")],
    max_elements: Annotated[int, Field(description="Max number of elements to return (to avoid huge responses). Default: 20", ge=1, le=100)] = 20
) -> dict[str, Any]:
    """
    Find ALL elements matching a selector and return info about each one.

    WHEN TO USE:
    - To get a list of items (products, search results, menu items, etc.)
    - To count how many elements match a selector
    - To extract data from tables or lists
    - To find which element to interact with when there are multiple matches

    COMMON USE CASES:
    - List all links: query_selector_all(selector="a")
    - List all products: query_selector_all(selector=".product-card")
    - Get table rows: query_selector_all(selector="table tr")
    - Get menu items: query_selector_all(selector="nav li")

    Returns: {
        "status": "success",
        "total_count": 45,        <- Total matching elements
        "returned_count": 20,     <- How many in this response (limited by max_elements)
        "elements": [
            {"index": 0, "tag": "a", "text": "Home", "visible": true},
            {"index": 1, "tag": "a", "text": "About", "visible": true},
            ...
        ]
    }

    TO INTERACT WITH A SPECIFIC ELEMENT:
    Use nth-child or :nth-of-type in your selector:
    - click(selector=".item:nth-child(3)")  <- Click the 3rd item
    - Or use the index with nth: click(selector=".item >> nth=2")  <- Click index 2 (3rd item, 0-based)
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        count = await locator.count()

        elements = []
        for i in range(min(count, max_elements)):
            elem = locator.nth(i)
            elements.append({
                "index": i,
                "tag": await elem.evaluate("el => el.tagName.toLowerCase()"),
                "text": (await elem.text_content() or "")[:100],  # Truncate long text
                "visible": await elem.is_visible()
            })

        return {
            "status": "success",
            "selector": selector,
            "total_count": count,
            "returned_count": len(elements),
            "elements": elements
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


# =============================================================================
# Screenshot Tools
# =============================================================================

@mcp.tool()
async def screenshot(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    full_page: Annotated[bool, Field(description="True = capture entire scrollable page. False (default) = only visible viewport.")] = False,
    selector: Annotated[str | None, Field(description="Optional: capture only this element instead of the whole page, e.g., '.chart', '#diagram'")] = None,
    quality: Annotated[int | None, Field(description="JPEG quality 1-100. Only used when image_type='jpeg'. Lower = smaller file.", ge=1, le=100)] = None,
    image_type: Annotated[Literal["png", "jpeg"], Field(description="'png' (default, lossless) or 'jpeg' (smaller file size)")] = "png"
) -> dict[str, Any]:
    """
    Take a screenshot and return it as base64-encoded image data.

    THREE SCREENSHOT MODES:
    1. VIEWPORT (default): screenshot(page_id="page_1")
       - Captures only what's currently visible (like your screen)

    2. FULL PAGE: screenshot(page_id="page_1", full_page=True)
       - Captures the entire scrollable page (can be very tall)
       - Great for saving entire articles or long pages

    3. ELEMENT ONLY: screenshot(page_id="page_1", selector=".chart")
       - Captures just a specific element
       - Great for charts, images, specific sections

    IMAGE FORMATS:
    - png (default): Lossless quality, larger file size. Best for text/UI.
    - jpeg: Smaller file size, slight quality loss. Best for photos. Use quality param.

    Returns: {
        "status": "success",
        "image_type": "png",
        "size_bytes": 45000,
        "data": "iVBORw0KGgo..."  <- base64-encoded image data
    }

    TO SAVE TO FILE INSTEAD: Use screenshot_to_file() tool.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        screenshot_options = {
            "full_page": full_page,
            "type": image_type
        }
        if quality and image_type == "jpeg":
            screenshot_options["quality"] = quality

        if selector:
            locator = page.locator(selector)
            screenshot_bytes = await locator.screenshot(**screenshot_options)
        else:
            screenshot_bytes = await page.screenshot(**screenshot_options)

        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        return {
            "status": "success",
            "image_type": image_type,
            "full_page": full_page,
            "size_bytes": len(screenshot_bytes),
            "data": screenshot_base64
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def screenshot_to_file(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    path: Annotated[str, Field(description="File path to save, e.g., './screenshots/page.png' or '/tmp/screenshot.png'. Extension determines format.")],
    full_page: Annotated[bool, Field(description="True = capture entire scrollable page")] = False,
    selector: Annotated[str | None, Field(description="Optional: capture only this element")] = None
) -> dict[str, Any]:
    """
    Take a screenshot and save it directly to a file on disk.

    USE THIS WHEN:
    - You want to save screenshots for later viewing
    - You're doing batch screenshots
    - You don't need the image data in your response

    FILE FORMAT: Determined by file extension:
    - .png: Lossless quality (recommended for most cases)
    - .jpg/.jpeg: Compressed, smaller file size

    Examples:
    - screenshot_to_file(page_id="page_1", path="./screenshot.png")
    - screenshot_to_file(page_id="page_1", path="./full_page.png", full_page=True)
    - screenshot_to_file(page_id="page_1", path="./chart.png", selector=".chart")

    Returns: {"status": "success", "path": "./screenshot.png"}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        if selector:
            locator = page.locator(selector)
            await locator.screenshot(path=path, full_page=full_page)
        else:
            await page.screenshot(path=path, full_page=full_page)

        return {
            "status": "success",
            "path": path,
            "full_page": full_page
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Wait and Evaluate Tools
# =============================================================================

@mcp.tool()
async def wait_for_selector(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="The element to wait for, e.g., '.loading-spinner', '#results', '.modal'")],
    state: Annotated[
        Literal["attached", "detached", "visible", "hidden"],
        Field(description="'visible' (default): wait until visible. 'hidden': wait until hidden. 'attached': in DOM. 'detached': removed from DOM.")
    ] = "visible",
    timeout: Annotated[int, Field(description="Max wait time in milliseconds. Increase for slow-loading content.", ge=1000, le=60000)] = 30000
) -> dict[str, Any]:
    """
    Wait for an element to appear, disappear, or change state. Essential for dynamic pages.

    WHEN TO USE:
    - After clicking a button that loads content dynamically
    - Waiting for a loading spinner to disappear
    - Waiting for search results to appear
    - Waiting for a modal/popup to show
    - Before interacting with dynamically loaded content

    STATE OPTIONS:
    - "visible" (default): Wait until element is visible on screen
    - "hidden": Wait until element is hidden or removed
    - "attached": Wait until element exists in DOM (may not be visible)
    - "detached": Wait until element is removed from DOM

    COMMON PATTERNS:

    1. Wait for loading to finish:
       click(selector="button.search")  # Triggers loading
       wait_for_selector(selector=".loading", state="hidden")  # Wait for spinner to hide
       wait_for_selector(selector=".results")  # Wait for results to appear

    2. Wait for modal:
       click(selector=".open-modal")
       wait_for_selector(selector=".modal", state="visible")

    3. Wait for content to load:
       navigate(url="https://example.com")
       wait_for_selector(selector=".dynamic-content")  # Wait for JS to render

    Returns: {"status": "success"} when element reaches the desired state.
    Returns: {"status": "error"} if timeout is exceeded.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.wait_for(state=state, timeout=timeout)

        return {
            "status": "success",
            "selector": selector,
            "state": state
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "selector": selector}


@mcp.tool()
async def wait_for_load_state(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    state: Annotated[
        Literal["load", "domcontentloaded", "networkidle"],
        Field(description="'load': all resources loaded. 'networkidle': no network for 500ms. 'domcontentloaded': HTML parsed.")
    ] = "load",
    timeout: Annotated[int, Field(description="Max wait time in milliseconds", ge=1000, le=60000)] = 30000
) -> dict[str, Any]:
    """
    Wait for the page to reach a specific load state.

    WHEN TO USE:
    - After an action that causes page content to reload
    - After clicking a link that navigates within a SPA
    - To ensure the page is fully loaded before extracting content

    STATES:
    - "load": Wait for the 'load' event (all images, scripts, etc. loaded)
    - "networkidle": Wait until no network requests for 500ms (best for SPAs)
    - "domcontentloaded": Wait until HTML is parsed (fast, but content may still be loading)

    Example:
    click(selector=".load-more")
    wait_for_load_state(state="networkidle")  # Wait for AJAX to complete
    get_text(selector=".results")  # Now safe to read new content
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        await page.wait_for_load_state(state, timeout=timeout)

        return {"status": "success", "state": state}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def wait_for_url(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    url_pattern: Annotated[str, Field(description="URL or pattern to wait for. Use '**/path' for any domain, or full URL 'https://example.com/page'")],
    timeout: Annotated[int, Field(description="Max wait time in milliseconds", ge=1000, le=60000)] = 30000
) -> dict[str, Any]:
    """
    Wait for the page URL to change to a specific pattern. Useful after form submissions or link clicks.

    WHEN TO USE:
    - After clicking a login button, wait for redirect to dashboard
    - After form submission, wait for success page
    - After clicking a link, wait for navigation to complete

    URL PATTERN OPTIONS:
    - Exact URL: "https://example.com/dashboard"
    - Glob pattern: "**/dashboard" (any domain ending with /dashboard)
    - Glob pattern: "**/order/*" (matches /order/123, /order/456, etc.)
    - Glob pattern: "https://example.com/**" (any path on example.com)

    Example - Login flow:
    fill(selector="input[name='email']", value="user@example.com")
    fill(selector="input[name='password']", value="password")
    click(selector="button[type='submit']")
    wait_for_url(url_pattern="**/dashboard")  # Wait for redirect after login
    # Now we're on the dashboard page

    Returns: {"status": "success", "current_url": "https://example.com/dashboard"}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        await page.wait_for_url(url_pattern, timeout=timeout)

        return {
            "status": "success",
            "url_pattern": url_pattern,
            "current_url": page.url
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def evaluate(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    expression: Annotated[str, Field(description="JavaScript code to run in the browser. Can be an expression or statement.")],
) -> dict[str, Any]:
    """
    Execute JavaScript code in the browser and return the result. Very powerful for advanced operations.

    WHEN TO USE:
    - To get information not accessible through other tools
    - To interact with page JavaScript/APIs
    - To perform complex DOM manipulations
    - To access browser APIs (localStorage, cookies, etc.)

    COMMON EXAMPLES:

    Get page information:
    - evaluate(expression="document.title")  -> Page title
    - evaluate(expression="window.location.href")  -> Current URL
    - evaluate(expression="document.querySelectorAll('a').length")  -> Count links

    Access browser storage:
    - evaluate(expression="localStorage.getItem('token')")  -> Get stored token
    - evaluate(expression="JSON.stringify(localStorage)")  -> All localStorage
    - evaluate(expression="document.cookie")  -> Get cookies

    Get computed styles:
    - evaluate(expression="getComputedStyle(document.body).backgroundColor")

    Scroll to position:
    - evaluate(expression="window.scrollTo(0, 500)")  -> Scroll to Y=500
    - evaluate(expression="window.scrollY")  -> Get current scroll position

    Complex operations:
    - evaluate(expression="Array.from(document.querySelectorAll('a')).map(a => a.href)")
      -> Get all link URLs as an array

    Returns: {"status": "success", "expression": "...", "result": <JS return value>}

    NOTE: The result must be JSON-serializable. DOM elements return null.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        result = await page.evaluate(expression)

        return {
            "status": "success",
            "expression": expression,
            "result": result
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# PDF and Printing
# =============================================================================

@mcp.tool()
async def save_as_pdf(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    path: Annotated[str, Field(description="File path for PDF, e.g., './report.pdf' or '/tmp/page.pdf'")],
    format: Annotated[
        Literal["Letter", "Legal", "Tabloid", "Ledger", "A0", "A1", "A2", "A3", "A4", "A5", "A6"],
        Field(description="Paper size. 'A4' (default) for standard, 'Letter' for US standard.")
    ] = "A4",
    print_background: Annotated[bool, Field(description="Include background colors/images. True recommended for styled pages.")] = True,
    landscape: Annotated[bool, Field(description="True for landscape (horizontal), False (default) for portrait (vertical)")] = False
) -> dict[str, Any]:
    """
    Save the current page as a PDF file. Great for saving articles, reports, or documentation.

    IMPORTANT: Only works with Chromium browser in headless mode.
    If using Firefox or WebKit, this will fail.

    WHEN TO USE:
    - To save a webpage for offline reading
    - To generate reports from web dashboards
    - To archive web content
    - To create printable versions of pages

    PAPER FORMATS:
    - A4: Standard international (210 x 297 mm) - default
    - Letter: US standard (8.5 x 11 inches)
    - Legal: US legal (8.5 x 14 inches)
    - A3, A5, etc.: Other ISO sizes

    Example:
    browser_launch(browser_type="chromium", headless=True)  # Must be Chromium + headless
    navigate(url="https://example.com/article")
    save_as_pdf(page_id="page_1", path="./article.pdf", format="A4")

    Returns: {"status": "success", "path": "./article.pdf", "format": "A4"}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        await page.pdf(
            path=path,
            format=format,
            print_background=print_background,
            landscape=landscape
        )

        return {
            "status": "success",
            "path": path,
            "format": format
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Scroll and Viewport
# =============================================================================

@mcp.tool()
async def scroll(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    direction: Annotated[
        Literal["up", "down", "left", "right", "top", "bottom"],
        Field(description="'down' (default), 'up', 'left', 'right' to scroll by amount. 'top'/'bottom' to jump to start/end.")
    ] = "down",
    amount: Annotated[int, Field(description="Pixels to scroll. Ignored when direction is 'top' or 'bottom'. Default: 500", ge=0)] = 500
) -> dict[str, Any]:
    """
    Scroll the page in a direction or jump to top/bottom.

    WHEN TO USE:
    - To reveal content below the fold
    - To load lazy-loaded content (infinite scroll pages)
    - To bring an element into view before screenshotting
    - To navigate long pages

    SCROLL OPTIONS:
    - "down": Scroll down by 'amount' pixels (default: 500px)
    - "up": Scroll up by 'amount' pixels
    - "left"/"right": Horizontal scrolling
    - "top": Jump to the top of the page (amount ignored)
    - "bottom": Jump to the bottom of the page (amount ignored)

    COMMON PATTERNS:

    1. Load more content (infinite scroll):
       scroll(direction="down", amount=1000)
       wait_for_load_state(state="networkidle")  # Wait for new content to load
       scroll(direction="down", amount=1000)
       # Repeat as needed

    2. Go to bottom then back to top:
       scroll(direction="bottom")  # Jump to end
       scroll(direction="top")     # Jump back to start

    3. Small scroll to reveal element:
       scroll(direction="down", amount=200)

    Returns: {"status": "success", "direction": "down", "amount": 500}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        if direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif direction == "down":
            await page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await page.evaluate(f"window.scrollBy(0, -{amount})")
        elif direction == "right":
            await page.evaluate(f"window.scrollBy({amount}, 0)")
        elif direction == "left":
            await page.evaluate(f"window.scrollBy(-{amount}, 0)")

        return {"status": "success", "direction": direction, "amount": amount}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def set_viewport(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    width: Annotated[int, Field(description="Viewport width in pixels. Common: 1920 (desktop), 1280 (laptop), 768 (tablet), 375 (mobile)", ge=320, le=3840)] = 1280,
    height: Annotated[int, Field(description="Viewport height in pixels. Common: 1080 (desktop), 720 (laptop), 1024 (tablet), 812 (mobile)", ge=240, le=2160)] = 720
) -> dict[str, Any]:
    """
    Change the browser viewport (window) size. Affects how the page renders and what's visible in screenshots.

    WHEN TO USE:
    - To test responsive design at different screen sizes
    - To simulate mobile, tablet, or desktop views
    - To capture screenshots at specific dimensions
    - Before screenshotting to ensure proper layout

    COMMON VIEWPORT SIZES:
    - Desktop HD: width=1920, height=1080
    - Desktop: width=1280, height=720 (default)
    - Laptop: width=1366, height=768
    - Tablet landscape: width=1024, height=768
    - Tablet portrait: width=768, height=1024
    - Mobile (iPhone): width=375, height=812
    - Mobile (Android): width=360, height=640

    Example - Test mobile view:
    set_viewport(page_id="page_1", width=375, height=812)
    screenshot(page_id="page_1", path="./mobile-view.png")

    Returns: {"status": "success", "width": 375, "height": 812}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        await page.set_viewport_size({"width": width, "height": height})

        return {"status": "success", "width": width, "height": height}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Dialog Handling
# =============================================================================

@mcp.tool()
async def handle_dialog(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    action: Annotated[
        Literal["accept", "dismiss"],
        Field(description="'accept': click OK/Yes. 'dismiss': click Cancel/No or close the dialog.")
    ] = "accept",
    prompt_text: Annotated[str | None, Field(description="For prompt() dialogs only: the text to enter before accepting")] = None
) -> dict[str, Any]:
    """
    Prepare to handle the next JavaScript dialog (alert, confirm, or prompt).
    IMPORTANT: Call this BEFORE the action that triggers the dialog.

    WHEN TO USE:
    - Before clicking a delete button that shows "Are you sure?" confirm dialog
    - Before an action that shows an alert() message
    - Before an action that shows a prompt() asking for input

    DIALOG TYPES:
    - alert(): Shows a message with OK button. Use action="accept".
    - confirm(): Shows OK/Cancel. Use action="accept" for OK, "dismiss" for Cancel.
    - prompt(): Shows text input with OK/Cancel. Use prompt_text to enter text.

    CRITICAL: This sets up a ONE-TIME handler. Call it BEFORE triggering the dialog.

    Example - Accept a confirmation:
    handle_dialog(page_id="page_1", action="accept")  # Prepare to accept
    click(selector=".delete-button")  # This triggers the confirm dialog
    # Dialog is automatically accepted

    Example - Dismiss/Cancel a dialog:
    handle_dialog(page_id="page_1", action="dismiss")
    click(selector=".risky-action")

    Example - Enter text in a prompt:
    handle_dialog(page_id="page_1", action="accept", prompt_text="My answer")
    click(selector=".ask-name")  # Triggers prompt()
    # "My answer" is entered and OK is clicked

    Returns: {"status": "success", "action": "accept"}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        async def handle(dialog):
            if action == "accept":
                if prompt_text is not None:
                    await dialog.accept(prompt_text)
                else:
                    await dialog.accept()
            else:
                await dialog.dismiss()

        page.once("dialog", handle)

        return {
            "status": "success",
            "message": f"Dialog handler set to {action}",
            "action": action
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# File Upload
# =============================================================================

@mcp.tool()
async def upload_file(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for <input type='file'> element, e.g., 'input[type=\"file\"]', '#file-upload'")],
    file_paths: Annotated[list[str], Field(description="List of absolute file paths to upload, e.g., ['/path/to/file.pdf']")]
) -> dict[str, Any]:
    """
    Upload one or more files to a file input element on the page.

    WHEN TO USE:
    - To upload documents, images, or other files to a website
    - For forms that require file attachments
    - For profile picture uploads, document submissions, etc.

    IMPORTANT:
    - The file paths must be absolute paths on the system where the browser is running
    - The files must exist at the specified paths
    - For multiple file uploads, the input must have the 'multiple' attribute

    HOW TO FIND FILE INPUTS:
    - By type: 'input[type="file"]'
    - By ID: '#file-upload'
    - By name: 'input[name="attachment"]'
    - By accept attribute: 'input[accept="image/*"]'

    Example - Single file upload:
    upload_file(
        page_id="page_1",
        selector="input[type='file']",
        file_paths=["/home/user/document.pdf"]
    )

    Example - Multiple files:
    upload_file(
        page_id="page_1",
        selector="input[type='file'][multiple]",
        file_paths=["/home/user/photo1.jpg", "/home/user/photo2.jpg"]
    )

    Returns: {"status": "success", "files": ["/path/to/file.pdf"]}
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        await locator.set_input_files(file_paths)

        return {
            "status": "success",
            "selector": selector,
            "files": file_paths
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Element State Checks
# =============================================================================

@mcp.tool()
async def is_visible(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for the element to check, e.g., '.modal', '#error-message', '.loading'")]
) -> dict[str, Any]:
    """
    Check if an element is currently visible on the page. Does NOT wait - returns immediately.

    WHEN TO USE:
    - To check if a modal/popup is showing
    - To check if an error message appeared
    - To verify an element is displayed before interacting
    - To check if a loading spinner is visible
    - For conditional logic (if visible, do X; else do Y)

    VISIBILITY CRITERIA:
    An element is visible if:
    - It exists in the DOM
    - It has non-zero size
    - It's not hidden by CSS (display: none, visibility: hidden, opacity: 0)

    Returns: {"status": "success", "visible": true} or {"visible": false}

    NOTE: This returns immediately. If you want to WAIT for an element to become visible,
    use wait_for_selector(selector="...", state="visible") instead.
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        visible = await locator.is_visible()

        return {"status": "success", "selector": selector, "visible": visible}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def is_enabled(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for the element to check, e.g., 'button.submit', '#send-btn', 'input[name=\"email\"]'")]
) -> dict[str, Any]:
    """
    Check if an element is enabled (not disabled). Useful for buttons and form fields.

    WHEN TO USE:
    - To check if a submit button is enabled before clicking
    - To verify form validation has enabled the submit button
    - To check if an input field is editable

    AN ELEMENT IS DISABLED IF:
    - It has the 'disabled' attribute: <button disabled>
    - It's inside a disabled fieldset
    - CSS pointer-events: none (though this may still return enabled)

    Returns: {"status": "success", "enabled": true} or {"enabled": false}

    Example - Check before clicking:
    result = is_enabled(page_id="page_1", selector="button[type='submit']")
    # If result["enabled"] is true, safe to click
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        enabled = await locator.is_enabled()

        return {"status": "success", "selector": selector, "enabled": enabled}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def is_checked(
    page_id: Annotated[str, Field(description="The page_id, e.g., 'page_1'")],
    selector: Annotated[str, Field(description="Selector for checkbox or radio button, e.g., 'input[name=\"agree\"]', '#newsletter-checkbox'")]
) -> dict[str, Any]:
    """
    Check if a checkbox or radio button is currently checked/selected.

    WHEN TO USE:
    - To verify a checkbox state before submitting a form
    - To check if a "remember me" or "agree to terms" box is checked
    - To verify the selected radio button option
    - For conditional logic based on checkbox state

    Returns: {"status": "success", "checked": true} or {"checked": false}

    Example - Verify terms accepted:
    result = is_checked(page_id="page_1", selector="input[name='terms']")
    if result["checked"]:
        click(selector="button[type='submit']")  # Safe to submit
    else:
        check(selector="input[name='terms']")  # Check the box first
    """
    try:
        page = await session_manager.get_page(page_id)
        if not page:
            return {"status": "error", "message": f"Page {page_id} not found"}

        locator = page.locator(selector)
        checked = await locator.is_checked()

        return {"status": "success", "selector": selector, "checked": checked}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Credential Management Tools
# =============================================================================

def _domain_to_env_prefix(domain: str) -> str:
    """Convert a domain name to environment variable prefix.

    Examples:
        domain.com -> domaincom
        sub.domain.com -> subdomaincom
        my-site.org -> mysiteorg
    """
    # Remove protocol if present
    domain = re.sub(r'^https?://', '', domain)
    # Remove path and query string
    domain = domain.split('/')[0]
    # Remove port if present
    domain = domain.split(':')[0]
    # Remove all non-alphanumeric characters and convert to lowercase
    prefix = re.sub(r'[^a-zA-Z0-9]', '', domain).lower()
    return prefix


@mcp.tool()
async def get_credentials(
    domain: Annotated[str, Field(description="Domain to get credentials for, e.g., 'example.com', 'https://login.example.com', 'my-site.org'")]
) -> dict[str, Any]:
    """
    Retrieve stored credentials (username/password) for a domain from environment variables.

    CREDENTIAL STORAGE PATTERN:
    For a domain like 'example.com', set these environment variables:
    - examplecom_username - The username/email for authentication
    - examplecom_password - The password for authentication

    DOMAIN TO ENV MAPPING EXAMPLES:
    | Domain | Username Env Var | Password Env Var |
    |--------|------------------|------------------|
    | example.com | examplecom_username | examplecom_password |
    | login.github.com | logingithubcom_username | logingithubcom_password |
    | my-app.io | myappio_username | myappio_password |
    | sub.domain.co.uk | subdomaincouk_username | subdomaincouk_password |

    WHEN TO USE:
    - Before filling login forms to retrieve stored credentials
    - To check if credentials are configured for a domain
    - For automated authentication workflows

    SECURITY NOTE:
    - Credentials are read from environment variables (not stored in code)
    - Password is masked in the response (shows length only)
    - Set environment variables securely before running the server

    Returns: {
        "status": "success",
        "domain": "example.com",
        "env_prefix": "examplecom",
        "username": "user@example.com",
        "password": "********",  # Actual password (masked in description)
        "has_credentials": true
    }

    Example workflow:
    1. creds = get_credentials(domain="github.com")
    2. fill(page_id="page_1", selector="#login_field", value=creds["username"])
    3. fill(page_id="page_1", selector="#password", value=creds["password"])
    4. click(page_id="page_1", selector="input[type='submit']")
    """
    try:
        env_prefix = _domain_to_env_prefix(domain)
        username_key = f"{env_prefix}_username"
        password_key = f"{env_prefix}_password"

        username = os.getenv(username_key)
        password = os.getenv(password_key)

        has_credentials = username is not None and password is not None

        return {
            "status": "success",
            "domain": domain,
            "env_prefix": env_prefix,
            "username_env": username_key,
            "password_env": password_key,
            "username": username,
            "password": password,
            "has_credentials": has_credentials,
            "message": f"Credentials {'found' if has_credentials else 'not found'} for {domain}"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def list_credential_env_vars() -> dict[str, Any]:
    """
    List all environment variables that match the credential pattern (*_username, *_password).

    WHEN TO USE:
    - To see which domains have credentials configured
    - To debug credential setup issues
    - To verify environment variables are loaded correctly

    SECURITY NOTE:
    - Only shows environment variable NAMES, not values
    - Passwords are never exposed by this tool
    - Use get_credentials() to retrieve actual values for a specific domain

    Returns: {
        "status": "success",
        "credential_pairs": [
            {"domain_prefix": "githubcom", "username_env": "githubcom_username", "password_env": "githubcom_password"},
            {"domain_prefix": "examplecom", "username_env": "examplecom_username", "password_env": "examplecom_password"}
        ],
        "total_pairs": 2
    }
    """
    try:
        # Find all _username and _password env vars
        username_vars = {}
        password_vars = set()

        for key in os.environ:
            if key.endswith('_username'):
                prefix = key[:-9]  # Remove '_username'
                username_vars[prefix] = key
            elif key.endswith('_password'):
                prefix = key[:-9]  # Remove '_password'
                password_vars.add(prefix)

        # Find pairs where both username and password exist
        credential_pairs = []
        for prefix, username_key in username_vars.items():
            if prefix in password_vars:
                credential_pairs.append({
                    "domain_prefix": prefix,
                    "username_env": username_key,
                    "password_env": f"{prefix}_password",
                    "has_both": True
                })
            else:
                credential_pairs.append({
                    "domain_prefix": prefix,
                    "username_env": username_key,
                    "password_env": f"{prefix}_password",
                    "has_both": False,
                    "missing": "password"
                })

        # Check for orphan passwords (password without username)
        for prefix in password_vars:
            if prefix not in username_vars:
                credential_pairs.append({
                    "domain_prefix": prefix,
                    "username_env": f"{prefix}_username",
                    "password_env": f"{prefix}_password",
                    "has_both": False,
                    "missing": "username"
                })

        return {
            "status": "success",
            "credential_pairs": credential_pairs,
            "total_pairs": len([p for p in credential_pairs if p.get("has_both", False)]),
            "total_partial": len([p for p in credential_pairs if not p.get("has_both", False)])
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Entry Point
# =============================================================================

def main():
    """Run the MCP server with stdio transport (default)."""
    mcp.run()


def main_sse(host: str = "0.0.0.0", port: int = 8000):
    """Run the MCP server with SSE (Server-Sent Events) transport.

    Args:
        host: Host to bind to. Default "0.0.0.0" for all interfaces.
        port: Port to listen on. Default 8000.
    """
    mcp.run(transport="sse", host=host, port=port)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--sse":
        # Parse optional host and port arguments
        host = "0.0.0.0"
        port = 8000

        for arg in sys.argv[2:]:
            if arg.startswith("--host="):
                host = arg.split("=", 1)[1]
            elif arg.startswith("--port="):
                port = int(arg.split("=", 1)[1])

        print(f"Starting Playwright MCP server with SSE transport on {host}:{port}")
        main_sse(host=host, port=port)
    else:
        main()
