"""Logistic regression weight trainer for the directional estimator.

Extract features and outcomes from historical market windows, then fit
optimal estimator weights via gradient descent.  The learned weights
slot directly into ``DirectionalConfig`` — the model form is identical
to the hand-tuned weighted ensemble:
``P(Up) = sigmoid(dot(features, w) + bias)``.

The bias (intercept) term allows the model to learn the empirical base
rate rather than assuming symmetric markets (sigmoid(0) = 0.5).

No new dependencies — uses numpy (available via pandas) for vectorised
gradient descent.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
from numpy.typing import NDArray

from trading_tools.apps.tick_collector.models import MarketMetadata
from trading_tools.core.models import Candle

from .backtest_runner import (
    BookSnapshotCache,
    WhaleTradeCache,
    determine_outcome,
    make_default_book,
    snapshot_to_order_book,
)
from .features import extract_features
from .models import FeatureVector, TickSample

_MS_PER_SECOND = 1000

# Feature names in FeatureVector field order → config weight field names.
FEATURE_NAMES: tuple[str, ...] = (
    "momentum",
    "volatility_regime",
    "volume_profile",
    "book_imbalance",
    "rsi_signal",
    "price_change_pct",
    "whale_signal",
    "leader_momentum",
    "tod_sin",
    "tod_cos",
    "tick_imbalance",
    "tick_price_velocity",
    "tick_volume_accel",
)

WEIGHT_NAMES: tuple[str, ...] = (
    "w_momentum",
    "w_volatility",
    "w_volume",
    "w_book_imbalance",
    "w_rsi",
    "w_price_change",
    "w_whale",
    "w_leader_momentum",
    "w_tod_sin",
    "w_tod_cos",
    "w_tick_imbalance",
    "w_tick_price_velocity",
    "w_tick_volume_accel",
)

_DEFAULT_WEIGHTS: tuple[Decimal, ...] = (
    Decimal("0.15"),
    Decimal("0.05"),
    Decimal("0.05"),
    Decimal("0.10"),
    Decimal("0.05"),
    Decimal("0.10"),
    Decimal("0.50"),
    Decimal("0.0"),
    Decimal("0.0"),
    Decimal("0.0"),
    Decimal("0.0"),
    Decimal("0.0"),
    Decimal("0.0"),
)

_LEADER_ASSET = "BTC-USD"

_DEFAULT_BIAS = Decimal("0.0")

_TICK_LOOKBACK_MS = 60_000


class TickCache:
    """Pre-loaded tick data for O(1) lookups during training.

    Group tick samples by token ID for fast retrieval when building
    the training dataset.

    """

    def __init__(self, ticks_by_token: dict[str, list[TickSample]]) -> None:
        """Initialize with pre-grouped tick data.

        Args:
            ticks_by_token: Mapping from token ID to tick samples,
                each list ordered by timestamp.

        """
        self._by_token = ticks_by_token

    def get_ticks(self, token_id: str, since_ms: int, until_ms: int) -> list[TickSample]:
        """Return ticks for a token within a time range.

        Args:
            token_id: CLOB token identifier.
            since_ms: Start epoch milliseconds (inclusive).
            until_ms: End epoch milliseconds (inclusive).

        Returns:
            Filtered tick samples.

        """
        all_ticks = self._by_token.get(token_id, [])
        return [t for t in all_ticks if since_ms <= t.timestamp_ms <= until_ms]


_MIN_CANDLES = 16


@dataclass(frozen=True)
class TrainingDataset:
    """Feature matrix and label vector extracted from historical windows.

    Attributes:
        x: Feature matrix of shape ``(n_samples, 7)``.
        y: Binary label vector of shape ``(n_samples,)`` where
            ``1.0`` = Up and ``0.0`` = Down.
        feature_names: Ordered tuple of feature names matching columns.

    """

    x: NDArray[np.float64]
    y: NDArray[np.float64]
    feature_names: tuple[str, ...]


@dataclass(frozen=True)
class TrainingResult:
    """Outcome of logistic regression weight training.

    Attributes:
        weights: Mapping from config weight field name to learned value.
        bias: Learned bias (intercept) term.
        accuracy: Fraction of correctly classified training samples.
        log_loss: Mean negative log-likelihood on training data.
        n_samples: Number of samples used for training.
        n_skipped: Number of windows skipped (flat outcome, insufficient
            candles, etc.).

    """

    weights: dict[str, Decimal]
    bias: Decimal
    accuracy: float
    log_loss: float
    n_samples: int
    n_skipped: int


def _feature_vector_to_array(fv: FeatureVector) -> NDArray[np.float64]:
    """Convert a ``FeatureVector`` to a numpy float array.

    Args:
        fv: Feature vector with Decimal values.

    Returns:
        Array of shape ``(7,)`` with float64 values.

    """
    return np.array(
        [float(getattr(fv, name)) for name in FEATURE_NAMES],
        dtype=np.float64,
    )


def _stable_sigmoid(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Compute numerically stable sigmoid for an array of values.

    Use the ``exp(x) / (1 + exp(x))`` form for negative inputs and
    ``1 / (1 + exp(-x))`` for non-negative inputs to avoid overflow.

    Args:
        x: Input array.

    Returns:
        Sigmoid values in ``(0, 1)``.

    """
    result = np.empty_like(x)
    pos = x >= 0
    neg = ~pos
    result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    exp_x = np.exp(x[neg])
    result[neg] = exp_x / (1.0 + exp_x)
    return result


def build_training_dataset(
    metadata_list: Sequence[MarketMetadata],
    candles_by_asset: dict[str, list[Candle]],
    *,
    entry_window_start: int = 30,
    signal_lookback_seconds: int = 1200,
    snapshot_cache: BookSnapshotCache | None = None,
    whale_cache: WhaleTradeCache | None = None,
    tick_cache: TickCache | None = None,
) -> TrainingDataset:
    """Build a training dataset from historical market windows.

    For each window, determine the outcome from candle data, extract
    features using the same logic as the live estimator, and collect
    the feature vector and binary label.

    Args:
        metadata_list: Market metadata records for the training period.
        candles_by_asset: Pre-loaded Binance 1-min candles keyed by asset.
        entry_window_start: Seconds before window close to evaluate entry
            (used for snapshot/whale signal timing).
        signal_lookback_seconds: Seconds of candle lookback for feature
            extraction.
        snapshot_cache: Pre-built order book snapshot cache for O(log n)
            lookups.  When ``None``, default symmetric books are used.
        whale_cache: Pre-built whale trade cache for signal lookups.
            When ``None``, whale signal is omitted (zero).
        tick_cache: Pre-built tick sample cache for Polymarket trade
            flow features.  When ``None``, tick features are zero.

    Returns:
        A ``TrainingDataset`` with feature matrix and label vector.

    """
    rows: list[NDArray[np.float64]] = []
    labels: list[float] = []

    for meta in metadata_list:
        asset_candles = candles_by_asset.get(meta.asset, [])

        # Window candles for outcome determination
        window_candles = [
            c
            for c in asset_candles
            if meta.window_start_ts <= c.timestamp <= meta.window_end_ts  # type: ignore[union-attr]
        ]
        outcome = determine_outcome(window_candles)
        if outcome is None:
            continue

        # Lookback candles for feature extraction
        lookback_start = meta.window_end_ts - signal_lookback_seconds
        lookback_candles = [
            c
            for c in asset_candles
            if lookback_start <= c.timestamp <= meta.window_end_ts  # type: ignore[union-attr]
        ]
        if len(lookback_candles) < _MIN_CANDLES:
            continue

        # Order book snapshots near entry evaluation time
        entry_eval_ts = meta.window_end_ts - entry_window_start
        entry_eval_ms = entry_eval_ts * _MS_PER_SECOND

        if snapshot_cache is not None:
            up_snap = snapshot_cache.get_nearest(
                meta.up_token_id, entry_eval_ms, tolerance_ms=30_000
            )
            down_snap = snapshot_cache.get_nearest(
                meta.down_token_id, entry_eval_ms, tolerance_ms=30_000
            )
        else:
            up_snap = None
            down_snap = None

        up_book = (
            snapshot_to_order_book(up_snap) if up_snap else make_default_book(meta.up_token_id)
        )
        down_book = (
            snapshot_to_order_book(down_snap)
            if down_snap
            else make_default_book(meta.down_token_id)
        )

        # Whale signal (continuous)
        whale_signal_val: float | None = None
        if whale_cache is not None:
            whale_signal_val = whale_cache.get_signal(meta.condition_id, before_ts=entry_eval_ts)

        # Leader (BTC) candles for cross-asset momentum — None for BTC itself
        leader_candles_for_window: list[Candle] | None = None
        if meta.asset != _LEADER_ASSET:
            btc_candles = candles_by_asset.get(_LEADER_ASSET, [])
            if btc_candles:
                leader_candles_for_window = [
                    c
                    for c in btc_candles
                    if lookback_start <= c.timestamp <= meta.window_end_ts  # type: ignore[union-attr]
                ]

        # Tick data for Polymarket trade flow features
        up_ticks: list[TickSample] | None = None
        if tick_cache is not None:
            tick_since_ms = entry_eval_ms - _TICK_LOOKBACK_MS
            up_ticks = tick_cache.get_ticks(meta.up_token_id, tick_since_ms, entry_eval_ms)

        features = extract_features(
            lookback_candles,
            up_book,
            down_book,
            whale_signal=whale_signal_val,
            leader_candles=leader_candles_for_window,
            up_ticks=up_ticks,
            utc_epoch=entry_eval_ts,
        )

        rows.append(_feature_vector_to_array(features))
        labels.append(1.0 if outcome == "Up" else 0.0)

    if not rows:
        return TrainingDataset(
            x=np.empty((0, len(FEATURE_NAMES)), dtype=np.float64),
            y=np.empty(0, dtype=np.float64),
            feature_names=FEATURE_NAMES,
        )

    return TrainingDataset(
        x=np.stack(rows),
        y=np.array(labels, dtype=np.float64),
        feature_names=FEATURE_NAMES,
    )


def train_weights(
    dataset: TrainingDataset,
    *,
    learning_rate: float = 0.1,
    max_iterations: int = 10_000,
    tolerance: float = 1e-7,
    l2_lambda: float = 0.0,
) -> TrainingResult:
    """Fit logistic regression weights and bias via gradient descent.

    Minimise the binary cross-entropy loss (with optional L2
    regularisation) using batch gradient descent.  The bias (intercept)
    term is learned alongside feature weights, allowing the model to
    capture the empirical base rate rather than assuming symmetric
    markets.

    Args:
        dataset: Training data with feature matrix and labels.
        learning_rate: Step size for gradient descent.
        max_iterations: Maximum number of gradient descent iterations.
        tolerance: Stop when the absolute change in loss is below this
            threshold.
        l2_lambda: L2 regularisation coefficient.  Zero disables
            regularisation.  L2 is applied to feature weights only,
            not the bias term.

    Returns:
        A ``TrainingResult`` with the learned weights, bias, and metrics.

    """
    x = dataset.x
    y = dataset.y
    n = len(y)

    if n == 0:
        return TrainingResult(
            weights=dict(zip(WEIGHT_NAMES, _DEFAULT_WEIGHTS, strict=True)),
            bias=_DEFAULT_BIAS,
            accuracy=0.0,
            log_loss=0.0,
            n_samples=0,
            n_skipped=0,
        )

    w = np.zeros(x.shape[1], dtype=np.float64)
    b = 0.0
    prev_loss = float("inf")

    for _ in range(max_iterations):
        z = x @ w + b
        p = _stable_sigmoid(z)

        residual = p - y
        w_gradient = x.T @ residual / n
        b_gradient = float(np.mean(residual))
        if l2_lambda > 0:
            w_gradient += l2_lambda * w

        w -= learning_rate * w_gradient
        b -= learning_rate * b_gradient

        # Binary cross-entropy loss (clipped for numerical stability)
        eps = 1e-15
        p_clipped = np.clip(p, eps, 1.0 - eps)
        loss = -np.mean(y * np.log(p_clipped) + (1 - y) * np.log(1 - p_clipped))
        if l2_lambda > 0:
            loss += 0.5 * l2_lambda * float(np.sum(w**2))

        if abs(prev_loss - loss) < tolerance:
            break
        prev_loss = loss

    # Compute final metrics
    final_p = _stable_sigmoid(x @ w + b)
    decision_threshold = 0.5
    predictions = (final_p >= decision_threshold).astype(float)
    correct = predictions == y
    accuracy = float(correct.sum()) / n

    eps = 1e-15
    final_p_clipped = np.clip(final_p, eps, 1.0 - eps)
    log_loss_arr = y * np.log(final_p_clipped) + (1 - y) * np.log(1 - final_p_clipped)
    log_loss_val = -float(log_loss_arr.sum()) / n

    weight_dict = {name: Decimal(str(round(float(w[i]), 6))) for i, name in enumerate(WEIGHT_NAMES)}

    return TrainingResult(
        weights=weight_dict,
        bias=Decimal(str(round(b, 6))),
        accuracy=accuracy,
        log_loss=log_loss_val,
        n_samples=n,
        n_skipped=0,
    )


_MIN_SLUG_SAMPLES = 50


def train_all_slugs(
    metadata_list: Sequence[MarketMetadata],
    candles_by_asset: dict[str, list[Candle]],
    *,
    entry_window_start: int = 30,
    signal_lookback_seconds: int = 1200,
    snapshot_cache: BookSnapshotCache | None = None,
    whale_cache: WhaleTradeCache | None = None,
    tick_cache: TickCache | None = None,
    learning_rate: float = 0.1,
    max_iterations: int = 10_000,
    l2_lambda: float = 0.0,
) -> dict[str, TrainingResult]:
    """Train separate weights for each series slug with sufficient data.

    Group metadata by ``series_slug``, build a dataset per slug, and
    train independent logistic regression weights.  Slugs with fewer
    than ``_MIN_SLUG_SAMPLES`` samples are skipped.

    Args:
        metadata_list: All market metadata records.
        candles_by_asset: Pre-loaded Binance candles keyed by asset.
        entry_window_start: Seconds before window close for evaluation.
        signal_lookback_seconds: Candle lookback for features.
        snapshot_cache: Pre-built order book snapshot cache.
        whale_cache: Pre-built whale trade cache.
        tick_cache: Pre-built tick sample cache.
        learning_rate: Gradient descent step size.
        max_iterations: Max gradient descent iterations.
        l2_lambda: L2 regularisation coefficient.

    Returns:
        Mapping from series slug to ``TrainingResult``.  Only slugs
        with enough samples are included.

    """
    by_slug: dict[str, list[MarketMetadata]] = {}
    for meta in metadata_list:
        slug = meta.series_slug
        if slug is None:
            continue
        by_slug.setdefault(slug, []).append(meta)

    results: dict[str, TrainingResult] = {}
    for slug, slug_metadata in sorted(by_slug.items()):
        dataset = build_training_dataset(
            slug_metadata,
            candles_by_asset,
            entry_window_start=entry_window_start,
            signal_lookback_seconds=signal_lookback_seconds,
            snapshot_cache=snapshot_cache,
            tick_cache=tick_cache,
            whale_cache=whale_cache,
        )
        n_samples = dataset.x.shape[0]
        if n_samples < _MIN_SLUG_SAMPLES:
            continue

        n_skipped = len(slug_metadata) - n_samples
        result = train_weights(
            dataset,
            learning_rate=learning_rate,
            max_iterations=max_iterations,
            l2_lambda=l2_lambda,
        )
        results[slug] = TrainingResult(
            weights=result.weights,
            bias=result.bias,
            accuracy=result.accuracy,
            log_loss=result.log_loss,
            n_samples=result.n_samples,
            n_skipped=n_skipped,
        )
    return results


def format_training_report(result: TrainingResult) -> str:
    """Format a human-readable training report.

    Display the learned weights alongside the default hand-tuned values
    and overall training metrics.

    Args:
        result: Completed training result.

    Returns:
        Multi-line string report.

    """
    lines = [
        "--- Weight Training Results ---",
        f"Samples: {result.n_samples}  Skipped: {result.n_skipped}",
        f"Accuracy: {result.accuracy:.4f}  Log-loss: {result.log_loss:.4f}",
        "",
        f"{'Weight':<18} {'Learned':>10} {'Default':>10} {'Delta':>10}",
        "-" * 52,
    ]

    for i, name in enumerate(WEIGHT_NAMES):
        learned = result.weights[name]
        default = _DEFAULT_WEIGHTS[i]
        delta = learned - default
        lines.append(f"{name:<18} {learned:>10.4f} {default:>10.4f} {delta:>+10.4f}")

    bias_delta = result.bias - _DEFAULT_BIAS
    lines.append(f"{'bias':<18} {result.bias:>10.4f} {_DEFAULT_BIAS:>10.4f} {bias_delta:>+10.4f}")

    return "\n".join(lines)


def format_all_slugs_report(
    global_result: TrainingResult,
    slug_results: dict[str, TrainingResult],
) -> str:
    """Format a combined report with global and per-slug training results.

    Display the global weights first, then each slug's weights with
    its accuracy and sample count.

    Args:
        global_result: Training result from all data combined.
        slug_results: Per-slug training results.

    Returns:
        Multi-line string report.

    """
    lines = [format_training_report(global_result)]

    for slug, result in sorted(slug_results.items()):
        lines.append("")
        lines.append(f"--- {slug} (n={result.n_samples}, acc={result.accuracy:.4f}) ---")
        lines.append(f"{'Weight':<18} {'Learned':>10} {'Global':>10} {'Delta':>10}")
        lines.append("-" * 52)
        for name in WEIGHT_NAMES:
            learned = result.weights[name]
            global_w = global_result.weights[name]
            delta = learned - global_w
            lines.append(f"{name:<18} {learned:>10.4f} {global_w:>10.4f} {delta:>+10.4f}")
        bias_delta = result.bias - global_result.bias
        lines.append(
            f"{'bias':<18} {result.bias:>10.4f} {global_result.bias:>10.4f} {bias_delta:>+10.4f}"
        )

    return "\n".join(lines)
