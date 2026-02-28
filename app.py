# app.py
# -*- coding: utf-8 -*-

import os
import time
import threading
from datetime import datetime

import schedule
from flask import Flask, request, abort
from dotenv import load_dotenv

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource

# ------------------------------------------------------------
# 0) 起動前準備：.env を読み込む（無くても動くけど、初心者は .env 推奨）
# ------------------------------------------------------------
load_dotenv()

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    print("ERROR: 環境変数が足りません。以下を設定してください。")
    print(" - LINE_CHANNEL_SECRET")
    print(" - LINE_CHANNEL_ACCESS_TOKEN")
    print("（.env を作るのが簡単です。手順の例はこのあと説明します）")
    raise SystemExit(1)

# ------------------------------------------------------------
# 1) LINE SDK の基本セット
# ------------------------------------------------------------
handler = WebhookHandler(LINE_CHANNEL_SECRET)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# ------------------------------------------------------------
# 2) ユーザーID保存（Push通知の宛先になる）
#    ※Push通知は「誰に送るか」を明確にする必要があります
#      → だから最初のメッセージで user_id を保存します
# ------------------------------------------------------------
USER_ID_FILE = "user_id.txt"


def load_saved_user_id() -> str | None:
    if not os.path.exists(USER_ID_FILE):
        return None
    try:
        with open(USER_ID_FILE, "r", encoding="utf-8") as f:
            user_id = f.read().strip()
        return user_id if user_id else None
    except Exception as e:
        print(f"WARNING: user_id.txt の読み込みに失敗: {e}")
        return None


def save_user_id_if_needed(user_id: str) -> bool:
    """
    まだ user_id.txt が無い（または空）なら保存する。
    保存したら True、すでに保存済みなら False。
    """
    saved = load_saved_user_id()
    if saved:
        return False

    try:
        with open(USER_ID_FILE, "w", encoding="utf-8") as f:
            f.write(user_id)
        return True
    except Exception as e:
        print(f"ERROR: user_id.txt への保存に失敗: {e}")
        return False


# ------------------------------------------------------------
# 3) Push通知を送る関数（毎日00:00 / 09:00に使う）
# ------------------------------------------------------------
def push_text_message(text: str) -> None:
    user_id = load_saved_user_id()

    # まだ user_id が分からないと Push できないので注意
    if not user_id:
        print("INFO: まだ user_id が保存されていません。先にボットへ1回メッセージを送ってください。")
        return

    # LINE Messaging API に Push メッセージ送信
    try:
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            line_api.push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=text)],
                )
            )
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] PUSH送信OK: {text}")
    except Exception as e:
        print(f"ERROR: PUSH送信失敗: {e}")


def job_good_night() -> None:
    push_text_message("スマホをやめて寝る時間です！おやすみなさい💤")


def job_good_morning() -> None:
    push_text_message("おはようございます！起床時間です☀️")


# ------------------------------------------------------------
# 4) schedule を Flask と同時に動かす（別スレッドで常駐）
# ------------------------------------------------------------
def scheduler_loop() -> None:
    """
    schedule は「今実行していいジョブがある？」を定期的に確認する必要があります。
    Flaskの受信（Webhook）と同時に動かしたいので、スレッドで回します。
    """
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"ERROR: schedule の実行で例外: {e}")
        time.sleep(1)


def start_scheduler_thread() -> None:
    # 毎日決まった時刻に実行（PC/サーバーのローカル時刻が基準）
    schedule.every().day.at("23:30").do(job_good_night)
    schedule.every().day.at("09:00").do(job_good_morning)

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    print("INFO: スケジューラ起動（毎日 00:00 / 09:00 にPush）")


## ------------------------------------------------------------
# 5) Flask（Webhook受け口）
# ------------------------------------------------------------
app = Flask(__name__)

# トップページ（Cron-job用）
@app.route('/')
def home():
    print("DEBUG: Top page accessed")
    return "Render is awake!", 200

# LINE Webhook用
@app.route("/callback", methods=["POST"])
def callback():
    # 署名の取得
    signature = request.headers.get("X-Line-Signature", "")
    # データの取得
    body = request.get_data(as_text=True)
    
    # 【最重要】これがログに出るか確認したい！
    print("DEBUG: Webhook received!")
    print(f"DEBUG: Body content: {body[:50]}...") 

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("DEBUG: Invalid signature error")
        abort(400)
    except Exception as e:
        print(f"DEBUG: Exception in callback: {e}")
        return "Internal Error", 500

    return "OK"

# ------------------------------------------------------------
# 6) ユーザーがメッセージを送った時の処理
# ------------------------------------------------------------
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    # これがログに出れば、LINEとの連携は成功
    print("=========================================")
    print("DEBUG: handle_message start")
    
    user_text = event.message.text
    user_id = event.source.user_id if isinstance(event.source, UserSource) else None

    if user_id:
        save_user_id_if_needed(user_id)
        print(f"DEBUG: User ID = {user_id}")

    reply_text = f"受け取ったタスク：{user_text}\n受付完了しました！"

    try:
        with ApiClient(configuration) as api_client:
            line_api = MessagingApi(api_client)
            line_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )
        print("DEBUG: Reply sent successfully")
    except Exception as e:
        print(f"DEBUG: Reply error: {e}")
    
    print("=========================================")


# ------------------------------------------------------------
# 7) 起動
# ------------------------------------------------------------
if __name__ == "__main__":
    # Flaskの起動前にスケジューラを動かす
    start_scheduler_thread()

    # Windows + Flask はリローダーが動くと「二重起動」しやすいので False 推奨
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)