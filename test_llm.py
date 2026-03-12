import asyncio
import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()

GROQ_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

async def main():
    print("=" * 50)
    for model in ["llama-3.1-70b-versatile", "llama-3.3-70b-versatile", "llama3-groq-70b-8192-tool-use-preview"]:
        print(f"Testing Groq ({model})...")
        try:
            llm = ChatOpenAI(
                model=model,
                api_key=GROQ_KEY,
                base_url="https://api.groq.com/openai/v1",
                max_retries=0,
            )
            r = await llm.ainvoke([HumanMessage(content="Say OK in one word")])
            print(f"  OK: {r.content[:80]}")
        except Exception as e:
            print(f"  FAILED: {str(e)[:200]}")

    print()
    print("Testing OpenAI (gpt-4o-mini)...")
    try:
        llm2 = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=OPENAI_KEY,
            max_retries=0,
        )
        r2 = await llm2.ainvoke([HumanMessage(content="Say OK in one word")])
        print(f"  ✅ OpenAI WORKING: {r2.content[:80]}")
    except Exception as e:
        print(f"  ❌ OpenAI FAILED: {str(e)[:300]}")

    print("=" * 50)

asyncio.run(main())
