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
import uuid
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
    "run_id", "messages",
    "env_name", "env_ready",
    # ISSUE-9: batch-mode fields missing — without them, checkpoint resume in batch
    # mode restarts from sample 0 instead of the failed sample.
    "batch_mode", "current_sample_idx", "current_sample_id",
    "per_sample_results", "sample_manifest",
}


class CheckpointManager:
    """
    Gère la persistance et la restauration de l'état du pipeline.

    Fichier: <run_temp_dir>/.genomeer_checkpoint_<session_id>.json
    """

    VERSION = "1.0"

    def __init__(self, run_temp_dir: str, session_id: str):
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_-]+$', session_id):
            raise ValueError(f"[CheckpointManager] Invalid session_id (must be alphanumeric/underscore/hyphen): {session_id!r}")
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

            # Use mkstemp for a guaranteed-unique temp path; clean up on failure (C-06)
            import tempfile as _tmpfile
            fd, tmp_path_str = _tmpfile.mkstemp(
                dir=str(self.run_temp_dir),
                prefix=f".ckpt_{self.session_id}_",
                suffix=".json",
            )
            tmp_path = Path(tmp_path_str)
            try:
                def _safe_default(obj):
                    # BUG-12 residual: log non-serializable objects instead of
                    # silently converting them to undeserializable repr strings.
                    logger.warning(
                        f"[CHECKPOINT] Non-JSON-serializable object in state "
                        f"({type(obj).__name__}); replacing with null."
                    )
                    return None

                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(serializable, f, indent=2, default=_safe_default)
                os.replace(tmp_path_str, self.checkpoint_path)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

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
            
            # BUG-15: Relocalisation des chemins si run_temp_dir a changé
            old_dir = data.get("run_temp_dir")
            new_dir = str(self.run_temp_dir)
            if old_dir and old_dir != new_dir:
                logger.info(f"[CHECKPOINT] Relocating paths from {old_dir} to {new_dir}")
                # Recursively update paths in data
                data = self._relocate_paths(data, old_dir, new_dir)
                data["run_temp_dir"] = new_dir

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
            # BUG-13: catch and log per-file errors so a corrupted checkpoint
            # does not silently swallow valid neighbours.
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
            except json.JSONDecodeError as exc:
                logger.warning(
                    f"[CHECKPOINT] Corrupted checkpoint file skipped: {cp_file} — {exc}"
                )
            except Exception as exc:
                logger.warning(
                    f"[CHECKPOINT] Could not read checkpoint {cp_file}: {exc}"
                )
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
            elif field == "messages" and isinstance(val, list):
                # BUG-12 residual: LangChain message content can be a list (multipart /
                # vision messages like [{"type":"text","text":"…"},{"type":"image_url",…}]).
                # str() on a list produces an undeserializable Python repr.
                # We use json.dumps/json.loads to round-trip list/dict content safely.
                serializable[field] = []
                for m in val:
                    if isinstance(m, dict):
                        safe_msg = {
                            k: v for k, v in m.items()
                            if k in ("role", "content", "type", "id", "name")
                            and isinstance(v, (str, int, float, bool, list, dict, type(None)))
                        }
                        serializable[field].append(safe_msg)
                    elif hasattr(m, "type") and hasattr(m, "content"):
                        raw_content = getattr(m, "content", "")
                        # Safely serialise list / dict content (multipart messages)
                        if isinstance(raw_content, (list, dict)):
                            try:
                                content_safe = json.loads(json.dumps(raw_content, default=str))
                            except Exception:
                                content_safe = str(raw_content)
                        else:
                            content_safe = str(raw_content)
                        serializable[field].append({
                            "type": str(getattr(m, "type", "unknown")),
                            "content": content_safe,
                        })
                    else:
                        # Last resort: wrap the repr so deserialization never gets a raw string
                        serializable[field].append({
                            "type": "unknown",
                            "content": str(m),
                        })
            elif isinstance(val, (str, int, float, bool, list, dict, type(None))):
                serializable[field] = val
        return serializable

    @classmethod
    def _relocate_paths(cls, obj: Any, old_base: str, new_base: str) -> Any:
        """Replace old_base with new_base in strings recursively (precise prefix match)."""
        if isinstance(obj, str):
            # Only replace if old_base is a true path prefix (followed by separator or exact match)
            if obj == old_base:
                return new_base
            sep = os.sep
            if obj.startswith(old_base + sep) or obj.startswith(old_base + '/'):
                return new_base + obj[len(old_base):]
            return obj
        elif isinstance(obj, list):
            return [cls._relocate_paths(i, old_base, new_base) for i in obj]
        elif isinstance(obj, dict):
            return {k: cls._relocate_paths(v, old_base, new_base) for k, v in obj.items()}
        return obj
