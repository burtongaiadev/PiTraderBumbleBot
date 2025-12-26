"""
utils/memory.py - Gestion mémoire pour Raspberry Pi 4GB

Patterns implémentés:
- Monitoring mémoire proactif
- Garbage collection stratégique
- Context managers pour scope mémoire
- Générateurs pour traitement streaming

Optimisé pour Raspberry Pi 5 (4GB RAM)
"""
import gc
import sys
import logging
from typing import Generator, Iterable, TypeVar, Callable
from contextlib import contextmanager
import functools

logger = logging.getLogger(__name__)

T = TypeVar('T')


class MemoryMonitor:
    """
    Moniteur mémoire pour Raspberry Pi
    Déclenche GC et alertes si mémoire basse
    """

    # Seuils en bytes (pour 4GB system)
    WARNING_THRESHOLD = 3.0 * 1024**3   # 3GB utilisés = warning
    CRITICAL_THRESHOLD = 3.5 * 1024**3  # 3.5GB = critical

    @staticmethod
    def get_memory_usage() -> dict:
        """
        Retourne utilisation mémoire actuelle

        Returns:
            dict avec total, available, used, percent
        """
        try:
            import psutil
            mem = psutil.virtual_memory()
            return {
                "total": mem.total,
                "available": mem.available,
                "used": mem.used,
                "percent": mem.percent
            }
        except ImportError:
            # Fallback sans psutil - lecture /proc/meminfo
            try:
                with open('/proc/meminfo', 'r') as f:
                    lines = f.readlines()
                    meminfo = {}
                    for line in lines:
                        parts = line.split(':')
                        if len(parts) == 2:
                            key = parts[0].strip()
                            value = int(parts[1].strip().split()[0]) * 1024  # kB -> bytes
                            meminfo[key] = value

                    total = meminfo.get('MemTotal', 0)
                    available = meminfo.get('MemAvailable', 0)
                    used = total - available

                    return {
                        "total": total,
                        "available": available,
                        "used": used,
                        "percent": (used / total * 100) if total > 0 else 0
                    }
            except (FileNotFoundError, ValueError):
                return {"percent": 0, "available": float('inf'), "used": 0, "total": 0}

    @classmethod
    def check_and_cleanup(cls) -> bool:
        """
        Vérifie mémoire et nettoie si nécessaire

        Returns:
            True si nettoyage effectué
        """
        mem = cls.get_memory_usage()

        if mem["used"] > cls.CRITICAL_THRESHOLD:
            logger.warning(f"Memory critical ({mem['percent']:.1f}%), forcing full GC...")
            gc.collect(generation=2)  # Full GC
            gc.collect()  # Double collect pour générations
            return True
        elif mem["used"] > cls.WARNING_THRESHOLD:
            logger.info(f"Memory elevated ({mem['percent']:.1f}%), running GC...")
            gc.collect(generation=1)
            return True

        return False

    @classmethod
    def log_stats(cls):
        """Log statistiques mémoire et GC"""
        mem = cls.get_memory_usage()
        gc_stats = gc.get_stats()

        available_mb = mem['available'] / (1024**2)
        logger.info(
            f"Memory: {mem['percent']:.1f}% used, "
            f"{available_mb:.0f}MB available"
        )
        logger.debug(f"GC stats: {gc_stats}")

    @classmethod
    def is_memory_low(cls) -> bool:
        """Vérifie si la mémoire est basse"""
        mem = cls.get_memory_usage()
        return mem["used"] > cls.WARNING_THRESHOLD


def memory_efficient(func: Callable) -> Callable:
    """
    Décorateur qui force GC après exécution de fonctions lourdes

    Utilisation:
        @memory_efficient
        def process_large_data():
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            # Nettoyer après fonctions lourdes
            gc.collect()

    return wrapper


@contextmanager
def memory_scope():
    """
    Context manager pour scope mémoire contrôlé

    Utilisation:
        with memory_scope():
            # Opérations lourdes
            data = load_large_data()
            process(data)
        # GC automatique à la sortie
    """
    try:
        yield
    finally:
        gc.collect()


def chunked_generator(iterable: Iterable[T], chunk_size: int) -> Generator:
    """
    Générateur qui traite par chunks pour économiser RAM

    Args:
        iterable: Itérable à traiter
        chunk_size: Taille des chunks

    Yields:
        Chunks de l'itérable

    Utilisation:
        for chunk in chunked_generator(large_list, 100):
            process_chunk(chunk)
    """
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
            gc.collect()  # GC entre chunks

    if chunk:  # Dernier chunk
        yield chunk


def lazy_property(func: Callable):
    """
    Property qui calcule la valeur une seule fois (lazy loading)
    Évite calculs inutiles et économise mémoire

    Utilisation:
        class MyClass:
            @lazy_property
            def expensive_data(self):
                return compute_expensive()
    """
    attr_name = f"_lazy_{func.__name__}"

    @property
    @functools.wraps(func)
    def wrapper(self):
        if not hasattr(self, attr_name):
            setattr(self, attr_name, func(self))
        return getattr(self, attr_name)

    return wrapper


def sizeof_fmt(num: float) -> str:
    """
    Format taille mémoire lisible

    Args:
        num: Taille en bytes

    Returns:
        String formaté (ex: "1.5GB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}TB"


def get_object_size(obj) -> int:
    """
    Calcule taille mémoire d'un objet récursif

    Args:
        obj: Objet à mesurer

    Returns:
        Taille en bytes
    """
    seen = set()

    def sizeof(o):
        if id(o) in seen:
            return 0
        seen.add(id(o))
        size = sys.getsizeof(o)

        if isinstance(o, dict):
            size += sum(sizeof(k) + sizeof(v) for k, v in o.items())
        elif isinstance(o, (list, tuple, set, frozenset)):
            size += sum(sizeof(i) for i in o)

        return size

    return sizeof(obj)
