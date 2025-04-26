import streamlit as st
import os
import json
import re
import requests
from dotenv import load_dotenv
from groq import Groq
from careerjet_api import CareerjetAPIClient

# --- Setup APIs ---
os.environ['CAREERJET_AFFID'] = 'testaff'
os.environ['JOOBLE_API_KEY']   = 'b799e3f7-afb3-4981-9588-471adced4d73'
os.environ['WEB3_TOKEN']       = 'MZ1bUvjrDzUecnA25Wr45L5G4ZEHmoWj'
os.environ['GROQ_API_KEY']     = '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'

load_dotenv()

CAREERJET_AFFID = os.getenv('CAREERJET_AFFID')
JOOBLE_API_KEY = os.getenv('JOOBLE_API_KEY')
WEB3_TOKEN = os.getenv('WEB3_TOKEN')
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

groq_client = Groq(api_key=GROQ_API_KEY)

def chat_with_groq(system: str, user: str) -> str:
    resp = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        temperature=0.2,
        max_tokens=512,
        top_p=1.0,
        stream=False
    )
    return resp.choices[0].message.content.strip()

def parse_query(user_input: str, history: list) -> dict:
    try:
        m = re.search(r"\b(.*) jobs in ([A-Za-z ]+)", user_input, re.IGNORECASE)
        if m:
            role = m.group(1).strip()
            loc = m.group(2).strip()
            return {"keywords": [role], "location": loc}
        
        # fallback: try only location
        m2 = re.search(r"\bin ([A-Za-z ]+)", user_input, re.IGNORECASE)
        if m2:
            return {"location": m2.group(1).strip()}

        # fallback: normal Groq parsing
        serializable = [{"user": h[0], "slots": h[1]} for h in history]
        system = (
            "You are JobGPT. You have access to the last 5 interactions as history. "
            "Each entry is {user: string, slots: JSON}. Given this history plus a new user message, "
            "extract slots like keywords, location, remote, days."
        )
        user = f"History: {json.dumps(serializable)}\nUser: \"{user_input}\""
        raw = chat_with_groq(system, user)
        return json.loads(raw)
    
    except Exception as e:
        print(f"[Warning] parse_query failed: {e}")
        return {}


class JobContext:
    def __init__(self):
        self.slots = {}
    def update(self, new_slots: dict):
        for k, v in new_slots.items():
            self.slots[k] = v
    def to_params(self, api_name: str) -> dict:
        mapping = {
            'keywords': {'cj':'keywords','jj':'keywords','w3':'tag'},
            'location': {'cj':'location','jj':'location','w3':'country'},
            'remote': {'w3':'remote'},
            'days': {'jj':'datePosted','w3':'posted_since'},
        }
        params = {}
        for slot, val in self.slots.items():
            if slot in mapping and api_name in mapping[slot]:
                key = mapping[slot][api_name]
                if slot == 'days':
                    params[key] = f"last {val} days"
                elif slot == 'remote':
                    params[key] = str(val).lower()
                elif slot == 'keywords':
                    params[key] = " ".join(val) if isinstance(val, list) else val
                else:
                    params[key] = val
        return params

class CareerjetClient:
    def __init__(self, locale='en_US'):
        self.client = CareerjetAPIClient(locale)
    def search(self, **params):
        p = {
            **params,
            'affid': CAREERJET_AFFID,
            'user_ip': '1.2.3.4',
            'user_agent': 'ChatGPT-Client',
            'url': 'https://your.domain/jobs'
        }
        return self.client.search(p).get('jobs', [])

class JoobleClient:
    def __init__(self):
        self.endpoint = f"https://jooble.org/api/{JOOBLE_API_KEY}"
    def search(self, **params):
        return requests.post(self.endpoint, json=params).json().get('jobs', [])

class Web3Client:
    def __init__(self):
        self.base = "https://web3.career/api/v1"
    def search(self, **params):
        p = {**params, 'token': WEB3_TOKEN}
        data = requests.get(self.base, params=p).json()
        return data[2] if len(data) > 2 else []

def aggregate(*lists):
    seen = {}
    for lst in lists:
        for job in lst:
            key = job.get('apply_url') or job.get('url')
            if key and key not in seen:
                seen[key] = job
    return list(seen.values())

class JobOrchestrator:
    def __init__(self):
        self.context = JobContext()
        self.cj = CareerjetClient('en_IN')
        self.jj = JoobleClient()
        self.w3 = Web3Client()
        self.history = []

    def handle(self, user_message: str):
        last_five = self.history[-5:]
        try:
            slots = parse_query(user_message, last_five)
        except Exception:
            slots = {}
        if not slots:
            return "I’m missing some details—can you specify role or location?"

        merged = self.context.slots.copy()
        merged.update(slots)
        self.context.slots = merged

        self.history.append((user_message, merged.copy()))
        if len(self.history) > 5:
            self.history.pop(0)

        cj_p = self.context.to_params('cj')
        jj_p = self.context.to_params('jj')
        w3_p = self.context.to_params('w3')

        jobs = aggregate(
            self.cj.search(**cj_p),
            self.jj.search(**jj_p),
            self.w3.search(**w3_p)
        )

        if not jobs:
            return "No matching jobs found. Try broadening your search."

        lines = []
        for i, job in enumerate(jobs[:5], 1):
            title = job.get('title', 'N/A')
            company = job.get('company', 'N/A')
            loc = job.get('location') or job.get('locations', 'N/A')
            url = job.get('apply_url') or job.get('url', '')
            lines.append(f"{i}. **{title}** at {company} ({loc})\nApply: {url}")
        lines.append("\nAnything else I can refine?")
        return "\n\n".join(lines)

# --- Streamlit App ---
st.set_page_config(page_title="JobGPT - Find Jobs", page_icon="🧑‍💻", layout="centered")

st.title("JobGPT 🧑‍💻")
st.write("Hi! Ask me about jobs you are looking for.")

if "orch" not in st.session_state:
    st.session_state.orch = JobOrchestrator()

user_input = st.text_input("Your request:", key="input")
if st.button("Find Jobs"):
    if user_input.strip():
        response = st.session_state.orch.handle(user_input)
        st.markdown(response)
    else:
        st.warning("Please enter something!")
