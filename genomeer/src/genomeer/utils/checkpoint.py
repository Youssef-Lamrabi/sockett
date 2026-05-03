"""
genomeer/src/genomeer/utils/checkpoint.py
==========================================
Système de checkpoints pour le pipeline Genomeer.

Sauvegarde l'état AgentState après chaque step "done" sur disque,
permettant la reprise (resume) après un crash ou interruption.

USAGE:
    from genomeer.utils.checkpoint import CheckpointManager

    # Dans _observer (après step done):
    cp = CheckpointManager(run_temp_dir, session_id)
    cp.save(state, current_idx)

    # Dans BioAgent.run() pour reprendre:
    cp = CheckpointManager(run_temp_dir, session_id)
    if cp.exists():
        state = cp.load()
        print(f"Resuming from step {state['current_idx']}")
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("genomeer.checkpoint")

# Champs de AgentState sérialisables en JSON
# (on exclut les messages LangChain qui ne sont pas JSON-sérialisables nativement)
_SERIALIZABLE_FIELDS = {
    "plan", "current_idx", "manifest", "pending_code",
    "last_prompt", "last_result", "run_temp_dir",
    "retry_counts", "batch_strategy", "run_started_at",
    "next_step", "diagnostic_mode", "session_id",
    "run_id",
}


class CheckpointManager:
    """
    Gère la persistance et la restauration de l'état du pipeline.

    Fichier: <run_temp_dir>/.genomeer_checkpoint_<session_id>.json
    """

    VERSION = "1.0"

    def __init__(self, run_temp_dir: str, session_id: str):
        self.run_temp_dir = Path(run_temp_dir)
        self.session_id = session_id
        self.checkpoint_path = self.run_temp_dir / f".genomeer_checkpoint_{session_id}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, state: Dict[str, Any], current_idx: int) -> bool:
        """
        Sauvegarde l'état sérialisable après un step done.

        Parameters
        ----------
        state       : AgentState courant
        current_idx : Index du step qui vient d'être complété

        Returns True si la sauvegarde a réussi.
        """
        try:
            serializable = self._serialize_state(state)
            serializable["_checkpoint_version"] = self.VERSION
            serializable["_saved_at"] = time.time()
            serializable["_completed_step_idx"] = current_idx

            self.run_temp_dir.mkdir(parents=True, exist_ok=True)

            # Écriture atomique (write + rename) pour éviter la corruption
            tmp_path = self.checkpoint_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2, default=str)
            tmp_path.rename(self.checkpoint_path)

            logger.info(
                f"[CHECKPOINT] Saved after step {current_idx} → {self.checkpoint_path}"
            )
            return True

        except Exception as e:
            logger.warning(f"[CHECKPOINT] Save failed: {e}")
            return False

    def load(self) -> Optional[Dict[str, Any]]:
        """
        Charge l'état depuis le fichier de checkpoint.

        Returns l'état sérialisé, ou None si le fichier n'existe pas.
        """
        if not self.checkpoint_path.exists():
            return None
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info(
                f"[CHECKPOINT] Loaded — step={data.get('_completed_step_idx')}, "
                f"saved={time.ctime(data.get('_saved_at', 0))}"
            )
            return data
        except Exception as e:
            logger.warning(f"[CHECKPOINT] Load failed: {e}")
            return None

    def exists(self) -> bool:
        """Vérifie si un checkpoint existe pour cette session."""
        return self.checkpoint_path.exists()

    def delete(self) -> None:
        """Supprimer le checkpoint après un run réussi."""
        try:
            if self.checkpoint_path.exists():
                self.checkpoint_path.unlink()
                logger.info(f"[CHECKPOINT] Deleted {self.checkpoint_path}")
        except Exception:
            pass

    def summary(self) -> Optional[Dict[str, Any]]:
        """Retourne un résumé du checkpoint sans charger tout l'état."""
        data = self.load()
        if not data:
            return None
        plan = data.get("plan", [])
        done_steps = [s for s in plan if s.get("status") == "done"]
        todo_steps = [s for s in plan if s.get("status") == "todo"]
        return {
            "session_id": self.session_id,
            "completed_step": data.get("_completed_step_idx"),
            "saved_at": data.get("_saved_at"),
            "last_prompt": (data.get("last_prompt") or "")[:80],
            "plan_total": len(plan),
            "plan_done": len(done_steps),
            "plan_remaining": len(todo_steps),
        }

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def find_checkpoints(run_dir: str) -> List[Dict[str, Any]]:
        """
        Liste tous les checkpoints disponibles dans run_dir.
        Utile pour afficher les runs reprenables à l'utilisateur.
        """
        results = []
        for cp_file in Path(run_dir).glob(".genomeer_checkpoint_*.json"):
            try:
                with open(cp_file) as f:
                    data = json.load(f)
                session_id = cp_file.stem.replace(".genomeer_checkpoint_", "")
                plan = data.get("plan", [])
                results.append({
                    "session_id": session_id,
                    "path": str(cp_file),
                    "completed_step": data.get("_completed_step_idx", "?"),
                    "saved_at": data.get("_saved_at"),
                    "last_prompt": (data.get("last_prompt") or "")[:80],
                    "plan_total": len(plan),
                    "plan_done": len([s for s in plan if s.get("status") == "done"]),
                })
            except Exception:
                pass
        return sorted(results, key=lambda x: x.get("saved_at") or 0, reverse=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """Extrait et sérialise les champs JSON-compatibles de l'état."""
        serializable = {}
        for field in _SERIALIZABLE_FIELDS:
            if field not in state:
                continue
            val = state[field]
            if field == "plan" and isinstance(val, list):
                # Plan: garder uniquement les champs primitifs de chaque step
                serializable[field] = [
                    {
                        k: v for k, v in step.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    }
                    for step in val
                ]
            elif isinstance(val, (str, int, float, bool, list, dict, type(None))):
                serializable[field] = val
        return serializable
