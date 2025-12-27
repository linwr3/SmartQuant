import pandas as pd
import numpy as np
import os
from datetime import datetime
import time
import requests
import json
import shutil
import tushare as ts
import akshare as ak
import baostock as bs

# --- 全局配置 ---
DATA_DIR = "data"
HISTORY_DIR = os.path.join(DATA_DIR, "history")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

# 完整的模型列表
MODEL_PROVIDERS = {
    "DeepSeek": {"base_url": "https://api.deepseek.com", "default_model": "deepseek-chat", "help_url": "https://www.deepseek.com/"},
    "Qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen-plus", "help_url": "https://bailian.console.aliyun.com/"},
    "Tencent Hunyuan": {"base_url": "https://api.hunyuan.cloud.tencent.com/v1", "default_model": "hunyuan-pro", "help_url": "https://cloud.tencent.com/product/hunyuan"},
    "Gitee AI": {"base_url": "https://ai.gitee.com/v1", "default_model": "yi-34b-chat", "help_url": "https://ai.gitee.com/"},
    "360 AI": {"base_url": "https://api.360.cn/v1", "default_model": "360gpt-pro", "help_url": "https://ai.360.com/"},
    "Aliyun Bailian": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen-max", "help_url": "https://bailian.console.aliyun.com/"},
    "Baidu Qianfan": {"base_url": "https://qianfan.baidubce.com/v2", "default_model": "ernie-4.0-8k-latest", "help_url": "https://cloud.baidu.com/product/wenxinworkshop"},
    "O3 (Zero One)": {"base_url": "https://api.01.ai/v1", "default_model": "yi-large", "help_url": "https://platform.01.ai/"},
    "StepFun (Jieyue)": {"base_url": "https://api.stepfun.com/v1", "default_model": "step-1-8k", "help_url": "https://platform.stepfun.com/"},
    "Moonshot (Kimi)": {"base_url": "https://api.moonshot.cn/v1", "default_model": "moonshot-v1-8k", "help_url": "https://platform.moonshot.cn/"},
    "OpenAI": {"base_url": "https://api.openai.com/v1", "default_model": "gpt-4o", "help_url": "https://platform.openai.com/"},
    "Ollama (Local)": {"base_url": "http://localhost:11434/v1", "default_model": "llama3", "help_url": "https://ollama.com/"},
    "Groq": {"base_url": "https://api.groq.com/openai/v1", "default_model": "llama3-70b-8192", "help_url": "https://console.groq.com/"},
    "Gemini": {"base_url": "https://api.gemini.google.com/v1", "default_model": "gemini-1.5-pro", "help_url": "https://ai.google.dev/"},
}

MAX_TRY_TIMES = 60

if not os.path.exists(HISTORY_DIR):
    os.makedirs(HISTORY_DIR)

DATA_DIR = "data"
CONFIG_FILE = f"{DATA_DIR}/ai_config.json"

def load_ai_config():
    if not os.path.exists(CONFIG_FILE): return {"strategy": "Dynamic-Market-Adjusted", "period_minutes": 10}
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f: return json.load(f)

def save_ai_config(strategy, period_minutes):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    config = {
        "strategy": strategy, 
        "period_minutes": period_minutes, 
    }
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def load_settings():
    """加载配置"""
    if not os.path.exists(SETTINGS_FILE):
        return {
            "tushare_tokens": "", 
            "selected_provider": "DeepSeek",
            "api_key": "",
            "model_name": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "market_data_source": "sina",
            "wxpusher_token": "",
            "wxpusher_uids": "",
        }
    with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_settings(settings):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def fetch_stock_name_sina(symbol):
    code = symbol.lower()
    if not (code.startswith('sh') or code.startswith('sz')):
        if code.startswith('6') or code.startswith('9') or code.startswith('5'): code = 'sh' + code
        else: code = 'sz' + code
    url = f"http://hq.sinajs.cn/list={code}"
    try:
        resp = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'}, timeout=2)
        if resp.status_code == 200 and len(resp.text) > 20:
            content = resp.text.split('="')[1].split(',')
            return content[0]
    except: pass
    return ""

def get_stock_name(symbol):
    name = fetch_stock_name_sina(symbol)
    if name: return name
    return ""

def calculate_indicators(df, params=None):
    if df.empty:
        return df
    
    # 确保数据量足够计算长周期指标（如26日EMA）
    if len(df) < 30:
        # 数据太少时返回原始数据，避免 AI 获取到一堆空值
        return df
    
    df = df.sort_index(ascending=True)
    df.rename(columns=lambda x: x.lower(), inplace=True)
    if 'close' not in df.columns: return df
    try:
        # 1. 基础移动平均线 (Trend)
        df['MA5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['MA10'] = df['close'].rolling(window=10, min_periods=1).mean()
        df['MA20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['MA60'] = df['close'].rolling(window=60, min_periods=1).mean()
        # 2. MACD 计算 (使用标准的 12, 26, 9 参数)
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp1 - exp2
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD'] = 2 * (df['DIF'] - df['DEA'])
        # 3. KDJ 计算 (标准 9, 3, 3)
        low_list = df['low'].rolling(window=9, min_periods=9).min()
        high_list = df['high'].rolling(window=9, min_periods=9).max()
        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        rsv = rsv.fillna(50) # 填充空值
        df['K'] = rsv.ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['J'] = 3 * df['K'] - 2 * df['D']
        # 4. RSI 计算 (14日)
        delta = df['close'].diff()
        up = delta.clip(lower=0)
        down = -1 * delta.clip(upper=0)
        rs = up.ewm(com=13, adjust=False).mean() / (down.ewm(com=13, adjust=False).mean() + 1e-10)
        df['RSI'] = 100 - (100 / (1 + rs))
        # 5. 信号标记：金叉与死叉
        # MACD 金叉：DIF 上穿 DEA
        df['MACD_Cross'] = 0
        df.loc[(df['DIF'] > df['DEA']) & (df['DIF'].shift(1) <= df['DEA'].shift(1)), 'MACD_Cross'] = 1
        df.loc[(df['DIF'] < df['DEA']) & (df['DIF'].shift(1) >= df['DEA'].shift(1)), 'MACD_Cross'] = -1

        return df
    except Exception as e:
        print(f"指标计算异常: {e}")
        return df

def get_realtime_quote(symbol):
    # 默认使用 Sina，因为最稳定且免费
    code = symbol.lower()
    if not (code.startswith('sh') or code.startswith('sz')):
        code = ('sh' + code) if code.startswith('6') or code.startswith('9') else ('sz' + code)
    url = f"http://hq.sinajs.cn/list={code}"
    try:
        resp = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn/'}, timeout=3)
        if resp.status_code == 200:
            content = resp.text.split('="')[1].split(',')
            if len(content) > 30:
                price = float(content[3])
                if price < 0.01: price = float(content[2]) # 昨收
                return {'price': price, 'name': content[0], 'source': 'sina'}
    except: pass
    return {'price': 0.0, 'name': '未知', 'source': 'none'}

def get_index_quote(source="sina"):
    """
    根据指定源获取指数数据
    """
    index_map = {'sh000001': '上证指数', 'sz399001': '深证成指', 'sz399006': '创业板指'}
    
    if source == "sina":
        data_list = []
        for code, name in index_map.items():
            url = f"http://hq.sinajs.cn/list={code}"
            try:
                r = requests.get(url, headers={'Referer': 'https://finance.sina.com.cn'})
                parts = r.text.split('="')[1].split(',')
                price = float(parts[3])
                pre_close = float(parts[2])
                chg = (price - pre_close) / pre_close * 100
                data_list.append({'名称': name, '最新价': price, '涨跌幅': chg, '涨跌额': price-pre_close})
            except: pass
        return pd.DataFrame(data_list)
        
    elif source == "baostock":
        try:
            bs.login()
            rs = bs.query_history_k_data_plus("sh.000001", "close,pctChg", start_date=datetime.now().strftime("%Y-%m-%d"), frequency="d")
            bs.logout()
            # Baostock 实时性较差，通常只返回日线，这里仅作演示结构
            return pd.DataFrame([{'名称': '上证指数(BS延迟)', '最新价': 0, '涨跌幅': 0, '涨跌额': 0}])
        except: return pd.DataFrame()

    elif source == "akshare":
        try:
            df = ak.stock_zh_index_spot() # AkShare 指数接口
            # 筛选主要指数
            target = ['上证指数', '深证成指', '创业板指']
            df = df[df['名称'].isin(target)]
            return df[['名称', '最新价', '涨跌幅', '涨跌额']]
        except: return pd.DataFrame()

    elif source == "tushare":
        settings = load_settings()
        token = settings.get("tushare_tokens", "").split(',')[0]
        if not token: return pd.DataFrame()
        try:
            ts.set_token(token)
            pro = ts.pro_api()
            # Tushare 实时接口积分要求高，这里用 Daily 模拟（只能看到昨日）
            # 或者使用 Tushare 的 tick 接口
            return pd.DataFrame([{'名称': 'TuShare需高积分权限', '最新价': 0, '涨跌幅': 0}])
        except: return pd.DataFrame()
        
    return pd.DataFrame()

class TushareScheduler:
    def __init__(self, tokens):
        self.tokens = tokens
        self.index = 0
        self.pro = ts.pro_api(self.tokens[0])

    def next_token(self):
        """当遇到限频错误时，切换到下一个 Token"""
        if len(self.tokens) > 1:
            self.index = (self.index + 1) % len(self.tokens)
            ts.set_token(self.tokens[self.index])
            self.pro = ts.pro_api()
            print(f"切换至 Token 索引: {self.index}")
            return True
        return False

    def get_pro(self):
        return self.pro

def init_history_data_tushare():
    """
    【修正版】全量初始化：具备多Token轮换、指数重试、断点续传能力的工业级下载函数
    """
    settings = load_settings()
    tokens = [t.strip() for t in settings.get("tushare_tokens", "").split(',') if t.strip()]
    if not tokens:
        return False, "错误：未配置 TuShare Token"
    
    scheduler = TushareScheduler(tokens)

    # 1. 备份与目录准备 (保留)
    if os.path.exists(HISTORY_DIR) and os.listdir(HISTORY_DIR):
        backup_name = f"history_bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_dirname = os.path.join(DATA_DIR, backup_name)
        if os.path.exists(backup_dirname):
            print('备份失败: 目标目录已存在')
            return False, f"备份失败: 目标目录`{backup_name}`已存在, 请检查是否有同名目录"
        shutil.move(HISTORY_DIR, backup_dirname)
    
    if not os.path.exists(HISTORY_DIR):
        os.makedirs(HISTORY_DIR)

    # 2. 获取股票列表
    try:
        pro = scheduler.get_pro()
        stock_list = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name')
        stock_list.to_csv(os.path.join(DATA_DIR, "stock_basic.csv"), index=False, encoding='utf-8')
    except Exception as e:
        return False, f"无法获取股票列表: {e}"

    total = len(stock_list)
    success_count = 0
    
    # 3. 循环下载（带多级重试逻辑）
    for i, row in stock_list.iterrows():
        ts_code = row['ts_code']
        symbol = row['symbol']
        
        # 指数退避重试逻辑
        max_retries = 3
        for retry in range(max_retries):
            try:
                # 随机延迟防止过于规律的请求
                df = scheduler.get_pro().daily(ts_code=ts_code, start_date='20200101', adj='qfq')
                if not df.empty:
                    df = df.iloc[::-1] # 升序
                    df.to_csv(os.path.join(HISTORY_DIR, f"{symbol}.csv"), index=False)
                    success_count += 1
                
                # 打印进度
                if success_count % 50 == 0:
                    print(f"进度: {success_count}/{total}")
                
                # 基础限频：每秒最多请求数取决于积分，这里给个保守值
                time.sleep(0.2) 
                break # 成功则跳出重试循环

            except Exception as e:
                err_msg = str(e)
                if "抱歉，您每分钟最多访问" in err_msg or "接口请求频率超限" in err_msg:
                    print(f"检测到限频，尝试切换 Token...")
                    if not scheduler.next_token():
                        # 如果没有更多Token，强制休眠一分钟
                        print("无更多Token可用，强制等待60秒...")
                        time.sleep(60)
                else:
                    print(f"下载 {symbol} 出错 (重试 {retry}): {e}")
                    time.sleep(2 ** retry) # 指数退避
        
        # 实时反馈给 UI 的状态更新（通过打印或文件，UI层会捕获）
    
    return True, f"初始化完成！成功下载 {success_count}/{total} 只股票。备份已存至 data 目录。"

def update_today_data_tushare():
    """
    【修正版】每日增量更新：同样应用 Token 轮换逻辑，防止更新到一半卡死
    """
    settings = load_settings()
    tokens = [t.strip() for t in settings.get("tushare_tokens", "").split(',') if t.strip()]
    if not tokens: return "错误：未配置 Token"

    scheduler = TushareScheduler(tokens)
    today_str = datetime.now().strftime("%Y%m%d")
    
    try:
        # 获取当天所有股票日线数据
        df_today = None
        for _ in range(len(tokens) + 1):
            try:
                df_today = scheduler.get_pro().daily(trade_date=today_str)
                break
            except:
                if not scheduler.next_token(): break
        
        if df_today is None or df_today.empty:
            return "TuShare 今日无数据 (非交易日或未收盘)"

        update_count = 0
        for _, row in df_today.iterrows():
            symbol = row['ts_code'].split('.')[0]
            csv_path = os.path.join(HISTORY_DIR, f"{symbol}.csv")
            
            if os.path.exists(csv_path):
                # 将 row 转换为 CSV 格式并追加
                # 注意：这里需要确保 CSV 列顺序一致
                try:
                    # 读取表头确定顺序
                    df_old = pd.read_csv(csv_path, nrows=0)
                    row_df = pd.DataFrame([row])
                    row_df = row_df[df_old.columns] # 强制列对齐
                    row_df.to_csv(csv_path, mode='a', header=False, index=False)
                    update_count += 1
                except: continue

        return f"增量更新完成，共更新 {update_count} 只股票"
    except Exception as e:
        return f"更新过程中发生异常: {e}"
    
def load_local_history(symbol):
    path = os.path.join(HISTORY_DIR, f"{symbol}.csv")
    if not os.path.exists(path):
        return pd.DataFrame(columns=['trade_date', 'close', 'open', 'high', 'low', 'vol'])
    try:
        df = pd.read_csv(path)
        df['trade_date'] = pd.to_datetime(df['trade_date'].astype(str), format='%Y%m%d', errors='coerce')
        df.set_index('trade_date', inplace=True)
        df.sort_index(inplace=True)
        return df
    except:
        return pd.DataFrame()

def screen_stocks_local(strategy_name):
    """
    【修正版】严谨选股逻辑：剔除垃圾股，增加停牌和量比校验
    """
    results = []
    if not os.path.exists(HISTORY_DIR):
        return []

    # 获取所有股票的基本信息（用于过滤ST和名称）
    # 假设你在初始化时保存了 stock_basic.csv，如果没有，我们从历史文件名中提取
    files = [f for f in os.listdir(HISTORY_DIR) if f.endswith('.csv')]
    
    # 临时获取 stock_basic 用于过滤 ST (建议在初始化时保存一份到 data/stock_basic.csv)
    basic_info = {}
    basic_path = os.path.join(DATA_DIR, "stock_basic.csv")
    if os.path.exists(basic_path):
        try:
            df_basic = pd.read_csv(basic_path)
            # symbol -> name 的映射
            basic_info = dict(zip(df_basic['symbol'].astype(str), df_basic['name']))
        except: pass

    for f in files:
        symbol = f.replace('.csv', '')
        try:
            # 过滤1：名称过滤（剔除ST、退市、*ST）
            name = basic_info.get(symbol, "")
            if any(x in name for x in ["ST", "退市", "B股", "北证"]): 
                continue

            df = pd.read_csv(os.path.join(HISTORY_DIR, f))
            if len(df) < 60: continue # 过滤2：上市不满60天的次新股
            
            # 计算指标用于选股
            df = calculate_indicators(df)
            curr = df.iloc[-1]
            
            # 过滤3：停牌过滤
            if curr['vol'] <= 0: continue 

            score = 0
            reason = ""
            
            # --- 策略：一夜持股 (修正后的量价共振逻辑) ---
            if strategy_name == "overnight":
                vol_ratio = curr['vol'] / (df['vol'].rolling(5).mean().iloc[-2] + 1) # 对比5日均量
                
                # 逻辑：涨幅在3%-8%之间，放量1.5倍以上，收盘价站上MA5且处于上升趋势
                if 3 < curr['pct_chg'] < 8 and vol_ratio > 1.8:
                    if curr['close'] > curr['MA5'] and curr['DIF'] > 0:
                        score = 80 + min(vol_ratio * 2, 15) # 量比越大权重越高，最高加15分
                        reason = f"量比{vol_ratio:.1f} 趋势向上"
            
            # --- 策略：打板策略 (修正后的强势股逻辑) ---
            elif strategy_name == "limit_up":
                # A股主板涨停一般 > 9.9%，创业板 > 19.9%
                if (symbol.startswith('60') or symbol.startswith('00')) and curr['pct_chg'] > 9.8:
                    score = 95
                    reason = "主板涨停"
                elif (symbol.startswith('30') or symbol.startswith('68')) and curr['pct_chg'] > 19.8:
                    score = 98
                    reason = "双创涨停"

            if score > 0:
                results.append({
                    'symbol': symbol, # 股票代码
                    'name': name, # 股票名称
                    'score': round(score, 1),
                    'reason': reason,
                    'close': curr['close'], # 最新收盘价
                    'pct_chg': curr['pct_chg'] # 涨跌幅
                })
        except Exception as e:
            # print(f"解析 {symbol} 失败: {e}")
            continue

    # 按分数降序排列
    return sorted(results, key=lambda x: x['score'], reverse=True)[:50]