from typing import List, Optional
from lancedb.pydantic import LanceModel


class Nutrition(LanceModel):
    calories: Optional[float]
    fat: Optional[float]
    protein: Optional[float]
    saturates: Optional[float]
    carbs: Optional[float]
    sugar: Optional[float]
    fibre: Optional[float]
    salt: Optional[float]


class Recipe(LanceModel):
    id: int
    name: str
    cuisine_type: Optional[str] = None
    serves_no: float
    difficulty: str
    prep_time: str
    cook_time: str
    rating: int
    description: str
    features: List[str]
    ingredients: List[str]
    method: List[str]
    comments: List[str]
    nutrition: Optional[Nutrition] = None
    image: bytes
