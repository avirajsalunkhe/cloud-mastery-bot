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
    Fetches exactly 1 short question from Gemini. 
    Implements a robust fallback and high-patience rate-limit cooling.
    """
    if not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY is missing.")
        return None

    # Strategies updated based on logs:
    # 1. Standard 1.5 Flash (Removed -latest suffix to avoid 404)
    # 2. 2.0 Flash (Confirmed exists in logs but limited)
    # 3. 1.5 Flash 8B (Higher quota for free tier)
    strategies = [
        ("v1beta", "gemini-1.5-flash", True),
        ("v1", "gemini-1.5-flash", False),
        ("v1beta", "gemini-2.0-flash", True),
        ("v1beta", "gemini-1.5-flash-8b", True),
    ]
    
    # Prompt updated for 1 question and 40 char limit
    prompt = (
        f"Generate exactly 1 multiple-choice question for the {exam} certification. "
        "The question text MUST be extremely short (maximum 40 characters). "
        "Return a JSON array containing exactly one object. The object must have: "
        "'question', 'options' (array of 4), 'correctIndex' (0-3), 'explanation', and 'topic'. "
        "IMPORTANT: Output ONLY the raw JSON array. No markdown code blocks, no preamble."
    )
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ],
        "generationConfig": {
            "temperature": 0.8
        }
    }

    for api_version, model, use_json_mode in strategies:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        current_payload = json.loads(json.dumps(payload))
        
        if use_json_mode:
            current_payload["generationConfig"]["responseMimeType"] = "application/json"
        
        print(f"  Trying {model} via {api_version}...")
        
        # High-patience retries for 429 (Rate Limit) errors
        for retry in range(3): 
            try:
                res = requests.post(url, json=current_payload, timeout=60)
                
                if res.status_code == 200:
                    data = res.json()
                    if 'candidates' in data and data['candidates'] and 'content' in data['candidates'][0]:
                        return data['candidates'][0]['content']['parts'][0]['text']
                    print("    ⚠️ API returned 200 but no valid content.")
                    break 
                
                elif res.status_code == 429:
                    # Free tier quotas reset strictly. We wait 90s to ensure the window clears completely.
                    wait_time = 90 
                    if retry == 2:
                        print(f"    🛑 Quota exhausted for {model}. Moving to next strategy.")
                        break
                    print(f"    ⚠️ 429 Rate Limit. Quota full. Cooling down for {wait_time}s...")
                    time.sleep(wait_time)
                
                elif res.status_code == 400:
                    reason = res.json().get('error', {}).get('message', 'Unknown 400 Error')
                    if "responseMimeType" in reason:
                        print(f"    ⚠️ 400: JSON mode not supported. Retrying without it...")
                        current_payload["generationConfig"].pop("responseMimeType", None)
                        continue 
                    print(f"    ⚠️ 400 Bad Request: {reason}")
                    break 
                
                elif res.status_code == 404:
                    print(f"    ⚠️ 404 Model Not Found.")
                    break
                
                else:
                    print(f"    ⚠️ API Error {res.status_code}: {res.text[:100]}")
                    break 
                    
            except Exception as e:
                print(f"    ⚠️ Connection error: {e}")
                break
        
        # Buffer between model strategies to avoid triggering cumulative limits
        time.sleep(15)
                
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
    
    colors = ["#3b82f6"] # Only one color needed for one question
    q_html = ""
    for i, q in enumerate(q_list):
        q_html += f"""
        <div style='margin-bottom:25px; border-left:4px solid {colors[0]}; padding-left:15px;'>
            <div style="font-size:10px; color:{colors[0]}; font-weight:bold; text-transform:uppercase;">Daily Challenge</div>
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
                Here is your daily <b>{exam}</b> question. Challenge yourself!
            </p>
            {q_html}
            <div style="margin-top:40px; border-top:1px solid #f1f5f9; padding-top:25px; text-align:center;">
                <a href="{manage_url}" style="display:inline-block; background:#1e293b; color:white; padding:14px 30px; border-radius:12px; text-decoration:none; font-weight:bold; font-size:14px;">Manage Subscription</a>
            </div>
        </div>
    </div>"""
    
    msg = MIMEMultipart()
    msg['Subject'] = f"🚀 Day {streak}: Your {exam} Question"
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
    for exam in needed_exams:
        print(f"🧠 Fetching {exam} pack...")
        pack = get_question_pack(exam)
        if pack:
            packs[exam] = pack
            print(f"  ✅ {exam} pack cached successfully.")
        else:
            print(f"  ❌ All strategies failed for {exam}. (Check log for 404 vs 429 specifics)")

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
