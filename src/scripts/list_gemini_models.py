from dotenv import load_dotenv
import os
from google import genai

def list_models():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env")
        return

    try:
        client = genai.Client(api_key=api_key)
        print("Listing available Gemini models...")
        models = client.models.list()
        found = False
        for m in models:
            name = getattr(m, 'name', str(m))
            if "gemini" in name.lower():
                print(f"- {name}")
                found = True
        
        if not found:
            print("No Gemini models found.")
            
    except Exception as e:
        print(f"Error querying API: {e}")

if __name__ == "__main__":
    list_models()
