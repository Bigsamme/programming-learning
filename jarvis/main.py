from openai import OpenAI
from dotenv import load_dotenv
import pyttsx3

load_dotenv()

client = OpenAI()
engine = pyttsx3.init()

response = client.responses.create(
    model="gpt-4.1-nano",
    input="what is the best gatorage flavor"
)

print(response.output_text)



engine.say(f"{response.output_text}")
engine.runAndWait()