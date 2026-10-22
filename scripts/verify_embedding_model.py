import os
import sys
from dotenv import load_dotenv

load_dotenv()

from google import genai

def test_embeddings():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("FAIL: GEMINI_API_KEY not found!")
        return
        
    client = genai.Client(api_key=api_key)
    text = "Did I watch any videos recently about anime?"
    
    print(f"Text to embed: '{text}'")
    
    # 1. Test Preview Model
    print("\n[1] Testing model='models/gemini-embedding-2-preview'...")
    try:
        result_preview = client.models.embed_content(
            model="models/gemini-embedding-2-preview",
            contents=text,
            config={
                'task_type': 'RETRIEVAL_DOCUMENT',
                'output_dimensionality': 768
            }
        )
        v = result_preview.embeddings[0].values
        print(f"  SUCCESS! Vector length: {len(v)}")
        print(f"  First 5 dimensions: {v[:5]}")
    except Exception as e:
        print(f"  FAILED: {e}")
        
    # 2. Test Stable Model
    print("\n[2] Testing model='models/gemini-embedding-001'...")
    try:
        result_stable = client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=text,
            config={
                'task_type': 'RETRIEVAL_DOCUMENT'
            }
        )
        v = result_stable.embeddings[0].values
        print(f"  SUCCESS! Vector length: {len(v)}")
        print(f"  First 5 dimensions: {v[:5]}")
    except Exception as e:
        print(f"  FAILED: {e}")

if __name__ == "__main__":
    test_embeddings()
