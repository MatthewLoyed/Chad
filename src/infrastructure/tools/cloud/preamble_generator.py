import hashlib
import logging
from typing import Optional

logger = logging.getLogger("PreambleGenerator")

PREAMBLE_PROMPT = """You are generating a semantic search index document for an AI agent tool.
Your output will be embedded as a vector and matched against real user queries.

Given this tool:
Name: {name}
Description: {description}
Parameters: {parameters}

Generate a structured index document in exactly this format:

INTENT: [One sentence starting with a verb describing what this tool accomplishes]
QUERIES:
- [Short natural user query that needs this tool, 3-8 words]
- [Different phrasing of the same intent]
- [More casual/conversational version]
- [Technical version a developer might type]
- [Edge case or related request this tool handles]
KEYWORDS: [comma-separated synonyms, related concepts, domain terms]

Rules:
- QUERIES must sound like real things a user would type, not descriptions
- Each query must be genuinely different — different vocabulary, not just word order
- Do not use the tool name in the queries unless users would naturally type it
- Do not use "Users invoke this when" or any third-person framing
- Output only the structured document, no explanation"""


MANUAL_PREAMBLES = {
    "get_recent_transactions": """INTENT: Retrieve recent financial transactions across connected bank accounts, credit cards, and institutions (Plaid: SoFi, Capital One, Fidelity, Chase).
QUERIES:
- How much did I spend this month on SoFi and Capital One?
- show my recent transactions
- what did I buy yesterday?
- check my credit card charges
- how much did I spend on groceries this week?
- get transactions for my checking account
KEYWORDS: transactions, spending, spend, purchases, charges, credit card, bank account, sofi, capital one, fidelity, plaid, expenses, bought""",
    "get_financial_summary": """INTENT: Get an overarching financial health snapshot including total balances, net worth, cash flow, debts, and category spending breakdowns.
QUERIES:
- what is my net worth?
- how much money do I have across all accounts?
- show my financial summary
- what are my account balances?
- how is my cash flow looking this month?
KEYWORDS: net worth, account balance, balances, total cash, financial overview, summary, cashflow, assets, liabilities, sofi, capital one, fidelity""",
    "generate_image": """INTENT: Generate an AI image locally via ComfyUI on the local GPU from a descriptive text prompt.
QUERIES:
- generate an image of a dog
- draw a picture of a cat in space
- create an image of a cybernetic warrior
- paint a watercolor landscape of mountains at sunset
- make an illustration of a cute robot companion
- generate art for a fantasy castle
- draw a portrait of a golden retriever
KEYWORDS: generate image, create image, draw, paint, artwork, illustration, photo, picture, wallpaper, comfyui, stable diffusion, render image, text to image""",
    "render_chart": """INTENT: Generate and display visual charts (doughnut, horizontal bar, line runway, sankey) for financial spending breakdowns, budgets, account allocations, or timelines.
QUERIES:
- make a chart of my spending
- visualize my expenses by category
- show a bar chart of my bank balances
- plot my cashflow
- make a pie chart of my monthly budget
KEYWORDS: chart, graph, plot, visualize, figure, doughnut, bar chart, line chart, diagram, render chart, visual report""",
    "manage_user_memory": """INTENT: Store, update, or retrieve long-term facts, preferences, and personal details about the user.
QUERIES:
- remember that I like espresso
- what is my wife's name?
- update my coding preferences in your memory
- forget my previous phone number
- recall facts about my hobbies
KEYWORDS: memory, save fact, forget, recall, remember, preferences, user info""",
    "manage_tool_configs": """INTENT: View, configure, or update operational parameters, API keys, and settings for agent tools.
QUERIES:
- change my gmail api key
- configure hubspace login credentials
- list my current tool settings
- update the system temperature threshold
- check my integration preferences
KEYWORDS: settings, config, preferences, api key, credentials, tools config""",
    "run_host_command": """INTENT: Execute arbitrary terminal commands, git commands, and shell scripts on the host operating system.
QUERIES:
- check git status
- run git push to origin
- run npm install in the workspace
- compile the project using dotnet build
- run a shell command
- git diff and show changes
- git log
- run pytest in root
KEYWORDS: shell, terminal, bash, powershell, command line, execute, script, cli, process, run, system command, git status, git push, git diff, npm, pip, python""",
    "search_gmail": """INTENT: Search, list, and filter recent emails in the user's **Gmail** inbox only (Google mail). Not for Outlook, Apple Mail, Yahoo, or other mail clients — those need `delegate_to_browser_agent` (or a product-specific tool if one exists). When the user says "email"/"inbox" without naming another client, Gmail is the default. Returns metadata and longer snippets (not full bodies); call read_gmail for full thread contents.
QUERIES:
- search my gmail for canvas reviews
- check my emails for updates
- did I get any new mail today?
- list my latest gmail messages
- search my gmail for messages from Sam
- get my recent emails
KEYWORDS: gmail, google mail, google inbox, search gmail, check gmail, list gmail, fetch gmail, recent mail""",
    "read_gmail": """INTENT: Retrieve and read the full conversation thread for a **Gmail** message or thread ID (every message in the thread, not just a single body). Not for Outlook or other mail clients. When the user leaves the mail client unnamed, Gmail is the default.
QUERIES:
- read the gmail content for message id 1234abcd
- show the full text of email 1234abcd
- get the body of the message 1234abcd
- read the full gmail thread for thread id xyz987
- show the email conversation history for thread xyz987
- summarize the full email conversation about canvas reviews
KEYWORDS: gmail, google mail, read gmail, get gmail, message body, full content, gmail thread, conversation, chronological""",
    "gmail_check_inbox": """INTENT: Scan and check the user's **Gmail** inbox specifically for unread emails containing specific keywords. Not for Outlook or other mail clients. When the user leaves the mail client unnamed, Gmail is the default.
QUERIES:
- check if I have unread emails about the server
- check my inbox for new alerts
- are there any new unread messages?
- scan my email inbox for urgent updates
- check my gmail inbox for unread mail
KEYWORDS: gmail, google mail, unread gmail, gmail inbox, scan gmail, check gmail, unread emails, check gmail inbox""",
    "open_app": """INTENT: Launch, open, and start desktop applications on the host operating system.
QUERIES:
- open discord and slack
- start google chrome
- launch visual studio code
- open spotify
- start steam
KEYWORDS: open app, launch application, start program, open program, run app, launch, open, discord, slack, chrome""",
    "capture_screen": """INTENT: Capture a screenshot of the current active screen or desktop window.
QUERIES:
- take a screenshot
- capture my current screen
- look at what is on my screen
- take a snap of the window
- screenshot the desktop
KEYWORDS: screenshot, capture screen, take screenshot, screen snap, look at screen, active window, desktop image""",
    "system_settings": """INTENT: Control and adjust local PC hardware settings like screen brightness, battery, or sleep.
QUERIES:
- raise the screen brightness to max
- dim my screen brightness to 50%
- check my battery level
- adjust PC display brightness
- set screen brightness to low
KEYWORDS: brightness, screen brightness, display settings, screen dim, hardware, battery, display brightness""",
    "control_volume": """INTENT: Control, mute, unmute, and adjust the system master audio volume.
QUERIES:
- increase my system audio volume to 80%
- mute my computer audio
- turn up the volume
- set master volume to 50%
- unmute system sound
KEYWORDS: volume, sound, audio, mute, unmute, master volume, system volume, speaker volume, turn up sound""",
    "add_task": """INTENT: Create and add a new Todoist task or reminder to the todo list.
QUERIES:
- add a new urgent task 'buy groceries' due tomorrow
- add buy groceries to my todoist inbox
- create a new task buy milk tomorrow
- put research paper on my todo list
- add task buy groceries
- put 'plan game night with friends' on my dashboard
- add this to my dashboard tasks
KEYWORDS: todoist, task, add task, todo list, create task, reminder, urgent task, add todo, dashboard, dashboard task, put on dashboard""",
    "fuzzy_find_local": """INTENT: Find a local file by fuzzy-matching part of its name, without knowing the exact path.
QUERIES:
- find file with prompt in name
- where's that resume file on my computer
- find the physics notes file
- look for a file called invoice somewhere on my pc
- search my computer for a file named budget
KEYWORDS: find file, fuzzy find, search local files, locate file, file search, filename search, find on computer""",
    "read_file": """INTENT: Read the text contents of a local file by path.
QUERIES:
- read the contents of setup.txt
- open and read this file
- show me what's in that markdown file
- read the notes file on my desktop
KEYWORDS: read file, open file, file contents, view file, local file, read text""",
    "write_file": """INTENT: Write or save text content to a new or existing local file.
QUERIES:
- save a three-bullet summary of the setup steps to a markdown file on my desktop
- write this summary to a file
- create a text file with these notes
- save this list to a file on my computer
KEYWORDS: write file, save file, create file, save to desktop, save markdown, write text, save summary""",
    "list_directory": """INTENT: List the files and folders inside a local directory.
QUERIES:
- what's on my desktop
- show me the files in my downloads folder
- list what's in this folder
- what files are on my computer here
KEYWORDS: list directory, list files, folder contents, browse folder, desktop files, list folder""",
    "call_mcp_tool": """INTENT: Execute a tool on a configured MCP server for capabilities not exposed natively.
QUERIES:
- search the web with brave
- use github mcp to list issues
- call an mcp server tool
- run a tool on the notion server
- use fetch mcp to read a url
KEYWORDS: mcp, model context protocol, server tool, brave search, github, notion, fetch, playwright""",
    "show_youtube_video": """INTENT: Display a YouTube video as an inline player card in the chat UI.
QUERIES:
- show me a youtube video about skyrizi
- find and play a youtube video on psoriasis
- open a yt video in chat
- display this youtube link in the chat
- watch a youtube video about cooking
KEYWORDS: youtube, yt video, show video, inline player, watch video, video card, skyrizi""",
    "play_youtube_music": """INTENT: Search YouTube Music and open a song or playlist in the user's browser.
QUERIES:
- play me some anime music
- put on lo-fi beats
- play a chill playlist on youtube music
- find anime OSTs and play one
- play Joe Hisaishi on youtube music
KEYWORDS: youtube music, play music, put on, playlist, anime music, lo-fi, ost, soundtrack, listen to""",
    "geocode": """INTENT: Convert a place name or address to latitude/longitude via OpenStreetMap. Load osm-maps skill first for places workflows.
QUERIES:
- geocode times square
- what are the coordinates of the eiffel tower
- convert this address to lat lon
- find the location of downtown austin
KEYWORDS: geocode, coordinates, latitude, longitude, address lookup, openstreetmap, nominatim""",
    "search_places": """INTENT: Find nearby points of interest (restaurants, cafes, pharmacies, parks, etc.) via OpenStreetMap. Prefer amenity filters. Load skill osm-maps; finish with show_map. No Google ratings.
QUERIES:
- find restaurants near me
- cafes within 1km of downtown
- pharmacies near the hotel
- parks around central park
- italian restaurants near times square
KEYWORDS: places, restaurants, cafes, nearby, poi, openstreetmap, amenity, map search, local businesses""",
    "get_route": """INTENT: Get turn-by-turn driving, walking, or cycling directions between two coordinates via OSRM. Geocode both ends first. Load osm-maps skill.
QUERIES:
- directions from home to the airport
- walking route to the cafe
- how long to drive from A to B
- bike route between these places
KEYWORDS: directions, route, driving, walking, cycling, osrm, eta, navigation""",
    "show_map": """INTENT: Display an interactive OpenStreetMap card in chat with place markers. Call after geocode/search_places using real coordinates only. Load osm-maps skill first.
QUERIES:
- show these places on a map
- put the restaurants on a map
- display a map of downtown cafes
- map markers for these locations
KEYWORDS: show map, map card, leaflet, openstreetmap, markers, places map, inline map""",
    "search": """INTENT: Search Google Drive files and folders by name or query.
QUERIES:
- find my resume in google drive
- search drive for the budget spreadsheet
- look for homework folder in drive
- find documents named project plan
KEYWORDS: google drive, drive search, find file, search files, gdrive""",
    "listFolder": """INTENT: List files and folders inside a Google Drive folder.
QUERIES:
- list files in my drive folder
- show what's in the homework folder
- browse drive folder contents
- list google drive directory
KEYWORDS: google drive, list folder, browse drive, folder contents, drive files""",
    "downloadFile": """INTENT: Read or download the contents of a Google Drive file.
QUERIES:
- read a google doc from drive
- download file from drive
- get contents of my drive document
- open drive file by id
KEYWORDS: google drive, read file, download, drive file, doc contents""",
    "google_drive_search": """INTENT: Search Google Drive for files and folders by name or keywords.
QUERIES:
- find my resume in google drive
- search drive for the budget spreadsheet
- look for wedding folder in drive
- find documents named project plan
KEYWORDS: google drive, drive search, find file, search files, gdrive""",
    "google_drive_list_folder": """INTENT: List files and folders inside a Google Drive folder.
QUERIES:
- list files in my drive folder
- show what's in the homework folder
- browse drive folder contents
- list google drive directory
KEYWORDS: google drive, list folder, browse drive, folder contents, drive files""",
    "google_drive_read_file": """INTENT: Read the text content of a Google Drive file by ID or share URL (Docs/Drive link). Use when DOCUMENT CONTEXT (DRIVE) lists a file with id= and the user needs what's inside.
QUERIES:
- read a google doc from drive
- get contents of my drive document
- open drive file by id
- read spreadsheet from drive
- read the wedding roadmap from document context
KEYWORDS: google drive, read file, doc contents, drive file, get content, file id, document context""",
    "google_drive_create_doc": """INTENT: Create a new Google Doc with a title and optional content.
QUERIES:
- create a new google doc
- make a google document called meeting notes
- write a new doc in drive
KEYWORDS: google drive, create doc, new document, google doc, make doc""",
    "google_drive_edit_doc": """INTENT: Edit an existing Google Doc by appending or find-and-replace.
QUERIES:
- append text to my google doc
- replace text in a google document
- update my drive doc
- add paragraph to google doc
KEYWORDS: google drive, edit doc, update document, append, find replace, google doc""",
    "compile_typst": """INTENT: Compile a designed, print-ready PDF document from Typst markup for reports, memos, letters, and write-ups.
QUERIES:
- create a pdf about how to play spikeball
- make me a pdf report on this topic
- turn this outline into a designed PDF
- write a formatted PDF document I can download
- generate a professional-looking PDF memo
KEYWORDS: pdf, document, report, memo, letter, typst, compile, designed pdf, print, write-up, downloadable pdf""",
    "render_diagram": """INTENT: Render a structured flowchart, sequence, architecture, or ER diagram from Mermaid/PlantUML/D2/GraphViz source using Kroki.
QUERIES:
- make me a flowchart diagram
- draw a sequence diagram for this API
- create an architecture diagram
- render an entity relationship diagram
- turn this process into a mermaid diagram
KEYWORDS: diagram, flowchart, sequence diagram, architecture diagram, mermaid, plantuml, graphviz, kroki, er diagram, chart""",
    "render_marp": """INTENT: Render a quick markdown slide deck to PDF, PPTX, or HTML using Marp.
QUERIES:
- make me quick slides from this outline
- turn these notes into a markdown slide deck
- create a fast presentation from bullet points
- render markdown slides to pdf
KEYWORDS: slides, slide deck, presentation, marp, markdown slides, quick deck, pptx, export slides""",
    "render_animation": """INTENT: Render a screenshot, filmstrip, or before/after diff of an animated SVG/HTML/CSS snippet so a model can visually check what it wrote.
QUERIES:
- show me what this animation looks like
- take a screenshot of this animated svg
- compare before and after css animation frames
- render a filmstrip of this CSS animation
KEYWORDS: animation preview, svg animation, css animation, screenshot, filmstrip, visual diff, headless render""",
    "generate_presentation": """INTENT: Generate a polished, AI-branded slide deck (PPTX or PDF) from a topic, outline, or document summary via Presenton.
QUERIES:
- make me a professional slide deck about this topic
- create a branded presentation from this outline
- generate a polished pptx deck
- build a designed slideshow from my document
KEYWORDS: slide deck, presentation, pptx, branded deck, ai slides, presenton, polished deck, designed slides""",
    "update_dashboard": """INTENT: Post a short entry (thought, life update, system status, or discovery) to the user's personal Dashboard feed widget.
QUERIES:
- post this as a thought on my dashboard
- add a status update to my dashboard feed
- share this discovery on my dashboard
- pin this as a life update on my dashboard
KEYWORDS: dashboard feed, thought, status update, discovery, life widget, pinned, post to dashboard feed""",
    "browser_click": """INTENT: Click an element (button, link, field) on the currently open web page in the browser.
QUERIES:
- click the login button on this page
- click that link in the browser
- press the submit button on the webpage
- click on the search icon
KEYWORDS: click, browser click, press button, click link, click element, web page interaction""",
    "browser_navigate": """INTENT: Navigate the open browser to a specific URL or web page.
QUERIES:
- go to this website in my browser
- open this URL in the browser
- navigate the browser to this page
- load this webpage
KEYWORDS: navigate, go to url, open website, load page, browser navigation, visit site""",
    "browser_snapshot": """INTENT: Capture an accessibility snapshot of the currently open web page to see what's on it (e.g. checking a dashboard or webpage in the browser).
QUERIES:
- check what's on the Supabase dashboard in my browser
- see what's currently showing on this webpage
- look at the page that's open in the browser
- check my browser tab for what's displayed
KEYWORDS: check dashboard in browser, see webpage, view open page, browser snapshot, look at browser tab, check what's on screen in browser""",
    "manage_curiosity": """INTENT: Discover, retrieve, or inspect verified media (YouTube shorts, tech articles, GitHub repos, anime releases) and tune content interest preferences.
QUERIES:
- find me interesting tech articles or github repos
- discover some cool youtube shorts about spikeball
- check the latest anime releases and episode drops
- tune my content interests to show more anime
- get a fresh discovery to share
- what's in my curiosity stash?
- record feedback on that discovery link
KEYWORDS: content discovery, curiosity, youtube shorts, tech articles, github repos, anime releases, spikeball, interest tuning, discovery pipeline, stash, trending repos""",
}

def _static_fallback(tool_name: str) -> str:
    return f"INTENT: Use the {tool_name} tool.\nQUERIES:\n- run {tool_name}\n- use {tool_name}\nKEYWORDS: {tool_name}"


class PreambleGenerator:
    def __init__(self):
        self.adapter = None

    def _build_local_adapter(self):
        """Builds a LiteLLMAdapter pointed at the user's active local model (LM Studio/Ollama),
        mirroring the local-vs-cloud selection in base_skill.py. Returns None if no local
        model is configured — there is no cloud fallback for this path.

        Under ``CHAD_TESTING`` (eval dry-run) this returns None so we never JIT-load the
        main chat model (Gemma 26B) for background preamble work (#632).
        """
        import os
        if os.getenv("CHAD_TESTING") == "true":
            logger.info("[ PREAMBLE ] Skipping local-adapter build under CHAD_TESTING (no Gemma JIT).")
            return None

        from src.shared.config_manager import ConfigManager
        if not ConfigManager.is_local_model_active():
            return None

        from src.core.intelligence.adapters.litellm_adapter import LiteLLMAdapter
        local_cfg = ConfigManager.get_local_model_config()
        provider = local_cfg.get("provider", "lmstudio")
        provider_cfg = ConfigManager.get_local_provider_config(provider)

        model_name = local_cfg.get("name", "")
        if model_name and "/" not in model_name:
            model_name = f"{provider}/{model_name}"

        return LiteLLMAdapter(
            model_name=model_name,
            base_url=local_cfg.get("base_url") or provider_cfg.get("base_url"),
            api_key=local_cfg.get("api_key") or provider_cfg.get("api_key"),
        )

    async def generate_preamble(self, tool_name: str, description: str, parameters: Optional[str] = None) -> str:
        """Generates a user-intent preamble for a tool using the user's local model
        (with manual overrides). Falls back to a static, content-free preamble when no
        local model is configured or generation fails — never calls a cloud API."""
        if tool_name in MANUAL_PREAMBLES:
            logger.info(f"Using manual intent preamble override for: {tool_name}")
            return MANUAL_PREAMBLES[tool_name]

        from src.shared.config_manager import ConfigManager
        is_auto_enabled = ConfigManager.get("tool_sync.automatic_preambles", True)
        if not is_auto_enabled:
            return _static_fallback(tool_name)

        try:
            if self.adapter is None:
                self.adapter = self._build_local_adapter()
            if self.adapter is None:
                logger.info(f"No local model active; using static fallback preamble for: {tool_name}")
                return _static_fallback(tool_name)

            prompt = PREAMBLE_PROMPT.format(name=tool_name, description=description, parameters=parameters or "None")
            messages = [{"role": "user", "content": prompt}]

            response = await self.adapter.generate(messages, temperature=0.0)
            preamble = (response.text or "").strip()
            if not preamble:
                return _static_fallback(tool_name)

            # Clean up potential markdown or quotes
            preamble = preamble.replace('"', '').replace("'", "").strip()

            return preamble
        except Exception as e:
            logger.error(f"Failed to generate preamble for {tool_name}: {e}")
            return _static_fallback(tool_name)

def get_description_hash(description: str) -> str:
    """Returns MD5 hash of the tool description for change detection."""
    return hashlib.md5(description.encode()).hexdigest()
