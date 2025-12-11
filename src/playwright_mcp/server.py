"""
Playwright MCP Server

A comprehensive MCP server for browser automation using Playwright.
Built with FastMCP for seamless integration with Claude and other MCP clients.
"""

import base64
import json
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
        Field(description="Browser engine to use: chromium, firefox, or webkit")
    ] = "chromium",
    headless: Annotated[
        bool,
        Field(description="Run browser in headless mode (no visible UI)")
    ] = True
) -> dict[str, Any]:
    """
    Launch a browser instance. Call this before other browser operations.

    Supported browsers:
    - chromium: Chrome/Edge-based browser (fastest, best compatibility)
    - firefox: Mozilla Firefox
    - webkit: Safari-based browser
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
    Close the browser and clean up all resources.
    Call this when done with browser automation.
    """
    try:
        await session_manager.cleanup()
        return {"status": "success", "message": "Browser closed successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def page_new() -> dict[str, Any]:
    """
    Create a new browser tab/page.
    Returns a page_id to use with other page operations.
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
    page_id: Annotated[str, Field(description="ID of the page to close")]
) -> dict[str, Any]:
    """Close a specific browser page/tab."""
    try:
        closed = await session_manager.close_page(page_id)
        if closed:
            return {"status": "success", "message": f"Page {page_id} closed"}
        return {"status": "error", "message": f"Page {page_id} not found or already closed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def page_list() -> dict[str, Any]:
    """List all open browser pages with their URLs."""
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
    url: Annotated[str, Field(description="URL to navigate to (must include protocol, e.g., https://)")],
    page_id: Annotated[str | None, Field(description="Page ID. Creates new page if not specified")] = None,
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="When to consider navigation complete")
    ] = "load",
    timeout: Annotated[
        int,
        Field(description="Maximum time to wait in milliseconds", ge=1000, le=60000)
    ] = 30000
) -> dict[str, Any]:
    """
    Navigate to a URL in the browser.

    Wait strategies:
    - load: Wait for 'load' event (all resources loaded)
    - domcontentloaded: Wait for DOM to be ready
    - networkidle: Wait until no network requests for 500ms
    - commit: Wait for response received
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
    page_id: Annotated[str, Field(description="Page ID to navigate back")],
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="When to consider navigation complete")
    ] = "load"
) -> dict[str, Any]:
    """Navigate back in browser history."""
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
    page_id: Annotated[str, Field(description="Page ID to navigate forward")],
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="When to consider navigation complete")
    ] = "load"
) -> dict[str, Any]:
    """Navigate forward in browser history."""
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
    page_id: Annotated[str, Field(description="Page ID to reload")],
    wait_until: Annotated[
        Literal["load", "domcontentloaded", "networkidle", "commit"],
        Field(description="When to consider reload complete")
    ] = "load"
) -> dict[str, Any]:
    """Reload the current page."""
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
    page_id: Annotated[str, Field(description="Page ID where to click")],
    selector: Annotated[str, Field(description="CSS selector, XPath (prefix with xpath=), or text (prefix with text=)")],
    button: Annotated[
        Literal["left", "right", "middle"],
        Field(description="Mouse button to use")
    ] = "left",
    click_count: Annotated[int, Field(description="Number of clicks (2 for double-click)", ge=1, le=3)] = 1,
    timeout: Annotated[int, Field(description="Timeout in milliseconds", ge=1000, le=30000)] = 10000
) -> dict[str, Any]:
    """
    Click an element on the page.

    Selector examples:
    - CSS: "button.submit", "#login-btn", "[data-testid='submit']"
    - XPath: "xpath=//button[@type='submit']"
    - Text: "text=Sign In", "text=Click here"
    - Role: "role=button[name='Submit']"
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for input field")],
    value: Annotated[str, Field(description="Text to fill into the field")],
    timeout: Annotated[int, Field(description="Timeout in milliseconds", ge=1000, le=30000)] = 10000
) -> dict[str, Any]:
    """
    Fill text into an input field. This clears existing content first.
    Use for <input>, <textarea>, and contenteditable elements.
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for input field")],
    text: Annotated[str, Field(description="Text to type character by character")],
    delay: Annotated[int, Field(description="Delay between keystrokes in ms", ge=0, le=500)] = 50
) -> dict[str, Any]:
    """
    Type text character by character with optional delay.
    Use when you need to simulate real typing behavior.
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
    page_id: Annotated[str, Field(description="Page ID")],
    key: Annotated[str, Field(description="Key to press (e.g., Enter, Tab, Escape, ArrowDown, Control+a)")],
    selector: Annotated[str | None, Field(description="Optional selector to focus first")] = None
) -> dict[str, Any]:
    """
    Press a keyboard key or key combination.

    Key examples:
    - Single keys: Enter, Tab, Escape, Backspace, Delete
    - Arrow keys: ArrowUp, ArrowDown, ArrowLeft, ArrowRight
    - Modifiers: Control+a, Shift+Tab, Alt+F4, Meta+c (Cmd on Mac)
    - Function keys: F1, F2, etc.
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for element to hover over")],
    timeout: Annotated[int, Field(description="Timeout in milliseconds", ge=1000, le=30000)] = 10000
) -> dict[str, Any]:
    """Hover over an element (useful for dropdowns, tooltips)."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for <select> element")],
    value: Annotated[str | None, Field(description="Option value attribute")] = None,
    label: Annotated[str | None, Field(description="Option visible text")] = None,
    index: Annotated[int | None, Field(description="Option index (0-based)")] = None
) -> dict[str, Any]:
    """
    Select an option from a <select> dropdown.
    Provide one of: value, label, or index.
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for checkbox or radio button")],
    checked: Annotated[bool, Field(description="True to check, False to uncheck")] = True
) -> dict[str, Any]:
    """Check or uncheck a checkbox or radio button."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for element to focus")]
) -> dict[str, Any]:
    """Focus an element on the page."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for element to extract text from")] = "body"
) -> dict[str, Any]:
    """
    Get the text content of an element.
    Defaults to body to get all page text.
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for element")]
) -> dict[str, Any]:
    """Get the inner HTML of an element."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for element")],
    attribute: Annotated[str, Field(description="Attribute name to retrieve (e.g., href, src, class)")]
) -> dict[str, Any]:
    """Get the value of an element's attribute."""
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
    page_id: Annotated[str, Field(description="Page ID")]
) -> dict[str, Any]:
    """Get the full HTML content of the page."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="CSS selector to find elements")],
    max_elements: Annotated[int, Field(description="Maximum elements to return", ge=1, le=100)] = 20
) -> dict[str, Any]:
    """
    Find all elements matching a selector.
    Returns basic info about each matching element.
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
    page_id: Annotated[str, Field(description="Page ID")],
    full_page: Annotated[bool, Field(description="Capture full scrollable page")] = False,
    selector: Annotated[str | None, Field(description="Selector to screenshot specific element")] = None,
    quality: Annotated[int | None, Field(description="JPEG quality 1-100 (only for jpeg)", ge=1, le=100)] = None,
    image_type: Annotated[Literal["png", "jpeg"], Field(description="Image format")] = "png"
) -> dict[str, Any]:
    """
    Take a screenshot of the page or a specific element.
    Returns base64-encoded image data.
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
    page_id: Annotated[str, Field(description="Page ID")],
    path: Annotated[str, Field(description="File path to save screenshot (e.g., ./screenshot.png)")],
    full_page: Annotated[bool, Field(description="Capture full scrollable page")] = False,
    selector: Annotated[str | None, Field(description="Selector to screenshot specific element")] = None
) -> dict[str, Any]:
    """Save a screenshot directly to a file."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector to wait for")],
    state: Annotated[
        Literal["attached", "detached", "visible", "hidden"],
        Field(description="State to wait for")
    ] = "visible",
    timeout: Annotated[int, Field(description="Timeout in milliseconds", ge=1000, le=60000)] = 30000
) -> dict[str, Any]:
    """
    Wait for an element to reach a specific state.

    States:
    - visible: Wait until element is visible
    - hidden: Wait until element is hidden
    - attached: Wait until element is in DOM
    - detached: Wait until element is removed from DOM
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
    page_id: Annotated[str, Field(description="Page ID")],
    state: Annotated[
        Literal["load", "domcontentloaded", "networkidle"],
        Field(description="Load state to wait for")
    ] = "load",
    timeout: Annotated[int, Field(description="Timeout in milliseconds", ge=1000, le=60000)] = 30000
) -> dict[str, Any]:
    """Wait for the page to reach a specific load state."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    url_pattern: Annotated[str, Field(description="URL string or glob pattern (e.g., '**/login')")],
    timeout: Annotated[int, Field(description="Timeout in milliseconds", ge=1000, le=60000)] = 30000
) -> dict[str, Any]:
    """Wait for the page URL to match a pattern."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    expression: Annotated[str, Field(description="JavaScript expression to evaluate")]
) -> dict[str, Any]:
    """
    Evaluate JavaScript in the browser context.
    Returns the result of the expression.

    Examples:
    - "document.title"
    - "window.location.href"
    - "document.querySelectorAll('a').length"
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
    page_id: Annotated[str, Field(description="Page ID")],
    path: Annotated[str, Field(description="File path to save PDF")],
    format: Annotated[
        Literal["Letter", "Legal", "Tabloid", "Ledger", "A0", "A1", "A2", "A3", "A4", "A5", "A6"],
        Field(description="Paper format")
    ] = "A4",
    print_background: Annotated[bool, Field(description="Print background graphics")] = True,
    landscape: Annotated[bool, Field(description="Landscape orientation")] = False
) -> dict[str, Any]:
    """
    Save the page as a PDF file.
    Note: Only works with Chromium in headless mode.
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
    page_id: Annotated[str, Field(description="Page ID")],
    direction: Annotated[
        Literal["up", "down", "left", "right", "top", "bottom"],
        Field(description="Scroll direction or position")
    ] = "down",
    amount: Annotated[int, Field(description="Pixels to scroll (ignored for top/bottom)", ge=0)] = 500
) -> dict[str, Any]:
    """
    Scroll the page in a direction or to a position.

    Directions:
    - up/down/left/right: Scroll by specified amount
    - top/bottom: Scroll to top or bottom of page
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
    page_id: Annotated[str, Field(description="Page ID")],
    width: Annotated[int, Field(description="Viewport width in pixels", ge=320, le=3840)] = 1280,
    height: Annotated[int, Field(description="Viewport height in pixels", ge=240, le=2160)] = 720
) -> dict[str, Any]:
    """Set the browser viewport size."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    action: Annotated[
        Literal["accept", "dismiss"],
        Field(description="How to handle the next dialog")
    ] = "accept",
    prompt_text: Annotated[str | None, Field(description="Text to enter for prompt dialogs")] = None
) -> dict[str, Any]:
    """
    Set up handler for the next JavaScript dialog (alert, confirm, prompt).
    Call this BEFORE triggering the action that shows the dialog.
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for file input element")],
    file_paths: Annotated[list[str], Field(description="List of file paths to upload")]
) -> dict[str, Any]:
    """Upload files to a file input element."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector to check")]
) -> dict[str, Any]:
    """Check if an element is visible on the page."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector to check")]
) -> dict[str, Any]:
    """Check if an element is enabled (not disabled)."""
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
    page_id: Annotated[str, Field(description="Page ID")],
    selector: Annotated[str, Field(description="Selector for checkbox/radio")]
) -> dict[str, Any]:
    """Check if a checkbox or radio button is checked."""
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
# Entry Point
# =============================================================================

def main():
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
