import openai
import json, re
import data_manager

def generate_batch_prompt(portfolio_summary, stocks_data):
    """
    仅生成 Prompt 字符串，用于调试或发送
    """
    strategy = portfolio_summary.get('strategy', 'Dynamic-Market-Adjusted')
    cash = portfolio_summary.get('cash', 0)
    total_assets = portfolio_summary.get('total_assets', 1)
    
    if strategy == "High-Risk/High-Reward":
        strategy_desc = (
            "【激进策略】\n"
            "- 目标: 追求短期爆发，捕捉龙头妖股。\n"
            "- 风控: 单票上限40%。\n"
            "- **止损**: 亏损超过 -8% 坚决止损。\n"
            "- **止盈**: 盈利超过 +20% 后若出现技术面走弱(如MACD死叉)则分批止盈。"
        )
        max_pos_limit = 40
    elif strategy == "Low-Risk/Low-Yield":
        strategy_desc = (
            "【稳健策略】\n"
            "- 目标: 保本增值，偏好低估值蓝筹和高股息。\n"
            "- 风控: 单票上限15%。\n"
            "- **止损**: 亏损超过 -5% 立即止损，严禁扛单。\n"
            "- **止盈**: 盈利 +10% 左右即可考虑逐步落袋，不贪婪。"
        )
        max_pos_limit = 15
    else:
        strategy_desc = (
            "【动态均衡策略】\n"
            "- 目标: 兼顾成长与风控，跟随市场热点轮动。\n"
            "- 风控: 单票上限30%。\n"
            "- **止损**: 亏损 -6% 至 -8% 区间触发止损。\n"
            "- **止盈**: 结合技术指标，若RSI超买(>80)或高位放量滞涨，建议止盈。"
        )
        max_pos_limit = 30

    enriched_stocks = []
    for s in stocks_data:
        cost = s.get('cost_price', 0.0)
        curr = s.get('current_price', 0.0)
        pnl_pct = 0.0
        if cost > 0:
            pnl_pct = (curr - cost) / cost * 100
        
        # 注入计算好的字段
        s['pnl_ratio'] = f"{pnl_pct:.2f}%" 
        enriched_stocks.append(s)

    holdings_json = json.dumps(enriched_stocks, ensure_ascii=False, indent=2)
    
    user_prompt = f"""
    请根据以下账户状态和持仓数据，进行全面的投资决策分析。

    【账户概况】
    - 策略风格: {strategy} ({strategy_desc})
    - 总资产: {total_assets} 元
    - 可用现金: {cash} 元
    - 单票仓位上限: {max_pos_limit}% (约为 {total_assets * max_pos_limit / 100:.0f} 元)

    【当前持仓数据】
    {holdings_json}

    【任务要求】
    1. **持仓诊断(核心)**: 遍历上述每一只股票。
       - 计算其当前仓位占比。必须关注 `pnl_ratio` (盈亏率) 、 `cost_price` (成本价)、`avail_shares` (当前可交易股数)、`shares`(持有总股数)。
       - 结合 JSON 中的 `indicators` (MACD, RSI, MA5) 判断趋势。MACD_Cross=1 为金叉(买入/持有信号)，-1 为死叉(卖出/减仓信号)。
       - 如果当前仓位超过 {max_pos_limit}%，必须建议减仓 (REDUCE)。
       - 如果技术面死叉或严重破位或顶背离，建议卖出 (SELL) 或清仓 (CLEAR)。
       - 严格对照上述策略中的【止损线】，如果亏损幅度触及止损线，除非有极强的反转信号(如底背离金叉)，否则必须建议 SELL/CLEAR。
       - 如果趋势良好且仓位不足，可建议加仓 (BUY)。
       - 对于 BUY/SELL 操作，请给出建议的 **价格区间 (price_range)** (例如: "20.50-20.80")。
    
    2. **机会发现 (Market Opportunities)**: 
       - 基于你对中国股市板块轮动和近期（截止你训练数据知识库）的热门方向（如科技、新能源、中特估等），结合当前策略。
       - 如果上述持仓中有表现不佳的股票，请建议是否应该更换。
       - 必须关注账户`可用现金`是否足够买入新股，买入后仓位占比是否合理。
       - 推荐 3-5 个你认为值得关注的比目前持仓更有盈利机会的潜力股票或具体概念（请提供具体的板块名称、具体代码和选股逻辑）。
       - *注意*: 如果没有足够信心，可以返回空列表。

    【输出格式】
    必须是合法的 JSON 对象，不包含 Markdown 格式：
    {{
        "holdings_analysis": [
            {{
                "symbol": "股票代码",
                "name": "股票名称",
                "action": "BUY/SELL/HOLD/REDUCE/CLEAR",
                "quantity": 建议交易股数 (100的整数倍),
                "price_range": "建议买卖价格区间 (字符串)",
                "reason": "简短理由 (包含技术面和仓位逻辑)"
            }}
        ],
        "market_opportunities": [
            {{
                "symbol": "建议关注的代码或板块名",
                "name": "名称",
                "price": "建议买入价格区间 (字符串)",
                "quantity": 建议买入股数 (100的整数倍),
                "recommendation": 推荐度（1-100）,
                "reason": "推荐理由及潜在的买入逻辑"
            }}
        ]
    }}
    """
    return "你是一名A股顶级基金经理。请只输出JSON。", user_prompt

def get_batch_decision(portfolio_summary, stocks_data):
    """
    构建 Prompt 并调用 AI
    """
    settings = data_manager.load_settings()
    api_key = settings.get("api_key")
    base_url = settings.get("base_url")
    model_name = settings.get("model_name")
    
    if not api_key: raise ValueError("未配置 API Key")

    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    
    # 1. 生成 Prompt
    system_prompt, user_prompt = generate_batch_prompt(portfolio_summary, stocks_data)
    
    # 2. 调用 API
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        raw_text = response.choices[0].message.content
        
        # 3. 解析
        json_match = re.search(r'\{.*\}', raw_text.strip(), re.DOTALL)
        json_str = json_match.group(0) if json_match else raw_text.strip()
        json_str = json_str.replace("'", '"')
        
        result = json.loads(json_str)
        if "holdings_analysis" not in result:
             if isinstance(result, list): result = {"holdings_analysis": result}
        
        return result

    except Exception as e:
        print(f"AI Error: {e}")
        return {"holdings_analysis": [], "market_opportunities": []}