"""Data models for the directional trading algorithm.

Define value objects for market opportunities, feature vectors,
open positions, and closed trade results.  Also define the
``DirectionalResultRecord`` SQLAlchemy ORM model for persisting
closed trades to PostgreSQL (or SQLite).

All models are independent from spread capture — no imports from
``trading_tools.apps.spread_capture``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


@dataclass(frozen=True)
class MarketOpportunity:
    """A 5-minute crypto Up/Down market eligible for directional trading.

    Represent a single binary market discovered by the market scanner.
    Include both sides' token identifiers, current prices, and order
    book depth so the engine can compute features and size positions.

    Attributes:
        condition_id: Polymarket market condition identifier.
        title: Human-readable market title.
        asset: Spot trading pair (e.g. ``"BTC-USD"``).
        up_token_id: CLOB token identifier for the Up outcome.
        down_token_id: CLOB token identifier for the Down outcome.
        window_start_ts: UTC epoch seconds when the market window opens.
        window_end_ts: UTC epoch seconds when the market window closes.
        up_price: Current best ask price for the Up side.
        down_price: Current best ask price for the Down side.
        up_ask_depth: Total visible ask liquidity for the Up outcome.
        down_ask_depth: Total visible ask liquidity for the Down outcome.
        series_slug: Event series slug that discovered this market
            (e.g. ``"btc-updown-5m"``).  ``None`` when unknown.

    """

    condition_id: str
    title: str
    asset: str
    up_token_id: str
    down_token_id: str
    window_start_ts: int
    window_end_ts: int
    up_price: Decimal
    down_price: Decimal
    up_ask_depth: Decimal = Decimal(0)
    down_ask_depth: Decimal = Decimal(0)
    series_slug: str | None = None


@dataclass(frozen=True)
class TickSample:
    """Lightweight tick record for feature extraction.

    Decouple the directional feature code from the tick collector ORM
    model.  Live and replay adapters convert to this representation
    before passing ticks into the feature layer.

    Attributes:
        price: Execution price.
        size: Trade size in tokens.
        side: ``"BUY"`` or ``"SELL"``.
        timestamp_ms: Epoch milliseconds.

    """

    price: float
    size: float
    side: str
    timestamp_ms: int


@dataclass(frozen=True)
class FeatureVector:
    """Extracted features for a single market at a point in time.

    Each feature is normalised to the range ``[-1, 1]`` where positive
    values indicate an Up bias and negative values indicate a Down bias.

    Attributes:
        momentum: Recency-weighted price momentum signal.
        volatility_regime: ATR-based volatility relative to price.
        volume_profile: Recent volume vs. average volume z-score.
        book_imbalance: Order book depth asymmetry between Up and Down.
        rsi_signal: RSI mapped from ``[0, 100]`` to ``[-1, 1]``.
        price_change_pct: Percentage price change over the lookback
            period, normalised to ``[-1, 1]``.
        whale_signal: Whale net directional positioning.  ``1`` = whales
            heavily buying Up, ``-1`` = heavily buying Down, ``0`` = no
            whale activity or balanced.
        leader_momentum: BTC price momentum over the last 60 seconds,
            normalised to ``[-1, 1]``.  Captures the "BTC leads altcoins"
            effect.  Set to ``0`` for BTC markets to avoid double-counting
            with the momentum feature.
        tick_imbalance: Polymarket trade flow imbalance.
            ``(buy_vol - sell_vol) / total_vol`` for the Up token in the
            last 60 seconds.  Positive = net buying (bullish).
        tick_price_velocity: Linear rate of change of Up-token tick
            prices over the last 30 seconds, normalised to ``[-1, 1]``.
        tick_volume_accel: Ratio of recent to earlier trading volume,
            normalised to ``[-1, 1]``.  Positive = accelerating activity.

    """

    momentum: Decimal
    volatility_regime: Decimal
    volume_profile: Decimal
    book_imbalance: Decimal
    rsi_signal: Decimal
    price_change_pct: Decimal
    whale_signal: Decimal = Decimal(0)
    leader_momentum: Decimal = Decimal(0)
    tick_imbalance: Decimal = Decimal(0)
    tick_price_velocity: Decimal = Decimal(0)
    tick_volume_accel: Decimal = Decimal(0)


@dataclass
class DirectionalPosition:
    """An open directional position on one side of a binary market.

    Mutable so the engine can update fill details during execution.
    Store the feature vector used at entry time for offline analysis.

    Attributes:
        opportunity: The market opportunity that triggered this position.
        predicted_side: ``"Up"`` or ``"Down"`` — the predicted winner.
        p_up: Estimated probability that Up wins.
        token_id: CLOB token identifier for the side being bought.
        entry_price: Actual fill price.
        quantity: Number of tokens bought.
        cost_basis: Total USDC spent (price * quantity + fee).
        fee: Polymarket fee paid on entry.
        entry_time: UTC epoch seconds when the position was opened.
        features: Feature vector computed at entry time.
        order_id: CLOB order identifier, or ``None`` for paper/backtest.
        is_paper: ``True`` for simulated trades, ``False`` for live.

    """

    opportunity: MarketOpportunity
    predicted_side: str
    p_up: Decimal
    token_id: str
    entry_price: Decimal
    quantity: Decimal
    cost_basis: Decimal
    fee: Decimal
    entry_time: int
    features: FeatureVector
    order_id: str | None = None
    is_paper: bool = True


@dataclass(frozen=True)
class DirectionalResult:
    """Outcome of a closed directional trade.

    Immutable record of a position that has been settled, with the
    actual winning side and final P&L.  Store all feature values for
    offline re-scoring without re-running feature extraction.

    Attributes:
        opportunity: The market that was traded.
        predicted_side: Side the algorithm predicted would win.
        winning_side: Side that actually won, or ``None`` if unresolved.
        p_up: Estimated probability of Up at entry.
        token_id: CLOB token for the side that was bought.
        entry_price: Actual fill price.
        quantity: Number of tokens bought.
        cost_basis: Total USDC spent (price * quantity + fee).
        fee: Fee paid on entry.
        entry_time: UTC epoch seconds when position was opened.
        settled_at: UTC epoch seconds when position was settled.
        pnl: Realised profit/loss in USDC.
        features: Feature vector at entry time.
        is_paper: ``True`` for simulated trades, ``False`` for live.
        order_id: CLOB order identifier, or ``None`` for paper/backtest.

    """

    opportunity: MarketOpportunity
    predicted_side: str
    winning_side: str | None
    p_up: Decimal
    token_id: str
    entry_price: Decimal
    quantity: Decimal
    cost_basis: Decimal
    fee: Decimal
    entry_time: int
    settled_at: int
    pnl: Decimal
    features: FeatureVector
    is_paper: bool = True
    order_id: str | None = None


class DirectionalBase(DeclarativeBase):
    """Declarative base class for all directional trading ORM models."""


class DirectionalResultRecord(DirectionalBase):
    """Persisted record of a closed directional trade for post-hoc analysis.

    Denormalize opportunity and feature fields alongside execution
    details so that queries can filter and aggregate without joins.

    Attributes:
        id: Auto-incrementing primary key.
        condition_id: Polymarket market condition identifier.
        asset: Spot trading pair (e.g. ``"BTC-USD"``).
        window_start_ts: UTC epoch when the market window opens.
        window_end_ts: UTC epoch when the market window closes.
        predicted_side: Side the algorithm predicted would win.
        winning_side: Side that actually won, or ``None``.
        p_up: Estimated probability of Up at entry.
        token_id: CLOB token for the side bought.
        entry_price: Actual fill price.
        quantity: Tokens bought.
        cost_basis: Total USDC spent.
        fee: Fee paid.
        entry_time: UTC epoch when position was opened.
        settled_at: UTC epoch when position was settled.
        pnl: Realised P&L in USDC.
        is_paper: Paper or live trade.
        order_id: CLOB order identifier.
        f_momentum: Momentum feature value at entry.
        f_volatility: Volatility regime feature value at entry.
        f_volume: Volume profile feature value at entry.
        f_book_imbalance: Book imbalance feature value at entry.
        f_rsi: RSI signal feature value at entry.
        f_price_change: Price change feature value at entry.
        f_leader_momentum: Leader (BTC) momentum feature value at entry.
        f_tick_imbalance: Tick trade flow imbalance at entry.
        f_tick_price_velocity: Tick price velocity at entry.
        f_tick_volume_accel: Tick volume acceleration at entry.

    """

    __tablename__ = "directional_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    condition_id: Mapped[str] = mapped_column(String, index=True)
    asset: Mapped[str] = mapped_column(String)
    window_start_ts: Mapped[int] = mapped_column(BigInteger)
    window_end_ts: Mapped[int] = mapped_column(BigInteger)
    predicted_side: Mapped[str] = mapped_column(String)
    winning_side: Mapped[str | None] = mapped_column(String, nullable=True)
    p_up: Mapped[float] = mapped_column(Float)
    token_id: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    entry_time: Mapped[int] = mapped_column(BigInteger)
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pnl: Mapped[float] = mapped_column(Float)
    is_paper: Mapped[bool] = mapped_column(Boolean)
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    f_momentum: Mapped[float] = mapped_column(Float)
    f_volatility: Mapped[float] = mapped_column(Float)
    f_volume: Mapped[float] = mapped_column(Float)
    f_book_imbalance: Mapped[float] = mapped_column(Float)
    f_rsi: Mapped[float] = mapped_column(Float)
    f_price_change: Mapped[float] = mapped_column(Float)
    f_whale: Mapped[float] = mapped_column(Float, default=0.0)
    f_leader_momentum: Mapped[float] = mapped_column(Float, default=0.0)
    f_tick_imbalance: Mapped[float] = mapped_column(Float, default=0.0)
    f_tick_price_velocity: Mapped[float] = mapped_column(Float, default=0.0)
    f_tick_volume_accel: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_directional_condition_settled", "condition_id", "settled_at"),
        Index("ix_directional_asset_settled", "asset", "settled_at"),
        Index("ix_directional_paper_settled", "is_paper", "settled_at"),
    )

    @classmethod
    def from_result(cls, result: DirectionalResult) -> DirectionalResultRecord:
        """Create a database record from an in-memory ``DirectionalResult``.

        Flatten the nested ``MarketOpportunity`` and ``FeatureVector``
        fields and convert ``Decimal`` values to ``float`` for storage.

        Args:
            result: The closed trade result to persist.

        Returns:
            A new ``DirectionalResultRecord`` ready for insertion.

        """
        opp = result.opportunity
        feat = result.features
        return cls(
            condition_id=opp.condition_id,
            asset=opp.asset,
            window_start_ts=opp.window_start_ts,
            window_end_ts=opp.window_end_ts,
            predicted_side=result.predicted_side,
            winning_side=result.winning_side,
            p_up=float(result.p_up),
            token_id=result.token_id,
            entry_price=float(result.entry_price),
            quantity=float(result.quantity),
            cost_basis=float(result.cost_basis),
            fee=float(result.fee),
            entry_time=result.entry_time,
            settled_at=result.settled_at,
            pnl=float(result.pnl),
            is_paper=result.is_paper,
            order_id=result.order_id,
            f_momentum=float(feat.momentum),
            f_volatility=float(feat.volatility_regime),
            f_volume=float(feat.volume_profile),
            f_book_imbalance=float(feat.book_imbalance),
            f_rsi=float(feat.rsi_signal),
            f_price_change=float(feat.price_change_pct),
            f_whale=float(feat.whale_signal),
            f_leader_momentum=float(feat.leader_momentum),
            f_tick_imbalance=float(feat.tick_imbalance),
            f_tick_price_velocity=float(feat.tick_price_velocity),
            f_tick_volume_accel=float(feat.tick_volume_accel),
        )
