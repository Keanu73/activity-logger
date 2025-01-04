# activity-logger
This simple Python script is a WhatsApp bot which listens for incoming audio messages, transcribes them using a [locally hosted instance](https://github.com/ahmetoner/whisper-asr-webservice) of [OpenAI Whisper](https://github.com/openai/whisper), then feeds the transcription into a ChatGPT prompt to extract certain information, and then appends this information into rows into a Google Sheet.

Third-party libraries used:
- [whatsapp-python](https://github.com/filipporomani/whatsapp-python)
- [gspread](https://github.com/burnash/gspread)
- [Flask](https://flask.palletsprojects.com/en/stable/)
- [python-dotenv](https://github.com/theskumar/python-dotenv)
- [openai-python](https://github.com/openai/openai-python)
