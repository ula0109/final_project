
from flask import Flask, request, jsonify, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import *
import google.generativeai as genai
import os
import re
import json
from datetime import datetime

app = Flask(__name__)

# ==== LINE 與 Gemini API 設定 ====
LINE_CHANNEL_ACCESS_TOKEN = 'HLuTgqylcDY6t20wEFfTKXonspRbYfmcbay/4c8mPi5xzknBtmh4lA8HJUpSEjZcFWXnJAFvXqNhuIQym69zVG TgnW16fITsnkulP9eAC7MHCa2O0n8vvKcNaeJ9dVyCsk6NrJnbfk56o7VFs21+nwdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = '216e320cbec53650dcddf1213a819201'
GEMINI_API_KEY = 'AIzaSyDEsssaqNilIi66LhfpElF8aPyVspZjpug'

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(model_name="models/gemini-1.5-pro-latest")

# ==== 資料儲存區 ====
history = []
calendar_data = {}  # {'user_id': {'2024-06-20': ['事件1', '事件2']}}

# ==== 行事曆處理函式 ====

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
            if not user_calendar[date_str]:  # 沒有剩下的行程
                del user_calendar[date_str]
            return True, f"🗑️ 已刪除 {date_str} 的「{event_text}」"
        except ValueError:
            return False, f"❌ 找不到「{event_text}」在 {date_str}"
    else:
        del user_calendar[date_str]
        return True, f"🗑️ 已刪除 {date_str} 所有行程"

# ==== LINE Webhook ====

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

    if isinstance(event.message, TextMessage):
        msg = event.message.text.strip()
        history.append({'user': user_id, 'message': msg})
        today_str = datetime.now().strftime("%Y-%m-%d")

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
            if schedule:
                reply = f"📅 今天你有以下行程：\n" + "\n".join(f"- {s}" for s in schedule)
            else:
                reply = "📭 今天沒有任何行程喔～"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # === 查詢特定日期行程 ===
        match = re.match(r"我(\d{1,2})[月/](\d{1,2})日有什麼(行程|事)\？?", msg)
        if match:
            month, day = match.groups()[:2]
            query_date = f"{datetime.now().year}-{int(month):02d}-{int(day):02d}"
            schedule = get_user_schedule(user_id, query_date)
            if schedule:
                reply = f"📅 {query_date} 你有以下行程：\n" + "\n".join(f"- {s}" for s in schedule)
            else:
                reply = f"📭 {query_date} 沒有安排任何行程喔"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # === 刪除行程（指定日期+事件 或 全部）===
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

        # === 刪除今天行程 ===
        if msg in ["刪除今天的行程", "刪除今天行程"]:
            success, reply = delete_event(user_id, today_str)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # === LINE 其他訊息 ===
        elif msg == "位置":
            location = LocationSendMessage(
                title="元智大學",
                address="320桃園市中壢區遠東路135號",
                latitude=24.970079,
                longitude=121.267750
            )
            line_bot_api.reply_message(event.reply_token, location)
            return

        # === Gemini AI 回覆 ===
        try:
            response = model.generate_content(msg)
            ai_text = getattr(response, 'text', '⚠️ AI 沒有回應任何內容').strip()
        except Exception as e:
            ai_text = f"❌ AI 發生錯誤：{str(e)}"
        history.append({'bot': ai_text})
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ai_text))


# ==== 查詢與清除歷史紀錄 ====
@app.route('/history', methods=['GET'])
def get_history():
    return jsonify(history)

@app.route('/history', methods=['DELETE'])
def delete_history():
    history.clear()
    return jsonify({"message": "history cleared"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
