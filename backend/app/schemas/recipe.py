import uuid

from pydantic import BaseModel, Field

# Limits mirror the DB columns so oversized input gives 422 instead of a database error (500).
MAX_INGREDIENTS_PER_RECIPE = 100


class IngredientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    default_unit: str = Field(default="stk", max_length=20)


class IngredientRead(IngredientCreate):
    id: uuid.UUID

    model_config = {"from_attributes": True}


class RecipeIngredientInput(BaseModel):
    ingredient_id: uuid.UUID
    quantity: float = Field(default=1.0, ge=0, le=1_000_000)
    unit: str = Field(default="stk", max_length=20)


class RecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(max_length=4000)
    servings: int = Field(default=4, ge=1, le=100)
    prep_minutes: int = Field(default=30, ge=0, le=1440)
    ingredients: list[RecipeIngredientInput] = Field(max_length=MAX_INGREDIENTS_PER_RECIPE)


class RecipeRead(BaseModel):
    id: uuid.UUID
    name: str
    instructions: str
    servings: int
    prep_minutes: int

    model_config = {"from_attributes": True}


class RecipeSuggestRequest(BaseModel):
    available_ingredient_ids: list[uuid.UUID]


class RecipeSuggestion(BaseModel):
    recipe: RecipeRead
    matched_ingredients: int
    missing_ingredients: int
