import json
import os
from datetime import datetime, timedelta

DATA_DIR = "data"
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def load_portfolio():
    """加载并处理 T+1 逻辑，增加旧数据结构兼容性处理"""
    if not os.path.exists(PORTFOLIO_FILE):
        return {"cash": 100000.0, "holdings": []}
    
    try:
        with open(PORTFOLIO_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        today = datetime.now().date()
        migrated = False # 标记是否发生了数据迁移
        
        # --- 兼容性检查和迁移 ---
        new_holdings = []
        for h in data.get('holdings', []):
            
            # --- 数据兼容与初始化 ---
            if 'total_shares' not in h: h['total_shares'] = h.get('shares', 0)
            if 'locked_shares' not in h: h['locked_shares'] = 0
            if 'locked_date' not in h: h['locked_date'] = "2000-01-01"
            if 'cost' not in h: h['cost'] = 0.0 # 确保成本字段存在
            
            # --- T+1 刷新逻辑 (使用 locked_shares) ---
            avail_shares = h['total_shares']
            
            if h['total_shares'] > 0:
                locked_date = datetime.strptime(h['locked_date'], "%Y-%m-%d").date()
                
                # 如果锁定日期是今天，则锁定股数不可用
                if locked_date == today:
                    # 可用股数 = 总股数 - 锁定的股数 (即 T0 买入的股数)
                    avail_shares = max(0, h['total_shares'] - h['locked_shares'])
                else:
                    # 否则，锁定股数清零，所有股数均可用
                    h['locked_shares'] = 0
                    
            h['avail_shares'] = avail_shares
            new_holdings.append(h)
            
        data['holdings'] = new_holdings
        # 不再进行 save_portfolio(data)，避免频繁写入
        
        return data
        
    except Exception as e:
        # 如果 JSON 文件完全损坏或格式错误，则返回空
        print(f"致命错误: 无法解析 portfolio.json 文件。将返回空持仓。错误: {e}")
        return {"cash": 100000.0, "holdings": []}

def save_portfolio(data):
    with open(PORTFOLIO_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def update_cash(amount):
    """手动维护现金"""
    data = load_portfolio()
    data['cash'] = float(amount)
    save_portfolio(data)

def upsert_holding(symbol, name, total_shares, avail_shares, cost, buy_date_str):
    """
    新增或更新持仓 (库存校准模式)
    """
    data = load_portfolio()
    holdings = data['holdings']
    
    # 计算锁定股数 (T0 买入股数)
    locked_qty = int(total_shares) - int(avail_shares)
    
    if total_shares <= 0:
        # 清仓/删除操作
        data['holdings'] = [h for h in holdings if h['symbol'] != symbol]
        save_portfolio(data)
        return True
        
    # 查找是否存在
    existing = next((h for h in holdings if h['symbol'] == symbol), None)
    
    if existing:
        # 更新逻辑
        existing['name'] = name
        existing['total_shares'] = int(total_shares)
        existing['cost'] = float(cost)
        existing['locked_date'] = buy_date_str
        # 锁定股数和日期
        if locked_qty > 0:
            existing['locked_shares'] = locked_qty
            
        else:
            # 如果锁定股数 <= 0，则全部可用
            existing['locked_shares'] = 0 
            
    else:
        # 新增逻辑
        new_item = {
            "symbol": symbol,
            "name": name,
            "total_shares": int(total_shares),
            "cost": float(cost),
            "locked_shares": max(0, locked_qty),
            "locked_date": buy_date_str,
            "avail_shares": int(avail_shares) # 仅用于初始化，下次加载时会被重算
        }
        holdings.append(new_item)
        
    save_portfolio(data)
    return True

def delete_holding(symbol):
    data = load_portfolio()
    data['holdings'] = [h for h in data['holdings'] if h['symbol'] != symbol]
    save_portfolio(data)
    return True