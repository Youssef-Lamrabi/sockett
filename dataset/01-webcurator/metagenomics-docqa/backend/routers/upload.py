from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_role
from ..database import get_db
from ..models import UserRole, Chunk, QAItem
from ..pipeline import generate_qas_for_chunk


router = APIRouter()


@router.post("/file")
def upload_raw_file(
    f: UploadFile = File(...),
    _user=Depends(require_role(UserRole.provider)),
    db: Session = Depends(get_db),
):
    # Accept uploaded jsonl OR json array of raw chunks with fields: chunk_id, source_url, content
    if not f.filename.endswith((".jsonl", ".json")):
        raise HTTPException(status_code=400, detail="Only .jsonl or .json supported for now")
    raw_bytes = f.file.read()
    content = raw_bytes.decode("utf-8", errors="ignore")
    import json

    records = []
    # Try JSON array first
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
            records = parsed["data"]
    except Exception:
        records = []

    # Fallback to JSONL lines
    if not records:
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                records.append(obj)
            except Exception:
                continue

    created, qa_created, skipped = 0, 0, 0
    for obj in records:
        chunk_id = obj.get("chunk_id") or obj.get("id")
        text = obj.get("content") or obj.get("text") or ""
        if not text:
            skipped += 1
            continue

        # Skip duplicates by chunk_id
        existing = None
        if chunk_id:
            existing = db.query(Chunk).filter(Chunk.chunk_id == chunk_id).first()
        if existing:
            skipped += 1
            continue

        chunk = Chunk(
            chunk_id=chunk_id,
            source_url=obj.get("source_url", ""),
            content=text,
        )
        db.add(chunk)
        db.commit()
        db.refresh(chunk)
        created += 1

        # autogenerate QAs via Ollama, but be resilient
        try:
            for qa in generate_qas_for_chunk(chunk.content):
                db.add(QAItem(chunk_id_fk=chunk.id, question=qa["question"], answer=qa["answer"]))
                qa_created += 1
            db.commit()
        except Exception:
            # If LLM generation fails, continue without blocking ingest
            db.rollback()
            continue

    return {"chunks": created, "qa_generated": qa_created, "skipped": skipped, "total_received": len(records)}


