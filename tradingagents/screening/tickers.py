"""Standard ticker lists for batch scanning.

STANDARD_TICKERS — ~600 liquid US equities drawn from S&P 500, NASDAQ 100,
and popular large/mid-cap names. Use with `tradingagents scan --list standard`.

Lists are intentionally static so the scanner works offline and without any
paid data feed. Update periodically to reflect index rebalances.
"""

from __future__ import annotations
from typing import List

# ── S&P 500 sector components ──────────────────────────────────────────────
_TECH = [
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "AMD", "CRM", "CSCO", "INTC",
    "QCOM", "TXN", "IBM", "MU", "AMAT", "LRCX", "KLAC", "ADI", "MCHP",
    "CDNS", "SNPS", "ANSS", "FTNT", "CTSH", "HPQ", "HPE", "JNPR", "NTAP",
    "STX", "WDC", "NOW", "WDAY", "ADBE", "PANW", "CRWD", "DDOG", "NET",
    "TEAM", "INTU", "PAYC", "ROP", "KEYS", "VRSN", "AKAM", "CDW", "SWKS",
    "QRVO", "MPWR", "MRVL", "ON", "SMCI", "FSLR", "ENPH", "GDDY", "EPAM",
    "OKTA", "GTLB", "HUBS", "ZS", "BILL", "PCOR", "AZPN", "APPN",
]

_HEALTHCARE = [
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "TMO", "ABT", "DHR", "BMY", "AMGN",
    "GILD", "ISRG", "VRTX", "REGN", "BIIB", "IDXX", "IQV", "ZBH", "BDX",
    "BAX", "EW", "HOLX", "DXCM", "PODD", "HUM", "CI", "CVS", "MCK", "ABC",
    "CAH", "MOH", "CNC", "HCA", "LH", "DGX", "ALGN", "MDT", "SYK", "BSX",
    "ZTS", "CRL", "MTD", "WST", "TECH", "TFX", "GEHC", "DVA", "VTRS",
    "RMD", "INCY", "SGEN", "NBIX", "EXAS", "NTRA", "PHM", "PTGX",
]

_FINANCIALS = [
    "BRK.B", "JPM", "BAC", "WFC", "MS", "GS", "BLK", "SCHW", "AXP", "USB",
    "PNC", "COF", "TFC", "SPGI", "MCO", "ICE", "CME", "CBOE", "NDAQ", "FDS",
    "MSCI", "MA", "V", "DFS", "SYF", "ALLY", "CFG", "HBAN", "RF", "KEY",
    "MTB", "FITB", "NTRS", "STT", "BK", "TROW", "IVZ", "BEN", "FLT", "ADP",
    "PAYX", "CINF", "PRU", "MET", "AFL", "HIG", "TRV", "CB", "AON", "MMC",
    "WTW", "ACGL", "PGR", "ALL", "WRB", "RNR", "CINF", "GL", "ERIE",
]

_CONSUMER_DISC = [
    "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG",
    "MAR", "HLT", "TGT", "ROST", "BBY", "F", "GM", "ORLY", "AZO", "AN",
    "KMX", "APTV", "BWA", "LKQ", "RL", "PVH", "CMG", "YUM", "DRI", "QSR",
    "DPZ", "EL", "CPRI", "TPR", "GPC", "ETSY", "EBAY", "PHM", "TOL", "DHI",
    "LEN", "NVR", "MDC", "MTH", "POOL", "SWK", "WHR", "LEG", "FND", "W",
    "RCL", "CCL", "NCLH", "LVS", "MGM", "WYNN", "CZR", "PENN",
]

_CONSUMER_STAPLES = [
    "PG", "KO", "PEP", "WMT", "COST", "PM", "MO", "KHC", "GIS", "K",
    "CPB", "CAG", "HRL", "SJM", "MKC", "CLX", "CL", "CHD", "KR", "SFM",
    "WBA", "EL", "REYN", "POST", "LANC", "CALM", "FLO", "DOLE", "INGR",
    "ADM", "BG", "MOS", "NTR", "AVY", "PKG", "SON", "COTY", "SPB",
]

_ENERGY = [
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "PSX", "VLO", "MPC", "HES",
    "DVN", "FANG", "CTRA", "APA", "HAL", "BKR", "NOV", "RRC", "AR", "CNX",
    "EQT", "CQP", "LNG", "WMB", "OKE", "KMI", "EPD", "MMP", "PAA", "ET",
    "DT", "HFC", "PBF", "PARR", "REX", "DINO",
]

_INDUSTRIALS = [
    "UPS", "HON", "UNP", "RTX", "BA", "GE", "LMT", "NOC", "GD", "TXT",
    "TDG", "FTV", "ITW", "EMR", "ETN", "PH", "GWW", "FAST", "CTAS", "RSG",
    "WM", "CMI", "PCAR", "DE", "CAT", "IR", "XYL", "AME", "ROP", "IEX",
    "GXO", "EXPD", "CHRW", "FDX", "NSC", "CSX", "ODFL", "JBHT", "SAIA",
    "XPO", "WERN", "KNX", "DAL", "LUV", "AAL", "UAL", "ALK", "JBLU",
    "L3H", "HII", "LDOS", "SAIC", "CACI", "BAH", "DRS", "SPXC",
]

_COMMUNICATION = [
    "GOOGL", "GOOG", "META", "DIS", "NFLX", "CMCSA", "T", "VZ", "TMUS",
    "CHTR", "WBD", "PARA", "LYV", "TTWO", "EA", "FOXA", "FOX", "NYT",
    "NWSA", "NWS", "MTCH", "IAC", "YELP", "ZNGA", "RBLX",
]

_REAL_ESTATE = [
    "AMT", "PLD", "EQIX", "CCI", "SBA", "SPG", "EQR", "AVB", "MAA", "UDR",
    "CPT", "ESS", "ARE", "VTR", "WELL", "PEAK", "HR", "CUBE", "COLD",
    "SBAC", "IRM", "PSA", "EXR", "LSI", "NSA", "REXR", "STAG", "LPT",
]

_UTILITIES = [
    "NEE", "DUK", "SO", "AEP", "D", "EXC", "SRE", "XEL", "ES", "FE",
    "ETR", "PPL", "CNP", "NI", "WEC", "AWK", "CMS", "EVRG", "ATO", "NW",
    "OGE", "POR", "AVA", "IDA", "MGEE", "NJR", "SJW", "AWR",
]

_MATERIALS = [
    "LIN", "APD", "ECL", "SHW", "PPG", "ALB", "NEM", "FCX", "STLD", "NUE",
    "RS", "CMC", "BALL", "SEE", "PKG", "AVY", "AMCR", "IP", "WRK", "GPK",
    "SLVM", "TREX", "AZEK", "CSL", "ATI", "X", "CLF", "AA", "MP", "LAC",
]

# ── Additional popular NASDAQ / growth / widely-traded names ───────────────
_POPULAR_EXTRA = [
    # Large-cap NASDAQ not in S&P 500
    "SHOP", "MELI", "SE", "GRAB", "JD", "PDD", "BABA", "NIO", "XPEV", "LI",
    "NTES", "VIPS", "ATHM", "WB", "ZS", "DUOL", "CFLT", "RGEN",
    # Crypto / fintech
    "COIN", "HOOD", "SOFI", "SQ", "PYPL", "UPST", "AFRM", "LC", "OPEN",
    # EV / clean energy
    "RIVN", "LCID", "PLUG", "FCEL", "BLNK", "CHPT", "BE", "ARRY",
    # Biotech growth
    "MRNA", "BNTX", "NTLA", "BEAM", "EDIT", "CRSP", "FATE", "ACAD",
    "ARWR", "IONS", "ALNY", "BGNE", "ZLAB", "LEGN",
    # Software / SaaS
    "SNOW", "PLTR", "PATH", "U", "DDOG", "MNDY", "FRSH", "BRZE",
    "S", "ASAN", "ZM", "DOCU", "FROG", "CFLT",
    # Consumer tech
    "ROKU", "SPOT", "PINS", "SNAP", "TWLO", "BAND", "AMPL",
    # Gaming
    "TTWO", "EA", "ATVI", "NTDOY", "DSGX",
    # Semiconductors beyond S&P
    "CEVA", "SLAB", "DIOD", "IXYS", "AOSL", "VSH", "ONTO",
    # Popular ETFs (useful for broad-market context)
    "SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "HYG", "XLE", "XLF",
    "XLV", "XLK", "XLI", "XLP", "XLU", "XLRE", "XLB", "XLC", "XLY",
    "VTI", "VOO", "ARKK", "ARKG", "ARKF", "ARKQ",
]

# ── Assembled list (deduplicated, sorted) ─────────────────────────────────
def _build() -> List[str]:
    all_tickers = (
        _TECH + _HEALTHCARE + _FINANCIALS + _CONSUMER_DISC + _CONSUMER_STAPLES
        + _ENERGY + _INDUSTRIALS + _COMMUNICATION + _REAL_ESTATE + _UTILITIES
        + _MATERIALS + _POPULAR_EXTRA
    )
    seen: set = set()
    result: List[str] = []
    for t in all_tickers:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


STANDARD_TICKERS: List[str] = _build()

SP500_TICKERS: List[str] = [
    t for t in STANDARD_TICKERS if t not in set(_POPULAR_EXTRA)
]

NASDAQ100_TICKERS: List[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO",
    "COST", "NFLX", "ASML", "AMD", "CSCO", "PEP", "ADBE", "TMUS", "TXN",
    "INTU", "QCOM", "CMCSA", "AMGN", "HON", "AMAT", "ISRG", "BKNG", "VRTX",
    "MU", "LRCX", "REGN", "PANW", "ADP", "SNPS", "GILD", "SBUX", "KLAC",
    "MELI", "MDLZ", "CDNS", "INTC", "ADI", "CTAS", "ABNB", "ORLY", "MAR",
    "FTNT", "PYPL", "MNST", "CEG", "KDP", "CPRT", "WDAY", "MCHP", "KHC",
    "FAST", "ON", "DXCM", "CCEP", "TTD", "TEAM", "CDW", "CSX", "ROP",
    "IDXX", "AEP", "GFS", "PAYX", "NXPI", "VRSK", "FANG", "PCAR", "ROST",
    "ODFL", "EA", "BIIB", "CSGP", "ANSS", "CTSH", "CRWD", "EXC", "DLTR",
    "BKR", "GEHC", "XEL", "ZS", "DDOG", "SMCI", "ILMN", "WBD",
]


def get_tickers(name: str = "standard") -> List[str]:
    """Return a named ticker list.

    Args:
        name: "standard" | "sp500" | "nasdaq100"
    """
    mapping = {
        "standard": STANDARD_TICKERS,
        "sp500": SP500_TICKERS,
        "nasdaq100": NASDAQ100_TICKERS,
    }
    return mapping.get(name.lower(), STANDARD_TICKERS)
