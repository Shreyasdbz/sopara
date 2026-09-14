from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sopara.domain.errors import DomainError
from sopara.domain.evidence import EvidenceClass, EvidenceSelection
from sopara.domain.instruments import ES, NES, Direction, Money, Price
from sopara.domain.reason_codes import ReasonCode


def test_one_nes_tick_is_twenty_five_cents_for_one_contract() -> None:
    assert NES.tick_value == Money.parse("0.25")
    assert NES.gross_pnl(
        Price.parse("6000.0"), Price.parse("6000.5"), Direction.LONG, 1
    ) == Money.parse("0.25")


def test_es_valid_price_can_be_off_grid_for_nes() -> None:
    price = Price.parse("6000.25")
    assert ES.is_on_grid(price)
    assert not NES.is_on_grid(price)


def test_binary_float_and_non_finite_values_are_rejected() -> None:
    with pytest.raises(DomainError, match="binary floating-point"):
        Price.parse(6000.5)  # type: ignore[arg-type]
    with pytest.raises(DomainError, match="finite"):
        Money(Decimal("NaN"))


def test_observed_nes_cannot_predate_launch() -> None:
    with pytest.raises(DomainError, match=ReasonCode.OBSERVED_NES_PRE_LAUNCH):
        EvidenceSelection(EvidenceClass.OBSERVED_NES, date(2026, 8, 23))


def test_proxy_requires_opt_in_and_never_qualifies_as_observed() -> None:
    with pytest.raises(DomainError, match=ReasonCode.PROXY_OPT_IN_REQUIRED):
        EvidenceSelection(EvidenceClass.SYNTHETIC_NES_PROXY, date(2026, 8, 24))
    proxy = EvidenceSelection(
        EvidenceClass.SYNTHETIC_NES_PROXY, date(2026, 8, 24), proxy_opt_in=True
    )
    assert not proxy.qualifies_as_observed_nes
