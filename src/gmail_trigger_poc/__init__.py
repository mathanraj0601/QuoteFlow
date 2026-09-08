"""gmail-trigger-poc — poll a Gmail inbox and hand new emails to process_email().

QuoteFlow integration, Step 1 (Gmail polling + OAuth). Step 2 pipeline is stubbed.
"""

import logging as _logging

# Library-style default: no output until configure_logging() runs.
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

# Make ANSI colours work in legacy Windows consoles (no-op elsewhere / if absent).
try:  # pragma: no cover - environment dependent
    import colorama as _colorama

    _colorama.just_fix_windows_console()
except Exception:  # noqa: BLE001
    pass

from gmail_trigger_poc.pipeline import process_email

__all__ = ["process_email"]
