import requests
import json
import data_manager

def send_wechat_msg(title, content):
    setting = data_manager.load_settings()
    APP_TOKEN = setting.get("wxpusher_token", "")
    YOUR_UID = setting.get("wxpusher_uids", "").split(",")
    url = "https://wxpusher.zjiecode.com/api/send/message"
    if len(APP_TOKEN) == 0 or len(YOUR_UID) == 0:
        raise ValueError("请先配置 wxpusher_token 和 wxpusher_uids 参数")
    data = {
        "appToken": APP_TOKEN,
        "content": content,
        "summary": title,
        "contentType": 1,
        "uids": YOUR_UID
    }
    response = requests.post(url, json=data)
    return response.json()

# 在脚本最后添加
if __name__ == "__main__":
    # 你的脚本代码...
    result = "脚本运行结果..."
    
    # 发送到微信
    send_wechat_msg(f"脚本执行完成：{result}")
    print("结果已发送到微信！")