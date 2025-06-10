from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import google.generativeai as genai
from datetime import datetime
import re
import os
import requests
import feedparser

# Flask 初始化
app = Flask(__name__)

# 環境變數讀取（建議從 Render 設定）
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Gemini 初始化
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(model_name="models/gemini-1.5-pro-latest")

# 行事曆與歷史紀錄
calendar_data = {}  # {'user_id': {'YYYY-MM-DD': ['行程1', '行程2']}}
history = []

def get_yahoo_news():
    feed = feedparser.parse("https://tw.news.yahoo.com/rss")
    news_items = feed.entries[:3]
    reply = "📰 今日 Yahoo 即時新聞：\n"
    for item in news_items:
        reply += f"\n🔹 {item.title}\n👉 {item.link}\n"
    return reply


# === 行事曆函式 ===

def parse_calendar_input(text):
    match = re.match(r"(\d{1,2})[月/](\d{1,2})日?\s*(.+)", text)
    if match:
        month, day, event = match.groups()
        now = datetime.now()
        date_str = f"{now.year}-{int(month):02d}-{int(day):02d}"
        return date_str, event.strip()
    return None, None

def get_user_schedule(user_id, date_str):
    user_calendar = calendar_data.get(user_id, {})
    return user_calendar.get(date_str, [])

def delete_event(user_id, date_str, event_text=None):
    if user_id not in calendar_data:
        return False, "⚠️ 找不到你的行程資料。"
    user_calendar = calendar_data[user_id]
    if date_str not in user_calendar:
        return False, f"📭 {date_str} 沒有任何行程。"
    if event_text:
        try:
            user_calendar[date_str].remove(event_text)
            if not user_calendar[date_str]:
                del user_calendar[date_str]
            return True, f"🗑️ 已刪除 {date_str} 的「{event_text}」"
        except ValueError:
            return False, f"❌ 找不到「{event_text}」在 {date_str}"
    else:
        del user_calendar[date_str]
        return True, f"🗑️ 已刪除 {date_str} 所有行程"

# === LINE Bot Webhook ===

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    today_str = datetime.now().strftime("%Y-%m-%d")
    history.append({'user': user_id, 'message': msg})

    # === 指令提示 ===
    if msg == "日曆":
        sample_text = (
            "🗓️ 行事曆使用範本：\n\n"
            "➕ 新增行程：\n"
            "EX:6月20日 看牙醫\n\n"
            "🔍 查詢行程：\n"
            "EX:今天有什麼行程？\n"
            "EX:我6月20日有什麼事？\n"
            "🗑️ 刪除行程：\n"
            "EX:刪除6月20日 看牙醫\n"
            "EX:刪除6月20日全部\n"
            "EX:刪除今天的行程"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=sample_text))
        return

    
   # === 查詢今天新聞 ===
     if msg == "新聞":
     reply = get_yahoo_news()
     line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
     return


    # === 新增行程 ===
    date_str, event_content = parse_calendar_input(msg)
    if date_str and event_content:
        calendar_data.setdefault(user_id, {})
        calendar_data[user_id].setdefault(date_str, []).append(event_content)
        reply = f"✅ 已幫你記下 {date_str}：{event_content}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # === 查詢今天行程 ===
    if msg in ["今天有什麼行程？", "今天要做什麼？"]:
        schedule = get_user_schedule(user_id, today_str)
        reply = (
            f"📅 今天你有以下行程：\n" + "\n".join(f"- {s}" for s in schedule)
            if schedule else "📭 今天沒有任何行程喔～"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

  
    # === 查詢指定日期行程 ===
    match = re.match(r"我(\d{1,2})[月/](\d{1,2})日有什麼(行程|事)\？?", msg)
    if match:
        month, day = match.groups()[:2]
        query_date = f"{datetime.now().year}-{int(month):02d}-{int(day):02d}"
        schedule = get_user_schedule(user_id, query_date)
        reply = (
            f"📅 {query_date} 你有以下行程：\n" + "\n".join(f"- {s}" for s in schedule)
            if schedule else f"📭 {query_date} 沒有安排任何行程喔"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # === 刪除行程 ===
    delete_match = re.match(r"刪除(\d{1,2})[月/](\d{1,2})日(.*)", msg)
    if delete_match:
        month, day, content = delete_match.groups()
        date_str = f"{datetime.now().year}-{int(month):02d}-{int(day):02d}"
        content = content.strip()
        if content == "全部":
            success, reply = delete_event(user_id, date_str)
        else:
            success, reply = delete_event(user_id, date_str, content)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if msg in ["刪除今天的行程", "刪除今天行程"]:
        success, reply = delete_event(user_id, today_str)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

       

    # === Gemini AI 回覆 ===
    try:
        response = model.generate_content(msg)
        ai_text = getattr(response, 'text', '⚠️ AI 沒有回應任何內容').strip()
    except Exception as e:
        ai_text = f"❌ AI 發生錯誤：{str(e)}"

    history.append({'bot': ai_text})
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_text))

# === 可選：查詢/清除歷史紀錄 API ===

@app.route('/history', methods=['GET'])
def get_history():
    return jsonify(history)

@app.route('/history', methods=['DELETE'])
def delete_history():
    history.clear()
    return jsonify({"message": "history cleared"})

# === 執行 Flask 應用 ===

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
