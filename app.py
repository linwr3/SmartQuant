import streamlit as st
import pandas as pd
import json
import time
import os
import sys
import threading
import shutil
from datetime import datetime, timedelta, date
from streamlit.runtime.scriptrunner import add_script_run_ctx

# 导入所有需要的模块
import data_manager
import ai_engine
import ai_scheduler
import portfolio
import subprocess
import signal

# 进程管理文件
PID_FILE = "data/ai_scheduler.pid"

st.set_page_config(page_title="SmartQuant Pro - A股量化决策系统", layout="wide")

# --- Session State 初始化 ---
if 'edit_symbol' not in st.session_state: st.session_state['edit_symbol'] = ""
if 'edit_name' not in st.session_state: st.session_state['edit_name'] = ""
if 'edit_shares' not in st.session_state: st.session_state['edit_shares'] = 0 
if 'edit_cost' not in st.session_state: st.session_state['edit_cost'] = 0.0
if 'edit_avail_shares' not in st.session_state: st.session_state['edit_avail_shares'] = 0 
if 'edit_buy_date_str' not in st.session_state: st.session_state['edit_buy_date_str'] = datetime.now().strftime("%Y-%m-%d") 
if 'clear_form_after_submit' not in st.session_state: st.session_state['clear_form_after_submit'] = False

# --- 后台任务状态管理 (数据仓库用) ---
if 'task_status' not in st.session_state: st.session_state['task_status'] = "idle" # idle, running, completed, error
if 'task_message' not in st.session_state: st.session_state['task_message'] = ""
if 'task_result_data' not in st.session_state: st.session_state['task_result_data'] = None

def populate_form(row):
    """点击表格行回调：填充表单"""
    st.session_state.edit_symbol = row['symbol']
    st.session_state.edit_name = row['name']
    st.session_state.edit_shares = row['total_shares']
    st.session_state.edit_avail_shares = row['avail_shares']
    st.session_state.edit_cost = row['cost']
    st.session_state.edit_buy_date_str = row['locked_date']
    # st.rerun()
def clear_form():
    """清空表单"""
    st.session_state.edit_name = ""
    st.session_state.edit_shares = 0 
    st.session_state.edit_avail_shares = 0 
    st.session_state.edit_cost = 0.0
    st.session_state.edit_buy_date_str = datetime.now().strftime("%Y-%m-%d")

def on_symbol_change():
    """代码输入框回调：自动查询名称"""
    symbol = st.session_state.edit_symbol
    if symbol:
        holdings = portfolio.load_portfolio().get('holdings', [])
        existing = next((h for h in holdings if h['symbol'] == symbol), None)
        if existing:
            populate_form(existing)
        else:
            clear_form()
            # n = data_manager.get_stock_name(symbol)
            # if not st.session_state.edit_name or "失败" in st.session_state.edit_name:
            #     st.session_state.edit_name = n if n else "查询失败"

# 初始化数据目录
if not os.path.exists(data_manager.DATA_DIR):
    os.makedirs(data_manager.DATA_DIR)

# --- 侧边栏 ---
page = st.sidebar.radio("功能导航", ["📊 市场全景", "🤖 智能决策 & 机会", "📂 数据仓库 & 选股", "💰 资产管理 (T+1)", "⚙️ 系统设置"])

# --- 辅助函数 ---

def is_scheduler_running():
    if not os.path.exists(PID_FILE): return False
    with open(PID_FILE, 'r') as f:
        try:
            pid = int(f.read().strip())
        except ValueError:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        return False

def start_ai_scheduler():
    if ai_scheduler.TEST_SWITCH:
        ai_scheduler.start_scheduler()
    else:
        if is_scheduler_running():
            st.error("AI 决策任务已在后台运行中。")
            return
        
        SCHEDULER_LOG = "logs/ai_scheduler_error.log"
        if not os.path.exists("logs"): os.makedirs("logs")

        with open(SCHEDULER_LOG, 'w') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Attempting to start ai_scheduler...\n")

        try:
            process = subprocess.Popen(
                [sys.executable, "ai_scheduler.py"], 
                creationflags=subprocess.CREATE_NEW_CONSOLE, 
                close_fds=True
            )
            with open(PID_FILE, 'w') as f:
                f.write(str(process.pid))
            st.success(f"AI 决策任务启动成功！PID: {process.pid}")
            time.sleep(1) 
            st.rerun()
        except Exception as e:
            st.error(f"启动失败: {e}")

def stop_ai_scheduler():
    if not os.path.exists(PID_FILE): return
    with open(PID_FILE, 'r') as f:
        try:
            pid = int(f.read().strip())
        except ValueError:
            os.remove(PID_FILE); return
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        st.success("AI 任务已终止。")
        st.rerun()
    except OSError:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)

# --- 1. 市场全景 ---
if page == "📊 市场全景":
    st.title("A股市场全景")
    
    # 读取配置中的默认数据源
    settings = data_manager.load_settings()
    current_source = settings.get("market_data_source", "sina")
    
    source_options = {
        "sina": "新浪财经 (Sina) - 推荐，速度快",
        "akshare": "AkShare (东方财富源)",
        "baostock": "BaoStock (证券宝)",
        "tushare": "TuShare Pro (需配置Token)"
    }
    
    # 转换为列表索引
    keys = list(source_options.keys())
    idx = keys.index(current_source) if current_source in keys else 0
    
    selected_source = st.selectbox(
        "选择实时行情数据源 (自动刷新)", 
        options=keys, 
        index=idx,
        format_func=lambda x: source_options[x]
    )
    
    # 如果切换了数据源，保存设置并刷新
    if selected_source != current_source:
        settings["market_data_source"] = selected_source
        data_manager.save_settings(settings)
        st.rerun()

    st.divider()

    # 自动获取数据
    with st.spinner(f"正在从 {source_options[selected_source]} 获取数据..."):
        df = data_manager.get_index_quote(source=selected_source)
        
        if not df.empty:
            st.success(f"数据获取成功 ({datetime.now().strftime('%H:%M:%S')})")
            
            # 样式优化
            def highlight_change(val):
                if isinstance(val, (int, float)):
                    color = 'red' if val > 0 else 'green' if val < 0 else 'black'
                    return f'color: {color}'
                return ''

            st.dataframe(
                df.style.map(highlight_change, subset=['涨跌幅', '涨跌额']), 
                use_container_width=True,
                height=400
            )
        else:
            st.error(f"未能从 {source_options[selected_source]} 获取到有效数据，请尝试切换其他数据源。")

# --- 2. 智能决策任务 ---
elif page == "🤖 智能决策 & 机会":
    st.title("AI 投研决策中心")
    
    st.subheader("决策设置")
    c_set1, c_set2 = st.columns(2)
    
    config = data_manager.load_ai_config()

    strategy_options = {
        "High-Risk/High-Reward": "高风险/高收益 (激进策略)",
        "Low-Risk/Low-Yield": "低风险/低收益 (稳健策略)",
        "Dynamic-Market-Adjusted": "动态市场调整 (综合策略)"
    }
    strategy_options_keys = list(strategy_options.keys())
    selected_strategy = c_set1.selectbox("选择决策策略", 
                                         options=strategy_options_keys, 
                                         format_func=lambda x: strategy_options[x], 
                                         index=strategy_options_keys.index(config.get('strategy')))
    period_options = {p: f"{p} 分钟" for p in range(10, 121, 10)}
    period_options_key = list(period_options.keys())
    selected_period = c_set2.selectbox("检测周期", 
                                       options=period_options_key, 
                                       format_func=lambda p: period_options[p], 
                                       index=period_options_key.index(config.get('period_minutes')))

    st.markdown("---")
    st.subheader("任务控制")

    running = is_scheduler_running()
    if running:
        st.success("✅ 后台调度任务正在运行中。")
    else:
        st.error("🛑 后台调度任务未运行。")
    
    current_holdings = portfolio.load_portfolio().get('holdings', [])
    
    col_btn1, col_btn2, col_btn3 = st.columns(3)
    
    if col_btn1.button("🚀 启动 AI 调度", disabled=running, type="primary"):
        data_manager.save_ai_config(selected_strategy, selected_period)
        start_ai_scheduler()
    
    if col_btn2.button("🔴 停止 AI 调度", disabled=not running):
        stop_ai_scheduler()

    # 调试按钮
    if col_btn3.button("🐞 调试 Prompt (不消耗Token)", type="secondary"):
        st.info("正在生成 Prompt 预览...")
        data_manager.save_ai_config(selected_strategy, selected_period)
        portfolio_summary, mock_stocks = ai_scheduler.gen_holding_stocks_info()
        if not mock_stocks:
            pass
        
        system_prompt, user_prompt = ai_engine.generate_batch_prompt(portfolio_summary, mock_stocks)
        st.text_area("生成的 Prompt 内容", system_prompt + user_prompt, height=400)

# --- 3. 数据仓库管理 (含后台线程) ---
elif page == "📂 数据仓库 & 选股":
    st.title("本地数据仓库")

    # --- 后台任务线程逻辑 (修复 Context 问题) ---
    def run_background_task(task_type, **kwargs):
        """通用后台任务执行器"""
        print(f"线程启动: {task_type}") # 调试输出到控制台
        
        # 显式更新状态，因为有了 Context，Streamlit 应该能感知到
        st.session_state['task_status'] = "running"
        st.session_state['task_message'] = "任务正在初始化..."
        
        try:
            if task_type == "full_init":
                st.session_state['task_message'] = "正在执行全量初始化 (备份 + 下载)... 这可能需要几分钟"
                success, msg = data_manager.init_history_data_tushare()
                st.session_state['task_message'] = msg
                st.session_state['task_status'] = "completed" if success else "error"
                print(f"全量初始化结束: {success}, {msg}")
                
            elif task_type == "daily_update":
                st.session_state['task_message'] = "正在执行每日增量更新..."
                msg = data_manager.update_today_data_tushare()
                st.session_state['task_message'] = msg
                st.session_state['task_status'] = "completed"
                print(f"每日更新结束: {msg}")
                
        except Exception as e:
            error_msg = f"线程内部错误: {str(e)}"
            print(error_msg)
            st.session_state['task_message'] = error_msg
            st.session_state['task_status'] = "error"

    # --- UI 显示状态监控区 ---
    status_placeholder = st.empty()
    
    # 状态逻辑显示
    with status_placeholder.container():
        current_status = st.session_state.get('task_status', 'idle')
        current_msg = st.session_state.get('task_message', '')
        
        if current_status == "running":
            st.warning(f"🔄 执行中: {current_msg}")
            # 如果是 running 状态，自动刷新页面以轮询状态变化
            time.sleep(2) 
            st.rerun()
            
        elif current_status == "completed":
            st.success(f"✅ {current_msg}")
            if st.button("关闭消息", key="close_msg_success"):
                st.session_state['task_status'] = "idle"
                st.rerun()
                
        elif current_status == "error":
            st.error(f"❌ {current_msg}")
            if st.button("关闭消息", key="close_msg_error"):
                st.session_state['task_status'] = "idle"
                st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("数据更新")
        # 每日更新
        if st.button("📅 每日更新 (TuShare)", disabled=(st.session_state['task_status']=="running")):
            # 🚨 关键：启动线程时注入 Context
            thread = threading.Thread(target=run_background_task, args=("daily_update",))
            add_script_run_ctx(thread) # 注入上下文
            thread.start()
            st.rerun() # 立即重刷以显示 running 状态
            
    with c2:
        st.subheader("全量初始化")
        # 全量初始化
        if st.button("🛠️ 全量历史数据初始化 (备份旧数据)", type="secondary", disabled=(st.session_state['task_status']=="running")):
            if st.session_state['task_status'] != "running":
                thread = threading.Thread(target=run_background_task, args=("full_init",))
                add_script_run_ctx(thread) # 注入上下文
                thread.start()
                st.rerun()

    st.divider()
    st.subheader("本地策略选股 (无需联网)")
    
    s1, s2 = st.columns(2)
    strategy = None
    if s1.button("🌙 一夜持股法"): strategy = "overnight"
    if s2.button("🚀 打板策略"): strategy = "limit_up"
    
    if strategy:
        with st.spinner("正在筛选本地数据..."):
            results = data_manager.screen_stocks_local(strategy)
            if len(results) > 0:
                st.write(f"筛选出 {len(results)} 只股票:")
                df_res = pd.DataFrame(results)
                st.dataframe(
                    df_res, 
                    column_config={"score": st.column_config.ProgressColumn("推荐度", min_value=0, max_value=100)},
                    width="stretch" # 🚨 修复: 替换 use_container_width
                )

                if st.button("生成查询prompt"):
                    system_prompt, user_prompt = ai_engine.generate_batch_recommand_prompt(results)
                    st.info("正在生成 Prompt 预览...")
                    st.text_area("生成的 Prompt 内容", system_prompt + user_prompt, height=400)
            else:
                st.info("本地数据中未筛选到符合条件的股票，请先确保已下载历史数据。")

# --- 4. 系统设置 ---
elif page == "⚙️ 系统设置":
    st.title("配置中心")
    
    current_settings = data_manager.load_settings()
    
    with st.form("config_form"):
        st.subheader("🤖 AI 模型配置")
        
        # 将配置转换为列表形式
        providers = data_manager.MODEL_PROVIDERS
        provider_keys = list(providers.keys())
        
        # 当前选中的
        cur_prov = current_settings.get("selected_provider", "DeepSeek")
        if cur_prov not in provider_keys: cur_prov = provider_keys[0]
        
        selected_p = st.radio("选择激活的 AI 模型厂商", provider_keys, index=provider_keys.index(cur_prov))
        
        st.markdown("---")
        p_info = providers[selected_p]
        
        c_key, c_model = st.columns(2)
        new_api_key = c_key.text_input(f"{selected_p} API Key", value=current_settings.get("api_key", ""), type="password")
        new_model_name = c_model.text_input(f"模型名称", value=current_settings.get("model_name", p_info['default_model']))
        
        st.info(f"👉 [点击申请 Key]({p_info['help_url']}) | Base URL: `{p_info['base_url']}` (自动应用)")

        st.subheader("💾 数据源配置")
        ts_tokens = st.text_input(
            "TuShare Token(s) (逗号分隔)", 
            value=current_settings.get("tushare_tokens", ""), 
            type="password",
            help="用于历史数据下载，建议配置多个以避免限频"
        )
        st.subheader("WxPusher配置")
        wxpusher_token = st.text_input(
            "WxPusher AppToken", 
            value=current_settings.get("wxpusher_token", ""), 
            type="password"
        )
        st.subheader("WxPusher uids")
        wxpusher_uids = st.text_input(
            "WxPusher 推送的uid配置，用逗号间隔", 
            value=current_settings.get("wxpusher_uids", "")
        )
        if st.form_submit_button("保存全部设置"):
            new_settings = current_settings.copy()
            new_settings.update({
                "selected_provider": selected_p,
                "api_key": new_api_key,
                "model_name": new_model_name,
                "base_url": p_info['base_url'],
                "tushare_tokens": ts_tokens,
                "wxpusher_token":wxpusher_token,
                "wxpusher_uids":wxpusher_uids,
            })
            data_manager.save_settings(new_settings)
            st.success("配置已保存")

# --- 5. 资产管理 (保持不变) ---
elif page == "💰 资产管理 (T+1)":
    # (此处代码与您提供的完全一致，为节省篇幅省略，请直接使用您上传文件中的资产管理部分代码)
    if st.session_state.get('clear_form_after_submit', False):
        st.session_state['edit_symbol'] = ''
        st.session_state['edit_name'] = ''
        st.session_state['edit_shares'] = 0
        st.session_state['edit_cost'] = 0.0
        st.session_state['clear_form_after_submit'] = False

    st.title("实战资产管理")
    
    # 1. 顶部：资金维护
    data = portfolio.load_portfolio()
    col1, col2, col3 = st.columns(3)
    with col1:
        new_cash = st.number_input("当前可用资金 (手动维护)", value=data.get('cash', 100000.0), step=1000.0)
        if new_cash != data.get('cash'):
            portfolio.update_cash(new_cash)
            st.rerun()
            
    # 计算总资产
    holdings = data.get('holdings', [])
    market_val = 0
    df_data = []
    
    for h in holdings:
        rt = data_manager.get_realtime_quote(h['symbol'])
        price = rt['price']
        
        if price <= 0:
            if h.get('buys') and len(h['buys']) > 0:
                price = h['buys'][0]['cost']
            else:
                price = 0.0 

        total_cost = h['total_shares'] * h['cost']
        latest_buy_date = h['locked_date'] 
        
        mv = price * h['total_shares']
        market_val += mv
        
        df_data.append({
            "symbol": h['symbol'],
            "name": h['name'],
            "total_shares": h['total_shares'],
            "avail_shares": h['avail_shares'],
            "cost": h['cost'], 
            "price": price,
            "market_value": round(mv,3),
            "profit": round(mv - total_cost, 3),
            "locked_date": latest_buy_date, 
            "total_cost": total_cost 
        })
        
    with col2: st.metric("持仓市值", f"¥{market_val:,.3f}")
    with col3: st.metric("账户总资产", f"¥{(new_cash + market_val):,.3f}")

    st.divider()

    # 2. 中部：持仓列表
    st.subheader("持仓列表 (点击行进行修改/删除)")
    if df_data:
        df = pd.DataFrame(df_data)
        st.dataframe(df, 
            column_config={
                "symbol": "代码", 
                "name": "名称", 
                "total_shares": "持有股数", 
                "avail_shares": "可用股数(T+1)",
                "cost": "平均成本", 
                "price": "现价",
                "market_value": "市值", 
                "profit": "浮动盈亏",
                "latest_buy_date": "最近买入日"
            }
        )
        st.divider()
        symbol_in = st.text_input("代码", key="edit_symbol", on_change=on_symbol_change)
        with st.form("upsert_form"):
            c3, c4, c5, c6 = st.columns(4)
            shares_in = c3.number_input("最新总持有股数", min_value=0, step=100, key="edit_shares")
            avail_shares_in = c4.number_input("最新可用股数 (T+1)", min_value=0, step=100, key="edit_avail_shares")
            cost_in = c5.number_input("最新平均成本", min_value=0.0, step=0.1, key="edit_cost")
            buy_date_input = c6.date_input("买入日期 (锁定 T+1)", value=datetime.now().date(), max_value=datetime.now().date()) 
            
            b1, b2, b3 = st.columns([1, 1, 4])
            submit = b1.form_submit_button("💾 保存/新增/修改", type="primary")
            delete = b2.form_submit_button("🗑️ 删除此股 (清仓)", type="secondary")
            
            if submit:
                final_symbol = st.session_state.get('edit_symbol', '')
                final_name = st.session_state.get('edit_name', '')
                if not final_name or "失败" in final_name:
                    final_name = data_manager.get_stock_name(final_symbol)
                portfolio.upsert_holding(final_symbol, final_name, shares_in, avail_shares_in, cost_in, buy_date_input.strftime("%Y-%m-%d"))
                st.session_state['clear_form_after_submit'] = True
                st.success(f"{final_symbol} 保存成功")
                st.rerun()
                
            if delete:
                final_symbol = st.session_state.get('edit_symbol', '')
                if final_symbol:
                    portfolio.delete_holding(final_symbol)
                    st.session_state['clear_form_after_submit'] = True
                    st.warning(f"{final_symbol} 已删除")
                    st.rerun()
    else:
        st.info("空仓状态，请在下方添加持仓")