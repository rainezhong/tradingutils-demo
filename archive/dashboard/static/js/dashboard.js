/**
 * Trading Dashboard - Strategy-Centric View
 *
 * Manages strategy selection, start/stop controls, and real-time updates
 * for positions, trades, game state, and market pricing.
 */

class Dashboard {
    constructor() {
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 10;
        this.reconnectDelay = 1000;

        // Strategy state
        this.selectedStrategy = null;
        this.selectedGame = null;
        this.mode = 'paper';
        this.session = null;

        // Available games
        this.liveGames = [];

        // Market maker states
        this.mmStates = {};

        // Probability timeline
        this.probHistory = [];
        this.probChart = null;

        // Activity log
        this.activityLog = [];

        this.init();
    }

    init() {
        this.bindEvents();
        this.connect();
        this.loadLiveGames();
    }

    bindEvents() {
        // Strategy selector
        const strategySelector = document.getElementById('strategy-selector');
        if (strategySelector) {
            strategySelector.addEventListener('change', () => this.onStrategyChange());
        }

        // Game selector
        const gameSelector = document.getElementById('game-selector');
        if (gameSelector) {
            gameSelector.addEventListener('change', () => this.onGameChange());
        }

        // Refresh games button
        const btnRefresh = document.getElementById('btn-refresh-games');
        if (btnRefresh) {
            btnRefresh.addEventListener('click', () => this.loadLiveGames());
        }

        // Mode toggle
        const modePaper = document.getElementById('mode-paper');
        const modeLive = document.getElementById('mode-live');
        if (modePaper) {
            modePaper.addEventListener('click', () => this.setMode('paper'));
        }
        if (modeLive) {
            modeLive.addEventListener('click', () => this.setMode('live'));
        }

        // Start/Stop buttons
        const btnStart = document.getElementById('btn-start');
        const btnStop = document.getElementById('btn-stop');
        if (btnStart) {
            btnStart.addEventListener('click', () => this.startStrategy());
        }
        if (btnStop) {
            btnStop.addEventListener('click', () => this.stopStrategy());
        }
    }

    // ==================== Game Selection ====================

    async loadLiveGames() {
        const gameSelector = document.getElementById('game-selector');
        if (!gameSelector) return;

        try {
            const response = await fetch('/api/games/live');
            const data = await response.json();

            // Clear existing options
            gameSelector.innerHTML = '<option value="">Select game...</option>';

            if (data.games && data.games.length > 0) {
                this.liveGames = data.games;

                data.games.forEach(game => {
                    // Create option group for each game
                    const optgroup = document.createElement('optgroup');
                    optgroup.label = `${game.display} (${game.clock})`;

                    // Add market options
                    game.markets.forEach(market => {
                        const option = document.createElement('option');
                        option.value = market.ticker;
                        const teamName = market.team || market.ticker.split('-')[2] || 'Win';
                        option.textContent = `${teamName} - Bid: ${market.yes_bid}c Ask: ${market.yes_ask}c`;
                        optgroup.appendChild(option);
                    });

                    gameSelector.appendChild(optgroup);
                });

                // Enable selector if strategy is running
                if (this.session && this.session.status === 'running') {
                    gameSelector.disabled = false;
                }
            } else {
                // No live games - add demo option
                const option = document.createElement('option');
                option.value = 'demo';
                option.textContent = 'No live games - using demo data';
                gameSelector.appendChild(option);
            }

        } catch (error) {
            console.error('Failed to load live games:', error);
            // Add error indicator
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'Error loading games';
            gameSelector.appendChild(option);
        }
    }

    async onGameChange() {
        const gameSelector = document.getElementById('game-selector');
        const ticker = gameSelector.value;

        if (!ticker || ticker === 'demo') {
            this.selectedGame = null;
            return;
        }

        this.selectedGame = ticker;
        console.log('Selected game:', ticker);

        // If strategy is running, switch to this market immediately
        if (this.session && this.session.status === 'running') {
            await this.switchToMarket(ticker);
        }
    }

    async switchToMarket(ticker) {
        try {
            const response = await fetch('/api/strategy/select-game', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticker: ticker })
            });

            const result = await response.json();
            if (result.status === 'ok') {
                console.log('Switched to market:', ticker);
            } else {
                console.error('Failed to switch market:', result.error);
                alert('Failed to switch market: ' + result.error);
            }
        } catch (error) {
            console.error('Failed to switch market:', error);
        }
    }

    // ==================== WebSocket Connection ====================

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

    // ==================== Message Handling ====================

    handleMessage(message) {
        switch (message.type) {
            case 'snapshot':
                this.handleSnapshot(message.data);
                break;
            case 'strategy_session':
                this.handleSession(message.data);
                break;
            case 'strategy_position':
                this.handlePositionUpdate(message.data);
                break;
            case 'strategy_position_removed':
                this.handlePositionRemoved(message.data);
                break;
            case 'strategy_trade':
                this.handleTradeUpdate(message.data);
                break;
            case 'game_state':
                this.renderGame(message.data);
                this.updateProbTimeline(message.data);
                break;
            case 'market_state':
                this.renderMarket(message.data);
                break;
            case 'strategy_stopped':
                this.handleStopped();
                break;
            case 'activity':
                this.handleActivity(message.data);
                break;
            case 'oms_metrics':
                this.renderOMSMetrics(message.data);
                break;
            case 'mm_state':
                this.renderMMState(message.data);
                break;
            case 'heartbeat':
                // Ignore heartbeats
                break;
            default:
                console.log('Unknown message type:', message.type);
        }
    }

    handleSnapshot(data) {
        // Update uptime
        if (data.metrics && data.metrics.uptime_seconds !== undefined) {
            const uptime = this.formatUptime(data.metrics.uptime_seconds);
            document.getElementById('uptime').textContent = 'Uptime: ' + uptime;
        }

        // Load activity log
        if (data.activity_log) {
            this.activityLog = data.activity_log;
            this.renderActivityLog();
        }

        // Load OMS metrics
        if (data.oms_metrics) {
            this.renderOMSMetrics(data.oms_metrics);
        }

        // Load MM states
        if (data.mm_states && data.mm_states.length > 0) {
            data.mm_states.forEach(mm => this.renderMMState(mm));
        }

        // Load strategy session if active
        if (data.strategy_session) {
            this.handleSession(data.strategy_session);
        }
    }

    handleSession(session) {
        this.session = session;

        // Update control state
        const btnStart = document.getElementById('btn-start');
        const btnStop = document.getElementById('btn-stop');
        const statusPanel = document.getElementById('status-panel');

        if (session && session.status === 'running') {
            btnStart.disabled = true;
            btnStop.disabled = false;
            statusPanel.style.display = 'flex';

            // Update selector to match
            const selector = document.getElementById('strategy-selector');
            if (selector) {
                selector.value = session.strategy_type;
                selector.disabled = true;
            }

            // Update mode buttons
            this.mode = session.mode;
            document.getElementById('mode-paper').classList.toggle('active', session.mode === 'paper');
            document.getElementById('mode-live').classList.toggle('active', session.mode === 'live');
        }

        this.renderStatus(session);
        this.renderPositions(session.positions);
        this.renderTrades(session.trades);
        if (session.game) this.renderGame(session.game);
        if (session.market) this.renderMarket(session.market);
    }

    handlePositionUpdate(position) {
        if (!this.session) return;

        // Update or add position
        let found = false;
        for (let i = 0; i < this.session.positions.length; i++) {
            if (this.session.positions[i].ticker === position.ticker &&
                this.session.positions[i].side === position.side) {
                this.session.positions[i] = position;
                found = true;
                break;
            }
        }
        if (!found) {
            this.session.positions.push(position);
        }

        this.renderPositions(this.session.positions);
    }

    handlePositionRemoved(data) {
        if (!this.session) return;

        this.session.positions = this.session.positions.filter(
            p => !(p.ticker === data.ticker && p.side === data.side)
        );
        this.renderPositions(this.session.positions);
    }

    handleTradeUpdate(trade) {
        if (!this.session) return;

        this.session.trades.push(trade);
        this.session.trade_count = this.session.trades.length;

        // Update win/loss counts and P&L
        if (trade.pnl !== null && trade.pnl !== undefined) {
            this.session.realized_pnl += trade.pnl;
            if (trade.pnl > 0) {
                this.session.win_count++;
            } else if (trade.pnl < 0) {
                this.session.loss_count++;
            }
        }
        this.session.total_pnl = this.session.realized_pnl + this.session.unrealized_pnl;

        this.renderTrades(this.session.trades);
        this.renderStatus(this.session);
    }

    handleStopped() {
        this.session = null;

        const btnStart = document.getElementById('btn-start');
        const btnStop = document.getElementById('btn-stop');
        const statusPanel = document.getElementById('status-panel');
        const selector = document.getElementById('strategy-selector');

        btnStart.disabled = false;
        btnStop.disabled = true;
        statusPanel.style.display = 'none';
        if (selector) selector.disabled = false;

        // Clear displays
        this.renderPositions([]);
        this.renderTrades([]);
        document.getElementById('game-display').innerHTML = '<div class="empty-state">No game data</div>';
        document.getElementById('orderbook-display').innerHTML = '<div class="empty-state">No market data</div>';
        document.getElementById('market-ticker').textContent = '';
        document.getElementById('market-info').innerHTML = '';

        // Clear probability timeline
        this.probHistory = [];
        if (this.probChart) {
            this.probChart.destroy();
            this.probChart = null;
        }
        const probPanel = document.getElementById('prob-timeline-panel');
        if (probPanel) probPanel.style.display = 'none';
    }

    handleActivity(data) {
        this.activityLog.push(data);
        if (this.activityLog.length > 200) {
            this.activityLog.shift();
        }
        this.renderActivityLog();
    }

    // ==================== Strategy Control ====================

    onStrategyChange() {
        this.selectedStrategy = document.getElementById('strategy-selector').value;
        this.updateControlState();
    }

    setMode(mode) {
        this.mode = mode;
        document.getElementById('mode-paper').classList.toggle('active', mode === 'paper');
        document.getElementById('mode-live').classList.toggle('active', mode === 'live');
    }

    updateControlState() {
        const btnStart = document.getElementById('btn-start');
        btnStart.disabled = !this.selectedStrategy || (this.session && this.session.status === 'running');
    }

    async startStrategy() {
        const selector = document.getElementById('strategy-selector');
        const strategy = selector.value;

        if (!strategy) {
            alert('Please select a strategy');
            return;
        }

        const btnStart = document.getElementById('btn-start');
        btnStart.disabled = true;
        btnStart.textContent = 'Starting...';

        try {
            const response = await fetch('/api/strategy/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    strategy: strategy,
                    mode: this.mode,
                    config: {}
                })
            });

            const result = await response.json();

            if (response.ok) {
                const btnStop = document.getElementById('btn-stop');
                const statusPanel = document.getElementById('status-panel');
                const gameSelector = document.getElementById('game-selector');

                btnStart.disabled = true;
                btnStart.textContent = 'Start';
                btnStop.disabled = false;
                statusPanel.style.display = 'flex';
                selector.disabled = true;

                // Refresh games list
                await this.loadLiveGames();

                // If a game was already selected, switch to it
                if (this.selectedGame) {
                    await this.switchToMarket(this.selectedGame);
                }

                console.log('Strategy started:', result);
            } else {
                alert('Failed to start strategy: ' + (result.detail || 'Unknown error'));
                btnStart.disabled = false;
                btnStart.textContent = 'Start';
            }
        } catch (error) {
            console.error('Failed to start strategy:', error);
            alert('Failed to start strategy');
            btnStart.disabled = false;
            btnStart.textContent = 'Start';
        }
    }

    async stopStrategy() {
        const btnStop = document.getElementById('btn-stop');
        btnStop.disabled = true;
        btnStop.textContent = 'Stopping...';

        try {
            await fetch('/api/strategy/stop', { method: 'POST' });

            const btnStart = document.getElementById('btn-start');
            const selector = document.getElementById('strategy-selector');

            btnStart.disabled = false;
            btnStop.disabled = true;
            btnStop.textContent = 'Stop';
            if (selector) selector.disabled = false;
        } catch (error) {
            console.error('Failed to stop strategy:', error);
            btnStop.disabled = false;
            btnStop.textContent = 'Stop';
        }
    }

    // ==================== Rendering ====================

    renderStatus(session) {
        if (!session) return;

        const statusEl = document.getElementById('strategy-status');
        const startedEl = document.getElementById('strategy-started');
        const pnlEl = document.getElementById('strategy-pnl');
        const tradesEl = document.getElementById('strategy-trades');
        const winrateEl = document.getElementById('strategy-winrate');

        if (statusEl) {
            statusEl.textContent = session.status.charAt(0).toUpperCase() + session.status.slice(1);
        }

        if (startedEl && session.started_at) {
            startedEl.textContent = new Date(session.started_at).toLocaleTimeString();
        }

        if (pnlEl) {
            const pnl = session.total_pnl || 0;
            pnlEl.textContent = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
            pnlEl.className = 'status-value pnl ' + (pnl >= 0 ? 'positive' : 'negative');
        }

        if (tradesEl) {
            tradesEl.textContent = session.trade_count || 0;
        }

        if (winrateEl) {
            const trades = session.trade_count || 0;
            const wins = session.win_count || 0;
            if (trades > 0) {
                winrateEl.textContent = ((wins / trades) * 100).toFixed(1) + '%';
            } else {
                winrateEl.textContent = '--';
            }
        }
    }

    renderGame(game) {
        const container = document.getElementById('game-display');
        if (!game) {
            container.innerHTML = '<div class="empty-state">No game data</div>';
            return;
        }

        const modelProbPct = (game.model_prob * 100).toFixed(1);

        container.innerHTML = `
            <div class="game-score">
                <span class="team">${game.away_team}</span>
                <span class="score">${game.away_score}</span>
                <span class="vs">@</span>
                <span class="score">${game.home_score}</span>
                <span class="team">${game.home_team}</span>
            </div>
            <div class="game-clock">Q${game.quarter} ${game.clock}</div>
            <div class="game-prob">
                Model: <span class="value">${modelProbPct}%</span> ${game.home_team} win
            </div>
        `;
    }

    renderMarket(market) {
        const tickerEl = document.getElementById('market-ticker');
        const container = document.getElementById('orderbook-display');
        const infoEl = document.getElementById('market-info');

        if (!market) {
            if (tickerEl) tickerEl.textContent = '';
            container.innerHTML = '<div class="empty-state">No market data</div>';
            if (infoEl) infoEl.innerHTML = '';
            return;
        }

        // Update ticker
        if (tickerEl) {
            tickerEl.textContent = market.ticker;
        }

        // Get bids and asks, default to empty arrays
        const bids = market.bids || [];
        const asks = market.asks || [];

        // Find max size for bar width calculation
        const allSizes = [...bids.map(b => b.size), ...asks.map(a => a.size)];
        const maxSize = Math.max(...allSizes, 1);

        // Build order book HTML
        // We'll show asks (reversed so lowest at bottom) then spread then bids
        const askRows = asks.slice().reverse();  // Reverse so best ask is at bottom
        const bidRows = bids;  // Best bid at top

        let html = `
            <div class="orderbook-header">
                <span>Bid Size</span>
                <span>Price</span>
                <span>Ask Size</span>
            </div>
        `;

        // Ask rows (higher prices at top)
        for (const ask of askRows) {
            const barWidth = (ask.size / maxSize) * 100;
            html += `
                <div class="orderbook-row">
                    <div class="orderbook-bid"></div>
                    <div class="orderbook-price ask-price">${ask.price.toFixed(1)}c</div>
                    <div class="orderbook-ask">
                        <div class="size-bar" style="width: ${barWidth}%"></div>
                        <span class="orderbook-size">${ask.size}</span>
                    </div>
                </div>
            `;
        }

        // Spread row
        html += `
            <div class="orderbook-spread">
                <div></div>
                <div>
                    <div class="spread-label">Spread</div>
                    <div class="spread-value">${market.spread.toFixed(1)}c</div>
                </div>
                <div></div>
            </div>
        `;

        // Bid rows (higher prices at top)
        for (const bid of bidRows) {
            const barWidth = (bid.size / maxSize) * 100;
            html += `
                <div class="orderbook-row">
                    <div class="orderbook-bid">
                        <div class="size-bar" style="width: ${barWidth}%"></div>
                        <span class="orderbook-size">${bid.size}</span>
                    </div>
                    <div class="orderbook-price bid-price">${bid.price.toFixed(1)}c</div>
                    <div class="orderbook-ask"></div>
                </div>
            `;
        }

        container.innerHTML = html;

        // Update market info
        if (infoEl) {
            const lastTrade = market.last_trade ? market.last_trade.toFixed(1) + 'c' : '--';
            infoEl.innerHTML = `
                <span>Vol: <span class="value">${market.volume.toLocaleString()}</span></span>
                <span>Last: <span class="value">${lastTrade}</span></span>
            `;
        }
    }

    renderPositions(positions) {
        const tbody = document.getElementById('positions-body');
        if (!positions || positions.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No positions</td></tr>';
            return;
        }

        tbody.innerHTML = positions.map(p => {
            const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'positive' : 'negative';
            const pnlPct = p.entry_price > 0
                ? ((p.current_price - p.entry_price) / p.entry_price * 100).toFixed(1)
                : '0.0';

            return `
                <tr>
                    <td>${this.truncate(p.ticker, 25)}</td>
                    <td class="side-${p.side.toLowerCase()}">${p.side}</td>
                    <td>${p.entry_price.toFixed(0)}c</td>
                    <td>${p.current_price.toFixed(0)}c</td>
                    <td>${p.size}</td>
                    <td class="${pnlClass}">
                        $${(p.unrealized_pnl || 0).toFixed(2)} (${pnlPct}%)
                    </td>
                </tr>
            `;
        }).join('');
    }

    renderTrades(trades) {
        const tbody = document.getElementById('trades-body');
        if (!trades || trades.length === 0) {
            tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No trades</td></tr>';
            return;
        }

        // Show most recent first, limit to 20
        const recentTrades = trades.slice(-20).reverse();

        tbody.innerHTML = recentTrades.map(t => {
            const time = new Date(t.timestamp).toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            });
            const pnlClass = (t.pnl || 0) >= 0 ? 'positive' : 'negative';

            return `
                <tr>
                    <td>${time}</td>
                    <td>${this.truncate(t.ticker, 20)}</td>
                    <td class="action-${t.action.toLowerCase()}">${t.action}</td>
                    <td class="side-${t.side.toLowerCase()}">${t.side}</td>
                    <td>${t.price.toFixed(0)}c</td>
                    <td>${t.size}</td>
                    <td class="${pnlClass}">
                        ${t.pnl != null ? '$' + t.pnl.toFixed(2) : '-'}
                    </td>
                </tr>
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

    // ==================== Probability Timeline ====================

    updateProbTimeline(game) {
        if (!game || game.model_prob == null) return;

        const panel = document.getElementById('prob-timeline-panel');
        if (!panel) return;

        panel.style.display = 'block';

        const now = new Date().toLocaleTimeString('en-US', {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });

        this.probHistory.push({
            time: now,
            prob: (game.model_prob * 100).toFixed(1),
            label: `Q${game.quarter} ${game.clock}`,
        });

        // Keep last 200 points
        if (this.probHistory.length > 200) {
            this.probHistory.shift();
        }

        const canvas = document.getElementById('prob-chart');
        if (!canvas) return;

        const labels = this.probHistory.map(p => p.time);
        const data = this.probHistory.map(p => parseFloat(p.prob));

        if (this.probChart) {
            this.probChart.data.labels = labels;
            this.probChart.data.datasets[0].data = data;
            this.probChart.update('none');  // No animation for performance
        } else {
            const ctx = canvas.getContext('2d');
            this.probChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Home Win %',
                        data: data,
                        borderColor: '#1f77b4',
                        backgroundColor: 'rgba(31, 119, 180, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                        borderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    scales: {
                        y: {
                            min: 0,
                            max: 100,
                            grid: { color: 'rgba(255,255,255,0.05)' },
                            ticks: { color: '#a0a0a0', callback: v => v + '%' },
                        },
                        x: {
                            grid: { display: false },
                            ticks: {
                                color: '#a0a0a0',
                                maxTicksLimit: 10,
                                maxRotation: 0,
                            },
                        }
                    },
                    plugins: {
                        legend: { display: false },
                        annotation: {
                            annotations: {
                                line50: {
                                    type: 'line',
                                    yMin: 50, yMax: 50,
                                    borderColor: 'rgba(255,255,255,0.2)',
                                    borderDash: [5, 5],
                                    borderWidth: 1,
                                }
                            }
                        }
                    }
                }
            });
        }
    }

    // ==================== Market Maker ====================

    renderMMState(mm) {
        const panel = document.getElementById('mm-panel');
        if (!panel) return;

        panel.style.display = 'block';

        // Track state
        this.mmStates[mm.ticker] = mm;

        // Rebuild all cards
        const container = document.getElementById('mm-cards');
        if (!container) return;

        container.innerHTML = Object.values(this.mmStates).map(s => {
            const statusClass = !s.running ? 'mm-status-stopped'
                : s.dry_run ? 'mm-status-dry' : 'mm-status-running';
            const statusText = !s.running ? 'Stopped'
                : s.dry_run ? 'Dry Run' : 'Live';

            const pnlClass = (s.gross_pnl || 0) >= 0 ? 'positive' : 'negative';
            const posClass = s.position > 0 ? 'positive' : s.position < 0 ? 'negative' : '';

            const bidStr = s.active_bid != null ? s.active_bid.toFixed(1) + 'c' : '--';
            const askStr = s.active_ask != null ? s.active_ask.toFixed(1) + 'c' : '--';
            const spreadStr = (s.active_bid != null && s.active_ask != null)
                ? (s.active_ask - s.active_bid).toFixed(1) + 'c' : '--';

            return `
                <div class="mm-card">
                    <div class="mm-card-header">
                        <span class="mm-card-ticker">${s.ticker}</span>
                        <span class="mm-card-status ${statusClass}">${statusText}</span>
                    </div>
                    <div class="mm-card-stats">
                        <div class="mm-card-stat">
                            <span class="mm-card-stat-label">Position</span>
                            <span class="mm-card-stat-value ${posClass}">${s.position}</span>
                        </div>
                        <div class="mm-card-stat">
                            <span class="mm-card-stat-label">P&L</span>
                            <span class="mm-card-stat-value ${pnlClass}">$${(s.gross_pnl || 0).toFixed(2)}</span>
                        </div>
                        <div class="mm-card-stat">
                            <span class="mm-card-stat-label">MtM</span>
                            <span class="mm-card-stat-value">$${(s.mtm_pnl || 0).toFixed(2)}</span>
                        </div>
                        <div class="mm-card-stat">
                            <span class="mm-card-stat-label">Fees</span>
                            <span class="mm-card-stat-value">$${(s.total_fees || 0).toFixed(2)}</span>
                        </div>
                        <div class="mm-card-stat">
                            <span class="mm-card-stat-label">Volume</span>
                            <span class="mm-card-stat-value">${s.total_volume || 0}</span>
                        </div>
                        <div class="mm-card-stat">
                            <span class="mm-card-stat-label">Fills</span>
                            <span class="mm-card-stat-value">${s.fills_received || 0}</span>
                        </div>
                    </div>
                    <div class="mm-card-quotes">
                        <div>
                            <div class="mm-quote-label">Bid</div>
                            <div class="mm-quote-bid">${bidStr}</div>
                        </div>
                        <div>
                            <div class="mm-quote-label">Spread</div>
                            <div class="mm-quote-spread">${spreadStr}</div>
                        </div>
                        <div>
                            <div class="mm-quote-label">Ask</div>
                            <div class="mm-quote-ask">${askStr}</div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    // ==================== OMS Metrics ====================

    renderOMSMetrics(metrics) {
        const panel = document.getElementById('oms-panel');
        if (!panel) return;

        panel.style.display = 'block';

        // Orders
        const byStatus = metrics.orders_by_status || {};
        this.setText('oms-active', metrics.active_orders || 0);
        this.setText('oms-pending', metrics.pending_orders || 0);
        this.setText('oms-filled', byStatus.filled || 0);
        this.setText('oms-canceled', byStatus.canceled || 0);
        this.setText('oms-failed', metrics.failed_orders || 0);
        this.setText('oms-total', metrics.total_tracked_orders || 0);

        // Color failed/violations red if non-zero
        this.setDangerIfNonZero('oms-failed', metrics.failed_orders);
        this.setDangerIfNonZero('oms-violations', metrics.constraint_violations);

        // Fills
        this.setText('oms-fill-contracts', (metrics.total_filled_contracts || 0).toLocaleString());
        this.setText('oms-fill-value', '$' + (metrics.total_filled_value || 0).toFixed(2));
        this.setText('oms-fill-avg-price',
            metrics.avg_fill_price != null ? metrics.avg_fill_price.toFixed(1) + 'c' : '--');
        this.setText('oms-fill-avg-time',
            metrics.avg_fill_time_seconds != null ? metrics.avg_fill_time_seconds.toFixed(2) + 's' : '--');

        // Capital
        const capital = metrics.capital;
        if (capital) {
            this.setText('oms-cap-balance', '$' + (capital.total_balance || 0).toFixed(2));
            this.setText('oms-cap-available', '$' + (capital.total_available || 0).toFixed(2));
            this.setText('oms-cap-reserved', '$' + (capital.total_reserved || 0).toFixed(2));
        } else {
            this.setText('oms-cap-balance', '--');
            this.setText('oms-cap-available', '--');
            this.setText('oms-cap-reserved', '--');
        }
        this.setText('oms-cap-exposure', '$' + (metrics.total_exposure || 0).toFixed(2));

        // Issues
        this.setText('oms-failures', metrics.failed_orders || 0);
        this.setText('oms-violations', metrics.constraint_violations || 0);
        this.setText('oms-exchanges', metrics.exchanges_registered || 0);
        this.setText('oms-timeouts', metrics.timeout_registered || 0);

        // Failure reasons breakdown
        const reasons = metrics.failure_reasons || {};
        const reasonKeys = Object.keys(reasons);
        const reasonsContainer = document.getElementById('oms-failure-reasons');
        const reasonsList = document.getElementById('oms-failure-list');

        if (reasonKeys.length > 0 && reasonsContainer && reasonsList) {
            reasonsContainer.style.display = 'block';
            reasonsList.innerHTML = reasonKeys.map(reason =>
                `<span class="oms-failure-tag">${reason}: ${reasons[reason]}</span>`
            ).join('');
        } else if (reasonsContainer) {
            reasonsContainer.style.display = 'none';
        }

        // Updated timestamp
        if (metrics.updated_at) {
            const time = new Date(metrics.updated_at).toLocaleTimeString();
            this.setText('oms-updated', 'Updated ' + time);
        }
    }

    setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    setDangerIfNonZero(id, value) {
        const el = document.getElementById(id);
        if (!el) return;
        if (value && value > 0) {
            el.classList.add('negative');
        } else {
            el.classList.remove('negative');
        }
    }

    // ==================== Utility Methods ====================

    formatUptime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        return `${hours}h ${minutes}m ${secs}s`;
    }

    truncate(str, length) {
        if (!str) return '';
        if (str.length <= length) return str;
        return str.substring(0, length) + '...';
    }
}

// Start dashboard
const dashboard = new Dashboard();
