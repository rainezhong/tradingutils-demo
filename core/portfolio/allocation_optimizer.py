"""
Multi-variate Kelly criterion optimizer for portfolio allocation.

Implements constrained Kelly optimization to determine optimal capital
allocation across correlated strategies. Supports copula-based modeling
for tail dependence.
"""

from datetime import datetime
from typing import Dict, List, Optional, Union
import logging
import random
import numpy as np

from core.portfolio.types import (
    StrategyStats,
    AllocationResult,
    AllocationConfig,
)
from core.portfolio.copula import GaussianCopula, StudentTCopula


logger = logging.getLogger(__name__)


class AllocationOptimizer:
    """Optimize portfolio allocation using multi-variate Kelly."""

    def __init__(self, config: AllocationConfig):
        """Initialize allocation optimizer.

        Args:
            config: Allocation configuration
        """
        self.config = config

    def calculate_allocations(
        self,
        strategy_stats: Dict[str, StrategyStats],
        correlation_matrix: Optional[np.ndarray] = None,
        strategy_names: Optional[List[str]] = None,
        rebalance_reason: str = "manual",
        trade_pnls: Optional[Dict[str, List[float]]] = None,
        copula: Optional[Union[GaussianCopula, StudentTCopula]] = None,
    ) -> AllocationResult:
        """Calculate optimal allocations using multi-variate Kelly.

        Args:
            strategy_stats: Dict mapping strategy name to performance stats
            correlation_matrix: n x n correlation matrix (deprecated, use copula)
            strategy_names: Ordered list of strategy names (matches matrix order)
            rebalance_reason: Why rebalancing was triggered
            trade_pnls: Optional dict of strategy_name -> list of trade PnLs (for empirical Kelly)
            copula: Copula object (GaussianCopula or StudentTCopula). If None, uses correlation_matrix.

        Returns:
            AllocationResult with optimal allocations
        """
        # Infer strategy names if not provided
        if strategy_names is None:
            strategy_names = sorted(strategy_stats.keys())

        n = len(strategy_names)

        if n == 0:
            return AllocationResult(
                allocations={},
                total_allocated=0.0,
                expected_growth_rate=0.0,
                portfolio_variance=0.0,
                portfolio_sharpe=0.0,
                timestamp=datetime.now(),
                rebalance_reason=rebalance_reason,
            )

        # Extract edges and std devs
        edges = np.array([strategy_stats[s].edge for s in strategy_names])
        stds = np.array([strategy_stats[s].std_dev for s in strategy_names])

        # Apply empirical Kelly adjustment if enabled
        if self.config.use_empirical_kelly and trade_pnls:
            edges = self._apply_empirical_kelly_adjustment(
                edges, strategy_names, trade_pnls
            )

        # Filter negative edges (set to zero allocation)
        negative_mask = edges < 0
        if np.any(negative_mask):
            logger.warning(
                f"Negative edges detected: "
                f"{[strategy_names[i] for i in np.where(negative_mask)[0]]}"
            )
            edges[negative_mask] = 0.0

        # Build covariance matrix (copula-aware if provided)
        if copula is not None:
            cov_matrix = copula.build_covariance_matrix(stds)

            # Log tail dependence for t-copula
            if isinstance(copula, StudentTCopula):
                lambda_L, lambda_U = copula.get_tail_dependence()
                logger.info(
                    f"Using student-t copula: df={copula.df:.2f}, "
                    f"tail dependence λ={lambda_L:.3f}"
                )
            else:
                logger.info("Using Gaussian copula (zero tail dependence)")
        else:
            # Backward compatibility: use correlation matrix
            if correlation_matrix is None:
                raise ValueError(
                    "Must provide either copula or correlation_matrix"
                )
            cov_matrix = self._build_covariance_matrix(
                correlation_matrix, stds
            )
            logger.info("Using correlation matrix (Gaussian copula assumed)")

        # Solve multi-variate Kelly
        try:
            allocations = self._solve_kelly(edges, cov_matrix)
        except np.linalg.LinAlgError as e:
            logger.error(f"Kelly solver failed: {e}, falling back to equal weight")
            allocations = self._equal_weight_fallback(strategy_names)
            return self._build_result(
                strategy_names,
                allocations,
                edges,
                cov_matrix,
                rebalance_reason,
            )

        # Apply Kelly fraction (half Kelly)
        allocations = allocations * self.config.kelly_fraction

        # Apply constraints
        allocations = self._apply_constraints(allocations)

        return self._build_result(
            strategy_names,
            allocations,
            edges,
            cov_matrix,
            rebalance_reason,
        )

    def _build_covariance_matrix(
        self,
        correlation_matrix: np.ndarray,
        stds: np.ndarray,
    ) -> np.ndarray:
        """Build covariance matrix from correlation and standard deviations.

        Cov[i,j] = ρ[i,j] * σ[i] * σ[j]

        Args:
            correlation_matrix: n x n correlation matrix
            stds: n-vector of standard deviations

        Returns:
            n x n covariance matrix
        """
        # Diagonal matrix of std devs
        std_matrix = np.diag(stds)

        # Cov = D * Corr * D
        cov_matrix = std_matrix @ correlation_matrix @ std_matrix

        # Add ridge regularization for numerical stability
        n = cov_matrix.shape[0]
        cov_matrix += np.eye(n) * self.config.ridge_regularization

        return cov_matrix

    def _solve_kelly(
        self,
        edges: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> np.ndarray:
        """Solve multi-variate Kelly criterion.

        f* = Σ⁻¹ · m

        Where:
        - f* = vector of optimal fractions
        - Σ = covariance matrix
        - m = vector of mean returns (edges)

        Args:
            edges: n-vector of strategy edges
            cov_matrix: n x n covariance matrix

        Returns:
            n-vector of optimal fractions (unconstrained)
        """
        # Solve linear system: Σ · f* = m
        allocations = np.linalg.solve(cov_matrix, edges)

        return allocations

    def _apply_constraints(self, allocations: np.ndarray) -> np.ndarray:
        """Apply allocation constraints.

        1. Non-negative (zero if negative)
        2. Individual caps (max per strategy)
        3. Total allocation cap (max deployed)
        4. Minimum threshold (ignore small allocations)

        Args:
            allocations: Unconstrained allocations

        Returns:
            Constrained allocations
        """
        # 1. Non-negative
        allocations = np.maximum(allocations, 0.0)

        # 2. Individual caps
        allocations = np.minimum(
            allocations,
            self.config.max_allocation_per_strategy
        )

        # 3. Total allocation cap
        total = np.sum(allocations)
        if total > self.config.max_total_allocation:
            # Normalize to max total
            allocations = allocations * (
                self.config.max_total_allocation / total
            )

        # 4. Minimum threshold (zero out small allocations)
        # Only apply if we have multiple strategies and threshold would leave some allocation
        if len(allocations) > 1:
            allocations[allocations < self.config.min_allocation_threshold] = 0.0
        elif len(allocations) == 1 and allocations[0] > 0:
            # Single strategy: don't zero out if positive
            pass

        return allocations

    def _build_result(
        self,
        strategy_names: List[str],
        allocations: np.ndarray,
        edges: np.ndarray,
        cov_matrix: np.ndarray,
        rebalance_reason: str,
    ) -> AllocationResult:
        """Build allocation result with portfolio metrics.

        Args:
            strategy_names: Ordered list of strategy names
            allocations: n-vector of allocations
            edges: n-vector of edges
            cov_matrix: n x n covariance matrix
            rebalance_reason: Why rebalancing was triggered

        Returns:
            AllocationResult
        """
        allocation_dict = {
            name: float(alloc)
            for name, alloc in zip(strategy_names, allocations)
            if alloc > 0
        }

        total_allocated = float(np.sum(allocations))

        # Portfolio expected return
        portfolio_return = float(np.dot(allocations, edges))

        # Portfolio variance
        portfolio_variance = float(
            allocations @ cov_matrix @ allocations
        )

        # Portfolio Sharpe ratio
        portfolio_std = portfolio_variance ** 0.5
        portfolio_sharpe = (
            portfolio_return / portfolio_std
            if portfolio_std > 0
            else 0.0
        )

        # Geometric growth rate approximation: E[R] - 0.5 * Var[R]
        expected_growth_rate = portfolio_return - 0.5 * portfolio_variance

        return AllocationResult(
            allocations=allocation_dict,
            total_allocated=total_allocated,
            expected_growth_rate=expected_growth_rate,
            portfolio_variance=portfolio_variance,
            portfolio_sharpe=portfolio_sharpe,
            timestamp=datetime.now(),
            rebalance_reason=rebalance_reason,
        )

    def _equal_weight_fallback(
        self,
        strategy_names: List[str],
    ) -> np.ndarray:
        """Fallback to equal-weight allocation.

        Args:
            strategy_names: List of strategy names

        Returns:
            Equal-weight allocation vector
        """
        n = len(strategy_names)
        if n == 0:
            return np.array([])

        # Equal weight, respecting total allocation cap
        equal_weight = self.config.max_total_allocation / n

        # Respect individual caps
        equal_weight = min(equal_weight, self.config.max_allocation_per_strategy)

        return np.full(n, equal_weight)

    def _apply_empirical_kelly_adjustment(
        self,
        edges: np.ndarray,
        strategy_names: List[str],
        trade_pnls: Dict[str, List[float]],
    ) -> np.ndarray:
        """Apply empirical Kelly CV-based adjustment to edge estimates.

        Uses Monte Carlo resampling to estimate edge distribution uncertainty,
        then applies CV-based haircut: f_empirical = f_kelly × (1 - CV_edge)

        Args:
            edges: n-vector of point estimate edges
            strategy_names: Ordered list of strategy names
            trade_pnls: Dict mapping strategy name to list of trade PnLs

        Returns:
            Adjusted edges with CV haircut applied
        """
        adjusted_edges = edges.copy()

        for i, name in enumerate(strategy_names):
            pnls = trade_pnls.get(name)
            if not pnls or len(pnls) < 10:
                # Not enough data for empirical adjustment, use point estimate
                logger.info(
                    f"{name}: insufficient data for empirical Kelly "
                    f"({len(pnls) if pnls else 0} trades), using point estimate"
                )
                continue

            # Estimate edge distribution via Monte Carlo resampling
            edge_mean, edge_std = self._estimate_edge_uncertainty(pnls)

            # Calculate coefficient of variation
            if edge_mean > 0:
                cv_edge = edge_std / edge_mean
            else:
                # Negative or zero edge: no CV adjustment needed (will be zeroed anyway)
                cv_edge = 0.0

            # Apply CV-based haircut: f_empirical = f_kelly × (1 - CV_edge)
            # This is equivalent to adjusting the edge estimate
            haircut_factor = max(0.0, 1.0 - cv_edge)
            adjusted_edges[i] = edges[i] * haircut_factor

            logger.info(
                f"{name}: empirical Kelly adjustment - "
                f"CV={cv_edge:.3f}, haircut={haircut_factor:.3f}, "
                f"edge {edges[i]:.4f} -> {adjusted_edges[i]:.4f}"
            )

        return adjusted_edges

    def _estimate_edge_uncertainty(
        self,
        pnls: List[float],
    ) -> tuple:
        """Estimate edge distribution via Monte Carlo resampling.

        Resamples trades with replacement (bootstrap) to estimate the
        uncertainty in the edge estimate.

        Args:
            pnls: List of trade PnLs

        Returns:
            (mean_edge, std_edge) across Monte Carlo simulations
        """
        n_sims = self.config.empirical_kelly_simulations
        rng = random.Random(self.config.empirical_kelly_seed)

        edge_estimates = []

        for _ in range(n_sims):
            # Resample with replacement (bootstrap)
            resampled_pnls = [rng.choice(pnls) for _ in range(len(pnls))]

            # Calculate edge for this resampled history
            edge = sum(resampled_pnls) / len(resampled_pnls)
            edge_estimates.append(edge)

        # Calculate mean and std of edge estimates
        mean_edge = sum(edge_estimates) / len(edge_estimates)

        if len(edge_estimates) > 1:
            variance = sum((e - mean_edge) ** 2 for e in edge_estimates) / (len(edge_estimates) - 1)
            std_edge = variance ** 0.5
        else:
            std_edge = 0.0

        return mean_edge, std_edge
