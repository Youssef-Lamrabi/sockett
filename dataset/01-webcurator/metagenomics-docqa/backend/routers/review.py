from typing import List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..auth import get_current_user
from ..database import get_db
from ..models import QAItem, QAStatus, Annotation, User, Chunk, Category
from ..schemas import QAOut, AnnotationIn, AnnotationOut


router = APIRouter()


@router.get("/stats")
def get_stats(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    total_qas = db.query(QAItem).count()
    pending_qas = db.query(QAItem).filter(QAItem.status == QAStatus.pending).count()
    ready_qas = db.query(QAItem).filter(QAItem.status == QAStatus.ready).count()
    rejected_qas = db.query(QAItem).filter(QAItem.status == QAStatus.rejected).count()
    
    return {
        "total": total_qas,
        "pending": pending_qas,
        "ready": ready_qas,
        "rejected": rejected_qas
    }


@router.get("/pending", response_model=List[Dict[str, Any]])
def list_pending(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    items = db.query(QAItem).filter(QAItem.status == QAStatus.pending).all()
    result = []
    for item in items:
        chunk = db.query(Chunk).filter(Chunk.id == item.chunk_id_fk).first()
        annotations = db.query(Annotation).filter(Annotation.qa_item_id_fk == item.id).order_by(Annotation.created_at.desc()).all()
        annotators = []
        for ann in annotations:
            user = db.query(User).filter(User.id == ann.annotated_by_user_id).first()
            if user:
                annotators.append({
                    "annotation_id": ann.id,
                    "user_id": ann.annotated_by_user_id,
                    "name": user.full_name or user.email,
                    "date": ann.created_at.isoformat(),
                    "score": ann.score,
                    "edited_question": ann.edited_question,
                    "edited_answer": ann.edited_answer,
                })
        
        result.append({
            "id": item.id,
            "chunk_id": item.chunk_id_fk,
            "chunk_content": (chunk.content if chunk else ""),
            "question": item.question,
            "answer": item.answer,
            "category_id": item.category_id_fk,
            "status": item.status.value,
            "created_at": item.created_at.isoformat(),
            "annotators": annotators,
            "annotator_count": len(annotators),
        })
    return result


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    cats = db.query(Category).all()
    return [{"id": c.id, "name": c.name, "description": c.description} for c in cats]


@router.post("/set_category")
def set_category(payload: dict, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    qa_id = int(payload.get("qa_item_id"))
    category_id = payload.get("category_id")
    qa = db.query(QAItem).filter(QAItem.id == qa_id).first()
    if not qa:
        raise HTTPException(status_code=404, detail="QA not found")
    qa.category_id_fk = int(category_id) if category_id is not None else None
    db.commit()
    return {"ok": True}


@router.post("/support")
def support_annotation(payload: dict, db: Session = Depends(get_db), _user=Depends(get_current_user)):
    ann_id = int(payload.get("annotation_id"))
    delta = float(payload.get("delta", 1.0))
    ann = db.query(Annotation).filter(Annotation.id == ann_id).first()
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found")
    ann.score = float(ann.score or 0) + delta
    # also mark QA as ready when supported
    qa = db.query(QAItem).filter(QAItem.id == ann.qa_item_id_fk).first()
    if qa:
        qa.status = QAStatus.ready
    db.commit()
    return {"ok": True, "score": ann.score, "qa_item_id": ann.qa_item_id_fk}


@router.post("/annotate", response_model=AnnotationOut)
def annotate(payload: AnnotationIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    qa = db.query(QAItem).filter(QAItem.id == payload.qa_item_id).first()
    if not qa:
        raise HTTPException(status_code=404, detail="QA not found")
    ann = Annotation(
        qa_item_id_fk=qa.id,
        edited_question=payload.edited_question,
        edited_answer=payload.edited_answer,
        score=payload.score,
        comment=payload.comment,
        validated=payload.validated,
        annotated_by_user_id=user.id,
    )
    if payload.validated and payload.score >= 0.7:
        qa.status = QAStatus.ready
    elif payload.validated and payload.score < 0.3:
        qa.status = QAStatus.rejected
    db.add(ann)
    # increment denormalized count
    try:
        qa.annotation_count = (qa.annotation_count or 0) + 1
    except Exception:
        pass
    db.commit()
    db.refresh(ann)
    return ann


