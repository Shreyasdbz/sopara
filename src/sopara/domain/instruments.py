from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sopara.domain.errors import DomainError

DecimalInput = str | int | Decimal


def exact_decimal(value: DecimalInput) -> Decimal:
    if isinstance(value, (bool, float)):
        raise DomainError("binary floating-point values are forbidden")
    decimal = Decimal(value)
    if not decimal.is_finite():
        raise DomainError("numeric values must be finite")
    return decimal


@dataclass(frozen=True, slots=True)
class Price:
    value: Decimal

    def __post_init__(self) -> None:
        if not self.value.is_finite() or self.value <= 0:
            raise DomainError("price must be finite and positive")

    @classmethod
    def parse(cls, value: DecimalInput) -> Price:
        return cls(exact_decimal(value))


@dataclass(frozen=True, slots=True)
class Money:
    value: Decimal

    def __post_init__(self) -> None:
        if not self.value.is_finite():
            raise DomainError("money must be finite")

    @classmethod
    def parse(cls, value: DecimalInput) -> Money:
        return cls(exact_decimal(value))

    def __add__(self, other: Money) -> Money:
        return Money(self.value + other.value)

    def __sub__(self, other: Money) -> Money:
        return Money(self.value - other.value)


class Product(StrEnum):
    ES = "ES"
    NES = "NES"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    product: Product
    tick_size: Decimal
    multiplier: Decimal

    def __post_init__(self) -> None:
        if self.tick_size <= 0 or self.multiplier <= 0:
            raise DomainError("tick size and multiplier must be positive")

    @property
    def tick_value(self) -> Money:
        return Money(self.tick_size * self.multiplier)

    def is_on_grid(self, price: Price) -> bool:
        return price.value % self.tick_size == 0

    def ticks(self, price: Price) -> int:
        if not self.is_on_grid(price):
            raise DomainError(f"{price.value} is off the {self.product} price grid")
        return int(price.value / self.tick_size)

    def price_from_ticks(self, ticks: int) -> Price:
        if ticks <= 0:
            raise DomainError("price ticks must be positive")
        return Price(self.tick_size * ticks)

    def gross_pnl(
        self,
        entry: Price,
        exit_price: Price,
        direction: Direction,
        quantity: int,
    ) -> Money:
        if quantity <= 0:
            raise DomainError("quantity must be positive")
        self.ticks(entry)
        self.ticks(exit_price)
        points = (exit_price.value - entry.value) * direction.sign
        return Money(points * self.multiplier * quantity)


ES = InstrumentSpec(product=Product.ES, tick_size=Decimal("0.25"), multiplier=Decimal("50"))
NES = InstrumentSpec(product=Product.NES, tick_size=Decimal("0.5"), multiplier=Decimal("0.50"))


@dataclass(frozen=True, slots=True)
class ContractId:
    product: Product
    expiry: date

    def __post_init__(self) -> None:
        if self.expiry.month not in {3, 6, 9, 12}:
            raise DomainError("only explicit quarterly contracts are supported")

    @property
    def canonical(self) -> str:
        return f"{self.product}-{self.expiry:%Y-%m}"

    @property
    def spec(self) -> InstrumentSpec:
        return ES if self.product is Product.ES else NES
