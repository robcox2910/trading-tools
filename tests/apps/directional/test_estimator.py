"""Tests for the probability estimator."""

from decimal import Decimal

from trading_tools.apps.directional.config import DirectionalConfig
from trading_tools.apps.directional.estimator import ProbabilityEstimator
from trading_tools.apps.directional.models import FeatureVector
from trading_tools.core.models import ZERO

_NEUTRAL = Decimal("0.5")
_TOLERANCE = Decimal("0.01")


def _make_features(
    momentum: Decimal = ZERO,
    volatility: Decimal = ZERO,
    volume: Decimal = ZERO,
    book: Decimal = ZERO,
    rsi: Decimal = ZERO,
    price: Decimal = ZERO,
) -> FeatureVector:
    """Create a FeatureVector with specified values, defaulting to zero."""
    return FeatureVector(
        momentum=momentum,
        volatility_regime=volatility,
        volume_profile=volume,
        book_imbalance=book,
        rsi_signal=rsi,
        price_change_pct=price,
    )


class TestProbabilityEstimator:
    """Test weighted ensemble probability estimator."""

    def test_neutral_features_give_half(self) -> None:
        """All-zero features produce P(Up) = 0.5 (no directional bias)."""
        config = DirectionalConfig()
        estimator = ProbabilityEstimator(config)
        features = _make_features()
        p_up = estimator.estimate(features)
        assert abs(p_up - _NEUTRAL) < _TOLERANCE

    def test_positive_features_above_half(self) -> None:
        """All-positive features produce P(Up) > 0.5."""
        config = DirectionalConfig()
        estimator = ProbabilityEstimator(config)
        features = _make_features(
            momentum=Decimal("0.8"),
            volatility=Decimal("0.5"),
            volume=Decimal("0.6"),
            book=Decimal("0.7"),
            rsi=Decimal("0.4"),
            price=Decimal("0.6"),
        )
        p_up = estimator.estimate(features)
        assert p_up > _NEUTRAL

    def test_negative_features_below_half(self) -> None:
        """All-negative features produce P(Up) < 0.5."""
        config = DirectionalConfig()
        estimator = ProbabilityEstimator(config)
        features = _make_features(
            momentum=Decimal("-0.8"),
            volatility=Decimal("-0.5"),
            volume=Decimal("-0.6"),
            book=Decimal("-0.7"),
            rsi=Decimal("-0.4"),
            price=Decimal("-0.6"),
        )
        p_up = estimator.estimate(features)
        assert p_up < _NEUTRAL

    def test_output_between_zero_and_one(self) -> None:
        """P(Up) is always in (0, 1)."""
        config = DirectionalConfig()
        estimator = ProbabilityEstimator(config)
        # Extreme positive features
        features = _make_features(
            momentum=Decimal(1),
            volatility=Decimal(1),
            volume=Decimal(1),
            book=Decimal(1),
            rsi=Decimal(1),
            price=Decimal(1),
        )
        p_up = estimator.estimate(features)
        assert ZERO < p_up < Decimal(1)

    def test_custom_weights(self) -> None:
        """Custom weights change the estimator output."""
        config_heavy_momentum = DirectionalConfig(
            w_momentum=Decimal("0.80"),
            w_volatility=Decimal("0.04"),
            w_volume=Decimal("0.04"),
            w_book_imbalance=Decimal("0.04"),
            w_rsi=Decimal("0.04"),
            w_price_change=Decimal("0.04"),
        )
        config_default = DirectionalConfig()
        est_heavy = ProbabilityEstimator(config_heavy_momentum)
        est_default = ProbabilityEstimator(config_default)

        features = _make_features(momentum=Decimal("0.9"))
        p_heavy = est_heavy.estimate(features)
        p_default = est_default.estimate(features)
        # Heavy momentum weight should produce a higher P(Up) for positive momentum
        assert p_heavy > p_default

    def test_symmetric_features(self) -> None:
        """Opposite feature signs produce symmetric probabilities around 0.5."""
        config = DirectionalConfig()
        estimator = ProbabilityEstimator(config)
        features_pos = _make_features(momentum=Decimal("0.5"))
        features_neg = _make_features(momentum=Decimal("-0.5"))
        p_pos = estimator.estimate(features_pos)
        p_neg = estimator.estimate(features_neg)
        # p_pos + p_neg should be approximately 1.0 (sigmoid symmetry)
        assert abs(p_pos + p_neg - Decimal(1)) < _TOLERANCE

    def test_positive_bias_shifts_baseline_above_half(self) -> None:
        """Positive bias shifts P(Up) above 0.5 when features are zero."""
        config = DirectionalConfig(bias=Decimal("0.5"))
        estimator = ProbabilityEstimator(config)
        features = _make_features()
        p_up = estimator.estimate(features)
        assert p_up > _NEUTRAL

    def test_negative_bias_shifts_baseline_below_half(self) -> None:
        """Negative bias shifts P(Up) below 0.5 when features are zero."""
        config = DirectionalConfig(bias=Decimal("-0.5"))
        estimator = ProbabilityEstimator(config)
        features = _make_features()
        p_up = estimator.estimate(features)
        assert p_up < _NEUTRAL

    def test_zero_bias_gives_half_for_neutral(self) -> None:
        """Zero bias with zero features gives exactly 0.5."""
        config = DirectionalConfig(bias=Decimal("0.0"))
        estimator = ProbabilityEstimator(config)
        features = _make_features()
        p_up = estimator.estimate(features)
        assert abs(p_up - _NEUTRAL) < _TOLERANCE


class TestForSlug:
    """Test slug-specific estimator construction."""

    def test_for_slug_none_uses_global_weights(self) -> None:
        """Passing None slug uses global weights (identical to default estimator)."""
        config = DirectionalConfig()
        est_global = ProbabilityEstimator(config)
        est_slug = ProbabilityEstimator.for_slug(config, None)
        features = _make_features(momentum=Decimal("0.5"))
        assert est_global.estimate(features) == est_slug.estimate(features)

    def test_for_slug_with_overrides(self) -> None:
        """Slug-specific weights produce different estimates than global."""
        config = DirectionalConfig(
            weights_by_slug={
                "btc-updown-5m": {"w_momentum": Decimal("2.0")},
            },
        )
        est_global = ProbabilityEstimator.for_slug(config, None)
        est_btc = ProbabilityEstimator.for_slug(config, "btc-updown-5m")
        features = _make_features(momentum=Decimal("0.5"))
        p_global = est_global.estimate(features)
        p_btc = est_btc.estimate(features)
        # BTC estimator has heavier momentum weight → higher P(Up)
        assert p_btc > p_global

    def test_for_slug_unknown_uses_global(self) -> None:
        """Unknown slug falls back to global weights."""
        config = DirectionalConfig(
            weights_by_slug={"btc-updown-5m": {"w_momentum": Decimal("2.0")}},
        )
        est_global = ProbabilityEstimator.for_slug(config, None)
        est_unknown = ProbabilityEstimator.for_slug(config, "sol-updown-5m")
        features = _make_features(momentum=Decimal("0.5"))
        assert est_global.estimate(features) == est_unknown.estimate(features)
