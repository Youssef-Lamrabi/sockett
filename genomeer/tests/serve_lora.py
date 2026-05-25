#!/usr/bin/env python3
"""
OpenAI-compatible API server for Apertus-8B-BioInstruct (LoRA fine-tuned).
Serves at http://127.0.0.1:8001/v1

Usage (in sft_env):
    /mnt/nfs/llmhub/sft_env/bin/python serve_lora.py
"""

import subprocess, sys

# Auto-install server deps if missing
for _pkg_import, _pkg_install in [("fastapi", "fastapi"), ("uvicorn", "uvicorn[standard]")]:
    try:
        __import__(_pkg_import)
    except ImportError:
        print(f"[SETUP] Installing {_pkg_install}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", _pkg_install, "-q"])

import json, time, uuid, asyncio
from typing import List, Dict, Any, Optional
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# ─────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────
BASE_MODEL_ID  = "swiss-ai/Apertus-8B-Instruct-2509"
ADAPTER_PATH   = "/mnt/nfs/llmhub/Genomeer/sft/output/Models/v2v4_r64/Apertus-8B-BioInstruct"
PORT           = 8001
MODEL_ALIAS    = "Apertus-8B-BioInstruct"

# Use default local HF cache (~/.cache/huggingface) — reads from local NVMe SSD,
# much faster than NFS. Model is already cached locally.

# ─────────────────────────────────────────────────────────────────
# Load model + LoRA adapter
# ─────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"  {MODEL_ALIAS}")
print("=" * 65)

print("  [1/3] Loading tokenizer from adapter directory...")
tokenizer = AutoTokenizer.from_pretrained(
    ADAPTER_PATH,
    trust_remote_code=True,
)

print(f"  [2/3] Loading base model: {BASE_MODEL_ID}")
base_model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL_ID,
    device_map="auto",
    torch_dtype=torch.bfloat16,
    trust_remote_code=True,
    low_cpu_mem_usage=True,
)

print(f"  [3/3] Merging LoRA adapter: {ADAPTER_PATH}")
model = PeftModel.from_pretrained(base_model, ADAPTER_PATH)
model.eval()

_device = next(model.parameters()).device
print(f"\n  Model loaded on {_device}  (dtype={next(model.parameters()).dtype})")
print("=" * 65)
print(f"  API ready at http://127.0.0.1:{PORT}/v1")
print("=" * 65)


# ─────────────────────────────────────────────────────────────────
# Inference helper
# ─────────────────────────────────────────────────────────────────
def _infer(
    messages: List[Dict],
    max_tokens: int,
    temperature: float,
    stop: List[str],
) -> str:
    """Apply Apertus chat template and generate a response."""
    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(_device)
    input_len = inputs["input_ids"].shape[1]

    gen_kwargs: Dict[str, Any] = dict(
        **inputs,
        max_new_tokens=min(max_tokens, 4096),
        pad_token_id=tokenizer.eos_token_id,
        eos_token_id=tokenizer.eos_token_id,
        repetition_penalty=1.05,
    )
    if temperature > 0.01:
        gen_kwargs["do_sample"] = True
        gen_kwargs["temperature"] = float(temperature)
        gen_kwargs["top_p"] = 0.9
    else:
        gen_kwargs["do_sample"] = False

    with torch.no_grad():
        output_ids = model.generate(**gen_kwargs)

    new_tokens = output_ids[0][input_len:]
    text = tokenizer.decode(new_tokens, skip_special_tokens=True)

    # Trim at stop sequences
    for s in (stop or []):
        if s in text:
            text = text[: text.index(s)]

    return text.strip()


# ─────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────
app = FastAPI(title=f"{MODEL_ALIAS} — OpenAI-compatible API", version="1.0.0")


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{"id": MODEL_ALIAS, "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body        = await request.json()
        messages    = body.get("messages", [])
        max_tokens  = int(body.get("max_tokens", 2048))
        temperature = float(body.get("temperature", 0.7))
        stop        = body.get("stop") or []
        if isinstance(stop, str):
            stop = [stop]

        if not messages:
            raise HTTPException(status_code=400, detail="messages cannot be empty")

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(
            None,
            lambda: _infer(messages, max_tokens, temperature, stop),
        )

        return JSONResponse({
            "id":      f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object":  "chat.completion",
            "created": int(time.time()),
            "model":   MODEL_ALIAS,
            "choices": [{
                "index":         0,
                "message":       {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens":     0,
                "completion_tokens": len(text.split()),
                "total_tokens":      0,
            },
        })

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
