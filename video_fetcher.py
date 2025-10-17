# video_fetcher.py

import logging
from typing import List, Dict, Optional

from TikTokApi import TikTokApi
from TikTokApi.exceptions import TikTokException
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TikTokApiFetcher:
    """
    Fetches videos using a shared TikTok-Api instance.
    """
    def __init__(self, tiktok_api: TikTokApi):
        self.api = tiktok_api

    @retry(wait=wait_exponential(multiplier=2, max=10), stop=stop_after_attempt(5))
    async def fetch_videos(self, keyword: str, count: int) -> Optional[List[Dict]]:
        """
        Fetches trending TikTok videos for a given keyword with retry logic.
        """
        logger.info(f"Fetching {count} videos for keyword: '{keyword}'...")
        videos_data = []
        try:
            async for video in self.api.search.videos(keyword, count=count):
                video_dict = video.as_dict
                processed_video = {
                    "video_id": video_dict.get("id"),
                    "author_username": video_dict.get("author", {}).get("uniqueId"),
                    "create_time": video_dict.get("createTime"),
                    "description": video_dict.get("desc"),
                    "video_url": video_dict.get("video", {}).get("playAddr"),
                    "cover_url": video_dict.get("video", {}).get("cover"),
                    "stats": video_dict.get("stats", {}),
                }
                videos_data.append(processed_video)
                if len(videos_data) >= count:
                    break
            
            logger.info(f"Successfully fetched {len(videos_data)} videos for '{keyword}'.")
            return videos_data

        except TikTokException as e:
            logger.error(f"A TikTok-Api error occurred while fetching for '{keyword}': {e}")
            raise # Reraise to trigger tenacity retry
        except Exception as e:
            logger.error(f"An unexpected error occurred during video fetching for '{keyword}': {e}")
            raise # Reraise to trigger tenacity retry

        