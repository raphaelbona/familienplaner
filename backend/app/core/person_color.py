"""Stable per-child color index, so a child has ONE color on the wall dashboard and on their own board.

The frontend maps ``color_index`` to a palette color (``colorForPerson`` in ``utils/personColor.ts``). The
index is the child's 0-based position among ALL children of the family, ordered by display name (the id is
the tiebreaker for equal names). It is computed from the whole family on the server, so a child login (which
may only ever see itself) still gets the family-wide index, and nothing about siblings leaks beyond a number.

Known limitation: the index is derived, not stored. Adding a child whose name sorts earlier (or renaming a
child) shifts the indices, and thereby the colors, of the children after it. Persisting a color per child is a
possible later step (it would need a migration).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User, UserRole

# One ordering for every place that lists children, so list order and color index always agree.
CHILD_ORDER = (User.display_name, User.id)


def child_color_indices(db: Session, family_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Map every child of the family to its position in the family's name-ordered children list."""
    child_ids = db.scalars(
        select(User.id).where(User.family_id == family_id, User.role == UserRole.CHILD).order_by(*CHILD_ORDER)
    ).all()
    return {child_id: index for index, child_id in enumerate(child_ids)}
