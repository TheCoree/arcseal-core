import asyncio
import logging
from typing import Dict, List, Optional
import time

from app.services.game_manager import PlayerSession, game_manager

logger = logging.getLogger(__name__)


class MatchmakerQueue:
    def __init__(self):
        # List of PlayerSessions waiting
        self.queue: List[PlayerSession] = []
        self.wait_times: Dict[str, float] = {} # user_id -> start_time
        self.is_running = False
        # Keep a reference so the loop task isn't garbage-collected mid-run.
        self._task: Optional[asyncio.Task] = None

    def add_player(self, player: PlayerSession):
        if any(p.user_id == player.user_id for p in self.queue):
            return # Already in queue
        self.queue.append(player)
        self.wait_times[player.user_id] = time.time()

    def remove_player(self, user_id: str):
        self.queue = [p for p in self.queue if p.user_id != user_id]
        if user_id in self.wait_times:
            del self.wait_times[user_id]

    def get_allowed_elo_diff(self, wait_time: float) -> int:
        if wait_time < 5.0:
            return 50
        elif wait_time < 10.0:
            return 150
        elif wait_time < 15.0:
            return 300
        elif wait_time < 30.0:
            return 500
        else:
            return 2300

    async def _matchmaking_loop(self):
        while self.is_running:
            await asyncio.sleep(1.0)
            # One failed tick (e.g. DB unavailable while creating a room) must
            # not end matchmaking for the whole server — players stay queued
            # and the pair is retried on the next tick.
            try:
                await self._try_match()
            except Exception:
                logger.exception("Matchmaking tick failed")

    async def _try_match(self):
        if len(self.queue) < 2:
            return

        current_time = time.time()
        
        # Sort queue to prioritize players who have waited longest
        self.queue.sort(key=lambda p: self.wait_times.get(p.user_id, current_time))

        i = 0
        while i < len(self.queue) - 1:
            p1 = self.queue[i]
            p1_wait = current_time - self.wait_times.get(p1.user_id, current_time)
            p1_allowed_diff = self.get_allowed_elo_diff(p1_wait)

            matched_idx = -1
            for j in range(i + 1, len(self.queue)):
                p2 = self.queue[j]
                p2_wait = current_time - self.wait_times.get(p2.user_id, current_time)
                p2_allowed_diff = self.get_allowed_elo_diff(p2_wait)

                actual_diff = abs(p1.elo - p2.elo)
                
                # Match is valid if BOTH players' allowed differences accommodate the actual difference
                if actual_diff <= p1_allowed_diff and actual_diff <= p2_allowed_diff:
                    matched_idx = j
                    break

            if matched_idx != -1:
                p2 = self.queue[matched_idx]

                # Game creation needs DB access (to seed the draft). Each match
                # opens its own short-lived session — the websocket request
                # session belongs to the player sockets, not to this loop.
                from app.core.database import AsyncSessionLocal
                async with AsyncSessionLocal() as db:
                    await game_manager.create_game(p1, p2, db)

                # Remove from queue
                self.remove_player(p1.user_id)
                self.remove_player(p2.user_id)
                
                # We removed elements, don't increment i to process the new element at i
            else:
                i += 1

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._matchmaking_loop())

    def stop(self):
        self.is_running = False

matchmaker = MatchmakerQueue()
