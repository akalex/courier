"""Courier package.

This package contains transport-agnostic message forwarding logic and adapters
for receiving WhatsApp messages and sending them to messaging destinations.
"""

from importlib.metadata import version


__all__ = ["__version__"]
__version__ = version("courier")
