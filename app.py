import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime
import pytz
import concurrent.futures

# --- 1. 页面配置 ---
st.set_page_config(page_title="Binance OI Scanner", layout="wide")

# CSS: 优化手机端显示与表格字体
st.markdown("""
<style>
    .stApp { background-color: #ffffff; color: #333; }
    /* 手机端优化：减小顶部留白 */
    .block-container { padding-top: 1rem; padding-bottom: 2rem; }
    /* 隐藏无关元素 */
    header {visibility: hidden;}
    /* 表格字体优化 */
    div[data-testid="stDataFrame"] { font-family: 'Roboto', 'Helvetica Neue', sans-serif; }
</style>
""", unsafe_allow_html=True)

# --- 主标题 ---
st.title("🛰️ 全网全品种 OI 深度扫描")

# --- 2. 核心逻辑：多线程并发获取 (保持不变) ---
exchange = ccxt.binance({'options': {'defaultType': 'future'}})

def fetch_oi_single(symbol):
    try:
        data = exchange.fetch_open_interest(symbol)
        return {
            'symbol': symbol,
            'oi_amount': float(data.get('openInterestAmount', 0)),
        }
    except: return None

def get_full_market_data():
    # 1. 获取基础行情
    with st.spinner("Step 1/3: 正在拉取全网价格与成交量..."):
        tickers = exchange.fetch_tickers()
    
    # 2. 获取资金费率
    funding_map = {}
    try:
        raw_premium = exchange.fapiPublicGetPremiumIndex()
        for item in raw_premium:
            funding_map[item['symbol']] = float(item['lastFundingRate'])
    except: pass

    target_symbols = [s for s in tickers if '/USDT' in s]
            
    # 3. 多线程暴力拉取 OI
    oi_map = {}
    progress_text = "Step 3/3: 正在并发扫描 300+ 个合约的持仓数据..."
    progress_bar = st.progress(0, text=progress_text)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_symbol = {executor.submit(fetch_oi_single, sym): sym for sym in target_symbols}
        completed_count = 0
        for future in concurrent.futures.as_completed(future_to_symbol):
            result = future.result()
            if result: oi_map[result['symbol']] = result['oi_amount']
            completed_count += 1
            progress_bar.progress(completed_count / len(target_symbols), text=progress_text)
            
    progress_bar.empty()

    # 4. 数据组装
    final_data = []
    for symbol in target_symbols:
        ticker = tickers[symbol]
        coin = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
        price = float(ticker.get('last') or 0)
        change = float(ticker.get('percentage') or 0)
        vol = float(ticker.get('quoteVolume') or 0)
        
        raw_symbol = symbol.replace('/', '').replace(':USDT', '')
        funding = funding_map.get(raw_symbol, 0.0)
        
        oi_amount = oi_map.get(symbol, 0)
        oi_value = oi_amount * price
        
        oi_vol_ratio = 0
        if vol > 0: oi_vol_ratio = oi_value / vol
            
        if vol > 10000 or oi_value > 10000:
            final_data.append({
                "Symbol": coin,
                "Price": price,
                "Chg%": change,
                "Vol 24h": vol,
                "OI (Hold)": oi_value,
                "OI/Vol": oi_vol_ratio,
                "Funding": funding * 100
            })
            
    return pd.DataFrame(final_data), datetime.now()

# --- 3. 操作区域 (移至主界面中央) ---
# 使用容器把提示和按钮包起来，增加一点背景色，更突出
with st.container():
    st.info("""
    **🤖 操作指南 & 流量预警**
    点击下方按钮开始扫描。此模式会发送约 300 次请求，耗时 **10-15 秒**。
    为防止 IP 被限，请勿频繁点击。
    """)
    
    # 【关键改动】按钮放在这里，并且设置 use_container_width=True 让它在手机上占满一行
    if st.button("🚀 立即开始全网扫描 (Start Scan)", type="primary", use_container_width=True):
        st.session_state.run_scan = True # 设置一个标志位
        st.rerun() # 重新运行以开始扫描

# --- 4. 展示逻辑 ---
if st.session_state.get('run_scan', False):
    # 执行扫描
    df, fetch_time = get_full_market_data()
    
    # 扫描完成后，清除标志位，防止刷新页面重复提交
    st.session_state.run_scan = False 
    
    # 时间快照
    tz = pytz.timezone('Asia/Shanghai')
    local_time = fetch_time.astimezone(tz).strftime('%H:%M:%S')
    st.markdown(f"### ⏱️ 数据快照: `{local_time}` | 已扫描合约数: {len(df)}")

    # 样式设置函数
    def color_change(val):
        color = '#2e7d32' if val > 0 else '#d32f2f'
        return f'color: {color}; font-weight: bold'
    
    def highlight_high_ratio(val):
        if val > 2.0: return 'background-color: #ffebee; color: #c62828; font-weight: bold'
        elif val > 0.5: return 'background-color: #fff3e0; color: #ef6c00'
        return ''

    # 默认按 OI/Vol 降序
    df = df.sort_values(by="OI/Vol", ascending=False)

    styled_df = (df.style
        .format({
            "Price": "${:,.4f}",
            "Chg%": "{:+.2f}%",
            "Vol 24h": "${:,.0f}",
            "OI (Hold)": "${:,.0f}",
            "OI/Vol": "{:.3f}",
            "Funding": "{:+.4f}%"
        })
        .applymap(color_change, subset=['Chg%'])
        .applymap(highlight_high_ratio, subset=['OI/Vol'])
        .background_gradient(subset=['Funding'], cmap='coolwarm', vmin=-0.05, vmax=0.05)
        .bar(subset=['Vol 24h'], color='#e3f2fd')
        .bar(subset=['OI (Hold)'], color='#fff9c4')
    )

    st.dataframe(
        styled_df,
        height=1200,
        use_container_width=True,
        column_config={
            "Symbol": st.column_config.TextColumn("Symbol", width="small", fixed=True), # 固定代币列，方便横向滚动
            "OI/Vol": st.column_config.NumberColumn("OI/Vol Ratio", help="数值越高，主力锁仓越重"),
        }
    )
else:
    # 如果还没开始扫描，显示一个占位提示
    st.write("---")
    st.markdown("<h3 style='text-align: center; color: #999;'>👆 请点击上方按钮开始获取数据</h3>", unsafe_allow_html=True)

# 侧边栏可以放一些次要信息，或者干脆隐藏
with st.sidebar:
    st.header("关于")
    st.markdown("本工具用于辅助发现主力资金动向，非投资建议。")
