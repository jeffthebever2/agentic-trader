# TradingAgents API Documentation (v1.0.0)

## Core Classes

### TradingAgentsGraph

The main class for running the multi-agent trading framework.

#### Constructor

```python
TradingAgentsGraph(
    selected_analysts: Optional[List[str]] = None,
    debug: bool = False,
    config: Optional[Dict[str, Any]] = None,
    callbacks: Optional[List] = None,
)
```

**Parameters:**
- `selected_analysts`: List of analyst types to include. Defaults to `["market", "social", "news", "fundamentals"]`
- `debug`: Enable debug mode for detailed logging
- `config`: Configuration dictionary. Uses `DEFAULT_CONFIG` if None
- `callbacks`: Optional list of callback handlers for LLM/tool monitoring

#### Methods

##### propagate(company_name: str, trade_date: str) -> Tuple[Dict[str, Any], str]

Run the trading agents graph for a specific company and date.

**Parameters:**
- `company_name`: Ticker symbol (e.g., 'AAPL')
- `trade_date`: Date string in YYYY-MM-DD format

**Returns:**
- Tuple of (final_state_dict, decision_string)

**Raises:**
- `ValueError`: For invalid company_name or trade_date
- `RuntimeError`: For execution failures

**Example:**
```python
from tradingagents.graph.trading_graph import TradingAgentsGraph

# Initialize with default config
ta = TradingAgentsGraph()

# Run analysis for Apple on a specific date
state, decision = ta.propagate("AAPL", "2024-01-15")
print(f"Decision: {decision}")
```

## Configuration

The framework uses a configuration dictionary with the following key options:

- `llm_provider`: LLM provider ('openai', 'anthropic', 'google', etc.)
- `deep_think_llm`: Model for complex reasoning tasks
- `quick_think_llm`: Model for fast responses
- `max_debate_rounds`: Maximum rounds of agent debate
- `data_cache_dir`: Directory for caching data
- `checkpoint_enabled`: Enable checkpoint/resume functionality

## Data Providers

The framework supports multiple data sources:

- **yfinance**: Free stock data, fundamentals, news
- **alpha_vantage**: Premium financial data (requires API key)
- **fmp**: Financial Modeling Prep data
- **sec**: SEC EDGAR filings

## Agent Types

- **Fundamentals Analyst**: Company financial analysis
- **Sentiment Analyst**: Social media and news sentiment
- **Technical Analyst**: Chart patterns and indicators
- **News Analyst**: Global news impact assessment
- **Research Team**: Bull/bear debate and analysis
- **Trader**: Decision making and trade timing
- **Risk Manager**: Portfolio risk assessment
- **Portfolio Manager**: Final trade approval

## Error Handling

The framework includes comprehensive error handling:

- Input validation for tickers and dates
- API rate limiting and retry logic
- Graceful degradation when data sources fail
- Structured logging for debugging

## Data Caching

The dataflow layer supports file-based caching for vendor data requests.
Cache behavior is enabled by default and controlled via configuration:

- `data_cache_enabled`: enable or disable vendor response caching
- `data_cache_ttl_hours`: time-to-live for cached responses in hours

The cache is automatically used by `route_to_vendor()` when resolving stock, news, social, and fundamentals data.

## CLI Metrics

A new CLI command is available for displaying runtime metrics:

```bash
tradingagents metrics
```

This command prints collected counters and timing metrics such as:

- `propagate_success`
- `propagate_failure`
- `data_cache_hit`
- `data_cache_miss`
- `api_call_<provider>_<endpoint>_success`
- `api_call_<provider>_<endpoint>_error`

## Performance Monitoring

Built-in metrics collection for:

- API call counts and success rates
- LLM token usage
- Operation timing
- Cache hit rates

Access metrics via:
```python
from tradingagents.metrics import get_metrics
metrics = get_metrics()
summary = metrics.get_summary()
```