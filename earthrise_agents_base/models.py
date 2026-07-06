import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Chat(models.Model):
    """Model to store chat sessions"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='chats',
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    title = models.CharField(max_length=200, blank=True, null=True)

    agent_state = models.JSONField(default=dict, blank=True)
    agent_memory = models.JSONField(default=dict, blank=True)
    is_processing = models.BooleanField(default=False)

    # Per-chat agent-scoping policy. Empty list = every discovered
    # subagent is enabled (no restriction). A populated list names the
    # `agent_label` strings allowed; the orchestrator filters out
    # everything else before building the ReAct tool registry.
    enabled_agents = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "List of agent_labels allowed for this chat's "
            "orchestrator. Empty list = all discovered agents enabled."
        ),
    )

    # When True, `enabled_agents` is immutable. The PATCH endpoint
    # refuses updates and the UI hides the toggle bar. Set by any
    # caller that wants to hard-pin a chat's agent scope at creation.
    agents_locked = models.BooleanField(
        default=False,
        help_text=(
            "When True, `enabled_agents` cannot be changed after "
            "creation. Used by callers that want to hard-pin a chat's "
            "agent scope at creation time."
        ),
    )

    def __str__(self):
        return f"Chat {self.id} - {self.title or 'Untitled'}"

    class Meta:
        ordering = ['-updated_at']
        db_table = 'chat_chat'


# ---------------------------------------------------------------------------
# LangGraph checkpoint tables (unmanaged)
#
# Owned by `langgraph-checkpoint-postgres` — created by `saver.setup()` in
# `earthrise_agents_base/agent/chat_agent.py`. We don't migrate them; we
# only need ORM access for one operation: cascading deletion when a Chat
# is deleted.
#
# The real schemas use composite primary keys; Django requires a single
# primary_key field. We pick whichever single column is closest to a PK
# and live with the lie — it's harmless for filter+delete by thread_id,
# which is the only access pattern we use here.
# ---------------------------------------------------------------------------

class LangGraphCheckpoint(models.Model):
    """Per-iteration graph state. One row per node transition."""
    thread_id = models.TextField()
    checkpoint_ns = models.TextField()
    checkpoint_id = models.TextField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'checkpoints'


class LangGraphCheckpointWrite(models.Model):
    """Pending writes for a checkpoint."""
    thread_id = models.TextField()
    checkpoint_ns = models.TextField()
    checkpoint_id = models.TextField()
    task_id = models.TextField(primary_key=True)

    class Meta:
        managed = False
        db_table = 'checkpoint_writes'


class LangGraphCheckpointBlob(models.Model):
    """Large state blobs referenced by checkpoints."""
    thread_id = models.TextField()
    checkpoint_ns = models.TextField()
    channel = models.TextField(primary_key=True)
    version = models.TextField()

    class Meta:
        managed = False
        db_table = 'checkpoint_blobs'


class Message(models.Model):
    """Model to store individual messages in a chat"""
    MESSAGE_TYPES = [
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('system', 'System'),
    ]

    PROCESSING_STATUS = [
        ('completed', 'Completed'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
        ('interrupted', 'Interrupted'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='messages')
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES)
    content = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    status = models.CharField(max_length=15, choices=PROCESSING_STATUS, default='completed')
    task_id = models.CharField(max_length=255, blank=True, null=True)
    current_node = models.CharField(max_length=255, blank=True, null=True)

    artifacts = models.JSONField(default=list, blank=True,
        help_text="Visual artifacts from subagents (charts, maps, tables, etc.)")
    interrupt_data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.message_type}: {self.content[:50]}..."

    class Meta:
        ordering = ['created_at']
        db_table = 'chat_message'


class ChatExperiment(models.Model):
    """Links a Chat session to a SimulationAgent ExperimentSession."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='experiments')
    experiment_id = models.UUIDField(help_text="SimulationAgent ExperimentSession PK")
    experiment_type = models.CharField(max_length=16, default='single')
    label = models.CharField(max_length=200, blank=True)
    is_baseline = models.BooleanField(default=False)
    params_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at']
        db_table = 'chat_chatexperiment'

    def __str__(self):
        return f"ChatExperiment {self.id} ({self.experiment_type}) for chat {self.chat_id}"


class ExecutionPlan(models.Model):
    """Tracks multi-task plan execution for progress display and history."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='plans')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    plan_summary = models.TextField(blank=True)
    plan_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        db_table = 'chat_executionplan'

    def __str__(self):
        return f"ExecutionPlan {self.id} ({self.status})"


class SubagentTask(models.Model):
    """Individual task within an ExecutionPlan."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(ExecutionPlan, on_delete=models.CASCADE, related_name='tasks')
    task_key = models.CharField(max_length=100)
    display_name = models.CharField(max_length=200)
    agent = models.CharField(max_length=20, help_text='data/knowledge/simulation/llm')
    skill = models.CharField(max_length=100)
    params = models.JSONField(default=dict, blank=True)
    depends_on = models.JSONField(default=list, blank=True, help_text='List of task_key strings')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    result_data = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    order = models.IntegerField(default=0)
    is_critical = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'created_at']
        db_table = 'chat_subagenttask'

    def __str__(self):
        return f"SubagentTask {self.task_key} ({self.status})"


class Chart(models.Model):
    """Model to store chart data for messages"""
    CHART_TYPES = [
        ('line', 'Line Chart'),
        ('bar', 'Bar Chart'),
        ('scatter', 'Scatter Plot'),
        ('pie', 'Pie Chart'),
        ('heatmap', 'Heatmap'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='charts')
    chart_type = models.CharField(max_length=20, choices=CHART_TYPES)
    title = models.CharField(max_length=200)
    data = models.JSONField()
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.chart_type}: {self.title}"

    class Meta:
        ordering = ['created_at']
        db_table = 'chat_chart'


class MessageFeedback(models.Model):
    """Developer-facing rating on a single assistant message.

    Not consumed by the orchestrator — this is a signal for humans
    reviewing conversations to diagnose intent/response mismatches.
    The `chat` FK is denormalised from `message.chat` so the admin
    list can filter/group by conversation without joining through
    the messages table.
    """

    RATING = [
        ('up', 'Thumbs Up'),
        ('down', 'Thumbs Down'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE,
        related_name='message_feedback', db_index=True,
    )
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE,
        related_name='feedback', db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='message_feedback',
    )
    rating = models.CharField(max_length=4, choices=RATING)
    comment = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'message')]
        ordering = ['-created_at']
        db_table = 'chat_message_feedback'

    def __str__(self):
        return f"{self.rating} on {self.message_id} by {self.user_id}"


class SiteFeedback(models.Model):
    """Platform-wide feedback submission (modal from any non-home page)."""

    CATEGORIES = [
        ('feature_suggestion', 'Feature Suggestion'),
        ('usability_issue',    'Usability Issue'),
        ('technical_issue',    'Technical Issue'),
        ('general_feedback',   'General Feedback'),
        ('praise',             'Praise'),
        ('data_quality',       'Data Quality Concern'),
    ]

    STATUS = [
        ('new',       'New'),
        ('reviewing', 'Reviewing'),
        ('resolved',  'Resolved'),
        ('archived',  'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='site_feedback',
    )
    anonymous_email = models.EmailField(blank=True, default='')
    category = models.CharField(max_length=32, choices=CATEGORIES, db_index=True)
    content = models.TextField()
    page_url = models.URLField(max_length=500, blank=True, default='')
    status = models.CharField(max_length=16, choices=STATUS, default='new', db_index=True)
    admin_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reviewed_feedback',
    )

    class Meta:
        ordering = ['-created_at']
        db_table = 'chat_site_feedback'

    def __str__(self):
        return f"SiteFeedback {self.id} ({self.category}, {self.status})"
