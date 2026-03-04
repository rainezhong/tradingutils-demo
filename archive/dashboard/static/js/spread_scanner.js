/**
 * Spread Scanner Tab - Frontend Logic
 *
 * Features:
 * - Configurable refresh rates (fast display, slow scan)
 * - Two-tier polling (display refresh + background discovery)
 * - Toggle-able trade log panel
 * - Mock trading with immediate logging
 * - Smart update notification (show button when new data available)
 */

class SpreadScanner {
    constructor() {
        this.config = {
            fastRefreshInterval: 5000,  // 5 seconds
            slowScanInterval: 60000,    // 60 seconds
            minProfit: 0.05,
            minVolume: 0,
            contractSize: 100,
            discoverMode: true,
        };

        this.currentDisplay = [];
        this.hasNewData = false;
        this.showLog = false;
        this.isRunning = false;

        this.fastRefreshTimer = null;
        this.slowScanTimer = null;

        this.init();
    }

    init() {
        this.setupEventListeners();
        this.loadConfig();
        // Don't auto-start polling - wait for user to click start
    }

    setupEventListeners() {
        // Start/Stop button
        document.getElementById('btn-start-scanner')?.addEventListener('click', () => {
            this.toggleScanner();
        });

        // Update list button
        document.getElementById('btn-update-spreads')?.addEventListener('click', () => {
            this.syncDisplay();
        });

        // Toggle log panel
        document.getElementById('btn-toggle-log')?.addEventListener('click', () => {
            this.toggleLogPanel();
        });

        // Clear log button
        document.getElementById('btn-clear-log')?.addEventListener('click', () => {
            this.clearLogs();
        });

        // Config save button
        document.getElementById('btn-save-config')?.addEventListener('click', () => {
            this.saveConfig();
        });

        // Config form inputs
        const configInputs = document.querySelectorAll('#spread-config-form input');
        configInputs.forEach(input => {
            input.addEventListener('change', () => this.updateConfigFromForm());
        });
    }

    async loadConfig() {
        try {
            const response = await fetch('/api/spreads/config');
            if (response.ok) {
                this.config = await response.json();
                this.updateConfigForm();
            }
        } catch (error) {
            console.error('Failed to load config:', error);
        }
    }

    async saveConfig() {
        try {
            const response = await fetch('/api/spreads/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.config),
            });

            if (response.ok) {
                this.showNotification('Configuration saved', 'success');
                this.restartPolling();
            }
        } catch (error) {
            console.error('Failed to save config:', error);
            this.showNotification('Failed to save configuration', 'error');
        }
    }

    updateConfigForm() {
        document.getElementById('config-fast-refresh').value = this.config.fastRefreshInterval / 1000;
        document.getElementById('config-slow-scan').value = this.config.slowScanInterval / 1000;
        document.getElementById('config-min-profit').value = this.config.minProfit;
        document.getElementById('config-min-volume').value = this.config.minVolume;
        document.getElementById('config-discover').checked = this.config.discoverMode;
    }

    updateConfigFromForm() {
        this.config.fastRefreshInterval = parseInt(document.getElementById('config-fast-refresh').value) * 1000;
        this.config.slowScanInterval = parseInt(document.getElementById('config-slow-scan').value) * 1000;
        this.config.minProfit = parseFloat(document.getElementById('config-min-profit').value);
        this.config.minVolume = parseInt(document.getElementById('config-min-volume').value);
        this.config.discoverMode = document.getElementById('config-discover').checked;
    }

    toggleScanner() {
        if (this.isRunning) {
            this.stopScanner();
        } else {
            this.startScanner();
        }
    }

    startScanner() {
        this.isRunning = true;
        this.updateStartButton();
        this.startPolling();
        this.showNotification('Scanner started', 'success');
    }

    stopScanner() {
        this.isRunning = false;
        this.updateStartButton();
        this.stopPolling();

        // Keep the display visible (don't clear currentDisplay)
        // Hide any alerts since scanner is stopped
        this.hasNewData = false;
        this.updateNewDataAlert();

        this.showNotification('Scanner stopped - data preserved', 'info');
        console.log('[SCANNER] Scanner stopped - loops will exit after current operation');
        console.log('[SCANNER] Current display preserved:', this.currentDisplay.length, 'spreads');
    }

    updateStartButton() {
        const btn = document.getElementById('btn-start-scanner');
        if (!btn) return;

        if (this.isRunning) {
            btn.textContent = '⏹ Stop Scanner';
            btn.classList.remove('btn-start');
            btn.classList.add('btn-stop');
        } else {
            btn.textContent = '▶ Start Scanner';
            btn.classList.remove('btn-stop');
            btn.classList.add('btn-start');
        }
    }

    startPolling() {
        // Clear display and start with background scan only
        this.currentDisplay = [];
        this.renderTable();

        // Start sequential refresh loop (not interval-based)
        // Refreshes top 10 spreads, waits for completion, then waits 5s before next batch
        console.log('[SCANNER] Starting sequential refresh loop (refresh → complete → 5s wait → repeat)');
        this.startRefreshLoop();

        // Start sequential background discovery loop
        // Discovers top 10 pairs, waits for completion, then waits 30s before next scan
        console.log('[SCANNER] Starting sequential discovery loop (scan → complete → 30s wait → repeat)');
        this.startDiscoveryLoop();
    }

    async startRefreshLoop() {
        console.log('[REFRESH] Refresh loop started');
        // Sequential refresh: wait for each batch to complete before starting next
        while (this.isRunning) {
            if (this.currentDisplay.length > 0) {
                const startTime = Date.now();
                console.log(`[REFRESH] Starting refresh batch for ${this.currentDisplay.length} spreads`);

                await this.refreshDisplay();

                const elapsed = Date.now() - startTime;
                console.log(`[REFRESH] Batch completed in ${elapsed}ms`);

                if (!this.isRunning) break; // Exit immediately if stopped

                console.log(`[REFRESH] Waiting 5 seconds before next batch...`);

                // Wait 5 seconds before next refresh batch
                await this.sleep(5000);
            } else {
                // No display data yet, wait a bit before checking again
                await this.sleep(1000);
            }
        }
        console.log('[REFRESH] ✓ Refresh loop stopped');
    }

    async startDiscoveryLoop() {
        console.log('[DISCOVERY] Discovery loop started');
        // Sequential discovery: wait for each scan to complete before starting next
        while (this.isRunning) {
            const startTime = Date.now();
            console.log(`[DISCOVERY] Starting background scan to find top 10 pairs`);

            // Trigger scan and wait for it to complete
            await this.triggerBackgroundScan();

            if (!this.isRunning) break; // Exit immediately if stopped

            console.log(`[DISCOVERY] Scan triggered, waiting for backend to complete...`);

            // Wait for backend scan to complete (typically 10-60 seconds)
            // Poll every 2 seconds to check if scan is done
            let scanComplete = false;
            let attempts = 0;
            while (!scanComplete && attempts < 30 && this.isRunning) { // Max 60 seconds wait
                await this.sleep(2000);

                if (!this.isRunning) break; // Exit immediately if stopped

                attempts++;

                // Check if background cache was updated (scan completed)
                try {
                    const response = await fetch('/api/spreads/background');
                    if (response.ok) {
                        const data = await response.json();
                        if (data.last_scanned && data.last_scanned > startTime / 1000) {
                            scanComplete = true;
                            console.log(`[DISCOVERY] Scan completed after ${attempts * 2}s`);
                        }
                    }
                } catch (error) {
                    console.error('[DISCOVERY] Error checking scan status:', error);
                }
            }

            if (!this.isRunning) break; // Exit immediately if stopped

            // Check if we have new data (different from current display)
            console.log(`[DISCOVERY] Checking for new opportunities...`);
            await this.checkForNewData();

            if (!this.isRunning) break; // Exit immediately if stopped

            console.log(`[DISCOVERY] Waiting 30 seconds before next scan...`);
            await this.sleep(30000);
        }
        console.log('[DISCOVERY] ✓ Discovery loop stopped');
    }

    sleep(ms) {
        // Sleep with ability to check isRunning periodically
        return new Promise((resolve) => {
            const checkInterval = 500; // Check every 500ms if we should exit
            let elapsed = 0;

            const check = () => {
                if (!this.isRunning || elapsed >= ms) {
                    resolve();
                } else {
                    elapsed += checkInterval;
                    setTimeout(check, Math.min(checkInterval, ms - elapsed));
                }
            };

            check();
        });
    }

    stopPolling() {
        // Stop the refresh loop by setting isRunning to false
        // The while loop will exit on next iteration
        console.log('[SCANNER] Stopping refresh loop...');
    }

    restartPolling() {
        this.stopPolling();
        this.startPolling();
    }

    async refreshDisplay() {
        try {
            const response = await fetch('/api/spreads/refresh', { method: 'POST' });
            if (response.ok) {
                const data = await response.json();
                this.currentDisplay = data.opportunities;
                this.hasNewData = data.has_new_data;
                this.renderTable();
                this.updateTimestamp('display', data.last_updated);
                this.updateNewDataAlert();
            }
        } catch (error) {
            console.error('Failed to refresh display:', error);
        }
    }

    async triggerBackgroundScan() {
        try {
            const response = await fetch('/api/spreads/discover', { method: 'POST' });
            if (response.ok) {
                // Scan runs in background
                this.updateTimestamp('scan', Date.now() / 1000);
                console.log('[DISCOVERY] Background scan request sent');
                // Note: Discovery loop will wait for completion and check for new data
            }
        } catch (error) {
            console.error('Failed to trigger background scan:', error);
        }
    }

    async checkForNewData() {
        try {
            console.log('[ALERT] Checking for new opportunities...');

            const response = await fetch('/api/spreads/display');
            if (response.ok) {
                const data = await response.json();

                console.log('[ALERT] Current display count:', data.opportunities.length);
                console.log('[ALERT] has_new_data flag:', data.has_new_data);

                if (data.has_new_data) {
                    // Log what changed
                    const currentIds = data.opportunities.map(o => o.spread_id).slice(0, 10);
                    console.log('[ALERT] Current top 10 IDs:', currentIds);

                    // Fetch background cache to show what's different
                    const bgResponse = await fetch('/api/spreads/background');
                    if (bgResponse.ok) {
                        const bgData = await bgResponse.json();
                        const bgIds = bgData.opportunities.map(o => o.spread_id).slice(0, 10);
                        console.log('[ALERT] Background top 10 IDs:', bgIds);

                        // Show differences
                        const newInBg = bgIds.filter(id => !currentIds.includes(id));
                        const removedFromCurrent = currentIds.filter(id => !bgIds.includes(id));

                        if (newInBg.length > 0) {
                            console.log('[ALERT] New spreads in background:', newInBg);
                        }
                        if (removedFromCurrent.length > 0) {
                            console.log('[ALERT] Spreads removed from top 10:', removedFromCurrent);
                        }
                    }
                }

                this.hasNewData = data.has_new_data;
                this.updateNewDataAlert();
            }
        } catch (error) {
            console.error('[ALERT] Failed to check for new data:', error);
        }
    }

    async syncDisplay() {
        try {
            console.log('[ALERT] 🔄 "Update List" button clicked');
            console.log('[ALERT]   Syncing background cache to display...');

            // Sync the current background cache to display
            const response = await fetch('/api/spreads/sync', { method: 'POST' });
            if (response.ok) {
                const data = await response.json();

                const oldCount = this.currentDisplay.length;
                const newCount = data.opportunities.length;

                console.log('[ALERT]   Previous display count:', oldCount);
                console.log('[ALERT]   New display count:', newCount);

                if (newCount > 0) {
                    const topIds = data.opportunities.slice(0, 5).map(o => o.spread_id);
                    console.log('[ALERT]   New top 5 spread IDs:', topIds);
                }

                this.currentDisplay = data.opportunities;
                this.hasNewData = false; // Hide the alert
                this.renderTable();
                this.updateTimestamp('display', data.last_updated);
                this.updateNewDataAlert(); // This will hide the alert banner
                this.showNotification('Display updated with new opportunities', 'success');

                console.log('[ALERT]   ✅ Display synced successfully');
            }
            // Note: Discovery loop continues automatically in background
        } catch (error) {
            console.error('[ALERT] ❌ Failed to sync display:', error);
        }
    }

    renderTable() {
        const tbody = document.getElementById('spreads-table-body');
        if (!tbody) return;

        if (this.currentDisplay.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No opportunities found</td></tr>';
            return;
        }

        tbody.innerHTML = this.currentDisplay.map((opp, index) => `
            <tr>
                <td>${opp.rank}</td>
                <td>
                    <div class="event-title">${this.escapeHtml(opp.event_title)}</div>
                    <div class="event-subtitle">${this.escapeHtml(opp.ticker_a)} vs ${this.escapeHtml(opp.ticker_b)}</div>
                </td>
                <td class="profit-cell ${this.getProfitClass(opp.profit_change)}">
                    $${opp.profit.toFixed(4)}
                    ${opp.profit_change !== 0 ? `<span class="change">${this.formatChange(opp.profit_change)}</span>` : ''}
                </td>
                <td>
                    <div>${opp.ask_a.toFixed(2)} / ${opp.ask_b.toFixed(2)}</div>
                    <div class="combined">Combined: ${opp.combined_cost.toFixed(2)}</div>
                </td>
                <td class="volume-cell">
                    ${this.formatVolume(opp.volume)}
                    ${opp.volume_change_pct !== 0 ? `<span class="change">${this.formatChange(opp.volume_change_pct)}%</span>` : ''}
                </td>
                <td class="action-cell">
                    <button class="btn-trade btn-trade-full" onclick="spreadScanner.trade('${opp.spread_id}', ${opp.volume})">
                        Trade Full
                    </button>
                    <button class="btn-trade btn-trade-one" onclick="spreadScanner.trade('${opp.spread_id}', 1)">
                        Trade 1
                    </button>
                </td>
            </tr>
        `).join('');
    }

    async trade(spreadId, amount) {
        try {
            console.log(`[TRADE] 📊 Trade button clicked`);
            console.log(`[TRADE]   Spread ID: ${spreadId}`);
            console.log(`[TRADE]   Amount: ${amount} contracts`);

            // Find the opportunity details from current display
            const opp = this.currentDisplay.find(o => o.spread_id === spreadId);
            if (opp) {
                console.log(`[TRADE]   Event: ${opp.event_title}`);
                console.log(`[TRADE]   Ticker A: ${opp.ticker_a} @ $${opp.ask_a.toFixed(2)}`);
                console.log(`[TRADE]   Ticker B: ${opp.ticker_b} @ $${opp.ask_b.toFixed(2)}`);
                console.log(`[TRADE]   Combined Cost: $${opp.combined_cost.toFixed(2)}`);
                console.log(`[TRADE]   Profit per Contract: $${opp.profit.toFixed(4)}`);
                console.log(`[TRADE]   Total Cost: $${(opp.combined_cost * amount).toFixed(2)}`);
                console.log(`[TRADE]   Expected Profit: $${(opp.profit * amount).toFixed(2)}`);
                console.log(`[TRADE]   Volume: ${opp.volume}`);
            }

            const response = await fetch('/api/spreads/trade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ spread_id: spreadId, amount }),
            });

            if (response.ok) {
                const data = await response.json();
                console.log(`[TRADE] ✅ Trade logged successfully`);
                this.addLogEntry(data.trade_log);
                this.showNotification(`Mock trade logged: ${amount} contracts`, 'success');
            }
        } catch (error) {
            console.error('[TRADE] ❌ Failed to log trade:', error);
            this.showNotification('Failed to log trade', 'error');
        }
    }

    toggleLogPanel() {
        this.showLog = !this.showLog;
        const panel = document.getElementById('trade-log-panel');
        const btn = document.getElementById('btn-toggle-log');

        if (this.showLog) {
            panel.classList.remove('hidden');
            btn.textContent = '✕ Hide Log';
            this.loadLogs();
        } else {
            panel.classList.add('hidden');
            btn.textContent = '☰ Show Log';
        }
    }

    async loadLogs() {
        try {
            const response = await fetch('/api/spreads/logs');
            if (response.ok) {
                const data = await response.json();
                this.renderLogs(data.logs);
            }
        } catch (error) {
            console.error('Failed to load logs:', error);
        }
    }

    addLogEntry(log) {
        const container = document.getElementById('trade-log-entries');
        if (!container) return;

        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = `
            <div class="log-header">
                <span class="log-time">${new Date(log.timestamp).toLocaleTimeString()}</span>
                <span class="log-status">${log.status}</span>
            </div>
            <div class="log-title">${this.escapeHtml(log.event_title)}</div>
            <div class="log-details">
                <div><strong>Action:</strong> ${log.action} ${log.amount} contracts</div>
                <div><strong>Leg A:</strong> ${log.action} ${log.leg_a.ticker} ${log.leg_a.side} @ $${log.leg_a.price.toFixed(2)} (${log.leg_a.amount} contracts)</div>
                <div><strong>Leg B:</strong> ${log.action} ${log.leg_b.ticker} ${log.leg_b.side} @ $${log.leg_b.price.toFixed(2)} (${log.leg_b.amount} contracts)</div>
                <div><strong>Cost:</strong> $${log.total_cost.toFixed(2)}</div>
                <div><strong>Expected Profit:</strong> $${log.expected_profit.toFixed(2)}</div>
            </div>
        `;

        container.insertBefore(entry, container.firstChild);

        // Keep only last 50 entries in DOM
        while (container.children.length > 50) {
            container.removeChild(container.lastChild);
        }
    }

    renderLogs(logs) {
        const container = document.getElementById('trade-log-entries');
        if (!container) return;

        container.innerHTML = logs.reverse().map(log => `
            <div class="log-entry">
                <div class="log-header">
                    <span class="log-time">${new Date(log.timestamp).toLocaleTimeString()}</span>
                    <span class="log-status">${log.status}</span>
                </div>
                <div class="log-title">${this.escapeHtml(log.event_title)}</div>
                <div class="log-details">
                    <div><strong>Action:</strong> ${log.action} ${log.amount} contracts</div>
                    <div><strong>Leg A:</strong> ${log.leg_a.ticker} ${log.leg_a.side} @ $${log.leg_a.price.toFixed(2)}</div>
                    <div><strong>Leg B:</strong> ${log.leg_b.ticker} ${log.leg_b.side} @ $${log.leg_b.price.toFixed(2)}</div>
                    <div><strong>Cost:</strong> $${log.total_cost.toFixed(2)}</div>
                    <div><strong>Expected Profit:</strong> $${log.expected_profit.toFixed(2)}</div>
                </div>
            </div>
        `).join('');
    }

    async clearLogs() {
        try {
            const response = await fetch('/api/spreads/logs', { method: 'DELETE' });
            if (response.ok) {
                const container = document.getElementById('trade-log-entries');
                if (container) container.innerHTML = '';
                this.showNotification('Logs cleared', 'success');
            }
        } catch (error) {
            console.error('Failed to clear logs:', error);
        }
    }

    updateNewDataAlert() {
        const alert = document.getElementById('new-data-alert');
        if (!alert) return;

        if (this.hasNewData) {
            console.log('[ALERT] 🔔 Showing "New opportunities found" alert');
            console.log('[ALERT]   Reason: Background cache differs from current display');
            alert.classList.remove('hidden');
        } else {
            console.log('[ALERT] ❌ Hiding alert');
            console.log('[ALERT]   Reason: hasNewData = false (display is up to date)');
            alert.classList.add('hidden');
        }
    }

    updateTimestamp(type, timestamp) {
        const elem = document.getElementById(`timestamp-${type}`);
        if (!elem) return;

        if (timestamp) {
            const seconds = Math.floor(Date.now() / 1000 - timestamp);
            elem.textContent = seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`;
        }
    }

    getProfitClass(change) {
        if (change > 0.001) return 'profit-up';
        if (change < -0.001) return 'profit-down';
        return 'profit-stable';
    }

    formatChange(value) {
        return value > 0 ? `↑${Math.abs(value).toFixed(2)}` : `↓${Math.abs(value).toFixed(2)}`;
    }

    formatVolume(volume) {
        if (volume >= 1000) {
            return `${(volume / 1000).toFixed(1)}K`;
        }
        return volume.toString();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    showNotification(message, type = 'info') {
        // Simple notification system
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);

        setTimeout(() => {
            notification.classList.add('fade-out');
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }
}

// Global instance
let spreadScanner;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('spread-scanner-tab')) {
        spreadScanner = new SpreadScanner();
    }
});
