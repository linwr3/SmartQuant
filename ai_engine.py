import openai
import json, re
import data_manager

def call_ai(system_prompt, user_prompt):
    """
    构建 Prompt 并调用 AI
    """
    settings = data_manager.load_settings()
    api_key = settings.get("api_key")
    base_url = settings.get("base_url")
    model_name = settings.get("model_name")
    
    if not api_key: raise ValueError("未配置 API Key")
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

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
    return result

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
    followed_stocks = []
    for s in stocks_data:
        if s.get('shares', 0) == 0:
            followed_stocks.append({
                'symbol': s.get('symbol'),
                'name': s.get('name'),
                'current_price': s.get('current_price', 0.0),
                'indicators': s.get('indicators'),
            })
        else:
            cost = s.get('cost_price', 0.0)
            curr = s.get('current_price', 0.0)
            pnl_pct = 0.0
            if cost > 0:
                pnl_pct = (curr - cost) / cost * 100
            
            # 注入计算好的字段
            s['pnl_ratio'] = f"{pnl_pct:.2f}%" 
            enriched_stocks.append(s)

    holdings_json = json.dumps(enriched_stocks, ensure_ascii=False, indent=2)
    if len(followed_stocks) > 0:
        followed_stocks_json = json.dumps(followed_stocks, ensure_ascii=False, indent=2)
    else:
        followed_stocks_json = "[]"
    
    user_prompt = f"""
    你是一名A股顶级基金经理。[[严格按照要求]]，根据以下账户状态和持仓数据，进行全面的投资决策分析。

    【账户概况】
    - 策略风格: {strategy} ({strategy_desc})
    - 总资产: {total_assets} 元
    - 可用现金: {cash} 元
    - 单票仓位上限: {max_pos_limit}% (约为 {total_assets * max_pos_limit / 100:.0f} 元)

    【当前持仓数据】
    {holdings_json}

    【已跟踪但未持仓的股票】
    {followed_stocks_json}

    【任务要求】
    1. **持仓诊断(核心)**: 必须遍历上述【当前持仓数据】每一只股票。
       - 计算其当前**仓位占比**。必须关注 `pnl_ratio` (盈亏率) 、 `cost_price` (成本价)、`avail_shares` (**当前可交易股数**)、`shares`(持有总股数)。
       - 结合 JSON 中的 `indicators` (MACD, RSI, MA5) 判断趋势。MACD_Cross=1 为金叉(买入/持有信号)，-1 为死叉(卖出/减仓信号)。
       - 结合现在股票实时的 `MACDFS` 、 `分时量` 指标判断短期趋势。
       - 如果当前仓位超过 {max_pos_limit}%，且盈利或反转趋势不明显，必须建议减仓 (REDUCE)。
       - 如果技术面死叉或严重破位或顶背离，建议卖出 (SELL) 或清仓 (CLEAR)。
       - 严格对照上述策略中的【止损线】，如果亏损幅度触及止损线，除非有极强的反转信号(如底背离金叉)，否则必须建议 SELL/CLEAR。
       - 如果趋势良好且仓位不足，可建议加仓 (BUY)。
       - 如果消息面有利好，建议买入 (BUY)。如果消息面有利空，建议卖出防守(SELL)。消息面来源包括但不限于公司财报、财经新闻、财经论坛、财经博客、财经网站、财经APP、小红书评价等。
       - 对于 BUY/SELL 操作，请给出建议的 **价格区间 (price_range)** (例如: "20.50-20.80")和**目前股价**。
       - 分析结果输出到stocks_analysis。
       - 必须严格关注股票当前仓位占比，建议买入必须严格根据现价和可用金额计算买入股数，以及买入后所占仓位和总仓位是否合理。

    2. **关注股票诊断(核心)**：必须遍历上述【已跟踪但未持仓的股票】每一只股票。
       - 必须关注 `current_price` (目前股价) 。
       - 结合 JSON 中的 `indicators` (MACD, RSI, MA5) 判断趋势。MACD_Cross=1 为金叉(买入/持有信号)，-1 为死叉(卖出/减仓信号)。
       - 如果技术面金叉或底背离，建议买入 (BUY)。
       - 如果消息面有利好，建议买入 (BUY)。消息面来源包括但不限于公司财报、财经新闻、财经论坛、财经博客、财经网站、财经APP、小红书评价等。
       - 对于 BUY 操作，请给出建议的 **价格区间 (price_range)** (例如: "20.50-20.80")和**目前股价**。
       - 如果关注股票在市场上表现不佳、或短期内上升趋势不明显、或短期内预期收益率低于策略期望、或消息面上有利空短期内难以修复，请建议清除(CLEAR)。
       - 分析结果输出到stocks_analysis。
    
    3. **机会发现 (Market Opportunities)**: 
       - 基于你对中国股市板块轮动和近期（截止你训练数据知识库）的热门方向（如科技、新能源、中特估等），结合当前策略。
       - 如果上述持仓中有表现不佳的股票，请建议是否应该更换。
       - 必须关注账户`可用现金`是否足够买入新股，买入后仓位占比是否合理。
       - 推荐 3-5 个你认为值得关注的比目前持仓更有盈利机会的潜力股票或具体概念（请提供具体的板块名称、具体代码和选股逻辑）。
       - *注意*: 如果没有足够信心，可以返回空列表。
       - 分析结果输出到market_opportunities。

    【输出格式】
    必须是合法的 JSON 对象，不包含 Markdown 格式：
    {{
        "stocks_analysis": [
            {{
                "symbol": "股票代码",
                "name": "股票名称",
                "action": "BUY/SELL/HOLD/REDUCE/CLEAR",
                "quantity": 建议交易股数 (100的整数倍),
                "price_range": "建议买卖价格区间 (字符串)",
                "current_price": 当前股价(小数点后保留2位的float),
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

def generate_batch_recommand_prompt(stocks_data):
    stocks_data_jsons = json.dumps(stocks_data, ensure_ascii=False, indent=2)
    user_prompt = f"""
    根据用户提供的股票数据，遍历每一只股票，对股票进行多维度的深度分析，判断其投资价值，制定短期和长期的交易策略，并计算推荐评分。

    【股票数据】
    {stocks_data_jsons}

    【任务要求】
    请按照以下逻辑进行思考（不要在输出中展示思考过程，仅输出最终JSON）：
    1. **基本面分析 (Fundamentals):** 评估估值水平 (PE/PB)、行业地位、护城河及盈利能力。
    2. **技术面分析 (Technicals):** 必须关注提供的参数， `close` （当前价格）、 `pct_chg` （涨跌幅），并另外查询获取目前均线位置、成交量及指标状态，判断当前是处于吸筹、拉升、派发还是下跌阶段。
    * *注意：如果数据中缺乏具体技术指标，请尽可能从公司财报、财经新闻、财经论坛、财经博客、财经网站、财经APP等获取。*
    3. **消息面/情绪面 (Sentiment):** 结合提供的近期消息，从公司财报、财经新闻、财经论坛、财经博客、财经网站、财经APP等多方消息源查找相关信息，判断市场情绪是贪婪还是恐慌。
    4. **价格区间** 提供的价格区间必须基于当前价格（Current Price）进行合理的支撑位（Support）和阻力位（Resistance）推算。

    # 推荐度逻辑 (Rating 0-100)
    * **80-100:** 强烈推荐。基本面优秀且技术面出现极佳买点（如缩量回调到位、突破关键阻力）。
    * **60-79:** 谨慎推荐。基本面良好，但技术面需要等待回调或进一步确认。
    * **40-59:** 观望/中性。趋势不明朗，或估值合理但缺乏催化剂。
    * **0-39:** 不推荐/卖出。基本面恶化或技术面破位下跌。

    【输出格式】
    必须是合法的 JSON 对象，不包含 Markdown 格式：
    {{
        "stocks_analysis": [
            "symbol": "股票代码",
            "name": "股票名称",
            "score": "推荐度（0-100）",
            "short_term_strategy": {{
                "action": "建议操作: BUY（买入）/HOLD（观望）",
                "price": "短期适合买入的价格区间（字符串）"
                "target_price": "短期止盈目标价（字符串）",
                "reason": "短期策略理由"
            }},
            "long_term_strategy": {{
                "action": "建议操作: BUY（买入）/HOLD（观望）",
                "price": "理想的长线建仓价格区间（字符串）",
                "target_price": "长线预期止盈目标价格区间（字符串）",
                "reason": "长期策略理由"
            }},
            "analysis_summary": {{
                "fundamental_view": "基本面评价",
                "technical_view": "技术面评价",
                "overall_reasoning": "综合分析理由"
            }},
        ]
    }}
    """
    return "你是一位拥有20年A股实战经验的资深基金经理，擅长“基本面选股+技术面择时”的策略。你精通波浪理论、量价关系以及企业财报分析。同时，你是一个严格的数据分析机器人，输出结果必须严格遵循JSON格式。", user_prompt


def get_batch_decision(portfolio_summary, stocks_data):
    system_prompt, user_prompt = generate_batch_prompt(portfolio_summary, stocks_data)
    try:
        result = call_ai(system_prompt, user_prompt)
        if "stocks_analysis" not in result:
             if isinstance(result, list): result = {"stocks_analysis": result}
        return result
    except Exception as e:
        print(f"AI Error: {e}")
        return {"stocks_analysis": [], "market_opportunities": []}