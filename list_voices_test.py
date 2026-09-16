import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()
client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

response = client.voices.get_all()
for voice in response.voices:
    print(f"{voice.name}: {voice.voice_id}")