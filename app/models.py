
from sqlalchemy import Boolean, Column, String, Integer, ForeignKey, DateTime, JSON, Text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class Location(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    path = Column(String, index=True) # e.g. "Home/Lab/Rack A"
    parent_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    assets = relationship("Asset", back_populates="location")
    # many-to-one: remote_side=[id] marks this side as pointing at the parent row
    parent = relationship("Location", remote_side=[id], backref="children")

class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    serial_no = Column(String, unique=True, index=True, nullable=True)
    category = Column(String, nullable=False)
    status = Column(String, default="active")
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    embedding = Column(Vector(1536), nullable=True)
    notes = Column(Text, nullable=True)
    timezone = Column(String(64), nullable=True)  # server's local tz; NULL = owner's tz
    # Physical site (see app/geo.py). country is ISO 3166-1 alpha-2.
    country = Column(String(2), nullable=True, index=True)
    region = Column(String(120), nullable=True)   # state / county / province
    city = Column(String(120), nullable=True)
    site = Column(String(120), nullable=True)     # datacentre / provider / building
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True)  # hidden, restorable
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Server agent (heartbeat). Only a SHA-256 of the token is stored.
    agent_token_hash = Column(String(64), unique=True, index=True, nullable=True)
    agent_last_seen = Column(DateTime(timezone=True), nullable=True)
    agent_metrics = Column(JSON, nullable=True)
    
    location = relationship("Location", back_populates="assets")
    logs = relationship("AuditLog", back_populates="asset")

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    action = Column(String, nullable=False) # MOVE, EDIT, STATUS_CHANGE
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    
    asset = relationship("Asset", back_populates="logs")

class Maintenance(Base):
    __tablename__ = "maintenance"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    scheduled_date = Column(DateTime(timezone=True), nullable=False)  # window start
    end_date = Column(DateTime(timezone=True), nullable=True)         # window end
    completion_date = Column(DateTime(timezone=True), nullable=True)
    description = Column(Text, nullable=False)
    status = Column(String, default="scheduled") # scheduled, completed, cancelled
    
    asset = relationship("Asset", backref="maintenance_records")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(254), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    timezone = Column(String(64), nullable=False, default="Europe/London", server_default="Europe/London")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    user = relationship("User")
