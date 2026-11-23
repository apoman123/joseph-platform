"""Strategy rules on exchange session calendars and rate instruments."""

from __future__ import annotations

import copy
import datetime
import zoneinfo
from collections.abc import Callable

import pytest

from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.domain import sessions
from joseph.domain import timeframes
from joseph.strategy.adapters import memory
from joseph.strategy.app import processor
from joseph.strategy.app import version_service
from joseph.strategy.domain import ast
from joseph.strategy.domain import compiler
from joseph.strategy.domain import setup_fsm
from joseph.strategy.testing import fixtures
from joseph.strategy.testing import frames as frame_testing
from joseph.strategy.testing import scenarios

TF = timeframes.Timeframe
NY = zoneinfo.ZoneInfo("America/New_York")
ACME = "mock:stock:ACME"
Make = Callable[..., ast.StrategyDefinition]
_ALL_KINDS = [
    "WATCHING_AOI",
    "PRE_SIGNAL",
    "ENTRY_CONFIRMED",
    "ENTRY_BLOCKED",
    "AOI_INVALIDATED",
    "SETUP_EXPIRED",
]
_TOUCH = {
    "op": "AOI_TOUCH",
    "kinds": ["FVG"],
    "side": "bullish",
    "within_bars": 3,
}


def _ny(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    """Returns UTC epoch ms of a New York wall time.

    Args:
        year: Year.
        month: Month.
        day: Day.
        hour: Hour.
        minute: Minute.

    Returns:
        Epoch milliseconds.
    """
    moment = datetime.datetime(year, month, day, hour, minute, tzinfo=NY)
    return int(moment.timestamp() * 1000)


def _codes(
    result: compiler.CompileResult, *, warnings: bool = False
) -> set[str]:
    """Collects issue codes.

    Args:
        result: Compile result.
        warnings: Collect warnings instead of errors.

    Returns:
        Codes.
    """
    issues = (
        result.validation.warnings if warnings else result.validation.errors
    )
    return {issue.code for issue in issues}


def _universe(
    venues: list[str], whitelist: list[str] | None = None
) -> dict[str, object]:
    """Builds a universe override.

    Args:
        venues: Venue names.
        whitelist: Optional explicit instruments.

    Returns:
        Universe object.
    """
    return {
        "qualification": "ANY",
        "venues": venues,
        "whitelist": whitelist or [],
        "blacklist": [],
        "min_qualified_minutes": 0,
    }


# Frame builders ---------------------------------------------------------


def test_frame_factory_counts_session_bars() -> None:
    """Bar 77 is Friday's 15:55 bar; bar 78 opens Monday."""
    factory = frame_testing.FrameFactory(instrument_id=ACME)
    assert factory.base_ms == _ny(2026, 11, 20, 9, 30)
    assert factory.close(77) == _ny(2026, 11, 20, 16)
    assert factory.start(78) == _ny(2026, 11, 23, 9, 30)
    assert factory.start(-1) == _ny(2026, 11, 19, 15, 55)
    event = factory.event("SSL_SWEEP", 77, tf=TF.H1)
    assert event.bar_start_ms == _ny(2026, 11, 20, 15, 30)
    snapshot = factory.snapshot("daily_bias", TF.D1, 78)
    assert snapshot.bar_start_ms == _ny(2026, 11, 20, 9, 30)


# Lifecycle ---------------------------------------------------------------


def test_lifecycle_counts_bars_on_the_calendar(
    make_definition: Make,
) -> None:
    """Two 5m bars after Friday's close end at Monday 09:40."""
    plan = compiler.compile_strategy(make_definition()).plan
    assert plan is not None
    policy = setup_fsm.LifecyclePolicy.from_plan(plan, sessions.XNYS)
    close = _ny(2026, 11, 20, 16)
    assert policy.bars_after(close, 0) == close
    assert policy.bars_after(close, 1) == _ny(2026, 11, 23, 9, 35)
    assert policy.bars_after(close, 2) == _ny(2026, 11, 23, 9, 40)
    utc = setup_fsm.LifecyclePolicy.from_plan(plan)
    assert utc.calendar is sessions.CONTINUOUS
    assert utc.bars_after(close, 2) == close + 2 * 300_000


def _acme_run(definition: ast.StrategyDefinition) -> list[tuple[int, str]]:
    """Runs the Long A world on ACME with the entry on Friday's last bar.

    Args:
        definition: Strategy definition.

    Returns:
        ``(bar index, signal kind)`` of every emission.
    """
    plan = compiler.compile_strategy(definition).plan
    assert plan is not None
    version = fixtures.make_version(definition)
    key = setup_fsm.InstanceKey(
        "tenant-01", version.strategy_version_id, "*", ACME, "LONG"
    )
    proc = processor.InstanceProcessor(plan, version, key, mode="PAPER")
    assert proc.policy.calendar is sessions.XNYS
    options = scenarios.WorldOptions(bars=90, entry_index=77, sweep=False)
    frames = scenarios.long_a_frames(options, instrument_id=ACME)
    factory = frame_testing.FrameFactory(instrument_id=ACME)
    index = {factory.start(i): i for i in range(90)}
    out = []
    for frame in frames:
        result = proc.step(frame, evaluated_at_ms=frame.close_ms)
        out.extend((index[s.bar_start_ms], s.kind) for s in result.signals)
    return out


def test_setup_expiry_is_counted_in_session_bars(make_definition: Make) -> None:
    """A Friday-close setup with a 2-bar life expires on Monday's 2nd bar."""
    raw = make_definition().to_contract()
    raw["notification"]["emit"] = _ALL_KINDS  # type: ignore[index]
    raw["setup"] = {**raw["setup"], "expire_after_bars": 2}  # type: ignore[dict-item]
    emitted = _acme_run(ast.parse_definition(copy.deepcopy(raw)))
    assert emitted[0] == (77, "PRE_SIGNAL")
    assert (79, "SETUP_EXPIRED") in emitted
    assert (78, "SETUP_EXPIRED") not in emitted


# Compiler --------------------------------------------------------------


def test_rate_venue_cannot_be_a_strategy_universe(
    make_definition: Make,
) -> None:
    """The treasury venue publishes rate series only and is rejected."""
    result = compiler.compile_strategy(
        make_definition(universe=_universe(["treasury"]))
    )
    assert "RATE_VENUE_NOT_TRADABLE" in _codes(result)


def test_rate_instrument_cannot_be_traded(make_definition: Make) -> None:
    """A whitelisted rate series is rejected with an explanation."""
    result = compiler.compile_strategy(
        make_definition(universe=_universe(["nasdaq"], ["nasdaq:rate:DGS10"]))
    )
    assert "RATE_NOT_TRADABLE" in _codes(result)
    message = next(
        issue.message
        for issue in result.validation.errors
        if issue.code == "RATE_NOT_TRADABLE"
    )
    assert "context" in message
    assert compiler.untradable_reason("treasury:rate:UST10Y") is not None
    assert compiler.untradable_reason("nasdaq:etf:TLT") is None
    assert compiler.untradable_reason("not an id") is None


def _funding_rule() -> dict[str, object]:
    """Builds a rule reading the funding rate.

    Returns:
        Rule node.
    """
    return {
        "op": "ALL",
        "children": [
            _TOUCH,
            {
                "op": "COMPARE",
                "left": {"kind": "market", "field": "funding_8h_pct"},
                "cmp": "lt",
                "right": {"kind": "const", "value": "0.1"},
            },
        ],
    }


def test_funding_operands_are_refused_for_exchange_universes(
    make_definition: Make,
) -> None:
    """Exchange securities have no funding; mixed universes get a warning."""
    stocks = compiler.compile_strategy(
        make_definition(when=_funding_rule(), universe=_universe(["nasdaq"]))
    )
    assert "MARKET_FIELD_UNAVAILABLE" in _codes(stocks)
    mixed = compiler.compile_strategy(
        make_definition(
            when=_funding_rule(), universe=_universe(["nasdaq", "binance"])
        )
    )
    assert mixed.validation.valid
    assert "MARKET_FIELD_PARTIAL" in _codes(mixed, warnings=True)
    crypto = compiler.compile_strategy(
        make_definition(when=_funding_rule(), universe=_universe(["binance"]))
    )
    assert crypto.validation.valid
    assert "MARKET_FIELD_PARTIAL" not in _codes(crypto, warnings=True)


def test_stock_universe_compiles_with_session_calendars(
    make_definition: Make,
) -> None:
    """Long A on nasdaq/nyse compiles; the DEMO venue spans two calendars."""
    result = compiler.compile_strategy(
        make_definition(universe=_universe(["nasdaq", "nyse"]))
    )
    assert result.validation.valid, result.validation.errors
    assert compiler.venue_calendars(instruments.Venue.MOCK) == frozenset(
        {sessions.CONTINUOUS, sessions.XNYS}
    )


def test_activation_refuses_rate_instruments(make_definition: Make) -> None:
    """A rate series can never be an activated instrument."""
    from joseph.domain import clock as clock_lib  # noqa: PLC0415

    service = version_service.StrategyVersionService(
        memory.InMemoryStrategyRepository(), clock_lib.ManualClock(0)
    )
    principal = identity.Principal("tenant-a", "alice")
    definition = make_definition(universe=_universe(["nasdaq"]))
    created = service.create(principal, definition)
    with pytest.raises(errors.ValidationFailedError, match="rate series"):
        service.activate(
            principal,
            created.strategy_id,
            created.version,
            "PAPER",
            ["nasdaq:rate:DGS10"],
        )
