import yfinance as yf

ticker = "NVDA"
try:
    df = yf.download(ticker, period="2d", interval="5m", progress=False)
    print(f"Rows found: {len(df)}")
    chart_data = []
    if not df.empty:
        for idx, row in df.iterrows():
            # Handle possible MultiIndex or Series vs DataFrame
            try:
                o = float(row['Open'])
                h = float(row['High'])
                l = float(row['Low'])
                c = float(row['Close'])
            except Exception:
                # If yf returns a different structure
                o = float(row[('Open', ticker)])
                h = float(row[('High', ticker)])
                l = float(row[('Low', ticker)])
                c = float(row[('Close', ticker)])
                
            chart_data.append({
                "time": int(idx.timestamp()),
                "open": o,
                "high": h,
                "low": l,
                "close": c
            })
    print(f"Formatted data: {len(chart_data)} points")
    print(f"First point: {chart_data[0] if chart_data else 'None'}")
except Exception as e:
    print(f"ERROR: {e}")
