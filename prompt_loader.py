#!/usr/bin/env python3
"""Module d'assemblage, d'externalisation et d'administration des prompts IA (SSOT v3.2)."""

import logging
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def load_project_rules() -> str:
    """Charge le socle commun des règles métier immuables."""
    rules_file = PROMPTS_DIR / "project_rules.md"
    if not rules_file.exists():
        logging.warning(f"⚠️ Fichier de règles introuvable : {rules_file}")
        return ""
    try:
        return rules_file.read_text(encoding="utf-8").strip()
    except Exception as e:
        logging.error(f"❌ Erreur lors de la lecture des règles du projet : {e}")
        return ""


def get_raw_prompt(prompt_name: str) -> str:
    """Lit le contenu brut d'un fichier de prompt Markdown spécifique sans assemblage."""
    clean_name = prompt_name.replace(".md", "").strip()
    file_path = PROMPTS_DIR / f"{clean_name}.md"
    if not file_path.exists():
        logging.warning(f"⚠️ Prompt SSOT introuvable : {file_path}")
        return ""
    try:
        return file_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logging.error(f"❌ Erreur lors de la lecture du prompt brut {clean_name} : {e}")
        return ""


def get_system_prompt(template_name: str) -> str:
    """Assemble les règles métier globales avec le template spécifique au rôle.

    Ex: template_name = 'risk_manager', 'auto_tpsl', 'advisor_speculative'
    """
    rules = load_project_rules()
    clean_name = template_name.replace(".md", "").strip()

    if clean_name == "project_rules":
        return rules

    template_file = PROMPTS_DIR / f"{clean_name}.md"
    if not template_file.exists():
        return rules

    try:
        role_instructions = template_file.read_text(encoding="utf-8").strip()
        if not rules:
            return role_instructions
        return f"{rules}\n\n---\n\n# CONSIGNES SPÉCIFIQUES DU RÔLE\n{role_instructions}"
    except Exception as e:
        logging.error(f"❌ Erreur d'assemblage du prompt system pour {clean_name} : {e}")
        return rules


def save_system_prompt(prompt_name: str, new_content: str) -> bool:
    """Sauvegarde le prompt modifié avec création automatique d'un fichier de secours (.bak)."""
    try:
        PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        clean_name = prompt_name.replace(".md", "").strip()
        file_path = PROMPTS_DIR / f"{clean_name}.md"

        # Backup de sécurité automatique si le fichier existe déjà
        if file_path.exists():
            backup_path = PROMPTS_DIR / f"{clean_name}.md.bak"
            backup_path.write_text(file_path.read_text(encoding="utf-8"), encoding="utf-8")
            logging.info(f"💾 Backup créé : {backup_path.name}")

        file_path.write_text(new_content.strip(), encoding="utf-8")
        logging.info(f"✅ Prompt SSOT {clean_name}.md mis à jour avec succès.")
        return True
    except Exception as e:
        logging.error(f"❌ Erreur lors de la sauvegarde du prompt {prompt_name} : {e}")
        return False


def restore_backup_prompt(prompt_name: str) -> bool:
    """Restaure le prompt à partir de sa dernière sauvegarde .bak si disponible."""
    try:
        clean_name = prompt_name.replace(".md", "").strip()
        file_path = PROMPTS_DIR / f"{clean_name}.md"
        backup_path = PROMPTS_DIR / f"{clean_name}.md.bak"

        if not backup_path.exists():
            logging.warning(f"⚠️ Aucune sauvegarde .bak trouvée pour {clean_name}")
            return False

        file_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        logging.info(f"🔄 Prompt {clean_name}.md restauré depuis son backup .bak.")
        return True
    except Exception as e:
        logging.error(f"❌ Erreur lors de la restauration du prompt {prompt_name} : {e}")
        return False


def list_available_prompts() -> list[str]:
    """Retourne la liste des noms des fichiers de prompts disponibles (sans extension ni fichier .bak)."""
    default_prompts = ["advisor_speculative", "auto_tpsl", "project_rules", "risk_manager"]
    if PROMPTS_DIR.exists():
        found = [
            p.stem for p in PROMPTS_DIR.glob("*.md")
            if not p.name.endswith(".bak")
        ]
        return sorted(list(set(default_prompts + found)))
    return sorted(default_prompts)