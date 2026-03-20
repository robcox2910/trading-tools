"""CLI command for training directional estimator weights.

Fit logistic regression weights on historical market outcome data.
Extract features from Binance candles, order book snapshots, and whale
trades, then run gradient descent to find optimal weights for the
``P(Up) = sigmoid(dot(features, w))`` model.
"""

import asyncio
import os
from pathlib import Path
from typing import Annotated

import typer
import yaml

from trading_tools.apps.directional.backtest_runner import (
    BookSnapshotCache,
    WhaleTradeCache,
)
from trading_tools.apps.directional.weight_trainer import (
    TrainingResult,
    build_training_dataset,
    format_all_slugs_report,
    format_training_report,
    train_all_slugs,
    train_weights,
)
from trading_tools.apps.polymarket.backtest_common import (
    configure_verbose_logging,
    parse_date,
)
from trading_tools.apps.polymarket.cli.directional_backtest_cmd import fetch_binance_candles
from trading_tools.apps.tick_collector.repository import TickRepository
from trading_tools.apps.whale_monitor.repository import WhaleRepository
from trading_tools.clients.binance.client import BinanceAPIError
from trading_tools.core.models import Candle

_DEFAULT_DB_URL = os.environ.get("TICK_DB_URL", "sqlite+aiosqlite:///tick_data.db")
_DEFAULT_WHALE_DB_URL = os.environ.get("WHALE_DB_URL", "")

_MS_PER_SECOND = 1000


async def _fetch_candles_safe(
    assets: list[str], start_ts: int, end_ts: int, lookback: int
) -> dict[str, list[Candle]]:
    """Fetch Binance candles, skipping assets that fail (e.g. invalid symbols).

    Args:
        assets: Asset names to fetch.
        start_ts: Start epoch seconds.
        end_ts: End epoch seconds.
        lookback: Extra seconds before start for feature extraction.

    Returns:
        Mapping from asset name to candles, excluding failed assets.

    """
    try:
        return await fetch_binance_candles(assets, start_ts, end_ts, lookback)
    except BinanceAPIError:
        # Fall back to fetching one at a time, skipping failures
        result: dict[str, list[Candle]] = {}
        for asset in assets:
            try:
                batch = await fetch_binance_candles([asset], start_ts, end_ts, lookback)
                result.update(batch)
            except BinanceAPIError:
                typer.echo(f"  Skipping {asset} (not available on Binance)")
        return result


async def _load_and_train(
    *,
    start_ts: int,
    end_ts: int,
    db_url: str,
    whale_url: str,
    series_slug: str | None,
    entry_start: int,
    signal_lookback: int,
    learning_rate: float,
    max_iterations: int,
    l2_lambda: float,
    output_yaml: str | None,
    all_slugs: bool = False,
) -> None:
    """Load historical data, build training dataset, and fit weights.

    Args:
        start_ts: Start epoch seconds.
        end_ts: End epoch seconds.
        db_url: Tick data DB URL.
        whale_url: Whale trade DB URL.
        series_slug: Optional series slug filter.
        entry_start: Entry window start seconds.
        signal_lookback: Binance candle lookback seconds.
        learning_rate: Gradient descent step size.
        max_iterations: Max iterations for gradient descent.
        l2_lambda: L2 regularisation coefficient.
        output_yaml: Optional output YAML path.
        all_slugs: Train per-slug weights alongside global weights.

    """
    repo = TickRepository(db_url)
    w_repo = WhaleRepository(whale_url)
    try:
        metadata_list = await repo.get_market_metadata_in_range(
            start_ts, end_ts, series_slug=series_slug if not all_slugs else None
        )
        assets = sorted({m.asset for m in metadata_list})
        typer.echo(
            f"Found {len(metadata_list)} windows across {len(assets)} assets: {', '.join(assets)}"
        )

        candles_by_asset = await _fetch_candles_safe(assets, start_ts, end_ts, signal_lookback)

        snapshots = await repo.get_all_book_snapshots_in_range(
            start_ts * _MS_PER_SECOND, end_ts * _MS_PER_SECOND
        )
        snapshot_cache = BookSnapshotCache(snapshots)
        typer.echo(f"Loaded {len(snapshots)} order book snapshots")

        condition_ids = {m.condition_id for m in metadata_list}
        whale_trades = await w_repo.get_buy_trades_for_conditions(condition_ids)
        whale_cache = WhaleTradeCache(whale_trades)
        typer.echo(f"Loaded {len(whale_trades)} whale trades")
        typer.echo("")

        dataset = build_training_dataset(
            metadata_list,
            candles_by_asset,
            entry_window_start=entry_start,
            signal_lookback_seconds=signal_lookback,
            snapshot_cache=snapshot_cache,
            whale_cache=whale_cache,
        )

        n_skipped = len(metadata_list) - dataset.x.shape[0]
        typer.echo(f"Training dataset: {dataset.x.shape[0]} samples, {n_skipped} skipped")

        if dataset.x.shape[0] == 0:
            typer.echo("Error: no training samples — check date range and data", err=True)
            raise typer.Exit(code=1)

        result = train_weights(
            dataset,
            learning_rate=learning_rate,
            max_iterations=max_iterations,
            l2_lambda=l2_lambda,
        )
        result = TrainingResult(
            weights=result.weights,
            bias=result.bias,
            accuracy=result.accuracy,
            log_loss=result.log_loss,
            n_samples=result.n_samples,
            n_skipped=n_skipped,
        )

        if all_slugs:
            slug_results = train_all_slugs(
                metadata_list,
                candles_by_asset,
                entry_window_start=entry_start,
                signal_lookback_seconds=signal_lookback,
                snapshot_cache=snapshot_cache,
                whale_cache=whale_cache,
                learning_rate=learning_rate,
                max_iterations=max_iterations,
                l2_lambda=l2_lambda,
            )
            typer.echo("")
            typer.echo(format_all_slugs_report(result, slug_results))

            if output_yaml:
                _write_combined_yaml(result, slug_results, output_yaml)
                typer.echo(f"\nWeights written to {output_yaml}")
        else:
            typer.echo("")
            typer.echo(format_training_report(result))

            if output_yaml:
                _write_yaml(result, output_yaml)
                typer.echo(f"\nWeights written to {output_yaml}")

    finally:
        await repo.close()
        await w_repo.close()


def train_weights_cmd(
    start: Annotated[str, typer.Option(help="Start date YYYY-MM-DD")] = "",
    end: Annotated[str, typer.Option(help="End date YYYY-MM-DD")] = "",
    db_url: Annotated[
        str, typer.Option(help="SQLAlchemy async DB URL for tick data")
    ] = _DEFAULT_DB_URL,
    whale_db_url: Annotated[
        str, typer.Option(help="DB URL for whale trades (defaults to --db-url)")
    ] = _DEFAULT_WHALE_DB_URL,
    series_slug: Annotated[
        str | None, typer.Option("--series-slug", help="Filter to a specific series slug")
    ] = None,
    entry_start: Annotated[int, typer.Option(help="Seconds before close to evaluate entry")] = 30,
    signal_lookback: Annotated[int, typer.Option(help="Seconds of Binance candle lookback")] = 1200,
    learning_rate: Annotated[float, typer.Option(help="Gradient descent learning rate")] = 0.1,
    max_iterations: Annotated[
        int, typer.Option(help="Maximum gradient descent iterations")
    ] = 10_000,
    l2_lambda: Annotated[
        float, typer.Option(help="L2 regularisation coefficient (0 = none)")
    ] = 0.0,
    output_yaml: Annotated[
        str | None, typer.Option("--output-yaml", help="Write learned weights to YAML file")
    ] = None,
    all_slugs: Annotated[
        bool, typer.Option("--all-slugs", help="Train per-slug weights alongside global")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Enable verbose logging")
    ] = False,
) -> None:
    """Train directional estimator weights via logistic regression.

    Fit all 7 feature weights simultaneously on historical market
    outcome data using gradient descent.  The learned weights are
    mathematically optimal for the existing estimator model form
    and can be loaded directly into DirectionalConfig.

    Use ``--all-slugs`` to train separate weights for each series
    slug (e.g. btc-updown-5m, eth-updown-5m) alongside the global
    weights.  The output YAML includes a ``weights_by_slug`` section
    that ``DirectionalConfig.from_yaml()`` reads automatically.
    """
    if not start or not end:
        typer.echo("Error: --start and --end dates are required", err=True)
        raise typer.Exit(code=1)

    if verbose:
        configure_verbose_logging()

    start_ts = parse_date(start)
    end_ts = parse_date(end)
    if start_ts >= end_ts:
        typer.echo("Error: --start must be before --end", err=True)
        raise typer.Exit(code=1)

    typer.echo("Directional Weight Trainer")
    typer.echo(f"Period: {start} to {end}")
    typer.echo(f"Entry window start: {entry_start}s  Signal lookback: {signal_lookback}s")
    typer.echo(f"Learning rate: {learning_rate}  L2 lambda: {l2_lambda}")
    typer.echo("")

    asyncio.run(
        _load_and_train(
            start_ts=start_ts,
            end_ts=end_ts,
            db_url=db_url,
            whale_url=whale_db_url or db_url,
            series_slug=series_slug,
            entry_start=entry_start,
            signal_lookback=signal_lookback,
            learning_rate=learning_rate,
            max_iterations=max_iterations,
            l2_lambda=l2_lambda,
            output_yaml=output_yaml,
            all_slugs=all_slugs,
        )
    )


def _write_yaml(result: TrainingResult, path: str) -> None:
    """Write learned weights to a YAML config file.

    Output a YAML file compatible with ``DirectionalConfig.from_yaml()``.
    Only weight fields are written so they can be merged with other
    config values.

    Args:
        result: Training result containing the weight dict.
        path: Output file path.

    """
    data: dict[str, object] = {name: float(value) for name, value in result.weights.items()}
    data["bias"] = float(result.bias)

    output_path = Path(path)
    with output_path.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)


def _write_combined_yaml(
    global_result: TrainingResult,
    slug_results: dict[str, TrainingResult],
    path: str,
) -> None:
    """Write global and per-slug weights to a YAML config file.

    Output a YAML file with top-level global weights and a nested
    ``weights_by_slug`` section, compatible with
    ``DirectionalConfig.from_yaml()``.

    Args:
        global_result: Global training result.
        slug_results: Per-slug training results.
        path: Output file path.

    """
    data: dict[str, object] = {name: float(value) for name, value in global_result.weights.items()}
    data["bias"] = float(global_result.bias)
    if slug_results:
        weights_by_slug: dict[str, dict[str, float]] = {}
        for slug, result in sorted(slug_results.items()):
            slug_data = {name: float(value) for name, value in result.weights.items()}
            slug_data["bias"] = float(result.bias)
            weights_by_slug[slug] = slug_data
        data["weights_by_slug"] = weights_by_slug

    output_path = Path(path)
    with output_path.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
