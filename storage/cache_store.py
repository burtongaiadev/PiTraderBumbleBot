"""
storage/cache_store.py - Cache JSON persistant

Stockage fichiers JSON pour cache persistant.
Léger et sans dépendance (pas de SQLite).

Caractéristiques:
- Sauvegarde/lecture fichiers JSON
- Nettoyage automatique fichiers expirés
- Thread-safe avec locks
"""
import json
import os
import time
import threading
import logging
from typing import Optional, Any, Dict
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config

logger = logging.getLogger(__name__)


class CacheStore:
    """
    Cache JSON persistant

    Stocke les données en fichiers JSON individuels.
    Adapté pour Raspberry Pi (pas de base de données lourde).
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or config.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _get_path(self, key: str) -> Path:
        """Génère le chemin du fichier cache"""
        # Sanitize key pour nom de fichier valide
        safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)
        return self.cache_dir / f"{safe_key}.json"

    def get(self, key: str, default: Any = None, ttl: Optional[int] = None) -> Any:
        """
        Récupère une valeur du cache

        Args:
            key: Clé de cache
            default: Valeur par défaut si non trouvée
            ttl: TTL en secondes (vérifie l'expiration)

        Returns:
            Valeur ou default
        """
        path = self._get_path(key)

        with self._lock:
            if not path.exists():
                return default

            try:
                with open(path, "r") as f:
                    data = json.load(f)

                # Vérifier expiration si TTL spécifié
                if ttl is not None:
                    cached_at = data.get("_cached_at", 0)
                    if time.time() - cached_at > ttl:
                        # Expiré, supprimer et retourner default
                        path.unlink()
                        return default

                return data.get("value", default)

            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to read cache {key}: {e}")
                return default

    def set(self, key: str, value: Any) -> bool:
        """
        Stocke une valeur dans le cache

        Args:
            key: Clé de cache
            value: Valeur à stocker (doit être JSON-serializable)

        Returns:
            True si succès
        """
        path = self._get_path(key)

        with self._lock:
            try:
                data = {
                    "value": value,
                    "_cached_at": time.time(),
                    "_key": key
                }

                with open(path, "w") as f:
                    json.dump(data, f, indent=2, default=str)

                return True

            except (TypeError, IOError) as e:
                logger.error(f"Failed to write cache {key}: {e}")
                return False

    def delete(self, key: str) -> bool:
        """
        Supprime une entrée du cache

        Args:
            key: Clé à supprimer

        Returns:
            True si supprimée
        """
        path = self._get_path(key)

        with self._lock:
            if path.exists():
                path.unlink()
                return True
            return False

    def exists(self, key: str) -> bool:
        """Vérifie si une clé existe"""
        return self._get_path(key).exists()

    def clear(self) -> int:
        """
        Vide tout le cache

        Returns:
            Nombre de fichiers supprimés
        """
        count = 0
        with self._lock:
            for path in self.cache_dir.glob("*.json"):
                try:
                    path.unlink()
                    count += 1
                except IOError:
                    pass
        return count

    def cleanup_expired(self, ttl: int) -> int:
        """
        Nettoie les entrées expirées

        Args:
            ttl: TTL en secondes

        Returns:
            Nombre de fichiers supprimés
        """
        count = 0
        now = time.time()

        with self._lock:
            for path in self.cache_dir.glob("*.json"):
                try:
                    with open(path, "r") as f:
                        data = json.load(f)

                    cached_at = data.get("_cached_at", 0)
                    if now - cached_at > ttl:
                        path.unlink()
                        count += 1

                except (json.JSONDecodeError, IOError):
                    # Fichier corrompu, supprimer
                    try:
                        path.unlink()
                        count += 1
                    except IOError:
                        pass

        logger.info(f"Cleaned up {count} expired cache files")
        return count

    def list_keys(self) -> list:
        """Liste toutes les clés en cache"""
        keys = []
        for path in self.cache_dir.glob("*.json"):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                    keys.append(data.get("_key", path.stem))
            except (json.JSONDecodeError, IOError):
                pass
        return keys

    def get_stats(self) -> Dict[str, Any]:
        """Statistiques du cache"""
        files = list(self.cache_dir.glob("*.json"))
        total_size = sum(f.stat().st_size for f in files)

        return {
            "entries": len(files),
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024),
            "cache_dir": str(self.cache_dir)
        }


# Instance singleton
cache_store = CacheStore()
