"""
utils/decorators.py - Décorateurs pour gestion erreurs et performance

Patterns implémentés:
- Retry avec exponential backoff
- Circuit Breaker pour éviter cascade d'échecs
- Rate Limiter pour protection API
- Thermal aware pour Raspberry Pi

Optimisé pour Raspberry Pi 5 (4GB RAM)
"""
import time
import functools
import logging
from typing import Callable, Tuple, Type, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def retry_with_backoff(
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    on_retry: Optional[Callable] = None
):
    """
    Décorateur retry avec exponential backoff

    Args:
        exceptions: Tuple des exceptions à intercepter
        max_retries: Nombre maximum de tentatives
        initial_delay: Délai initial en secondes
        backoff_factor: Multiplicateur du délai
        max_delay: Délai maximum
        on_retry: Callback optionnel appelé à chaque retry

    Utilisation:
        @retry_with_backoff(
            exceptions=(ConnectionError, TimeoutError),
            max_retries=3,
            initial_delay=2.0
        )
        def fetch_data():
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"[Retry {attempt + 1}/{max_retries}] "
                            f"{func.__name__} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        if on_retry:
                            on_retry(attempt, e)
                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} retries: {e}"
                        )
            raise last_exception

        return wrapper
    return decorator


@dataclass
class CircuitBreakerState:
    """État du circuit breaker"""
    failures: int = 0
    last_failure: Optional[datetime] = None
    state: str = "closed"  # closed, open, half-open


class CircuitBreaker:
    """
    Circuit Breaker Pattern

    Évite de surcharger un service défaillant.
    États: closed -> open (après N échecs) -> half-open (après timeout) -> closed

    Utilisation:
        cb = CircuitBreaker(failure_threshold=5)

        @cb
        def call_external_api():
            ...
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        exceptions: Tuple[Type[Exception], ...] = (Exception,)
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.exceptions = exceptions
        self._state = CircuitBreakerState()

    @property
    def is_open(self) -> bool:
        return self._state.state == "open"

    def __call__(self, func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Vérifier état du circuit
            if self._state.state == "open":
                if self._should_attempt_reset():
                    self._state.state = "half-open"
                    logger.info(f"Circuit breaker half-open, testing {func.__name__}")
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker open for {func.__name__}"
                    )

            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
            except self.exceptions as e:
                self._on_failure()
                raise

        return wrapper

    def _should_attempt_reset(self) -> bool:
        if self._state.last_failure is None:
            return True
        elapsed = datetime.now() - self._state.last_failure
        return elapsed > timedelta(seconds=self.recovery_timeout)

    def _on_success(self):
        if self._state.state == "half-open":
            logger.info("Circuit breaker closed after successful test")
        self._state.failures = 0
        self._state.state = "closed"

    def _on_failure(self):
        self._state.failures += 1
        self._state.last_failure = datetime.now()
        if self._state.failures >= self.failure_threshold:
            self._state.state = "open"
            logger.warning(
                f"Circuit breaker opened after {self._state.failures} failures"
            )

    def reset(self):
        """Reset manuel du circuit breaker"""
        self._state = CircuitBreakerState()


class CircuitBreakerOpenError(Exception):
    """Exception levée quand le circuit breaker est ouvert"""
    pass


def rate_limiter(calls_per_minute: int = 30):
    """
    Rate limiter simple basé sur le temps

    Important pour:
    - Éviter rate limiting APIs (429 errors)
    - Réduire charge CPU sur Raspberry Pi

    Utilisation:
        @rate_limiter(calls_per_minute=10)
        def call_api():
            ...
    """
    min_interval = 60.0 / calls_per_minute

    def decorator(func: Callable):
        last_call = [0.0]  # Mutable pour closure

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_call[0]
            if elapsed < min_interval:
                sleep_time = min_interval - elapsed
                time.sleep(sleep_time)

            result = func(*args, **kwargs)
            last_call[0] = time.time()
            return result

        return wrapper
    return decorator


def get_cpu_temperature() -> float:
    """
    Lit la température CPU du Raspberry Pi

    Returns:
        Température en Celsius, ou 0.0 si non disponible
    """
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = float(f.read().strip()) / 1000.0
            return temp
    except (FileNotFoundError, ValueError, PermissionError):
        return 0.0  # Non-RPi ou erreur


def thermal_aware(warning_temp: float = 70.0, critical_temp: float = 80.0, cooldown: float = 5.0):
    """
    Décorateur qui vérifie la température CPU avant exécution
    Spécifique Raspberry Pi

    Args:
        warning_temp: Température de warning (pause courte)
        critical_temp: Température critique (pause longue)
        cooldown: Durée de pause en secondes

    Utilisation:
        @thermal_aware(warning_temp=70.0)
        def heavy_computation():
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            temp = get_cpu_temperature()

            if temp > critical_temp:
                logger.warning(f"CPU temp critical ({temp:.1f}°C), waiting {cooldown*2:.1f}s...")
                time.sleep(cooldown * 2)
            elif temp > warning_temp:
                logger.info(f"CPU temp elevated ({temp:.1f}°C), short pause...")
                time.sleep(cooldown)

            return func(*args, **kwargs)

        return wrapper
    return decorator
