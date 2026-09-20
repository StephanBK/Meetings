"""Base adapter interface for document discovery."""

from dataclasses import dataclass
from datetime import date
from typing import Optional
from abc import ABC, abstractmethod


@dataclass
class DocRef:
    """Reference to a discovered document."""
    source_url: str
    link_text: str
    doc_type: str  # agenda, minutes, packet, committee_agenda, other
    meeting_date: Optional[date]
    meeting_type: Optional[str]  # regular, special, committee name, etc.


class Adapter(ABC):
    """Base class for document discovery adapters."""

    @abstractmethod
    def list_documents(self, body_id: str, pages: list[str]) -> list[DocRef]:
        """
        Discover documents from the given pages.

        Args:
            body_id: The body identifier
            pages: List of URLs to scan for documents

        Returns:
            List of DocRef objects for discovered documents
        """
        pass
