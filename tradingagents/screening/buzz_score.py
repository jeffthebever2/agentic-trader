"""Sentiment-weighted BUZZ scoring — pure, deterministic, unit-testable.

The old buzz was *attention*: a sum of weighted mention counts. A stock getting
thousands of "dump it / taking profits" posts scored the same as one getting
thousands of "loading up / strong guidance" posts. This module makes buzz reflect
**market conviction, not volume**:

  buzz = base_volume + bull_contribution - bear_contribution + neutral_contribution

where each contribution is derived from the per-scan social-intent tally (already
classified by ``tweet_intent`` — lexicon for the clear posts, free-AI for the
ambiguous ones). Bullish conviction RAISES buzz; bearish conviction (sell calls,
profit-taking, dilution, lowered guidance, failed catalysts, downgrades) LOWERS it;
neutral/holding/news has only a small, capped effect; raw discussion volume on its
own is capped so attention alone can't pump a name.

Everything here is pure: it takes numbers in and returns a ``BuzzBreakdown`` out,
so the scorer can log every component and tests can assert each behaviour.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, asdict
from typing import Optional


# ── Tunable weights (env-overridable, mirrors the SIZER_* / THEMATIC_* pattern) ──
@dataclass(frozen=True)
class BuzzConfig:
    # Per-mention COUNT weights (how much one classified mention adds to the volume
    # layer). Bullish action counts full; neutral/holding/news count little so that
    # raw attention alone cannot inflate buzz. Sellers are excluded entirely (0).
    w_buy: float = 1.0
    w_bull_comment: float = 0.85    # bullish sentiment, no explicit buy action
    w_watch: float = 0.7
    w_hold: float = 0.6
    w_news: float = 0.7
    w_unclear: float = 0.55
    w_sell: float = 0.0             # sellers never pad the volume layer

    # Modulation scales applied to the accumulated conviction-weights.
    k_bull: float = 5.0             # bullish bonus (light — count layer already rewards buys)
    k_bear: float = 8.0             # bearish PENALTY (still > bull so bearish lowers buzz, but not crushing)
    k_neutral: float = 0.0          # neutral adds nothing to the delta (kept for logging symmetry)

    # Attention layer: volume contribution is logarithmic and hard-capped, so a name
    # with thousands of mentions gets only marginally more "volume" than one with tens.
    vol_cap: float = 15.0
    k_vol: float = 4.0

    # A net-bearish name (more bearish than bullish conviction) is flagged avoid.
    neg_avoid: float = -0.20

    @staticmethod
    def from_env() -> "BuzzConfig":
        g = os.getenv
        def f(name: str, d: float) -> float:
            try:
                return float(g(name, str(d)))
            except (TypeError, ValueError):
                return d
        return BuzzConfig(
            w_buy=f("BUZZ_W_BUY", 1.0),
            w_bull_comment=f("BUZZ_W_BULL_COMMENT", 0.85),
            w_watch=f("BUZZ_W_WATCH", 0.7),
            w_hold=f("BUZZ_W_HOLD", 0.6),
            w_news=f("BUZZ_W_NEWS", 0.7),
            w_unclear=f("BUZZ_W_UNCLEAR", 0.55),
            w_sell=f("BUZZ_W_SELL", 0.0),
            k_bull=f("BUZZ_K_BULL", 5.0),
            k_bear=f("BUZZ_K_BEAR", 8.0),
            k_neutral=f("BUZZ_K_NEUTRAL", 0.0),
            vol_cap=f("BUZZ_VOL_CAP", 15.0),
            k_vol=f("BUZZ_K_VOL", 4.0),
            neg_avoid=f("BUZZ_NEG_AVOID", -0.20),
        )


DEFAULT = BuzzConfig()
_EPS = 1e-6


@dataclass
class BuzzBreakdown:
    """Decomposed buzz for one ticker — every field is logged + persisted."""
    bull: float            # bullish contribution (added to buzz)
    bear: float            # bearish contribution (magnitude; SUBTRACTED from buzz)
    neutral: float         # neutral/low-conviction contribution (small, added)
    volume: float          # attention/volume contribution (capped)
    base: float            # the pre-modulation source-volume score
    delta: float           # net sentiment delta applied = bull - bear + neutral
    buzz: float            # final buzz = max(0, base + delta)
    bull_bear_ratio: float
    net_sentiment: float   # [-1, +1] — feeds composite_score's sentiment term
    n_bull: int
    n_bear: int
    n_neutral: int
    n_total: int
    avoid: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}


def count_weight(res, *, cfg: BuzzConfig = DEFAULT) -> float:
    """Conviction weight of ONE classified mention toward the VOLUME layer (>= 0).

    Bullish action = full weight; neutral/holding/news = small; sellers = 0. This is
    what stops raw attention (lots of neutral chatter) from inflating buzz.
    ``res`` is a tweet_intent.IntentResult (duck-typed: label/sentiment/conf/flags).
    """
    from tradingagents.screening import tweet_intent as ti
    if getattr(res, "reduce_buy", False):
        return cfg.w_sell
    label = getattr(res, "label", ti.UNCLEAR)
    sent = float(getattr(res, "sentiment", 0.0) or 0.0)
    if getattr(res, "increase_buy", False) or label == ti.BUY_SIGNAL:
        return cfg.w_buy
    if label == ti.WATCHLIST_ONLY:
        # bullish-comment (sent > 0) ranks above a pure "on my watchlist"
        return cfg.w_bull_comment if sent > 0.15 else cfg.w_watch
    if label == ti.HOLD_SIGNAL:
        return cfg.w_hold
    if label == ti.NEWS_ONLY:
        # a bullish-fundamental news item (strong earnings / raised guidance) counts
        # a touch more than neutral reportage.
        return max(cfg.w_news, cfg.w_bull_comment) if sent > 0.3 else cfg.w_news
    return cfg.w_unclear  # UNCLEAR / mixed / could-go-either-way → minimal


def contribution(res, *, weight: float = 1.0) -> tuple[float, float, float]:
    """Signed conviction contribution of ONE mention → (bull_w, bear_w, neut_w).

    Magnitude scales with the classifier's confidence × the source weight × the
    sentiment strength, so a high-confidence "dumping, dilution incoming" subtracts
    far more than a low-confidence "eh, could go either way" (which is ~neutral).
    Exactly one of the three is non-zero.
    """
    sent = float(getattr(res, "sentiment", 0.0) or 0.0)
    conf = float(getattr(res, "confidence", 0.0) or 0.0)
    reduce_buy = bool(getattr(res, "reduce_buy", False))
    increase_buy = bool(getattr(res, "increase_buy", False))
    m = max(0.0, conf) * max(0.0, weight)

    if reduce_buy or sent <= -0.15:
        return (0.0, m * max(0.20, -sent), 0.0)      # bearish
    if increase_buy or sent >= 0.15:
        return (m * max(0.20, sent), 0.0, 0.0)        # bullish
    return (0.0, 0.0, m * 0.5)                         # neutral / mixed → limited


def compute_buzz(base: float, tally: dict, *, cfg: BuzzConfig = DEFAULT) -> BuzzBreakdown:
    """Combine the source-volume ``base`` with the per-ticker social-intent ``tally``
    into a sentiment-weighted buzz + full decomposition.

    ``tally`` keys (accumulated by the scorer as posts are classified):
        bull_w, bear_w, neut_w : float   conviction-weighted sentiment sums
        buy, sell, hold, watch, news, unclear : int   mention counts (for ratio/vol)
    """
    base = max(0.0, float(base or 0.0))
    bull_w = max(0.0, float(tally.get("bull_w", 0.0) or 0.0))
    bear_w = max(0.0, float(tally.get("bear_w", 0.0) or 0.0))
    neut_w = max(0.0, float(tally.get("neut_w", 0.0) or 0.0))

    n_bull = int(tally.get("buy", 0) or 0)
    n_bear = int(tally.get("sell", 0) or 0)
    n_neutral = int(tally.get("hold", 0) or 0) + int(tally.get("watch", 0) or 0) \
        + int(tally.get("news", 0) or 0) + int(tally.get("unclear", 0) or 0)
    n_total = n_bull + n_bear + n_neutral

    bull = cfg.k_bull * bull_w
    bear = cfg.k_bear * bear_w
    neutral = cfg.k_neutral * neut_w
    volume = min(cfg.vol_cap, cfg.k_vol * math.log1p(max(0, n_total)))

    delta = bull - bear + neutral
    buzz = max(0.0, base + delta)

    denom = bull_w + bear_w + neut_w + _EPS
    net_sentiment = max(-1.0, min(1.0, (bull_w - bear_w) / denom))
    bull_bear_ratio = min(999.0, (bull_w + _EPS) / (bear_w + _EPS))  # cap so all-bull doesn't log as ~millions
    avoid = net_sentiment <= cfg.neg_avoid and bear_w > 0.0

    return BuzzBreakdown(
        bull=bull, bear=bear, neutral=neutral, volume=volume, base=base,
        delta=delta, buzz=buzz, bull_bear_ratio=bull_bear_ratio,
        net_sentiment=net_sentiment, n_bull=n_bull, n_bear=n_bear,
        n_neutral=n_neutral, n_total=n_total, avoid=avoid,
    )


def blend_sentiment(llm: Optional[float], social: Optional[float]) -> float:
    """Blend the LLM's per-pick sentiment with the crowd's social sentiment for the
    composite score. Bearish consensus is allowed to DRAG the result down (take the
    min) so a "everyone's selling" crowd can trip the composite's hard bearish cap,
    while in the normal case the two are averaged."""
    l = None if llm is None else max(-1.0, min(1.0, float(llm)))
    s = None if social is None else max(-1.0, min(1.0, float(social)))
    if s is None:
        return 0.0 if l is None else l
    if l is None:
        return s
    blended = 0.5 * l + 0.5 * s
    if s <= -0.3:                      # bearish crowd → conservative, don't average it away
        blended = min(blended, s)
    return max(-1.0, min(1.0, blended))
