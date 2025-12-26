"""
utils/cache.py - Cache LRU avec TTL pour optimisation RAM

Patterns implémentés:
- LRU avec expiration temporelle
- Thread-safe avec locks
- Décorateur de cache pour fonctions

Optimisé pour Raspberry Pi 5 (4GB RAM)
"""
import time
import threading
from functools import wraps
from typing import Callable, Optional, Any, Dict
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Cache LRU avec Time-To-Live

    Combine les avantages de LRU (limite mémoire) et TTL (fraîcheur données)
    Adapté pour Raspberry Pi avec 4GB RAM

    Utilisation:
        cache = TTLCache(maxsize=100, ttl=300)
        cache.set("key", "value")
        value = cache.get("key")
    """

    def __init__(self, maxsize: int = 100, ttl: int = 300):
        """
        Args:
            maxsize: Nombre maximum d'entrées
            ttl: Time-to-live en secondes
        """
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._timestamps: Dict[Any, float] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[Any]:
        """
        Récupère une valeur du cache

        Args:
            key: Clé à récupérer

        Returns:
            Valeur ou None si non trouvée/expirée
        """
        with self._lock:
            if key not in self._cache:
                return None

            # Vérifier expiration
            if time.time() - self._timestamps[key] > self.ttl:
                del self._cache[key]
                del self._timestamps[key]
                return None

            # Déplacer en fin (most recently used)
            self._cache.move_to_end(key)
            return self._cache[key]

    def set(self, key: Any, value: Any):
        """
        Stocke une valeur dans le cache

        Args:
            key: Clé
            value: Valeur à stocker
        """
        with self._lock:
            # Éviction si plein
            while len(self._cache) >= self.maxsize:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                del self._timestamps[oldest_key]

            self._cache[key] = value
            self._timestamps[key] = time.time()
            self._cache.move_to_end(key)

    def delete(self, key: Any) -> bool:
        """
        Supprime une entrée du cache

        Args:
            key: Clé à supprimer

        Returns:
            True si supprimée, False si non trouvée
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                del self._timestamps[key]
                return True
            return False

    def clear(self):
        """Vide le cache"""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()

    def cleanup_expired(self) -> int:
        """
        Nettoie les entrées expirées

        Returns:
            Nombre d'entrées supprimées
        """
        with self._lock:
            now = time.time()
            expired = [
                k for k, t in self._timestamps.items()
                if now - t > self.ttl
            ]
            for key in expired:
                del self._cache[key]
                del self._timestamps[key]
            return len(expired)

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    @property
    def stats(self) -> dict:
        """Statistiques du cache"""
        return {
            "size": len(self._cache),
            "maxsize": self.maxsize,
            "ttl": self.ttl
        }


def ttl_lru_cache(maxsize: int = 128, ttl: int = 300):
    """
    Décorateur combinant lru_cache et TTL

    Args:
        maxsize: Nombre maximum d'entrées
        ttl: Time-to-live en secondes

    Utilisation:
        @ttl_lru_cache(maxsize=50, ttl=300)
        def get_stock_data(symbol: str):
            ...
    """
    def decorator(func: Callable):
        cache = TTLCache(maxsize=maxsize, ttl=ttl)

        @wraps(func)
        def wrapper(*args, **kwargs):
            # Créer clé hashable
            try:
                key = (args, tuple(sorted(kwargs.items())))
            except TypeError:
                # Si args non hashable, exécuter sans cache
                return func(*args, **kwargs)

            # Chercher en cache
            result = cache.get(key)
            if result is not None:
                logger.debug(f"Cache hit for {func.__name__}")
                return result

            # Exécuter et mettre en cache
            result = func(*args, **kwargs)
            if result is not None:  # Ne pas cacher None
                cache.set(key, result)

            return result

        # Exposer méthodes utilitaires
        wrapper.cache_clear = cache.clear
        wrapper.cache_cleanup = cache.cleanup_expired
        wrapper.cache_info = lambda: cache.stats

        return wrapper
    return decorator


class CacheManager:
    """
    Gestionnaire centralisé des caches

    Utilisation:
        manager = CacheManager()
        market_cache = manager.register("market", maxsize=50, ttl=300)
    """

    def __init__(self):
        self.caches: Dict[str, TTLCache] = {}

    def register(self, name: str, maxsize: int, ttl: int) -> TTLCache:
        """
        Enregistre un nouveau cache

        Args:
            name: Nom du cache
            maxsize: Taille maximum
            ttl: Time-to-live

        Returns:
            Instance TTLCache
        """
        cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self.caches[name] = cache
        logger.debug(f"Registered cache '{name}' (maxsize={maxsize}, ttl={ttl})")
        return cache

    def get(self, name: str) -> Optional[TTLCache]:
        """Récupère un cache par son nom"""
        return self.caches.get(name)

    def cleanup_all(self) -> Dict[str, int]:
        """
        Nettoie tous les caches

        Returns:
            Dict avec nombre d'entrées supprimées par cache
        """
        results = {}
        for name, cache in self.caches.items():
            results[name] = cache.cleanup_expired()
        return results

    def clear_all(self):
        """Vide tous les caches"""
        for cache in self.caches.values():
            cache.clear()
        logger.info("All caches cleared")

    def get_stats(self) -> Dict[str, dict]:
        """
        Statistiques de tous les caches

        Returns:
            Dict avec stats par cache
        """
        return {
            name: cache.stats
            for name, cache in self.caches.items()
        }


# Instance globale du gestionnaire de cache
cache_manager = CacheManager()
