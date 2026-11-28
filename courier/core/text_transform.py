from __future__ import annotations

import re
from string import Template


class MessageTextTransform:
    """Extract named values from a message with a regex and render a text template."""

    def __init__(self, pattern: str, template: str) -> None:
        try:
            self._pattern = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid message_transform.pattern: {exc}") from exc

        group_names = set(self._pattern.groupindex)
        if not group_names:
            raise ValueError("message_transform.pattern must contain at least one named capture group")

        self._template = Template(template)
        if not self._template.is_valid():
            raise ValueError("message_transform.template contains an invalid placeholder")
        unknown_names = set(self._template.get_identifiers()) - group_names
        if unknown_names:
            names = ", ".join(sorted(unknown_names))
            raise ValueError(f"message_transform.template references unknown capture group(s): {names}")

    def render(self, text: str) -> str | None:
        """Return rendered text for the first regex match, or None when there is no match."""
        match = self._pattern.search(text)
        if match is None:
            return None
        return self._template.substitute(match.groupdict(default=""))
