from dataclasses import dataclass


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: UsageTotals) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ModelPrice:
    input_usd_per_1m: float
    output_usd_per_1m: float
    source: str


MODEL_PRICES = {
    "gemini-3.1-flash-lite": ModelPrice(
        input_usd_per_1m=0.25,
        output_usd_per_1m=1.50,
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    "google_genai:gemini-3.1-flash-lite": ModelPrice(
        input_usd_per_1m=0.25,
        output_usd_per_1m=1.50,
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
}


def calculate_cost(model: str, usage: UsageTotals) -> dict[str, object] | None:
    price = MODEL_PRICES.get(model)
    if price is None:
        return None

    input_cost = usage.input_tokens * price.input_usd_per_1m / 1_000_000
    output_cost = usage.output_tokens * price.output_usd_per_1m / 1_000_000
    total_cost = input_cost + output_cost
    return {
        "currency": "USD",
        "input_cost": round(input_cost, 8),
        "output_cost": round(output_cost, 8),
        "total_cost": round(total_cost, 8),
        "input_usd_per_1m": price.input_usd_per_1m,
        "output_usd_per_1m": price.output_usd_per_1m,
        "source": price.source,
        "estimated": False,
    }
