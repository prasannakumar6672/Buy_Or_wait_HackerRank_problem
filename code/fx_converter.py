"""
code/fx_converter.py

Deterministic FX normalization layer using only fixed dated exchange rates
from dataset/exchange_rates.csv.

Rules & Invariants:
1. Exact dated lookup: (rate_date, from_currency, to_currency)
2. Same-currency conversion: rate = 1.0, converted_amount = original_amount
3. Missing-rate failure: Fails loudly with MissingExchangeRateError. Never approximates.
4. Numerical precision: Uses Python's Decimal module for monetary calculations.
5. Auditability: Exposes an auditable record for every conversion.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, Optional, Tuple, Union
import pandas as pd


class MissingExchangeRateError(Exception):
    """Raised when an exchange rate is missing for a required currency conversion."""
    pass


@dataclass(frozen=True)
class FXConversionRecord:
    """
    Auditable record of a single deterministic currency conversion.
    """
    event_id: Optional[str]
    original_amount: Decimal
    original_currency: str
    home_currency: str
    rate: Decimal
    rate_date: str
    converted_amount: Decimal

    def to_dict(self) -> Dict[str, Union[str, float]]:
        return {
            "event_id": self.event_id or "",
            "original_amount": float(self.original_amount),
            "original_currency": self.original_currency,
            "home_currency": self.home_currency,
            "rate": float(self.rate),
            "rate_date": self.rate_date,
            "converted_amount": float(self.converted_amount),
        }


class FXConverter:
    """
    Deterministic foreign exchange rate engine backed strictly by dataset/exchange_rates.csv.
    """

    def __init__(self, exchange_rates_path: Optional[Union[str, Path]] = None):
        if exchange_rates_path is None:
            exchange_rates_path = Path(__file__).resolve().parent.parent / "dataset" / "exchange_rates.csv"
        self._rates_path = Path(exchange_rates_path)
        self._rates: Dict[Tuple[str, str, str], Decimal] = {}
        self._load_rates()

    def _load_rates(self) -> None:
        if not self._rates_path.exists():
            raise FileNotFoundError(f"Exchange rates file not found at {self._rates_path}")

        df = pd.read_csv(self._rates_path)
        for _, row in df.iterrows():
            rate_date = str(row["rate_date"]).strip()
            from_curr = str(row["from_currency"]).strip()
            to_curr = str(row["to_currency"]).strip()
            rate = Decimal(str(row["rate"]))
            self._rates[(rate_date, from_curr, to_curr)] = rate

    @property
    def total_rates(self) -> int:
        return len(self._rates)

    def get_rate(self, from_currency: str, to_currency: str, rate_date: str) -> Decimal:
        """
        Retrieves the exact dated conversion rate from from_currency to to_currency.
        Returns Decimal('1') if currencies are identical.
        Raises MissingExchangeRateError if missing.
        """
        from_curr = from_currency.strip().upper()
        to_curr = to_currency.strip().upper()
        r_date = rate_date.strip()

        if from_curr == to_curr:
            return Decimal("1")

        key = (r_date, from_curr, to_curr)
        if key in self._rates:
            return self._rates[key]

        raise MissingExchangeRateError(
            f"No exchange rate found in exchange_rates.csv for {from_curr} -> {to_curr} on date {r_date}."
        )

    def convert(
        self,
        amount: Union[Decimal, float, int, str],
        from_currency: str,
        to_currency: str,
        rate_date: str,
        event_id: Optional[str] = None,
        round_digits: Optional[int] = None,
    ) -> FXConversionRecord:
        """
        Converts an amount into the target currency using the exact dated rate.
        Retains an immutable FXConversionRecord for auditing.
        """
        amt_decimal = Decimal(str(amount))
        rate = self.get_rate(from_currency, to_currency, rate_date)
        converted = amt_decimal * rate

        if round_digits is not None:
            quantize_pattern = Decimal("1." + "0" * round_digits) if round_digits > 0 else Decimal("1")
            converted = converted.quantize(quantize_pattern, rounding=ROUND_HALF_UP)

        return FXConversionRecord(
            event_id=event_id,
            original_amount=amt_decimal,
            original_currency=from_currency.strip().upper(),
            home_currency=to_currency.strip().upper(),
            rate=rate,
            rate_date=rate_date.strip(),
            converted_amount=converted,
        )
