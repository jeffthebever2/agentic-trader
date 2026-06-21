"""Tweet / social-post INTENT classifier — run BEFORE scoring.

A ticker *mention* is not a buy. "Selling $NVDA, taking profits" must not add buy
conviction the way "Started a $NVDA position, adding on dips" does. This module
reads the author's actual action, not just cashtags or hype words, and labels each
post (and each ticker within it) so the scorer can count only genuine buy intent
and penalize / avoid sell intent.

Pure + deterministic (lexicon-based, no network, no LLM) so it can run on every
scraped item cheaply and be unit-tested offline. The downstream LLM (`_ai_pick`)
still does deeper analysis on the finalists; this is the upstream gate the spec
asks for.

Labels:
    BUY_SIGNAL      first-person bullish ACTION (bought / adding / long / buy dip)
    SELL_SIGNAL     first-person bearish ACTION or a downside warning
                    (sold / trimming / took profits / exiting / "overextended")
    HOLD_SIGNAL     still holding / no change
    WATCHLIST_ONLY  watching / waiting for confirmation / on watch
    NEWS_ONLY       third-person news / discussion, no author action
    UNCLEAR         a ticker is present but no intent can be read

Mixed-tweet rule: a bearish ACTION beats bullish sentiment. "I love $XYZ long term
but I'm trimming here" → SELL_SIGNAL, because the action is selling.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

# ── Cue lexicons (whole-word/phrase, case-insensitive) ──────────────────────────
# ACTIONS are first-person, decisive moves; they outrank mere sentiment adjectives.
_BUY_ACTIONS = [
    r"\bbought\b", r"\bbuying\b", r"\bi (?:just )?bought\b", r"\badding\b",
    r"\badded (?:to|more)\b", r"\baccumulat(?:e|ing|ed)\b", r"\bstarted a position\b",
    r"\bopened a position\b", r"\binitiated\b", r"\bstarter\b", r"\bloaded up\b",
    r"\bloading up\b", r"\bscooped\b", r"\bgrabbed\b", r"\baveraging (?:down|in)\b",
    r"\bgoing long\b", r"\bi'?m long\b", r"\bwent long\b", r"\blong\b(?![\s-]?term)",
    r"\bbuy the dip\b", r"\bbuying the dip\b", r"\bbtfd\b", r"\bbuy dips\b",
    r"\bback(?:ing)? up the truck\b", r"\ball in\b", r"\bdca(?:ing)?\b",
]
_BUY_SENTIMENT = [
    r"\bbullish\b", r"\bconviction\b", r"\bundervalued\b", r"\bbreakout\b",
    r"\battractive\b", r"\bcheap\b", r"\bgreat setup\b", r"\bstrong buy\b",
    r"\bmust own\b", r"\bgenerational\b", r"\bcoiled\b", r"\bbottom(?:ed|ing)?\b",
    r"\bripping\b", r"\bsend it\b", r"\blov(?:e|ing|ed)\b",
]
# SELL actions + downside warnings (the spec lumps "warning others" with sell).
_SELL_ACTIONS = [
    r"\bsold\b", r"\bselling\b", r"\bi (?:just )?sold\b", r"\btrim(?:med|ming)?\b",
    r"\btook profits?\b", r"\btaking profits?\b", r"\bprofit took\b", r"\bexit(?:ed|ing)?\b",
    r"\bclosed (?:my |the )?position\b", r"\bclosing\b", r"\bcut(?:ting)? (?:my )?(?:losses|position)\b",
    r"\breduc(?:e|ing|ed) (?:my )?(?:exposure|position)\b", r"\blighten(?:ed|ing)? up\b",
    r"\bdumped\b", r"\bdumping\b", r"\bbailed\b", r"\bsold out\b", r"\bgot out\b",
    r"\boffload(?:ed|ing)?\b", r"\bdistribut(?:e|ing)\b", r"\bunload(?:ed|ing)?\b",
]
_SELL_SENTIMENT = [
    r"\bbearish\b", r"\boverextended\b", r"\bover-?bought\b", r"\bparabolic\b",
    r"\btop is in\b", r"\btopped\b", r"\bblow-?off\b", r"\bexhaust(?:ed|ion)\b",
    r"\bno longer (?:like|hold|own|bullish)\b", r"\bavoid\b", r"\bwarning\b",
    r"\bbe careful\b", r"\bbubble\b", r"\bcrash(?:ing)?\b", r"\bdump\b", r"\bfade\b",
    r"\brug\b", r"\bdead cat\b", r"\bshort(?:ing)?\b", r"\bputs\b", r"\bdowngrade\b",
    r"\bovervalued\b", r"\brich\b", r"\bextended here\b", r"\btaking it off\b",
]
_HOLD_CUES = [
    r"\bstill holding\b", r"\bholding\b", r"\bhold(?:ing)? (?:my )?(?:shares|position)\b",
    r"\bhodl\b", r"\bdiamond hands\b", r"\bnot selling\b", r"\bsitting (?:on|tight)\b",
    r"\briding it\b", r"\bletting it ride\b", r"\bunchanged\b",
]
_WATCH_CUES = [
    r"\bwatch(?:ing|list)?\b", r"\bon (?:my )?watch\b", r"\bwaiting for\b",
    r"\bneed(?:s)? confirmation\b", r"\bkeep(?:ing)? an eye\b", r"\bey(?:e|ing)\b",
    r"\bif it (?:breaks|holds|reclaims)\b", r"\bwould (?:buy|add) (?:if|below|on)\b",
    r"\bsetup forming\b", r"\bstalking\b",
]
# Third-person news verbs (no first-person action) → NEWS_ONLY.
_NEWS_CUES = [
    r"\breport(?:s|ed|ing)?\b", r"\bannounc(?:e|es|ed|ement)\b", r"\bearnings\b",
    r"\bguidance\b", r"\bupgrade(?:s|d)?\b", r"\binitiat(?:es|ed) coverage\b",
    r"\bprice target\b", r"\bfda\b", r"\bapproval\b", r"\bfiles?\b", r"\bsec\b",
    r"\blawsuit\b", r"\bmerger\b", r"\bacquir(?:e|es|ed|ing)\b", r"\bpartnership\b",
    r"\bcontract\b", r"\bunveil(?:s|ed)\b", r"\blaunch(?:es|ed)\b", r"\bbeats?\b",
    r"\bmisses\b", r"\bquarterly\b", r"\brevenue\b",
]
# Hard bearish FUNDAMENTALS = active selling pressure (NOT already covered by the
# sell-sentiment list). Dilution, missed earnings, cut guidance, insider selling,
# failed catalysts, distress. These reduce buy conviction even amid hype.
_BEAR_FUNDAMENTAL = [
    r"\bdilut(?:e|es|ed|ing|ion|ive)\b", r"\b(?:secondary|share|stock|public|atm)\s+offering\b",
    r"\bpriced\s+offering\b", r"\brais(?:e|es|ing)\s+capital\b", r"\bcapital\s+raise\b",
    r"\bearnings\s+miss\b", r"\bmissed\s+(?:earnings|estimates|revenue|the\s+quarter)\b",
    r"\brevenue\s+miss\b", r"\bprofit\s+warning\b",
    r"\b(?:cut|cuts|cutting|lowered|lower|slashed|reduced|weak|soft)\s+guidance\b",
    r"\bguidance\s+(?:cut|miss|lowered)\b", r"\binsider\s+(?:selling|sales|sold)\b",
    r"\binsiders\s+(?:selling|sold|dumping)\b", r"\b(?:ceo|cfo|executives?)\s+sold\b",
    r"\bcomplete\s+response\s+letter\b", r"\bcrl\b", r"\btrial\s+fail(?:ed|ure)?\b",
    r"\bfailed\s+(?:trial|study|phase|catalyst)\b", r"\b(?:study|phase\s*\d)\s+fail(?:ed|ure)?\b",
    r"\brecall(?:ed|s)?\b", r"\bgoing\s+concern\b", r"\baccounting\s+(?:fraud|issues?)\b",
    r"\bsec\s+(?:probe|investigation|subpoena)\b", r"\bdelist(?:ed|ing)\b",
    r"\bbankrupt(?:cy)?\b", r"\bchapter\s*11\b", r"\bhalted\b",
]
# Bullish FUNDAMENTALS (specific — NOT bare "earnings"/"guidance", which stay NEWS).
_BULL_FUNDAMENTAL = [
    r"\b(?:rais(?:e|es|ed|ing)|hik(?:e|ed|ing)|boost(?:ed)?)\s+guidance\b",
    r"\bguidance\s+(?:raise|raised|hike)\b", r"\bbeat[\s-]and[\s-]rais(?:e|ed)\b",
    r"\bstrong\s+earnings\b", r"\bblow[\s-]?out\b", r"\bcrushed\s+(?:earnings|estimates|it)\b",
    r"\b(?:beat|smashed|topped)\s+(?:estimates|expectations|on\s+(?:top|both))\b",
    r"\bearnings\s+beat\b", r"\brecord\s+(?:revenue|earnings|quarter|bookings|backlog)\b",
    r"\bupgrad(?:e|ed)\s+to\s+buy\b", r"\b(?:rais(?:e|ed)|hik(?:e|ed))\s+price\s+target\b",
    r"\bprice\s+target\s+rais(?:e|ed)\b", r"\bawarded\s+(?:a\s+)?contract\b",
    r"\bcontract\s+win\b", r"\bfda\s+approv(?:al|ed)\b", r"\bapproved\s+by\s+the\s+fda\b",
]
# Soft bearish stance: "wouldn't buy here / wouldn't chase" → reduce buy lean.
_WONT_BUY = [
    r"\bwould\s*n'?t\s+(?:buy|add|chase|touch|get\s+in)\b", r"\bwill\s*not\s+buy\b",
    r"\bnot\s+(?:a\s+buy|buying)\s+(?:here|now|yet)\b", r"\bnot\s+a\s+buy\b",
    r"\bno\s+longer\s+buying\b", r"\bpass(?:ing)?\s+on\s+(?:this|it)\b",
    r"\bi'?d\s+(?:avoid|stay\s+away)\b", r"\bstay\s+away\b",
]
# Valuation concern → SLIGHTLY bearish (limited impact). New phrases not in sell-sent.
_VAL_CONCERN = [
    r"\bvaluation\s+(?:is\s+)?(?:too\s+)?(?:high|stretched|rich|expensive|extended|demanding|insane|nuts)\b",
    r"\bover[\s-]?valued\b", r"\btoo\s+expensive\b", r"\bpriced\s+for\s+perfection\b",
    r"\bexpensive\s+(?:here|now)\b", r"\brich(?:ly)?\s+valued\b", r"\bvaluation\s+concerns?\b",
    r"\bfrothy\s+valuation\b", r"\bpriced\s+in\b",
]
# Soft bearish hedges (mild) — used for mixed detection + lone slight-bear.
_SOFT_BEAR = [
    r"\bconcern(?:s|ed)?\b", r"\bheadwinds?\b", r"\bcautious(?:ly)?\b", r"\buncertain(?:ty)?\b",
    r"\brisk(?:s|y)?\b", r"\bworri(?:ed|some)\b", r"\bskeptic(?:al)?\b", r"\bsoftness\b",
    r"\bslow(?:ing|down)\b", r"\bnear[\s-]?term\s+(?:weakness|pressure|concern|softness)\b",
]
# Soft bullish words (mild) — ONLY for mixed detection (do NOT trigger a buy alone).
_SOFT_BULL = [
    r"\bstrong(?:er)?\b", r"\brobust\b", r"\bsolid\b", r"\brecord\b", r"\bgrowth\b",
    r"\bupside\b", r"\blong[\s-]?term\s+(?:outlook|story|growth|thesis|winner)\b",
    r"\bsecular\b", r"\bdurable\b", r"\bhealthy\b", r"\bimproving\b", r"\bmomentum\b",
]
_EITHER_WAY = [
    r"\bcould\s+go\s+either\s+way\b", r"\beither\s+direction\b", r"\bcoin\s*flip\b",
    r"\b50[\s/-]*50\b", r"\btoss[\s-]?up\b", r"\bhard\s+to\s+say\b", r"\bno\s+edge\b",
    r"\bunclear\s+direction\b",
]
_CONTRAST = [
    r"\bbut\b", r"\bhowever\b", r"\bthough\b", r"\balthough\b", r"\byet\b",
    r"\bwhile\b", r"\bthat\s+said\b", r"\bon\s+the\s+other\s+hand\b",
]


def _compile(patterns: List[str]) -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# Negated sell ("not selling", "won't trim", "no plans to exit") is NOT a sell —
# it's a hold. Strip these spans before sell detection so they don't false-trigger.
_NEG_SELL = re.compile(
    r"\b(?:not|never|won'?t|wont|do(?:es)?n'?t|did'?nt|didn'?t|ain'?t|"
    r"no(?:t)?\s+(?:plan|intention)s?\s+(?:to|on)|no\s+way\s+i'?m)\s+\w*\s*"
    r"(?:sell(?:ing)?|trim(?:ming)?|exit(?:ing)?|dump(?:ing)?|sold|reduc\w*)\b",
    re.IGNORECASE,
)

# Phrases that LOOK like selling but aren't the author exiting — "sold out"
# (capacity gone = strong demand, bullish), "selling out" (same), "profit-taking
# concerns" as a topic. Stripped before sell detection so they don't false-flag.
# "sold/selling out" is bullish (capacity/demand) UNLESS it's "sold out OF/MY/ALL
# /HERE …" which means the author exited the position — keep that as a real sell.
_FALSE_SELL = re.compile(
    r"\b(?:sold|sell(?:s|ing)?)[\s-]?out\b(?!\s+(?:of|my|all|everything|here|the\s+position))"
    r"|\bselling\s+like\s+hotcakes\b|\bflying\s+off\s+(?:the\s+)?shelves\b",
    re.IGNORECASE,
)


_RX = {
    "buy_action": _compile(_BUY_ACTIONS),
    "buy_sent": _compile(_BUY_SENTIMENT),
    "sell_action": _compile(_SELL_ACTIONS),
    "sell_sent": _compile(_SELL_SENTIMENT),
    "hold": _compile(_HOLD_CUES),
    "watch": _compile(_WATCH_CUES),
    "news": _compile(_NEWS_CUES),
    "bear_fund": _compile(_BEAR_FUNDAMENTAL),
    "bull_fund": _compile(_BULL_FUNDAMENTAL),
    "wont_buy": _compile(_WONT_BUY),
    "val_concern": _compile(_VAL_CONCERN),
    "soft_bear": _compile(_SOFT_BEAR),
    "soft_bull": _compile(_SOFT_BULL),
    "either_way": _compile(_EITHER_WAY),
    "contrast": _compile(_CONTRAST),
}

# Labels
BUY_SIGNAL = "BUY_SIGNAL"
SELL_SIGNAL = "SELL_SIGNAL"
HOLD_SIGNAL = "HOLD_SIGNAL"
WATCHLIST_ONLY = "WATCHLIST_ONLY"
NEWS_ONLY = "NEWS_ONLY"
UNCLEAR = "UNCLEAR"


@dataclass
class IntentResult:
    ticker: Optional[str]
    label: str                 # one of the six labels
    action: str                # short human action, e.g. "trimming", "adding"
    sentiment: float           # -1.0 (bearish) .. +1.0 (bullish)
    confidence: float          # 0.0 .. 1.0
    reason: str
    increase_buy: bool         # may this raise buy conviction?
    reduce_buy: bool           # should this reduce / block buy conviction?

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "label": self.label,
            "action": self.action,
            "sentiment": round(self.sentiment, 3),
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "increase_buy": self.increase_buy,
            "reduce_buy": self.reduce_buy,
        }


def _count(patterns: List[re.Pattern], text: str) -> List[str]:
    hits: List[str] = []
    for rx in patterns:
        m = rx.search(text)
        if m:
            hits.append(m.group(0).strip())
    return hits


def classify_intent(text: str, ticker: Optional[str] = None) -> IntentResult:
    """Classify one post's intent. ``ticker`` is optional context for the reason.

    Decision order (mixed tweets resolved here):
      1. A bearish ACTION (sold/trimming/took profits/exiting/reducing) → SELL,
         even alongside bullish words ("love it but trimming" → SELL).
      2. A downside WARNING (overextended / no longer like / avoid) → SELL.
      3. A bullish ACTION (bought/adding/long/buy the dip) → BUY.
      4. HOLD cue → HOLD. WATCH cue → WATCHLIST. NEWS verb only → NEWS_ONLY.
      5. Bullish/bearish SENTIMENT with no action → lean BUY/HOLD vs SELL warning.
      6. Nothing readable → UNCLEAR.
    """
    t = (text or "").strip()
    if not t:
        return IntentResult(ticker, UNCLEAR, "none", 0.0, 0.0, "empty text", False, False)
    low = t.lower()
    # Neutralize negated-sell ("not selling") AND false-sell ("sold out capacity")
    # phrases before sell detection so they don't read as the author selling.
    low_sell = _NEG_SELL.sub(" ", low)
    low_sell = _FALSE_SELL.sub(" ", low_sell)

    buy_act = _count(_RX["buy_action"], low)
    sell_act = _count(_RX["sell_action"], low_sell)
    sell_sent = _count(_RX["sell_sent"], low_sell)
    buy_sent = _count(_RX["buy_sent"], low)
    hold = _count(_RX["hold"], low)
    watch = _count(_RX["watch"], low)
    news = _count(_RX["news"], low)
    # selling-pressure / fundamentals / valuation / mixed cues (sentiment-aware buzz)
    bear_fund = _count(_RX["bear_fund"], low_sell)
    bull_fund = _count(_RX["bull_fund"], low)
    wont_buy = _count(_RX["wont_buy"], low)
    val_concern = _count(_RX["val_concern"], low)
    soft_bear = _count(_RX["soft_bear"], low)
    soft_bull = _count(_RX["soft_bull"], low)
    either_way = _count(_RX["either_way"], low)
    contrast = _count(_RX["contrast"], low)

    def res(label, action, sent, conf, reason, inc, red):
        return IntentResult(ticker, label, action, max(-1.0, min(1.0, sent)),
                            max(0.0, min(1.0, conf)), reason, inc, red)

    # 1. Bearish ACTION wins over any bullish wording (mixed-tweet rule).
    if sell_act:
        conf = 0.7 + 0.1 * min(len(sell_act), 3)
        mixed = " (mixed: bullish words present but action is selling)" if (buy_act or buy_sent or bull_fund or soft_bull) else ""
        return res(SELL_SIGNAL, sell_act[0], -0.7, conf,
                   f"author action: {sell_act[0]}{mixed}", False, True)

    # 2. Hard bearish FUNDAMENTALS = selling pressure (dilution, earnings miss,
    #    lowered guidance, insider selling, failed catalyst). Reduce buy even amid hype.
    if bear_fund:
        conf = 0.6 + 0.1 * min(len(bear_fund), 3)
        return res(SELL_SIGNAL, "bearish-fundamental", -0.6, conf,
                   f"selling pressure: {bear_fund[0]}", False, True)

    # 3. Downside WARNING / hard bearish sentiment (overextended, bearish, crash).
    if sell_sent:
        conf = 0.55 + 0.1 * min(len(sell_sent), 3)
        return res(SELL_SIGNAL, "warning", -0.55, conf,
                   f"downside warning: {sell_sent[0]}", False, True)

    # 4. Explicitly two-sided ("could go either way") → neutral, no edge.
    if either_way:
        return res(UNCLEAR, "either-way", 0.0, 0.4,
                   "explicitly two-sided / no edge", False, False)

    # 5. MIXED: a contrast of bullish and (soft) bearish points with no decisive
    #    action — "near-term concerns but strong long-term outlook". Net ~0, low
    #    confidence → limited downstream impact.
    has_bull_cue = bool(buy_sent or bull_fund or soft_bull)
    has_soft_bear = bool(val_concern or soft_bear or wont_buy)
    if contrast and has_bull_cue and has_soft_bear and not buy_act:
        nb = len(buy_sent) + len(bull_fund) + len(soft_bull)
        ns = len(val_concern) + len(soft_bear) + len(wont_buy)
        net = max(-0.3, min(0.3, 0.2 * (nb - ns)))
        return res(UNCLEAR, "mixed", net, 0.35,
                   "mixed: bullish and bearish points balanced", False, False)

    # 6. "Holding but wouldn't buy here" / "wouldn't chase" → soft sell lean.
    if wont_buy:
        return res(SELL_SIGNAL, "wont-buy", -0.45, 0.6,
                   f"would not buy here: {wont_buy[0]}", False, True)

    # 7. Valuation concern alone → SLIGHTLY bearish (limited, does not hard-avoid).
    if val_concern:
        return res(WATCHLIST_ONLY, "valuation-concern", -0.3, 0.45,
                   f"valuation concern: {val_concern[0]}", False, False)

    # 8. Bullish ACTION → BUY.
    if buy_act:
        conf = 0.7 + 0.1 * min(len(buy_act) + len(buy_sent), 3)
        return res(BUY_SIGNAL, buy_act[0], 0.7, conf,
                   f"author action: {buy_act[0]}", True, False)

    # 9. Bullish FUNDAMENTAL (strong earnings, raised guidance, beat-and-raise) →
    #    bullish read even without an author action (does not auto-increase buy).
    if bull_fund:
        conf = 0.5 + 0.1 * min(len(bull_fund), 2)
        return res(WATCHLIST_ONLY, "bullish-fundamental", 0.55, conf,
                   f"bullish catalyst: {bull_fund[0]}", False, False)

    # 10. Watch beats hold beats news when the author states their own stance.
    if watch:
        return res(WATCHLIST_ONLY, "watching", 0.1, 0.55,
                   f"watching/waiting: {watch[0]}", False, False)
    if hold:
        return res(HOLD_SIGNAL, "holding", 0.0, 0.55,
                   f"holding, no change: {hold[0]}", False, False)

    # 11. Bullish sentiment with no action → weak buy lean (increase_buy stays False).
    if buy_sent and not news:
        return res(WATCHLIST_ONLY, "bullish-comment", 0.35, 0.45,
                   f"bullish sentiment, no action: {buy_sent[0]}", False, False)

    # 12. Soft bearish (concerns / cautious) with no bull side → slightly bearish.
    if soft_bear:
        return res(WATCHLIST_ONLY, "soft-bear", -0.25, 0.4,
                   f"caution: {soft_bear[0]}", False, False)

    # 13. Third-person news / discussion.
    if news:
        return res(NEWS_ONLY, "news", 0.0, 0.5, f"news/discussion: {news[0]}", False, False)

    return res(UNCLEAR, "none", 0.0, 0.2, "ticker mentioned, no readable intent", False, False)


# ── Per-ticker resolution for multi-ticker posts ────────────────────────────────
_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})\b")
# Above this many distinct tickers, a post is a list/thesis, not per-name calls.
_LIST_TWEET_TICKERS = 6


def aggregate_for_tickers(text: str, tickers: List[str], *, window: int = 70) -> Dict[str, IntentResult]:
    """Classify intent for EACH ticker in a multi-ticker post.

    For each ticker, classify the ±``window``-char neighbourhood around its
    mention(s); if that local window carries no decisive cue, fall back to the
    whole-text classification. Lets "buying $AAPL, dumping $TSLA" tag AAPL BUY and
    TSLA SELL. ``tickers`` are upper-case symbols already extracted by the caller.
    """
    text = text or ""
    uniq = sorted({t.upper() for t in tickers if t})
    if not uniq:
        return {}
    return _aggregate(text, uniq)


def _aggregate(text: str, uniq: List[str]) -> Dict[str, IntentResult]:
    out: Dict[str, IntentResult] = {}

    # SINGLE ticker → classify the WHOLE post, so the mixed-tweet rule holds
    # ("I love $XYZ but I'm trimming" → SELL even though the mention is bullish).
    if len(uniq) == 1:
        out[uniq[0]] = classify_intent(text, ticker=uniq[0])
        return out

    # LIST / THESIS tweet (many tickers) → a watchlist / value-chain list, not N
    # individual buy calls. Classify the WHOLE post once: a clear SELL/warning
    # propagates, but a bullish list is demoted to WATCHLIST_ONLY (no buy inflation).
    if len(uniq) > _LIST_TWEET_TICKERS:
        whole = classify_intent(text)
        for tk in uniq:
            if whole.label == SELL_SIGNAL:
                out[tk] = IntentResult(tk, SELL_SIGNAL, whole.action, whole.sentiment,
                                       whole.confidence * 0.8, f"list tweet, net-bearish: {whole.reason}",
                                       False, True)
            else:
                out[tk] = IntentResult(tk, WATCHLIST_ONLY, "list-mention", 0.15, 0.4,
                                       f"appears in a {len(uniq)}-ticker list/thesis — watchlist, not a per-name buy",
                                       False, False)
        return out

    # MULTIPLE tickers → attribute by CLAUSE so "buying $AAPL, dumping $TSLA"
    # tags each separately. Split on punctuation + contrast conjunctions.
    clauses = re.split(r"[,;.!?\n]+|\b(?:but|however|while|though|whereas|yet|and then)\b",
                       text, flags=re.IGNORECASE)
    clauses = [c.strip() for c in clauses if c and c.strip()]
    whole = classify_intent(text)
    for tk in uniq:
        mine = [c for c in clauses if re.search(rf"\$?{re.escape(tk.lower())}\b", c.lower())]
        local = " … ".join(mine) if mine else text
        r = classify_intent(local, ticker=tk)
        if r.label in (UNCLEAR, NEWS_ONLY) and whole.label != UNCLEAR:
            r = IntentResult(tk, whole.label, whole.action, whole.sentiment,
                             whole.confidence * 0.9, whole.reason, whole.increase_buy, whole.reduce_buy)
        out[tk] = r
    return out


_VALID_LABELS = {BUY_SIGNAL, SELL_SIGNAL, HOLD_SIGNAL, WATCHLIST_ONLY, NEWS_ONLY, UNCLEAR}


_LEXICON_TRUST = 0.7   # at/above this confidence the lexicon read is "explicit"


def reconcile_intents(lexicon: IntentResult, ai: Optional[IntentResult],
                      *, lexicon_trust: float = _LEXICON_TRUST) -> IntentResult:
    """Merge the fast lexicon read with an optional free-AI read.

    Two regimes:
      * IFFY lexicon (confidence < ``lexicon_trust``) — it was sent to the AI
        precisely because it was borderline, so the AI verdict WINS outright (no
        more iffy lexicon yes/no). This is the user's "send it to AI" preference.
      * EXPLICIT lexicon (confidence >= trust, e.g. an outright "sold my $NVDA") —
        conservative merge: a SELL from EITHER blocks the buy, and the AI can never
        flip an explicit sell into a buy.
    ``ai`` None → lexicon unchanged.
    """
    if ai is None or ai.label not in _VALID_LABELS:
        return lexicon
    # Borderline lexicon read → trust the AI (that's why we asked it).
    if lexicon.confidence < lexicon_trust:
        return IntentResult(
            ticker=lexicon.ticker or ai.ticker, label=ai.label, action=ai.action,
            sentiment=ai.sentiment, confidence=ai.confidence,
            reason=f"AI-decided ({ai.reason[:60]}); lexicon was iffy ({lexicon.label} {lexicon.confidence:.2f})",
            increase_buy=ai.increase_buy, reduce_buy=ai.reduce_buy,
        )
    # Explicit lexicon read → conservative merge.
    reduce_buy = bool(lexicon.reduce_buy or ai.reduce_buy)
    increase_buy = (not reduce_buy) and bool(lexicon.increase_buy or ai.increase_buy)
    if reduce_buy:
        label = SELL_SIGNAL
    elif increase_buy:
        label = BUY_SIGNAL
    else:
        # neither buy nor sell — take the more informative non-UNCLEAR read.
        cand = ai.label if ai.label != UNCLEAR else lexicon.label
        label = cand if cand in (HOLD_SIGNAL, WATCHLIST_ONLY, NEWS_ONLY) else UNCLEAR
    decisive = ai if ai.confidence >= lexicon.confidence else lexicon
    return IntentResult(
        ticker=lexicon.ticker or ai.ticker,
        label=label,
        action=decisive.action,
        sentiment=max(-1.0, min(1.0, (lexicon.sentiment + ai.sentiment) / 2.0)),
        confidence=max(lexicon.confidence, ai.confidence),
        reason=f"AI:{ai.label}({ai.reason[:50]}) | lex:{lexicon.label}",
        increase_buy=increase_buy,
        reduce_buy=reduce_buy,
    )
