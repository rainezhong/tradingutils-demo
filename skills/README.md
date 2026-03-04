# Research Cycle Skill - No API Calls Required!

This skill orchestrates autonomous trading research using **Claude Code's Task tool** instead of paid Anthropic API calls. Everything runs on your existing Claude subscription.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  You: claude run research cycle                    │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Python: Research Orchestrator                      │
│  ├─ Discover data sources (pure code)              │
│  ├─ Need LLM? → Write request file                 │
│  └─ Signal: "NEEDS_LLM"                            │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Claude Code (you): Detect signal                   │
│  ├─ Read request file                              │
│  ├─ Spawn Task agent with prompt                   │◄─── Uses Claude subscription!
│  ├─ Agent generates analysis plans                 │     No extra cost!
│  └─ Write response file                            │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  Python: Resume orchestrator                        │
│  ├─ Read response file                             │
│  ├─ Execute analysis plans (pure code)             │
│  └─ Generate hypotheses                            │
└─────────────────────────────────────────────────────┘
```

## How It Works

### **Phase 1: Data Discovery** (Pure Python)
```python
# The orchestrator discovers all databases
data_sources = discover_data_sources()

# Found:
# - weather_markets.db (Kalshi temperature markets)
# - noaa_forecasts.db (NOAA weather forecasts)
# - btc_latency_probe.db (BTC prices on Kraken + Kalshi)
```

### **Phase 2: LLM Reasoning Request** (File-Based)
```python
# Python writes a request file
write_file("tmp/llm_requests/data_scout_request.json", {
    "task": "generate_analysis_plans",
    "data_sources": [...]
})

# Returns signal
return {"status": "NEEDS_LLM", "request_file": "..."}
```

### **Phase 3: Claude Code Processes** (Task Tool)
When Claude Code sees the signal, it:

1. Reads the request file
2. Generates a prompt for a sub-agent
3. Spawns the agent using Task tool
4. Writes the response file

Example prompt generated:
```
You are analyzing trading data sources for edges.

Available data:
- weather_markets.db: Kalshi temperature markets
- noaa_forecasts.db: NOAA forecasts with confidence intervals

Suggest comparisons that could reveal mispricings.
Return JSON analysis plans.
```

Sub-agent returns:
```json
{
  "analysis_plans": [
    {
      "name": "Weather Forecast Arbitrage",
      "compare": ["weather_markets", "noaa_forecasts"],
      "edge_definition": "NOAA confidence > market price",
      "reasoning": "Retail traders use weather apps, not raw NOAA data..."
    }
  ]
}
```

### **Phase 4: Execution** (Pure Python)
```python
# Python reads the response
plans = read_file("tmp/llm_responses/data_scout_response.json")

# Executes each plan (statistical analysis, SQL queries, etc.)
for plan in plans:
    results = execute_plan(plan)  # Pure code - fast!
```

## Usage

### **Option 1: Via Claude Code** (Recommended)

```bash
# In your terminal where Claude Code is running:
claude run research cycle
```

Claude Code will:
- Execute `skills/research_cycle.py`
- Detect when LLM reasoning is needed
- Spawn sub-agents automatically
- Display results

### **Option 2: Manual** (For Testing)

```bash
# Step 1: Run orchestrator (stops when LLM needed)
python3 skills/research_cycle.py

# Output:
# ⏸️  PAUSED: LLM Reasoning Required
# Request file: tmp/llm_requests/data_scout_request.json

# Step 2: Process LLM request manually
python3 skills/llm_agent_handler.py  # Generates sample response

# Step 3: Resume orchestrator
python3 skills/research_cycle.py  # Reads response, continues
```

### **Option 3: With Real Claude Code Integration**

```bash
# This is what YOU (Claude Code) would run internally:

# 1. Execute orchestrator
result = run_python("skills/research_cycle.py")

# 2. If NEEDS_LLM:
if result["status"] == "NEEDS_LLM":
    # Read request
    request = read_json(result["request_file"])

    # Spawn sub-agent
    response = Task(
        subagent_type="general-purpose",
        description="Generate analysis plans",
        prompt=generate_prompt(request)
    )

    # Write response
    write_json(result["response_file"], parse(response))

    # Resume orchestrator
    result = run_python("skills/research_cycle.py")

# 3. Display final result
print(result)
```

## File-Based Interface

### **Request Format** (`tmp/llm_requests/*.json`)
```json
{
  "task": "generate_analysis_plans",
  "timestamp": "2026-02-27T...",
  "data_sources": [
    {
      "path": "data/weather_markets.db",
      "name": "weather_markets",
      "description": "Kalshi temperature markets",
      "tables": ["markets", "prices"],
      "row_count": 12480
    }
  ],
  "instructions": "Suggest comparisons for trading edges..."
}
```

### **Response Format** (`tmp/llm_responses/*.json`)
```json
{
  "analysis_plans": [
    {
      "name": "Weather Forecast Arbitrage",
      "data_sources": ["weather_markets", "noaa_forecasts"],
      "comparison_type": "forecast_vs_market_price",
      "edge_definition": "noaa_confidence > market_price/100 + 0.30",
      "reasoning": "Information asymmetry between NOAA and retail traders",
      "expected_pattern": "Markets < 15¢ when NOAA > 70% confident",
      "statistical_test": "T-test for systematic underpricing"
    }
  ]
}
```

## Cost Comparison

| **Method** | **Cost** | **How It Works** |
|------------|----------|------------------|
| **Anthropic API** | $0.50-2.00 per cycle | Direct API calls to Claude |
| **Claude Code Task** | $0 (included in subscription) | Uses your Claude subscription via Task tool |

**Example:**
- Research cycle with 5 LLM calls
- API cost: ~$2.50
- Task tool cost: $0 (part of Claude subscription)
- **Savings: $2.50 per cycle × 30 cycles/month = $75/month**

## Benefits

### ✅ **No Separate Billing**
- All on your Claude subscription
- No API key management
- No usage tracking needed

### ✅ **Full Transparency**
- See every LLM request
- Review prompts before execution
- Understand the reasoning

### ✅ **Cost Effective**
- Included in subscription
- No per-call charges
- Unlimited research cycles (within subscription limits)

### ✅ **Simple Architecture**
- File-based interface (easy to debug)
- No complex API integrations
- Pure Python for execution (fast!)

## Next Steps

1. **Test the flow:**
   ```bash
   python3 skills/research_cycle.py
   ```

2. **Add your data:**
   ```bash
   # Add weather data
   python3 scripts/collect_weather_data.py

   # Add NOAA forecasts
   python3 scripts/collect_noaa_forecasts.py
   ```

3. **Run research cycle:**
   ```bash
   claude run research cycle
   ```

4. **Review findings:**
   ```bash
   # Check research database
   python3 research/inspect_db.py

   # Open generated reports
   jupyter notebook research/reports/
   ```

## Troubleshooting

**Problem:** "NEEDS_LLM signal but no response"
- Check if `tmp/llm_requests/` has request file
- Manually run `llm_agent_handler.py` to generate test response
- Or use Claude Code to spawn real agent

**Problem:** "Analysis plans not found"
- Check `tmp/llm_responses/` for response file
- Verify JSON format matches expected structure
- Check timestamps (responses expire after 1 hour)

**Problem:** "Data sources not discovered"
- Verify databases exist in `data/` directory
- Check SQLite files are not corrupted
- Run `python3 -c "from agents.data_scout_llm import HybridDataScout; HybridDataScout().discover_data_sources()"`

## Architecture Notes

**Why file-based instead of direct integration?**
- Claude Code Task tool is available to ME (Claude Code), not to your Python scripts
- File-based interface provides clean separation
- Easy to debug and inspect
- Resumable (can stop/start without losing state)

**Can Python call Claude Code directly?**
- Not directly - Task tool only works within Claude Code execution context
- File-based interface is the bridge between Python ↔ Claude Code
- Future: Could create MCP server for this, but files are simpler

**Is this slower than API calls?**
- File I/O is negligible (< 1ms)
- Task spawning is similar speed to API call
- Overall: Same speed, $0 cost!
