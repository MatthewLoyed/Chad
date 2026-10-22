"""
Interactive Live Demonstration of Chad's Media & Content Discovery Pipeline.

Exercises the real pipeline end-to-end:
1. Status check of curiosity buffer & interest profile
2. Live on-demand discovery across providers (Hacker News, GitHub, AniList, YouTube)
3. Popping the top discovery and formatting Chad's spontaneous Telegram message
4. Simulating live user feedback (reaction) to show real-time interest tuning
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.tools.system.utilities.manage_curiosity import ManageCuriosity


async def main():
    tool = ManageCuriosity()

    print("\n" + "=" * 65)
    print(" [1] CHECKING CURRENT INTEREST PROFILE & STASH STATUS")
    print("=" * 65)
    interests = await tool.execute(action="get_interests")
    print("Current Category Weights:")
    for cat, wt in interests["categories"].items():
        print(f"  • {cat:<15}: {wt:.2f}")

    print("\nTop Topic Affinities:")
    for top, wt in interests["top_topics"][:5]:
        print(f"  • {top:<15}: {wt:.2f}")

    print("\n" + "=" * 65)
    print(" [2] EXECUTING LIVE ON-DEMAND DISCOVERY (REAL APIS)")
    print("=" * 65)
    print("Querying live providers (Hacker News, GitHub Trending, AniList Releases)...")
    res_disc = await tool.execute(action="discover", limit=4)
    print(f"Discovered and verified {res_disc.get('discovered_count', 0)} new fresh item(s):")
    for it in res_disc.get("items", []):
        print(f"\n  * [{it['category'].upper()}] {it['title']}")
        print(f"    URL:    {it['url']}")
        print(f"    Source: {it['source']} | Final Score: {it['final_score']:.2f}")
        print(f"    Hook:   {it['summary'][:120]}...")

    print("\n" + "=" * 65)
    print(" [3] POPPING TOP ITEM AS CHAD FOR TELEGRAM")
    print("=" * 65)
    pop_res = await tool.execute(action="get_next")
    if pop_res.get("success"):
        item = pop_res["item"]
        print(f"Selected Candidate for Matthew:")
        print(f"  Title:    {item.get('title')}")
        print(f"  Category: {item.get('category')}")
        print(f"  Link:     {item.get('url')}")
        print(f"\nChad's Persona Prompt Directive:")
        print(f"  \"{pop_res.get('tip')}\"")
        print("\nChad's Generated Spontaneous Text:")
        if item.get("category") == "anime":
            print(f"  \"yo new ep of {item.get('title')} drops soon, looks clean: {item.get('url')}\"")
        elif item.get("category") == "github":
            print(f"  \"stumbled on this repo, pretty clever take on local tooling: {item.get('url')}\"")
        else:
            print(f"  \"check this out when you get a second: {item.get('url')}\"")
    else:
        print("  Stash was empty or item already sent.")

    print("\n" + "=" * 65)
    print(" [4] REAL-TIME INTEREST TUNING VIA CONVERSATIONAL FEEDBACK")
    print("=" * 65)
    print("Simulating Matthew replying on Telegram: 'yo that's fire bro'")
    fb_res = await tool.execute(
        action="feedback",
        feedback_text="yo that's fire bro",
        category="github",
        topic="local_llm",
    )
    print(f"Feedback Engine Processed: {fb_res.get('feedback_result', {})}")

    updated = await tool.execute(action="get_interests")
    print("\nUpdated Preferences (Immediate effect):")
    print(f"  • github category : {updated['categories'].get('github'):.2f}")
    print(f"  • local_llm topic : {dict(updated['top_topics']).get('local_llm'):.2f}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
