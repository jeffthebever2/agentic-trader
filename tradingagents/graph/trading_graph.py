# TradingAgents/graph/trading_graph.py

import logging
import os
from pathlib import Path
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List, Optional

import yfinance as yf

from tradingagents.logging_config import get_logger

logger = get_logger(__name__)

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config
from tradingagents.metrics import get_metrics, timed_operation
from tradingagents.portfolio import (
    CorrelationAnalyzer,
    DrawdownMonitor,
    PortfolioState,
)
from tradingagents.agents.news_impact_filter import NewsImpactFilter
from tradingagents.agents.analysts.regime_analyst import get_market_regime, get_vix_regime
from tradingagents.agents.trader.execution_scheduler import ExecutionScheduler

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news,
    get_social_sentiment,
)

from .checkpointer import checkpoint_step, clear_checkpoint, get_checkpointer, thread_id
from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor

try:
    from tradingagents.rl.rl_signal import RLSignalProvider
    _RL_AVAILABLE = True
except ImportError:
    _RL_AVAILABLE = False


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts: Optional[List[str]] = None,
        debug: bool = False,
        config: Optional[Dict[str, Any]] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        if selected_analysts is None:
            selected_analysts = ["market", "social", "news", "fundamentals"]
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        
        self.memory_log = TradingMemoryLog(self.config)
        self.portfolio_state = PortfolioState(self.config)
        self.metrics = get_metrics()
        self.drawdown_monitor = DrawdownMonitor(self.config)
        self.correlation_analyzer = CorrelationAnalyzer()
        self.news_impact_filter = NewsImpactFilter()
        self.execution_scheduler = ExecutionScheduler()

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.conditional_logic,
        )

        self.propagator = Propagator()
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # RL signal provider (optional — loads if a trained checkpoint exists)
        self.rl_signal_provider: Optional["RLSignalProvider"] = None
        if _RL_AVAILABLE:
            rl_checkpoint = self.config.get(
                "rl_checkpoint_dir", "rl_models/td3_checkpoint"
            )
            rl_path = Path(rl_checkpoint)
            if rl_path.exists() and (rl_path / "meta.json").exists():
                try:
                    self.rl_signal_provider = RLSignalProvider.from_checkpoint(
                        rl_path,
                        device=self.config.get("rl_device", "cpu"),
                    )
                    logger.info("TD3 RL signal provider loaded from %s", rl_path)
                except Exception as exc:
                    logger.warning("Could not load RL signal provider: %s", exc)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph: keep the workflow for recompilation with a checkpointer.
        self.workflow = self.graph_setup.setup_graph(selected_analysts)
        self.graph = self.workflow.compile()
        self._checkpointer_ctx = None

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # Social/news tools for sentiment analysis
                    get_news,
                    get_social_sentiment,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def _fetch_returns(
        self, ticker: str, trade_date: str, holding_days: int = 5
    ) -> Tuple[Optional[float], Optional[float], Optional[int]]:
        """Fetch raw and alpha return for ticker over holding_days from trade_date.

        Returns (raw_return, alpha_return, actual_holding_days) or
        (None, None, None) if price data is unavailable (too recent, delisted,
        or network error).
        """
        try:
            start = datetime.strptime(trade_date, "%Y-%m-%d")
            end = start + timedelta(days=holding_days + 7)  # buffer for weekends/holidays
            end_str = end.strftime("%Y-%m-%d")

            stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
            spy = yf.Ticker("SPY").history(start=trade_date, end=end_str)

            if len(stock) < 2 or len(spy) < 2:
                return None, None, None

            actual_days = min(holding_days, len(stock) - 1, len(spy) - 1)
            raw = float(
                (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
                / stock["Close"].iloc[0]
            )
            spy_ret = float(
                (spy["Close"].iloc[actual_days] - spy["Close"].iloc[0])
                / spy["Close"].iloc[0]
            )
            alpha = raw - spy_ret
            return raw, alpha, actual_days
        except Exception as e:
            logger.warning(
                "Could not resolve outcome for %s on %s (will retry next run): %s",
                ticker, trade_date, e,
            )
            return None, None, None

    def _resolve_pending_entries(self, ticker: str) -> None:
        """Resolve pending log entries for ticker at the start of a new run.

        Fetches returns for each same-ticker pending entry, generates reflections,
        then writes all updates in a single atomic batch write to avoid redundant I/O.
        Skips entries whose price data is not yet available (too recent or delisted).

        Trade-off: only same-ticker entries are resolved per run.  Entries for
        other tickers accumulate until that ticker is run again.
        """
        pending = [e for e in self.memory_log.get_pending_entries() if e["ticker"] == ticker]
        if not pending:
            return

        updates = []
        for entry in pending:
            raw, alpha, days = self._fetch_returns(ticker, entry["date"])
            if raw is None:
                continue  # price not available yet — try again next run
            reflection = self.reflector.reflect_on_final_decision(
                final_decision=entry.get("decision", ""),
                raw_return=raw,
                alpha_return=alpha,
            )
            updates.append({
                "ticker": ticker,
                "trade_date": entry["date"],
                "raw_return": raw,
                "alpha_return": alpha,
                "holding_days": days,
                "reflection": reflection,
            })

        if updates:
            self.memory_log.batch_update_with_outcomes(updates)

    def propagate(self, company_name: str, trade_date: str) -> Tuple[Dict[str, Any], str]:
        """Run the trading agents graph for a company on a specific date.

        When ``checkpoint_enabled`` is set in config, the graph is recompiled
        with a per-ticker SqliteSaver so a crashed run can resume from the last
        successful node on a subsequent invocation with the same ticker+date.

        Args:
            company_name: The ticker symbol (e.g., 'AAPL')
            trade_date: The date string in YYYY-MM-DD format

        Returns:
            Tuple of (final_state_dict, decision_string)

        Raises:
            ValueError: If company_name or trade_date are invalid
            RuntimeError: If the graph execution fails
        """
        if not company_name or not isinstance(company_name, str):
            raise ValueError(f"Invalid company_name: {company_name}")

        if not trade_date or not isinstance(trade_date, str):
            raise ValueError(f"Invalid trade_date: {trade_date}")

        # Validate date format
        try:
            datetime.fromisoformat(trade_date)
        except ValueError as e:
            raise ValueError(f"Invalid date format for trade_date '{trade_date}': {e}")

        logger.info("Starting propagation for %s on %s", company_name, trade_date)
        self.ticker = company_name

        try:
            with timed_operation("propagate"):
                # Resolve any pending memory-log entries for this ticker before the pipeline runs.
                self._resolve_pending_entries(company_name)

                # Recompile with a checkpointer if the user opted in.
                if self.config.get("checkpoint_enabled"):
                    self._checkpointer_ctx = get_checkpointer(
                        self.config["data_cache_dir"], company_name
                    )
                    saver = self._checkpointer_ctx.__enter__()
                    self.graph = self.workflow.compile(checkpointer=saver)

                    step = checkpoint_step(
                        self.config["data_cache_dir"], company_name, str(trade_date)
                    )
                    if step is not None:
                        logger.info(
                            "Resuming from step %d for %s on %s", step, company_name, trade_date
                        )
                    else:
                        logger.info("Starting fresh for %s on %s", company_name, trade_date)

                result = self._run_graph(company_name, trade_date)

            self.metrics.increment_counter("propagate_success")
            logger.info("Successfully completed propagation for %s on %s", company_name, trade_date)
            return result

        except Exception as e:
            self.metrics.increment_counter("propagate_failure")
            logger.error("Failed to propagate for %s on %s: %s", company_name, trade_date, str(e))
            raise RuntimeError(f"Propagation failed for {company_name} on {trade_date}: {e}") from e
        finally:
            if self._checkpointer_ctx is not None:
                self._checkpointer_ctx.__exit__(None, None, None)
                self._checkpointer_ctx = None
                self.graph = self.workflow.compile()

    def _run_graph(self, company_name: str, trade_date: str) -> Tuple[Dict[str, Any], str]:
        """Execute the graph and write the resulting state to disk and memory log."""
        can_trade, drawdown_reason = TradingAgentsGraph._safe_status(
            getattr(self, "drawdown_monitor", None),
            "should_keep_trading",
            default=(True, "Drawdown monitor unavailable"),
        )
        if not can_trade:
            final_state = self._build_no_trade_state(company_name, trade_date, drawdown_reason)
            self.curr_state = final_state
            return final_state, "Hold"

        # Initialize state — inject memory log context for PM.
        past_context = self.memory_log.get_past_context(company_name)
        portfolio_context = self._build_portfolio_context(company_name)
        execution_context = self._build_execution_context(company_name, str(trade_date))
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            past_context=past_context,
            portfolio_context=portfolio_context,
            execution_context=execution_context,
        )
        args = self.propagator.get_graph_args()

        # Inject thread_id so same ticker+date resumes, different date starts fresh.
        if self.config.get("checkpoint_enabled"):
            tid = thread_id(company_name, str(trade_date))
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = tid

        if self.debug:
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if chunk.get("messages"):
                    chunk["messages"][-1].pretty_print()
                trace.append(chunk)
            final_state = trace[-1]
        else:
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection.
        self.curr_state = final_state

        # Log state to disk.
        self._log_state(trade_date, final_state)

        # Store decision for deferred reflection on the next same-ticker run.
        self.memory_log.store_decision(
            ticker=company_name,
            trade_date=trade_date,
            final_trade_decision=final_state["final_trade_decision"],
        )
        decision = self.process_signal(final_state["final_trade_decision"])
        if self.config.get("paper_trading_enabled", True):
            self.portfolio_state.record_paper_decision(
                ticker=company_name,
                decision=final_state["final_trade_decision"],
                rating=decision,
                trade_date=str(trade_date),
            )

        # Clear checkpoint on successful completion to avoid stale state.
        if self.config.get("checkpoint_enabled"):
            clear_checkpoint(
                self.config["data_cache_dir"], company_name, str(trade_date)
            )

        return final_state, decision

    def _build_portfolio_context(self, ticker: str) -> str:
        try:
            return self.portfolio_state.context_for(ticker)
        except Exception as exc:
            return f"Portfolio context unavailable: {exc}"

    def _build_execution_context(self, ticker: str, trade_date: str = "") -> str:
        parts = []
        can_trade, drawdown_reason = TradingAgentsGraph._safe_status(
            getattr(self, "drawdown_monitor", None),
            "should_keep_trading",
            default=(True, "Drawdown monitor unavailable"),
        )
        parts.append(f"Drawdown circuit breaker: {'PASS' if can_trade else 'BLOCK'} - {drawdown_reason}")

        corr_ok, corr_reason = TradingAgentsGraph._safe_status(
            getattr(self, "correlation_analyzer", None),
            "check_concentration_risk",
            getattr(self, "portfolio_state", None),
            ticker,
            default=(True, "Correlation analyzer unavailable"),
        )
        parts.append(f"Correlation/concentration check: {'PASS' if corr_ok else 'BLOCK'} - {corr_reason}")

        event_ok, event_reason = TradingAgentsGraph._safe_status(
            getattr(self, "news_impact_filter", None),
            "should_trade_this_stock",
            ticker,
            default=(True, "News impact filter unavailable"),
        )
        parts.append(f"High-impact event filter: {'PASS' if event_ok else 'CAUTION'} - {event_reason}")

        try:
            regime = get_market_regime.invoke({})
        except Exception as exc:
            regime = f"UNKNOWN ({exc})"
        try:
            vix = get_vix_regime.invoke({})
        except Exception as exc:
            vix = f"UNKNOWN ({exc})"
        parts.append(f"Market regime: {regime}")
        parts.append(f"Volatility regime: {vix}")
        parts.append(f"Execution time window: {self.execution_scheduler.get_execution_time_window()}")
        context = "## Execution and Risk Context\n" + "\n".join(f"- {part}" for part in parts)

        # Append RL signal block if a trained checkpoint is available
        if self.rl_signal_provider and self.rl_signal_provider.is_available():
            try:
                rl_context = self.rl_signal_provider.get_signal_context(ticker, trade_date)
                if rl_context:
                    context = context + "\n\n" + rl_context
            except Exception as exc:
                logger.debug("RL signal generation failed (non-fatal): %s", exc)

        return context

    @staticmethod
    def _safe_status(owner: Any, method_name: str, *args: Any, default: Tuple[bool, str]) -> Tuple[bool, str]:
        method = getattr(owner, method_name, None)
        if method is None or not callable(method):
            return default
        try:
            result = method(*args)
        except Exception as exc:
            return default[0], f"{default[1]} ({exc})"
        if not isinstance(result, tuple) or len(result) != 2:
            return default
        return bool(result[0]), str(result[1])

    def _build_no_trade_state(self, ticker: str, trade_date: str, reason: str) -> Dict[str, Any]:
        decision = (
            "Rating: Hold\n\n"
            "**Action**: NO_TRADE\n\n"
            f"**Reason**: {reason}\n\n"
            "The paper-trading circuit breaker blocked new analysis for this run."
        )
        empty_debate = {
            "bull_history": "",
            "bear_history": "",
            "history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        }
        empty_risk = {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
            "latest_speaker": "Judge",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": decision,
            "count": 0,
        }
        return {
            "messages": [],
            "company_of_interest": ticker,
            "trade_date": str(trade_date),
            "past_context": self.memory_log.get_past_context(ticker),
            "portfolio_context": self._build_portfolio_context(ticker),
            "execution_context": f"Drawdown circuit breaker: BLOCK - {reason}",
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_debate_state": empty_debate,
            "investment_plan": "",
            "trader_investment_plan": "",
            "risk_debate_state": empty_risk,
            "final_trade_decision": decision,
        }

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file
        directory = Path(self.config["results_dir"]) / self.ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
