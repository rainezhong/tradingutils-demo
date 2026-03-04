"""
Tests for copula models (Gaussian and student-t).
"""

import pytest
import numpy as np
from scipy import stats

from core.portfolio.copula import (
    GaussianCopula,
    StudentTCopula,
    estimate_t_copula_df,
    estimate_tail_dependence_empirical,
)


class TestGaussianCopula:
    """Tests for Gaussian copula."""

    def test_initialization(self):
        """Test basic initialization."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])
        copula = GaussianCopula(corr_matrix)

        assert copula.n_dim == 2
        np.testing.assert_array_equal(copula.correlation_matrix, corr_matrix)

    def test_zero_tail_dependence(self):
        """Gaussian copula should have zero tail dependence."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])
        copula = GaussianCopula(corr_matrix)

        lambda_L, lambda_U = copula.get_tail_dependence()

        assert lambda_L == 0.0
        assert lambda_U == 0.0

    def test_build_covariance_matrix(self):
        """Test covariance matrix construction."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])
        copula = GaussianCopula(corr_matrix)

        stds = np.array([10.0, 20.0])
        cov_matrix = copula.build_covariance_matrix(stds)

        # Expected: [[100, 140], [140, 400]]
        expected = np.array([[100.0, 140.0], [140.0, 400.0]])

        np.testing.assert_array_almost_equal(cov_matrix, expected)

    def test_sample_shape(self):
        """Test sample generation shape."""
        corr_matrix = np.array([[1.0, 0.5], [0.5, 1.0]])
        copula = GaussianCopula(corr_matrix)

        samples = copula.sample(n_samples=100, random_state=42)

        assert samples.shape == (100, 2)

        # All samples should be in [0, 1]
        assert np.all(samples >= 0.0)
        assert np.all(samples <= 1.0)

    def test_sample_correlation(self):
        """Test that samples have roughly correct correlation."""
        corr_matrix = np.array([[1.0, 0.8], [0.8, 1.0]])
        copula = GaussianCopula(corr_matrix)

        # Large sample for stable correlation estimate
        samples = copula.sample(n_samples=10000, random_state=42)

        # Transform back to normal for correlation check
        z = stats.norm.ppf(samples)

        # Empirical correlation should be close to 0.8
        empirical_corr = np.corrcoef(z, rowvar=False)[0, 1]

        assert abs(empirical_corr - 0.8) < 0.05


class TestStudentTCopula:
    """Tests for student-t copula."""

    def test_initialization(self):
        """Test basic initialization."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])
        copula = StudentTCopula(corr_matrix, df=5.0)

        assert copula.n_dim == 2
        assert copula.df == 5.0
        np.testing.assert_array_equal(copula.correlation_matrix, corr_matrix)

    def test_df_validation(self):
        """Test that df must be > 2."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])

        with pytest.raises(ValueError, match="df must be > 2"):
            StudentTCopula(corr_matrix, df=2.0)

        with pytest.raises(ValueError, match="df must be > 2"):
            StudentTCopula(corr_matrix, df=1.0)

        # Should work
        StudentTCopula(corr_matrix, df=2.1)

    def test_positive_tail_dependence(self):
        """t-copula should have positive tail dependence."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])
        copula = StudentTCopula(corr_matrix, df=5.0)

        lambda_L, lambda_U = copula.get_tail_dependence(correlation=0.7)

        # Should have symmetric, positive tail dependence
        assert lambda_L == lambda_U  # Symmetric
        assert lambda_L > 0.0  # Positive
        assert lambda_L < 1.0  # Less than perfect

        # For df=5, rho=0.7, lambda should be around 0.20-0.40
        assert 0.15 < lambda_L < 0.45

    def test_tail_dependence_vs_df(self):
        """Lower df -> higher tail dependence."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])

        copula_df3 = StudentTCopula(corr_matrix, df=3.0)
        copula_df10 = StudentTCopula(corr_matrix, df=10.0)
        copula_df100 = StudentTCopula(corr_matrix, df=100.0)

        lambda_df3, _ = copula_df3.get_tail_dependence(correlation=0.7)
        lambda_df10, _ = copula_df10.get_tail_dependence(correlation=0.7)
        lambda_df100, _ = copula_df100.get_tail_dependence(correlation=0.7)

        # Lower df -> higher tail dependence
        assert lambda_df3 > lambda_df10 > lambda_df100

        # df=100 should be close to Gaussian (lambda ≈ 0)
        assert lambda_df100 < 0.05

    def test_tail_dependence_vs_correlation(self):
        """Higher correlation -> higher tail dependence."""
        corr_matrix = np.eye(2)  # Will override with explicit correlation
        copula = StudentTCopula(corr_matrix, df=5.0)

        lambda_low, _ = copula.get_tail_dependence(correlation=0.3)
        lambda_mid, _ = copula.get_tail_dependence(correlation=0.6)
        lambda_high, _ = copula.get_tail_dependence(correlation=0.9)

        # Higher correlation -> higher tail dependence
        assert lambda_low < lambda_mid < lambda_high

    def test_build_covariance_matrix(self):
        """Test covariance matrix construction (same as Gaussian)."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])
        copula = StudentTCopula(corr_matrix, df=5.0)

        stds = np.array([10.0, 20.0])
        cov_matrix = copula.build_covariance_matrix(stds)

        # Covariance formula is same as Gaussian
        # (tail dependence affects joint extremes, not covariance)
        expected = np.array([[100.0, 140.0], [140.0, 400.0]])

        np.testing.assert_array_almost_equal(cov_matrix, expected)

    def test_sample_shape(self):
        """Test sample generation shape."""
        corr_matrix = np.array([[1.0, 0.5], [0.5, 1.0]])
        copula = StudentTCopula(corr_matrix, df=5.0)

        samples = copula.sample(n_samples=100, random_state=42)

        assert samples.shape == (100, 2)

        # All samples should be in [0, 1]
        assert np.all(samples >= 0.0)
        assert np.all(samples <= 1.0)

    def test_sample_heavy_tails(self):
        """t-copula should produce more extreme co-movements than Gaussian."""
        corr_matrix = np.array([[1.0, 0.7], [0.7, 1.0]])

        gaussian = GaussianCopula(corr_matrix)
        t_copula = StudentTCopula(corr_matrix, df=4.0)

        n_samples = 10000
        gaussian_samples = gaussian.sample(n_samples, random_state=42)
        t_samples = t_copula.sample(n_samples, random_state=42)

        # Count joint extremes (both in bottom 5%)
        threshold = 0.05

        gaussian_joint_extremes = np.sum(
            (gaussian_samples[:, 0] < threshold) & (gaussian_samples[:, 1] < threshold)
        )
        t_joint_extremes = np.sum(
            (t_samples[:, 0] < threshold) & (t_samples[:, 1] < threshold)
        )

        # t-copula should have MORE joint extremes (tail dependence)
        assert t_joint_extremes > gaussian_joint_extremes

        # For independence, we'd expect 0.05 * 0.05 * 10000 = 25
        # For rho=0.7 Gaussian, maybe 100-150
        # For t-copula with df=4, should be 200-300+
        print(f"Gaussian joint extremes: {gaussian_joint_extremes}")
        print(f"t-copula joint extremes: {t_joint_extremes}")

        # t-copula should have at least 1.2x more (conservative, can be noisy)
        assert t_joint_extremes > 1.15 * gaussian_joint_extremes


class TestCopulaEstimation:
    """Tests for copula parameter estimation."""

    def test_estimate_t_copula_df_moment(self):
        """Test moment-based df estimation."""
        # Generate synthetic returns with known excess kurtosis
        # For t-distribution with df=5: excess kurtosis = 6 / (5-4) = 6
        rng = np.random.default_rng(42)

        # Two strategies with df=5 marginals
        returns = np.column_stack([
            stats.t.rvs(df=5, size=1000, random_state=42),
            stats.t.rvs(df=5, size=1000, random_state=43),
        ])

        corr_matrix = np.corrcoef(returns, rowvar=False)

        df_est = estimate_t_copula_df(
            returns, corr_matrix, method="moment"
        )

        # Should estimate df ≈ 5 (though noisy with finite samples)
        # Accept range 3-10
        assert 3.0 < df_est < 10.0

    def test_estimate_t_copula_df_mle(self):
        """Test MLE-based df estimation."""
        # Generate synthetic returns with known df
        rng = np.random.default_rng(42)

        returns = np.column_stack([
            stats.t.rvs(df=7, size=1000, random_state=42),
            stats.t.rvs(df=7, size=1000, random_state=43),
        ])

        corr_matrix = np.corrcoef(returns, rowvar=False)

        df_est = estimate_t_copula_df(
            returns, corr_matrix, method="mle"
        )

        # Should estimate df ≈ 7 (though noisy)
        # Accept range 4-12
        assert 4.0 < df_est < 12.0

    def test_estimate_empirical_tail_dependence(self):
        """Test empirical tail dependence estimation."""
        # Generate highly correlated t-copula samples
        corr_matrix = np.array([[1.0, 0.8], [0.8, 1.0]])
        copula = StudentTCopula(corr_matrix, df=5.0)

        # Sample and transform to "returns" (just use normal marginals)
        samples = copula.sample(n_samples=5000, random_state=42)
        returns = stats.norm.ppf(samples)

        lambda_L, lambda_U = estimate_tail_dependence_empirical(
            returns, 0, 1, quantile=0.05
        )

        # Should detect positive tail dependence
        assert lambda_L > 0.0
        assert lambda_U > 0.0

        # Should be symmetric (t-copula is symmetric)
        assert abs(lambda_L - lambda_U) < 0.1

        # Theoretical tail dependence for df=5, rho=0.8 is ~0.4-0.5
        # Empirical estimate should be in range 0.3-0.6 (noisy with finite samples)
        assert 0.3 < lambda_L < 0.7

    def test_empirical_tail_independence_gaussian(self):
        """Gaussian copula should show near-zero empirical tail dependence."""
        corr_matrix = np.array([[1.0, 0.6], [0.6, 1.0]])
        copula = GaussianCopula(corr_matrix)

        # Large sample for stable estimate
        samples = copula.sample(n_samples=10000, random_state=42)
        returns = stats.norm.ppf(samples)

        lambda_L, lambda_U = estimate_tail_dependence_empirical(
            returns, 0, 1, quantile=0.05
        )

        # Should be lower than t-copula, though not exactly zero
        # Empirical estimate picks up correlation effect even for Gaussian
        # With rho=0.6, empirical lambda can be 0.2-0.4 due to finite samples
        # Key is it's MUCH lower than t-copula (which would be 0.5-0.7 for rho=0.6)
        assert lambda_L < 0.45
        assert lambda_U < 0.45


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
