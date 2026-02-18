import os
import json
import smtplib
import requests
import firebase_admin
from datetime import datetime, timezone
from firebase_admin import credentials, firestore
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- SET THIS TO YOUR GITHUB PAGES URL ---
DASHBOARD_URL = "https://avirajsalunkhe.github.io/cloud-mastery-bot" 

# Config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")
APP_ID = "cloud-devops-bot"

# Firebase Init
service_account = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(service_account))
    firebase_admin.initialize_app(cred)
db = firestore.client()

def get_question_pack(exam):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
    prompt = f"Generate 5 MCQs for {exam} exam. Q1:Easy, Q2:Medium, Q3:Intermediate, Q4:Hard, Q5:Expert. Return JSON array of objects with: question, options (4), correctIndex, topic."
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
    res = requests.post(url, json=payload)
    return res.json()['candidates'][0]['content']['parts'][0]['text']

def send_email(user_data, questions_json):
    q_list = json.loads(questions_json)
    streak = user_data.get('streak', 0) + 1
    exam = user_data['examType']
    manage_url = f"{DASHBOARD_URL}?tab=manage&email={user_data['email']}"
    
    q_html = "".join([f"<div style='margin-bottom:20px; border-left:4px solid #3b82f6; padding-left:15px;'><b>{i+1}. {q['question']}</b><br><small>{q['topic']}</small></div>" for i, q in enumerate(q_list)])
    
    body = f"""
    <div style="font-family:sans-serif; padding:20px; background:#f8fafc;">
        <div style="max-width:600px; margin:auto; background:white; border-radius:20px; padding:30px; border:1px solid #e2e8f0;">
            <h1 style="color:#2563eb;">Day {streak} Study Pack</h1>
            <p>Your 5 daily {exam} questions are ready.</p>
            <hr>
            {q_html}
            <div style="margin-top:30px; text-align:center;">
                <a href="{manage_url}" style="color:#64748b; font-size:12px;">Unsubscribe or Manage Data</a>
            </div>
        </div>
    </div>
    """
    msg = MIMEMultipart(); msg['Subject'] = f"🔥 Day {streak}: {exam} Daily Pack"; msg['From'] = SENDER_EMAIL; msg['To'] = user_data['email']
    msg.attach(MIMEText(body, 'html'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(SENDER_EMAIL, SENDER_PASSWORD)
        s.sendmail(SENDER_EMAIL, user_data['email'], msg.as_string())

if __name__ == "__main__":
    subs = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers').stream()
    packs = {}
    for doc in subs:
        u = doc.to_dict(); u['id'] = doc.id
        if u['examType'] not in packs: packs[u['examType']] = get_question_pack(u['examType'])
        send_email(u, packs[u['examType']])
        db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers').document(u['id']).update({'streak': u.get('streak', 0) + 1})
