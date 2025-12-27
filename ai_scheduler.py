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
        self.scan_pending = False # 是否有待执行的扫描任务
        self.scan_thread = None # 扫描数据线程句柄

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
                    send_notification("AI 数据仓库", f"每日数据更新成功\n{result_msg}")
                    wxpusher.send_wechat_msg("每日数据更新成功", result_msg)

                    self.scan_pending = True
                    self.scan_thread = threading.Thread(target=scan_task, name="ScanStocksThread")
                    self.scan_thread.start()
                elif "今日无数据" in result_msg:
                    print(f">>> [Scheduler] 更新异常: {result_msg}")
                    self.update_pending = False # ✅ 成功，取消挂起状态
                    wxpusher.send_wechat_msg("每日数据更新异常，停止更新", result_msg)
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
                strategy = "overnight"
                # strategy = "limit_up"
                results = data_manager.screen_stocks_local(strategy)
                if len(results) > 0:
                    print(f">>> [Scheduler] 筛选出 {len(results)} 只股票")
                    wxpusher.send_wechat_msg(f"策略{strategy}扫描结果", str(results))
                else:
                    print(f">>> [Scheduler] 没有符合条件的股票")
                    wxpusher.send_wechat_msg(f"策略{strategy}扫描结果", "没有符合条件的股票")
                self.scan_pending = False
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
                "current_price": price,
                "cost_price": h['cost'],
                "shares": h['total_shares'],
                "market_value": val,
                "avail_shares": h['total_shares'] - h['locked_shares'],
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
        res = ai_engine.get_batch_decision(summary, stocks_data_list)
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output_info = ""
        for d in res.get("stocks_analysis", []):
            act = d.get("action")
            if act in ["BUY", "SELL", "REDUCE", "CLEAR"]:
                msg = f"【{act}】{d.get('name', '')}({d.get('symbol')}) 价格区间：{d.get('price_range','')}；操作股数：{d.get('quantity',0)}\n{d.get('reason')}"
                send_notification(f"AI 信号: {act} {d.get('symbol')}", msg)
                output_info += f"{timestamp}: {msg}\n"
                print(f"{timestamp}: {msg}")
        for d in res.get("market_opportunities", []):
            msg = f"【推荐({d.get('recommendation',0)})】{d.get('name', '')}({d.get('symbol')}) 价格区间：{d.get('price')}；操作股数：{d.get('quantity',0)}\n{d.get('reason')}"
            send_notification(f"AI 信号: 推荐 {d.get('symbol')}", msg)
            output_info += f"{timestamp}: {msg}\n"
            print(f"{timestamp}: {msg}")
        if len(output_info) > 0: 
            wxpusher.send_wechat_msg(f"AI 信号: {timestamp}", output_info)
            write_signal_log(f"{output_info}\n")
        print(f"{timestamp}: AI 决策完成!")
                
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
    elif curr_is_market_break:
        scheduler_update_history_ctx.was_market_open = True
        scheduler_update_history_ctx.update_pending = False # 强制结束，防止历史数据更新任务一直挂起
        scheduler_update_history_ctx.scan_pending = False
    else:
        if scheduler_update_history_ctx.was_market_open or scheduler_update_history_ctx.update_pending or scheduler_update_history_ctx.scan_pending:
            if scheduler_update_history_ctx.was_market_open:
                scheduler_update_history_ctx.was_market_open = False
                scheduler_update_history_ctx.update_pending = True
            scheduler_update_history_ctx.trigger_history_update()

def start_scheduler():
    config = data_manager.load_ai_config()
    period = config.get('period_minutes', 30)
    scheduler = BlockingScheduler()
    scheduler.add_job(execute_auto_scheduler, 'interval', minutes=period, start_date=datetime.now())
    print(f"调度器启动，周期 {period} 分钟")
    execute_auto_scheduler()
    try: scheduler.start()
    except: pass

if __name__ == '__main__':
    start_scheduler()