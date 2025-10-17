# video_commentary.py

import os
import asyncio
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

import aiohttp
from TikTokApi import TikTokApi
import google.genai as genai
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential

# --- Pydantic Model for LLM Output ---
class VideoScript(BaseModel):
    script: str = Field(description="An engaging and humorous 2-3 sentence script to introduce the video. It should be written in A1 English.")

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
DOWNLOADS_DIR = Path("downloads")

class CommentaryGenerator:
    """
    Generates scripts for curated videos, downloads them, and prepares a final
    package for the video editor.
    """
    def __init__(self, gemini_client: genai.Client, tiktok_api: TikTokApi, config: dict):
        self.client = gemini_client
        self.tiktok_api = tiktok_api
        self.model = config.get('gemini_model')
        self.script_prompt_config = config.get('prompts', {}).get('script_generation', {})
        self.MAX_COMMENTS = config.get('max_comments_for_scripting', 20)

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3))
    async def get_comments_for_video(self, video_id: str) -> List[str]:
        """Fetches top comments for context with retry logic."""
        logger.info(f"Fetching comments for video ID {video_id} for script context...")
        try:
            video = self.tiktok_api.video(id=video_id)
            return [comment.text async for comment in video.comments(count=self.MAX_COMMENTS)]
        except Exception as e:
            logger.error(f"Could not fetch comments for video {video_id}: {e}")
            raise

    @retry(wait=wait_exponential(multiplier=2, max=15), stop=stop_after_attempt(3))
    async def generate_script(self, video_data: dict, comments: List[str]) -> Optional[str]:
        """Uses the Gemini LLM to generate a script for a single video."""
        prompt = self.script_prompt_config.get('prompt_template', '').format(
            video_description=video_data.get('description', 'No description provided.'),
            author_username=video_data.get('author_username'),
            comments_json=json.dumps(comments, indent=2)
        )
        logger.info(f"Generating script for video {video_data['video_id']}...")
        try:
            gen_config = {'response_mime_type': 'application/json', 'response_schema': VideoScript}
            response = self.client.generate_content(
                model=self.model,
                contents=prompt,
                generation_config=gen_config,
                system_instruction=self.script_prompt_config.get('system_instruction')
            )
            response_json = json.loads(response.text)
            return response_json.get("script")
        except Exception as e:
            logger.error(f"Failed to generate script for video {video_data['video_id']}: {e}")
            raise

    @retry(wait=wait_exponential(multiplier=2, max=10), stop=stop_after_attempt(3))
    async def download_video(self, project_id: int, video_id: str, video_url: str) -> Optional[str]:
        """Downloads a video from its URL and saves it locally."""
        project_dir = DOWNLOADS_DIR / f"project_{project_id}"
        project_dir.mkdir(parents=True, exist_ok=True)
        file_path = project_dir / f"{video_id}.mp4"
        
        logger.info(f"Downloading video {video_id} to {file_path}...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url) as response:
                    response.raise_for_status()
                    with open(file_path, "wb") as f:
                        f.write(await response.read())
                    logger.info(f"Successfully downloaded {file_path}.")
                    return str(file_path)
        except Exception as e:
            logger.error(f"An error occurred during video download for {video_id}: {e}")
            return None # Don't retry on file write errors etc.

    async def process_single_video(self, project_id: int, video: dict) -> dict:
        """Orchestrates script generation and download for one video."""
        comments = await self.get_comments_for_video(video['video_id'])
        script = await self.generate_script(video, comments)
        
        file_path = None
        if video.get('video_url'):
            file_path = await self.download_video(project_id, video['video_id'], video['video_url'])
        else:
            logger.warning(f"Video {video['video_id']} has no valid URL. Skipping download.")
        
        return {
            "db_id": video['id'],
            "video_id": video['video_id'],
            "author": video['author_username'],
            "description": video['description'],
            "stats": video['stats'],
            "cover_image_url": video['cover_url'],
            "local_file_path": file_path,
            "generated_script": script,
            "script": script, # For db update
            "file_path": file_path # for db update
        }

    async def process_project_videos_concurrently(self, project_id: int, videos_to_process: List[Dict]) -> List[Dict]:
        """Processes all videos for a project concurrently."""
        tasks = [self.process_single_video(project_id, video) for video in videos_to_process]
        processed_videos = await asyncio.gather(*tasks)
        return [v for v in processed_videos if v is not None]

    async def create_summary_file(self, project_id: int, processed_videos: List[Dict]):
        """Creates the final JSON file for the video editor."""
        project_dir = DOWNLOADS_DIR / f"project_{project_id}"
        summary_file_path = project_dir / "video_compilation_data.json"
        
        # Clean up data for the final JSON
        final_data = [{k: v for k, v in video.items() if k not in ['db_id', 'script', 'file_path']} for video in processed_videos]

        with open(summary_file_path, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=4)
        logger.info(f"Final data package saved to {summary_file_path}")



