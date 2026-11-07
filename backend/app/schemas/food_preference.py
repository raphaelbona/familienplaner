"""Schemas for food preferences."""

import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

MAX_ITEMS_PER_KIND = 50
MAX_ITEM_LENGTH = 80



def _needs_letter_or_digit(value: str) -> str:
    """Blank entries are dropped later; anything else needs a letter or digit (of any script).

    Emoji/punctuation-only names cannot be matched against ingredients in a meaningful way.
    """
    if value and not any(ch.isalnum() for ch in value):
        raise ValueError("must contain at least one letter or digit")
    return value


PreferenceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=MAX_ITEM_LENGTH),
    AfterValidator(_needs_letter_or_digit),
]
PreferenceList = Annotated[list[PreferenceName], Field(max_length=MAX_ITEMS_PER_KIND)]


class UserFoodPreferencesRead(BaseModel):
    """Food preferences for a user (read)."""

    likes: list[str] = Field(default_factory=list, description="Liked ingredients")
    dislikes: list[str] = Field(default_factory=list, description="Disliked ingredients")
    allergies: list[str] = Field(default_factory=list, description="Allergic ingredients")


class MemberFoodPreferences(UserFoodPreferencesRead):
    """One member's preferences in the family overview."""

    user_id: uuid.UUID
    display_name: str


class UserFoodPreferencesWrite(BaseModel):
    """Food preferences for a user (write); replaces the member's whole set.

    At most 50 entries per kind, each at most 80 characters (after stripping); blank
    entries are dropped. Violations are rejected with 422.
    """

    likes: PreferenceList = Field(default_factory=list)
    dislikes: PreferenceList = Field(default_factory=list)
    allergies: PreferenceList = Field(default_factory=list)
