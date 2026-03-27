"""
RBAC (Role-Based Access Control) Module for A2rchi

This module provides authentication and authorization functionality including:
- Permission registry and role-to-permission mappings
- Route protection decorators
- JWT token parsing for role extraction
- Audit logging for security events

Usage:
    from src.utils.rbac import require_permission, has_permission, Permission

    @app.route('/api/upload')
    @require_permission(Permission.Upload.DOCUMENTS)
    def upload():
        ...
"""

from src.utils.rbac.decorators import (require_any_permission,
                                       require_authenticated,
                                       require_permission)
from src.utils.rbac.jwt_parser import (assign_default_role,
                                       extract_roles_from_token,
                                       get_user_roles)
from src.utils.rbac.permission_enum import Permission
from src.utils.rbac.permissions import (check_permission, get_user_permissions,
                                        has_permission)
from src.utils.rbac.registry import (RBACRegistry, get_registry,
                                     load_rbac_config)

__all__ = [
    # Permission enum
    "Permission",
    # Registry
    "RBACRegistry",
    "get_registry",
    "load_rbac_config",
    # Decorators
    "require_permission",
    "require_any_permission",
    "require_authenticated",
    # Permissions
    "has_permission",
    "get_user_permissions",
    "check_permission",
    # JWT Parser
    "extract_roles_from_token",
    "get_user_roles",
    "assign_default_role",
]
