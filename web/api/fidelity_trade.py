from pydantic import BaseModel, Field
from typing import Optional

class FidelityTradeRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    action: str = Field(..., description="'Buy' or 'Sell'")
    quantity: float = Field(..., gt=0)
    order_type: str = Field("Limit", description="'Market' or 'Limit'")
    limit_price: Optional[float] = None
    time_in_force: str = Field("Day", description="'Day' or 'GTC'")
    account: Optional[str] = None
    execute: bool = Field(False, description="If true, actually places the order. If false, just previews.")
