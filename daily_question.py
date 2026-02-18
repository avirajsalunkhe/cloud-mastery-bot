import os
import json
import smtplib
import requests
import firebase_admin
import time
from datetime import datetime, timezone
from firebase_admin import credentials, firestore
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Configuration ---
DASHBOARD_URL = "https://your-username.github.io/cloud-mastery-bot" 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")
APP_ID = "cloud-devops-bot"
MODEL_NAME = "gemini-2.5-flash-preview-09-2025"

# Firebase Initialization
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"Failed to initialize Firebase: {e}")
        exit(1)

db = firestore.client()

def get_question_pack(exam):
    """
    Fetches a 5-question pack with exponential backoff as per requirements.
    Retries up to 5 times with delays of 1s, 2s, 4s, 8s, 16s.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY}"
    
    prompt = (
        f"Generate exactly 5 multiple-choice questions for the {exam} certification. "
        "Sequence: Q1-Easy, Q2-Medium, Q3-Intermediate, Q4-Hard, Q5-Expert. "
        "Return a JSON array of objects. Each must have: 'question', 'options' (array of 4), "
        "'correctIndex' (0-3), 'explanation', and 'topic'."
    )
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7
        }
    }

    for i in range(5):
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                # Safety check for the 'candidates' key to avoid KeyError
                if 'candidates' in data and data['candidates'] and len(data['candidates']) > 0:
                    content = data['candidates'][0].get('content', {})
                    parts = content.get('parts', [])
                    if parts and len(parts) > 0:
                        return parts[0].get('text')
            
            # if status code is 429 or 5xx, or candidates missing, we retry
        except Exception:
            pass # Silent retry as per instructions
        
        time.sleep(2**i) # 1s, 2s, 4s, 8s, 16s
        
    print(f"Error: Failed to generate question pack for {exam} after 5 attempts.")
    return None

def send_email(user_data, questions_json):
    if not questions_json:
        return False
        
    try:
        q_list = json.loads(questions_json)
    except Exception as e:
        print(f"JSON Parsing Error: {e}")
        return False

    streak = user_data.get('streak', 0) + 1
    email = user_data['email']
    exam = user_data.get('examType', 'Certification')
    manage_url = f"{DASHBOARD_URL.rstrip('/')}/?tab=manage&email={email}"
    
    colors = ["#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444"]
    q_html = ""
    for i, q in enumerate(q_list):
        q_html += f"""
        <div style='margin-bottom:25px; border-left:4px solid {colors[i]}; padding-left:15px;'>
            <div style="font-size:10px; color:{colors[i]}; font-weight:bold; text-transform:uppercase;">Level {i+1}</div>
            <b style="font-size:16px; color:#1e293b;">{q['question']}</b><br>
            <div style="margin-top:8px; color:#64748b; font-size:13px;">Topic: {q.get('topic', 'General')}</div>
        </div>"""

    body = f"""
    <div style="font-family: sans-serif; padding:20px; background:#f1f5f9;">
        <div style="max-width:600px; margin:auto; background:white; border-radius:24px; padding:40px; border:1px solid #e2e8f0;">
            <div style="text-align:center; margin-bottom:30px;">
                <h1 style="color:#2563eb; margin:0;">Cloud Mastery Bot</h1>
                <div style="display:inline-block; margin-top:10px; background:#dbeafe; color:#1e40af; padding:5px 15px; border-radius:20px; font-weight:bold; font-size:12px;">
                    🔥 {streak} DAY STREAK
                </div>
            </div>
            {q_html}
            <div style="margin-top:30px; text-align:center;">
                <a href="{manage_url}" style="display:inline-block; background:#1e293b; color:white; padding:12px 25px; border-radius:10px; text-decoration:none; font-weight:bold; font-size:13px;">Manage Subscription</a>
            </div>
        </div>
    </div>"""
    
    msg = MIMEMultipart()
    msg['Subject'] = f"🚀 Day {streak}: Your {exam} Master Pack"
    msg['From'] = f"Cloud Mastery Bot <{SENDER_EMAIL}>"
    msg['To'] = email
    msg.attach(MIMEText(body, 'html'))
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(SENDER_EMAIL, SENDER_PASSWORD)
            s.sendmail(SENDER_EMAIL, email, msg.as_string())
        return True
    except Exception as e:
        print(f"SMTP Error for {email}: {e}")
        return False

if __name__ == "__main__":
    # Fetch active subscribers
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where('status', '==', 'active').stream()
    
    sub_list = []
    needed_exams = set()
    for doc in subs:
        u = doc.to_dict()
        u['id'] = doc.id
        sub_list.append(u)
        needed_exams.add(u.get('examType', 'AZ-900'))
    
    # Batch generate packs to save quota
    packs = {}
    for exam in needed_exams:
        pack = get_question_pack(exam)
        if pack:
            packs[exam] = pack
            
    # Send personalized emails
    for u in sub_list:
        exam = u.get('examType', 'AZ-900')
        if exam in packs:
            if send_email(u, packs[exam]):
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastDelivery': datetime.now(timezone.utc)
                })
