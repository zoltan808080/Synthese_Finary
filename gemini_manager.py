#!/usr/bin/env python3
"""Gestionnaire centralisé et résilient des appels API Gemini (SSOT Engine v3.5.0 - Support Model Override)."""

import concurrent.futures
import json
import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from google import genai

load_dotenv()

CONFIG_FILE = Path(__file__).parent / "config" / "gemini_config.json"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def load_gemini_config() -> dict:
    """Charge la configuration dynamique des modèles, timeouts et retries."""
    default_config = {
        "models_priority": [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ],
        "timeout_seconds": 45,
        "max_retries_per_model": 2,
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if "models" in data and "models_priority" not in data:
                data["models_priority"] = data["models"]
            return {**default_config, **data}
        except Exception as e:
            logging.warning(f"⚠️ Erreur de lecture de {CONFIG_FILE}, utilisation du fallback : {e}")
    else:
        save_gemini_config(default_config)
    return default_config


def save_gemini_config(config_data: dict) -> bool:
    """Sauvegarde la configuration modifiée depuis le Dashboard Streamlit."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(config_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        logging.error(f"❌ Erreur lors de la sauvegarde de {CONFIG_FILE} : {e}")
        return False


def call_gemini_with_fallback(
    prompt_text: str,
    system_instruction: str = None,
    json_mode: bool = False,
    custom_timeout: int = None,
    model_override: str | list[str] | None = None,
) -> tuple[str, str]:
    """Exécute un appel Gemini avec bascule automatique sur les modèles de secours ET retries par modèle.

    Args:
        prompt_text (str): Texte du prompt utilisateur.
        system_instruction (str, optional): Prompt système / rôle.
        json_mode (bool, optional): Force une sortie JSON valide.
        custom_timeout (int, optional): Timeout spécifique en secondes.
        model_override (str | list[str], optional): Modèle unique ou liste prioritaire dédiée
            passée dynamiquement (ex: ["gemini-3.5-flash-lite"] pour la sentinelle 15 min).

    Returns:
        tuple[str, str]: (contenu_reponse, nom_du_modele_utilise)
    """
    config = load_gemini_config()

    # Gestion de l'override local prioritaire sur la config globale
    if model_override:
        if isinstance(model_override, str):
            models = [model_override]
        else:
            models = list(model_override)
    else:
        models = config.get("models_priority") or config.get("models", ["gemini-3.8-flash"])

    timeout_val = custom_timeout or config.get("timeout_seconds", 45)
    max_retries = config.get("max_retries_per_model", 2)

    client = genai.Client(api_key=GEMINI_API_KEY)

    full_content = (
        f"{system_instruction}\n\n---\n\nDemande utilisateur :\n{prompt_text}"
        if system_instruction
        else prompt_text
    )

    gen_config = {}
    if json_mode:
        gen_config["response_mime_type"] = "application/json"

    last_error = None

    for model_name in models:
        for attempt in range(1, max_retries + 1):
            try:
                logging.info(f"🔄 Tentative d'appel Gemini [{model_name}] (Essai {attempt}/{max_retries})...")

                def _execute_call():
                    res = client.models.generate_content(
                        model=model_name,
                        contents=full_content,
                        config=gen_config if gen_config else None,
                    )
                    return res.text if res else None

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_execute_call)
                    response_text = future.result(timeout=timeout_val)

                if response_text:
                    logging.info(f"✅ Réponse obtenue avec succès via [{model_name}]")
                    return response_text, model_name

            except concurrent.futures.TimeoutError:
                last_error = f"Timeout ({timeout_val}s) dépassé"
                logging.warning(f"⏱️ {last_error} sur [{model_name}] (Essai {attempt}/{max_retries}).")
                if attempt < max_retries:
                    time.sleep(1.0)

            except Exception as e:
                last_error = str(e)
                logging.warning(f"⚠️ Erreur sur [{model_name}] (Essai {attempt}/{max_retries}) : {last_error}")

                # Tentative de retry sur erreurs temporaires (429, 503, Quota/Concurrency)
                if (
                    "429" in last_error
                    or "503" in last_error
                    or "UNAVAILABLE" in last_error
                    or "RESOURCE_EXHAUSTED" in last_error
                ) and attempt < max_retries:
                    time.sleep(1.5)
                    continue
                else:
                    break

        logging.warning(f"❌ Échec de toutes les tentatives sur [{model_name}], passage au modèle suivant...")

    raise RuntimeError(f"❌ Tous les modèles Gemini ont échoué. Dernière erreur : {last_error}")


if __name__ == "__main__":
    cfg = load_gemini_config()
    print("✅ Gemini Manager v3.5.0 initialisé avec succès :", cfg)