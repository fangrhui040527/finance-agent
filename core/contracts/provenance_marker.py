"""Provenance markers.

docs/13 section 2.1: a store is agent-editable only if it carries an explicit
created_by marker. Being in the right directory is NOT provenance - the marker is
written at creation time and never inferred from location.

docs/13 section 5 flags this as a P0 item precisely because it cannot be added
retroactively: without it the L4 gate has nothing to enforce against.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class Author(str, Enum):
    AGENT = "agent"
    HUMAN = "human"


class ProvenanceMarker(BaseModel):
    """Who made this, and may an agent rewrite it."""

    model_config = ConfigDict(frozen=True)

    created_by: Author
    created_at: datetime
    reviewed_at: datetime | None = None
    pinned: bool = False

    @property
    def managed(self) -> bool:
        """Agent-editable only when agent-created and not pinned.

        Human-authored content is off-limits forever. Pinned opts an
        agent-created store out of automatic lifecycle transitions
        (docs/13 section 1.3).
        """
        return self.created_by is Author.AGENT and not self.pinned


def is_managed(marker: ProvenanceMarker | None) -> bool:
    """Absent marker means unmanaged, never adopted by default."""
    return marker is not None and marker.managed
