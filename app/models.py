
from sqlalchemy import Boolean, Column, String, Integer, ForeignKey, DateTime, Date, JSON, Text
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
    # Lifecycle dates drive the warranty / end-of-life alerts (app/alerts.py).
    warranty_expires = Column(Date, nullable=True, index=True)
    eol_date = Column(Date, nullable=True, index=True)
    alerts_muted = Column(Boolean, nullable=False, server_default="false")
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
    # Deactivated users keep their audit history but cannot log in.
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")
    name = Column(String(80), nullable=True)
    timezone = Column(String(64), nullable=False, default="Europe/London", server_default="Europe/London")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    user = relationship("User")


class Invite(Base):
    """A single-use link that lets a colleague set their own password.

    Self-hosted installs have no mail server, so the admin copies the link and
    sends it however they like. Only the SHA-256 of the token is stored, so a
    leaked database dump cannot be replayed to claim an account.
    """
    __tablename__ = "invites"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(254), nullable=False, index=True)
    name = Column(String(80), nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    invited_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    invited_by = relationship("User")


class AlertChannel(Base):
    """Where alerts get delivered. A webhook, because a self-hosted install
    has no mail server (same reasoning as invite links)."""
    __tablename__ = "alert_channels"
    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    url = Column(Text, nullable=False)
    kind = Column(String(16), nullable=False, default="generic")  # discord/slack/ntfy/generic
    enabled = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_ok_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)


class AlertSent(Base):
    """One row per alert key, holding when it last went out.

    This table is the whole anti-spam mechanism: app/alerts.py decides what is
    true, this decides whether we have already said it recently.
    """
    __tablename__ = "alerts_sent"
    key = Column(String(120), primary_key=True)
    severity = Column(String(16), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=True)
    title = Column(Text, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_sent_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
