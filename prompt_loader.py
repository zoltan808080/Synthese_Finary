#!/usr/bin/env python3
"""Module d'assemblage et d'externalisation des prompts IA (SSOT)."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_project_rules() -> str:
  """Charge le socle commun des règles métier immuables."""
  rules_file = PROMPTS_DIR / "project_rules.md"
  if not rules_file.exists():
    raise FileNotFoundError(
        f"❌ Fichier de règles introuvable : {rules_file}"
    )
  return rules_file.read_text(encoding="utf-8")


def get_system_prompt(template_name: str) -> str:
  """Assemble les règles métier globales avec le template spécifique au rôle.

  Ex: template_name = 'risk_manager' ou 'advisor_speculative'
  """
  rules = load_project_rules()
  template_file = PROMPTS_DIR / f"{template_name}.md"

  if not template_file.exists():
    # Si pas de template spécifique, renvoie uniquement les règles de base
    return rules

  role_instructions = template_file.read_text(encoding="utf-8")
  return f"{rules}\n\n---\n\n# CONSIGNES SPÉCIFIQUES DU RÔLE\n{role_instructions}"
