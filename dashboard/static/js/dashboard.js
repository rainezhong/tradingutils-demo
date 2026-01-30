/**
 * Trading Dashboard - WebSocket Client
 *
 * Connects to the dashboard backend and updates the UI in real-time.
 */

class Dashboard {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;

        // Charts
        this.opportunityChart = null;
        this.profitChart = null;
        this.backtestEquityChart = null;
        this.backtestResultChart = null;
        this.backtestPriceChart = null;

        // Data
        this.opportunities = {};
        this.executions = {};
        this.mmStates = {};
        this.nbaStates = {};
        this.backtestStates = {};
        this.activityLog = [];
        this.signalCount = 0;

        // Backtest state
        this.recordings = { nba: [], crypto: [] };
        this.activeBacktestId = null;

        this.init();
    }

    init() {
        this.initCharts();
        this.initBacktestCharts();
        this.loadRecordings();
        this.connect();
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            console.log('Connected to dashboard');
            this.setConnectionStatus(true);
            this.reconnectAttempts = 0;
        };

        this.ws.onclose = () => {
            console.log('Disconnected from dashboard');
            this.setConnectionStatus(false);
            this.scheduleReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.handleMessage(message);
        };
    }

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.log('Max reconnect attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.connect(), delay);
    }

    setConnectionStatus(connected) {
        const status = document.getElementById('connection-status');
        if (connected) {
            status.textContent = 'Connected';
            status.className = 'status-connected';
        } else {
            status.textContent = 'Disconnected';
            status.className = 'status-disconnected';
        }
    }

    handleMessage(message) {
        switch (message.type) {
            case 'snapshot':
                this.handleSnapshot(message.data);
                break;
            case 'opportunity':
                this.handleOpportunity(message.data);
                break;
            case 'opportunity_removed':
                this.handleOpportunityRemoved(message.data);
                break;
            case 'execution':
                this.handleExecution(message.data);
                break;
            case 'mm_state':
                this.handleMMState(message.data);
                break;
            case 'nba_state':
                this.handleNBAState(message.data);
                break;
            case 'activity':
                this.handleActivity(message.data);
                break;
            case 'backtest_state':
                this.handleBacktestState(message.data);
                break;
            case 'backtest_progress':
                this.handleBacktestProgress(message.data);
                break;
            case 'backtest_removed':
                this.handleBacktestRemoved(message.data);
                break;
            case 'heartbeat':
                // Ignore heartbeats
                break;
            default:
                console.log('Unknown message type:', message.type);
        }
    }

    handleSnapshot(data) {
        // Update metrics
        this.updateMetrics(data.metrics);

        // Load opportunities
        data.opportunities.forEach(opp => {
            this.opportunities[opp.id] = opp;
        });
        this.renderOpportunities();

        // Load executions
        data.executions.forEach(exec => {
            this.executions[exec.id] = exec;
        });
        this.renderExecutions();

        // Load MM states
        data.mm_states.forEach(state => {
            this.mmStates[state.ticker] = state;
        });
        this.renderMMStates();

        // Load NBA states
        data.nba_states.forEach(state => {
            this.nbaStates[state.game_id] = state;
        });
        this.renderNBAStates();

        // Load activity log
        if (data.activity_log) {
            this.activityLog = data.activity_log;
            this.renderActivityLog();
        }

        // Load chart history
        if (data.history) {
            this.updateChartHistory(data.history);
        }

        // Load backtest states
        if (data.backtest_states) {
            data.backtest_states.forEach(state => {
                this.backtestStates[state.id] = state;
                if (state.status === 'running') {
                    this.activeBacktestId = state.id;
                    this.showBacktestProgress(state);
                } else if (state.status === 'completed' && state.result) {
                    this.showBacktestResults(state);
                }
            });
        }
    }

    handleOpportunity(data) {
        this.opportunities[data.id] = data;
        this.renderOpportunities();
        this.updateMetrics({ active_opportunities: Object.values(this.opportunities).filter(o => o.is_active).length });
    }

    handleOpportunityRemoved(data) {
        if (this.opportunities[data.id]) {
            this.opportunities[data.id].is_active = false;
            this.renderOpportunities();
        }
    }

    handleExecution(data) {
        this.executions[data.id] = data;
        this.renderExecutions();
    }

    handleMMState(data) {
        this.mmStates[data.ticker] = data;
        this.renderMMStates();
    }

    handleNBAState(data) {
        this.nbaStates[data.game_id] = data;
        this.renderNBAStates();
    }

    handleActivity(data) {
        this.activityLog.push(data);
        if (this.activityLog.length > 200) {
            this.activityLog.shift();
        }
        if (data.event_type === 'signal') {
            this.signalCount++;
            const el = document.getElementById('metric-trades');
            if (el) el.textContent = this.signalCount;
        }
        this.renderActivityLog();
    }

    updateMetrics(metrics) {
        if (metrics.active_opportunities !== undefined) {
            document.getElementById('metric-opportunities').textContent = metrics.active_opportunities;
        }
        if (metrics.running_bots !== undefined) {
            document.getElementById('metric-bots').textContent = metrics.running_bots;
        }
        if (metrics.pending_executions !== undefined) {
            document.getElementById('metric-executions').textContent = metrics.pending_executions;
        }
        if (metrics.total_profit !== undefined) {
            const el = document.getElementById('metric-profit');
            el.textContent = '$' + metrics.total_profit.toFixed(2);
            el.className = metrics.total_profit >= 0 ? 'metric-value positive' : 'metric-value negative';
        }
        if (metrics.tracked_games !== undefined) {
            document.getElementById('metric-games').textContent = metrics.tracked_games;
        }
        if (metrics.uptime_seconds !== undefined) {
            const uptime = this.formatUptime(metrics.uptime_seconds);
            document.getElementById('uptime').textContent = 'Uptime: ' + uptime;
        }
    }

    formatUptime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return `${hours}h ${minutes}m ${secs}s`;
    }

    renderOpportunities() {
        const tbody = document.querySelector('#opportunities-table tbody');
        const activeOpps = Object.values(this.opportunities).filter(o => o.is_active);

        if (activeOpps.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="8">No active opportunities</td></tr>';
            return;
        }

        tbody.innerHTML = activeOpps.map(opp => `
            <tr>
                <td>${opp.id}</td>
                <td title="${opp.event_description}">${this.truncate(opp.event_description, 30)}</td>
                <td>${opp.opportunity_type}</td>
                <td><span class="platform-${opp.buy_platform}">${opp.buy_platform}</span> @ ${opp.buy_price.toFixed(3)}</td>
                <td><span class="platform-${opp.sell_platform}">${opp.sell_platform}</span> @ ${opp.sell_price.toFixed(3)}</td>
                <td class="edge-positive">${(opp.net_edge * 100).toFixed(2)}c</td>
                <td>$${opp.estimated_profit.toFixed(2)}</td>
                <td>${this.formatAge(opp.first_seen)}</td>
            </tr>
        `).join('');
    }

    renderExecutions() {
        const tbody = document.querySelector('#executions-table tbody');
        const execList = Object.values(this.executions).slice(-20).reverse();

        if (execList.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No executions</td></tr>';
            return;
        }

        tbody.innerHTML = execList.map(exec => `
            <tr>
                <td>${exec.id}</td>
                <td><span class="status-badge status-${exec.status}">${exec.status}</span></td>
                <td>${exec.leg1_filled ? 'Filled' : 'Pending'} @ ${exec.leg1_price.toFixed(2)}</td>
                <td>${exec.leg2_filled ? 'Filled' : 'Pending'} @ ${exec.leg2_price.toFixed(2)}</td>
                <td>$${exec.expected_profit.toFixed(2)}</td>
                <td>${exec.actual_profit !== null ? '$' + exec.actual_profit.toFixed(2) : '-'}</td>
            </tr>
        `).join('');
    }

    renderMMStates() {
        const container = document.getElementById('mm-container');
        const states = Object.values(this.mmStates);

        if (states.length === 0) {
            container.innerHTML = '<div class="empty-state">No active market makers</div>';
            return;
        }

        container.innerHTML = states.map(state => `
            <div class="mm-state">
                <div class="mm-item">
                    <div class="mm-item-label">Ticker</div>
                    <div class="mm-item-value">${state.ticker}</div>
                </div>
                <div class="mm-item">
                    <div class="mm-item-label">Position</div>
                    <div class="mm-item-value ${state.position >= 0 ? 'mm-position-positive' : 'mm-position-negative'}">
                        ${state.position > 0 ? '+' : ''}${state.position}
                    </div>
                </div>
                <div class="mm-item">
                    <div class="mm-item-label">MTM P&L</div>
                    <div class="mm-item-value ${state.mtm_pnl >= 0 ? 'edge-positive' : 'edge-negative'}">
                        $${state.mtm_pnl.toFixed(2)}
                    </div>
                </div>
                <div class="mm-item">
                    <div class="mm-item-label">Bid</div>
                    <div class="mm-item-value">${state.active_bid ? state.active_bid.toFixed(2) : '-'}</div>
                </div>
                <div class="mm-item">
                    <div class="mm-item-label">Ask</div>
                    <div class="mm-item-value">${state.active_ask ? state.active_ask.toFixed(2) : '-'}</div>
                </div>
                <div class="mm-item">
                    <div class="mm-item-label">Volatility</div>
                    <div class="mm-item-value">${(state.sigma * 100).toFixed(2)}%</div>
                </div>
            </div>
        `).join('');
    }

    renderNBAStates() {
        const container = document.getElementById('nba-container');
        const games = Object.values(this.nbaStates);

        if (games.length === 0) {
            container.innerHTML = '<div class="empty-state">No tracked games</div>';
            return;
        }

        container.innerHTML = games.map(game => {
            const hasEdge = Math.abs(game.edge_cents) >= 3;
            const edgeClass = hasEdge ? 'has-edge' : 'no-edge';
            const scoreDiff = game.home_score - game.away_score;
            const diffStr = scoreDiff > 0 ? `+${scoreDiff}` : scoreDiff.toString();

            let signalHtml = '<div class="nba-signal nba-signal-none">No signal (edge below threshold)</div>';
            if (game.last_signal) {
                signalHtml = `<div class="nba-signal nba-signal-buy">${game.last_signal}</div>`;
            }

            return `
            <div class="nba-game ${edgeClass}">
                <div class="nba-header">
                    <div class="nba-matchup">${game.away_team} @ ${game.home_team}</div>
                    <div class="nba-period">Q${game.period} ${game.time_remaining}</div>
                </div>

                <div class="nba-score-row">
                    <div class="nba-team-score">
                        <div class="nba-team-code">${game.away_team}</div>
                        <div>${game.away_score}</div>
                    </div>
                    <div class="nba-vs">vs</div>
                    <div class="nba-team-score">
                        <div class="nba-team-code">${game.home_team}</div>
                        <div>${game.home_score}</div>
                    </div>
                </div>

                <div class="nba-analysis">
                    <div class="nba-stat">
                        <div class="nba-stat-label">Score Diff</div>
                        <div class="nba-stat-value ${scoreDiff >= 0 ? 'positive' : 'negative'}">${diffStr}</div>
                    </div>
                    <div class="nba-stat">
                        <div class="nba-stat-label">Model Prob</div>
                        <div class="nba-stat-value">${(game.home_win_prob * 100).toFixed(1)}%</div>
                    </div>
                    <div class="nba-stat">
                        <div class="nba-stat-label">Market Price</div>
                        <div class="nba-stat-value">${(game.market_price * 100).toFixed(1)}c</div>
                    </div>
                    <div class="nba-stat">
                        <div class="nba-stat-label">Edge</div>
                        <div class="nba-stat-value ${game.edge_cents >= 0 ? 'positive' : 'negative'}">${game.edge_cents >= 0 ? '+' : ''}${game.edge_cents.toFixed(1)}c</div>
                    </div>
                </div>

                ${signalHtml}

                <div style="margin-top: 0.5rem; font-size: 0.75rem; color: var(--text-secondary); text-align: center;">
                    Trading: ${game.is_trading_allowed ? 'ALLOWED' : 'BLOCKED (past Q2)'} | Position: ${game.position}
                </div>
            </div>
            `;
        }).join('');
    }

    renderActivityLog() {
        const container = document.getElementById('activity-log');
        const countEl = document.getElementById('activity-count');

        if (countEl) {
            countEl.textContent = `(${this.activityLog.length} events)`;
        }

        if (this.activityLog.length === 0) {
            container.innerHTML = '<div class="empty-state">No activity yet - start a strategy to see live events</div>';
            return;
        }

        // Show most recent first
        const recentLogs = this.activityLog.slice(-50).reverse();

        container.innerHTML = recentLogs.map(entry => {
            const time = new Date(entry.timestamp).toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });

            let detailsHtml = '';
            if (entry.details && Object.keys(entry.details).length > 0) {
                const detailStr = Object.entries(entry.details)
                    .map(([k, v]) => `${k}: ${typeof v === 'number' ? v.toFixed(2) : v}`)
                    .join(' | ');
                detailsHtml = `<div class="activity-details">${detailStr}</div>`;
            }

            return `
            <div class="activity-entry">
                <div class="activity-time">${time}</div>
                <div class="activity-type activity-type-${entry.event_type}">${entry.event_type}</div>
                <div>
                    <div class="activity-message">[${entry.strategy.toUpperCase()}] ${entry.message}</div>
                    ${detailsHtml}
                </div>
            </div>
            `;
        }).join('');

        // Auto-scroll to top (most recent)
        container.scrollTop = 0;
    }

    // ==================== Backtest Methods ====================

    async loadRecordings() {
        try {
            const response = await fetch('/api/recordings');
            this.recordings = await response.json();
            this.updateRecordingDropdown();
        } catch (error) {
            console.error('Failed to load recordings:', error);
        }
    }

    updateRecordingDropdown() {
        const select = document.getElementById('bt-recording');
        const strategy = document.getElementById('bt-strategy').value;

        // Clear existing options
        select.innerHTML = '<option value="">Select recording...</option>';

        // Add recordings for current strategy
        let recordingList;
        if (strategy === 'nba_mispricing' || strategy === 'nba_blowout') {
            recordingList = this.recordings.nba;
        } else {
            recordingList = this.recordings.crypto;
        }

        recordingList.forEach(rec => {
            const option = document.createElement('option');
            option.value = rec.path;
            option.textContent = `${rec.name} (${rec.size_kb} KB)`;
            select.appendChild(option);
        });
    }

    onStrategyChange() {
        const strategy = document.getElementById('bt-strategy').value;
        const nbaParams = document.getElementById('nba-params');
        const blowoutParams = document.getElementById('blowout-params');
        const cryptoParams = document.getElementById('crypto-params');

        // Hide all first
        nbaParams.style.display = 'none';
        blowoutParams.style.display = 'none';
        cryptoParams.style.display = 'none';

        // Show relevant params
        if (strategy === 'nba_mispricing') {
            nbaParams.style.display = 'grid';
        } else if (strategy === 'nba_blowout') {
            blowoutParams.style.display = 'grid';
        } else {
            cryptoParams.style.display = 'grid';
        }

        this.updateRecordingDropdown();
    }

    async runBacktest() {
        const strategy = document.getElementById('bt-strategy').value;
        const recording = document.getElementById('bt-recording').value;

        if (!recording) {
            alert('Please select a recording file');
            return;
        }

        // Build request body
        const body = {
            strategy: strategy,
            data_source: recording,
        };

        if (strategy === 'nba_mispricing') {
            body.min_edge_cents = parseFloat(document.getElementById('bt-min-edge').value);
            body.position_size = parseInt(document.getElementById('bt-position-size').value);
            body.max_period = parseInt(document.getElementById('bt-max-period').value);
            body.fill_probability = parseFloat(document.getElementById('bt-fill-prob').value);
        } else if (strategy === 'nba_blowout') {
            body.min_point_differential = parseInt(document.getElementById('bt-min-lead').value);
            body.max_time_remaining_seconds = parseInt(document.getElementById('bt-max-time').value);
            body.blowout_position_size = parseFloat(document.getElementById('bt-blowout-size').value);
        } else {
            body.min_edge_cents = parseFloat(document.getElementById('bt-crypto-edge').value);
            body.bankroll = parseFloat(document.getElementById('bt-bankroll').value);
            body.kelly_fraction = parseFloat(document.getElementById('bt-kelly').value);
            body.signal_stability_sec = parseFloat(document.getElementById('bt-stability').value);
        }

        // Disable button
        const btn = document.getElementById('bt-run-btn');
        btn.disabled = true;
        btn.textContent = 'Starting...';

        try {
            const response = await fetch('/api/backtest/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });

            const result = await response.json();

            if (response.ok) {
                this.activeBacktestId = result.job_id;
                console.log('Backtest started:', result.job_id);
            } else {
                alert('Failed to start backtest: ' + (result.detail || 'Unknown error'));
                btn.disabled = false;
                btn.textContent = 'Run Backtest';
            }
        } catch (error) {
            console.error('Failed to start backtest:', error);
            alert('Failed to start backtest');
            btn.disabled = false;
            btn.textContent = 'Run Backtest';
        }
    }

    async cancelBacktest() {
        if (!this.activeBacktestId) return;

        try {
            await fetch(`/api/backtest/${this.activeBacktestId}/cancel`, {
                method: 'POST',
            });
        } catch (error) {
            console.error('Failed to cancel backtest:', error);
        }
    }

    handleBacktestState(data) {
        this.backtestStates[data.id] = data;

        if (data.status === 'running') {
            this.activeBacktestId = data.id;
            this.showBacktestProgress(data);
        } else if (data.status === 'completed') {
            this.activeBacktestId = null;
            this.hideBacktestProgress();
            this.showBacktestResults(data);
            this.resetRunButton();
        } else if (data.status === 'failed' || data.status === 'cancelled') {
            this.activeBacktestId = null;
            this.hideBacktestProgress();
            this.resetRunButton();
            if (data.error) {
                alert('Backtest failed: ' + data.error);
            }
        }
    }

    handleBacktestProgress(data) {
        if (data.id !== this.activeBacktestId) return;

        // Update progress bar
        const progressBar = document.getElementById('bt-progress-bar');
        const progressPct = document.getElementById('bt-progress-pct');
        const progressSignals = document.getElementById('bt-progress-signals');
        const progressTrades = document.getElementById('bt-progress-trades');
        const progressPnl = document.getElementById('bt-progress-pnl');

        if (progressBar) progressBar.style.width = `${data.progress_pct}%`;
        if (progressPct) progressPct.textContent = `${data.progress_pct.toFixed(1)}%`;
        if (progressSignals) progressSignals.textContent = data.signals_generated;
        if (progressTrades) progressTrades.textContent = data.trades_executed;
        if (progressPnl) {
            progressPnl.textContent = `$${data.current_pnl.toFixed(2)}`;
            progressPnl.className = 'stat-value ' + (data.current_pnl >= 0 ? 'positive' : 'negative');
        }

        // Update equity chart
        if (data.equity_curve && data.equity_curve.length > 0 && this.backtestEquityChart) {
            const labels = data.equity_curve.map((_, i) => i);
            const values = data.equity_curve.map(p => p.pnl);

            this.backtestEquityChart.data.labels = labels;
            this.backtestEquityChart.data.datasets[0].data = values;
            this.backtestEquityChart.update('none');
        }
    }

    handleBacktestRemoved(data) {
        delete this.backtestStates[data.id];
    }

    showBacktestProgress(state) {
        const progressSection = document.getElementById('backtest-progress');
        const resultsSection = document.getElementById('backtest-results');

        if (progressSection) progressSection.style.display = 'block';
        if (resultsSection) resultsSection.style.display = 'none';

        // Update title
        const title = document.getElementById('bt-progress-title');
        if (title) {
            const recordingName = state.data_source.split('/').pop();
            title.textContent = `Running ${state.strategy} on ${recordingName}...`;
        }

        // Reset progress
        const progressBar = document.getElementById('bt-progress-bar');
        if (progressBar) progressBar.style.width = '0%';

        // Clear chart
        if (this.backtestEquityChart) {
            this.backtestEquityChart.data.labels = [];
            this.backtestEquityChart.data.datasets[0].data = [];
            this.backtestEquityChart.update('none');
        }
    }

    hideBacktestProgress() {
        const progressSection = document.getElementById('backtest-progress');
        if (progressSection) progressSection.style.display = 'none';
    }

    resetRunButton() {
        const btn = document.getElementById('bt-run-btn');
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Run Backtest';
        }
    }

    showBacktestResults(state) {
        const resultsSection = document.getElementById('backtest-results');
        if (!resultsSection || !state.result) return;

        resultsSection.style.display = 'block';

        // Update status badge
        const statusBadge = document.getElementById('bt-result-status');
        if (statusBadge) {
            statusBadge.textContent = state.status;
            statusBadge.className = `status-badge status-${state.status}`;
        }

        const metrics = state.result.metrics || {};
        const isNBA = state.strategy === 'nba_mispricing' || state.strategy === 'nba_blowout';
        const isBlowout = state.strategy === 'nba_blowout';

        // Update summary metrics
        const pnl = isNBA ? metrics.net_pnl : metrics.total_pnl;
        const pnlEl = document.getElementById('bt-result-pnl');
        if (pnlEl) {
            pnlEl.textContent = `$${(pnl || 0).toFixed(2)}`;
            pnlEl.className = 'result-value ' + ((pnl || 0) >= 0 ? 'positive' : 'negative');
        }

        const accuracy = isNBA ? metrics.accuracy_pct : metrics.win_rate;
        const accuracyEl = document.getElementById('bt-result-accuracy');
        if (accuracyEl) accuracyEl.textContent = `${(accuracy || 0).toFixed(1)}%`;

        const trades = isNBA ? metrics.orders_filled : metrics.total_trades;
        const tradesEl = document.getElementById('bt-result-trades');
        if (tradesEl) tradesEl.textContent = trades || 0;

        const signals = isNBA ? metrics.total_signals : metrics.settled;
        const signalsEl = document.getElementById('bt-result-signals');
        if (signalsEl) signalsEl.textContent = signals || 0;

        const avgEdge = isNBA ? metrics.avg_edge_cents : 0;
        const edgeEl = document.getElementById('bt-result-edge');
        if (edgeEl) edgeEl.textContent = `${(avgEdge || 0).toFixed(1)}c`;

        // Update details section
        const detailsEl = document.getElementById('bt-result-details');
        if (detailsEl) {
            if (isBlowout) {
                detailsEl.innerHTML = `
                    <div class="detail-row">
                        <span class="detail-label">Game</span>
                        <span>${state.result.away_team} @ ${state.result.home_team}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Final Score</span>
                        <span>${state.result.away_team} ${state.result.final_away_score} - ${state.result.final_home_score} ${state.result.home_team}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Winner</span>
                        <span>${state.result.winner.toUpperCase()}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Strategy</span>
                        <span>Late Game Blowout (${metrics.min_point_differential}+ pts, ≤${Math.floor(metrics.max_time_remaining_seconds/60)} min)</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Correct / Incorrect</span>
                        <span class="trade-win">${metrics.correct_signals}</span> / <span class="trade-loss">${metrics.incorrect_signals}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Gross P&L</span>
                        <span>$${(metrics.gross_pnl || 0).toFixed(2)}</span>
                    </div>
                `;
            } else if (isNBA) {
                detailsEl.innerHTML = `
                    <div class="detail-row">
                        <span class="detail-label">Game</span>
                        <span>${state.result.away_team} @ ${state.result.home_team}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Final Score</span>
                        <span>${state.result.away_team} ${state.result.final_away_score} - ${state.result.final_home_score} ${state.result.home_team}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Winner</span>
                        <span>${state.result.winner}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Correct / Incorrect</span>
                        <span class="trade-win">${metrics.correct_signals}</span> / <span class="trade-loss">${metrics.incorrect_signals}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Gross P&L</span>
                        <span>$${(metrics.gross_pnl || 0).toFixed(2)}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Fees</span>
                        <span>$${(metrics.fees || 0).toFixed(2)}</span>
                    </div>
                `;
            } else {
                detailsEl.innerHTML = `
                    <div class="detail-row">
                        <span class="detail-label">Snapshots Processed</span>
                        <span>${state.result.total_snapshots || 0}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Starting Bankroll</span>
                        <span>$${(metrics.starting_bankroll || 0).toFixed(2)}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Final Bankroll</span>
                        <span>$${(metrics.final_bankroll || 0).toFixed(2)}</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Return</span>
                        <span class="${(metrics.return_pct || 0) >= 0 ? 'positive' : 'negative'}">${(metrics.return_pct || 0).toFixed(1)}%</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Wins / Losses</span>
                        <span class="trade-win">${metrics.wins || 0}</span> / <span class="trade-loss">${metrics.losses || 0}</span>
                    </div>
                `;
            }
        }

        // Update equity curve chart
        if (state.result.equity_curve && this.backtestResultChart) {
            const labels = state.result.equity_curve.map((_, i) => i);
            const values = state.result.equity_curve.map(p => p.pnl);

            this.backtestResultChart.data.labels = labels;
            this.backtestResultChart.data.datasets[0].data = values;
            this.backtestResultChart.update();
        }

        // Update price chart with trade markers (NBA only)
        if (isNBA && state.result.price_history && this.backtestPriceChart) {
            this.updatePriceChart(state.result.price_history, state.result.trades || []);
        }

        // Update trade table
        this.renderBacktestTrades(state.result.trades || [], isNBA, isBlowout);
    }

    updatePriceChart(priceHistory, trades) {
        if (!this.backtestPriceChart || !priceHistory.length) return;

        // Build labels showing period and score
        const labels = priceHistory.map(p => {
            return `Q${p.period} ${p.home_score}-${p.away_score}`;
        });

        // Price line data
        const priceData = priceHistory.map(p => p.home_price);

        // Build trade marker datasets
        const buyYesData = [];
        const buyNoData = [];

        trades.forEach(trade => {
            if (!trade.filled) return;

            // Find the closest price point to this trade
            let closestIdx = 0;
            let minDiff = Infinity;
            priceHistory.forEach((p, idx) => {
                const diff = Math.abs(p.frame - trade.frame_idx);
                if (diff < minDiff) {
                    minDiff = diff;
                    closestIdx = idx;
                }
            });

            const dataPoint = {
                x: closestIdx,
                y: trade.market_mid,
                trade: trade,
            };

            if (trade.direction === 'BUY YES') {
                buyYesData.push(dataPoint);
            } else {
                buyNoData.push(dataPoint);
            }
        });

        // Update chart data
        this.backtestPriceChart.data.labels = labels;
        this.backtestPriceChart.data.datasets[0].data = priceData;
        this.backtestPriceChart.data.datasets[1].data = buyYesData;
        this.backtestPriceChart.data.datasets[2].data = buyNoData;

        this.backtestPriceChart.update();
    }

    renderBacktestTrades(trades, isNBA, isBlowout = false) {
        const tbody = document.querySelector('#bt-trades-table tbody');
        const countEl = document.getElementById('bt-trade-count');

        if (countEl) countEl.textContent = `(${trades.length} trades)`;

        if (!trades.length) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No trades executed</td></tr>';
            return;
        }

        // Show most recent trades first, limit to 50
        const recentTrades = trades.slice(-50).reverse();

        tbody.innerHTML = recentTrades.map(trade => {
            if (isBlowout) {
                // Blowout strategy trades
                const resultClass = trade.correct === true ? 'trade-win' : trade.correct === false ? 'trade-loss' : 'trade-pending';
                const resultText = trade.correct === true ? 'WIN' : trade.correct === false ? 'LOSS' : 'PENDING';
                return `
                    <tr>
                        <td>Q${trade.period} ${trade.time_remaining}</td>
                        <td>${trade.direction}</td>
                        <td>+${trade.score_differential} pts (${trade.confidence})</td>
                        <td>${trade.fill_price ? trade.fill_price.toFixed(0) + 'c' : '-'}</td>
                        <td class="${resultClass}">${resultText}</td>
                        <td class="${(trade.pnl || 0) >= 0 ? 'trade-win' : 'trade-loss'}">$${(trade.pnl || 0).toFixed(2)}</td>
                    </tr>
                `;
            } else if (isNBA) {
                const resultClass = trade.correct === true ? 'trade-win' : trade.correct === false ? 'trade-loss' : 'trade-pending';
                const resultText = trade.correct === true ? 'WIN' : trade.correct === false ? 'LOSS' : 'PENDING';
                return `
                    <tr>
                        <td>Q${trade.period}</td>
                        <td>${trade.direction}</td>
                        <td>${trade.edge_cents.toFixed(1)}c</td>
                        <td>${trade.fill_price ? (trade.fill_price * 100).toFixed(0) + 'c' : '-'}</td>
                        <td class="${resultClass}">${resultText}</td>
                        <td class="${(trade.pnl || 0) >= 0 ? 'trade-win' : 'trade-loss'}">$${(trade.pnl || 0).toFixed(2)}</td>
                    </tr>
                `;
            } else {
                const resultClass = trade.pnl > 0 ? 'trade-win' : trade.pnl < 0 ? 'trade-loss' : 'trade-pending';
                const resultText = trade.result ? trade.result.toUpperCase() : 'PENDING';
                return `
                    <tr>
                        <td>${trade.asset || '-'}</td>
                        <td>${trade.side.toUpperCase()}</td>
                        <td>${(trade.edge * 100).toFixed(1)}%</td>
                        <td>${trade.entry_price_cents}c</td>
                        <td class="${resultClass}">${resultText}</td>
                        <td class="${(trade.pnl || 0) >= 0 ? 'trade-win' : 'trade-loss'}">$${(trade.pnl || 0).toFixed(2)}</td>
                    </tr>
                `;
            }
        }).join('');
    }

    initBacktestCharts() {
        // Live equity chart during backtest
        const equityCtx = document.getElementById('backtest-equity-chart');
        if (equityCtx) {
            this.backtestEquityChart = new Chart(equityCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'P&L',
                        data: [],
                        borderColor: '#00d26a',
                        backgroundColor: 'rgba(0, 210, 106, 0.1)',
                        fill: true,
                        tension: 0.4,
                        pointRadius: 0,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { display: false },
                        y: {
                            ticks: {
                                color: '#a0a0a0',
                                callback: (value) => '$' + value.toFixed(0)
                            },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        }
                    }
                }
            });
        }

        // Results equity chart
        const resultCtx = document.getElementById('backtest-result-chart');
        if (resultCtx) {
            this.backtestResultChart = new Chart(resultCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Cumulative P&L',
                        data: [],
                        borderColor: '#00d26a',
                        backgroundColor: 'rgba(0, 210, 106, 0.1)',
                        fill: true,
                        tension: 0.4,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: {
                            display: true,
                            title: { display: true, text: 'Trade #', color: '#a0a0a0' },
                            ticks: { color: '#a0a0a0' },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        },
                        y: {
                            ticks: {
                                color: '#a0a0a0',
                                callback: (value) => '$' + value.toFixed(0)
                            },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        }
                    }
                }
            });
        }

        // Price chart with trade markers
        const priceCtx = document.getElementById('backtest-price-chart');
        if (priceCtx) {
            this.backtestPriceChart = new Chart(priceCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: 'Home Team Price',
                            data: [],
                            borderColor: '#3498db',
                            backgroundColor: 'rgba(52, 152, 219, 0.1)',
                            fill: true,
                            tension: 0.2,
                            pointRadius: 0,
                            borderWidth: 2,
                        },
                        {
                            label: 'BUY YES',
                            data: [],
                            borderColor: '#00d26a',
                            backgroundColor: '#00d26a',
                            pointRadius: 8,
                            pointStyle: 'triangle',
                            showLine: false,
                            pointHoverRadius: 10,
                        },
                        {
                            label: 'BUY NO',
                            data: [],
                            borderColor: '#e94560',
                            backgroundColor: '#e94560',
                            pointRadius: 8,
                            pointStyle: 'triangle',
                            rotation: 180,
                            showLine: false,
                            pointHoverRadius: 10,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false,
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    if (context.datasetIndex === 0) {
                                        return `Price: ${context.parsed.y.toFixed(1)}¢`;
                                    } else {
                                        const trade = context.raw.trade;
                                        if (trade) {
                                            return [
                                                `${trade.direction}`,
                                                `Edge: ${trade.edge_cents.toFixed(1)}¢`,
                                                `Fill: ${trade.fill_price ? trade.fill_price.toFixed(0) + '¢' : 'No'}`,
                                                `Result: ${trade.correct === true ? 'WIN' : trade.correct === false ? 'LOSS' : '?'}`,
                                            ];
                                        }
                                        return context.dataset.label;
                                    }
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            display: true,
                            title: { display: true, text: 'Game Progress', color: '#a0a0a0' },
                            ticks: {
                                color: '#a0a0a0',
                                maxTicksLimit: 10,
                            },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        },
                        y: {
                            display: true,
                            title: { display: true, text: 'Price (cents)', color: '#a0a0a0' },
                            min: 0,
                            max: 100,
                            ticks: {
                                color: '#a0a0a0',
                                callback: (value) => value + '¢'
                            },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        }
                    }
                }
            });
        }
    }

    initCharts() {
        // Opportunity count chart
        const oppCtx = document.getElementById('opportunity-chart');
        if (oppCtx) {
            this.opportunityChart = new Chart(oppCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Active Opportunities',
                        data: [],
                        borderColor: '#e94560',
                        backgroundColor: 'rgba(233, 69, 96, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: { display: false },
                        y: {
                            beginAtZero: true,
                            ticks: { color: '#a0a0a0' },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        }
                    }
                }
            });
        }

        // Cumulative profit chart
        const profitCtx = document.getElementById('profit-chart');
        if (profitCtx) {
            this.profitChart = new Chart(profitCtx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Cumulative P&L',
                        data: [],
                        borderColor: '#00d26a',
                        backgroundColor: 'rgba(0, 210, 106, 0.1)',
                        fill: true,
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: { display: false },
                        y: {
                            ticks: {
                                color: '#a0a0a0',
                                callback: (value) => '$' + value.toFixed(2)
                            },
                            grid: { color: 'rgba(160, 160, 160, 0.1)' }
                        }
                    }
                }
            });
        }
    }

    updateChartHistory(history) {
        // Update opportunity chart
        if (this.opportunityChart && history.opportunities && history.opportunities.length > 0) {
            const labels = history.opportunities.map(h => this.formatTime(h.timestamp));
            const data = history.opportunities.map(h => h.count);

            this.opportunityChart.data.labels = labels;
            this.opportunityChart.data.datasets[0].data = data;
            this.opportunityChart.update('none');
        }

        // Update profit chart
        if (this.profitChart && history.profit && history.profit.length > 0) {
            const labels = history.profit.map(h => this.formatTime(h.timestamp));
            const data = history.profit.map(h => h.cumulative);

            this.profitChart.data.labels = labels;
            this.profitChart.data.datasets[0].data = data;
            this.profitChart.update('none');
        }
    }

    truncate(str, length) {
        if (str.length <= length) return str;
        return str.substring(0, length) + '...';
    }

    formatAge(isoString) {
        const date = new Date(isoString);
        const seconds = Math.floor((new Date() - date) / 1000);

        if (seconds < 60) return `${seconds}s`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
        return `${Math.floor(seconds / 3600)}h`;
    }

    formatTime(isoString) {
        const date = new Date(isoString);
        return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    }
}

// Start dashboard
const dashboard = new Dashboard();

// Demo mode
async function startDemo() {
    try {
        const response = await fetch('/api/demo/start', { method: 'POST' });
        const result = await response.json();
        console.log('Demo started:', result);
    } catch (error) {
        console.error('Failed to start demo:', error);
    }
}

// Panel toggle functionality
function togglePanel(panelType) {
    const checkbox = document.getElementById(`toggle-${panelType}`);
    const panels = document.querySelectorAll(`[data-panel="${panelType}"]`);

    panels.forEach(panel => {
        if (checkbox.checked) {
            panel.classList.remove('panel-hidden');
        } else {
            panel.classList.add('panel-hidden');
        }
    });

    // Save preference
    localStorage.setItem(`panel-${panelType}`, checkbox.checked);
}

// Restore panel preferences on load
document.addEventListener('DOMContentLoaded', () => {
    ['arb', 'mm', 'nba', 'backtest'].forEach(panelType => {
        const saved = localStorage.getItem(`panel-${panelType}`);
        if (saved !== null) {
            const checkbox = document.getElementById(`toggle-${panelType}`);
            if (checkbox) {
                checkbox.checked = saved === 'true';
                togglePanel(panelType);
            }
        }
    });
});
