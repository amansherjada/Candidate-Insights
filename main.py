from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleAuthRequest
import os
import re
import requests
import tempfile
import logging

# === Logging ===
logging.basicConfig(level=logging.INFO)

# === Env Vars Check ===
openai_api_key = os.getenv("OPENAI_API_KEY")
gcred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

if not openai_api_key:
    raise RuntimeError("❌ OPENAI_API_KEY not set.")
if not gcred_path or not os.path.exists(gcred_path):
    raise RuntimeError("❌ GOOGLE_APPLICATION_CREDENTIALS path is invalid.")

client = OpenAI(api_key=openai_api_key)

# === FastAPI Setup ===
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Helpers ===

def clean_transcript(text):
    text = re.sub(r"\\an\d+\\?.*?", "", text)
    text = re.sub(r"[-–—_=*#{}<>[\]\"\'`|]", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\d{2,}[:.]\d{2,}[:.]\d{2,}", "", text)
    return text.strip()

def download_mp3_from_drive(file_id):
    logging.info(f"📥 Downloading MP3 from Google Drive: {file_id}")
    credentials = service_account.Credentials.from_service_account_file(
        gcred_path,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    credentials.refresh(GoogleAuthRequest())
    drive_service = build("drive", "v3", credentials=credentials)

    file_metadata = drive_service.files().get(fileId=file_id, fields="name").execute()
    file_name = os.path.splitext(file_metadata['name'])[0]
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    headers = {"Authorization": f"Bearer {credentials.token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Failed to download MP3: {response.status_code} - {response.text}")

    mp3_path = os.path.join(tempfile.gettempdir(), file_name + ".mp3")
    with open(mp3_path, "wb") as f:
        f.write(response.content)

    return mp3_path

def transcribe_audio(mp3_path):
    logging.info("🧠 Transcribing audio...")
    with open(mp3_path, "rb") as file:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=file,
            response_format="text",
            language="en"
        )
    return clean_transcript(result)

def generate_llm_report(prompt, transcript):
    logging.info("📝 Generating LLM report...")
    full_input = prompt + "\n\nTranscript:\n" + transcript
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are an expert HR interview evaluator."},
            {"role": "user", "content": full_input}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

# === Endpoints ===

@app.post("/transcribe")
async def transcribe_endpoint(request: Request):
    try:
        data = await request.json()
        file_id = data.get("file_id")
        if not file_id:
            return JSONResponse(status_code=400, content={"error": "Missing file_id"})

        mp3_path = download_mp3_from_drive(file_id)
        transcript = transcribe_audio(mp3_path)
        os.remove(mp3_path)

        return {"transcript": transcript.strip()}

    except Exception as e:
        logging.exception("❌ Transcription failed")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate-report")
async def generate_report_endpoint(request: Request):
    try:
        data = await request.json()
        prompt = data.get("prompt", "Evaluate the following interview transcript.")
        transcript = data.get("transcript")

        if not transcript:
            return JSONResponse(status_code=400, content={"error": "Missing transcript"})

        report = generate_llm_report(prompt, transcript)
        return {"report": report}

    except Exception as e:
        logging.exception("❌ Report generation failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
