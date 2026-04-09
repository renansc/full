from datetime import datetime

from flask_login import UserMixin

from .extensions import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WorkflowState(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    order_index = db.Column(db.Integer, default=0, nullable=False)
    color = db.Column(db.String(32), default="#4f46e5", nullable=False)
    is_closed = db.Column(db.Boolean, default=False, nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)


class Department(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    color = db.Column(db.String(32), default="#38bdf8", nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class Label(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    color = db.Column(db.String(32), default="#64748b", nullable=False)


ticket_labels = db.Table(
    "ticket_labels",
    db.Column("ticket_id", db.Integer, db.ForeignKey("ticket.id"), primary_key=True),
    db.Column("label_id", db.Integer, db.ForeignKey("label.id"), primary_key=True),
)


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="operator", nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=True)

    department = db.relationship("Department")


class Ticket(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=False)
    client_name = db.Column(db.String(140), nullable=False)
    client_phone = db.Column(db.String(40), nullable=False, index=True)
    company = db.Column(db.String(140), default="", nullable=False)
    service = db.Column(db.String(200), default="", nullable=False)
    description = db.Column(db.Text, default="", nullable=False)
    status_id = db.Column(db.Integer, db.ForeignKey("workflow_state.id"), nullable=False)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=True)
    due_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)

    status = db.relationship("WorkflowState")
    assigned_to = db.relationship("User")
    department = db.relationship("Department")
    labels = db.relationship("Label", secondary=ticket_labels, lazy="joined")


class Conversation(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False, unique=True)
    wa_chat_id = db.Column(db.String(255), nullable=True, index=True)
    contact_name = db.Column(db.String(140), default="", nullable=False)
    last_message_at = db.Column(db.DateTime, nullable=True)
    unread_incoming_count = db.Column(db.Integer, default=0, nullable=False)

    ticket = db.relationship("Ticket", backref=db.backref("conversation", uselist=False))


class Message(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id"), nullable=False)
    external_id = db.Column(db.String(255), nullable=True, index=True)
    direction = db.Column(db.String(12), nullable=False)
    sender_name = db.Column(db.String(140), nullable=False)
    sender_department = db.Column(db.String(140), default="", nullable=False)
    content = db.Column(db.Text, nullable=False)
    media_url = db.Column(db.String(500), default="", nullable=False)
    delivered_at = db.Column(db.DateTime, nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)

    conversation = db.relationship("Conversation", backref=db.backref("messages", lazy="dynamic"))


class QuickReply(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    shortcut = db.Column(db.String(60), nullable=False, unique=True)
    body = db.Column(db.Text, nullable=False)


class Setting(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), nullable=False, unique=True, index=True)
    value = db.Column(db.Text, nullable=False)


class ReminderLog(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False, index=True)
    reminder_type = db.Column(db.String(40), nullable=False, default="appointment")
    scheduled_for = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    channel = db.Column(db.String(40), nullable=False, default="whatsapp")
    status = db.Column(db.String(40), nullable=False, default="sent")

    ticket = db.relationship("Ticket", backref=db.backref("reminder_logs", lazy="dynamic"))
