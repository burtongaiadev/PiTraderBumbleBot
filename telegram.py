"""
telegram.py - Alertes Telegram pour PiTrader

Fonctionnalités:
- Envoi de signaux (texte structuré)
- Commande /review pour lister signaux à noter
- Boutons inline pour notation différée
- Commande /stats pour statistiques

Pas de graphiques (simplifié pour Pi)
"""
import requests
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from config import config
from storage.signals_store import signals_store, SignalRecord
from data.twelve_data import twelve_data_client
from utils.decorators import retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass
class TelegramMessage:
    """Message Telegram"""
    text: str
    parse_mode: str = "HTML"
    reply_markup: Optional[Dict] = None


class TelegramBot:
    """
    Bot Telegram pour PiTrader

    Gère:
    - Envoi de notifications
    - Commandes interactives
    - Boutons inline pour notation
    """

    def __init__(self):
        self.token = config.telegram.bot_token
        self.chat_id = config.telegram.chat_id
        self.enabled = config.telegram.enabled and bool(self.token)
        self.base_url = f"https://api.telegram.org/bot{self.token}"

        if not self.enabled:
            logger.warning("Telegram not configured or disabled")

    @retry_with_backoff(
        exceptions=(requests.RequestException,),
        max_retries=3,
        initial_delay=2.0
    )
    def _send_request(self, method: str, data: Dict[str, Any]) -> Dict:
        """
        Envoie une requête à l'API Telegram

        Args:
            method: Méthode API (sendMessage, etc.)
            data: Données à envoyer

        Returns:
            Réponse JSON
        """
        url = f"{self.base_url}/{method}"

        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()

        result = response.json()
        if not result.get("ok"):
            raise ValueError(f"Telegram error: {result.get('description')}")

        return result

    def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict] = None
    ) -> bool:
        """
        Envoie un message texte

        Args:
            text: Texte du message (HTML supporté)
            parse_mode: Mode de parsing (HTML, Markdown)
            reply_markup: Boutons inline (optionnel)

        Returns:
            True si succès
        """
        if not self.enabled:
            logger.info(f"[Telegram disabled] Would send: {text[:100]}...")
            return False

        try:
            data = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode
            }

            if reply_markup:
                data["reply_markup"] = reply_markup

            self._send_request("sendMessage", data)
            return True

        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def send_signal_alert(self, signal: SignalRecord) -> bool:
        """
        Envoie une alerte de signal

        Format structuré avec scores détaillés

        Args:
            signal: SignalRecord à envoyer

        Returns:
            True si succès
        """
        # Construire le message
        scores = signal.scores
        text = f"""🚨 <b>SIGNAL ACHAT: {signal.symbol}</b>

📊 <b>Score: {signal.total_score:.1f}/10</b>
├─ Macro:       {scores.get('macro', 0):+.0f} ({signal.macro_summary})
├─ Marché:      {scores.get('market', 0):+.0f} ({signal.market_summary})
├─ Fondamental: {scores.get('fundamental', 0):+.1f} ({signal.fundamental_summary})
└─ Sentiment:   {scores.get('sentiment', 0):+.1f} ({signal.sentiment_summary})

💰 Prix: ${signal.price_at_signal:.2f}

<i>ID: {signal.id[:8]}</i>
<i>Utilisez /review plus tard pour noter ce signal</i>"""

        return self.send_message(text)

    def send_review_list(self, signals: List[SignalRecord]) -> bool:
        """
        Envoie la liste des signaux à noter

        Avec prix actuel vs prix signal

        Args:
            signals: Liste de SignalRecord non notés

        Returns:
            True si succès
        """
        if not signals:
            return self.send_message("✅ Aucun signal à noter!")

        text = f"📋 <b>SIGNAUX À NOTER ({len(signals)})</b>\n\n"

        for i, s in enumerate(signals, 1):
            # Récupérer prix actuel
            current_price = None
            return_pct = None

            try:
                quote = twelve_data_client.get_quote(s.symbol)
                if quote.is_valid and quote.price and s.price_at_signal:
                    current_price = quote.price
                    return_pct = (current_price - s.price_at_signal) / s.price_at_signal * 100
            except Exception:
                pass

            # Formater date
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(s.timestamp)
                date_str = dt.strftime("%d/%m")
            except ValueError:
                date_str = "?"

            # Ligne du signal
            if current_price and return_pct is not None:
                sign = "+" if return_pct >= 0 else ""
                text += f"{i}. <b>{s.symbol}</b> ({date_str}) - ${s.price_at_signal:.2f} → ${current_price:.2f} ({sign}{return_pct:.1f}%)\n"
            else:
                text += f"{i}. <b>{s.symbol}</b> ({date_str}) - ${s.price_at_signal:.2f}\n"

        text += "\n<i>Envoyez le numéro pour noter (ex: 1)</i>"

        return self.send_message(text)

    def send_rating_prompt(self, signal: SignalRecord, current_price: Optional[float] = None) -> bool:
        """
        Envoie le prompt de notation avec boutons

        Args:
            signal: Signal à noter
            current_price: Prix actuel (optionnel)

        Returns:
            True si succès
        """
        # Calculer return
        return_str = ""
        if current_price and signal.price_at_signal:
            return_pct = (current_price - signal.price_at_signal) / signal.price_at_signal * 100
            sign = "+" if return_pct >= 0 else ""
            return_str = f" ({sign}{return_pct:.1f}%)"

        text = f"""📝 <b>Noter: {signal.symbol}</b>

Prix signal: ${signal.price_at_signal:.2f}
Prix actuel: ${current_price:.2f if current_price else '?'}{return_str}

Score initial: {signal.total_score:.1f}/10

Choisissez une note:"""

        # Boutons inline pour notation
        keyboard = {
            "inline_keyboard": [[
                {"text": "⭐1", "callback_data": f"rate_{signal.id}_1"},
                {"text": "⭐2", "callback_data": f"rate_{signal.id}_2"},
                {"text": "⭐3", "callback_data": f"rate_{signal.id}_3"},
                {"text": "⭐4", "callback_data": f"rate_{signal.id}_4"},
                {"text": "⭐5", "callback_data": f"rate_{signal.id}_5"},
            ]]
        }

        return self.send_message(text, reply_markup=keyboard)

    def send_stats(self) -> bool:
        """
        Envoie les statistiques des signaux

        Returns:
            True si succès
        """
        stats = signals_store.get_statistics()

        if stats.get("count", 0) == 0:
            return self.send_message("📊 Aucun signal enregistré.")

        text = f"""📊 <b>STATISTIQUES PITRADER</b>

<b>Signaux</b>
├─ Total: {stats.get('total_count', 0)}
├─ Notés: {stats.get('rated_count', 0)}
└─ À noter: {stats.get('unrated_count', 0)}
"""

        if stats.get('avg_rating'):
            text += f"\n<b>Notes</b>\n├─ Moyenne: {stats['avg_rating']:.1f}/5\n"
            dist = stats.get('rating_distribution', {})
            for i in range(5, 0, -1):
                count = dist.get(i, 0)
                text += f"├─ {'⭐' * i}: {count}\n"

        if stats.get('avg_return') is not None:
            text += f"""
<b>Performance</b>
├─ Return moyen: {stats['avg_return']:+.2f}%
├─ Positifs: {stats.get('positive_returns', 0)}
└─ Négatifs: {stats.get('negative_returns', 0)}"""

        return self.send_message(text)

    def send_error_alert(self, error: str) -> bool:
        """Envoie une alerte d'erreur"""
        text = f"⚠️ <b>PiTrader Error</b>\n\n<code>{error[:500]}</code>"
        return self.send_message(text)

    def send_startup_notification(self, watchlist_count: int, ollama_available: bool) -> bool:
        """
        Envoie une notification de démarrage après reboot

        Args:
            watchlist_count: Nombre d'actions surveillées
            ollama_available: Si Ollama est disponible

        Returns:
            True si succès
        """
        from datetime import datetime
        import platform
        import os

        # Récupérer uptime système
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                uptime_min = int(uptime_seconds // 60)
                uptime_str = f"{uptime_min} min" if uptime_min < 60 else f"{uptime_min // 60}h {uptime_min % 60}min"
        except (FileNotFoundError, IOError):
            uptime_str = "N/A"

        # Récupérer température CPU (Raspberry Pi)
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                cpu_temp = int(f.read()) / 1000
                temp_str = f"{cpu_temp:.1f}°C"
        except (FileNotFoundError, IOError):
            temp_str = "N/A"

        ollama_emoji = "✅" if ollama_available else "⚠️"
        ollama_status = "Actif" if ollama_available else "Fallback"

        text = f"""🚀 <b>PiTrader Démarré</b>
{datetime.now().strftime('%d/%m/%Y %H:%M')}

<b>Système</b>
├─ Host: {platform.node()}
├─ Uptime: {uptime_str}
└─ CPU Temp: {temp_str}

<b>Configuration</b>
├─ Actions: {watchlist_count}
└─ {ollama_emoji} Ollama: {ollama_status}

<i>Première analyse en cours...</i>"""

        return self.send_message(text)

    def send_daily_summary(
        self,
        macro_score: int,
        market_score: int,
        signals_count: int,
        top_picks: List[str]
    ) -> bool:
        """
        Envoie le résumé quotidien

        Args:
            macro_score: Score macro
            market_score: Score marché
            signals_count: Nombre de signaux générés
            top_picks: Liste des meilleurs picks

        Returns:
            True si succès
        """
        from datetime import datetime

        # Emoji selon scores
        macro_emoji = "🟢" if macro_score >= 0 else "🟡" if macro_score >= -1 else "🔴"
        market_emoji = "🟢" if market_score >= 0 else "🟡" if market_score >= -1 else "🔴"

        text = f"""📈 <b>RÉSUMÉ PITRADER</b>
{datetime.now().strftime('%d/%m/%Y %H:%M')}

<b>Contexte</b>
├─ {macro_emoji} Macro: {macro_score:+d}
└─ {market_emoji} Marché: {market_score:+d}

<b>Signaux générés: {signals_count}</b>
"""

        if top_picks:
            text += "\n<b>Top picks:</b>\n"
            for pick in top_picks[:3]:
                text += f"• {pick}\n"

        return self.send_message(text)


# Instance singleton
telegram_bot = TelegramBot()
