from openai import OpenAI
from dotenv import load_dotenv
import pyttsx3
import time
import threading

load_dotenv()

client = OpenAI()
engine = pyttsx3.init()
voices = engine.getProperty('voices')

engine.setProperty('voice', voices[1].id)


run = True
engine.startLoop(False)


chat_history = []
threads = []


def speech_module(text):
    engine.say(f"{text}")
    engine.iterate()

        
    print(engine.isBusy())
    talking = engine.isBusy()

    while talking:
        engine.iterate()

        time.sleep(.1)
        talking = engine.isBusy()


def openai_api_call(message,chat_histery ):
    chat_history.append({"role":"user", 'content': message})
    response = client.responses.create(
        model="gpt-4.1-nano",
        input= chat_history
    )

    print(response.output_text)
    chat_history.append({"role":"assistant", 'content': response.output_text})
    return response.output_text, chat_histery
        



previous_thread = None

while run:
    message = input("what is your message:   ")
    
    if message != "exit":
        text, chat_history = openai_api_call(message, chat_history)

        tts_thread = threading.Thread(target=speech_module, kwargs={"text": text})
        
        if previous_thread and previous_thread.is_alive():
            previous_thread.join()
        
        tts_thread.start()
        previous_thread = tts_thread


        

 

    else:
        run=False
        


engine.endLoop()