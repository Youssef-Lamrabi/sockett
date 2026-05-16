from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from .db import Base

class UserModel(Base):
    __tablename__ = "user_models"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    source = Column(String, nullable=False)
    base_url = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="models")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    models = relationship("UserModel", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("ChatSession", back_populates="user", cascade="all, delete-orphan")
    provider_config = relationship("ProviderConfig", back_populates="user", uselist=False, cascade="all, delete-orphan")

class ProviderConfig(Base):
    """
    One per user. Controls which backend to use and how to connect.
    source: "OpenAI" | "AzureOpenAI" | "Anthropic" | "Ollama" | "Gemini" | "Bedrock" | "Groq" | "Custom"
    """
    __tablename__ = "provider_configs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    source = Column(String, default="Ollama")          # sensible default for local dev
    base_url = Column(String, nullable=True)           # for Custom/Ollama/OpenAI-compatible
    api_key = Column(String, nullable=True)            # for OpenAI/Groq/Gemini/etc.
    default_model = Column(String, default="gpt-oss:20b")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="provider_config")

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="New Chat")
    model = Column(String, default="gpt-oss:20b")
    interaction_mode = Column(String, default="auto")  # 'auto' | 'feedback'
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")

class MessageLog(Base):
    __tablename__ = "message_logs"
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, index=True)
    tag = Column(String, nullable=False)      # EXECUTE | OBSERVE | LOGS | THINK | STATUS | NEXT
    body = Column(Text, nullable=False)       # inner content only (see router)
    ord = Column(Integer, default=0)          # order within the message
    created_at = Column(DateTime, default=datetime.utcnow)

    message = relationship("Message", back_populates="logs")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String, nullable=False)  # user/assistant/system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")
    logs = relationship(
        "MessageLog",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageLog.ord",
    )

