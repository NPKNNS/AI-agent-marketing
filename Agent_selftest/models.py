"""Data models and schemas for the Robusta Coffee Trading Competition."""

from pydantic import BaseModel, Field
from typing import List, Dict, Literal

KG_PER_SACK = 60.0

class Inventory(BaseModel):
    grade_a: int = Field(default=0, description="Number of Grade A sacks (Premium)")
    grade_b: int = Field(default=0, description="Number of Grade B sacks (Standard)")
    grade_c: int = Field(default=0, description="Number of Grade C sacks (Commercial)")

    @property
    def total_sacks(self) -> int:
        return self.grade_a + self.grade_b + self.grade_c

    def get_count(self, grade: str) -> int:
        g = grade.upper().replace("GRADE ", "").strip()
        if g == "A":
            return self.grade_a
        elif g == "B":
            return self.grade_b
        elif g == "C":
            return self.grade_c
        return 0

    def deduct(self, grade: str, count: int) -> bool:
        g = grade.upper().replace("GRADE ", "").strip()
        if g == "A" and self.grade_a >= count:
            self.grade_a -= count
            return True
        elif g == "B" and self.grade_b >= count:
            self.grade_b -= count
            return True
        elif g == "C" and self.grade_c >= count:
            self.grade_c -= count
            return True
        return False

    def copy(self) -> "Inventory":
        return Inventory(grade_a=self.grade_a, grade_b=self.grade_b, grade_c=self.grade_c)

    def summary(self) -> str:
        return f"A: {self.grade_a} | B: {self.grade_b} | C: {self.grade_c} (Total: {self.total_sacks} sacks)"

class BuyerRequest(BaseModel):
    buyer_id: int
    name: str
    acceptable_grades: List[str] = Field(description="Grades this buyer accepts e.g. ['A', 'B']")
    demand_sacks: int = Field(description="Number of sacks the buyer wishes to purchase this round")
    max_price_per_kg: float = Field(description="Ceiling price buyer is willing to pay in THB/kg")

class SellerOffer(BaseModel):
    agent_name: str
    buyer_id: int
    grade: str
    sacks: int
    price_per_kg: float

class Deal(BaseModel):
    round_num: int
    buyer_id: int
    buyer_name: str
    seller_name: str
    grade: str
    sacks: int
    price_per_kg: float
    revenue_thb: float

class RoundContext(BaseModel):
    round_num: int
    total_rounds: int = 6
    market_base_price_thb: float
    buyers: List[BuyerRequest]

    @property
    def market_base_price(self) -> float:
        return self.market_base_price_thb
