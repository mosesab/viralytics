# Viral Video Content Pipeline Automator

This project is a complete, automated content pipeline for a YouTube channel that creates compilation and commentary videos based on internet culture and viral trends. It automates the entire process from discovering relevant trends to fetching, analyzing, and curating video clips, and finally generating scripts and downloading the source material for a human video editor.

The system is managed through a clean, modern web interface built with FastAPI and vanilla JavaScript, providing real-time logging and control over the entire workflow.

## Features

-   **Automated Trend Discovery**: Uses Google Trends (`Pytrends`) to find currently trending topics.
-   **AI-Powered Curation**: Leverages a Google Gemini LLM to analyze trends and select only those relevant to the channel's niche, preventing topic drift.
-   **Video Sourcing**: Fetches dozens of relevant video clips from TikTok based on the AI-selected trends.
-   **Intelligent Video Analysis**:
    -   Performs sentiment analysis on video comments using Hugging Face Transformers to gauge audience reception.
    -   Calculates a custom engagement score (`(likes + comments) / views`) to identify videos that truly resonate.
-   **Automated Filtering**: Filters a large pool of sourced videos down to the top 20 best clips based on positive sentiment and high engagement.
-   **AI Script Generation**: Uses the comments for context and Google Gemini to write short, humorous, and engaging introductory scripts for each of the top 20 videos.
-   **Content Packaging**: Downloads the final video clips and packages all the data (scripts, video info, local file paths) into a single `json` file, ready for a human video editor.
-   **Web-Based UI**: A simple and intuitive interface to create projects, run each step of the pipeline individually, or execute the entire workflow automatically.
-   **Real-time Logging**: A WebSocket connection provides live updates and logs directly in the browser, so you can monitor the progress of each task.

## Architecture Overview

The application uses a decoupled, event-driven architecture orchestrated by a central FastAPI server (`main.py`). The server manages the application state via an `aiosqlite` database and a `config.yaml` file. It injects dependencies (like API clients and configuration) into stateless modules that perform the actual work.

```
+------------------+      +-------------------+      +--------------------+
|   Web Browser    |----->|   FastAPI Server  |----->|     Database       |
| (HTML/CSS/JS)    |<-----|     (main.py)     |<-----| (video_projects.db)|
+------------------+      +--------+----------+      +---------^----------+
       ^                           |                             |
       | WebSocket (Live Status)   | (Orchestrates & Injects Deps) |
       |                           |                             |
+------v---------------------------v-----------------------------+----------+
|                                  |                +--------------------+  |
|   Stateless Python Modules       <----------------+    Configuration   |  |
|                                                   |     (config.yaml)  |  |
|   [1. Trend Analyzer] -> [2. Video Fetcher] ->    +--------------------+  |
|   [3. Video Analyzer] -> [4. Content Generator]                           |
|   (CPU tasks offloaded to separate processes)                             |
|                                                                           |
+---------------------------------------------------------------------------+
```

1.  **Frontend**: The user interacts with the `index.html` file, sending requests to the backend and receiving live status updates and logs via WebSockets.
2.  **FastAPI Server**: The orchestrator. It handles API requests, manages the database, reads from `config.yaml`, and controls the workflow execution. It creates shared API clients and injects them into the backend modules.
3.  **Database**: An `aiosqlite` database stores all project data, including trends, video metadata, analysis results, and the pause/resume state.
4.  **Configuration**: A `config.yaml` file holds all tunable parameters, prompts, and application settings, allowing for easy modification of the pipeline's behavior.
5.  **Backend Modules**: Each module is responsible for a single step and is designed to be stateless. They receive dependencies from `main.py` and return data, but do not interact with the database directly.

## Tech Stack

-   **Backend**: Python, FastAPI, aiosqlite, Pydantic
-   **Frontend**: HTML5, CSS3, JavaScript (with jQuery)
-   **AI & Machine Learning**:
    -   `google-generativeai` for trend selection and script generation.
    -   `TextBlob` & `NRCLex` for fast sentiment and emotion analysis.
-   **Data Sourcing & APIs**:
    -   `pytrends` for Google Trends data.
    -   `TikTokApi` (unofficial) for scraping video data.
-   **Configuration**: PyYAML
-   **Resilience**: Tenacity for robust API call retries.
-   **Concurrency**: `asyncio`, `ProcessPoolExecutor`

## How To Configure

Configuration is split between environment variables (for secrets) and a configuration file (for tunable parameters).

#### 1. Environment Variables
Edit the `.env` file with your secret keys.

```bash
# .env

# Required: Get this from Google AI Studio or Google Cloud
GEMINI_API_KEY="YOUR_GEMINI_API_KEY"

# Optional but highly recommended for TikTokApi reliability.
# See Readme.md for instructions on how to get this from your browser.
MS_TOKEN="YOUR_TIKTOK_MS_TOKEN"
```

#### 2. Configuration File
Modify `config.yaml` to tune the pipeline's behavior. You can change AI models, tweak prompts, and adjust analysis parameters here.


## How to Run

1.  Install the required Python libraries:
    ```bash
    pip install -r requirements.txt
    ```
2.  Install and setup playwright (It is a TikTokApi dependency):
    ```bash
    playwright install
    playwright install-deps  
    ```
3.  Start the FastAPI server:
    ```bash
    python main.py
    ```
4.  Once the server is running, open your web browser and navigate to:
    **http://127.0.0.1:8000**

## How to Use the Application

1.  **Create a Project**: On the web UI, click "New Project". Give your project a name and, importantly, provide a **Channel Description**. This description guides the AI in selecting relevant trends for that specific project.

2.  **Run the Workflow**:
    -   **Manual Control**: Click the buttons (`1. Analyze Trends`, `2. Fetch Videos`, etc.) in order. The status indicator next to each button will show its state (pending, running, complete).
    -   **Automatic Mode**: Click `Run All Steps` to execute the entire pipeline from start to finish.
    -   **Pause/Resume**: Click `Pause Workflow` to stop the pipeline after the currently executing step is finished. Click `Resume Workflow` to continue.

3.  **Review Results**: The UI will populate with data in real-time as each step completes. You can see the trends the AI selected, the final curated videos, and all other videos that were fetched but didn't make the cut.

4.  **Access Your Content**: Once the "Generate Content" step is complete, a `downloads/project_<id>` folder will be created. This folder contains all the downloaded `.mp4` video clips and a `video_compilation_data.json` file with all the metadata and generated scripts, ready for a video editor.

## File Structure

```
.
├── downloads/              # Output directory for downloaded videos and JSON
├── .env                    # API keys and environment variables
├── config.yaml             # NEW: All tunable parameters and prompts
├── database.py             # Manages SQLite connection and all queries
├── index.html              # The main web interface
├── main.py                 # The FastAPI application orchestrator
├── Readme.md               # This file
├── requirements.txt        # Python dependencies
├── trend_analyzer.py       # Step 1: Fetches and analyzes trends
├── video_analyzer.py       # Step 3: Analyzes and filters videos
├── video_commentary.py     # Step 4: Generates scripts and downloads videos
└── video_fetcher.py        # Step 2: Fetches videos from TikTok
```

## Future Improvements

-   **TTS Integration**: Add a Text-to-Speech service (like ElevenLabs) to automatically generate voice-overs for the scripts.
    For open source, you could just use https://github.com/remsky/Kokoro-FastAPI hosted on a VPS.
-   **AI Influencer Reactions**: Integrate a video generation model to create an AI character that reacts to the clips, adding another layer of transformative content.
    For open source, you could just use Genmo Mochi 1 or Rhymes Allegro generating reactions. You can determine the timing and reactions by using a VLM like InternVL.
-   **Advanced UI**: Transition from jQuery to a modern frontend framework like Vue.js or React for more complex state management.
-   **Webshare Proxy**: Add a webshare proxy to the TikTok API, if necessary.
-   **Error Resilience**: Implement a more robust job queue system (like Celery) to handle retries and failures for long-running tasks.

## Disclaimer

This project uses an unofficial TikTok API and is intended for educational purposes. Scraping content from platforms may be against their Terms of Service. Please use this tool responsibly.

When creating compilation videos, it is crucial to adhere to the principles of **Fair Use**. This includes adding commentary, criticism, or parody, and always crediting the original creators in your video and description. This project aims to facilitate transformative works, not to enable plagiarism.
