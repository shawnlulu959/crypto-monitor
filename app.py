import streamlit as st
import ccxt
import pandas as pd
from datetime import datetime
import pytz
import concurrent.futures # 引入多线程库

# --- 1. 页面配置 ---
st.set_page_config(page_title="Binance Full OI Scanner", layout="wide")

# CSS: 极致利用屏幕空间
st.markdown("""
<style>
    .stApp { background-color: #ffffff; color: #333; }
    [data-testid="stSidebar"] { background-color: #f8f9fa; }
    header {visibility: hidden;}
    .block-container { padding-top: 1rem; padding-bottom: 2rem; }
</style>
""", unsafe_allow_html=True)

st.title("🛰️ 全网全品种 OI 深度扫描 (All Coins)")

# --- 2. 核心逻辑：多线程并发获取 ---

# 初始化交易所 (不开启默认RateLimit，我们需要手动控制并发)
exchange = ccxt.binance({
    'options': {'defaultType': 'future'}
})

# 单个币种获取 OI 的函数 (给线程用的)
def fetch_oi_single(symbol):
    try:
        # 获取 OI (持仓量)
        # 注意：这里我们只拿 Current OI，因为拿 24h Change 需要更多请求，速度会太慢
        data = exchange.fetch_open_interest(symbol)
        return {
            'symbol': symbol,
            'oi_amount': float(data.get('openInterestAmount', 0)),
            'timestamp': data.get('timestamp')
        }
    except:
        return None

def get_full_market_data():
    # 1. 第一步：瞬间获取所有价格、成交量 (1次请求)
    st.caption("Step 1/3: 正在拉取全网基础行情...")
    tickers = exchange.fetch_tickers()
    
    # 2. 第二步：瞬间获取所有费率 (1次请求)
    st.caption("Step 2/3: 正在同步资金费率...")
    funding_map = {}
    try:
        raw_premium = exchange.fapiPublicGetPremiumIndex()
        for item in raw_premium:
            funding_map[item['symbol']] = float(item['lastFundingRate'])
    except: pass

    # 3. 准备币种列表
    target_symbols = []
    for symbol in tickers:
        if '/USDT' in symbol:
            target_symbols.append(symbol)
            
    # 4. 第三步：多线程暴力拉取 OI (300+次请求)
    st.caption(f"Step 3/3: 正在并发扫描 {len(target_symbols)} 个代币的持仓数据 (这需要一点时间)...")
    
    oi_map = {}
    # 进度条
    progress_bar = st.progress(0)
    
    # === 🚀 启动多线程 (20个线程同时工作) ===
    # 警告：线程数不要超过20，否则容易被币安封IP
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        # 提交所有任务
        future_to_symbol = {executor.submit(fetch_oi_single, sym): sym for sym in target_symbols}
        
        completed_count = 0
        total_count = len(target_symbols)
        
        for future in concurrent.futures.as_completed(future_to_symbol):
            result = future.result()
            if result:
                oi_map[result['symbol']] = result['oi_amount']
            
            # 更新进度条
            completed_count += 1
            progress_bar.progress(completed_count / total_count)
            
    progress_bar.empty() # 隐藏进度条

    # 5. 数据组装
    final_data = []
    for symbol in target_symbols:
        ticker = tickers[symbol]
        
        # 基础数据
        coin = symbol.replace('/USDT:USDT', '').replace('/USDT', '')
        price = float(ticker.get('last') or 0)
        change = float(ticker.get('percentage') or 0)
        vol = float(ticker.get('quoteVolume') or 0)
        
        # 费率
        raw_symbol = symbol.replace('/', '').replace(':USDT', '')
        funding = funding_map.get(raw_symbol, 0.0)
        
        # OI 数据
        oi_amount = oi_map.get(symbol, 0)
        oi_value = oi_amount * price # 换算成 U 价值
        
        # 计算 OI/Vol
        oi_vol_ratio = 0
        if vol > 0:
            oi_vol_ratio = oi_value / vol
            
        # 只有当有成交量或有持仓时才显示，过滤垃圾币
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

# --- 4. 侧边栏 ---
with st.sidebar:
    st.header("🎛️ 控制台")
    st.warning("""
    **⚠️ 流量预警**
    
    此模式会一次性发送约 300 次请求。
    刷新时间约需 10-15 秒。
    请勿频繁点击刷新，以免IP被暂时限制。
    """)
    
    if st.button("🚀 开始全网扫描", type="primary"):
        st.rerun()

# --- 5. 展示逻辑 ---
# 只有点击了按钮或初次加载才运行
if 'data_loaded' not in st.session_state:
    st.info("👋 点击左侧 **'开始全网扫描'** 按钮以加载全品种 OI 数据。")
else:
    # 重新加载
    with st.spinner("全网数据正在聚合中，请稍候..."):
        df, fetch_time = get_full_market_data()
        
    # 时间
    tz = pytz.timezone('Asia/Shanghai')
    local_time = fetch_time.astimezone(tz).strftime('%H:%M:%S')
    st.markdown(f"### ⏱️ 数据快照: `{local_time}` | 共扫描: {len(df)} 个合约")

    # 样式设置
    def color_change(val):
        color = '#2e7d32' if val > 0 else '#d32f2f'
        return f'color: {color}; font-weight: bold'
    
    def highlight_high_ratio(val):
        # 如果持仓是成交量的 2倍以上，标红，说明极度控盘
        if val > 2.0: return 'background-color: #ffebee; color: #c62828; font-weight: bold'
        # 如果 > 0.5，标黄
        elif val > 0.5: return 'background-color: #fff3e0; color: #ef6c00'
        return ''

    # 默认按 OI/Vol 降序，寻找主力控盘币
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
            "Symbol": st.column_config.TextColumn("Symbol", width="small"),
            "OI/Vol": st.column_config.NumberColumn("OI/Vol Ratio", help="数值越高，主力锁仓越重，爆发力越强"),
        }
    )

# 标记 Session
st.session_state.data_loaded = True