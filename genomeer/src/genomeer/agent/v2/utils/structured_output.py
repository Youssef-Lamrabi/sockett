"""
genomeer/src/genomeer/agent/v2/utils/structured_output.py
===========================================================
Remplacement du parsing regex fragilié sur les outputs LLM.

PROBLÈME ORIGINAL:
  Le nœud Observer (et Generator) parsait les balises XML produites par le LLM
  avec des regex simples (StateGraphHelper). Si le LLM dévie du format exact
  (<EXECUTE>, <STATUS:done>, <OK/>, etc.), le parsing échoue silencieusement
  et l'agent reste bloqué ou passe à l'étape suivante avec un état incorrect.

SOLUTION:
  1. ParsedLLMOutput — modèle Pydantic qui représente la réponse structurée
     attendue d'un nœud (code, statut, observations, next_step).
  2. RobustLLMParser — tente d'abord le parsing XML strict existant,
     puis applique 3 stratégies de fallback successives avant de déclarer
     un échec. Ne retourne JAMAIS None — retourne toujours un ParsedLLMOutput.
  3. LLMOutputValidator — valide la cohérence biologique du code généré
     (environnement correct, outils dans le bon env, pas de commandes destructives).

USAGE (dans BioAgent.py, nœud generator):
    from genomeer.agent.v2.utils.structured_output import RobustLLMParser

    parser = RobustLLMParser()
    parsed = parser.parse_generator_output(llm_response_text, current_step)
    
    if parsed.parse_failed:
        # demander une régénération au LLM avec le repair prompt
        ...
    else:
        code = parsed.code
        lang = parsed.lang   # "PY" | "R" | "BASH" | "CLI"
        env  = parsed.env    # "bio-agent-env1" | "meta-env1" | None

USAGE (dans BioAgent.py, nœud observer):
    parsed = parser.parse_observer_output(llm_response_text, current_step)
    status = parsed.status   # "done" | "blocked" | "retry" | "unknown"
    reason = parsed.reason   # message d'explication
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    DONE    = "done"
    BLOCKED = "blocked"
    RETRY   = "retry"
    UNKNOWN = "unknown"


class CodeLang(str, Enum):
    PY   = "PY"
    R    = "R"
    BASH = "BASH"
    CLI  = "CLI"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ParsedExecuteBlock(BaseModel):
    """Représente un bloc <EXECUTE> extrait d'un output LLM."""
    raw: str                          = Field(default="", description="Texte brut du bloc")
    lang: Optional[CodeLang]          = Field(default=None, description="Langage détecté")
    code: str                         = Field(default="", description="Corps du code sans le shebang")
    env: Optional[str]                = Field(default=None, description="Environnement micromamba cible")
    parse_failed: bool                = Field(default=False)
    failure_reason: Optional[str]     = Field(default=None)

    @field_validator("env")
    @classmethod
    def validate_env(cls, v: Optional[str]) -> Optional[str]:
        VALID_ENVS = {"bio-agent-env1", "meta-env1", "btools_env_py310"}
        if v and v not in VALID_ENVS:
            return None   # env inconnu → laisser le résolveur décider
        return v


class ParsedObserverOutput(BaseModel):
    """Représente l'évaluation faite par le nœud Observer."""
    status: StepStatus                = Field(default=StepStatus.UNKNOWN)
    reason: str                       = Field(default="")
    next_node: str                    = Field(default="orchestrator")
    quality_signals: Dict[str, Any]   = Field(default_factory=dict)
    raw_text: str                     = Field(default="")
    parse_failed: bool                = Field(default=False)


class ParsedLLMOutput(BaseModel):
    """Output générique d'un nœud LLM, combine execute + observer."""
    execute: Optional[ParsedExecuteBlock]  = None
    observer: Optional[ParsedObserverOutput] = None
    free_text: str                          = Field(default="")
    parse_failed: bool                      = Field(default=False)
    failure_reason: Optional[str]           = None


# ---------------------------------------------------------------------------
# Stratégies de parsing (par ordre de priorité)
# ---------------------------------------------------------------------------

# Stratégie 1 : parsing XML strict (balises uppercase EXECUTE)
_RX_EXECUTE_STRICT = re.compile(
    r"<EXECUTE(?P<attrs>[^>]*)>(?P<body>.*?)</EXECUTE>",
    re.DOTALL | re.IGNORECASE,
)
_RX_LANG = re.compile(r"^\s*#!(PY|R|BASH|CLI)\s*", re.IGNORECASE | re.MULTILINE)
_RX_ENV_ATTR = re.compile(r'\benv=["\']([^"\']+)["\']', re.IGNORECASE)

# Stratégie 2 : code fence Markdown (```python, ```bash, etc.)
_RX_CODE_FENCE = re.compile(
    r"```(?P<lang>python|bash|r|sh|shell)?\s*\n(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_LANG_MAP = {
    "python": CodeLang.PY,
    "bash": CodeLang.BASH,
    "sh": CodeLang.BASH,
    "shell": CodeLang.BASH,
    "r": CodeLang.R,
}

# Stratégie 3 : heuristique sur le texte libre (présence d'imports Python, etc.)
_PYTHON_HEURISTICS = [
    re.compile(r"^import\s+\w+", re.MULTILINE),
    re.compile(r"^from\s+\w+\s+import", re.MULTILINE),
    re.compile(r"run_\w+\("),
]
_BASH_HEURISTICS = [
    re.compile(r"^(fastp|kraken2|metaspades|megahit|minimap2|samtools|prokka|checkm2)", re.MULTILINE),
    re.compile(r"\bmicromamba\b"),
    re.compile(r"#!/bin/bash"),
]

# STATUS patterns (robustes)
_RX_STATUS_DONE    = re.compile(r"<STATUS\s*:\s*done\s*>|status[\"':\s]+done|✔|DONE", re.IGNORECASE)
_RX_STATUS_BLOCKED = re.compile(r"<STATUS\s*:\s*blocked\s*>|status[\"':\s]+blocked|BLOCKED|FAILED", re.IGNORECASE)
_RX_OK_STANDALONE  = re.compile(r"<OK\s*/\s*>|<ok/>|\bOK\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Résolveur d'environnement (extrait de BioAgent._META_ENV_SIGNALS)
# ---------------------------------------------------------------------------

_META_ENV_SIGNALS = {
    "fastp", "fastqc", "multiqc", "nanostat", "nanoplot",
    "metaspades.py", "spades.py", "megahit", "flye",
    "minimap2", "bowtie2", "bwa", "samtools", "bedtools",
    "kraken2", "bracken", "metaphlan", "gtdbtk",
    "metabat2", "das_tool", "checkm2", "virsorter2", "checkv", "deepvirfinder",
    "prokka", "prodigal", "diamond", "hmmsearch", "humann",
    "amrfinder", "rgi", "virsorter", "dvf.py",
    # wrappers Python
    "run_fastp", "run_kraken2", "run_metaspades", "run_megahit", "run_flye",
    "run_minimap2", "run_bowtie2", "run_metaphlan4", "run_gtdbtk",
    "run_metabat2", "run_das_tool", "run_checkm2", "run_prokka",
    "run_prodigal", "run_diamond", "run_hmmer", "run_humann3",
    "run_amrfinderplus", "run_rgi_card", "run_bracken",
    "run_virsorter2", "run_checkv", "run_deepvirfinder",
    "from genomeer.tools.function.metagenomics",
    "from genomeer.tools.function.viromics",
}


def _resolve_env_from_code(code: str) -> str:
    """Déduit l'environnement micromamba depuis le contenu du code."""
    code_lower = code.lower()
    for signal in _META_ENV_SIGNALS:
        if signal.lower() in code_lower:
            return "meta-env1"
    return "bio-agent-env1"


# ---------------------------------------------------------------------------
# Validateur biologique
# ---------------------------------------------------------------------------

class LLMOutputValidator:
    """
    Valide la cohérence biologique du code généré.
    Détecte les erreurs courantes avant exécution.
    """

    # Commandes destructives interdites
    _DESTRUCTIVE = re.compile(
        r"\brm\s+-rf\b|\bdd\s+if=\b|\bformat\b|\bshred\b|\bmkfs\b",
        re.IGNORECASE,
    )

    # Commandes réseau non autorisées (hors wrappers connus)
    _RAW_NETWORK = re.compile(
        r"\bcurl\s+.*https?://(?!eutils\.ncbi|rest\.kegg|rest\.uniprot|www\.ebi\.ac\.uk|card\.mcmaster)",
        re.IGNORECASE,
    )

    @classmethod
    def validate(cls, parsed: ParsedExecuteBlock) -> Tuple[bool, List[str]]:
        """
        Retourne (is_valid, list_of_warnings).
        is_valid=False uniquement pour des problèmes critiques (destructif).
        Les warnings sont transmis à l'Observer pour logguer.
        """
        warnings: List[str] = []
        code = parsed.code

        # 1. Commandes destructives → échec critique
        if cls._DESTRUCTIVE.search(code):
            return False, ["[SECURITY] Code contains potentially destructive commands (rm -rf, dd, etc.)"]

        # 2. Env mismatch — outil meta-env1 dans bio-agent-env1
        declared_env = parsed.env or _resolve_env_from_code(code)
        for signal in _META_ENV_SIGNALS:
            if signal in code and declared_env == "bio-agent-env1":
                warnings.append(
                    f"[ENV-MISMATCH] Tool '{signal}' requires meta-env1 but declared env is bio-agent-env1. "
                    "Auto-correcting to meta-env1."
                )
                parsed.env = "meta-env1"
                break

        # 3. BASH sans shebang ou micromamba prefix pour tools CLI
        if parsed.lang == CodeLang.BASH:
            for signal in _META_ENV_SIGNALS:
                if signal in code and "micromamba run" not in code and "conda run" not in code:
                    warnings.append(
                        f"[ENV-HINT] BASH code uses '{signal}' but no 'micromamba run -n meta-env1' prefix found. "
                        "Ensure the shell is in the correct env."
                    )
                    break

        return True, warnings


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class RobustLLMParser:
    """
    Parser robuste à 4 stratégies pour les outputs LLM de Genomeer.

    Ordre de tentative:
      1. XML strict   → <EXECUTE ...>...</EXECUTE>
      2. Code fence   → ```python ... ``` ou ```bash ... ```
      3. Heuristique  → détecter du code Python/Bash dans le texte libre
      4. JSON         → si le LLM a répondu en JSON malgré les instructions

    Ne lève jamais d'exception. Retourne toujours un ParsedExecuteBlock ou
    ParsedObserverOutput valide, avec parse_failed=True si tout échoue.
    """

    def __init__(self, strict_validation: bool = True):
        self.strict_validation = strict_validation
        self.validator = LLMOutputValidator()

    # ── Public API ────────────────────────────────────────────────────────────

    def parse_generator_output(
        self,
        text: str,
        step_title: str = "",
    ) -> ParsedExecuteBlock:
        """Parse le texte produit par le nœud Generator."""
        text = text or ""

        # Tentative 1 : XML strict
        result = self._try_xml_strict(text)
        if result:
            result = self._post_process(result, step_title)
            return result

        # Tentative 2 : Markdown code fence
        result = self._try_markdown_fence(text)
        if result:
            result = self._post_process(result, step_title)
            return result

        # Tentative 3 : Heuristique texte libre
        result = self._try_heuristic(text)
        if result:
            result = self._post_process(result, step_title)
            return result

        # Tentative 4 : JSON
        result = self._try_json(text)
        if result:
            result = self._post_process(result, step_title)
            return result

        # Échec total
        return ParsedExecuteBlock(
            raw=text,
            parse_failed=True,
            failure_reason=(
                f"Could not extract executable code from LLM output. "
                f"Step: '{step_title}'. First 200 chars: {text[:200]!r}"
            ),
        )

    def parse_observer_output(
        self,
        text: str,
        step_title: str = "",
    ) -> ParsedObserverOutput:
        """Parse le texte produit par le nœud Observer."""
        text = text or ""

        status = StepStatus.UNKNOWN
        reason = ""
        next_node = "orchestrator"

        # Détection du statut (multi-pattern robuste)
        if _RX_STATUS_DONE.search(text) or _RX_OK_STANDALONE.search(text):
            status = StepStatus.DONE
            next_node = "orchestrator"
        elif _RX_STATUS_BLOCKED.search(text):
            status = StepStatus.BLOCKED
            next_node = "diagnostics"

            # Essayer d'extraire la raison
            blocked_match = re.search(
                r"<STATUS\s*:\s*blocked\s*>\s*(.*?)(?:</STATUS>|$)",
                text, re.DOTALL | re.IGNORECASE
            )
            if blocked_match:
                reason = blocked_match.group(1).strip()[:500]

        # Extraction des métriques qualité depuis le texte (si quality_gate a été run)
        quality_signals = self._extract_quality_signals(text)

        # Si statut toujours UNKNOWN mais le texte parle de succès
        if status == StepStatus.UNKNOWN:
            success_patterns = [
                r"\bsuccess\b", r"\bcompleted\b", r"\bfinished\b",
                r"exit=0", r"exit code 0", r"done\b"
            ]
            for pat in success_patterns:
                if re.search(pat, text, re.IGNORECASE):
                    status = StepStatus.DONE
                    break

        if status == StepStatus.UNKNOWN:
            # TÂCHE 3: Fallback sécurisé vers BLOCKED au lieu de DONE
            status = StepStatus.BLOCKED
            reason = "[OBSERVER FALLBACK] No recognizable status tag found in LLM output. Defaulting to BLOCKED for safety."
            parse_failed = True
        else:
            parse_failed = False

        return ParsedObserverOutput(
            status=status,
            reason=reason,
            next_node=next_node,
            quality_signals=quality_signals,
            raw_text=text,
            parse_failed=parse_failed,
        )

    # ── Stratégies privées ────────────────────────────────────────────────────

    def _try_xml_strict(self, text: str) -> Optional[ParsedExecuteBlock]:
        """Stratégie 1 : XML strict <EXECUTE>...</EXECUTE>."""
        m = _RX_EXECUTE_STRICT.search(text)
        if not m:
            return None

        attrs = m.group("attrs") or ""
        body  = m.group("body") or ""

        # Extraire lang depuis le shebang (#!PY, #!BASH, etc.)
        lang = self._extract_lang(body)

        # Extraire l'env depuis l'attribut XML (env="meta-env1")
        env_match = _RX_ENV_ATTR.search(attrs)
        env = env_match.group(1) if env_match else None

        # Retirer le shebang du code
        code = re.sub(r"^\s*#!(PY|R|BASH|CLI)\s*\n?", "", body, count=1, flags=re.IGNORECASE).strip()

        return ParsedExecuteBlock(
            raw=m.group(0),
            lang=lang,
            code=code,
            env=env,
            parse_failed=False,
        )

    def _try_markdown_fence(self, text: str) -> Optional[ParsedExecuteBlock]:
        """Stratégie 2 : code fence Markdown."""
        m = _RX_CODE_FENCE.search(text)
        if not m:
            return None

        fence_lang_str = (m.group("lang") or "").lower()
        body = m.group("body") or ""

        lang = _FENCE_LANG_MAP.get(fence_lang_str, CodeLang.PY)

        # Si le corps contient un shebang, utiliser celui-ci
        detected_lang = self._extract_lang(body)
        if detected_lang:
            lang = detected_lang
            body = re.sub(r"^\s*#!(PY|R|BASH|CLI)\s*\n?", "", body, count=1, flags=re.IGNORECASE)

        return ParsedExecuteBlock(
            raw=m.group(0),
            lang=lang,
            code=body.strip(),
            parse_failed=False,
        )

    def _try_heuristic(self, text: str) -> Optional[ParsedExecuteBlock]:
        """Stratégie 3 : heuristique sur le texte libre."""
        # Chercher du code Python
        for pat in _PYTHON_HEURISTICS:
            if pat.search(text):
                # Extraire le bloc de code le plus probable
                code = self._extract_code_block_heuristic(text)
                if code:
                    return ParsedExecuteBlock(
                        raw=text,
                        lang=CodeLang.PY,
                        code=code,
                        parse_failed=False,
                    )

        # Chercher du Bash
        for pat in _BASH_HEURISTICS:
            if pat.search(text):
                code = self._extract_code_block_heuristic(text)
                if code:
                    return ParsedExecuteBlock(
                        raw=text,
                        lang=CodeLang.BASH,
                        code=code,
                        parse_failed=False,
                    )

        return None

    def _try_json(self, text: str) -> Optional[ParsedExecuteBlock]:
        """Stratégie 4 : JSON (LLM a répondu en JSON malgré les instructions)."""
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            return None
        try:
            data = json.loads(json_match.group(0))
            code = data.get("code") or data.get("script") or data.get("execute")
            if not code:
                return None
            lang_str = (data.get("language") or data.get("lang") or "PY").upper()
            lang = CodeLang(lang_str) if lang_str in CodeLang.__members__ else CodeLang.PY
            env = data.get("env") or data.get("environment")
            return ParsedExecuteBlock(
                raw=text,
                lang=lang,
                code=str(code).strip(),
                env=env,
                parse_failed=False,
            )
        except (json.JSONDecodeError, ValueError):
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_lang(self, body: str) -> Optional[CodeLang]:
        m = _RX_LANG.search(body)
        if not m:
            return None
        lang_str = m.group(1).upper()
        try:
            return CodeLang(lang_str)
        except ValueError:
            return None

    def _extract_code_block_heuristic(self, text: str) -> str:
        """Extrait le bloc de code le plus probable d'un texte libre."""
        lines = text.splitlines()
        code_lines = []
        in_code = False

        for line in lines:
            stripped = line.strip()
            # Détecter début de bloc de code
            if not in_code and (
                stripped.startswith("import ")
                or stripped.startswith("from ")
                or stripped.startswith("def ")
                or stripped.startswith("#!")
                or any(tool in stripped for tool in ["run_", "fastp", "kraken2", "samtools"])
            ):
                in_code = True

            if in_code:
                # Arrêter sur du texte narratif clair
                if re.match(r"^(Note|This|The|In|For|However|Therefore|Please)\b", stripped):
                    if len(code_lines) > 3:
                        break
                code_lines.append(line)

        return "\n".join(code_lines).strip()

    def _extract_quality_signals(self, text: str) -> Dict[str, Any]:
        """Extrait les métriques qualité mentionnées dans le texte de l'Observer."""
        signals: Dict[str, Any] = {}

        patterns = {
            "n50_bp": re.compile(r"N50[:\s=]+([0-9,]+)\s*(?:bp|kb)?", re.IGNORECASE),
            "classified_pct": re.compile(r"(?:classified\s+([0-9.]+)\s*%|([0-9.]+)\s*%\s*(?:reads?\s+)?classified)", re.IGNORECASE),
            "q30_rate": re.compile(r"Q30[:\s=]+([0-9.]+)\s*%?", re.IGNORECASE),
            "n_bins": re.compile(r"([0-9]+)\s+bins?\s+(?:produced|generated|found)", re.IGNORECASE),
            "mean_completeness": re.compile(r"completeness[:\s=]+([0-9.]+)\s*%?", re.IGNORECASE),
            "mean_contamination": re.compile(r"contamination[:\s=]+([0-9.]+)\s*%?", re.IGNORECASE),
        }

        for key, rx in patterns.items():
            m = rx.search(text)
            if m:
                try:
                    groups = m.groups()
                    raw = next((g for g in groups if g is not None), None) if groups else m.group(1)
                    if raw:
                        signals[key] = float(raw.replace(",", ""))
                except (ValueError, StopIteration):
                    pass

        return signals

    def _post_process(
        self,
        parsed: ParsedExecuteBlock,
        step_title: str,
    ) -> ParsedExecuteBlock:
        """Post-traitement commun : résolution d'env + validation."""
        # 1. Résoudre l'env si non déclaré
        if not parsed.env and parsed.code:
            parsed.env = _resolve_env_from_code(parsed.code)

        # 2. Résoudre le lang si non détecté
        if not parsed.lang:
            if parsed.env == "meta-env1" and parsed.code:
                parsed.lang = CodeLang.BASH
            else:
                parsed.lang = CodeLang.PY

        # 3. Validation biologique (optionnelle)
        if self.strict_validation and parsed.code:
            is_valid, warnings = self.validator.validate(parsed)
            if not is_valid:
                parsed.parse_failed = True
                parsed.failure_reason = "; ".join(warnings)
            elif warnings:
                # Ajouter les warnings comme commentaire en tête du code
                warn_block = "\n".join(f"# WARNING: {w}" for w in warnings)
                parsed.code = warn_block + "\n" + parsed.code

        return parsed


# ---------------------------------------------------------------------------
# Patch drop-in pour StateGraphHelper (rétrocompatibilité)
# ---------------------------------------------------------------------------

def patch_state_graph_helper(helper_class):
    """
    Monkey-patch StateGraphHelper pour utiliser RobustLLMParser
    à la place du regex natif dans parse_execute().

    Appel unique au démarrage de BioAgent:
        from genomeer.agent.v2.utils.structured_output import patch_state_graph_helper
        from genomeer.agent.v2.utils.state_graph import StateGraphHelper
        patch_state_graph_helper(StateGraphHelper)
    """
    _parser = RobustLLMParser()

    original_parse = helper_class.parse_execute.__func__ if hasattr(
        helper_class.parse_execute, "__func__"
    ) else helper_class.parse_execute

    @staticmethod
    def robust_parse_execute(text: str):
        """Version robuste de parse_execute — délègue à RobustLLMParser."""
        parsed = _parser.parse_generator_output(text)
        if parsed.parse_failed:
            return None, None, None
        lang = parsed.lang.value if parsed.lang else "PY"
        return parsed.code, lang, parsed.env

    helper_class.parse_execute = robust_parse_execute
    return helper_class