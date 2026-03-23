class Role:
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"
    VIEWER = "viewer"

ADMIN_ROLES = {Role.ADMIN}
ALL_ROLES = {Role.ADMIN, Role.MANAGER, Role.USER, Role.VIEWER}
