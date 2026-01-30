# Production Deployment Guide

This guide covers deploying the Kalshi Market Data Collector to a production Linux server.

## Prerequisites

- Linux server (Ubuntu 20.04+ or similar)
- Python 3.9+
- SQLite 3
- systemd (for service management)
- 1GB+ RAM
- 10GB+ disk space

## Installation

### 1. Create System User

```bash
# Create dedicated user for the service
sudo useradd -r -s /bin/false kalshi
```

### 2. Install Application

```bash
# Create application directory
sudo mkdir -p /opt/tradingutils
sudo chown kalshi:kalshi /opt/tradingutils

# Clone or copy application files
sudo -u kalshi git clone https://github.com/yourusername/tradingutils.git /opt/tradingutils
cd /opt/tradingutils

# Run setup
sudo -u kalshi ./scripts/setup.sh
```

### 3. Configure Application

```bash
# Edit configuration
sudo -u kalshi nano /opt/tradingutils/config.yaml
```

Recommended production settings:

```yaml
db_path: "/opt/tradingutils/data/markets.db"
api_base_url: "https://api.elections.kalshi.com/trade-api/v2"
api_timeout: 30
api_max_retries: 3

rate_limits:
  requests_per_second: 10
  requests_per_minute: 100

min_volume: 1000
snapshot_interval_seconds: 60

log_level: "INFO"
```

### 4. Create Log Directory

```bash
sudo mkdir -p /var/log/kalshi-collector
sudo chown kalshi:kalshi /var/log/kalshi-collector
```

### 5. Install Systemd Service

```bash
# Copy service file
sudo cp /opt/tradingutils/systemd/kalshi-collector.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable kalshi-collector

# Start the service
sudo systemctl start kalshi-collector

# Check status
sudo systemctl status kalshi-collector
```

## Service Management

### Basic Commands

```bash
# Start service
sudo systemctl start kalshi-collector

# Stop service
sudo systemctl stop kalshi-collector

# Restart service
sudo systemctl restart kalshi-collector

# Check status
sudo systemctl status kalshi-collector

# View logs
sudo journalctl -u kalshi-collector -f

# View application logs
tail -f /var/log/kalshi-collector/scheduler.log
```

### Manual Operations

```bash
# Switch to application directory
cd /opt/tradingutils
source venv/bin/activate

# Run manual scan
sudo -u kalshi python main.py scan

# Run manual pipeline
sudo -u kalshi python main.py pipeline

# Check health
sudo -u kalshi python main.py healthcheck

# View monitor
sudo -u kalshi python main.py monitor
```

## Backup Strategy

### Automated Backups

Add to crontab for daily backups:

```bash
# Edit crontab
sudo crontab -e

# Add daily backup at 2 AM
0 2 * * * /opt/tradingutils/scripts/backup_db.sh /opt/tradingutils/backups >> /var/log/kalshi-collector/backup.log 2>&1
```

### Manual Backup

```bash
sudo -u kalshi /opt/tradingutils/scripts/backup_db.sh
```

### Restore from Backup

```bash
# Stop service
sudo systemctl stop kalshi-collector

# Restore database
gunzip -c /opt/tradingutils/backups/markets_20240115_020000.db.gz > /opt/tradingutils/data/markets.db
chown kalshi:kalshi /opt/tradingutils/data/markets.db

# Start service
sudo systemctl start kalshi-collector
```

## Monitoring

### Health Check Cron

Add health check monitoring:

```bash
# Check health every 5 minutes
*/5 * * * * cd /opt/tradingutils && /opt/tradingutils/venv/bin/python main.py healthcheck --alert-if-unhealthy || echo "Health check failed" | mail -s "Kalshi Collector Alert" admin@example.com
```

### Log Rotation

Create `/etc/logrotate.d/kalshi-collector`:

```
/var/log/kalshi-collector/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 kalshi kalshi
    postrotate
        systemctl reload kalshi-collector > /dev/null 2>&1 || true
    endscript
}
```

### Prometheus Metrics (Optional)

For Prometheus monitoring, you can add an HTTP endpoint. See the `alerter.py` module for integration points.

## Security

### Firewall

The collector only makes outbound HTTPS requests. No inbound ports need to be opened.

### File Permissions

```bash
# Ensure proper ownership
sudo chown -R kalshi:kalshi /opt/tradingutils
sudo chmod 750 /opt/tradingutils
sudo chmod 640 /opt/tradingutils/config.yaml

# Protect database
sudo chmod 640 /opt/tradingutils/data/markets.db
```

### Secrets Management

For API keys (if needed in future):

1. Use environment variables
2. Or use a secrets manager
3. Never commit secrets to git

```bash
# Example: Set secrets via environment
sudo systemctl edit kalshi-collector

# Add:
[Service]
Environment="KALSHI_API_KEY=your-key-here"
```

## Troubleshooting

### Service Won't Start

```bash
# Check service status
sudo systemctl status kalshi-collector

# Check logs
sudo journalctl -u kalshi-collector -n 50

# Test manually
cd /opt/tradingutils
source venv/bin/activate
python main.py healthcheck
```

### Database Locked

```bash
# Stop service
sudo systemctl stop kalshi-collector

# Check for lock
fuser /opt/tradingutils/data/markets.db

# Restart
sudo systemctl start kalshi-collector
```

### High Memory Usage

The SQLite database may grow large over time. Consider:

1. Archiving old snapshots
2. Increasing server RAM
3. Running analysis less frequently

```sql
-- Archive snapshots older than 30 days
DELETE FROM snapshots WHERE timestamp < datetime('now', '-30 days');
VACUUM;
```

### API Rate Limiting

If you see 429 errors:

1. Reduce `requests_per_second` in config
2. Reduce `requests_per_minute` in config
3. Check for multiple instances running

## Scaling

### Multiple Instances

To run multiple collectors (e.g., different market categories):

1. Create separate config files
2. Create separate systemd services
3. Use separate databases

### Database Migration

For large datasets, consider migrating to PostgreSQL:

1. Export data from SQLite
2. Update `database.py` to use psycopg2
3. Import data to PostgreSQL

## Updates

### Updating the Application

```bash
# Stop service
sudo systemctl stop kalshi-collector

# Backup database
sudo -u kalshi /opt/tradingutils/scripts/backup_db.sh

# Pull updates
cd /opt/tradingutils
sudo -u kalshi git pull

# Update dependencies
sudo -u kalshi ./venv/bin/pip install -r requirements.txt

# Run tests
sudo -u kalshi ./venv/bin/pytest

# Start service
sudo systemctl start kalshi-collector
```

## Support

For issues:
1. Check logs: `journalctl -u kalshi-collector`
2. Run health check: `python main.py healthcheck`
3. Check monitor: `python main.py monitor`
4. Open GitHub issue with logs
