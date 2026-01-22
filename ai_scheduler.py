import json
import os
import sys
import time
import data_manager
import threading
import wxpusher
from datetime import datetime, time as dtime
from apscheduler.schedulers.blocking import BlockingScheduler
from plyer import notification 

try:
    import portfolio
    import data_manager
    import ai_engine 
except ImportError as e:
    print(f"模块导入错误: {e}")
    sys.exit(1)

UPDATE_TRY_TIME = 10
CLEAR_LIMIT = 1

TEST_SWITCH = False

def is_market_open():
    """
    判断当前是否为 A 股交易时间
    交易时间: 周一到周五 09:30-11:30, 13:00-15:00
    注意：此处未排除法定节假日，仅做基础时间判断
    """
    now = datetime.now()
    
    # 1. 排除周末 (0-4 是周一到周五, 5-6 是周末)
    if now.weekday() > 4:
        return False, False
    
    current_time = now.time()
    
    # 2. 定义时间段
    morning_start = dtime(9, 30)
    morning_end = dtime(11, 30)
    afternoon_start = dtime(13, 0)
    afternoon_end = dtime(15, 0)
    
    # 3. 判断是否在区间内
    is_morning = morning_start <= current_time <= morning_end
    is_break = morning_end < current_time < afternoon_start
    is_afternoon = afternoon_start <= current_time <= afternoon_end
    
    return is_morning or is_afternoon, is_break

def send_notification(title, message):
    try: notification.notify(title=title, message=message, app_name='SmartQuant Pro AI')
    except: pass

class SchedulerUpdateHistoryContext:
    """用于管理调度器跨任务状态的上下文类"""
    def __init__(self):
        curr_is_market_open, curr_is_market_break = is_market_open()
        self.was_market_open = curr_is_market_open or curr_is_market_break
        self.update_pending = False # 是否有待执行的更新任务
        self.update_thread = None   # 存储更新线程句柄
        self.update_try_time = 0 # 更新尝试次数
        self.scan_pending = False # 是否有待执行的扫描任务
        self.scan_thread = None # 扫描数据线程句柄
        self.analysis_time = -1 # 每日收盘后分析次数

    def trigger_history_update(self):
        """启动更新线程，包含容错和并发控制"""
        
        # 1. 并发控制：如果已经在更新中，直接跳过，等待下次调度检查
        if self.update_thread is not None and self.update_thread.is_alive():
            print(">>> [Scheduler] 历史数据更新正在进行中，跳过本次触发...")
            return
        if self.scan_thread is not None and self.scan_thread.is_alive():
            print(">>> [Scheduler] 扫描数据正在进行中，跳过本次触发...")
            return

        # 定义线程任务函数
        def update_task():
            try:
                # 调用 data_manager 的更新接口
                result_msg = data_manager.update_today_data_tushare()
                
                # 简单判断是否成功 (根据 data_manager 的返回字符串)
                if "完成" in result_msg:
                    print(f">>> [Scheduler] 更新成功: {result_msg}")
                    self.update_pending = False # ✅ 成功，取消挂起状态
                    # send_notification("AI 数据仓库", f"每日数据更新成功\n{result_msg}")
                    wxpusher.send_wechat_msg("每日数据更新成功", result_msg)

                    self.scan_pending = True
                    self.scan_thread = threading.Thread(target=scan_task, name="ScanStocksThread")
                    self.scan_thread.start()
                elif "今日无数据" in result_msg:
                    print(f">>> [Scheduler] 更新异常: {result_msg}")
                    self.update_try_time = self.update_try_time + 1
                    if self.update_try_time >= UPDATE_TRY_TIME:
                        self.update_pending = False # ✅ 成功，取消挂起状态
                        wxpusher.send_wechat_msg("每日数据更新异常，停止更新", result_msg)
                    else:
                        self.update_pending = True  # ❌ 失败，保持挂起，下次重试
                        wxpusher.send_wechat_msg(f"每日数据更新异常，稍后重试({self.update_try_time}/{UPDATE_TRY_TIME})", result_msg)
                else:
                    print(f">>> [Scheduler] 更新返回异常: {result_msg}")
                    self.update_pending = True  # ❌ 失败，保持挂起，下次重试
                    wxpusher.send_wechat_msg("每日数据更新失败", result_msg)
            except Exception as e:
                print(f">>> [Scheduler] 更新过程出错: {e}")
                self.update_pending = True      # ❌ 异常，保持挂起，下次重试
                wxpusher.send_wechat_msg("每日数据更新过程出错", e)
        def scan_task():
            try:
                data = portfolio.load_portfolio()
                holdings = data.get('holdings', [])
                buy_date_str =  datetime.now().date().strftime("%Y-%m-%d")

                append_followed_cnt = 0
                append_followed_data = []

                strategys = ["overnight", "limit_up"]
                for strategy in strategys:
                    results = data_manager.screen_stocks_local(strategy)
                    for h in results[:10]: # 取前10只
                        symbol = h.get('symbol')
                        existing = next((h for h in holdings if h['symbol'] == symbol), None)
                        if not existing:
                            price = data_manager.get_realtime_quote(h['symbol'])['price']
                            if price > 0 :
                                name = data_manager.get_stock_name(symbol)
                                portfolio.upsert_holding(symbol, name, 0, 0, 0, buy_date_str)
                                append_followed_cnt += 1
                                append_followed_data.append(h)
                self.scan_pending = False
                print(f">>> [Scheduler] 筛选结束 新增关注股票 {append_followed_cnt}只")
                wxpusher.send_wechat_msg(f"收盘数据扫描结束", f"新增关注股票{append_followed_cnt}只:\n{str(append_followed_data)}")
            except Exception as e:
                print(f">>> [Scheduler] 扫描数据过程出错: {e}")
                self.scan_pending = True      # ❌ 异常，保持挂起，下次重试
        
        if self.update_pending:
            self.update_thread = threading.Thread(target=update_task, name="HistoryUpdateThread")
            self.update_thread.start()
        elif self.scan_pending:
            self.scan_thread = threading.Thread(target=scan_task, name="ScanStocksThread")
            self.scan_thread.start()


# 实例化全局上下文
scheduler_update_history_ctx = SchedulerUpdateHistoryContext()


LOG_DIR = "logs"
def write_signal_log(message):
    today = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(LOG_DIR, f"ai_signals_{today}.txt"), 'a', encoding='utf-8') as f:
        f.write(f"{message}\n")

def gen_holding_stocks_info():
    config = data_manager.load_ai_config()
    strategy = config.get('strategy', 'Dynamic-Market-Adjusted')
    
    try:
        data = portfolio.load_portfolio()
        # holdings = [h for h in data.get('holdings', []) if h['total_shares'] > 0]
        holdings = data.get('holdings', [])
        cash = data.get('cash', 0.0)
    except: return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n--- [AI] 任务启动 ({timestamp}) ---")

    stocks_data_list = []
    total_val = 0.0
    
    for h in holdings:
        symbol = h['symbol']
        rt = data_manager.get_realtime_quote(symbol)
        price = rt.get('price', 0.0)
        # if price <= 0.01: continue
        
        val = price * h['total_shares']
        total_val += val
        
        try:
            hist = data_manager.load_local_history(symbol)
            indi = data_manager.calculate_indicators(hist)
            last = indi.iloc[-1] if not indi.empty else {}
            
            stocks_data_list.append({
                "symbol": symbol,
                "name": rt.get('name', h['name']),
                "total_shares": h['total_shares'],
                "cost": h['cost'],
                "avail_shares": h['total_shares'] - h['locked_shares'],

                "current_price": price,
                "market_value": val,
                "indicators": {
                    "MA5": float(last.get('close', 0)),
                    "RSI": float(last.get('RSI', 0)),
                    "MACD_Cross": int(last.get('MACD_Cross', 0))
                }
            })
        except: continue

    summary = {
        "cash": cash,
        "total_assets": cash + total_val,
        "strategy": strategy
    }
    return summary, stocks_data_list

def analysising_stocks_job():
    summary, stocks_data_list = gen_holding_stocks_info()
    if not stocks_data_list: return

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{timestamp}: 正在调用 AI...")

        res = {"stocks_analysis": [], "market_opportunities": []}
        system_prompt, user_prompt = ai_engine.generate_batch_prompt(summary, stocks_data_list)
        try:
            result = ai_engine.call_ai(system_prompt, user_prompt)
            if "stocks_analysis" not in result:
                if isinstance(result, list): result = {"stocks_analysis": result}
            res = result
        except Exception as e:
            print(f"AI Error: {e}")
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output_info = ""

        sorted_analysis_list = sorted(res.get("stocks_analysis", []), key=lambda x:x.get('quantity',0), reverse=True)
        for d in sorted_analysis_list:
            act = d.get("action")
            symbol = d.get('symbol')
            name = d.get('name', '')
            risk = d.get('risk', '')
            price_range = d.get('price_range', '')
            quantity = d.get('quantity', 0)
            reason = d.get('reason', '')
            msg = f'''
            **********
            【{act}】{name}({symbol})
            风险提示：{risk}
            价格区间：{price_range}
            操作股数：{quantity}
            理由：{reason}
            **********

'''
            output_info += msg

            if any(symbol == h.get('symbol') for h in stocks_data_list):
                h = next(h for h in stocks_data_list if h.get('symbol') == symbol)
            else:
                continue
            if act in ["SELL", "REDUCE", "CLEAR"]:
                if float(h.get('total_shares')) == 0:
                    if portfolio.add_clear_flag(symbol) >= CLEAR_LIMIT:
                        portfolio.delete_holding(d.get('symbol'))
                        msg = f"***从关注中移除 {d.get('symbol')}\n"
                        output_info += msg
            elif act in ["BUY", "HOLD"]:
                if float(h.get('total_shares')) == 0:
                    portfolio.reset_clear_flag(symbol)
        for d in res.get("market_opportunities", []):
            recommendation = d.get('recommendation', 0)
            name = d.get('name', '')
            symbol = d.get('symbol')
            risk = d.get('risk', '')
            price_range = d.get('price', '')
            target_price_range = d.get('target_price', '')
            quantity = d.get('quantity', 0)
            reason = d.get('reason', '')

            msg = f'''
            **********
            【推荐({recommendation})】{name}({symbol})
            风险提示：{risk}
            建议买入价格区间：{price_range}
            预估止盈价格区间：{target_price_range}
            操作股数：{quantity}
            理由：{reason}
            **********

            '''
            output_info += f"{timestamp}: {msg}\n"
        if len(output_info) > 0: 
            wxpusher.send_wechat_msg(f"AI 信号: {timestamp}", output_info)
            write_signal_log(f"{timestamp}AI决策结果：\n{output_info}\n")
        print(f"{timestamp}: AI 决策完成!")
                
    except Exception as e:
        print(f"执行失败: {e}")

def analysis_stock_market_after_close():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp}: 收盘后消息面分析...")
    try:
        res = None
        system_prompt, user_prompt = ai_engine.generate_daily_recommand_stock_prompt()
        try:
            result = ai_engine.call_ai(system_prompt, user_prompt)
            if "stocks_analysis" not in result:
                if isinstance(result, list): result = {"stocks_analysis": result}
            res = result
        except Exception as e:
            print(f"AI Error: {e}")
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        buy_date_str =  datetime.now().date().strftime("%Y-%m-%d")

        output_info = ""
        sorted_analysis_list = res.get("stocks_analysis", [])
        for d in sorted_analysis_list:
            symbol = d.get('symbol')
            name = d.get('name', '')
            sector = d.get('sector', '')
            real_price = data_manager.get_realtime_quote(symbol)['price']
            price = d.get('current_price', 0)
            price_range = d.get('price_range', '')
            new_events = d.get('news_events', [])
            reason = d.get('reason', '')
            risk = d.get('risk', '')

            news_msg = "\n"
            for e in new_events:
                news_msg += f"【{e.get('source_url', '')}】\n{e.get('content', '')}"

            msg = f'''
            **********
            {name}({symbol})
            所属板块：{sector}
            当前价格：{price}
            查询价格: {real_price}
            预期止盈价格区间：{price_range}
            消息源：{news_msg}
            理由：{reason}
            风险提示：{risk}
            **********

'''
            output_info += msg
            if real_price > 0 :
                portfolio.upsert_holding(symbol, name, 0, 0, 0, buy_date_str)
        if len(output_info) > 0: 
            wxpusher.send_wechat_msg(f"当日行情分析: {timestamp}", output_info)
            write_signal_log(f"{timestamp}当日行情分析：\n{output_info}\n")
        print(f"{timestamp}: 当日行情分析完成!")
    except Exception as e:
        print(f"执行失败: {e}")

def execute_auto_scheduler():
    global scheduler_update_history_ctx
    curr_is_market_open, curr_is_market_break = is_market_open()
    
    if curr_is_market_open:
        analysising_stocks_job()
        scheduler_update_history_ctx.was_market_open = True
        scheduler_update_history_ctx.update_pending = False # 强制结束，防止历史数据更新任务一直挂起
        scheduler_update_history_ctx.scan_pending = False
        scheduler_update_history_ctx.update_try_time = 0
        scheduler_update_history_ctx.analysis_time = -1
    elif curr_is_market_break:
        scheduler_update_history_ctx.was_market_open = True
        scheduler_update_history_ctx.update_pending = False # 强制结束，防止历史数据更新任务一直挂起
        scheduler_update_history_ctx.scan_pending = False
        scheduler_update_history_ctx.update_try_time = 0
        scheduler_update_history_ctx.analysis_time = -1
    else:
        if datetime.now().hour >= 16: # 延迟到下午4点后再更新数据，因为TuShare在3点多大概率更新不到
            if scheduler_update_history_ctx.was_market_open or scheduler_update_history_ctx.update_pending or scheduler_update_history_ctx.scan_pending:
                if scheduler_update_history_ctx.was_market_open:
                    scheduler_update_history_ctx.was_market_open = False
                    scheduler_update_history_ctx.update_pending = True
                    scheduler_update_history_ctx.update_try_time = 0
                scheduler_update_history_ctx.trigger_history_update()
        if datetime.now().hour >= 20: # 每天晚上8点后再分析
            if scheduler_update_history_ctx.analysis_time < 0:
                scheduler_update_history_ctx.analysis_time = 5
            if scheduler_update_history_ctx.analysis_time > 0:
                scheduler_update_history_ctx.analysis_time = scheduler_update_history_ctx.analysis_time - 1
                

def start_scheduler():
    config = data_manager.load_ai_config()
    period = config.get('period_minutes', 30)
    if TEST_SWITCH:
        analysising_stocks_job()
    else:
        scheduler = BlockingScheduler()
        scheduler.add_job(execute_auto_scheduler, 'interval', minutes=period, start_date=datetime.now())
        print(f"调度器启动，周期 {period} 分钟")
        execute_auto_scheduler()
        try: scheduler.start()
        except: pass

if __name__ == '__main__':
    start_scheduler()