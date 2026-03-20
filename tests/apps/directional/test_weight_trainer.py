"""Tests for the directional weight trainer."""

from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np

from trading_tools.apps.directional.backtest_runner import (
    BookSnapshotCache,
    WhaleTradeCache,
)
from trading_tools.apps.directional.models import FeatureVector
from trading_tools.apps.directional.weight_trainer import (
    FEATURE_NAMES,
    WEIGHT_NAMES,
    TrainingDataset,
    TrainingResult,
    _feature_vector_to_array,
    _stable_sigmoid,
    build_training_dataset,
    format_all_slugs_report,
    format_training_report,
    train_all_slugs,
    train_weights,
)
from trading_tools.apps.whale_monitor.models import WhaleTrade
from trading_tools.core.models import Candle, Interval

_BASE_TS = 1_710_000_000
_WINDOW_DURATION = 300
_TOLERANCE = 1e-10


def _make_candle(ts: int, open_: float, close: float) -> Candle:
    """Create a test candle with reasonable defaults."""
    return Candle(
        symbol="BTC-USD",
        timestamp=ts,
        open=Decimal(str(open_)),
        high=Decimal(str(max(open_, close) + 1)),
        low=Decimal(str(min(open_, close) - 1)),
        close=Decimal(str(close)),
        volume=Decimal(1000),
        interval=Interval.M1,
    )


def _make_metadata(
    condition_id: str = "cond_1",
    asset: str = "BTC-USD",
    window_start: int = _BASE_TS,
    series_slug: str | None = None,
) -> MagicMock:
    """Create a mock MarketMetadata record."""
    meta = MagicMock()
    meta.condition_id = condition_id
    meta.title = f"{asset} Up or Down?"
    meta.asset = asset
    meta.up_token_id = f"{condition_id}_up"
    meta.down_token_id = f"{condition_id}_down"
    meta.window_start_ts = window_start
    meta.window_end_ts = window_start + _WINDOW_DURATION
    meta.series_slug = series_slug
    return meta


def _make_candles_for_window(
    window_start: int,
    n_candles: int = 20,
    direction: str = "Up",
) -> list[Candle]:
    """Create a series of candles spanning a window.

    Generate candles with a clear directional trend for testing
    outcome determination and feature extraction.

    Args:
        window_start: Window start epoch seconds.
        n_candles: Number of 1-min candles to generate.
        direction: ``"Up"`` for rising prices, ``"Down"`` for falling.

    Returns:
        List of candles with a clear trend.

    """
    candles: list[Candle] = []
    base_price = 50000.0
    # Start candles before the window so lookback has enough data
    lookback_start = window_start - 1200
    for i in range(n_candles):
        ts = lookback_start + i * 60
        if direction == "Up":
            open_ = base_price + i * 10
            close = base_price + (i + 1) * 10
        else:
            open_ = base_price - i * 10
            close = base_price - (i + 1) * 10
        candles.append(_make_candle(ts, open_, close))

    # Add candles within the window itself
    window_end = window_start + _WINDOW_DURATION
    for i in range(6):
        ts = window_start + i * 60
        if ts > window_end:
            break
        idx = n_candles + i
        if direction == "Up":
            open_ = base_price + idx * 10
            close = base_price + (idx + 1) * 10
        else:
            open_ = base_price - idx * 10
            close = base_price - (idx + 1) * 10
        candles.append(_make_candle(ts, open_, close))

    return candles


class TestStableSigmoid:
    """Test the numerically stable sigmoid implementation."""

    def test_zero_returns_half(self) -> None:
        """Sigmoid of zero is 0.5."""
        result = _stable_sigmoid(np.array([0.0]))
        assert abs(float(result[0]) - 0.5) < _TOLERANCE

    def test_large_positive_near_one(self) -> None:
        """Large positive input maps near 1.0."""
        result = _stable_sigmoid(np.array([100.0]))
        assert abs(float(result[0]) - 1.0) < _TOLERANCE

    def test_large_negative_near_zero(self) -> None:
        """Large negative input maps near 0.0."""
        result = _stable_sigmoid(np.array([-100.0]))
        assert abs(float(result[0])) < _TOLERANCE

    def test_symmetric(self) -> None:
        """Sigmoid is symmetric: sigmoid(x) + sigmoid(-x) = 1."""
        x = np.array([1.5, -1.5])
        result = _stable_sigmoid(x)
        assert abs(float(result[0] + result[1]) - 1.0) < _TOLERANCE

    def test_no_overflow_extreme_values(self) -> None:
        """Handle extreme values without overflow."""
        x = np.array([1000.0, -1000.0])
        result = _stable_sigmoid(x)
        assert np.isfinite(result).all()


class TestFeatureVectorToArray:
    """Test FeatureVector to numpy array conversion."""

    def test_converts_all_features(self) -> None:
        """Convert all 7 features to a float array."""
        fv = FeatureVector(
            momentum=Decimal("0.5"),
            volatility_regime=Decimal("-0.3"),
            volume_profile=Decimal("0.1"),
            book_imbalance=Decimal("0.2"),
            rsi_signal=Decimal("-0.1"),
            price_change_pct=Decimal("0.4"),
            whale_signal=Decimal("1.0"),
        )
        arr = _feature_vector_to_array(fv)
        assert arr.shape == (7,)
        assert float(arr[0]) == 0.5
        assert float(arr[6]) == 1.0

    def test_preserves_order(self) -> None:
        """Feature order matches FEATURE_NAMES."""
        fv = FeatureVector(
            momentum=Decimal("0.1"),
            volatility_regime=Decimal("0.2"),
            volume_profile=Decimal("0.3"),
            book_imbalance=Decimal("0.4"),
            rsi_signal=Decimal("0.5"),
            price_change_pct=Decimal("0.6"),
            whale_signal=Decimal("0.7"),
        )
        arr = _feature_vector_to_array(fv)
        for i, name in enumerate(FEATURE_NAMES):
            expected = float(getattr(fv, name))
            assert float(arr[i]) == expected


class TestBuildTrainingDataset:
    """Test training dataset construction from historical data."""

    def test_builds_dataset_from_clear_trends(self) -> None:
        """Build a dataset from windows with clear Up/Down outcomes."""
        meta_up = _make_metadata("c1", asset="BTC-USD", window_start=_BASE_TS)
        meta_down = _make_metadata("c2", asset="ETH-USD", window_start=_BASE_TS)

        candles_up = _make_candles_for_window(_BASE_TS, direction="Up")
        candles_down = _make_candles_for_window(_BASE_TS, direction="Down")

        dataset = build_training_dataset(
            [meta_up, meta_down],
            {"BTC-USD": candles_up, "ETH-USD": candles_down},
            signal_lookback_seconds=1200,
        )

        assert dataset.x.shape[0] == 2
        assert dataset.x.shape[1] == 7
        assert dataset.y[0] == 1.0  # Up
        assert dataset.y[1] == 0.0  # Down

    def test_skips_flat_outcomes(self) -> None:
        """Skip windows where the outcome is flat (open == close)."""
        meta = _make_metadata(window_start=_BASE_TS)
        # Create candles where first open == last close
        candles = [_make_candle(_BASE_TS, 50000.0, 50000.0)]

        dataset = build_training_dataset(
            [meta],
            {"BTC-USD": candles},
        )

        assert dataset.x.shape[0] == 0

    def test_skips_insufficient_candles(self) -> None:
        """Skip windows with fewer than 16 lookback candles."""
        meta = _make_metadata(window_start=_BASE_TS)
        # Only 2 candles — enough for outcome but not features
        candles = [
            _make_candle(_BASE_TS, 50000.0, 50010.0),
            _make_candle(_BASE_TS + 60, 50010.0, 50020.0),
        ]

        dataset = build_training_dataset(
            [meta],
            {"BTC-USD": candles},
        )

        assert dataset.x.shape[0] == 0

    def test_empty_metadata_returns_empty_dataset(self) -> None:
        """Return an empty dataset when no metadata is provided."""
        dataset = build_training_dataset([], {})
        assert dataset.x.shape == (0, 7)
        assert dataset.y.shape == (0,)

    def test_uses_snapshot_cache(self) -> None:
        """Use order book snapshots from cache when available."""
        meta = _make_metadata(window_start=_BASE_TS)
        candles = _make_candles_for_window(_BASE_TS, direction="Up")

        # Empty cache — falls back to default books
        cache = BookSnapshotCache([])
        dataset = build_training_dataset(
            [meta],
            {"BTC-USD": candles},
            snapshot_cache=cache,
        )
        assert dataset.x.shape[0] == 1

    def test_uses_whale_cache(self) -> None:
        """Include whale signal from cache when available."""
        meta = _make_metadata(window_start=_BASE_TS)
        candles = _make_candles_for_window(_BASE_TS, direction="Up")

        trade = WhaleTrade(
            whale_address="0xaaa",
            transaction_hash="tx_1",
            side="BUY",
            asset_id="asset_1",
            condition_id="cond_1",
            size=1000.0,
            price=0.60,
            timestamp=_BASE_TS,
            title="Test",
            slug="test",
            outcome="Up",
            outcome_index=0,
            collected_at=_BASE_TS * 1000,
        )
        whale_cache = WhaleTradeCache([trade])

        dataset = build_training_dataset(
            [meta],
            {"BTC-USD": candles},
            whale_cache=whale_cache,
        )
        assert dataset.x.shape[0] == 1
        # Whale signal is the last feature (index 6) and should be 1.0 for Up
        assert float(dataset.x[0, 6]) == 1.0


class TestTrainWeights:
    """Test logistic regression weight training."""

    def test_learns_positive_weight_for_predictive_feature(self) -> None:
        """Learn a positive weight for a feature that predicts Up."""
        rng = np.random.default_rng(42)
        n = 500
        x = np.zeros((n, 7), dtype=np.float64)
        # Feature 0 (momentum) predicts the label
        signal = rng.standard_normal(n)
        x[:, 0] = signal
        y = (signal > 0).astype(np.float64)

        dataset = TrainingDataset(x=x, y=y, feature_names=FEATURE_NAMES)
        result = train_weights(dataset, learning_rate=0.5, max_iterations=5000)

        assert result.weights["w_momentum"] > Decimal(0)
        assert result.accuracy > 0.7

    def test_converges_on_separable_data(self) -> None:
        """Achieve high accuracy on perfectly separable data."""
        n = 200
        x = np.zeros((n, 7), dtype=np.float64)
        # Feature 0 is +1 for Up, -1 for Down — perfectly separable
        x[:100, 0] = 1.0
        x[100:, 0] = -1.0
        y = np.array([1.0] * 100 + [0.0] * 100)

        dataset = TrainingDataset(x=x, y=y, feature_names=FEATURE_NAMES)
        result = train_weights(dataset, learning_rate=0.5, max_iterations=10000)

        assert result.accuracy > 0.95
        assert result.n_samples == 200

    def test_l2_regularisation_shrinks_weights(self) -> None:
        """L2 regularisation produces smaller weight magnitudes."""
        rng = np.random.default_rng(42)
        n = 300
        x = rng.standard_normal((n, 7))
        y = (x[:, 0] > 0).astype(np.float64)

        dataset = TrainingDataset(x=x, y=y, feature_names=FEATURE_NAMES)
        result_no_reg = train_weights(dataset, l2_lambda=0.0, max_iterations=5000)
        result_reg = train_weights(dataset, l2_lambda=1.0, max_iterations=5000)

        mag_no_reg = sum(abs(v) for v in result_no_reg.weights.values())
        mag_reg = sum(abs(v) for v in result_reg.weights.values())
        assert mag_reg < mag_no_reg

    def test_empty_dataset_returns_defaults(self) -> None:
        """Return default weights and zero bias when the dataset is empty."""
        dataset = TrainingDataset(
            x=np.empty((0, 7), dtype=np.float64),
            y=np.empty(0, dtype=np.float64),
            feature_names=FEATURE_NAMES,
        )
        result = train_weights(dataset)
        assert result.n_samples == 0
        assert result.weights["w_whale"] == Decimal("0.50")
        assert result.bias == Decimal("0.0")

    def test_returns_all_seven_weights(self) -> None:
        """Result contains all 7 weight names and a bias."""
        x = np.ones((10, 7), dtype=np.float64)
        y = np.array([1.0, 0.0] * 5)
        dataset = TrainingDataset(x=x, y=y, feature_names=FEATURE_NAMES)
        result = train_weights(dataset, max_iterations=10)

        assert len(result.weights) == 7
        for name in WEIGHT_NAMES:
            assert name in result.weights
        assert isinstance(result.bias, Decimal)

    def test_learns_positive_bias_from_skewed_data(self) -> None:
        """Learn a positive bias when most labels are Up (1.0)."""
        rng = np.random.default_rng(42)
        n = 400
        # 75% Up labels with zero features — bias must absorb the skew
        x = rng.standard_normal((n, 7)) * 0.01  # near-zero features
        up_ratio = 0.75
        y = np.zeros(n, dtype=np.float64)
        y[: int(n * up_ratio)] = 1.0

        dataset = TrainingDataset(x=x, y=y, feature_names=FEATURE_NAMES)
        result = train_weights(dataset, learning_rate=0.5, max_iterations=5000)

        assert result.bias > Decimal(0)

    def test_learns_negative_bias_from_skewed_data(self) -> None:
        """Learn a negative bias when most labels are Down (0.0)."""
        rng = np.random.default_rng(42)
        n = 400
        # 75% Down labels with zero features
        x = rng.standard_normal((n, 7)) * 0.01
        down_ratio = 0.75
        y = np.ones(n, dtype=np.float64)
        y[: int(n * down_ratio)] = 0.0

        dataset = TrainingDataset(x=x, y=y, feature_names=FEATURE_NAMES)
        result = train_weights(dataset, learning_rate=0.5, max_iterations=5000)

        assert result.bias < Decimal(0)


class TestFormatTrainingReport:
    """Test the training report formatter."""

    def test_contains_metrics(self) -> None:
        """Report includes accuracy, log-loss, and sample count."""
        result = TrainingResult(
            weights={name: Decimal("0.1") for name in WEIGHT_NAMES},
            bias=Decimal("0.05"),
            accuracy=0.75,
            log_loss=0.55,
            n_samples=1000,
            n_skipped=50,
        )
        report = format_training_report(result)
        assert "0.7500" in report  # accuracy
        assert "0.5500" in report  # log-loss
        assert "1000" in report
        assert "50" in report

    def test_contains_all_weight_names(self) -> None:
        """Report lists all 7 weight names and bias."""
        result = TrainingResult(
            weights={name: Decimal("0.2") for name in WEIGHT_NAMES},
            bias=Decimal("0.0"),
            accuracy=0.8,
            log_loss=0.4,
            n_samples=500,
            n_skipped=0,
        )
        report = format_training_report(result)
        for name in WEIGHT_NAMES:
            assert name in report
        assert "bias" in report

    def test_shows_delta_from_defaults(self) -> None:
        """Report shows the difference between learned and default weights."""
        weights = {name: Decimal("0.1") for name in WEIGHT_NAMES}
        result = TrainingResult(
            weights=weights,
            bias=Decimal("0.0"),
            accuracy=0.7,
            log_loss=0.5,
            n_samples=100,
            n_skipped=0,
        )
        report = format_training_report(result)
        # w_whale default is 0.50, learned is 0.10, delta is -0.40
        assert "-0.4000" in report


class TestTrainAllSlugs:
    """Test per-slug weight training."""

    def test_groups_by_slug_and_trains(self) -> None:
        """Train separate weights for each slug with enough samples."""
        n_per_slug = 60
        metadata: list[MagicMock] = []
        candles: dict[str, list[Candle]] = {}
        for i in range(n_per_slug):
            ts = _BASE_TS + i * _WINDOW_DURATION * 2
            meta_btc = _make_metadata(f"btc_{i}", "BTC-USD", ts, series_slug="btc-updown-5m")
            meta_eth = _make_metadata(f"eth_{i}", "ETH-USD", ts, series_slug="eth-updown-5m")
            metadata.extend([meta_btc, meta_eth])

        candles["BTC-USD"] = _make_candles_for_window(_BASE_TS, direction="Up")
        candles["ETH-USD"] = _make_candles_for_window(_BASE_TS, direction="Down")

        # Extend candles to cover all windows
        for i in range(1, n_per_slug):
            ts = _BASE_TS + i * _WINDOW_DURATION * 2
            candles["BTC-USD"].extend(_make_candles_for_window(ts, direction="Up"))
            candles["ETH-USD"].extend(_make_candles_for_window(ts, direction="Down"))

        results = train_all_slugs(
            metadata,
            candles,
            learning_rate=0.5,
            max_iterations=1000,
        )

        assert "btc-updown-5m" in results
        assert "eth-updown-5m" in results
        assert results["btc-updown-5m"].n_samples >= _MIN_SLUG_SAMPLES
        assert results["eth-updown-5m"].n_samples >= _MIN_SLUG_SAMPLES

    def test_skips_slug_with_few_samples(self) -> None:
        """Skip slugs with fewer than 50 samples."""
        # Only 2 metadata records for one slug
        metadata = [
            _make_metadata("c1", "BTC-USD", _BASE_TS, series_slug="btc-updown-5m"),
            _make_metadata("c2", "BTC-USD", _BASE_TS + 600, series_slug="btc-updown-5m"),
        ]
        candles = {"BTC-USD": _make_candles_for_window(_BASE_TS, direction="Up")}

        results = train_all_slugs(metadata, candles)
        assert "btc-updown-5m" not in results

    def test_skips_none_slug(self) -> None:
        """Skip metadata records with no series_slug."""
        metadata = [_make_metadata("c1", "BTC-USD", _BASE_TS, series_slug=None)]
        candles = {"BTC-USD": _make_candles_for_window(_BASE_TS, direction="Up")}

        results = train_all_slugs(metadata, candles)
        assert results == {}


_MIN_SLUG_SAMPLES = 50


class TestFormatAllSlugsReport:
    """Test the combined report formatter."""

    def test_contains_slug_headers(self) -> None:
        """Report includes per-slug section headers."""
        global_result = TrainingResult(
            weights={name: Decimal("0.1") for name in WEIGHT_NAMES},
            bias=Decimal("0.05"),
            accuracy=0.75,
            log_loss=0.5,
            n_samples=1000,
            n_skipped=0,
        )
        slug_results = {
            "btc-updown-5m": TrainingResult(
                weights={name: Decimal("0.2") for name in WEIGHT_NAMES},
                bias=Decimal("0.10"),
                accuracy=0.80,
                log_loss=0.4,
                n_samples=500,
                n_skipped=10,
            ),
        }
        report = format_all_slugs_report(global_result, slug_results)
        assert "btc-updown-5m" in report
        assert "n=500" in report
        assert "acc=0.8000" in report
