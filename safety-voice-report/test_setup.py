import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
key = os.getenv("XAI_API_KEY")
print("Key loaded:", key[:15] + "..." if key else "NOT FOUND")

client = OpenAI(
    api_key=key,
    base_url="https://api.x.ai/v1"
)

response = client.chat.completions.create(
    model="grok-4-fast",
    max_tokens=50,
    messages=[{"role": "user", "content": "Say hello in 5 words."}]
)
print(response.choices[0].message.content)