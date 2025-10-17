# database.py

import aiosqlite
import logging
import json
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

DB_PATH = "video_projects.db"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@asynccontextmanager
async def get_db():
    """Async context manager for aiosqlite connection."""
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        await conn.commit()
    except Exception as e:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def initialize_database():
    """Creates all necessary tables if they don't exist."""
    async with get_db() as db:        
        # Projects table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_paused BOOLEAN DEFAULT FALSE,
                channel_description TEXT
            )
        """)

        # Trends chosen for a project
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                justification TEXT,
                suggested_video_title TEXT,
                long_term_potential BOOLEAN,
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        """)

        # Main table to track videos through the pipeline
        await db.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                trend_keyword TEXT,
                
                -- TikTok Data --
                video_id TEXT NOT NULL UNIQUE,
                author_username TEXT,
                create_time INTEGER,
                description TEXT,
                video_url TEXT,
                cover_url TEXT,
                stats TEXT, -- Stored as JSON string
                
                -- Analysis Data --
                sentiment_compound_score REAL,
                sentiment_polarity TEXT,
                emotion TEXT,
                engagement_score REAL,
                is_top_pick BOOLEAN DEFAULT FALSE,
                
                -- Content Generation Data --
                generated_script TEXT,
                local_file_path TEXT,

                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        """)
        
        await db.commit()
        logger.info("Database initialized successfully.")

# --- Project Management ---
async def create_project(name: str, description: str) -> int:
    """Creates a new project and returns its ID."""
    async with get_db() as db:
        cursor = await db.execute(
            "INSERT INTO projects (name, channel_description) VALUES (?, ?)",
            (name, description)
        )
        await db.commit()
        logger.info(f"Created new project '{name}' with ID {cursor.lastrowid}")
        return cursor.lastrowid

async def get_project(project_id: int) -> Optional[Dict]:
    """Retrieves a single project from the database."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_all_projects() -> List[Dict]:
    """Retrieves all projects from the database."""
    async with get_db() as db:
        cursor = await db.execute("SELECT id, name, created_at, channel_description FROM projects ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

async def toggle_project_pause(project_id: int, is_paused: bool) -> bool:
    """Sets the pause state for a project."""
    async with get_db() as db:
        await db.execute("UPDATE projects SET is_paused = ? WHERE id = ?", (is_paused, project_id))
        await db.commit()
        logger.info(f"Project {project_id} pause state set to {is_paused}")
        return True

async def is_project_paused(project_id: int) -> bool:
    """Checks if a project is currently paused."""
    async with get_db() as db:
        cursor = await db.execute("SELECT is_paused FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        return row['is_paused'] if row else True # Default to paused if project not found


# --- Data Persistence for Each Step ---

async def save_trends_to_db(project_id: int, trends: List[Dict]):
    """Saves the selected trends from the LLM to the database."""
    async with get_db() as db:
        for trend in trends:
            await db.execute("""
                INSERT INTO trends (project_id, keyword, justification, suggested_video_title, long_term_potential)
                VALUES (?, ?, ?, ?, ?)
            """, (
                project_id, trend['keyword'], trend['justification'], 
                trend['suggested_video_title'], trend['long_term_potential']
            ))
        await db.commit()
        logger.info(f"Saved {len(trends)} trends to the database for project {project_id}.")

async def save_fetched_videos_to_db(project_id: int, keyword: str, videos: List[Dict]):
    """Saves fetched video data into the main videos table."""
    async with get_db() as db:
        count = 0
        for video in videos:
            # Using INSERT OR IGNORE to prevent duplicates based on the UNIQUE video_id
            cursor = await db.execute("""
                INSERT OR IGNORE INTO videos (
                    project_id, trend_keyword, video_id, author_username, create_time,
                    description, video_url, cover_url, stats
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project_id, keyword, video['video_id'], video['author_username'],
                video['create_time'], video['description'], video['video_url'],
                video['cover_url'], json.dumps(video['stats'])
            ))
            if cursor.rowcount > 0:
                count += 1
        await db.commit()
        logger.info(f"Saved {count} new videos to the database for keyword '{keyword}'.")

async def get_videos_for_analysis(project_id: int) -> List[Dict]:
    """Gets videos that have been fetched but not yet analyzed."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM videos WHERE project_id = ? AND sentiment_compound_score IS NULL",
            (project_id,)
        )
        rows = await cursor.fetchall()
        # Deserialize JSON stats field
        results = []
        for row in rows:
            row_dict = dict(row)
            row_dict['stats'] = json.loads(row_dict.get('stats', '{}'))
            results.append(row_dict)
        return results

async def update_videos_with_analysis(project_id: int, analyzed_videos: List[Dict], top_video_ids: List[str]):
    """Updates videos with analysis scores and marks the top picks."""
    async with get_db() as db:
        for video in analyzed_videos:
            is_top = video['video_id'] in top_video_ids
            analysis_data = video['analysis']
            sentiment_data = analysis_data['sentiment']
            
            await db.execute("""
                UPDATE videos SET
                    sentiment_compound_score = ?,
                    sentiment_polarity = ?,
                    emotion = ?,
                    engagement_score = ?,
                    is_top_pick = ?
                WHERE id = ?
            """, (
                sentiment_data['compound'],
                sentiment_data['polarity'],
                analysis_data['emotion'],
                analysis_data['engagement_score'],
                is_top,
                video['id']
            ))
        await db.commit()
        logger.info(f"Updated {len(analyzed_videos)} videos with analysis data. Marked {len(top_video_ids)} as top picks.")


async def get_top_videos_for_commentary_generation(project_id: int) -> List[Dict]:
    """Gets videos marked as top picks that don't have a script yet."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM videos WHERE project_id = ? AND is_top_pick = TRUE AND generated_script IS NULL",
            (project_id,)
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            row_dict = dict(row)
            row_dict['stats'] = json.loads(row_dict.get('stats', '{}'))
            results.append(row_dict)
        return results

async def update_video_with_generated_commentary(video_db_id: int, script: Optional[str], file_path: Optional[str]):
    """Updates a video record with its generated script and local file path."""
    async with get_db() as db:
        await db.execute(
            "UPDATE videos SET generated_script = ?, local_file_path = ? WHERE id = ?",
            (script, file_path, video_db_id)
        )
        await db.commit()

# --- UI Data Retrieval ---
async def get_project_summary(project_id: int) -> Dict[str, Any]:
    """Retrieves a full summary of a project's state for the UI."""
    summary = {
        "trends": [],
        "fetched_videos": [],
        "top_videos": []
    }
    async with get_db() as db:
        # Get trends
        trends_cursor = await db.execute("SELECT * FROM trends WHERE project_id = ?", (project_id,))
        summary['trends'] = [dict(row) for row in await trends_cursor.fetchall()]
        
        # Get all videos for the project
        videos_cursor = await db.execute("SELECT * FROM videos WHERE project_id = ?", (project_id,))
        all_videos = [dict(row) for row in await videos_cursor.fetchall()]

        for video in all_videos:
            if video.get('stats'): # Deserialize stats
                video['stats'] = json.loads(video['stats'])

            if video['is_top_pick']:
                summary['top_videos'].append(video)
            else:
                 summary['fetched_videos'].append(video)

    return summary



