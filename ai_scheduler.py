import json
import os
import sys
import time
import data_manager
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from plyer import notification 

try:
    import portfolio
    import data_manager
    import ai_engine 
except ImportError as e:
    print(f"模块导入错误: {e}")
    sys.exit(1)

def send_notification(title, message):
    try: notification.notify(title=title, message=message, app_name='SmartQuant Pro AI')
    except: pass

LOG_DIR = "logs"
def write_signal_log(message):
    today = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(LOG_DIR, f"ai_signals_{today}.txt"), 'a', encoding='utf-8') as f:
        f.write(f"{message}\n")

def gen_ai_executer_info():
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

def execute_ai_decision():
    summary, stocks_data_list = gen_ai_executer_info()
    if not stocks_data_list: return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        print(f"{timestamp}: 正在调用 AI...")
        res = ai_engine.get_batch_decision(summary, stocks_data_list)
        
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
            write_signal_log(f"{output_info}\n")
        print(f"{timestamp}: AI 决策完成!")
                
    except Exception as e:
        print(f"执行失败: {e}")

def start_scheduler():
    config = data_manager.load_ai_config()
    period = config.get('period_minutes', 30)
    scheduler = BlockingScheduler()
    scheduler.add_job(execute_ai_decision, 'interval', minutes=period, start_date=datetime.now())
    print(f"调度器启动，周期 {period} 分钟")
    execute_ai_decision()
    try: scheduler.start()
    except: pass

if __name__ == '__main__':
    start_scheduler()