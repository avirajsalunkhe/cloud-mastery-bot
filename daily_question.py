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

# Custom Exception for Rate Limiting
class GeminiRateLimitError(Exception):
    pass

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
    Fetches a pack of 5 questions from Gemini. 
    Implements a multi-strategy fallback to handle 400, 404, and 429 errors.
    """
    if not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY is missing.")
        return None

    # Strategies prioritized by success probability and latest model aliases
    strategies = [
        ("v1beta", "gemini-1.5-flash-latest", True),
        ("v1beta", "gemini-2.0-flash", True),
        ("v1", "gemini-1.5-flash-latest", False),
        ("v1beta", "gemini-1.5-flash", False),
    ]
    
    prompt = (
        f"Generate exactly 5 multiple-choice questions for the {exam} certification. "
        "Sequence: Q1-Easy, Q2-Medium, Q3-Intermediate, Q4-Hard, Q5-Expert. "
        "Return a JSON array of objects. Each must have: 'question', 'options' (array of 4), "
        "'correctIndex' (0-3), 'explanation', and 'topic'. "
        "IMPORTANT: Output ONLY the raw JSON array. No markdown code blocks, no preamble."
    )
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.8}
    }

    for api_version, model, use_json_mode in strategies:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        current_payload = json.loads(json.dumps(payload))
        if use_json_mode:
            current_payload["generationConfig"]["responseMimeType"] = "application/json"
        
        print(f"  Trying {model} via {api_version} (JSON Mode: {use_json_mode})...")
        
        for retry in range(2): 
            try:
                res = requests.post(url, json=current_payload, timeout=45)
                
                if res.status_code == 200:
                    data = res.json()
                    if 'candidates' in data and data['candidates']:
                        return data['candidates'][0]['content']['parts'][0]['text']
                    print("    ⚠️ API returned 200 but no candidates found.")
                    break 
                
                elif res.status_code == 429:
                    if retry == 1:
                        print(f"    🛑 Hard Rate Limit hit on {model}.")
                        raise GeminiRateLimitError("Minute quota exhausted")
                    print(f"    ⚠️ 429 Rate Limit. Retrying in 10s...")
                    time.sleep(10)
                
                elif res.status_code == 400:
                    # Often means responseMimeType is not supported or safety filters triggered
                    reason = res.json().get('error', {}).get('message', 'Unknown 400 Error')
                    print(f"    ⚠️ 400 Bad Request: {reason}")
                    break 
                
                elif res.status_code == 404:
                    print(f"    ⚠️ 404 Model Not Found.")
                    break
                
                else:
                    print(f"    ⚠️ API Error {res.status_code}: {res.text[:100]}")
                    break 
                    
            except GeminiRateLimitError:
                raise
            except Exception as e:
                print(f"    ⚠️ Connection error: {e}")
                break
                
    return None

def send_email(user_data, questions_json):
    if not questions_json:
        return False
        
    try:
        # Aggressive cleaning of the string to ensure valid JSON
        clean_json = questions_json.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()
            
        # Find the start and end of the JSON array
        start_idx = clean_json.find('[')
        end_idx = clean_json.rfind(']') + 1
        if start_idx != -1 and end_idx != -1:
            clean_json = clean_json[start_idx:end_idx]
            
        q_list = json.loads(clean_json)
    except Exception as e:
        print(f"❌ JSON parsing failed for {user_data['email']}: {e}")
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
                <a href="{manage_url}" style="display:inline-block; background:#1e293b; color:white; padding:14px 30px; border-radius:12px; text-decoration:none; font-weight:bold; font-size:14px;">Manage Subscription</a>
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
        print(f"❌ SMTP failed for {email}: {e}")
        return False

if __name__ == "__main__":
    print(f"🚀 Starting dispatch at {datetime.now(timezone.utc)}")
    
    sub_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('subscribers')
    subs = sub_ref.where(filter=FieldFilter('status', '==', 'active')).stream()
    
    sub_list = []
    needed_exams = set()
    for doc in subs:
        u = doc.to_dict()
        u['id'] = doc.id
        sub_list.append(u)
        needed_exams.add(u.get('examType', 'AZ-900'))
    
    print(f"👥 Subscribers Found: {len(sub_list)}")

    packs = {}
    try:
        for exam in needed_exams:
            print(f"🧠 Fetching {exam} pack...")
            pack = get_question_pack(exam)
            if pack:
                packs[exam] = pack
                print(f"  ✅ {exam} pack cached successfully.")
            else:
                print(f"  ❌ All strategies failed for {exam}.")
    except GeminiRateLimitError:
        print("⚠️ Rate limit detected. Proceeding to mail successfully cached packs...")

    # Delivery Phase
    successful_sends = 0
    for u in sub_list:
        exam = u.get('examType', 'AZ-900')
        if exam in packs:
            print(f"📧 Mailing {u['email']}...")
            if send_email(u, packs[exam]):
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastDelivery': datetime.now(timezone.utc)
                })
                successful_sends += 1
    
    print(f"✅ Finished. Successfully delivered {successful_sends} emails.")
