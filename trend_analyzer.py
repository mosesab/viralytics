# trend_analyzer.py

import asyncio
import json
import logging
from typing import List, Dict, Optional

from pytrends.request import TrendReq
import google.genai as genai
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

# --- Pydantic Models for LLM Output ---
class Trend(BaseModel):
    keyword: str = Field(description="The trending keyword or phrase.")
    justification: str = Field(description="A brief explanation of why this trend is a good fit for the channel.")
    suggested_video_title: str = Field(description="A catchy, YouTube-friendly title idea based on this trend.")
    long_term_potential: bool = Field(description="A boolean that is True if the trend has lasting relevance, False if it is a short-term spike.")

class SelectedTrends(BaseModel):
    selected_trends: List[Trend] = Field(description="A list of the best trends to create a YouTube video on.")

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TrendAnalyzer:
    """
    Fetches trending topics from Google Trends and uses an LLM to select the most
    relevant ones for a specific YouTube channel.
    """
    def __init__(self, gemini_client: genai.Client, config: dict):
        self.pytrends = TrendReq(hl='en-US', tz=360)
        self.client = gemini_client
        self.model = config['gemini_model']
        self.config = config.get('prompts', {}).get('trend_selection', {})

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3))
    async def get_google_trends(self) -> List[str]:
        """
        Fetches top trends. Uses asyncio.to_thread to run the blocking
        pytrends library in a non-blocking way. Retries on failure.
        """
        loop = asyncio.get_running_loop()
        
        def fetch_trends():
            try:
                logger.info("Fetching Google Trends data...")
                daily = self.pytrends.trending_searches(pn='united_states').iloc[:, 0].tolist()
                realtime = self.pytrends.realtime_trending_searches(count=20, category='all', pn='US')['title'].tolist()
                combined = list(dict.fromkeys(daily + realtime))
                logger.info(f"Found {len(combined)} unique trends.")
                return combined
            except Exception as e:
                logger.error(f"Failed to fetch Google Trends: {e}")
                raise # Reraise to trigger tenacity retry

        return await loop.run_in_executor(None, fetch_trends)

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3))
    async def select_best_trends(self, trends: List[str], channel_description: str) -> Optional[dict]:
        """
        Uses the Gemini LLM to analyze and select the best trends for video content.
        Retries on failure.
        """
        prompt = self.config.get('prompt_template', '').format(
            channel_description=channel_description,
            trends_json=json.dumps(trends, indent=2)
        )
        
        logger.info("Asking LLM to select the best trends...")
        try:
            gen_config = {'response_mime_type': 'application/json', 'response_schema': SelectedTrends}
            response = self.client.generate_content(
                model=self.model,
                contents=prompt,
                generation_config=gen_config,
                system_instruction=self.config.get('system_instruction')
            )
            
            response_json = json.loads(response.text)
            logger.info("Successfully received and parsed LLM response for trend selection.")
            return response_json

        except Exception as e:
            logger.error(f"An error occurred during LLM trend selection: {e}")
            raise # Reraise to trigger tenacity retry

