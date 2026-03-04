"""Configuration for fill time probability estimation."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class FillTimeConfig:
    """All tunable parameters for the fill time estimation system."""

    # --- Snapshot Collection ---
    snapshot_interval_seconds: float = 3.0

    # --- Velocity Estimation ---
    fill_fraction_at_best: float = 0.80
    cancel_decay_per_cent: float = 0.10
    min_observations_for_estimate: int = 20
    velocity_ema_halflife_seconds: float = 120.0

    # --- Model ---
    model_type: str = "exponential"  # "exponential" or "gamma"
    prior_velocity: float = 0.05  # contracts/sec bootstrap before data
    prior_weight: float = 5.0  # weight of prior in Bayesian blending

    # --- Queue Position ---
    queue_position_pct: float = 0.5  # assumed position in queue (0=front, 1=back)

    # --- Spread Buckets ---
    spread_buckets: List[int] = field(default_factory=lambda: [0, 5, 10, 20, 100])

    # --- Snapshot Storage ---
    max_snapshots_per_file: int = 50000
    snapshot_dir: str = "data/depth_snapshots"

    # --- Integration Filters ---
    min_entry_fill_prob_60s: float = 0.3  # skip if P(fill in 60s) < this
    min_exit_fill_prob_60s: float = 0.2  # skip if P(exit fill in 60s) < this
