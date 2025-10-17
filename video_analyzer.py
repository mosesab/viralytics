# video_analyzer.py

import asyncio
import logging
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor

from TikTokApi import TikTokApi
from TikTokApi.exceptions import TikTokException
from tenacity import retry, stop_after_attempt, wait_exponential
from textblob import TextBlob
from nrclex import NRCLex

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CPU-Bound Analysis Function ---
# This function is designed to be run in a separate process to avoid blocking asyncio
def perform_text_analysis(comments: List[str]) -> Tuple[Dict[str, float], str]:
    """
    Performs sentiment and emotion analysis on a list of comments.
    This is a CPU-intensive task.
    """
    # 1. Sentiment Analysis with TextBlob
    polarity_sum = 0
    for comment in comments:
        polarity_sum += TextBlob(comment).sentiment.polarity
    
    avg_polarity = polarity_sum / len(comments) if comments else 0
    
    if avg_polarity > 0.1:
        sentiment_label = "Positive"
    elif avg_polarity < -0.1:
        sentiment_label = "Negative"
    else:
        sentiment_label = "Neutral"

    sentiment_results = {
        "compound": avg_polarity,
        "polarity": sentiment_label
    }

    # 2. Emotion Analysis with NRCLex
    full_text = " ".join(comments)
    emotion_scores = NRCLex(full_text).raw_emotion_scores
    
    # Filter out positive/negative as they are covered by sentiment
    dominant_emotion = "neutral"
    if emotion_scores:
        filtered_emotions = {k: v for k, v in emotion_scores.items() if k not in ['positive', 'negative']}
        if filtered_emotions:
            dominant_emotion = max(filtered_emotions, key=filtered_emotions.get)

    return sentiment_results, dominant_emotion

class VideoAnalyzer:
    """
    Analyzes TikTok videos by fetching comments, performing sentiment/emotion analysis,
    and calculating an engagement score to select the best ones.
    """
    def __init__(self, tiktok_api: TikTokApi, config: dict):
        self.tiktok_api = tiktok_api
        self.config = config.get('analysis_params', {})
        self.TOP_N_VIDEOS = config.get('top_n_videos', 20)
        self.MIN_SENTIMENT_SCORE = config.get('min_sentiment_score', 0.1)
        self.MAX_COMMENTS = config.get('max_comments_for_analysis', 50)
        # Create a process pool to run CPU-bound tasks
        self.process_pool = ProcessPoolExecutor()

    @retry(wait=wait_exponential(multiplier=1, max=10), stop=stop_after_attempt(3))
    async def get_comments_for_video(self, video_id: str) -> List[str]:
        """Fetches comments for a single TikTok video with retry logic."""
        comments_text = []
        logger.info(f"Fetching up to {self.MAX_COMMENTS} comments for video ID: {video_id}...")
        try:
            video = self.tiktok_api.video(id=video_id)
            async for comment in video.comments(count=self.MAX_COMMENTS):
                comments_text.append(comment.text)
            logger.info(f"Found {len(comments_text)} comments for video {video_id}.")
            return comments_text
        except TikTokException as e:
            logger.error(f"Could not fetch comments for video {video_id}: {e}")
            raise # Reraise for tenacity
        except Exception as e:
            logger.error(f"An unexpected error occurred fetching comments for {video_id}: {e}")
            raise # Reraise for tenacity

    def calculate_engagement_score(self, stats: dict) -> float:
        """Calculates an engagement score. (likes + comments) / views."""
        likes = stats.get('diggCount', 0)
        comments = stats.get('commentCount', 0)
        views = stats.get('playCount', 0)
        return (likes + comments) / views if views > 0 else 0.0

    async def analyze_single_video(self, video: dict) -> dict:
        """Analyzes one video: fetches comments, runs analysis, calculates scores."""
        comments = await self.get_comments_for_video(video['video_id'])
        
        # Offload the CPU-bound analysis to the process pool
        loop = asyncio.get_running_loop()
        sentiment_results, emotion = await loop.run_in_executor(
            self.process_pool, perform_text_analysis, comments
        )
        
        engagement_score = self.calculate_engagement_score(video['stats'])
        
        video['analysis'] = {
            "sentiment": sentiment_results,
            "emotion": emotion,
            "engagement_score": engagement_score
        }
        return video

    async def analyze_and_filter_videos_concurrently(self, videos_to_analyze: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        Orchestrates the analysis process for all videos in a project concurrently.
        Returns (top_videos, all_analyzed_videos).
        """
        if not videos_to_analyze:
            return [], []

        # Create a list of analysis tasks to run in parallel
        tasks = [self.analyze_single_video(video) for video in videos_to_analyze]
        analyzed_videos = await asyncio.gather(*tasks)
        
        # Filter out videos with low sentiment scores
        filtered_videos = [
            v for v in analyzed_videos 
            if v['analysis']['sentiment']['compound'] >= self.MIN_SENTIMENT_SCORE
        ]
        
        # Sort the remaining videos by engagement score, descending
        sorted_videos = sorted(
            filtered_videos,
            key=lambda v: v['analysis']['engagement_score'],
            reverse=True
        )
        
        top_videos = sorted_videos[:self.TOP_N_VIDEOS]
        logger.info(f"Filtered down to the top {len(top_videos)} videos from {len(analyzed_videos)} analyzed.")
        
        return top_videos, analyzed_videos
    


