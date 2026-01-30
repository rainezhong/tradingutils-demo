"""
Knowledge Bases for Entity Recognition.

Contains alias databases for sports teams, financial indices,
and political candidates.
"""

from .teams import TEAMS, get_team_canonical, get_all_team_aliases
from .indices import INDICES, get_index_canonical, get_all_index_aliases
from .candidates import CANDIDATES, get_candidate_canonical, get_all_candidate_aliases

__all__ = [
    # Teams
    "TEAMS",
    "get_team_canonical",
    "get_all_team_aliases",
    # Indices
    "INDICES",
    "get_index_canonical",
    "get_all_index_aliases",
    # Candidates
    "CANDIDATES",
    "get_candidate_canonical",
    "get_all_candidate_aliases",
]
