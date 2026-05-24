import redis
from config.settings import settings

class RedisService:
    """
    Service class interacting with Redis.
    Provides methods to maintain a rolling 60-second price window using Redis Sorted Sets.
    """
    def __init__(self):
        # Establish synchronous Redis client
        self.client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.key = "nifty_ticks"

    def add_tick(self, price: float, timestamp: float) -> None:
        """
        Appends a tick to the sorted set.
        The member is stored in format "price:timestamp" and the score is the timestamp.
        """
        member = f"{price}:{timestamp}"
        self.client.zadd(self.key, {member: timestamp})

    def prune_old_ticks(self, current_timestamp: float) -> int:
        """
        Removes ticks that are older than 60 seconds from the window.
        Returns the number of pruned ticks.
        """
        min_score = 0
        max_score = current_timestamp - 60.0
        return self.client.zremrangebyscore(self.key, min_score, max_score)

    def get_oldest_tick(self) -> tuple[float, float] | None:
        """
        Gets the oldest tick in the sorted set.
        After pruning, the oldest tick represents the price approximately 60 seconds ago.
        Returns tuple of (price, timestamp) or None if empty.
        """
        result = self.client.zrange(self.key, 0, 0, withscores=True)
        if not result:
            return None
        
        member, score = result[0]
        try:
            price = float(member.split(":")[0])
            return price, float(score)
        except (ValueError, IndexError):
            return None

    def get_tick_count(self) -> int:
        """
        Returns the count of active ticks in the rolling window.
        """
        return self.client.zcard(self.key)

    def add_historical_tick(self, price: float, timestamp: float) -> None:
        """
        Appends a tick to the historical list, keeping only the last 1000 items.
        """
        history_key = "nifty_history"
        member = f"{price}:{timestamp}"
        self.client.lpush(history_key, member)
        self.client.ltrim(history_key, 0, 999)

    def get_historical_ticks(self) -> list[tuple[float, float]]:
        """
        Returns all historical ticks in chronological order.
        """
        history_key = "nifty_history"
        members = self.client.lrange(history_key, 0, -1)
        ticks = []
        for m in reversed(members):
            try:
                parts = m.split(":")
                ticks.append((float(parts[0]), float(parts[1])))
            except (ValueError, IndexError):
                continue
        return ticks

    def clear_ticks(self) -> None:
        """
        Cleans the sorted set and historical list for resetting the system.
        """
        self.client.delete(self.key)
        self.client.delete("nifty_history")
