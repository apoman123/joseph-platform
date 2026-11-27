# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Stock heatmap snapshots with explicit quote.

Stock heatmap snapshots with explicit quote and optional metadata provenance.
"""

from __future__ import annotations

import asyncio
import decimal
from collections.abc import Sequence
from typing import Annotated
from typing import Literal

import pydantic
import sqlalchemy as sa

from joseph.domain import errors
from joseph.domain import identity
from joseph.domain import instruments
from joseph.market_data.app import universe

METADATA = sa.MetaData()
"""SQL metadata for private, user-supplied company classifications."""

COMPANIES = sa.Table(
    "heatmap_company_metadata",
    METADATA,
    sa.Column("tenant_id", sa.String(128), primary_key=True),
    sa.Column("instrument_id", sa.String(180), primary_key=True),
    sa.Column("body_json", sa.Text, nullable=False),
)
(
    "Optional user-provided sectors/capitalizations; never "
    "synthetic provider data."
)


class CompanyMetadata(pydantic.BaseModel):
    """Explicit optional metadata for feeds without company fundamentals.

    Attributes:
        instrument_id: Canonical stock or ETF identifier.
        sector: Optional user-provided industry/sector label, maximum 120 chars.
        market_cap: Optional positive market capitalization in its quote
            currency,
            encoded as a decimal string, never inferred from price or volume.
        as_of_ms: User-declared observation time in UTC epoch milliseconds.
    """

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    instrument_id: Annotated[str, pydantic.Field(max_length=180)]
    sector: Annotated[str, pydantic.Field(max_length=120)] | None = None
    market_cap: (
        Annotated[
            str, pydantic.Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$", max_length=64)
        ]
        | None
    ) = None
    as_of_ms: Annotated[int, pydantic.Field(ge=0)]


class CompanyMetadataBatch(pydantic.BaseModel):
    """An explicit batch of user-owned heatmap fundamentals.

    Attributes:
        records: At most five thousand canonical, dated company records.
    """

    model_config = pydantic.ConfigDict(extra="forbid")
    records: Annotated[list[CompanyMetadata], pydantic.Field(max_length=5000)]


class HeatmapTile(pydantic.BaseModel):
    """One stock tile whose missing metrics remain unavailable.

    Attributes:
        instrument_id: Exact venue-scoped stock or ETF identifier.
        symbol: Provider display symbol.
        name: Provider company/security name when available.
        exchange: Actual listing exchange or canonical venue.
        last: Latest source price as a decimal string, or None.
        change_pct: Source daily price change in percent, or None.
        volume: Source daily base volume as a decimal string, or None.
        turnover: Source daily quote turnover, or None.
        market_cap: Explicit optional company metadata, never a volume proxy.
        sector: Explicit optional company sector, otherwise unclassified.
        metadata_as_of_ms: Date of user-supplied fundamentals, or None.
        feed_state: Actual quote provenance state; unavailable without a quote.
        source: Actual provider label; never promoted to a broader market feed.
        as_of_ms: Actual quote observation time, or None without a quote.
        error: Provider/missing-data explanation, or None for an available
            quote.
    """

    instrument_id: str
    symbol: str
    name: str | None
    exchange: str
    last: str | None
    change_pct: str | None
    volume: str | None
    turnover: str | None
    market_cap: str | None
    sector: str | None
    metadata_as_of_ms: int | None
    feed_state: str
    source: str | None
    as_of_ms: int | None
    error: str | None


class HeatmapSnapshot(pydantic.BaseModel):
    """Real stock heatmap data and explicit market/coverage limits.

    Attributes:
        tiles: Selected stocks, including unavailable/neutral tiles.
        universe_count: Actual eligible stocks before the requested result
            limit.
        quote_count: Number with a returned source quote.
        generated_at_ms: Response generation time, UTC epoch milliseconds.
        region: US or Taiwan universe; quote currencies are never mixed by size.
        currency: USD or TWD quote currency of the selected stock region.
        available_metrics: Metrics actually present in at least one selected
            tile.
        missing_metadata: Whether sector or market-cap information is
            incomplete.
        source_errors: Provider metadata failures limiting the selected
            universe.
    """

    tiles: list[HeatmapTile]
    universe_count: int
    quote_count: int
    generated_at_ms: int
    region: Literal["us", "taiwan"]
    currency: str
    available_metrics: list[str]
    missing_metadata: bool
    source_errors: dict[str, str] = pydantic.Field(default_factory=dict)


class HeatmapService:
    """Builds heatmaps from configured sources.

    Builds heatmaps from configured sources and separately labeled user
    metadata.

    Attributes:
        universe: Shared real instruments and bounded quote loader.
        engine: Existing private database for optional company metadata.
    """

    def __init__(
        self, source: universe.UniverseService, engine: sa.Engine
    ) -> None:
        """Connects the stock source.

        Connects the stock source and creates the optional private metadata
        table.

        Args:
            source: Shared market universe and quote cache.
            engine: Application-owned SQL database.
        """
        self.universe = source
        self.engine = engine
        METADATA.create_all(engine)

    def metadata(self, principal: identity.Principal) -> list[CompanyMetadata]:
        """Lists only the caller's explicitly supplied company information.

        Args:
            principal: Authenticated tenant owner.

        Returns:
            Dated sector/market-cap records, never substituted into provider
            prices.
        """
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.select(COMPANIES.c.body_json).where(
                    COMPANIES.c.tenant_id == principal.tenant_id
                )
            )
            return [CompanyMetadata.model_validate_json(row[0]) for row in rows]

    def put_metadata(
        self, principal: identity.Principal, records: Sequence[CompanyMetadata]
    ) -> None:
        """Saves owned, dated company metadata.

        Saves owned, dated company metadata without inventing unavailable
        values.

        Args:
            principal: Authenticated user with authoring permission.
            records: Explicit canonical company classifications/capitalizations.

        Returns:
            None after replacing the supplied owned records atomically.

        Raises:
            errors.ForbiddenError: If writes are not permitted.
            errors.ValidationFailedError: For non-stock ids or invalid
                capitalization.
        """
        if principal.scopes and not principal.has_scope("strategy:write"):
            raise errors.ForbiddenError("strategy:write scope required")
        for record in records:
            try:
                instrument = instruments.InstrumentId.parse(
                    record.instrument_id
                )
            except ValueError:
                raise errors.ValidationFailedError(
                    "invalid canonical stock identifier"
                ) from None
            if instrument.market_type not in {
                instruments.MarketType.STOCK,
                instruments.MarketType.ETF,
            }:
                raise errors.ValidationFailedError(
                    "heatmap company metadata requires a stock or ETF"
                )
            if record.market_cap is not None and not 0 < decimal.Decimal(
                record.market_cap
            ) <= decimal.Decimal("1e20"):
                raise errors.ValidationFailedError(
                    "market capitalization must be positive and bounded"
                )
        with self.engine.begin() as connection:
            for record in records:
                condition = sa.and_(
                    COMPANIES.c.tenant_id == principal.tenant_id,
                    COMPANIES.c.instrument_id == record.instrument_id,
                )
                connection.execute(COMPANIES.delete().where(condition))
                connection.execute(
                    COMPANIES.insert().values(
                        tenant_id=principal.tenant_id,
                        instrument_id=record.instrument_id,
                        body_json=record.model_dump_json(),
                    )
                )

    async def snapshot(
        self,
        principal: identity.Principal,
        *,
        region: Literal["us", "taiwan"] = "us",
        venue: instruments.Venue | None = None,
        query: str = "",
        limit: int = 1000,
    ) -> HeatmapSnapshot:
        """Builds a bounded real heatmap.

        Builds a bounded real heatmap with missing quote/size fields preserved.

        Args:
            principal: Authenticated owner of optional company metadata.
            region: US or Taiwan listings, default US.
            venue: Optional configured exchange within that region.
            query: Optional provider symbol/company search.
            limit: Maximum tiles, one through five thousand.

        Returns:
            Real source data, coverage, metric availability and dated metadata.

        Raises:
            errors.JosephError: If an explicitly chosen source is unavailable.
        """
        allowed = {instruments.Venue.TWSE, instruments.Venue.TPEX}
        if region == "us":
            allowed = {
                instruments.Venue.NASDAQ,
                instruments.Venue.NYSE,
                instruments.Venue.CBOE,
            }
        if venue is not None and venue not in allowed | {
            instruments.Venue.MOCK
        }:
            raise errors.ValidationFailedError(
                "choose an exchange in the selected stock region"
            )
        targets = [venue] if venue is not None else []
        if venue is None:
            targets = [
                target
                for target in self.universe.market_data.registry.venues
                if target in allowed | {instruments.Venue.MOCK}
            ]
        results = await asyncio.gather(
            *(
                self.universe.select(venue=target, query=query)
                for target in targets
            ),
            return_exceptions=True,
        )
        rows = []
        source_errors = {}
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, errors.JosephError):
                source_errors[target.value] = result.message[:300]
            elif isinstance(result, BaseException):
                raise result
            else:
                rows.extend(result)
        if not targets:
            source_errors["market"] = (
                "No data feed is configured for this stock region"
            )
        selected = [
            row
            for row in rows
            if row.instrument_id.market_type
            in {instruments.MarketType.STOCK, instruments.MarketType.ETF}
            and (
                row.instrument_id.venue in allowed
                or (
                    row.instrument_id.venue is instruments.Venue.MOCK
                    and row.calendar == ("XNYS" if region == "us" else "XTAI")
                )
            )
        ]
        count = len(selected)
        selected = selected[:limit]
        quotes, failures = await self.universe.quotes(selected)
        metadata = {row.instrument_id: row for row in self.metadata(principal)}
        tiles = []
        for row in selected:
            key = str(row.instrument_id)
            quote = quotes.get(key)
            company = metadata.get(key)
            tiles.append(
                HeatmapTile(
                    instrument_id=key,
                    symbol=row.display_symbol,
                    name=row.name or row.local_name,
                    exchange=row.listing_exchange
                    or row.instrument_id.venue.value,
                    last=str(quote.last)
                    if quote and quote.last is not None
                    else None,
                    change_pct=str(quote.change_24h_pct)
                    if quote and quote.change_24h_pct is not None
                    else None,
                    volume=str(quote.volume_24h_base)
                    if quote and quote.volume_24h_base is not None
                    else None,
                    turnover=str(quote.turnover_24h_quote)
                    if quote and quote.turnover_24h_quote is not None
                    else None,
                    market_cap=company.market_cap if company else None,
                    sector=company.sector if company else None,
                    metadata_as_of_ms=company.as_of_ms if company else None,
                    feed_state=quote.feed_state.value
                    if quote
                    else "UNAVAILABLE",
                    source=quote.source if quote else None,
                    as_of_ms=quote.as_of_ms if quote else None,
                    error=failures.get(key),
                )
            )
        metrics = ["equal"]
        for field in (
            "volume",
            "turnover",
            "market_cap",
            "change_pct",
            "sector",
        ):
            if any(getattr(tile, field) is not None for tile in tiles):
                metrics.append(field)
        return HeatmapSnapshot(
            tiles=tiles,
            source_errors=source_errors,
            universe_count=count,
            quote_count=len(quotes),
            generated_at_ms=self.universe.market_data.now_ms(),
            region=region,
            currency="USD" if region == "us" else "TWD",
            available_metrics=metrics,
            missing_metadata=any(
                tile.market_cap is None or tile.sector is None for tile in tiles
            ),
        )
