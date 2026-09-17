import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "atlas.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)


class Memory(Base):
    __tablename__ = "memories"
    id = Column(Integer, primary_key=True)
    category = Column(String(50))
    content = Column(Text, nullable=False)
    source = Column(String(50), default="conversation")
    importance = Column(Integer, default=5)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationLog(Base):
    __tablename__ = "conversation_log"
    id = Column(Integer, primary_key=True)
    role = Column(String(20))
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class TaskLog(Base):
    __tablename__ = "task_log"
    id = Column(Integer, primary_key=True)
    tool_name = Column(String(100))
    arguments = Column(Text)
    result = Column(Text)
    success = Column(Boolean, default=True)
    duration_ms = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(engine)


def save_memory(content: str, category: str = "Facts", importance: int = 5, source: str = "conversation") -> str:
    """Saves a fact about the user for long-term recall across sessions."""
    session = SessionLocal()
    try:
        entry = Memory(content=content, category=category, importance=importance, source=source)
        session.add(entry)
        session.commit()
        return f"Remembered: {content}"
    except Exception as e:
        session.rollback()
        print(f"[Ошибка save_memory]: {e}")
        return "Couldn't save that to memory."
    finally:
        session.close()


def recall_memories(category: str = None, limit: int = 10) -> str:
    """Recalls saved facts, optionally filtered by category."""
    session = SessionLocal()
    try:
        query = session.query(Memory).order_by(Memory.importance.desc(), Memory.created_at.desc())
        if category:
            query = query.filter(Memory.category == category)
        results = query.limit(limit).all()
        if not results:
            return "I don't have anything saved in memory yet."
        return " | ".join(f"[{m.category}] {m.content}" for m in results)
    except Exception as e:
        print(f"[Ошибка recall_memories]: {e}")
        return "Couldn't access memory right now."
    finally:
        session.close()


def forget_memory(content_fragment: str) -> str:
    """Deletes a memory entry matching a text fragment."""
    session = SessionLocal()
    try:
        entry = session.query(Memory).filter(Memory.content.ilike(f"%{content_fragment}%")).first()
        if not entry:
            return f"Couldn't find a memory matching '{content_fragment}'."
        deleted_content = entry.content
        session.delete(entry)
        session.commit()
        return f"Forgot: {deleted_content}"
    except Exception as e:
        session.rollback()
        print(f"[Ошибка forget_memory]: {e}")
        return "Couldn't delete that memory."
    finally:
        session.close()


def get_all_memories(limit: int = 100) -> list:
    """Возвращает список всех сохранённых фактов для отображения в Memory UI (не для голоса)."""
    session = SessionLocal()
    try:
        results = session.query(Memory).order_by(Memory.created_at.desc()).limit(limit).all()
        return [
            {"id": m.id, "category": m.category or "Facts", "content": m.content,
             "importance": m.importance, "created_at": m.created_at.strftime("%Y-%m-%d %H:%M")}
            for m in results
        ]
    except Exception as e:
        print(f"[Ошибка get_all_memories]: {e}")
        return []
    finally:
        session.close()


def delete_memory_by_id(memory_id: int) -> bool:
    """Удаляет запись памяти по ID — используется кнопкой удаления в Memory UI."""
    session = SessionLocal()
    try:
        entry = session.query(Memory).filter(Memory.id == memory_id).first()
        if not entry:
            return False
        session.delete(entry)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        print(f"[Ошибка delete_memory_by_id]: {e}")
        return False
    finally:
        session.close()


def log_task(tool_name: str, arguments: dict, result: str, success: bool = True, duration_ms: int = 0) -> None:
    """Логирует выполнение tool call — основа для Execution Trace в UI."""
    session = SessionLocal()
    try:
        entry = TaskLog(
            tool_name=tool_name, arguments=str(arguments), result=str(result)[:500],
            success=success, duration_ms=duration_ms
        )
        session.add(entry)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[Ошибка log_task]: {e}")
    finally:
        session.close()


def get_recent_tasks(limit: int = 15) -> list:
    """Возвращает последние выполненные действия для Execution Trace в UI, в хронологическом порядке."""
    session = SessionLocal()
    try:
        results = session.query(TaskLog).order_by(TaskLog.timestamp.desc()).limit(limit).all()
        tasks = [
            {"id": t.id, "tool_name": t.tool_name, "arguments": t.arguments,
             "result": t.result, "success": t.success, "duration_ms": t.duration_ms,
             "timestamp": t.timestamp.strftime("%H:%M:%S")}
            for t in results
        ]
        return list(reversed(tasks))
    except Exception as e:
        print(f"[Ошибка get_recent_tasks]: {e}")
        return []
    finally:
        session.close()


def log_conversation(role: str, content: str) -> None:
    session = SessionLocal()
    try:
        entry = ConversationLog(role=role, content=content)
        session.add(entry)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"[Ошибка log_conversation]: {e}")
    finally:
        session.close()