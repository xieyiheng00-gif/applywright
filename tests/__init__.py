"""Test-only wait scaling, read here so it covers every way of starting a test process.

JOBFILL_TEST_WAIT_SCALE=0.25 (set by `python -m tests.run --fast`) makes Playwright's fixed waits and
typing delays a quarter as long in this process. Only importing this package applies it and nothing
under jobfill/ imports this package, so the waits that protect real sites never change.
"""

import functools
import os

WAIT_SCALE_ENV = "JOBFILL_TEST_WAIT_SCALE"


def scale_waits(scale: float, page, frame, locator) -> None:
    """Wrap wait_for_timeout on page and frame, and the delay of locator.type (classes, not instances)."""
    def scaled_wait(original):
        @functools.wraps(original)
        def wait_for_timeout(self, timeout):
            return original(self, timeout * scale)   # a number of ms or a timedelta
        return wait_for_timeout

    page.wait_for_timeout = scaled_wait(page.wait_for_timeout)
    frame.wait_for_timeout = scaled_wait(frame.wait_for_timeout)
    original_type = locator.type

    @functools.wraps(original_type)
    def scaled_type(self, text, *, delay=None, **kwargs):
        return original_type(self, text, delay=delay * scale if delay else delay, **kwargs)

    locator.type = scaled_type


def _apply_from_env() -> None:
    value = os.environ.get(WAIT_SCALE_ENV)
    if not value:
        return
    scale = float(value)
    if not 0 < scale <= 1:
        raise ValueError(f"{WAIT_SCALE_ENV} must be above 0 and at most 1, not {value}")
    try:
        from playwright.sync_api import Frame, Locator, Page
    except ImportError:
        return   # browser tests skip without Playwright
    scale_waits(scale, Page, Frame, Locator)


_apply_from_env()
