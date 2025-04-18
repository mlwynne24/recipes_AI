from typing import List, Optional
from lancedb.pydantic import LanceModel


class Nutrition(LanceModel):
    calories: Optional[float] = None
    fat: Optional[float] = None
    protein: Optional[float] = None
    saturates: Optional[float] = None
    carbs: Optional[float] = None
    sugar: Optional[float] = None
    fibre: Optional[float] = None
    salt: Optional[float] = None


class Recipe(LanceModel):
    id: Optional[int] = None
    name: str
    serves_no: float
    difficulty: str
    prep_time: str
    cook_time: str
    rating: Optional[int] = None
    description: Optional[str] = None
    features: Optional[List[str]] = None
    ingredients: List[str]
    method: List[str]
    comments: Optional[List[str]] = None
    nutrition: Optional[Nutrition] = None
    image: Optional[bytes] = None
