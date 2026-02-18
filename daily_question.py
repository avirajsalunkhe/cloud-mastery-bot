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
GROQ_API_KEY = os.getenv("GROQ_API_KEY") # Add this to your GitHub Secrets!
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

def fetch_from_gemini(exam, prompt):
    """Attempt to get questions from Gemini API."""
    if not GEMINI_API_KEY: return None
    
    strategies = [
        ("v1beta", "gemini-2.0-flash", True),
        ("v1beta", "gemini-1.5-flash", True),
    ]
    
    for api_version, model, use_json in strategies:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.8}
        }
        if use_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            
        try:
            res = requests.post(url, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                return data['candidates'][0]['content']['parts'][0]['text']
            elif res.status_code == 429:
                print(f"    ⚠️ Gemini {model} rate limited. Trying next...")
        except:
            pass
    return None

def fetch_from_groq(exam, prompt):
    """Attempt to get questions from Groq API (Llama 3). Highly reliable free alternative."""
    if not GROQ_API_KEY: 
        print("    ℹ️ Groq API Key not found in secrets. Skipping fallback.")
        return None
    
    print(f"    🚀 Attempting Groq Fallback (Llama-3)...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a cloud certification expert. Output ONLY valid raw JSON."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.7
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        if res.status_code == 200:
            data = res.json()
            return data['choices'][0]['message']['content']
        else:
            print(f"    ⚠️ Groq failed with status {res.status_code}")
    except Exception as e:
        print(f"    ⚠️ Groq connection error: {e}")
    return None

def refill_question_bank(exam):
    """
    Calls Multiple AI Providers to refill the question bank.
    If Gemini fails, it automatically falls back to Groq.
    """
    print(f"🧠 Bank empty for {exam}. Refilling...")
    
    prompt = (
        f"Generate exactly 10 multiple-choice questions for the {exam} certification. "
        "Each question text MUST be extremely short (maximum 40 characters). "
        "Return a JSON array of 10 objects. Each object must have: "
        "'question', 'options' (array of 4), 'correctIndex' (0-3), 'explanation', and 'topic'. "
        "Output ONLY the JSON array."
    )

    # Try Gemini First
    raw_response = fetch_from_gemini(exam, prompt)
    
    # Fallback to Groq if Gemini fails
    if not raw_response:
        raw_response = fetch_from_groq(exam, prompt)
        
    if not raw_response:
        return False

    try:
        # Clean JSON
        clean_json = raw_response.strip()
        if "```json" in clean_json:
            clean_json = clean_json.split("```json")[1].split("```")[0].strip()
        elif "```" in clean_json:
            clean_json = clean_json.split("```")[1].split("```")[0].strip()
        
        # Some providers return an object containing the array
        data = json.loads(clean_json)
        questions = data if isinstance(data, list) else data.get('questions', [])
        
        if not questions:
            return False

        # Save to Firestore Bank
        bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
        for q in questions:
            bank_ref.add({
                "examType": exam,
                "question_data": json.dumps([q]), 
                "used": False,
                "createdAt": datetime.now(timezone.utc)
            })
        print(f"✅ Successfully added {len(questions)} new questions to {exam} bank.")
        return True
    except Exception as e:
        print(f"    ⚠️ Error parsing AI response: {e}")
        return False

def get_question_from_bank(exam):
    """Checks the database for an unused question. Refills if empty."""
    bank_ref = db.collection('artifacts').document(APP_ID).collection('public').document('data').collection('question_bank')
    
    query = bank_ref.where(filter=FieldFilter("examType", "==", exam)).where(filter=FieldFilter("used", "==", False)).limit(1).stream()
    
    found_doc = None
    for doc in query:
        found_doc = doc
        break
        
    if found_doc:
        bank_ref.document(found_doc.id).update({"used": True, "usedAt": datetime.now(timezone.utc)})
        return found_doc.to_dict()["question_data"]
    
    if refill_question_bank(exam):
        time.sleep(2)
        return get_question_from_bank(exam) 
        
    return None

def send_email(user_data, questions_json):
    if not questions_json:
        return False
        
    try:
        q_list = json.loads(questions_json)
    except Exception:
        return False

    streak = user_data.get('streak', 0) + 1
    email = user_data['email']
    exam = user_data.get('examType', 'Certification')
    manage_url = f"{DASHBOARD_URL.rstrip('/')}/?tab=manage&email={email}"
    
    q_html = ""
    for q in q_list:
        q_html += f"""
        <div style='margin-bottom:25px; border-left:4px solid #3b82f6; padding-left:15px;'>
            <div style="font-size:10px; color:#3b82f6; font-weight:bold; text-transform:uppercase;">Daily Challenge</div>
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
    for exam in list(needed_exams):
        print(f"📦 Checking Question Bank for {exam}...")
        pack = get_question_from_bank(exam)
        if pack:
            packs[exam] = pack
        else:
            print(f"❌ Could not retrieve or generate questions for {exam}.")

    successful_sends = 0
    for u in sub_list:
        exam = u.get('examType', 'AZ-900')
        if exam in packs:
            if send_email(u, packs[exam]):
                sub_ref.document(u['id']).update({
                    'streak': u.get('streak', 0) + 1,
                    'lastDelivery': datetime.now(timezone.utc)
                })
                successful_sends += 1
    
    print(f"✅ Finished. Successfully delivered {successful_sends} emails.")
