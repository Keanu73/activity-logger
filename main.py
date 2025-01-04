import os
import requests
import logging
from whatsapp import WhatsApp, Message
from dotenv import load_dotenv
from flask import Flask, request, Response
import gspread
from openai import OpenAI
import json
from datetime import datetime

# Environment variables are declared here
load_dotenv(".env")

# Google Sheet and OpenAI API configuration
GS_SHEET_NAME = os.getenv("GS_SHEET_ID")
GS_SERVICE_ACCOUNT_FILE = os.getenv("GS_SERVICE_ACCOUNT_FILE")
WA_TOKEN = os.getenv("WA_TOKEN")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID")
AI_OPENAI_URL = os.getenv("AI_OPENAI_URL")
AI_OPENAI_KEY = os.getenv("AI_OPENAI_KEY")
AI_WHISPER_URL = os.getenv("AI_WHISPER_URL")
# Used for whatsapp webhook verification
WA_VERIFY_TOKEN = ""

# Fatally exit if any of the environment variables are empty
if not GS_SHEET_NAME or not GS_SERVICE_ACCOUNT_FILE or not WA_TOKEN or not WA_PHONE_NUMBER_ID or not WA_VERIFY_TOKEN or not AI_WHISPER_URL:
    logging.error("One or more environment variables are missing. Exiting.")
    exit(1)


# Initialize all instances required by app

# OpenAI api, we'll use LLAMA as a drop-in replacement. the key doesnt matter for now
client = OpenAI(api_key="")
if AI_OPENAI_URL != "":
    client = OpenAI(base_url = AI_OPENAI_URL, api_key=AI_OPENAI_KEY)

# Open the Google Sheet
gc = gspread.service_account(filename=GS_SERVICE_ACCOUNT_FILE)
sheet = gc.open(GS_SHEET_NAME).sheet1

# Initialise WhatsApp instance
messenger = WhatsApp(WA_TOKEN, phone_number_id={1: WA_PHONE_NUMBER_ID})

# Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
#log = logging.getLogger('werkzeug')
#logging.set(logging.ERROR)

app = Flask(__name__)

@app.get("/")
def verify_token():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        logging.info("Verified webhook")
        challenge = request.args.get("hub.challenge")
        return str(challenge)
    logging.error("Webhook Verification failed")
    return "Invalid verification token"


@app.post("/")
def hook():
    # Handle Webhook Subscriptions
    data = request.get_json()
    if data is None:
        return Response(status=200)
    logging.debug("Received webhook data: %s", data)
    changed_field = messenger.changed_field(data)
    if changed_field == "messages":
        new_message = messenger.is_message(data)
        if new_message:
            msg = Message(instance=messenger, data=data)
            mobile = data['entry'][0]['changes'][0]['value']['messages'][0]['from']
            name = msg.name
            message_type = msg.type
            logging.info(
                "New Message: Sender: %s Name: %s Type: %s", mobile, name, message_type
            )
            if message_type == "audio":
                audio = msg.audio
                if audio is None:
                    return Response(status=400)
                audio_id, mime_type = audio["id"], audio["mime_type"]
                audio_url = messenger.query_media_url(audio_id)
                if audio_url is None:
                    return Response(status=400)
                audio_filename = messenger.download_media(audio_url, mime_type)
                logging.info("%s sent audio %s", mobile, audio_filename)

                if audio_filename is None:
                    return Response(status=400)

                # Pass audio to Whisper model to do the rest
                text_guess = ai_parse_audio(audio_filename)
                if text_guess != "":
                    # Pass into local Llama-python-cpp OpenAI-esque api model to then feed a prompt into
                    logging.info("Whisper result: %s", text_guess)
                    sheet_rows = ai_parse_transcription(text_guess)
                    if sheet_rows[0] != "" or sheet_rows[1] != "":
                        result = append_to_sheet(sheet_rows)
                        if result:
                            m = Message(instance=messenger, to=mobile, content="Data successfully appended to Google Sheet")
                            m.send()
                        else:
                            m = Message(instance=messenger, to=mobile, content="Failed to append data to Google Sheet. Please try again later")
                            m.send()
                    else:
                        m = Message(instance=messenger, to=mobile, content="Failed to extract highlights from transcription. Please try again later")
                        m.send()
                else:
                    logging.error("Failed to transcribe audio")
                    m = Message(instance=messenger, to=mobile, content="Failed to transcribe audio. Please try again later")
                    m.send()
            else:
                logging.info("%s sent %s", mobile, message_type)
                logging.info(data)
        else:
            delivery = messenger.get_delivery(data)
            if delivery:
                logging.info("Message: %s", delivery)
            else:
                logging.info("No new message")
    return "OK", 200

def ai_parse_audio(audio_filename):
    """Use OpenAI Whisper to perform speech recognition on the audio file and return its best guess to be fed into OpenAI."""
    try:
        # Pass audio to Whisper model to do the rest
        with open(audio_filename, 'rb') as audio_file:

            # Send a POST request with the audio file
            response = requests.post(f'{AI_WHISPER_URL}/asr?language=en', files={'audio_file': audio_file})

            # Check if the request was successful
            if response.status_code == 200:
                transcription_result = response.text.strip()
                # Parse and print the transcription result
                return transcription_result
            else:
                logging.error("Failed to transcribe audio. Status code: %s Response: %s", response.status_code, response.text)
                return ""

    except Exception as e:
        # If parsing or any other error occurs, print the error message and the full response
        logging.error("Error while parsing Whisper response: %s", e)
        return "" # Fallback to empty data

def ai_parse_transcription(transcription):
    """Use OpenAI to extract physical and social highlights."""
    prompt = f"""
    Extract the following details from the transcription:
    1. Physical Win (any achievement related to fitness or physical activities).
    2. Social Highlight (any notable social event, interaction, or highlight).

    Return the result as a JSON object with keys 'Physical Win' and 'Social Highlight'.
    Please do not format the response with any code blocks or markdown; just provide the JSON object.

    Transcription: "{transcription}"
    """

    # Make the request to OpenAI API
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Use the suitable GPT model
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        )

        # Log the full response for debugging

        # Extract the result from the response
        result = response.choices[0].message.content
        if result:
            logging.info("Full AI Response: %s", result)
            result = result.strip()
            if json.loads(result) is None:
                logging.error("Failed to extract highlights from transcription. Response: %s", result)
                return ["", ""]

            result_json = json.loads(result)

            return [result_json.get("Physical Win", ""), result_json.get("Social Highlight", "")]
        return ["", ""]

    except Exception as e:
        # If parsing or any other error occurs, print the error message and the full response
        logging.error("Error while parsing AI response: %s", e)
        logging.error("Full AI Response: %s", response)  # Log the full response for further analysis
        return ["", ""]  # Fallback to empty data

def append_to_sheet(data):
    try:
        current_date = datetime.now()
        formatted_date = current_date.strftime("%d-%m-%Y")
        formatted_time = current_date.strftime("%H:%M:%S")
        data.insert(0, formatted_time.strip())
        data.insert(0, formatted_date.strip())
        logging.info(data)
        result = sheet.append_row(data, table_range="A1:D1")
        logging.info("Data appended to Google Sheet: %s", result)
        return True
    except Exception as e:
        logging.error("Error while appending data to Google Sheet: %s", e)
        return False

if __name__ == "__main__":
    app.run(port=6869, debug=True)
