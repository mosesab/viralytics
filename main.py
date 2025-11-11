# main.py

import os
import asyncio
import logging
import logging.handlers
import json
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
import yaml

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from TikTokApi import TikTokApi
import google.genai as genai


import database
from trend_analyzer import TrendAnalyzer
from video_fetcher import TikTokApiFetcher
from video_analyzer import VideoAnalyzer
from video_commentary import CommentaryGenerator


# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# --- App Setup ---
load_dotenv()

# --- Configuration Loader ---
@lru_cache()
def get_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)

# --- Platform-Specific Asyncio Policy ---
# This is the fix for the NotImplementedError on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# --- WebSocket Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

    async def broadcast_json(self, data: dict):
        await self.broadcast(json.dumps(data))

manager = ConnectionManager()

# --- Shared API Clients / Dependency Injection ---
shared_clients = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup
    await database.initialize_database()
    ms_token = os.getenv("MS_TOKEN")
    if not ms_token:
        logger.warning("MS_TOKEN environment variable not set. TikTokApi may be unreliable or fail.")
    
    # Create a single, shared TikTokApi instance
    shared_clients["tiktok_api"] = TikTokApi()
    await shared_clients["tiktok_api"].create_sessions(ms_tokens=[ms_token], num_sessions=1, sleep_after=3)
    yield
    # On shutdown
    logger.info("Application shutting down.")
    if "tiktok_api" in shared_clients:
        await shared_clients["tiktok_api"].close_sessions()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="."), name="static")


def get_tiktok_api():
    return shared_clients["tiktok_api"]

def get_gemini_client(api_key: str | None = None):
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY not provided.")
    return genai.Client(api_key=key)

# --- Pydantic Models for API ---
class ProjectCreate(BaseModel):
    name: str
    channel_description: str

class ApiKeys(BaseModel):
    gemini_api_key: str | None = None

class TogglePausePayload(BaseModel):
    is_paused: bool

class RunPayload(BaseModel):
    gemini_api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    region: str = "US"


# --- Utility Functions ---
async def log_and_broadcast(message: str, type: str = "log"):
    logger.info(message)
    await manager.broadcast_json({"type": type, "message": message})

async def broadcast_status(step: str, status: str, project_id: int):
    await manager.broadcast_json({
        "type": "status_update",
        "step": step,
        "status": status,
        "project_id": project_id
    })


# --- API Endpoints ---
@app.get("/")
async def read_index():
    return FileResponse('index.html')

# Project Management
@app.get("/projects")
async def get_projects():
    return await database.get_all_projects()

@app.post("/projects")
async def create_project_endpoint(project: ProjectCreate):
    project_id = await database.create_project(project.name, project.channel_description)
    return {"id": project_id, "name": project.name, "channel_description": project.channel_description}

@app.get("/projects/{project_id}/summary")
async def get_project_summary(project_id: int):
    summary = await database.get_project_summary(project_id)
    if not any(summary.values()): # Check if all lists are empty
        project_exists = await database.get_project(project_id)
        if not project_exists:
             raise HTTPException(status_code=404, detail="Project not found")
    return summary
    
@app.post("/projects/{project_id}/pause")
async def toggle_pause(project_id: int, payload: TogglePausePayload):
    await database.toggle_project_pause(project_id, payload.is_paused)
    status = "paused" if payload.is_paused else "resumed"
    await log_and_broadcast(f"Project {project_id} has been {status}.")
    return {"message": f"Project {project_id} state set to {status}."}

# --- Background Workflow Tasks ---
def get_overridden_config(payload: RunPayload, default_config: dict) -> dict:
    """Merges the payload overrides into a copy of the default config."""
    config = default_config.copy()
    if payload.model:
        config['gemini_model'] = payload.model
    # Note: temperature and max_tokens are not in the base config, 
    # but could be added to be used by the LLM client if needed.
    if payload.temperature:
        config.setdefault('llm', {})['temperature'] = payload.temperature
    if payload.max_tokens:
        config.setdefault('llm', {})['max_tokens'] = payload.max_tokens
    return config

async def run_trends_task(project_id: int, payload: RunPayload, config: dict):
    await broadcast_status("trends", "running", project_id)
    project_data = await database.get_project(project_id)
    if not project_data:
        raise HTTPException(status_code=404, detail="Project not found")

    analyzer = TrendAnalyzer(gemini_client=get_gemini_client(payload.gemini_api_key), config=config)
    
    await log_and_broadcast("Starting trend analysis...")
    trends_list = await analyzer.get_google_trends()
    
    if trends_list:
        selected_trends_data = await analyzer.select_best_trends(
            trends=trends_list,
            channel_description=project_data['channel_description']
        )
        if selected_trends_data:
            await database.save_trends_to_db(project_id, selected_trends_data['selected_trends'])
            await log_and_broadcast("Trend analysis complete.")
            await broadcast_status("trends", "complete", project_id)
        else:
            await log_and_broadcast("Failed to select trends.", type="error")
            await broadcast_status("trends", "error", project_id)
    else:
        await log_and_broadcast("Failed to fetch Google Trends data.", type="error")
        await broadcast_status("trends", "error", project_id)


async def run_fetch_task(project_id: int, payload: RunPayload, config: dict, tiktok_api: TikTokApi):
    await broadcast_status("fetch", "running", project_id)
    
    fetcher = TikTokApiFetcher(tiktok_api=tiktok_api)
    await log_and_broadcast(f"Fetching top 50 trending videos for region: '{payload.region}'")
    videos = await fetcher.fetch_trending_videos(count=50, region=payload.region)
    if videos:
        # Using region as the 'keyword' for grouping in the DB
        await database.save_fetched_videos_to_db(project_id, payload.region, videos)
    await log_and_broadcast("Video fetching complete.")
    await broadcast_status("fetch", "complete", project_id)


async def run_analyze_task(project_id: int, payload: RunPayload, config: dict, tiktok_api: TikTokApi):
    await broadcast_status("analyze", "running", project_id)
    analyzer = VideoAnalyzer(tiktok_api=tiktok_api, config=config)
    
    videos_to_analyze = await database.get_videos_for_analysis(project_id)
    if not videos_to_analyze:
        await log_and_broadcast("No new videos to analyze.")
        await broadcast_status("analyze", "complete", project_id)
        return

    await log_and_broadcast(f"Starting analysis for {len(videos_to_analyze)} videos...")
    top_videos, all_analyzed_videos = await analyzer.analyze_and_filter_videos_concurrently(videos_to_analyze)
    
    if all_analyzed_videos:
        await database.update_videos_with_analysis(
            project_id, all_analyzed_videos, [v['video_id'] for v in top_videos]
        )
    await log_and_broadcast("Video analysis and curation complete.")
    await broadcast_status("analyze", "complete", project_id)


async def run_generate_task(project_id: int, payload: RunPayload, config: dict, tiktok_api: TikTokApi):
    await broadcast_status("generate", "running", project_id)
    generator = CommentaryGenerator(
        gemini_client=get_gemini_client(payload.gemini_api_key),
        tiktok_api=tiktok_api,
        config=config
    )

    videos_to_process = await database.get_top_videos_for_commentary_generation(project_id)
    if not videos_to_process:
        await log_and_broadcast("No top videos ready for content generation.")
        await broadcast_status("generate", "complete", project_id)
        return

    await log_and_broadcast(f"Starting content generation for {len(videos_to_process)} videos...")
    processed_videos = await generator.process_project_videos_concurrently(project_id, videos_to_process)
    
    for video_data in processed_videos:
        await database.update_video_with_generated_commentary(
            video_data['db_id'], video_data['script'], video_data['file_path']
        )
        
    await generator.create_summary_file(project_id, processed_videos)
    await log_and_broadcast("Content generation complete.")
    await broadcast_status("generate", "complete", project_id)


@app.post("/run/{step}/{project_id}")
async def run_step(step: str, project_id: int, payload: RunPayload,
                   default_config: dict = Depends(get_config),
                   tiktok_api: TikTokApi = Depends(get_tiktok_api)):
    
    config = get_overridden_config(payload, default_config)

    tasks = {
        "trends": run_trends_task,
        "fetch": run_fetch_task,
        "analyze": run_analyze_task,
        "generate": run_generate_task,
    }
    if step in tasks:
        task_args = (project_id, payload, config)
        if step in ["fetch", "analyze", "generate"]:
            task_args += (tiktok_api,)

        try:
            asyncio.create_task(tasks[step](*task_args))
            return {"message": f"Step '{step}' started for project {project_id}."}
        except Exception as e:
            await log_and_broadcast(f"Error starting step {step}: {str(e)}", type="error")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="Step not found")


@app.post("/run/all/{project_id}")
async def run_all_steps(project_id: int, payload: RunPayload,
                        default_config: dict = Depends(get_config),
                        tiktok_api: TikTokApi = Depends(get_tiktok_api)):
    
    config = get_overridden_config(payload, default_config)

    async def workflow():
        steps = [
            (run_trends_task, "trends"),
            (run_fetch_task, "fetch"),
            (run_analyze_task, "analyze"),
            (run_generate_task, "generate")
        ]
        try:
            for task_func, name in steps:
                if await database.is_project_paused(project_id):
                    await log_and_broadcast(f"Workflow for project {project_id} is paused. Halting execution.", type="log")
                    await broadcast_status(name, "paused", project_id)
                    return
                
                task_args = (project_id, payload, config)
                if name in ["fetch", "analyze", "generate"]:
                    task_args += (tiktok_api,)

                await task_func(*task_args)
                await asyncio.sleep(1) 

            await log_and_broadcast("Full workflow completed successfully!")
            await manager.broadcast_json({"type": "workflow_complete", "project_id": project_id})
        except Exception as e:
            error_message = f"An error occurred during the workflow for project {project_id}: {str(e)}"
            await log_and_broadcast(error_message, type="error")

    asyncio.create_task(workflow())
    return {"message": f"Full workflow started for project {project_id}."}


# --- Web Socket ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text() # Keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected from WebSocket.")


# --- Uvicorn Runner ---
if __name__ == "__main__":
    logger.info(f"--- Script Starting --- \nPython {sys.version} \n-----------------------------")
    logger.info(f"Starting Uvicorn server on host 0.0.0.0, port 8000")
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)