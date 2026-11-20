"""
Audit logging for CRUD operations.

Logs create, update, delete operations with contextual information.
Separate from request logging — focuses on business-level events.

Usage
-----
from your_service_name.backend.logging.audit import audit_logger
audit_logger.info("item.created", item_id=str(item.id), name=item.name)
"""

from __future__ import annotations

import logging

# Dedicated audit logger — separate from application logs.
# Configure in logging config to route to a separate file/stream.
audit_logger = logging.getLogger("audit")
