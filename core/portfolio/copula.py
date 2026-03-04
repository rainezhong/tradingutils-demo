"""
Copula models for multi-variate dependence.

Provides Gaussian and student-t copulas for modeling tail dependence
in portfolio allocation.
"""

from typing import Optional, Tuple
import logging
import numpy as np
from scipy import stats
from scipy.optimize import minimize_scalar

logger = logging.getLogger(__name__)


class GaussianCopula:
    """Gaussian copula (zero tail dependence)."""

    def __init__(self, correlation_matrix: np.ndarray):
        """Initialize Gaussian copula.

        Args:
            correlation_matrix: n x n correlation matrix
        """
        self.correlation_matrix = correlation_matrix
        self.n_dim = correlation_matrix.shape[0]

    def get_tail_dependence(self) -> Tuple[float, float]:
        """Return tail dependence coefficients.

        Returns:
            (lambda_L, lambda_U) — both are 0 for Gaussian copula
        """
        return (0.0, 0.0)

    def build_covariance_matrix(self, stds: np.ndarray) -> np.ndarray:
        """Build covariance matrix.

        Args:
            stds: n-vector of standard deviations

        Returns:
            n x n covariance matrix
        """
        std_matrix = np.diag(stds)
        return std_matrix @ self.correlation_matrix @ std_matrix

    def sample(self, n_samples: int, random_state: Optional[int] = None) -> np.ndarray:
        """Generate samples from Gaussian copula.

        Args:
            n_samples: Number of samples to generate
            random_state: Random seed for reproducibility

        Returns:
            (n_samples, n_dim) array of uniform marginals
        """
        rng = np.random.default_rng(random_state)

        # Sample from multivariate normal
        mean = np.zeros(self.n_dim)
        samples = rng.multivariate_normal(
            mean, self.correlation_matrix, size=n_samples
        )

        # Transform to uniform marginals via Gaussian CDF
        u = stats.norm.cdf(samples)

        return u


class StudentTCopula:
    """Student-t copula (symmetric tail dependence).

    The t-copula exhibits tail dependence — extreme events are more
    correlated than the correlation matrix alone would suggest.

    Tail dependence increases as degrees of freedom (df) decreases:
    - df → ∞: equivalent to Gaussian copula (λ = 0)
    - df = 5: moderate tail dependence (λ ≈ 0.15-0.25 for ρ=0.7)
    - df = 3: high tail dependence (λ ≈ 0.25-0.35 for ρ=0.7)
    """

    def __init__(
        self,
        correlation_matrix: np.ndarray,
        df: float,
    ):
        """Initialize student-t copula.

        Args:
            correlation_matrix: n x n correlation matrix
            df: Degrees of freedom (must be > 2)
        """
        if df <= 2:
            raise ValueError(f"df must be > 2, got {df}")

        self.correlation_matrix = correlation_matrix
        self.df = df
        self.n_dim = correlation_matrix.shape[0]

    def get_tail_dependence(self, correlation: Optional[float] = None) -> Tuple[float, float]:
        """Calculate tail dependence coefficients.

        For student-t copula, tail dependence is symmetric: λ_L = λ_U = λ

        Formula:
            λ = 2 * t_{df+1}(-√[(df+1)(1-ρ)/(1+ρ)])

        where t_{df+1} is the student-t CDF with df+1 degrees of freedom.

        Args:
            correlation: Pairwise correlation (if None, use average off-diagonal)

        Returns:
            (lambda_L, lambda_U) — symmetric for t-copula
        """
        if correlation is None:
            # Use average pairwise correlation
            n = self.correlation_matrix.shape[0]
            if n == 1:
                return (0.0, 0.0)

            # Extract off-diagonal elements
            mask = ~np.eye(n, dtype=bool)
            correlation = np.mean(self.correlation_matrix[mask])

        # Handle edge cases
        if abs(correlation) >= 1.0:
            # Perfect correlation → perfect tail dependence
            return (1.0, 1.0)

        if abs(correlation) < 1e-8:
            # Zero correlation → zero tail dependence
            return (0.0, 0.0)

        # Calculate tail dependence
        df = self.df
        arg = -np.sqrt((df + 1) * (1 - correlation) / (1 + correlation))

        # t_{df+1} CDF
        lambda_tail = 2 * stats.t.cdf(arg, df=df + 1)

        return (lambda_tail, lambda_tail)

    def build_covariance_matrix(self, stds: np.ndarray) -> np.ndarray:
        """Build covariance matrix.

        For the student-t copula, the *marginal* covariance is still
        Cov[i,j] = ρ[i,j] * σ[i] * σ[j], but the joint distribution
        has heavier tails (tail dependence).

        The tail dependence affects the *probability of joint extremes*,
        not the covariance itself. This is captured when we use the
        t-copula for risk calculations (VaR, CVaR, joint extreme scenarios).

        Args:
            stds: n-vector of standard deviations

        Returns:
            n x n covariance matrix
        """
        std_matrix = np.diag(stds)
        return std_matrix @ self.correlation_matrix @ std_matrix

    def sample(self, n_samples: int, random_state: Optional[int] = None) -> np.ndarray:
        """Generate samples from student-t copula.

        Args:
            n_samples: Number of samples to generate
            random_state: Random seed for reproducibility

        Returns:
            (n_samples, n_dim) array of uniform marginals
        """
        rng = np.random.default_rng(random_state)

        # Sample from multivariate t-distribution
        # Method: multivariate normal / sqrt(chi-square / df)
        mean = np.zeros(self.n_dim)

        # Sample standard multivariate normal
        z = rng.multivariate_normal(mean, self.correlation_matrix, size=n_samples)

        # Sample chi-square random variables
        chi2_samples = rng.chisquare(self.df, size=n_samples)

        # Construct multivariate t samples
        t_samples = z / np.sqrt(chi2_samples / self.df)[:, np.newaxis]

        # Transform to uniform marginals via t CDF
        u = stats.t.cdf(t_samples, df=self.df)

        return u


def estimate_t_copula_df(
    returns: np.ndarray,
    correlation_matrix: np.ndarray,
    method: str = "mle",
    df_bounds: Tuple[float, float] = (2.1, 30.0),
) -> float:
    """Estimate degrees of freedom for student-t copula.

    Args:
        returns: (n_samples, n_strategies) array of strategy returns
        correlation_matrix: n x n correlation matrix (pre-estimated)
        method: Estimation method ("mle" or "moment")
        df_bounds: (min_df, max_df) search bounds

    Returns:
        Estimated degrees of freedom
    """
    if method == "moment":
        # Moment-based estimation using kurtosis
        # For multivariate t: excess kurtosis ≈ 6 / (df - 4) if df > 4
        # Use average marginal excess kurtosis
        kurtosis_vals = []
        for i in range(returns.shape[1]):
            kurt = stats.kurtosis(returns[:, i], fisher=True)  # Excess kurtosis
            if kurt > 0:  # Avoid negative/zero kurtosis
                kurtosis_vals.append(kurt)

        if not kurtosis_vals:
            logger.warning("No positive excess kurtosis, defaulting to df=5")
            return 5.0

        avg_kurt = np.mean(kurtosis_vals)

        # Solve for df: avg_kurt ≈ 6 / (df - 4)
        # df ≈ 4 + 6 / avg_kurt
        df_est = 4.0 + 6.0 / avg_kurt

        # Clamp to bounds
        df_est = np.clip(df_est, df_bounds[0], df_bounds[1])

        logger.info(f"Moment-based df estimate: {df_est:.2f} (avg excess kurtosis: {avg_kurt:.3f})")
        return df_est

    elif method == "mle":
        # Maximum likelihood estimation
        # (Simplified: use univariate t fits per margin, then average)
        df_estimates = []

        for i in range(returns.shape[1]):
            margin = returns[:, i]

            # Standardize
            margin_std = (margin - np.mean(margin)) / np.std(margin)

            # Fit t-distribution via MLE
            try:
                params = stats.t.fit(margin_std)
                df_margin = params[0]  # First param is df

                # Only use reasonable estimates
                if df_bounds[0] <= df_margin <= df_bounds[1]:
                    df_estimates.append(df_margin)
            except (ValueError, RuntimeError):
                logger.warning(f"Failed to fit t-distribution to margin {i}")
                continue

        if not df_estimates:
            logger.warning("MLE failed for all margins, defaulting to df=5")
            return 5.0

        # Use median of estimates (robust to outliers)
        df_est = np.median(df_estimates)

        logger.info(
            f"MLE df estimate: {df_est:.2f} "
            f"(median of {len(df_estimates)} margin fits)"
        )
        return df_est

    else:
        raise ValueError(f"Unknown method: {method}")


def estimate_tail_dependence_empirical(
    returns: np.ndarray,
    strategy_i: int,
    strategy_j: int,
    quantile: float = 0.05,
) -> Tuple[float, float]:
    """Estimate empirical tail dependence between two strategies.

    Uses the empirical copula method:
    - Lower tail: fraction of joint occurrences in bottom quantile
    - Upper tail: fraction of joint occurrences in top quantile

    Args:
        returns: (n_samples, n_strategies) array
        strategy_i: Index of first strategy
        strategy_j: Index of second strategy
        quantile: Quantile threshold (e.g., 0.05 for 5%)

    Returns:
        (lambda_L_empirical, lambda_U_empirical)
    """
    n_samples = returns.shape[0]

    # Convert to ranks (empirical CDF)
    ranks_i = stats.rankdata(returns[:, strategy_i]) / (n_samples + 1)
    ranks_j = stats.rankdata(returns[:, strategy_j]) / (n_samples + 1)

    # Lower tail: both in bottom quantile
    lower_tail_i = ranks_i <= quantile
    lower_tail_j = ranks_j <= quantile
    joint_lower = np.sum(lower_tail_i & lower_tail_j)
    marginal_lower = np.sum(lower_tail_i)

    if marginal_lower > 0:
        lambda_L = joint_lower / marginal_lower
    else:
        lambda_L = 0.0

    # Upper tail: both in top quantile
    upper_tail_i = ranks_i >= (1 - quantile)
    upper_tail_j = ranks_j >= (1 - quantile)
    joint_upper = np.sum(upper_tail_i & upper_tail_j)
    marginal_upper = np.sum(upper_tail_i)

    if marginal_upper > 0:
        lambda_U = joint_upper / marginal_upper
    else:
        lambda_U = 0.0

    return (lambda_L, lambda_U)
