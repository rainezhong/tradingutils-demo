#!/usr/bin/env python3
"""
NBA Scoring Dashboard

Interactive dashboard to visualize NBA game scoring progression.
Features:
- Team selection sidebar
- Game-by-game or aggregate view
- Point scoring progression over time
- Statistical analysis

Run with: streamlit run dashboard/nba_scoring_dashboard.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from typing import List, Dict, Tuple
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="NBA Scoring Dashboard",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# NBA TEAMS DATA
# =============================================================================
NBA_TEAMS = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "LA Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}


# =============================================================================
# DATA LOADING
# =============================================================================
@st.cache_data(ttl=300)
def load_game_data() -> pd.DataFrame:
    """Load NBA game data from CSV."""
    data_path = Path(__file__).parent.parent / "data" / "nba_ev_raw.csv"

    if data_path.exists():
        df = pd.read_csv(data_path)

        # Parse dates
        if "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"])

        # Add minutes_until_game_end if not present
        if "minutes_until_game_end" not in df.columns:
            game_end_times = df.groupby("event_ticker")[
                "minutes_until_settlement"
            ].min()
            df["settlement_delay"] = df["event_ticker"].map(game_end_times)
            df["minutes_until_game_end"] = (
                df["minutes_until_settlement"] - df["settlement_delay"]
            )

        # Add game_minute (elapsed time from start)
        max_time = df.groupby("event_ticker")["minutes_until_game_end"].max()
        df["max_game_time"] = df["event_ticker"].map(max_time)
        df["game_minute"] = df["max_game_time"] - df["minutes_until_game_end"]

        return df
    else:
        st.error(f"Data file not found: {data_path}")
        return pd.DataFrame()


def extract_team_from_title(title: str) -> Tuple[str, str]:
    """Extract team names from game title like 'Team A at Team B Winner?'"""
    try:
        clean = title.replace(" Winner?", "").replace(" Winner", "")
        if " at " in clean:
            parts = clean.split(" at ")
            return parts[0].strip(), parts[1].strip()
    except:
        pass
    return "", ""


def get_team_abbreviation(team_name: str) -> str:
    """Get team abbreviation from full name."""
    for abbr, full_name in NBA_TEAMS.items():
        if abbr.lower() in team_name.lower() or full_name.lower() in team_name.lower():
            return abbr
        # Check partial matches
        if any(word.lower() in team_name.lower() for word in full_name.split()):
            return abbr
    return team_name[:3].upper()


# =============================================================================
# VISUALIZATION FUNCTIONS
# =============================================================================
def create_scoring_progression_chart(
    game_data: pd.DataFrame, title: str = "Scoring Progression"
) -> go.Figure:
    """Create a line chart showing scoring progression (home_mid as probability)."""

    fig = go.Figure()

    # Sort by game time
    game_data = game_data.sort_values("game_minute")

    # Home team probability (implied score lead)
    fig.add_trace(
        go.Scatter(
            x=game_data["game_minute"],
            y=game_data["home_mid"] * 100,
            mode="lines",
            name="Home Win Probability",
            line=dict(color="#1f77b4", width=2),
            fill="tozeroy",
            fillcolor="rgba(31, 119, 180, 0.2)",
        )
    )

    # Away team probability
    fig.add_trace(
        go.Scatter(
            x=game_data["game_minute"],
            y=(1 - game_data["home_mid"]) * 100,
            mode="lines",
            name="Away Win Probability",
            line=dict(color="#ff7f0e", width=2),
        )
    )

    # 50% line (even game)
    fig.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5)

    fig.update_layout(
        title=title,
        xaxis_title="Game Time (minutes elapsed)",
        yaxis_title="Win Probability (%)",
        yaxis=dict(range=[0, 100]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=500,
        hovermode="x unified",
    )

    return fig


def create_aggregate_chart(
    games_data: List[pd.DataFrame], title: str = "Aggregate Scoring Progression"
) -> go.Figure:
    """Create aggregate chart with mean and spread visualization."""

    if not games_data:
        return go.Figure()

    # Normalize all games to same time scale (0-100% of game)
    normalized_games = []

    for game_df in games_data:
        game_df = game_df.sort_values("game_minute")
        max_time = game_df["game_minute"].max()
        if max_time > 0:
            game_df = game_df.copy()
            game_df["pct_elapsed"] = game_df["game_minute"] / max_time * 100
            normalized_games.append(game_df)

    if not normalized_games:
        return go.Figure()

    # Create time buckets
    time_points = np.arange(0, 101, 2)  # 0%, 2%, 4%, ..., 100%

    # Aggregate data at each time point
    aggregated = []

    for t in time_points:
        values_at_t = []
        for game_df in normalized_games:
            # Find closest time point
            closest_idx = (game_df["pct_elapsed"] - t).abs().idxmin()
            values_at_t.append(game_df.loc[closest_idx, "home_mid"])

        if values_at_t:
            aggregated.append(
                {
                    "pct_elapsed": t,
                    "mean": np.mean(values_at_t) * 100,
                    "std": np.std(values_at_t) * 100,
                    "min": np.min(values_at_t) * 100,
                    "max": np.max(values_at_t) * 100,
                    "q25": np.percentile(values_at_t, 25) * 100,
                    "q75": np.percentile(values_at_t, 75) * 100,
                    "n_games": len(values_at_t),
                }
            )

    agg_df = pd.DataFrame(aggregated)

    fig = go.Figure()

    # Confidence band (±1 std)
    fig.add_trace(
        go.Scatter(
            x=list(agg_df["pct_elapsed"]) + list(agg_df["pct_elapsed"][::-1]),
            y=list(agg_df["mean"] + agg_df["std"])
            + list((agg_df["mean"] - agg_df["std"])[::-1]),
            fill="toself",
            fillcolor="rgba(31, 119, 180, 0.2)",
            line=dict(color="rgba(255,255,255,0)"),
            name="±1 Std Dev",
            showlegend=True,
        )
    )

    # IQR band
    fig.add_trace(
        go.Scatter(
            x=list(agg_df["pct_elapsed"]) + list(agg_df["pct_elapsed"][::-1]),
            y=list(agg_df["q75"]) + list(agg_df["q25"][::-1]),
            fill="toself",
            fillcolor="rgba(31, 119, 180, 0.3)",
            line=dict(color="rgba(255,255,255,0)"),
            name="IQR (25-75%)",
            showlegend=True,
        )
    )

    # Mean line
    fig.add_trace(
        go.Scatter(
            x=agg_df["pct_elapsed"],
            y=agg_df["mean"],
            mode="lines",
            name="Mean",
            line=dict(color="#1f77b4", width=3),
        )
    )

    # 50% reference line
    fig.add_hline(y=50, line_dash="dash", line_color="gray", opacity=0.5)

    fig.update_layout(
        title=f"{title} (n={len(games_data)} games)",
        xaxis_title="Game Progress (%)",
        yaxis_title="Home Win Probability (%)",
        yaxis=dict(range=[0, 100]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=500,
        hovermode="x unified",
    )

    return fig


def calculate_game_stats(game_data: pd.DataFrame) -> Dict:
    """Calculate statistics for a game."""
    stats = {
        "n_observations": len(game_data),
        "avg_home_prob": game_data["home_mid"].mean() * 100,
        "max_home_prob": game_data["home_mid"].max() * 100,
        "min_home_prob": game_data["home_mid"].min() * 100,
        "home_prob_std": game_data["home_mid"].std() * 100,
        "final_home_prob": game_data.sort_values("game_minute").iloc[-1]["home_mid"]
        * 100,
        "winner": game_data["winner"].iloc[0]
        if "winner" in game_data.columns
        else "unknown",
    }

    # Lead changes (crossing 50%)
    above_50 = game_data["home_mid"] >= 0.5
    lead_changes = (above_50 != above_50.shift()).sum() - 1
    stats["lead_changes"] = max(0, lead_changes)

    # Volatility
    stats["volatility"] = game_data["home_mid"].diff().abs().mean() * 100

    return stats


# =============================================================================
# MAIN APP
# =============================================================================
def main():
    # Load data
    df = load_game_data()

    if df.empty:
        st.error("No data available. Please ensure the data file exists.")
        return

    # Extract team info
    df[["away_team", "home_team"]] = df["title"].apply(
        lambda x: pd.Series(extract_team_from_title(x))
    )

    # Get unique teams
    all_teams = set(df["home_team"].unique()) | set(df["away_team"].unique())
    all_teams = sorted([t for t in all_teams if t])

    # ==========================================================================
    # SIDEBAR
    # ==========================================================================
    st.sidebar.title("🏀 NBA Scoring Dashboard")
    st.sidebar.markdown("---")

    # Team selection
    st.sidebar.header("Team Selection")

    selected_team = st.sidebar.selectbox(
        "Select Team", options=["All Teams"] + all_teams, index=0
    )

    # Filter data by team
    if selected_team != "All Teams":
        team_mask = df["home_team"].str.contains(
            selected_team, case=False, na=False
        ) | df["away_team"].str.contains(selected_team, case=False, na=False)
        filtered_df = df[team_mask]
    else:
        filtered_df = df

    # Get unique games for this team
    unique_games = filtered_df["event_ticker"].unique()

    st.sidebar.markdown(f"**Games found:** {len(unique_games)}")

    # View mode
    st.sidebar.markdown("---")
    st.sidebar.header("View Mode")

    view_mode = st.sidebar.radio(
        "Display Mode", options=["Aggregate View", "Single Game"], index=0
    )

    # Game selection (for single game mode)
    selected_game = None
    if view_mode == "Single Game":
        # Create game labels
        game_labels = {}
        for ticker in unique_games:
            game_df = filtered_df[filtered_df["event_ticker"] == ticker]
            title = game_df["title"].iloc[0] if len(game_df) > 0 else ticker
            winner = game_df["winner"].iloc[0] if "winner" in game_df.columns else ""
            game_labels[ticker] = f"{title} ({winner} won)"

        selected_game = st.sidebar.selectbox(
            "Select Game",
            options=list(game_labels.keys()),
            format_func=lambda x: game_labels.get(x, x),
        )

    # Additional filters
    st.sidebar.markdown("---")
    st.sidebar.header("Filters")

    # Filter by winner
    winner_filter = st.sidebar.multiselect(
        "Winner", options=["home", "away"], default=["home", "away"]
    )

    filtered_df = filtered_df[filtered_df["winner"].isin(winner_filter)]

    # ==========================================================================
    # MAIN CONTENT
    # ==========================================================================

    # Header
    if selected_team != "All Teams":
        st.title(f"🏀 {selected_team} - Scoring Progression")
    else:
        st.title("🏀 NBA Scoring Progression Dashboard")

    # Create columns for layout
    col1, col2 = st.columns([2, 1])

    with col1:
        if view_mode == "Single Game" and selected_game:
            # Single game view
            game_data = filtered_df[filtered_df["event_ticker"] == selected_game]

            if len(game_data) > 0:
                title = game_data["title"].iloc[0]
                fig = create_scoring_progression_chart(game_data, title=title)
                st.plotly_chart(fig, use_container_width=True)

                # Stats
                stats = calculate_game_stats(game_data)

                st.markdown("### 📊 Game Statistics")

                stat_cols = st.columns(4)

                with stat_cols[0]:
                    st.metric("Winner", stats["winner"].upper())
                    st.metric("Lead Changes", int(stats["lead_changes"]))

                with stat_cols[1]:
                    st.metric("Avg Home Prob", f"{stats['avg_home_prob']:.1f}%")
                    st.metric("Final Home Prob", f"{stats['final_home_prob']:.1f}%")

                with stat_cols[2]:
                    st.metric("Max Home Prob", f"{stats['max_home_prob']:.1f}%")
                    st.metric("Min Home Prob", f"{stats['min_home_prob']:.1f}%")

                with stat_cols[3]:
                    st.metric("Volatility", f"{stats['volatility']:.2f}%")
                    st.metric("Observations", stats["n_observations"])
            else:
                st.warning("No data available for selected game")

        else:
            # Aggregate view
            games_data = []
            for ticker in unique_games[:50]:  # Limit to 50 games for performance
                game_df = filtered_df[filtered_df["event_ticker"] == ticker]
                if len(game_df) >= 5:
                    games_data.append(game_df)

            if games_data:
                fig = create_aggregate_chart(
                    games_data, title=f"Aggregate: {selected_team}"
                )
                st.plotly_chart(fig, use_container_width=True)

                # Aggregate stats
                st.markdown("### 📊 Aggregate Statistics")

                all_stats = [calculate_game_stats(g) for g in games_data]

                stat_cols = st.columns(4)

                with stat_cols[0]:
                    home_wins = sum(1 for s in all_stats if s["winner"] == "home")
                    st.metric(
                        "Home Win Rate", f"{home_wins / len(all_stats) * 100:.1f}%"
                    )
                    avg_lead_changes = np.mean([s["lead_changes"] for s in all_stats])
                    st.metric("Avg Lead Changes", f"{avg_lead_changes:.1f}")

                with stat_cols[1]:
                    avg_home_prob = np.mean([s["avg_home_prob"] for s in all_stats])
                    st.metric("Avg Home Prob", f"{avg_home_prob:.1f}%")
                    avg_final = np.mean([s["final_home_prob"] for s in all_stats])
                    st.metric("Avg Final Prob", f"{avg_final:.1f}%")

                with stat_cols[2]:
                    avg_max = np.mean([s["max_home_prob"] for s in all_stats])
                    st.metric("Avg Max Prob", f"{avg_max:.1f}%")
                    avg_min = np.mean([s["min_home_prob"] for s in all_stats])
                    st.metric("Avg Min Prob", f"{avg_min:.1f}%")

                with stat_cols[3]:
                    avg_vol = np.mean([s["volatility"] for s in all_stats])
                    st.metric("Avg Volatility", f"{avg_vol:.2f}%")
                    st.metric("Games Analyzed", len(games_data))
            else:
                st.warning("Not enough games with data for aggregate view")

    with col2:
        st.markdown("### 📋 Games List")

        # Create games table
        games_info = []
        for ticker in unique_games[:20]:
            game_df = filtered_df[filtered_df["event_ticker"] == ticker]
            if len(game_df) > 0:
                title = game_df["title"].iloc[0]
                away, home = extract_team_from_title(title)
                winner = (
                    game_df["winner"].iloc[0] if "winner" in game_df.columns else ""
                )

                games_info.append(
                    {
                        "Matchup": f"{away} @ {home}",
                        "Winner": winner.upper(),
                        "Obs": len(game_df),
                    }
                )

        if games_info:
            games_table = pd.DataFrame(games_info)
            st.dataframe(games_table, use_container_width=True, hide_index=True)

        # Distribution chart
        st.markdown("### 📈 Win Probability Distribution")

        if len(filtered_df) > 0:
            fig_hist = px.histogram(
                filtered_df,
                x="home_mid",
                nbins=50,
                title="Home Win Probability Distribution",
                labels={"home_mid": "Home Win Probability"},
            )
            fig_hist.update_layout(height=300)
            st.plotly_chart(fig_hist, use_container_width=True)

    # ==========================================================================
    # DETAILED STATS TABLE
    # ==========================================================================
    with st.expander("📊 Detailed Game Statistics", expanded=False):
        all_game_stats = []

        for ticker in unique_games[:30]:
            game_df = filtered_df[filtered_df["event_ticker"] == ticker]
            if len(game_df) >= 5:
                stats = calculate_game_stats(game_df)
                title = game_df["title"].iloc[0]
                away, home = extract_team_from_title(title)

                all_game_stats.append(
                    {
                        "Matchup": f"{away} @ {home}",
                        "Winner": stats["winner"].upper(),
                        "Avg Prob": f"{stats['avg_home_prob']:.1f}%",
                        "Final Prob": f"{stats['final_home_prob']:.1f}%",
                        "Max": f"{stats['max_home_prob']:.1f}%",
                        "Min": f"{stats['min_home_prob']:.1f}%",
                        "Lead Changes": int(stats["lead_changes"]),
                        "Volatility": f"{stats['volatility']:.2f}%",
                    }
                )

        if all_game_stats:
            stats_df = pd.DataFrame(all_game_stats)
            st.dataframe(stats_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
