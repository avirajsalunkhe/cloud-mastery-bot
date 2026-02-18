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
    Fetches a pack of 5 questions from Gemini with a robust fallback system 
    to handle 400 (Invalid Field), 404 (Model Not Found) and 429 (Rate Limit) errors.
    """
    # Updated strategies with more common model names
    strategies = [
        ("v1beta", "gemini-1.5-flash"),
        ("v1", "gemini-1.5-flash"),
        ("v1beta", "gemini-pro"),
        ("v1", "gemini-1.5-flash-8b"),
    ]
    
    prompt = (
        f"Generate exactly 5 multiple-choice questions for the {exam} certification. "
        "Sequence: Q1-Easy, Q2-Medium, Q3-Intermediate, Q4-Hard, Q5-Expert. "
        "Return a JSON array of objects. Each must have: 'question', 'options' (array of 4), "
        "'correctIndex' (0-3), 'explanation', and 'topic'. "
        "Ensure the output is valid raw JSON."
    )
    
    # Base payload
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8
        }
    }

    for api_version, model in strategies:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        # Try with JSON mode first, then without if it fails with 400
        for use_json_mode in [True, False]:
            current_payload = json.loads(json.dumps(payload)) # deep copy
            if use_json_mode:
                current_payload["generationConfig"]["responseMimeType"] = "application/json"
            
            for retry in range(2):
                try:
                    res = requests.post(url, json=current_payload, timeout=30)
                    
                    if res.status_code == 200:
                        data = res.json()
                        if 'candidates' in data and data['candidates']:
                            text_content = data['candidates'][0]['content']['parts'][0]['text']
                            print(f"✅ Successfully generated question pack for {exam} using {model} ({api_version}, json_mode={use_json_mode})")
                            return text_content
                    
                    elif res.status_code == 400:
                        error_msg = res.text
                        if "responseMimeType" in error_msg or "response_mime_type" in error_msg:
                            print(f"⚠️ Model {model} does not support JSON mode on {api_version}. Retrying without MIME type...")
                            break # Break retry loop to try use_json_mode=False
                        else:
                            print(f"⚠️ API Error 400 on {model}/{api_version}: {error_msg[:100]}")
                            break # Try next strategy
                    
                    elif res.status_code == 404:
                        print(f"⚠️ Strategy failed: {model} not found on {api_version}.")
                        break # Try next strategy
                        
                    elif res.status_code == 429:
                        wait_time = 2 ** (retry + 1)
                        print(f"⚠️ Rate limited (429). Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        
                    else:
                        print(f"⚠️ API Error {res.status_code} on {model}/{api_version}. Debug: {res.text[:100]}")
                        break
                        
                except Exception as e:
                    print(f"⚠️ Request Error: {e}")
                    break
            
            # If we were in JSON mode and failed due to a 400, the 'break' took us here.
            # We will now loop to use_json_mode=False. 
            # If it was a 404 or other error, we'll continue to the next strategy.
            if res.status_code != 400:
                break
        
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
        # Log a snippet of the failed content for debugging
        print(f"Debug Snippet: {questions_json[:100]}...")
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
            <b style="font-size:16px; color:#1e293b; display:block; margin-bottom:5px;">{q['question']}</b>
            <div style="margin-top:8px; color:#64748b; font-size:13px;">Topic: {q.get('topic', 'General')}</div>
        </div>"""

    body = f"""
    <div style="font-family: sans-serif; padding:20px; background:#f1f5f9;">
        <div style="max-width:600px; margin:auto; background:white; border-radius:24px; padding:40px; border:1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
            <div style="text-align:center; margin-bottom:30px;">
                <h1 style="color:#2563eb; margin:0; font-size:28px;">Cloud Mastery Bot</h1>
                <div style="display:inline-block; margin-top:15px; background:#dbeafe; color:#1e40af; padding:6px 16px; border-radius:20px; font-weight:bold; font-size:12px; letter-spacing:0.5px;">
                    🔥 {streak} DAY STREAK
                </div>
            </div>
            <p style="color:#475569; font-size:15px; line-height:1.6; text-align:center; margin-bottom:30px;">
                Here is your daily <b>{exam}</b> question pack. Challenge yourself to maintain your streak!
            </p>
            {q_html}
            <div style="margin-top:40px; border-top:1px solid #f1f5f9; padding-top:25px; text-align:center;">
                <a href="{manage_url}" style="display:inline-block; background:#1e293b; color:white; padding:14px 30px; border-radius:12px; text-decoration:none; font-weight:bold; font-size:14px; transition: background 0.2s;">Manage Subscription</a>
                <p style="margin-top:20px; color:#94a3b8; font-size:11px;">You are receiving this because you subscribed to {exam} daily questions.</p>
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
            print(f"❌ Failed to generate {exam} pack after trying all strategies.")
            
    for u in sub_list:
        exam = u.get('examType', 'AZ-900')
        if exam in packs:
            if send_email(u, packs[exam]):
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastDelivery': datetime.now(timezone.utc)
                })
    
    print("✅ Dispatch process completed.")
