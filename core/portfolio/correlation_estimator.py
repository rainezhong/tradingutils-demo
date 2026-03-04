"""
Correlation estimator for portfolio strategies.

Blends empirical sample correlation with domain-knowledge priors using
shrinkage estimation. Supports copula-based modeling for tail dependence.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional, Tuple
import logging
import numpy as np

from core.portfolio.types import StrategyTrade, PortfolioConfig
from core.portfolio.copula import (
    GaussianCopula,
    StudentTCopula,
    estimate_t_copula_df,
    estimate_tail_dependence_empirical,
)


logger = logging.getLogger(__name__)


class CorrelationEstimator:
    """Estimate correlation matrix between strategies."""

    def __init__(self, config: PortfolioConfig):
        """Initialize correlation estimator.

        Args:
            config: Portfolio configuration with prior correlations
        """
        self.config = config

    def estimate_correlation_matrix(
        self,
        strategy_trades: Dict[str, List[StrategyTrade]],
    ) -> np.ndarray:
        """Estimate correlation matrix using shrinkage.

        Args:
            strategy_trades: Dict mapping strategy name to list of trades

        Returns:
            n x n correlation matrix (numpy array)
        """
        strategy_names = sorted(strategy_trades.keys())
        n = len(strategy_names)

        if n == 0:
            return np.array([[]])

        if n == 1:
            return np.array([[1.0]])

        # Calculate sample correlation
        sample_corr = self._calculate_sample_correlation(
            strategy_names, strategy_trades
        )

        # Build prior correlation matrix
        prior_corr = self._build_prior_matrix(strategy_names, strategy_trades)

        # Apply shrinkage: blend sample + prior
        shrinkage = self.config.correlation_shrinkage
        final_corr = shrinkage * sample_corr + (1 - shrinkage) * prior_corr

        # Ensure positive semi-definite (numerical stability)
        final_corr = self._ensure_psd(final_corr)

        logger.debug(
            f"Correlation matrix (shrinkage={shrinkage:.2f}):\n{final_corr}"
        )

        return final_corr

    def _calculate_sample_correlation(
        self,
        strategy_names: List[str],
        strategy_trades: Dict[str, List[StrategyTrade]],
    ) -> np.ndarray:
        """Calculate empirical correlation from time-aligned returns.

        Args:
            strategy_names: Ordered list of strategy names
            strategy_trades: Dict mapping strategy name to trades

        Returns:
            n x n correlation matrix
        """
        n = len(strategy_names)

        # Align trades into 5-minute time buckets
        bucket_returns = self._align_returns_by_time(
            strategy_names, strategy_trades, bucket_size_min=5
        )

        if not bucket_returns or len(bucket_returns) < 2:
            logger.warning("Insufficient data for sample correlation")
            # Return identity matrix (uncorrelated)
            return np.eye(n)

        # Convert to numpy array (time x strategies)
        returns_matrix = np.array(
            [[bucket_returns[t][s] for s in strategy_names]
             for t in sorted(bucket_returns.keys())]
        )

        # Calculate correlation matrix
        if returns_matrix.shape[0] < 2:
            return np.eye(n)

        # Handle zero variance (no trades in strategy)
        stds = np.std(returns_matrix, axis=0)
        if np.any(stds == 0):
            logger.warning("Zero variance detected, using identity")
            return np.eye(n)

        corr_matrix = np.corrcoef(returns_matrix, rowvar=False)

        # Handle NaN (can occur with insufficient data)
        if np.any(np.isnan(corr_matrix)):
            logger.warning("NaN in correlation matrix, using identity")
            return np.eye(n)

        return corr_matrix

    def _align_returns_by_time(
        self,
        strategy_names: List[str],
        strategy_trades: Dict[str, List[StrategyTrade]],
        bucket_size_min: int = 5,
    ) -> Dict[datetime, Dict[str, float]]:
        """Align trades into time buckets and calculate per-bucket returns.

        Args:
            strategy_names: List of strategy names
            strategy_trades: Dict mapping strategy name to trades
            bucket_size_min: Size of time bucket in minutes

        Returns:
            Dict mapping bucket timestamp to dict of strategy returns
        """
        bucket_size_sec = bucket_size_min * 60

        # Bucket all trades
        buckets: Dict[datetime, Dict[str, List[float]]] = {}

        for strategy_name in strategy_names:
            trades = strategy_trades[strategy_name]

            for trade in trades:
                # Only use settled trades with PnL
                if trade.pnl is None:
                    continue

                # Round timestamp to bucket
                bucket_ts = datetime.fromtimestamp(
                    (trade.timestamp.timestamp() // bucket_size_sec)
                    * bucket_size_sec
                )

                if bucket_ts not in buckets:
                    buckets[bucket_ts] = {s: [] for s in strategy_names}

                buckets[bucket_ts][strategy_name].append(trade.pnl)

        # Calculate per-bucket returns (sum of PnLs)
        bucket_returns = {}
        for bucket_ts, strategy_pnls in buckets.items():
            bucket_returns[bucket_ts] = {
                strategy: sum(pnls) for strategy, pnls in strategy_pnls.items()
            }

        return bucket_returns

    def _build_prior_matrix(
        self,
        strategy_names: List[str],
        strategy_trades: Dict[str, List[StrategyTrade]],
    ) -> np.ndarray:
        """Build prior correlation matrix from config.

        Args:
            strategy_names: Ordered list of strategy names
            strategy_trades: Dict mapping strategy name to trades

        Returns:
            n x n prior correlation matrix
        """
        n = len(strategy_names)
        prior = np.eye(n)  # Diagonal is 1.0

        for i, strategy_i in enumerate(strategy_names):
            for j, strategy_j in enumerate(strategy_names):
                if i >= j:
                    continue  # Already filled diagonal, and matrix is symmetric

                # Get prior correlation from config
                prior_corr = self.config.get_prior_correlation(
                    strategy_i, strategy_j
                )

                # Check market overlap
                overlap = self._calculate_market_overlap(
                    strategy_trades[strategy_i],
                    strategy_trades[strategy_j],
                )

                if overlap > self.config.market_overlap_threshold:
                    # Force higher correlation for overlapping markets
                    prior_corr = max(
                        prior_corr,
                        self.config.market_overlap_correlation
                    )
                    logger.info(
                        f"Market overlap {overlap:.1%} between "
                        f"{strategy_i} and {strategy_j}, "
                        f"using correlation {prior_corr:.2f}"
                    )

                prior[i, j] = prior_corr
                prior[j, i] = prior_corr  # Symmetric

        return prior

    def _calculate_market_overlap(
        self,
        trades_i: List[StrategyTrade],
        trades_j: List[StrategyTrade],
    ) -> float:
        """Calculate fraction of overlapping tickers between two strategies.

        Args:
            trades_i: Trades from strategy i
            trades_j: Trades from strategy j

        Returns:
            Overlap ratio (0.0 to 1.0)
        """
        if not trades_i or not trades_j:
            return 0.0

        tickers_i = set(t.ticker for t in trades_i)
        tickers_j = set(t.ticker for t in trades_j)

        if not tickers_i or not tickers_j:
            return 0.0

        overlap = len(tickers_i & tickers_j)
        total = len(tickers_i | tickers_j)

        return overlap / total if total > 0 else 0.0

    def _ensure_psd(self, matrix: np.ndarray) -> np.ndarray:
        """Ensure matrix is positive semi-definite.

        Args:
            matrix: Correlation matrix

        Returns:
            Adjusted matrix (guaranteed PSD)
        """
        # Eigenvalue decomposition
        eigenvalues, eigenvectors = np.linalg.eigh(matrix)

        # Clamp negative eigenvalues to small positive value
        eigenvalues = np.maximum(eigenvalues, 1e-8)

        # Reconstruct matrix
        psd_matrix = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T

        # Ensure diagonal is exactly 1.0
        n = psd_matrix.shape[0]
        for i in range(n):
            psd_matrix[i, i] = 1.0

        return psd_matrix

    def fit_copula(
        self,
        strategy_trades: Dict[str, List[StrategyTrade]],
        copula_type: str = "gaussian",
        df: Optional[float] = None,
    ):
        """Fit copula to strategy returns.

        Args:
            strategy_trades: Dict mapping strategy name to list of trades
            copula_type: "gaussian" or "student-t"
            df: Degrees of freedom for student-t (if None, will estimate)

        Returns:
            Copula object (GaussianCopula or StudentTCopula)
        """
        strategy_names = sorted(strategy_trades.keys())

        # Estimate correlation matrix
        correlation_matrix = self.estimate_correlation_matrix(strategy_trades)

        if copula_type == "gaussian":
            copula = GaussianCopula(correlation_matrix)
            logger.info("Fitted Gaussian copula (zero tail dependence)")
            return copula

        elif copula_type == "student-t":
            if df is None:
                # Estimate degrees of freedom from data
                returns_matrix = self._get_returns_matrix(
                    strategy_names, strategy_trades
                )
                if returns_matrix is None or returns_matrix.shape[0] < 10:
                    logger.warning(
                        "Insufficient data for df estimation, defaulting to df=5"
                    )
                    df = 5.0
                else:
                    df = estimate_t_copula_df(
                        returns_matrix, correlation_matrix, method="moment"
                    )

            copula = StudentTCopula(correlation_matrix, df)

            # Log tail dependence
            lambda_L, lambda_U = copula.get_tail_dependence()
            logger.info(
                f"Fitted student-t copula: df={df:.2f}, "
                f"tail dependence λ={lambda_L:.3f}"
            )

            return copula

        else:
            raise ValueError(f"Unknown copula type: {copula_type}")

    def estimate_empirical_tail_dependence(
        self,
        strategy_trades: Dict[str, List[StrategyTrade]],
        strategy_i: str,
        strategy_j: str,
        quantile: float = 0.05,
    ) -> Tuple[float, float]:
        """Estimate empirical tail dependence between two strategies.

        Args:
            strategy_trades: Dict mapping strategy name to trades
            strategy_i: Name of first strategy
            strategy_j: Name of second strategy
            quantile: Tail quantile threshold (default 5%)

        Returns:
            (lambda_L, lambda_U) empirical tail dependence coefficients
        """
        strategy_names = [strategy_i, strategy_j]

        returns_matrix = self._get_returns_matrix(strategy_names, strategy_trades)

        if returns_matrix is None or returns_matrix.shape[0] < 20:
            logger.warning(
                f"Insufficient data for tail dependence estimation "
                f"between {strategy_i} and {strategy_j}"
            )
            return (0.0, 0.0)

        lambda_L, lambda_U = estimate_tail_dependence_empirical(
            returns_matrix, 0, 1, quantile=quantile
        )

        logger.info(
            f"Empirical tail dependence ({strategy_i}, {strategy_j}): "
            f"λ_L={lambda_L:.3f}, λ_U={lambda_U:.3f}"
        )

        return (lambda_L, lambda_U)

    def _get_returns_matrix(
        self,
        strategy_names: List[str],
        strategy_trades: Dict[str, List[StrategyTrade]],
    ) -> Optional[np.ndarray]:
        """Get aligned returns matrix for copula estimation.

        Args:
            strategy_names: Ordered list of strategy names
            strategy_trades: Dict mapping strategy name to trades

        Returns:
            (n_samples, n_strategies) returns matrix, or None if insufficient data
        """
        # Align trades into time buckets
        bucket_returns = self._align_returns_by_time(
            strategy_names, strategy_trades, bucket_size_min=5
        )

        if not bucket_returns or len(bucket_returns) < 2:
            return None

        # Convert to numpy array (time x strategies)
        returns_matrix = np.array(
            [[bucket_returns[t][s] for s in strategy_names]
             for t in sorted(bucket_returns.keys())]
        )

        if returns_matrix.shape[0] < 2:
            return None

        return returns_matrix
