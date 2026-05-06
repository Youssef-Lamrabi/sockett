"""
genomeer/src/genomeer/utils/security.py
=========================================
Vérifications de sécurité avant exécution de code généré par le LLM.

POURQUOI:
  Le code Bash et Python généré par le LLM tourne directement sur la machine hôte.
  Sans vérification, un prompt adversarial ou une hallucination peut générer
  'rm -rf /', 'shutil.rmtree("/")' ou un fork bomb.

USAGE:
    from genomeer.utils.security import check_bash_script, check_python_code

    # Dans run_bash_script (helper.py) :
    is_safe, reason = check_bash_script(script)
    if not is_safe:
        return f"Error: {reason}"

    # Dans run_python_code (helper.py) :
    is_safe, reason = check_python_code(code)
    if not is_safe:
        return f"Error: {reason}"
"""

from __future__ import annotations

import re
import logging
from typing import List, Tuple

logger = logging.getLogger("genomeer.security")

# ---------------------------------------------------------------------------
# Bash — patterns interdits
# ---------------------------------------------------------------------------
# Chaque entrée: (regex compilé, label humain, is_false_positive_check)
# Les chemins /tmp, /var/tmp, /run/user sont autorisés explicitement.

_BLOCKED_BASH: List[Tuple[re.Pattern, str]] = [
    # rm -rf et variantes (double espace, tabs, -fr, -Rf, etc.)
    (re.compile(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*[rf]\s+/(?!tmp|var/tmp|run/user|home/\w+/\.genomeer)", re.IGNORECASE),
     "rm -rf on root or sensitive path (not /tmp)"),

    # mkfs — formatage disque
    (re.compile(r"\bmkfs\b", re.IGNORECASE),
     "disk format command (mkfs)"),

    # dd écriture directe sur device
    (re.compile(r"\bdd\b.*\bof\s*=\s*/dev/", re.IGNORECASE | re.DOTALL),
     "raw disk write via dd"),

    # shutdown / reboot / poweroff / halt
    (re.compile(r"(?m)^\s*(shutdown|reboot|poweroff|halt)\b", re.IGNORECASE),
     "system shutdown/reboot command"),

    # Fork bomb
    (re.compile(r":\s*\(\s*\)\s*\{[^}]*\}", re.IGNORECASE),
     "fork bomb pattern"),

    # curl/wget pipe to bash (remote code execution)
    (re.compile(r"(curl|wget)\s+\S+\s*\|\s*(bash|sh|zsh|ksh)", re.IGNORECASE),
     "remote code execution via curl/wget pipe to shell"),

    # Écriture directe sur device
    (re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
     "direct write to block device"),

    # chmod 777 récursif sur la racine
    (re.compile(r"\bchmod\s+-R\s+777\s+/(?!tmp)", re.IGNORECASE),
     "chmod 777 recursive on root path"),

    # Écriture sur /etc/passwd ou /etc/shadow
    (re.compile(r"(>>?\s*/etc/(passwd|shadow|sudoers|crontab))", re.IGNORECASE),
     "write to critical system file"),

    # TÂCHE 2.2 — Blocage des expansions de variables dangereuses
    (re.compile(r'[A-Za-z_][A-Za-z0-9_]*\s*=\s*/[^;|\n]*[;\n].*rm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+\$', re.DOTALL),
     "dangerous variable assignment followed by rm -rf"),
    
    # eval $(...) ou eval `...`
    (re.compile(r'\beval\s+["\']?(\$\(|\`)', re.IGNORECASE),
     "dynamic code execution via eval and expansion"),

    # TÂCHE 2.3 — Blocage des redirections vers des fichiers système
    (re.compile(r'(>>?|\| tee -a?)\s+/(etc|boot|sys|proc|root|usr/local/bin)/', re.IGNORECASE),
     "redirection to sensitive system directory"),

    # TÂCHE 2.4 — Blocage des substitutions de commandes ($() ou ``)
    # Ferme le vecteur rm -rf $(echo /) ou rm -rf `echo /`
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+[^;|\n]*(\$\(|\`)", re.IGNORECASE),
     "command substitution inside rm -rf"),
    
    # Blocage générique des substitutions contenant des commandes destructrices
    (re.compile(r"(\$\(|\`)[^)\`]*\b(rm|chmod|chown|mkfs|dd|iptables|mv|cp)\b", re.IGNORECASE),
     "dangerous command inside command substitution"),

    # iptables flush (désactive le firewall)
    (re.compile(r"\biptables\s+-F\b", re.IGNORECASE),
     "iptables flush (firewall disable)"),
]


# ---------------------------------------------------------------------------
# Python — patterns interdits
# ---------------------------------------------------------------------------

_BLOCKED_PYTHON: List[Tuple[re.Pattern, str]] = [
    # shutil.rmtree sur chemin racine (pas /tmp)
    (re.compile(
        r"\bshutil\s*\.\s*rmtree\s*\(\s*['\"]?\s*/(?![tT]mp|[vV]ar/[tT]mp|[hH]ome/\w+/\.genomeer)",
        re.IGNORECASE,
    ), "shutil.rmtree on root or sensitive path"),

    # os.system avec rm -rf
    (re.compile(r"\bos\s*\.\s*system\s*\(.*\brm\s+-[a-zA-Z]*r[a-zA-Z]*f", re.IGNORECASE | re.DOTALL),
     "os.system with rm -rf"),

    # subprocess avec rm -rf sur racine
    (re.compile(r"\bsubprocess\b.*\brm\s+-[a-zA-Z]*r[a-zA-Z]*f\s+/(?!tmp)", re.IGNORECASE | re.DOTALL),
     "subprocess rm -rf on root path"),

    # eval() — exécution dynamique de code arbitraire
    (re.compile(r"\beval\s*\(", re.IGNORECASE),
     "eval() is forbidden (arbitrary code execution)"),

    # exec() — exécution de code dynamique (inclut exec("string"))
    (re.compile(r"\bexec\s*\(", re.IGNORECASE),
     "exec() is forbidden (arbitrary code execution)"),

    # import dynamique de os pour contournement
    (re.compile(r"__import__\s*\(\s*['\"]os['\"]", re.IGNORECASE),
     "__import__('os') dynamic import bypass"),

    # Écriture dans /etc
    (re.compile(r"open\s*\(\s*['\"]?\s*/etc/", re.IGNORECASE),
     "file write to /etc (system config)"),

    # os.remove / os.unlink dans une list comprehension (suppression de masse)
    (re.compile(r"\[\s*os\.(remove|unlink).*\bfor\b", re.IGNORECASE | re.DOTALL),
     "mass file deletion via os.remove/unlink in list comprehension"),

    # os.remove / os.unlink sur chemin absolu racine
    (re.compile(r"\bos\.(remove|unlink)\s*\(\s*['\"]?\s*/(?![tT]mp|[vV]ar/[tT]mp|[hH]ome/\w+/\.genomeer)", re.IGNORECASE),
     "os.remove/unlink on root or sensitive path"),
]


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _normalize_script(script: str) -> str:
    """
    Normalise un script bash pour faciliter la détection de patterns.
    1. Remplace les séquences de whitespace par un espace simple.
    2. Retire les backslashes de continuation de ligne.
    3. Gère les caractères Unicode 'lookalike' (homographes).
    """
    if not script:
        return ""
    # Retire les backslashes de continuation de ligne (\ followed by newline)
    script = script.replace("\\\n", " ")
    
    # Normalisation des espaces (tabs, unicode non-breaking spaces, etc.)
    # BUG-12: Ajout de caractères unicode 'lookalike' (U+00A0, U+200B, etc.)
    script = re.sub(r'[ \t\r\u00A0\u2000-\u200B\u202F\u205F\u3000]+', ' ', script)
    
    return script.lower().strip()


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def check_bash_script(script: str) -> Tuple[bool, str]:
    """
    Vérifie un script bash avant exécution.
    """
    if not script or not script.strip():
        return True, "ok"

    normalized = _normalize_script(script)

    # BUG-10: Détection et vérification des blocs base64
    # Si on voit 'base64 -d' ou 'base64 --decode', on tente de trouver le contenu encodé
    if "base64" in normalized and ("-d" in normalized or "decode" in normalized):
        import base64 as _b64
        # Extraction naïve des chaînes base64 probables (A-Za-z0-9+/=)
        for m in re.finditer(r'["\']([A-Za-z0-9+/=]{8,})["\']', script):
            try:
                decoded = _b64.b64decode(m.group(1)).decode("utf-8", errors="ignore")
                is_safe, reason = check_bash_script(decoded)
                if not is_safe:
                    return False, f"[SECURITY BLOCK] Dangerous content hidden in base64: {reason}"
            except Exception:
                continue

    for pattern, label in _BLOCKED_BASH:
        if pattern.search(normalized):
            reason = f"[SECURITY BLOCK] Dangerous bash pattern detected: {label}"
            logger.error(f"{reason}\nScript (first 300 chars): {script[:300]!r}")
            return False, reason

    return True, "ok"


def check_python_code(code: str) -> Tuple[bool, str]:
    """
    Vérifie du code Python avant exécution.
    """
    if not code or not code.strip():
        return True, "ok"

    for pattern, label in _BLOCKED_PYTHON:
        if pattern.search(code):
            reason = f"[SECURITY BLOCK] Dangerous Python pattern detected: {label}"
            logger.error(f"{reason}\nCode (first 300 chars): {code[:300]!r}")
            return False, reason

    # BUG-11: Hardened AST-based analysis
    import ast
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            # 1. Block direct calls to dangerous builtins
            if isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    # Check for builtins.eval, builtins.exec etc
                    if isinstance(func.value, ast.Name) and func.value.id in ("builtins", "__builtins__"):
                        func_name = func.attr
                    else:
                        func_name = func.attr
                
                if func_name in ("eval", "exec", "compile", "__import__", "getattr", "setattr", "hasattr", "delattr", "breakpoint"):
                    reason = f"[SECURITY BLOCK] Forbidden AST call detected: {func_name}()"
                    return False, reason

            # 2. Block access to __builtins__ dictionary or dangerous attributes
            if isinstance(node, ast.Attribute):
                if node.attr in ("__builtins__", "__globals__", "__subclasses__", "__bases__", "func_globals"):
                    reason = f"[SECURITY BLOCK] Forbidden attribute access: .{node.attr}"
                    return False, reason
            
            # 3. Block access via strings (e.g. globals()['eval'])
            if isinstance(node, ast.Subscript):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                    if node.value.func.id in ("globals", "locals", "vars"):
                        reason = f"[SECURITY BLOCK] Forbidden dynamic access via {node.value.func.id}()"
                        return False, reason

    except SyntaxError:
        pass

    return True, "ok"


def check_cli_command(command: str) -> Tuple[bool, str]:
    """
    Vérifie une commande CLI single-line avant exécution.
    Délègue à check_bash_script car même ensemble de risques.
    """
    return check_bash_script(command)
