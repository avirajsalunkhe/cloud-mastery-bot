import os
import json
import smtplib
import requests
import firebase_admin
import time
from datetime import datetime, timezone
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- Configuration ---
DASHBOARD_URL = "https://avirajsalunkhe.github.io/cloud-mastery-bot" 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDER_EMAIL = os.getenv("EMAIL_SENDER")
SENDER_PASSWORD = os.getenv("EMAIL_PASSWORD")
APP_ID = "cloud-devops-bot"

# Using the stable 1.5 Flash model
MODEL_NAME = "gemini-1.5-flash"

# Firebase Initialization
service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT")
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
        print("✅ Firebase initialized successfully.")
    except Exception as e:
        print(f"❌ Failed to initialize Firebase: {e}")
        exit(1)

db = firestore.client()

def get_question_pack(exam):
    """
    Fetches a pack of 5 questions from Gemini with exponential backoff for 429/500 errors.
    """
    # Using the stable v1 API endpoint to avoid 404 errors found in some beta regions
    url = f"https://generativelanguage.googleapis.com/v1/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY}"
    
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

    # Exponential backoff: 2s, 4s, 8s, 16s, 32s
    for i in range(5):
        try:
            res = requests.post(url, json=payload, timeout=30)
            
            if res.status_code == 200:
                data = res.json()
                if 'candidates' in data and data['candidates']:
                    text_content = data['candidates'][0]['content']['parts'][0]['text']
                    print(f"✅ Successfully generated question pack for {exam}")
                    return text_content
            
            elif res.status_code == 404:
                # If v1 fails, try v1beta as a fallback before giving up
                print(f"⚠️ Model '{MODEL_NAME}' not found on v1, trying v1beta...")
                beta_url = url.replace("/v1/", "/v1beta/")
                res = requests.post(beta_url, json=payload, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    text_content = data['candidates'][0]['content']['parts'][0]['text']
                    return text_content
                print(f"❌ Error 404: Model not found on either endpoint.")
                return None
                
            elif res.status_code == 429:
                wait_time = 2 ** (i + 1)
                print(f"⚠️ Gemini API rate limited (429). Retrying in {wait_time}s...")
                time.sleep(wait_time)
                
            else:
                wait_time = 2 ** (i + 1)
                print(f"⚠️ Gemini API error {res.status_code}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                
        except Exception as e:
            wait_time = 2 ** (i + 1)
            print(f"⚠️ Request Error: {e}. Retrying in {wait_time}s...")
            time.sleep(wait_time)
        
    return None

def send_email(user_data, questions_json):
    if not questions_json:
        return False
        
    try:
        # Strip potential markdown formatting if Gemini includes it
        clean_json = questions_json.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif clean_json.startswith("```"):
            clean_json = clean_json.split("```")[1].split("```")[0].strip()
            
        q_list = json.loads(clean_json)
    except Exception as e:
        print(f"❌ Failed to parse JSON for {user_data['email']}: {e}")
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
        print(f"📧 Email sent successfully to {email}")
        return True
    except Exception as e:
        print(f"❌ SMTP Error for {email}: {e}")
        return False

if __name__ == "__main__":
    print(f"🚀 Starting daily dispatch at {datetime.now(timezone.utc)}")
    
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    
    sub_list = []
    needed_exams = set()
    for doc in subs:
        u = doc.to_dict()
        u['id'] = doc.id
        sub_list.append(u)
        needed_exams.add(u.get('examType', 'AZ-900'))
    
    print(f"👥 Found {len(sub_list)} active subscribers across {len(needed_exams)} exam paths.")
    
    if len(sub_list) == 0:
        print("ℹ️ No subscribers to process. Ending task.")
        exit(0)

    packs = {}
    for exam in needed_exams:
        print(f"🧠 Requesting {exam} questions from Gemini...")
        pack = get_question_pack(exam)
        if pack:
            packs[exam] = pack
        else:
            print(f"❌ Failed to generate {exam} pack after retries.")
            
    for u in sub_list:
        exam = u.get('examType', 'AZ-900')
        if exam in packs:
            if send_email(u, packs[exam]):
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastDelivery': datetime.now(timezone.utc)
                })
    
    print("✅ Dispatch process completed.")
