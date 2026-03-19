"""Logistic regression weight trainer for the directional estimator.

Extract features and outcomes from historical market windows, then fit
optimal estimator weights via gradient descent.  The learned weights
slot directly into ``DirectionalConfig`` — the model form is identical
to the hand-tuned weighted ensemble: ``P(Up) = sigmoid(dot(features, w))``.

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
from .models import FeatureVector

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
)

WEIGHT_NAMES: tuple[str, ...] = (
    "w_momentum",
    "w_volatility",
    "w_volume",
    "w_book_imbalance",
    "w_rsi",
    "w_price_change",
    "w_whale",
)

_DEFAULT_WEIGHTS: tuple[Decimal, ...] = (
    Decimal("0.15"),
    Decimal("0.05"),
    Decimal("0.05"),
    Decimal("0.10"),
    Decimal("0.05"),
    Decimal("0.10"),
    Decimal("0.50"),
)

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
        accuracy: Fraction of correctly classified training samples.
        log_loss: Mean negative log-likelihood on training data.
        n_samples: Number of samples used for training.
        n_skipped: Number of windows skipped (flat outcome, insufficient
            candles, etc.).

    """

    weights: dict[str, Decimal]
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

        # Whale signal
        whale_direction: str | None = None
        if whale_cache is not None:
            whale_direction = whale_cache.get_signal(meta.condition_id, before_ts=entry_eval_ts)

        features = extract_features(
            lookback_candles,
            up_book,
            down_book,
            whale_direction=whale_direction,
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
    """Fit logistic regression weights via gradient descent.

    Minimise the binary cross-entropy loss (with optional L2
    regularisation) using batch gradient descent.  No intercept term
    is used, matching the current estimator form where all features
    are zero → P(Up) = 0.5.

    Args:
        dataset: Training data with feature matrix and labels.
        learning_rate: Step size for gradient descent.
        max_iterations: Maximum number of gradient descent iterations.
        tolerance: Stop when the absolute change in loss is below this
            threshold.
        l2_lambda: L2 regularisation coefficient.  Zero disables
            regularisation.

    Returns:
        A ``TrainingResult`` with the learned weights and metrics.

    """
    x = dataset.x
    y = dataset.y
    n = len(y)

    if n == 0:
        return TrainingResult(
            weights=dict(zip(WEIGHT_NAMES, _DEFAULT_WEIGHTS, strict=True)),
            accuracy=0.0,
            log_loss=0.0,
            n_samples=0,
            n_skipped=0,
        )

    w = np.zeros(x.shape[1], dtype=np.float64)
    prev_loss = float("inf")

    for _ in range(max_iterations):
        z = x @ w
        p = _stable_sigmoid(z)

        gradient = x.T @ (p - y) / n
        if l2_lambda > 0:
            gradient += l2_lambda * w

        w -= learning_rate * gradient

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
    final_p = _stable_sigmoid(x @ w)
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
        accuracy=accuracy,
        log_loss=log_loss_val,
        n_samples=n,
        n_skipped=0,
    )


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

    return "\n".join(lines)
