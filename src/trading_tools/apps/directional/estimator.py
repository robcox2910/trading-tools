"""Probability estimator for the directional trading algorithm.

Transform a ``FeatureVector`` into P(Up) via a weighted ensemble.
The estimator computes the dot product of feature values and weights,
then passes the result through a logistic sigmoid to produce a
calibrated probability in ``(0, 1)``.

The estimator is injectable — the engine receives it as a constructor
parameter, making it swappable from this weighted ensemble to a trained
model in later phases.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_tools.core.models import ZERO

from .config import DirectionalConfig

if TYPE_CHECKING:
    from .models import FeatureVector


def _sigmoid(x: float) -> float:
    """Compute the logistic sigmoid function.

    Args:
        x: Input value.

    Returns:
        Sigmoid output in ``(0, 1)``.

    """
    return 1.0 / (1.0 + math.exp(-x))


class ProbabilityEstimator:
    """Weighted ensemble estimator that maps features to P(Up).

    Compute the dot product of feature values and configurable weights,
    then apply a logistic sigmoid to produce a probability.  When all
    features are zero (neutral), the output is 0.5 (no directional bias).

    Args:
        config: Configuration containing the six feature weights
            (``w_momentum``, ``w_volatility``, etc.).

    """

    def __init__(self, config: DirectionalConfig) -> None:
        """Initialize with feature weights from config.

        Args:
            config: Configuration with estimator weight fields.

        """
        self._weights: tuple[tuple[str, Decimal], ...] = (
            ("momentum", config.w_momentum),
            ("volatility_regime", config.w_volatility),
            ("volume_profile", config.w_volume),
            ("book_imbalance", config.w_book_imbalance),
            ("rsi_signal", config.w_rsi),
            ("price_change_pct", config.w_price_change),
            ("whale_signal", config.w_whale),
        )

    @classmethod
    def for_slug(cls, config: DirectionalConfig, slug: str | None) -> ProbabilityEstimator:
        """Create an estimator with slug-specific weights.

        Look up per-slug weight overrides from *config* and build a new
        ``DirectionalConfig`` with those weights applied, then return an
        estimator initialised from it.  When *slug* is ``None`` or has
        no overrides, the returned estimator uses global weights.

        Args:
            config: Base configuration with global weights and
                ``weights_by_slug`` overrides.
            slug: Series slug to resolve weights for, or ``None``.

        Returns:
            A ``ProbabilityEstimator`` with the appropriate weights.

        """
        slug_weights = config.weights_for_slug(slug)
        return cls(DirectionalConfig.with_overrides(config, **slug_weights))

    def estimate(self, features: FeatureVector) -> Decimal:
        """Estimate P(Up) from a feature vector.

        Compute the weighted sum of features, apply logistic sigmoid,
        and return a ``Decimal`` probability.

        Args:
            features: Normalised feature vector with values in ``[-1, 1]``.

        Returns:
            Estimated probability of Up winning, in ``(0, 1)``.

        """
        weighted_sum = ZERO
        for attr_name, weight in self._weights:
            feature_val = getattr(features, attr_name)
            weighted_sum += weight * feature_val

        return Decimal(str(_sigmoid(float(weighted_sum))))
